#!/bin/bash
# 财务记账软件.app 的启动脚本：选择对应芯片的 Python 运行环境，在后台启动软件后立即退出。
C="$(cd "$(dirname "$0")/.." && pwd)"
case "$(uname -m)" in
  arm64) RT="$C/Resources/runtime-arm64" ;;
  *)     RT="$C/Resources/runtime-x86_64" ;;
esac
# 去掉下载时系统加上的“隔离”标记，否则内置的 Python 可能被拦截
xattr -dr com.apple.quarantine "$C/.." >/dev/null 2>&1
"$RT/bin/python3" "$C/Resources/mac_launch.py" >/dev/null 2>&1 &
disown
exit 0
