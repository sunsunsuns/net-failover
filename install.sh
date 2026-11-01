#!/usr/bin/env bash
# net-failover 一键安装：装脚本、生成配置、启用开机自启
set -euo pipefail

PREFIX="${PREFIX:-/usr/local/sbin}"
CONFDIR="${CONFDIR:-/etc/net-failover}"
UNITDIR="${UNITDIR:-/etc/systemd/system}"
SRC="$(cd "$(dirname "$0")" && pwd)"

info() { printf '\033[1;32m[net-failover]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[net-failover]\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31m[net-failover]\033[0m %s\n' "$*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "需要 root 权限，请用 sudo $0 运行"
command -v python3 >/dev/null 2>&1 || die "需要 python3"
command -v ip >/dev/null 2>&1 || die "需要 iproute2（ip 命令）"

info "安装脚本到 $PREFIX"
install -d "$PREFIX" "$CONFDIR"
install -m 0755 "$SRC/bin/net-failover" "$PREFIX/net-failover"
install -m 0755 "$SRC/bin/zt-watchdog" "$PREFIX/zt-watchdog"
install -m 0755 "$SRC/bin/zt-boot-connect" "$PREFIX/zt-boot-connect"

if [ ! -f "$CONFDIR/config.ini" ]; then
  install -m 0644 "$SRC/config/config.ini.example" "$CONFDIR/config.ini"
  info "已生成默认配置 $CONFDIR/config.ini"
else
  info "保留已有配置 $CONFDIR/config.ini"
fi

info "当前检测到的 IPv4 网卡："
ip -4 -br addr show scope global | awk '{print "    " $1 "  " $3}'

info "安装 systemd 单元"
install -m 0644 "$SRC/systemd/net-failover.service" "$UNITDIR/net-failover.service"
systemctl daemon-reload
systemctl enable net-failover >/dev/null
systemctl restart net-failover
info "net-failover 已启用并启动（开机自启）"

if systemctl cat zerotier-one.service >/dev/null 2>&1; then
  install -m 0644 "$SRC/systemd/zt-watchdog.service" "$UNITDIR/zt-watchdog.service"
  install -m 0644 "$SRC/systemd/zt-boot-connect.service" "$UNITDIR/zt-boot-connect.service"
  systemctl daemon-reload
  systemctl enable zt-watchdog zt-boot-connect >/dev/null
  systemctl restart zt-watchdog
  info "zt-watchdog 已启用并启动（ZeroTier 掉线自动重启）"
  info "zt-boot-connect 已启用（开机后尽快让 ZeroTier 上线）"
else
  warn "未检测到 zerotier-one.service，跳过 zt-watchdog"
  warn "装好 ZeroTier 后重新运行本脚本即可启用"
fi

echo
info "完成。常用命令："
echo "    systemctl status net-failover        # 运行状态"
echo "    journalctl -u net-failover -f        # 实时日志"
echo "    ip route show default                # 当前出口（metric 50 那条）"
echo "    sudo nano $CONFDIR/config.ini        # 改链路/优先级/探测"
echo "    sudo $SRC/tests/switch-test.py --yes  # 实测切换耗时（会短暂断网）"
