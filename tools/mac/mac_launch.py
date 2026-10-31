"""
Mac 版启动：把程序文件放到“~/Library/Application Support/财务记账软件”（账套数据也保存在那里），然后启动。
这样即使 .app 放在“下载”文件夹或只读位置也能正常使用，软件内更新也能写入。
.app 里带的版本比已安装的新时，自动覆盖程序文件（data、backups 不动）。
"""
import os
import re
import shutil
import sys

RES = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(RES, 'app_src')
HOME = os.path.join(os.path.expanduser('~'), 'Library', 'Application Support', '财务记账软件')


def version(root):
    try:
        with open(os.path.join(root, 'app', '__init__.py'), encoding='utf-8') as f:
            m = re.search(r"VERSION\s*=\s*['\"]([^'\"]+)['\"]", f.read())
        return tuple(int(x) for x in re.findall(r'\d+', m.group(1)))
    except Exception:
        return ()


def install():
    os.makedirs(HOME, exist_ok=True)
    for name in os.listdir(SRC):
        src, dst = os.path.join(SRC, name), os.path.join(HOME, name)
        if os.path.isdir(src):
            if os.path.exists(dst):
                shutil.rmtree(dst)
            shutil.copytree(src, dst)
        else:
            shutil.copy2(src, dst)


if __name__ == '__main__':
    if not os.path.exists(os.path.join(HOME, 'start.py')) or version(SRC) > version(HOME):
        install()
    os.execv(sys.executable, [sys.executable, os.path.join(HOME, 'start.py')])
