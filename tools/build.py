"""
打包脚本：生成三个免安装版、一个通用更新包、在线更新用的 latest.json，以及源代码包。

用法（在软件根目录运行）：
    python3 tools/build.py                       # 结果在 dist/ 文件夹
    python3 tools/build.py --base-url https://…/  # latest.json 里的下载地址前缀（用于在线更新）

需要的运行环境压缩包会自动从 GitHub 下载到 tools/cache/（只下载一次）：
  - Windows 10/11 和 Mac：python-build-standalone 的 CPython 3.12
  - Windows 7：PythonWin7 项目的 CPython 3.8.20 嵌入版（64 位 + 32 位）
独立桌面窗口组件（pywebview 及依赖）从 PyPI 下载（固定版本、校验 sha256），只放进 Windows 10/11 版和 Mac 版。
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

# 独立桌面窗口组件 pywebview 及其依赖（只放进 Windows 10/11 版和 Mac 版；Windows 7 版用浏览器打开）。
# 固定版本和 sha256，下载后逐个校验。升级时把新的地址和 sha256 一起换掉。
PYPI = 'https://files.pythonhosted.org/packages/'
WINDOW_COMMON = [
    ('3d/25/9491695c22c4842c5b3903b4dc172e0eecf67a27c0af34a71512c9b76a0a/pywebview-6.2.1-py3-none-any.whl',
     '9d07275f53894ab4d5e2e0e996227193e7187dec276d9b624dccbce029216b46'),
    ('83/f6/b55ec74cfe68c6584163faa311503c20b0da4c09883a41e8e00d6726c954/bottle-0.13.4-py2.py3-none-any.whl',
     '045684fbd2764eac9cdeb824861d1551d113e8b683d8d26e296898d3dd99a12e'),
    ('49/d3/b8441a820a491ddfc024b0b0cf0393375b75ea13866d9c66727e54c2fc80/typing_extensions-4.16.0-py3-none-any.whl',
     '481caa481374e813c1b176ada14e97f1f67a4539ce9cfeb3f350d78d6370c2e8'),
    # proxy_tools 只有源码包（只有一个 .py 文件），解压出 proxy_tools/ 文件夹即可
    ('f2/cf/77d3e19b7fabd03895caca7857ef51e4c409e0ca6b37ee6e9f7daa50b642/proxy_tools-0.1.0.tar.gz',
     'ccb3751f529c047e2d8a58440d86b205303cf0fe8146f784d1cbcd94f0a28010'),
]
WINDOW_PKGS = {
    'win': WINDOW_COMMON + [
        ('d8/dc/c96a542079a69d3d091e91808fc8aaf9a0d5e2f8e73a63560e4379134bb6/pythonnet-3.2.1-cp311.cp312.cp313.cp314.cp315-none-any.whl',
         'dd5668b74b0c903d9a24e1ba86f728fc490f8e919d56b3c24bf06739b9ee2b7f'),
        ('5e/da/ec1a6e36624000b6df0dd61183c42342ee5814c073315e802cadaad04d2f/clr_loader-0.3.1-py3-none-any.whl',
         'cbad189de20d202a7d621956b0fc38049e13c9bf7ca2923441eff725cd121aa1'),
        ('d9/79/615cc094e2fb508cade7de88d3b4f6c4ec2bab695c97bce9153dc65aadf5/cffi-2.1.1-cp312-cp312-win_amd64.whl',
         'f53e442b08449d42821fa4a4fba000095af9f62742a500f978a9f557ec44339a'),
        ('b6/ae/315bbeee126a769cbde43cf4908acb4b40408d8bf8bbf334dd7368d8dadf/pycparser-3.12-py3-none-any.whl',
         'a076f3f489b39b43ed13563a1dafe42447445bec2e294154ec5c57fcdda4c85e'),
    ],
    'mac': WINDOW_COMMON + [
        ('68/ac/783ecf1c5045792fb51ad18e0131ff64e33ed9dee9921766c14e03eb27db/pyobjc_core-13.0-cp312-cp312-macosx_10_13_universal2.whl',
         '5818e3d8cc2677a1e767968ebfe3bc0cb7f80c1f7c19c653ee25ef1cc1b3e188'),
        ('ea/44/98d626726809da517c757d77e2888d34eaf758ebad908effb8d0b60c8118/pyobjc_framework_cocoa-13.0-cp312-cp312-macosx_10_13_universal2.whl',
         'e1618d315939c9fae57cb858d4db496ec61f82495ce6a877398131ee494fc19d'),
        ('73/f9/be94503cc5475a076b61b65b64ccb030a2b72bf6fddbe68bae1f20d9e624/pyobjc_framework_quartz-13.0-cp312-cp312-macosx_10_13_universal2.whl',
         '5e1d49f2699d128553a52fad473622ff4fd424d05e879d9585dad0a9f7c3dcfd'),
        ('e6/26/cab74d983eedde4b07a3f79c442717eaf3045911dbd0a00da26701cdeff0/pyobjc_framework_webkit-13.0-cp312-cp312-macosx_10_13_universal2.whl',
         '868cbeca83d079af6f049765d018db731d8fb2fb3bbfaf8be7fd6e96e8c6ca1a'),
        ('e1/79/69acf6f3bbe0cfb26eb5483d9e453e943970c05810886ea3bc1b61d0405e/pyobjc_framework_security-13.0-cp312-cp312-macosx_10_13_universal2.whl',
         '6693e6e083497c6e51ac4533c137f41712b3751cb3c45c3daf7ecc6a197d437e'),
        ('73/90/a18455cfe09b8c4f339acf8667aea124b0fdb14094fd0f1b9a3c6ca2bce6/pyobjc_framework_uniformtypeidentifiers-13.0-py2.py3-none-any.whl',
         '5375270af9980825b3a20267312bbd616e9828c4b7d5ab008c98d9bf6b8a51e3'),
    ],
}
# 窗口组件里用不到的部分（其他系统的界面库、调试符号、测试）
WINDOW_TRIM_DIRS = {'PyObjCTest', 'android', '__pyinstaller', '__pycache__'}
WINDOW_TRIM_FILES = {'gtk.py', 'qt.py', 'cef.py', 'pywebview-android.jar'}

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


def fetch_checked(rel, sha):
    """下载 PyPI 上的文件到 tools/cache/，并核对 sha256。"""
    os.makedirs(CACHE, exist_ok=True)
    path = os.path.join(CACHE, os.path.basename(rel))
    if not os.path.exists(path):
        print('下载', PYPI + rel)
        with urllib.request.urlopen(PYPI + rel, timeout=600) as r, open(path + '.part', 'wb') as f:
            shutil.copyfileobj(r, f)
        os.replace(path + '.part', path)
    with open(path, 'rb') as f:
        got = hashlib.sha256(f.read()).hexdigest()
    if got != sha:
        os.remove(path)
        raise SystemExit('下载的文件校验不通过（sha256 不一致）：%s' % os.path.basename(rel))
    return path


def install_window_component(kind, dest):
    """把窗口组件解压到 dest（一个 site-packages 文件夹）。kind：win / mac。"""
    os.makedirs(dest, exist_ok=True)
    for rel, sha in WINDOW_PKGS[kind]:
        path = fetch_checked(rel, sha)
        if path.endswith('.whl'):
            with zipfile.ZipFile(path) as z:
                for n in z.namelist():
                    parts = n.split('/')
                    if (n.endswith('/') or '..' in parts or any(p in WINDOW_TRIM_DIRS or p.endswith('.dSYM') for p in parts)
                            or parts[-1] in WINDOW_TRIM_FILES or parts[0].endswith('.data')
                            or parts[0].endswith('.dist-info') and parts[-1] == 'RECORD'):
                        continue
                    target = os.path.join(dest, *parts)
                    os.makedirs(os.path.dirname(target), exist_ok=True)
                    with z.open(n) as src, open(target, 'wb') as f:
                        shutil.copyfileobj(src, f)
        else:  # proxy_tools 源码包
            with tarfile.open(path) as t:
                for m in t.getmembers():
                    parts = m.name.split('/')
                    if m.isfile() and len(parts) == 3 and parts[1] == 'proxy_tools' and parts[2].endswith('.py'):
                        target = os.path.join(dest, 'proxy_tools', parts[2])
                        os.makedirs(os.path.dirname(target), exist_ok=True)
                        with t.extractfile(m) as src, open(target, 'wb') as f:
                            shutil.copyfileobj(src, f)
    # Windows：只留 x64 / x86 / arm64 的 WebView2 加载器；Mac 不需要 Windows 的 DLL
    if kind == 'mac':
        shutil.rmtree(os.path.join(dest, 'webview', 'lib'), ignore_errors=True)
    if not os.path.isfile(os.path.join(dest, 'webview', '__init__.py')):
        raise SystemExit('窗口组件安装失败')


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
        install_window_component('win', os.path.join(d, 'Lib', 'site-packages'))
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
    # 窗口组件是 universal2（同时支持两种芯片），两个运行环境共用一份，由 mac_launch.py 加进 PYTHONPATH
    d = os.path.join(work, 'pylib')
    install_window_component('mac', d)
    z.tree(app + 'Resources/pylib/', d)
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


def file_sha256(path):
    """更新包的 sha256，写进 latest.json。软件在线更新时没有它会拒绝安装。"""
    with open(path, 'rb') as f:
        sha = hashlib.sha256(f.read()).hexdigest()
    if len(sha) != 64:
        raise SystemExit('sha256 计算异常：%s' % path)
    return sha


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
        sha = file_sha256(upd)
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
        sha = file_sha256(upd)
        with open(os.path.join(out, 'latest.json'), 'w', encoding='utf-8') as f:
            json.dump({'version': ver, 'notes': latest_notes(ver), 'sha256': sha, 'url': 'caiwu-jizhang-update.zip'},
                      f, ensure_ascii=False, indent=2)
        made.append(os.path.join(out, 'latest.json'))
    # Release 页面上的说明文字（不作为附件上传）
    with open(os.path.join(DIST, 'release-notes.md'), 'w', encoding='utf-8') as f:
        f.write(latest_notes(ver) + '\n\n**下载**：Windows 10/11 选 caiwu-jizhang-windows10-11.zip，Windows 7 选 '
                'caiwu-jizhang-windows7.zip，Mac 选 caiwu-jizhang-mac.zip，解压后双击“财务记账软件”即可。\n'
                '已安装的电脑：在“设置 → 软件更新”里点“检查更新”自动更新；连不上 GitHub 时下载 caiwu-jizhang-update.zip 离线更新。\n')
    return made


if __name__ == '__main__':
    sys.exit(main())
