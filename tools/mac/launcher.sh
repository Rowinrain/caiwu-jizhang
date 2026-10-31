#!/bin/bash
# 财务记账软件.app 的启动脚本：选择对应芯片的 Python 运行环境，在后台启动软件后立即退出。
C="$(cd "$(dirname "$0")/.." && pwd)"
# 按芯片选择。不用 uname -m：启动器脚本可能在 Rosetta 转译下运行，那时 uname 会误报 x86_64
if [ "$(sysctl -n hw.optional.arm64 2>/dev/null)" = "1" ]; then
  RT="$C/Resources/runtime-arm64"
else
  RT="$C/Resources/runtime-x86_64"
fi
# 去掉下载时系统加上的“隔离”标记，否则内置的 Python 可能被拦截
xattr -dr com.apple.quarantine "$C/.." >/dev/null 2>&1
"$RT/bin/python3" "$C/Resources/mac_launch.py" >/dev/null 2>&1 &
disown
exit 0
