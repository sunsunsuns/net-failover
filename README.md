# net-failover

**Automatic multi-uplink failover for Linux** — 以太网 / WiFi / 4G 之间自动切换，像手机和笔记本那样，不需要人工插拔。

`net-failover` keeps exactly one *healthy* uplink as the default route and switches by
itself when that uplink degrades or disappears. Every usable link keeps its own backup
route, so when the active link vanishes the kernel reroutes instantly — typically
**0.05 s of packet loss**, worst case a couple of seconds.

```
   ┌──────────────┐   priority   ┌───────────────────────────────┐
   │  eth0  (600) │  ──────────▶ │  metric 50  ← promoted route  │
   │  wlan0 (700) │              │  metric 600/700/800 ← backups │
   │  4G    (800) │              └───────────────────────────────┘
   └──────────────┘                        ▲
          │  TCP probe every 1.5 s        │  switch when unhealthy
          └───────────────────────────────┘
```

## Features

- **Priority-based selection** — wired > WiFi > cellular by default, fully configurable.
- **Health probes over TCP** — works on cellular links where ICMP is blocked.
- **Zero-route safety** — never deletes a route without a replacement; no more
  "all traffic goes into a dead interface".
- **Anti-flap** — N consecutive failures to demote, M successes to promote, cooldown
  between promotions. A single jittery probe will not move your traffic.
- **Signal hysteresis** — leave WiFi when RSSI drops below `rssi_down`, come back only
  above `rssi_up`; the band in between prevents flapping at the edge.
- **Gateway fallbacks** — route table → `nmcli` → cache → subnet+1, plus `onlink`
  when DHCP does not hand out a gateway.
- **ZeroTier watchdog** — restarts `zerotier-one` when the service dies or the node
  stays OFFLINE.
- **One-command install, systemd enabled** — boots automatically.
- **Measured** — see [docs/TEST-REPORT.md](docs/TEST-REPORT.md).

## Install

```sh
git clone https://github.com/sunsunsuns/net-failover.git net-failover
cd net-failover
sudo ./install.sh
```

> 内部部署时从自建 GitLab 克隆：`git clone ssh://git@<gitlab-host>:2222/root/net-failover.git`

The installer copies the daemons to `/usr/local/sbin`, writes
`/etc/net-failover/config.ini`, enables and starts **net-failover** and (if ZeroTier
is present) **zt-watchdog**. Reboot-safe.

Uninstall:

```sh
sudo ./uninstall.sh          # keeps /etc/net-failover/config.ini
sudo ./uninstall.sh --purge  # removes config too
```

## Configure

Edit `/etc/net-failover/config.ini`:

```ini
[general]
promote_metric = 50   ; the winning route gets this metric

[probe]
targets = 223.5.5.5:443, 119.29.29.29:443
timeout = 1.5
interval = 1.5
strikes_down = 3
strikes_up = 3
cooldown = 15
settle = 8

[link:ethernet]
device = eth0
metric = 600

[link:wifi]
device = wlan0
metric = 700
rssi_down = -75       # switch away below this dBm
rssi_up = -65         # allow switch back above this dBm

[link:cellular]
device = enx020c29a39b6d
metric = 800

[zerotier]
enabled = true
service = zerotier-one
interval = 30
offline_threshold = 3
```

Links are ordered by appearance; `metric` must increase down the file. Devices that
do not exist are skipped automatically.

Then `sudo systemctl restart net-failover`.

## Operate

```sh
systemctl status net-failover        # service state
journalctl -u net-failover -f        # live switch log
ip route show default                # metric 50 == active link
sudo ./tests/switch-test.py --yes    # measure failover times (briefly drops links)
```

## How it works

1. Every `interval` seconds, probe each link **in parallel** with `SO_BINDTODEVICE`
   + TCP connect to the configured targets.
2. Keep a backup default route on every link that has an IP and a gateway.
3. Promote the best healthy link with an extra `metric 50` route (`ip route replace`).
4. If the active link fails `strikes_down` times, promote the next healthy link;
   if a higher-priority link recovers `strikes_up` times, promote it back.
5. If no link is healthy, do nothing — the existing route is never removed.

More detail in [docs/DESIGN.md](docs/DESIGN.md).

## Troubleshooting: cellular dongle rebooting on the USB bus

Symptom: the network icon spins every few seconds and `dmesg` shows the same USB port
doing `new high-speed USB device` / `USB disconnect` in a loop. That is the modem
resetting itself, not the failover logic.

Check that it is bus-level, not our daemon:

```sh
dmesg -T | grep -iE 'usb 1-1\.[0-9]+: (new|USB disconnect)' | tail
sudo systemctl stop net-failover
journalctl -k --since '60 sec ago' | grep -c 'new high-speed'   # same count -> hardware
sudo systemctl start net-failover
```

Usual causes: insufficient power on the port/hub, bad cable or connector, overheating or
firmware crash. Fix by moving to a powered hub / another port, reseating the module.

While it is broken, tell NetworkManager to ignore the device so the icon stops spinning:

```sh
sudo tee /etc/NetworkManager/conf.d/99-unmanaged-4g.conf >/dev/null <<'EOF'
[keyfile]
unmanaged-devices=interface-name:enx020c29a39b6d
EOF
sudo systemctl reload NetworkManager
```

Remove that file and reload NetworkManager to restore the cellular uplink.

## Faster ZeroTier after boot

`zt-boot-connect` waits until the network really works, then restarts ZeroTier to force
an immediate reconnect (measured: **online 6.4 s after the network is ready**).
`net-failover` also normalizes stray `metric 0` default routes added by dhclient.

## Requirements

- Linux with `iproute2`, `python3` (3.6+), systemd.
- NetworkManager (optional, used for gateway lookup and for `tests/switch-test.py`).
- ZeroTier (optional, for `zt-watchdog`).

## License

MIT — see [LICENSE](LICENSE).
