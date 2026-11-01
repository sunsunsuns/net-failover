#!/usr/bin/env bash
# net-failover 卸载
set -euo pipefail

PREFIX="${PREFIX:-/usr/local/sbin}"
CONFDIR="${CONFDIR:-/etc/net-failover}"
UNITDIR="${UNITDIR:-/etc/systemd/system}"

[ "$(id -u)" -eq 0 ] || { echo "需要 root：sudo $0" >&2; exit 1; }

systemctl disable --now net-failover 2>/dev/null || true
systemctl disable --now zt-watchdog 2>/dev/null || true
rm -f "$UNITDIR/net-failover.service" "$UNITDIR/zt-watchdog.service"
systemctl daemon-reload
rm -f "$PREFIX/net-failover" "$PREFIX/zt-watchdog"
rm -f /run/net-failover-gw.json

if [ "${1:-}" = "--purge" ]; then
  rm -rf "$CONFDIR"
  echo "已卸载并删除配置 $CONFDIR"
else
  echo "已卸载，配置保留在 $CONFDIR（加 --purge 可一并删除）"
fi
