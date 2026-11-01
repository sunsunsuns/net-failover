# net-failover

**Linux 多上行链路自动切换** —— 有线 / WiFi / 4G 之间自动切换，像手机和笔记本那样，不需要人工插拔。

`net-failover` 让机器始终保持"一条**健康的**默认出口"，链路变差或消失时自己换路。
每条可用链路都保留一条备份默认路由，所以主链路消失时内核会立刻改道——实测
**业务断流 0.06 s**，最坏情况（WiFi 整条消失）约 2.2 s。

```
   ┌──────────────┐   优先级     ┌───────────────────────────────┐
   │  eth0  (600) │  ──────────▶ │  metric 50  ← 被提升的主路由    │
   │  wlan0 (700) │              │  metric 600/700/800 ← 备份路由 │
   │  4G    (800) │              └───────────────────────────────┘
   └──────────────┘                        ▲
          │  每 1.5 秒并行 TCP 探测          │  不健康就切
          └───────────────────────────────┘
```

## 特性

- **按优先级选择**：默认 有线 > WiFi > 4G，顺序和 metric 都可配置。
- **TCP 健康探测**：4G 链路通常封 ICMP，用 `ping` 判断会误判；本项目用 TCP 连接探测。
- **零路由死锁防护**：永远不删掉最后一条默认路由；不会出现"所有流量进死网口"。
- **防抖**：连续 N 次失败才降级、连续 M 次成功才升回、升回有冷却，单次抖动不会切走流量。
- **信号双门限**：WiFi 信号低于 `rssi_down` 就主动切走（不等断链）；高于 `rssi_up` 才允许切回，
  中间是回滞带，避免在临界点反复横跳。
- **网关四重兜底**：路由表 → `nmcli` → 缓存 → 网段+1；DHCP 不下发网关时自动 `onlink`。
- **ZeroTier 看门狗**：服务挂了或节点持续 OFFLINE 自动重启。
- **一键安装 + systemd 开机自启**。
- **有实测数据**：[docs/TEST-REPORT.md](docs/TEST-REPORT.md)。

## 安装

```sh
git clone https://github.com/sunsunsuns/net-failover.git net-failover
cd net-failover
sudo ./install.sh
```

> 内网部署时从自建 GitLab 克隆：`git clone ssh://git@<gitlab-host>:2222/root/net-failover.git`

安装脚本会：把脚本装到 `/usr/local/sbin`、生成 `/etc/net-failover/config.ini`、
启用并启动 **net-failover** 与（若已装 ZeroTier）**zt-watchdog**，并设置开机自启。

卸载：

```sh
sudo ./uninstall.sh          # 保留配置
sudo ./uninstall.sh --purge  # 连配置一起删
```

## 配置

编辑 `/etc/net-failover/config.ini`（安装脚本会给一份默认的，设备不存在会自动跳过）：

```ini
[general]
promote_metric = 50   ; 选中链路被提升到的 metric

[probe]
targets = 223.5.5.5:443, 119.29.29.29:443
timeout = 1.5
interval = 1.5
strikes_down = 3      ; 连续失败几次降级
strikes_up = 3        ; 连续成功几次升回
cooldown = 15         ; 升回冷却秒数
settle = 8

[link:ethernet]
device = eth0
metric = 600

[link:wifi]
device = wlan0
metric = 700
rssi_down = -75       ; 低于这个 dBm 主动切走
rssi_up = -65         ; 高于这个 dBm 才允许切回

[link:cellular]
device = enx020c29a39b6d
metric = 800

[zerotier]
enabled = true
service = zerotier-one
interval = 30
offline_threshold = 3
```

改完 `sudo systemctl restart net-failover`。

## 运维

```sh
systemctl status net-failover        # 状态
journalctl -u net-failover -f        # 实时日志
ip route show default                # metric 50 那条就是当前出口
sudo ./tests/switch-test.py --yes    # 实测切换耗时（会短暂断网）
```

## 工作原理

1. 每 `interval` 秒**并行**探测所有链路：`SO_BINDTODEVICE` 绑定网卡 + TCP 连接探测目标。
2. 每条有 IP 且有网关的链路，都保留一条自然 metric 的默认路由（备份，也让探测能发包）。
3. 把当前最健康的最高优先级链路，用 `ip route replace` 加一条 `metric 50` 的路由，压过其它链路。
4. 当前链路连续失败 `strikes_down` 次 → 换到下一条健康链路；更高优先级链路连续成功
   `strikes_up` 次 → 切回。
5. 所有链路都不健康时**什么都不做**，绝不删掉现有路由。

设计细节见 [docs/DESIGN.md](docs/DESIGN.md)。

## 排查：4G 模块反复重启（USB 层）

现象：网络图标每隔十几秒转圈，`dmesg` 里同一个 USB 口反复 `new high-speed USB device` /
`USB disconnect`。这不是切换逻辑在动，而是模块自己在 USB 层重启。

判断方法：

```sh
# 1) 看是不是 USB 层在循环（间隔 1~15 秒不等）
dmesg -T | grep -iE 'usb 1-1\.[0-9]+: (new|USB disconnect)' | tail

# 2) 停掉守护进程再数一次，若次数不变则与本项目无关
sudo systemctl stop net-failover
journalctl -k --since '60 sec ago' | grep -c 'new high-speed'
sudo systemctl start net-failover
```

常见原因：供电不足（USB 口/HUB 带不动 4G 模块）、线材/接口接触不良、模块过热或固件崩溃。
处理：换 USB 口或有源 HUB、重新插拔模块、检查线材。

模块无法修复期间，可以让 NetworkManager 忽略它，避免图标一直转：

```sh
sudo tee /etc/NetworkManager/conf.d/99-unmanaged-4g.conf >/dev/null <<'EOF'
[keyfile]
unmanaged-devices=interface-name:enx020c29a39b6d
EOF
sudo systemctl reload NetworkManager
```

修好后删掉该文件并 `sudo systemctl reload NetworkManager` 即可恢复 4G 链路。

## 开机加速：ZeroTier 尽快上线

开机时 ZeroTier 往往比网络先启动（`network-online.target` 在 WiFi 拿到地址前就已满足），
连不上根服务器后进入退避，等网络恢复还要等很久。`zt-boot-connect` 会在后台等网络真正
可用，然后重启 ZeroTier 触发立即重连（实测**网络就绪后 6.4 秒上线**）：

```sh
journalctl -u zt-boot-connect -b     # 看本次开机的耗时
```

## 依赖

- Linux + `iproute2` + `python3`(3.6+) + systemd
- NetworkManager（可选：用于取网关、以及 `tests/switch-test.py`）
- ZeroTier（可选：用于 `zt-watchdog`）

## 许可证

MIT，见 [LICENSE](LICENSE)。
