"""
核心计算的自动化测试（只用 Python 标准库 unittest，兼容 Python 3.8）。

运行方法（在仓库根目录）：
    python3 -m unittest discover -v

app.db 在导入时会根据环境变量决定账套目录，所以这里在任何测试导入 app 之前，
先把账套和备份目录指到临时文件夹，保证测试永远不会碰到 data/、backups/ 里的真实账套。
每个测试用例还会在 support.LedgerTestCase 里另建一个独立的临时目录。
"""
import atexit
import os
import shutil
import tempfile

_TMP = tempfile.mkdtemp(prefix='caiwu-test-')
os.environ['RENTAL_DATA_DIR'] = os.path.join(_TMP, 'data')
os.environ['RENTAL_BACKUP_DIR'] = os.path.join(_TMP, 'backups')
atexit.register(shutil.rmtree, _TMP, True)
