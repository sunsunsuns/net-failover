PREFIX ?= /usr/local/sbin
CONFDIR ?= /etc/net-failover

.PHONY: install uninstall test check

install:
	sudo ./install.sh

uninstall:
	sudo ./uninstall.sh

# 实测切换耗时（会短暂断网）
test:
	sudo ./tests/switch-test.py --yes

# 语法检查，不需要 root
check:
	python3 -m py_compile bin/net-failover bin/zt-watchdog tests/switch-test.py
	bash -n install.sh uninstall.sh
	@echo "syntax OK"
