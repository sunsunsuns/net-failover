# 设计说明

这份文档解释 `net-failover` 为什么这样设计，以及我们在真机上踩过的坑。

## 1. 问题：默认路由不等于"能用"

一台机器同时接有线、WiFi、4G 时，内核只按 **metric** 选默认路由，它不关心这条链路
是不是真的能上网。于是会出现：

- 网线拔了，但 ifupdown 给它配的静态默认路由还在（变成 `linkdown`），metric 又比 4G 小
  → 所有流量进黑洞，表现为 `ping: Destination unreachable`。
- WiFi 连上了，但上游没有外网 → 路由正常、实际全丢。
- 4G 通了，但优先级永远排在最后，没人调用它。

`net-failover` 解决的就是"**把默认路由从'配置正确'变成'实际可用'**"。

## 2. 路由模型：一条主路由 + 多条备份

```
default via 192.168.3.1 dev eth0  metric 50    ← 被提升的主链路（promote_metric）
default via 192.168.3.1 dev eth0  metric 600   ← 备份（eth0 的自然 metric）
default via 192.168.3.1 dev wlan0 metric 700   ← 备份
default via 192.168.43.1 dev enx… metric 800   ← 备份
```

- **备份路由必须存在**，两个原因：
  1. 主链路消失时内核能立刻落到下一条，切换是"零成本"的；
  2. `SO_BINDTODEVICE` 探测需要该网卡自己有一条默认路由才能把包发出去（见坑 2）。
- 主链路用 `promote_metric`（默认 50）压过所有备份。
- 切换 = `ip route replace default ... metric 50`，是一个原子操作，内核瞬间改道。
- **绝不删除别人的自然路由**，只在自己的 metric 冲突时清理残留（见坑 1）。

## 3. 健康探测

- 用 **TCP 连接**而不是 ICMP：4G 运营商普遍封 ICMP，`ping` 100% 丢包但网页正常。
- 多目标（默认 `223.5.5.5:443`、`119.29.29.29:443`），任一成功即健康。
- 三张网卡**并行**探测（线程池），单轮最多 `timeout` 秒，而不是 3×timeout。
- `SO_BINDTODEVICE`（setsockopt 25）把探测绑定到具体网卡，保证"测的是这条链路"。

## 4. 防抖

| 参数 | 默认 | 作用 |
|---|---|---|
| `strikes_down` | 3 | 连续 3 轮全失败才降级（≈4~6 s） |
| `strikes_up` | 3 | 连续 3 轮成功才升回 |
| `cooldown` | 15 s | 两次"升回"之间至少间隔，防止来回翻 |
| `settle` | 8 s | 新链路必须连续健康这么久才有资格升为主出口 |

关键细节：

- **冷却只限制"升回高优先级"，不拦降级**，否则主链路刚被提升就挂掉时会被冷却卡住。
- **`settle` 管"新链路"**：刚插上的网线、刚重连的 WiFi 要先稳定 8 秒，避免
  NetworkManager 重连抖动期间被反复提升。
- **设备被摘掉时不催**：主链路没有 IP（被 NM 临时置为 unmanaged / 拔线）时，内核
  已经落到备份路由，守护进程不再计失败次数、也不急着换，等它回来。
  这解决了"重启 networking 后链路连上又断、来回切"的问题。

## 4.5 无线信号双门限

只靠"能不能连上"判断 WiFi 是不够的：信号很弱时还连着，但质量已经很差。所以对无线链路
额外加两个阈值（dBm，`iw dev <dev> link` 读取）：

| 参数 | 默认 | 含义 |
|---|---|---|
| `rssi_down` | -75 | 低于它就主动切走（**不等断链**） |
| `rssi_up` | -65 | 高于它才允许切回来 |

两者之间是**回滞带**：

- 正在用 WiFi、信号掉到 -70（在带内）→ 继续用，不折腾；
- 已经从 4G 待着、信号回到 -70（在带内）→ **不切回**，直到升到 -65 以上；
- 这样在 -70 附近抖动时不会来回换。

非无线链路（有线/4G 模块）没有这个限制，不写 `rssi_*` 即为关闭。

## 5. 网关兜底

`nmcli` 在默认路由被删掉之后会返回空网关，不能只依赖它。顺序：

1. 当前路由表里该网卡的默认路由；
2. `nmcli -g IP4.GATEWAY`；
3. `/run/net-failover-gw.json` 缓存；
4. 由网卡地址 + 前缀推算 `网络号+1`。

最后一步在 DHCP 不下发网关时救过场；若内核仍认为网关不可达（`noprefixroute` 地址没有
自动生成直连路由），`ip route replace` 会报 `Nexthop has invalid gateway`，此时自动
加 `onlink` 重试。

## 6. 踩过的坑

### 坑 1：同 metric 不能并存

```
# ip route add default via 192.168.3.1 dev eth0 metric 50
RTNETLINK answers: File exists
```

内核用 `(目的前缀, metric)` 区分路由。已有 `metric 50` 的默认路由时再加一条会 EEXIST，
**即使网关/网卡不同**。所以切换必须用 `ip route replace`（原子替换），而不是先 add 后 del。

### 坑 2：SO_BINDTODEVICE 需要该网卡有默认路由

早期版本为了"干净"只保留一条默认路由，结果其它网卡的探测全部超时——包发不出去。
**备份路由不能删**，否则探测失效、守护进程会误判"所有链路都不可用"。

### 坑 3：ZeroTier/4G 的 ICMP

`ping 223.5.5.5` 在 4G 上 100% 丢包，但 `curl` 正常。所有健康判断都必须走 TCP。

### 坑 4：NetworkManager 的 MAC 钉死

profile 里的 `mac-address=` 与实际网卡不符时，`nmcli con up` 会报
"device … not available because profile is not compatible with device (mismatching
interface name)"，很容易被误导成接口名问题。清掉 MAC 钉死即可。

### 坑 5：DHCP 不下发网关

部分环境的 DHCP 只给地址不给网关，`nmcli` 的 `IP4.GATEWAY` 为空。靠网段+1 推算 +
`onlink` 兜底。

## 7. 与"无缝"的距离

切换必然改变出口源 IP，正在进行中的长连接（TCP 四元组）会断一次重连。这是网络切换
的物理限制，手机换网时也一样。想要真正的"零中断"，需要让业务流量走一条固定隧道
（例如始终经 ZeroTier/MPTCP 出去），那是另一个层面的方案。
