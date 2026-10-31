"""
打包脚本：生成三个免安装版、一个通用更新包、在线更新用的 latest.json，以及源代码包。

用法（在软件根目录运行）：
    python3 tools/build.py                       # 结果在 dist/ 文件夹
    python3 tools/build.py --base-url https://…/  # latest.json 里的下载地址前缀（用于在线更新）

需要的运行环境压缩包会自动从 GitHub 下载到 tools/cache/（只下载一次）：
  - Windows 10/11 和 Mac：python-build-standalone 的 CPython 3.12
  - Windows 7：PythonWin7 项目的 CPython 3.8.20 嵌入版（64 位 + 32 位）
Windows 启动器 tools/launcher/财务记账软件.exe 已经编译好放在仓库里；修改 launcher.c 后需要按文件头说明重新编译。
"""
import argparse
import hashlib
import io
import json
import os
import re
import shutil
import stat
import sys
import tarfile
import tempfile
import time
import urllib.parse
import urllib.request
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS = os.path.join(ROOT, 'tools')
CACHE = os.path.join(TOOLS, 'cache')
DIST = os.path.join(ROOT, 'dist')
NAME = '财务记账软件'

PBS = 'https://github.com/astral-sh/python-build-standalone/releases/download/20260718/'
RUNTIMES = {
    'win64': PBS + 'cpython-3.12.13%2B20260718-x86_64-pc-windows-msvc-install_only_stripped.tar.gz',
    'mac_arm64': PBS + 'cpython-3.12.13%2B20260718-aarch64-apple-darwin-install_only_stripped.tar.gz',
    'mac_x86_64': PBS + 'cpython-3.12.13%2B20260718-x86_64-apple-darwin-install_only_stripped.tar.gz',
    'win7_x64': 'https://github.com/adang1345/PythonWin7/releases/download/v3.8.20/python-3.8.20-embed-amd64.zip',
    'win7_x86': 'https://github.com/adang1345/PythonWin7/releases/download/v3.8.20/python-3.8.20-embed-win32.zip',
}

# 程序文件（更新包只包含这些）
DOCS = ['使用说明.md', '开发说明.md', '更新记录.md']

# 运行环境里用不到的部分，删掉以减小体积
TRIM_WIN = ['tcl', 'Lib/tkinter', 'Lib/idlelib', 'Lib/test', 'Lib/turtledemo', 'Lib/ensurepip', 'Lib/lib2to3',
            'Lib/site-packages', 'DLLs/_tkinter.pyd', 'DLLs/tcl86t.dll', 'DLLs/tk86t.dll', 'Scripts', 'include', 'libs']
TRIM_MAC = ['lib/python3.12/test', 'lib/python3.12/idlelib', 'lib/python3.12/tkinter', 'lib/python3.12/turtledemo',
            'lib/python3.12/ensurepip', 'lib/python3.12/site-packages', 'lib/tcl8.6', 'lib/tk8.6', 'lib/tcl9.0',
            'lib/tk9.0', 'lib/itcl4.2.4', 'lib/thread2.8.9', 'share', 'include', 'lib/pkgconfig']


def version():
    with open(os.path.join(ROOT, 'app', '__init__.py'), encoding='utf-8') as f:
        return re.search(r"VERSION\s*=\s*['\"]([^'\"]+)['\"]", f.read()).group(1)


def program_files():
    """[(相对路径, 绝对路径)]：app/*.py、web/**、start.py、说明文档。"""
    out = []
    for top in ('app', 'web'):
        for root, dirs, files in os.walk(os.path.join(ROOT, top)):
            dirs[:] = [d for d in dirs if d != '__pycache__']
            for f in sorted(files):
                if f.endswith('.pyc'):
                    continue
                full = os.path.join(root, f)
                out.append((os.path.relpath(full, ROOT).replace(os.sep, '/'), full))
    for f in ['start.py'] + DOCS:
        if os.path.exists(os.path.join(ROOT, f)):
            out.append((f, os.path.join(ROOT, f)))
    return out


def fetch(key):
    os.makedirs(CACHE, exist_ok=True)
    url = RUNTIMES[key]
    path = os.path.join(CACHE, os.path.basename(url).replace('%2B', '+'))
    if not os.path.exists(path):
        print('下载', url)
        with urllib.request.urlopen(url, timeout=600) as r, open(path + '.part', 'wb') as f:
            shutil.copyfileobj(r, f)
        os.replace(path + '.part', path)
    return path


def extract_runtime(key, dest):
    """解压运行环境到 dest（dest 下直接就是 python.exe / bin/python3）。"""
    src = fetch(key)
    tmp = tempfile.mkdtemp()
    if src.endswith('.zip'):
        with zipfile.ZipFile(src) as z:
            z.extractall(dest)
    else:
        with tarfile.open(src) as t:
            t.extractall(tmp)  # noqa: S202  官方发布的运行环境压缩包
        shutil.move(os.path.join(tmp, 'python'), dest)
    shutil.rmtree(tmp, ignore_errors=True)
    trim = TRIM_WIN if key == 'win64' else TRIM_MAC if key.startswith('mac') else []
    for rel in trim:
        p = os.path.join(dest, rel)
        if os.path.isdir(p) and not os.path.islink(p):
            shutil.rmtree(p)
        elif os.path.lexists(p):
            os.remove(p)
    if key.startswith('mac'):
        # bin 里只保留 python3（这个程序是静态链接的，libpython 动态库只在嵌入别的程序时才用）
        bindir = os.path.join(dest, 'bin')
        real = os.path.realpath(os.path.join(bindir, 'python3'))
        tmp_bin = os.path.join(dest, 'python3.tmp')
        shutil.copy2(real, tmp_bin)
        shutil.rmtree(bindir)
        os.makedirs(bindir)
        os.replace(tmp_bin, os.path.join(bindir, 'python3'))
        dylib = os.path.join(dest, 'lib', 'libpython3.12.dylib')
        if os.path.exists(dylib):
            os.remove(dylib)
        lib = os.path.join(dest, 'lib', 'python3.12')
        for d in os.listdir(lib):
            if d.startswith('config-'):
                shutil.rmtree(os.path.join(lib, d))
        dyn = os.path.join(lib, 'lib-dynload')
        for f in os.listdir(dyn):
            if f.startswith('_tkinter'):
                os.remove(os.path.join(dyn, f))


class Zip:
    """写 zip：保留可执行权限，符号链接按实际文件写入，文件名用 UTF-8。"""

    def __init__(self, path):
        self.z = zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED, compresslevel=9)

    def file(self, arc, src, mode=None):
        st = os.stat(src)
        zi = zipfile.ZipInfo(arc, time.localtime(st.st_mtime)[:6])
        zi.compress_type = zipfile.ZIP_DEFLATED
        zi.flag_bits |= 0x800
        m = mode if mode is not None else (st.st_mode & 0o777)
        zi.external_attr = (stat.S_IFREG | m) << 16
        with open(src, 'rb') as f:
            self.z.writestr(zi, f.read())

    def data(self, arc, data, mode=0o644):
        zi = zipfile.ZipInfo(arc, time.localtime()[:6])
        zi.compress_type = zipfile.ZIP_DEFLATED
        zi.flag_bits |= 0x800
        zi.external_attr = (stat.S_IFREG | mode) << 16
        self.z.writestr(zi, data)

    def tree(self, arc_prefix, src_dir):
        for root, dirs, files in os.walk(src_dir, followlinks=True):
            dirs.sort()
            for f in sorted(files):
                full = os.path.join(root, f)
                if not os.path.exists(full):
                    continue  # 断开的符号链接
                rel = os.path.relpath(full, src_dir).replace(os.sep, '/')
                self.file(arc_prefix + rel, full)

    def close(self):
        self.z.close()


def add_program(z, prefix):
    for rel, full in program_files():
        z.file(prefix + rel, full, 0o644)


def build_update(ver):
    path = os.path.join(DIST, '%s-更新包-v%s.zip' % (NAME, ver))
    z = Zip(path)
    add_program(z, '%s-更新包-v%s/' % (NAME, ver))
    z.close()
    return path


def build_windows(ver, win7=False):
    label = 'Windows7' if win7 else 'Windows10-11'
    path = os.path.join(DIST, '%s-v%s-%s.zip' % (NAME, ver, label))
    work = tempfile.mkdtemp()
    z = Zip(path)
    pre = NAME + '/'
    add_program(z, pre)
    z.file(pre + NAME + '.exe', os.path.join(TOOLS, 'launcher', NAME + '.exe'), 0o755)
    if win7:
        for key, folder in (('win7_x64', 'runtime-x64'), ('win7_x86', 'runtime-x86')):
            d = os.path.join(work, folder)
            extract_runtime(key, d)
            z.tree(pre + folder + '/', d)
    else:
        d = os.path.join(work, 'runtime')
        extract_runtime('win64', d)
        z.tree(pre + 'runtime/', d)
    z.close()
    shutil.rmtree(work, ignore_errors=True)
    return path


def build_mac(ver):
    path = os.path.join(DIST, '%s-v%s-Mac.zip' % (NAME, ver))
    work = tempfile.mkdtemp()
    z = Zip(path)
    app = NAME + '.app/Contents/'
    with open(os.path.join(TOOLS, 'mac', 'Info.plist.template'), encoding='utf-8') as f:
        z.data(app + 'Info.plist', f.read().replace('{VERSION}', ver).encode('utf-8'))
    z.file(app + 'MacOS/launcher', os.path.join(TOOLS, 'mac', 'launcher.sh'), 0o755)
    z.file(app + 'Resources/AppIcon.icns', os.path.join(TOOLS, 'assets', 'app.icns'), 0o644)
    z.file(app + 'Resources/mac_launch.py', os.path.join(TOOLS, 'mac', 'mac_launch.py'), 0o644)
    add_program(z, app + 'Resources/app_src/')
    for key, folder in (('mac_arm64', 'runtime-arm64'), ('mac_x86_64', 'runtime-x86_64')):
        d = os.path.join(work, folder)
        extract_runtime(key, d)
        z.tree(app + 'Resources/' + folder + '/', d)
    z.file('使用说明.md', os.path.join(ROOT, '使用说明.md'), 0o644)
    z.close()
    shutil.rmtree(work, ignore_errors=True)
    return path


def build_source(ver):
    path = os.path.join(DIST, '%s-源代码-v%s.zip' % (NAME, ver))
    z = Zip(path)
    skip = {'data', 'backups', 'dist', 'cache', '__pycache__'}
    for root, dirs, files in os.walk(ROOT):
        dirs[:] = sorted(d for d in dirs if d not in skip and not d.startswith('.'))
        for f in sorted(files):
            if f.endswith('.pyc'):
                continue
            full = os.path.join(root, f)
            z.file(NAME + '-源代码/' + os.path.relpath(full, ROOT).replace(os.sep, '/'), full)
    z.close()
    return path


def latest_notes(ver):
    try:
        with open(os.path.join(ROOT, '更新记录.md'), encoding='utf-8') as f:
            text = f.read()
        m = re.search(r'^##\s*v?' + re.escape(ver) + r'.*?\n(.*?)(?=^##\s|\Z)', text, re.S | re.M)
        return m.group(1).strip() if m else ''
    except OSError:
        return ''


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--base-url', default='', help='更新包在网上的目录地址，写进 latest.json')
    ap.add_argument('--only', default='', help='只生成指定的包：update,win10,win7,mac,source（逗号分隔）')
    ap.add_argument('--release', action='store_true',
                    help='额外在 dist/release/ 生成发布到 GitHub 用的英文文件名副本（文件名不带版本号，方便固定下载链接）')
    args = ap.parse_args()
    ver = version()
    os.makedirs(DIST, exist_ok=True)
    only = set(filter(None, args.only.split(','))) or {'update', 'win10', 'win7', 'mac', 'source'}
    made = []
    if 'update' in only:
        upd = build_update(ver)
        made.append(upd)
        with open(upd, 'rb') as f:
            sha = hashlib.sha256(f.read()).hexdigest()
        manifest = {'version': ver, 'notes': latest_notes(ver), 'sha256': sha,
                    'url': (args.base_url.rstrip('/') + '/' if args.base_url else '') + urllib.parse.quote(os.path.basename(upd))}
        with open(os.path.join(DIST, 'latest.json'), 'w', encoding='utf-8') as f:
            json.dump(manifest, f, ensure_ascii=False, indent=2)
        made.append(os.path.join(DIST, 'latest.json'))
    if 'win10' in only:
        made.append(build_windows(ver))
    if 'win7' in only:
        made.append(build_windows(ver, win7=True))
    if 'mac' in only:
        made.append(build_mac(ver))
    if 'source' in only:
        made.append(build_source(ver))
    if args.release:
        made += make_release(ver)
    for p in made:
        print('%8.1f MB  %s' % (os.path.getsize(p) / 1048576, os.path.relpath(p, ROOT)))


RELEASE_NAMES = {
    '%s-更新包-v{v}.zip' % NAME: 'caiwu-jizhang-update.zip',
    '%s-v{v}-Windows10-11.zip' % NAME: 'caiwu-jizhang-windows10-11.zip',
    '%s-v{v}-Windows7.zip' % NAME: 'caiwu-jizhang-windows7.zip',
    '%s-v{v}-Mac.zip' % NAME: 'caiwu-jizhang-mac.zip',
}


def make_release(ver):
    """发布用的文件：英文文件名 + 指向同目录更新包的 latest.json。
    放到 GitHub Releases 后，固定地址 …/releases/latest/download/latest.json 永远指向最新版本。"""
    out = os.path.join(DIST, 'release')
    shutil.rmtree(out, ignore_errors=True)
    os.makedirs(out)
    made = []
    for src_tpl, dst in RELEASE_NAMES.items():
        src = os.path.join(DIST, src_tpl.format(v=ver))
        if os.path.exists(src):
            shutil.copyfile(src, os.path.join(out, dst))
            made.append(os.path.join(out, dst))
    upd = os.path.join(out, 'caiwu-jizhang-update.zip')
    if os.path.exists(upd):
        with open(upd, 'rb') as f:
            sha = hashlib.sha256(f.read()).hexdigest()
        with open(os.path.join(out, 'latest.json'), 'w', encoding='utf-8') as f:
            json.dump({'version': ver, 'notes': latest_notes(ver), 'sha256': sha, 'url': 'caiwu-jizhang-update.zip'},
                      f, ensure_ascii=False, indent=2)
        made.append(os.path.join(out, 'latest.json'))
    return made


if __name__ == '__main__':
    sys.exit(main())
