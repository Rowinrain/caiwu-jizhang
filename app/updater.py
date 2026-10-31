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


def _fetch(url, limit=200 * 1024 * 1024, timeout=30):
    req = urllib.request.Request(_quote_url(url), headers={'User-Agent': 'CaiwuJizhang/%s' % VERSION})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = r.read(limit + 1)
    except Exception as e:
        raise AppError('无法连接更新地址：%s' % e)
    if len(data) > limit:
        raise AppError('下载的文件太大')
    return data


# 国内网络经常打不开 GitHub。更新地址是 GitHub 上的文件时，直连失败会自动改走下面这些
# GitHub 加速地址（格式都是“加速地址 + 完整的 GitHub 网址”）。这些是第三方公益服务，
# 可能随时失效，所以准备了好几个；也可以在 data/settings.json 里用 "update_mirrors" 自己指定。
MIRRORS = (
    'https://ghfast.top/',
    'https://gh-proxy.com/',
    'https://ghproxy.net/',
    'https://gh.llkk.cc/',
)
GITHUB = 'https://github.com/'


def _mirrors():
    m = _load_settings().get('update_mirrors')
    if isinstance(m, list) and m:
        return tuple(x.rstrip('/') + '/' for x in m if isinstance(x, str) and re.match(r'^https?://', x))
    return MIRRORS


def _sources(url):
    """返回 [(名称, 网址)]：先直连，GitHub 网址再加上各个加速地址。"""
    url = (url or '').strip()
    i = url.find(GITHUB)
    if i < 0:
        return [('直连', url)]
    gh = url[i:]  # 已经填了加速地址的，也还原成 GitHub 原网址
    out = [('直连', gh)] + [(m, m + gh) for m in _mirrors()]
    if i > 0 and url not in [u for _, u in out]:
        out.insert(0, ('自定义', url))
    return out


def _parse_manifest(raw):
    try:
        info = json.loads(raw.decode('utf-8-sig'))
        info['version'] = str(info['version'])
    except Exception:
        raise AppError('更新地址返回的内容格式不正确')
    return info


def _fetch_manifest(url, need_confirm=False):
    """取 latest.json，返回 (内容, 成功的来源列表[(名称, 网址)])。

    直连成功就用直连的。直连失败改走加速地址；need_confirm 为 True（要真正安装）时，
    至少两个加速地址返回完全一致的内容才算数，防止某一个第三方加速地址被篡改。
    """
    if not url:
        raise AppError('还没有设置更新地址')
    sources = _sources(url)
    direct = sources[0]
    errors = []
    try:
        return _parse_manifest(_fetch(direct[1], 1024 * 1024, timeout=10)), [direct]
    except AppError as e:
        if len(sources) == 1:
            raise
        errors.append(str(e))

    from concurrent.futures import ThreadPoolExecutor

    def get(src):
        try:
            return src, _parse_manifest(_fetch(src[1], 1024 * 1024, timeout=15))
        except AppError:
            return src, None

    with ThreadPoolExecutor(max_workers=len(sources) - 1) as ex:
        got = [(src, info) for src, info in ex.map(get, sources[1:]) if info is not None]
    if not got:
        raise AppError('无法连接更新地址（GitHub 直连和 %d 个国内加速地址都连不上）。'
                       '请检查网络后重试，或下载更新包后用“方法一”安装。%s'
                       % (len(sources) - 1, errors[0] if errors else ''))
    groups = {}
    for src, info in got:
        key = json.dumps(info, sort_keys=True, ensure_ascii=False)
        groups.setdefault(key, (info, []))[1].append(src)
    info, ok = max(groups.values(), key=lambda g: len(g[1]))
    if need_confirm and len(ok) < 2:
        raise AppError('GitHub 连不上，能用的加速地址只有一个（%s），无法交叉确认更新信息的真实性，'
                       '为安全起见已停止。请稍后重试，或下载更新包后用“方法一”安装' % ok[0][0])
    return info, ok


def _summary(info, srcs=None):
    latest = info['version']
    r = {'current': VERSION, 'latest': latest, 'notes': info.get('notes', ''),
         'has_update': _vtuple(latest) > _vtuple(VERSION), 'download': info.get('url', '')}
    if srcs:
        r['source'] = srcs[0][0]
    return r


def check_online(url):
    return _summary(*_fetch_manifest(url))


def update_online(url):
    manifest, srcs = _fetch_manifest(url, need_confirm=True)  # 只取一次，版本判断和校验用同一份内容
    if not _summary(manifest)['has_update']:
        raise AppError('已经是最新版本（%s）' % VERSION)
    if not manifest.get('url'):
        raise AppError('更新信息里没有下载地址')
    want = str(manifest.get('sha256') or '').strip().lower()
    if not re.fullmatch(r'[0-9a-f]{64}', want):
        raise AppError('更新信息里缺少有效的 sha256 校验值，为安全起见已停止更新。'
                       '请联系发布者，或下载更新包后用“离线更新”安装')
    import urllib.parse
    # 下载地址可以写相对于 latest.json 的路径；按能连上的来源依次尝试，每个都要通过 sha256 校验
    # 更新信息已经确认过，安装包有 sha256 把关，所以下载可以再试其余加速地址
    tried = srcs + [x for x in _sources(url) if x not in srcs]
    last = None
    for _, base in tried:
        i = base.find(GITHUB)
        if i >= 0:  # 加速地址：先和 GitHub 原网址拼好，再加上同一个加速前缀
            dl = urllib.parse.urljoin(base[i:], manifest['url'])
            if dl.startswith(GITHUB):
                dl = base[:i] + dl
        else:
            dl = urllib.parse.urljoin(base, manifest['url'])
        try:
            data = _fetch(dl)
        except AppError as e:
            last = last or e  # 校验失败的提示更重要，不被后面的连接失败覆盖
            continue
        if hmac.compare_digest(hashlib.sha256(data).hexdigest(), want):
            return apply_update(data)
        last = AppError('下载的更新包校验失败（文件不完整或被修改），请稍后重试')
    raise last
