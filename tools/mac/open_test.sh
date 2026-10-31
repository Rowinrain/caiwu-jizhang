#!/bin/bash
# Mac 版“双击打开”自动测试：像用户一样用 open 打开 .app（不经过终端），检查独立窗口是否打开。
# 用法（在软件根目录）：bash tools/mac/open_test.sh [quarantine]
#   quarantine：先给 .app 加上“从网上下载”的隔离标记，模拟浏览器下载后解压的情况
set -u
cd "$(dirname "$0")/../.."
V=$(python3 -c "import re;print(re.search(r\"VERSION = '([^']+)'\", open('app/__init__.py', encoding='utf-8').read()).group(1))")
python3 tools/build.py --only mac >/dev/null || exit 1
W=$(mktemp -d)
unzip -q "dist/财务记账软件-v$V-Mac.zip" -d "$W"
APP="$W/财务记账软件.app"
HOME_DIR="$HOME/Library/Application Support/财务记账软件"
rm -rf "$HOME_DIR"
if [ "${1:-}" = "quarantine" ]; then
  xattr -r -w com.apple.quarantine "0083;$(printf %x "$(date +%s)");Safari;" "$APP"
fi
open "$APP"
OK=0
for i in $(seq 1 60); do
  sleep 2
  S=$(curl -s -m 2 http://127.0.0.1:8765/api/desktop/status || true)
  [ -n "$S" ] && echo "第 ${i} 次检查：$S"
  case "$S" in *'"mode": "window"'*) OK=1; break ;; esac
done
if [ $OK = 1 ]; then
  echo "---- 软件开着时再双击一次 ----"
  open "$APP"; echo "open 退出代码：$?"
  sleep 8
  curl -s -m 2 http://127.0.0.1:8765/api/desktop/status; echo
  echo "---- 关掉窗口后再双击 ----"
  pkill -f -- '--window' ; sleep 4
  curl -s -m 2 http://127.0.0.1:8765/api/version >/dev/null && echo "后台服务还在运行（不应该）" || echo "后台服务已退出"
  open "$APP"; echo "open 退出代码：$?"
  sleep 15
  S2=$(curl -s -m 2 http://127.0.0.1:8765/api/desktop/status); echo "$S2"
  case "$S2" in *'"mode": "window"'*) ;; *) echo "再次打开失败"; OK=0 ;; esac
fi
echo "---- 进程 ----"; ps aux | grep -i "[p]ython3" || true
screencapture -x "${RUNNER_TEMP:-/tmp}/mac-open.png" 2>/dev/null || true
echo "---- 运行日志 ----"; cat "$HOME_DIR/data/运行日志.txt" 2>/dev/null || echo "（没有日志）"
curl -s -m 3 -X POST -H 'Content-Type: application/json' -d '{}' http://127.0.0.1:8765/api/shutdown >/dev/null || true
sleep 2
[ $OK = 1 ] && echo "结果：独立窗口已打开" || { echo "结果：独立窗口没有打开"; exit 1; }
