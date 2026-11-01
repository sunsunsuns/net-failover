#!/usr/bin/env python3
"""switch-test — 实测链路切换与恢复耗时.

做法：按配置里的链路顺序，依次"断开链路 -> 观察自动切换 -> 恢复链路"，
      期间高频探测互联网可达性，记录：
        * 路由切换耗时：从动作发生到默认路由变成期望设备
        * 业务断流耗时：从动作发生到互联网重新可达

注意：测试会真的断开网卡（nmcli device disconnect），会短暂断网。
      需要 root，且依赖 NetworkManager（nmcli）。

用法：
    sudo ./tests/switch-test.py --config /etc/net-failover/config.ini --yes
    sudo ./tests/switch-test.py --json report.json
"""

import argparse
import configparser
import json
import os
import socket
import subprocess
import sys
import threading
import time


def sh(cmd, timeout=60):
    return subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)


def route_dev(promote_metric):
    out = sh("ip -4 route show default").stdout
    for line in out.splitlines():
        parts = line.split()
        if "metric" in parts and parts[parts.index("metric") + 1] == str(promote_metric):
            return parts[parts.index("dev") + 1] if "dev" in parts else None
    return None


def tcp_ok(host, port, timeout):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect((host, port))
        return True
    except Exception:
        return False
    finally:
        s.close()


class Monitor(threading.Thread):
    def __init__(self, host, port):
        super().__init__(daemon=True)
        self.host, self.port = host, port
        self.samples = []
        self.stop = False

    def run(self):
        while not self.stop:
            t = time.time()
            self.samples.append((t, tcp_ok(self.host, self.port, 0.4)))
            time.sleep(0.05)


def load_links(path):
    cp = configparser.ConfigParser()
    if not cp.read(path, encoding="utf-8"):
        raise SystemExit("读不到配置 %s" % path)
    links = []
    for section in cp.sections():
        if section.startswith("link:") and cp[section].get("device"):
            links.append((section.split(":", 1)[1], cp[section]["device"].strip()))
    probe = cp["probe"] if cp.has_section("probe") else {}
    promote = 50
    general = cp["general"] if cp.has_section("general") else {}
    try:
        promote = int(general.get("promote_metric", 50))
    except ValueError:
        pass
    raw = probe.get("targets", "223.5.5.5:443").split(",")[0].strip()
    host, _, port = raw.rpartition(":")
    return links, promote, host, int(port)


def scenario(name, action, expect, promote, host, port, maxwait):
    th = Monitor(host, port)
    th.start()
    time.sleep(1.0)
    t0 = time.time()
    sh(action)
    route_t = http_t = None
    while time.time() - t0 < maxwait:
        if route_t is None and route_dev(promote) == expect:
            route_t = time.time() - t0
        if http_t is None:
            if any(ok and t >= t0 for t, ok in th.samples):
                http_t = time.time() - t0
        if route_t is not None and http_t is not None:
            break
        time.sleep(0.05)
    time.sleep(0.8)
    th.stop = True
    th.join(timeout=2)
    print("%-24s -> %-16s 路由 %-8s 断流 %-8s"
          % (name, expect,
             "timeout" if route_t is None else "%.2fs" % route_t,
             "timeout" if http_t is None else "%.2fs" % http_t), flush=True)
    return {"scenario": name, "expected": expect,
            "route_switch_s": None if route_t is None else round(route_t, 2),
            "outage_s": None if http_t is None else round(http_t, 2)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="/etc/net-failover/config.ini")
    ap.add_argument("--yes", action="store_true", help="跳过确认")
    ap.add_argument("--json", help="把结果写入 JSON 文件")
    ap.add_argument("--wait", type=float, default=45, help="单步最长等待秒数")
    args = ap.parse_args()

    if os.geteuid() != 0:
        raise SystemExit("需要 root（会操作网卡）：sudo %s" % sys.argv[0])

    links, promote, host, port = load_links(args.config)
    if len(links) < 2:
        raise SystemExit("配置里至少需要两条链路才能测试")

    print("链路顺序：%s" % " > ".join("%s(%s)" % (n, d) for n, d in links))
    print("探测目标：%s:%d" % (host, port))
    print("基线出口：%s" % route_dev(promote))

    if not args.yes:
        if input("\n测试会真实断开网卡，可能短暂断网。继续？[y/N] ").strip().lower() != "y":
            raise SystemExit("已取消")

    results = []
    (n0, d0), (n1, d1) = links[0], links[1]
    results.append(scenario("断开 %s" % n0, "nmcli device disconnect %s" % d0,
                            d1, promote, host, port, args.wait))
    time.sleep(1)
    if len(links) > 2:
        (n2, d2) = links[2]
        results.append(scenario("断开 %s" % n1, "nmcli device disconnect %s" % d1,
                                d2, promote, host, port, args.wait))
        time.sleep(1)
    results.append(scenario("恢复 %s" % n0, "nmcli device connect %s" % d0,
                            d0, promote, host, port, args.wait))
    time.sleep(1)
    results.append(scenario("恢复 %s" % n1, "nmcli device connect %s" % d1,
                            d0, promote, host, port, args.wait))

    print("\n==== 汇总 ====")
    for r in results:
        print("%-24s 路由 %-8s 断流 %-8s"
              % (r["scenario"],
                 "timeout" if r["route_switch_s"] is None else "%.2fs" % r["route_switch_s"],
                 "timeout" if r["outage_s"] is None else "%.2fs" % r["outage_s"]))
    print("\n最终出口：%s" % route_dev(promote))

    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump({"tested_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                       "links": [{"name": n, "device": d} for n, d in links],
                       "results": results}, f, ensure_ascii=False, indent=2)
        print("结果已写入 %s" % args.json)


if __name__ == "__main__":
    main()
