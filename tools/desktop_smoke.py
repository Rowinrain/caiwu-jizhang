"""
独立窗口的自动测试：打包 → 解压 → 用安装包里自带的 Python 启动软件，真的打开窗口，
确认页面脚本正常运行、页面能调用窗口功能，然后自动关窗口、软件退出。
GitHub Actions 在 Windows 和 Mac 上运行（.github/workflows/tests.yml）；本机也可以运行：
    python3 tools/desktop_smoke.py win10     # 在 Windows 10/11 上
    python3 tools/desktop_smoke.py mac       # 在 Mac 上
窗口打不开（改用浏览器）或页面没正常运行时，退出代码不为 0。
"""
import os
import subprocess
import sys
import tempfile
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'tools'))
import build  # noqa: E402


def main():
    kind = sys.argv[1] if len(sys.argv) > 1 else ('mac' if sys.platform == 'darwin' else 'win10')
    ver = build.version()
    os.makedirs(build.DIST, exist_ok=True)
    pkg = build.build_mac(ver) if kind == 'mac' else build.build_windows(ver)
    print('安装包：%s（%.1f MB）' % (os.path.basename(pkg), os.path.getsize(pkg) / 1048576))
    work = tempfile.mkdtemp(prefix='caiwu-smoke-')
    with zipfile.ZipFile(pkg) as z:
        for info in z.infolist():
            path = z.extract(info, work)
            mode = (info.external_attr >> 16) & 0o777
            if mode:
                os.chmod(path, mode)
    data = os.path.join(work, 'data')
    env = dict(os.environ, RENTAL_DESKTOP_SMOKE='1', RENTAL_DATA_DIR=data,
               RENTAL_BACKUP_DIR=os.path.join(work, 'backups'), PORT='8790', PYTHONIOENCODING='utf-8')
    env.pop('PYTHONPATH', None)
    if kind == 'mac':
        res = os.path.join(work, build.NAME + '.app', 'Contents', 'Resources')
        arch = 'arm64' if os.uname().machine == 'arm64' else 'x86_64'
        cmd = [os.path.join(res, 'runtime-' + arch, 'bin', 'python3'), os.path.join(res, 'mac_launch.py')]
    else:
        app = os.path.join(work, build.NAME)
        cmd = [os.path.join(app, 'runtime', 'python.exe'), os.path.join(app, 'start.py'), '--no-shortcut']
    print('运行：', ' '.join(cmd))
    try:
        code = subprocess.call(cmd, env=env, timeout=240)
    except subprocess.TimeoutExpired:
        print('超时：软件 240 秒内没有自动退出')
        code = 124
    print('退出代码：', code)
    log = os.path.join(data, '运行日志.txt')
    if os.path.exists(log):
        with open(log, encoding='utf-8', errors='replace') as f:
            print('---- 运行日志 ----\n' + f.read())
    return code


if __name__ == '__main__':
    sys.exit(main())
