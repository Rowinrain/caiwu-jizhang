"""
软件更新：上传新版本的压缩包，替换程序文件（app、web、启动文件、说明），
data（账套数据）和 backups（备份）文件夹不会被改动。替换前会自动备份所有账套和旧版程序。
"""
import datetime
import io
import os
import re
import stat
import zipfile

import hashlib
import hmac
import json
import urllib.request

from . import VERSION, UPDATE_URL
from .db import AppError, ROOT, BACKUP_DIR, DATA_DIR, list_companies, backup_company

# 只允许更新这些位置的文件
ALLOWED_TOP = ('app/', 'web/')
ALLOWED_FILES = ('start.py', '使用说明.md', '开发说明.md', '更新记录.md')


def _find_prefix(names):
    for n in names:
        if n.endswith('app/server.py'):
            prefix = n[:-len('app/server.py')]
            if prefix + 'web/index.html' in names and prefix + 'start.py' in names:
                return prefix
    raise AppError('这不是本软件的安装包：压缩包中找不到 app/server.py、web/index.html 和 start.py')


def _read_version(z, prefix):
    try:
        txt = z.read(prefix + 'app/__init__.py').decode('utf-8')
        m = re.search(r"VERSION\s*=\s*['\"]([^'\"]+)['\"]", txt)
        return m.group(1) if m else '未知'
    except KeyError:
        return '未知'


def _backup_current_code():
    folder = os.path.join(BACKUP_DIR, '旧版本程序')
    os.makedirs(folder, exist_ok=True)
    stamp = datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
    path = os.path.join(folder, 'v%s-%s.zip' % (VERSION, stamp))
    with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as z:
        for top in ('app', 'web'):
            for root, dirs, files in os.walk(os.path.join(ROOT, top)):
                dirs[:] = [d for d in dirs if d != '__pycache__']
                for f in files:
                    full = os.path.join(root, f)
                    z.write(full, os.path.relpath(full, ROOT))
        for f in ALLOWED_FILES:
            full = os.path.join(ROOT, f)
            if os.path.exists(full):
                z.write(full, f)
    return path


def apply_update(data):
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise AppError('文件不是有效的压缩包')
    names = z.namelist()
    prefix = _find_prefix(names)
    new_version = _read_version(z, prefix)
    if new_version != '未知' and _vtuple(new_version) < _vtuple(VERSION):
        raise AppError('压缩包里是旧版本（v%s），比现在的版本（v%s）还旧，不能用来更新' % (new_version, VERSION))

    members = []
    for n in names:
        if not n.startswith(prefix) or n.endswith('/'):
            continue
        rel = n[len(prefix):]
        if '..' in rel.split('/') or rel.startswith('/') or '__pycache__' in rel:
            continue
        if rel.startswith(ALLOWED_TOP) or rel in ALLOWED_FILES:
            members.append((n, rel))
    if not members:
        raise AppError('压缩包里没有可更新的文件')

    # 先备份：所有账套 + 旧版程序
    for c in list_companies():
        if not c.get('broken'):
            backup_company(c['id'], '更新前备份')
    old_code = _backup_current_code()

    for n, rel in members:
        target = os.path.normpath(os.path.join(ROOT, rel))
        if not target.startswith(ROOT) or target.startswith(DATA_DIR) or target.startswith(BACKUP_DIR):
            continue
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, 'wb') as f:
            f.write(z.read(n))
        if rel.endswith('.command'):
            os.chmod(target, os.stat(target).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return {'old_version': VERSION, 'new_version': new_version, 'files': len(members),
            'code_backup': os.path.relpath(old_code, ROOT)}


# ---------------------------------------------------------------- 在线更新
# 更新地址指向一个 JSON 文件（例如放在 GitHub、Gitee 或任何网站上），内容如：
# {"version": "1.5.0", "notes": "本次更新内容……", "url": "https://…/财务记账软件-更新包-v1.5.0.zip", "sha256": "…"}

def _settings_path():
    return os.path.join(DATA_DIR, 'settings.json')


def _load_settings():
    try:
        with open(_settings_path(), encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


def get_update_url():
    return _load_settings().get('update_url') or UPDATE_URL


def set_update_url(url):
    url = (url or '').strip()
    if url and not re.match(r'^https?://', url):
        raise AppError('更新地址应以 http:// 或 https:// 开头')
    s = _load_settings()
    s['update_url'] = url
    with open(_settings_path(), 'w', encoding='utf-8') as f:
        json.dump(s, f, ensure_ascii=False, indent=2)


def _vtuple(v):
    return tuple(int(x) for x in re.findall(r'\d+', v or '0')[:4])


def _quote_url(url):
    """网址里有中文（如文件名）时转成 %xx 编码。"""
    import urllib.parse
    p = urllib.parse.urlsplit(url.strip())
    path = urllib.parse.quote(p.path, safe='/%~+-._')
    query = urllib.parse.quote(p.query, safe='=&%~+-._')
    return urllib.parse.urlunsplit((p.scheme, p.netloc, path, query, p.fragment))


def _fetch(url, limit=200 * 1024 * 1024):
    req = urllib.request.Request(_quote_url(url), headers={'User-Agent': 'CaiwuJizhang/%s' % VERSION})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            data = r.read(limit + 1)
    except Exception as e:
        raise AppError('无法连接更新地址：%s' % e)
    if len(data) > limit:
        raise AppError('下载的文件太大')
    return data


def _fetch_manifest(url):
    if not url:
        raise AppError('还没有设置更新地址')
    try:
        info = json.loads(_fetch(url, 1024 * 1024).decode('utf-8-sig'))
        info['version'] = str(info['version'])
    except AppError:
        raise
    except Exception:
        raise AppError('更新地址返回的内容格式不正确')
    return info


def _summary(info):
    latest = info['version']
    return {'current': VERSION, 'latest': latest, 'notes': info.get('notes', ''),
            'has_update': _vtuple(latest) > _vtuple(VERSION), 'download': info.get('url', '')}


def check_online(url):
    return _summary(_fetch_manifest(url))


def update_online(url):
    manifest = _fetch_manifest(url)  # 只取一次，版本判断和校验用同一份内容
    if not _summary(manifest)['has_update']:
        raise AppError('已经是最新版本（%s）' % VERSION)
    if not manifest.get('url'):
        raise AppError('更新信息里没有下载地址')
    want = str(manifest.get('sha256') or '').strip().lower()
    if not re.fullmatch(r'[0-9a-f]{64}', want):
        raise AppError('更新信息里缺少有效的 sha256 校验值，为安全起见已停止更新。'
                       '请联系发布者，或下载更新包后用“离线更新”安装')
    import urllib.parse
    data = _fetch(urllib.parse.urljoin(url, manifest['url']))  # 下载地址可以写相对于 latest.json 的路径
    if not hmac.compare_digest(hashlib.sha256(data).hexdigest(), want):
        raise AppError('下载的更新包校验失败（文件不完整或被修改），请稍后重试')
    return apply_update(data)
