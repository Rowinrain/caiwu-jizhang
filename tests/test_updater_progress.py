"""在线更新进度：下载进度、各个步骤、失败时的中文提示。"""
import hashlib
import io
import json
import os
import shutil
import tempfile
import threading
import time
import unittest
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

from app import updater
from app.db import AppError


def make_pkg(version='99.0.0'):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as z:
        z.writestr('pkg/app/server.py', '# new')
        z.writestr('pkg/app/__init__.py', "VERSION = '%s'\n" % version)
        z.writestr('pkg/web/index.html', '<html></html>')
        z.writestr('pkg/start.py', '# start')
    return buf.getvalue()


class Server:
    """本机临时网站：routes 网址路径 → (状态码, 内容, 额外响应头)。"""

    def __init__(self, routes):
        outer = self
        self.routes = routes

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                code, body, headers = outer.routes.get(self.path, (404, b'not found', {}))
                self.send_response(code)
                self.send_header('Content-Length', str(headers.get('length', len(body))))
                self.end_headers()
                self.wfile.write(body)

        self.httpd = ThreadingHTTPServer(('127.0.0.1', 0), H)
        self.base = 'http://127.0.0.1:%d' % self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


class ProgressTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, True)
        data = os.path.join(self.root, 'data')
        backups = os.path.join(self.root, 'backups')
        os.makedirs(data)
        for top in ('app', 'web'):
            os.makedirs(os.path.join(self.root, top))
        for name, val in (('ROOT', self.root), ('DATA_DIR', data), ('BACKUP_DIR', backups)):
            p = mock.patch.object(updater, name, val)
            p.start()
            self.addCleanup(p.stop)
        p = mock.patch.object(updater, 'list_companies', lambda: [])
        p.start()
        self.addCleanup(p.stop)
        updater._job = None

    def serve(self, routes):
        s = Server(routes)
        self.addCleanup(s.close)
        return s

    def wait_job(self):
        for _ in range(200):
            st = updater.job_status()
            if st['state'] != 'running':
                return st
            time.sleep(0.05)
        self.fail('更新一直没有结束')

    def test_online_job_progress(self):
        pkg = make_pkg() * 1  # 内容不大，但会分块读取
        man = json.dumps({'version': '99.0.0', 'url': 'pkg.zip', 'sha256': hashlib.sha256(pkg).hexdigest()})
        s = self.serve({'/latest.json': (200, man.encode(), {}), '/pkg.zip': (200, pkg, {})})
        updater.start_job('online', s.base + '/latest.json')
        st = self.wait_job()
        self.assertEqual(st['state'], 'done', st)
        self.assertEqual(st['stage'], 'restart')
        self.assertEqual(st['result']['new_version'], '99.0.0')
        self.assertTrue(any('sha256 校验通过' in x for x in st['log']))
        with open(os.path.join(self.root, 'app', 'server.py')) as f:
            self.assertEqual(f.read(), '# new')

    def test_bad_sha_reports_chinese(self):
        pkg = make_pkg()
        man = json.dumps({'version': '99.0.0', 'url': 'pkg.zip', 'sha256': '0' * 64})
        s = self.serve({'/latest.json': (200, man.encode(), {}), '/pkg.zip': (200, pkg, {})})
        updater.start_job('online', s.base + '/latest.json')
        st = self.wait_job()
        self.assertEqual(st['state'], 'error')
        self.assertIn('校验失败', st['error'])
        self.assertFalse(os.path.exists(os.path.join(self.root, 'app', 'server.py')))  # 没有安装

    def test_404_message(self):
        man = json.dumps({'version': '99.0.0', 'url': 'missing.zip', 'sha256': 'a' * 64})
        s = self.serve({'/latest.json': (200, man.encode(), {})})
        with self.assertRaises(AppError) as cm:
            updater.update_online(s.base + '/latest.json')
        self.assertIn('404', str(cm.exception))
        self.assertIn('都没能下载成功', str(cm.exception))

    def test_incomplete_download(self):
        s = self.serve({'/pkg.zip': (200, b'x' * 1000, {'length': 5000})})
        p = updater.Progress()
        with self.assertRaises(AppError) as cm:
            updater._fetch(s.base + '/pkg.zip', progress=p, timeout=2)
        self.assertTrue('不完整' in str(cm.exception) or '中断' in str(cm.exception), str(cm.exception))
        self.assertEqual(p.snapshot()['total'], 5000)

    def test_download_progress_bytes(self):
        body = b'y' * 300000
        s = self.serve({'/f': (200, body, {})})
        p = updater.Progress()
        self.assertEqual(updater._fetch(s.base + '/f', progress=p), body)
        st = p.snapshot()
        self.assertEqual((st['done'], st['total']), (300000, 300000))

    def test_file_job(self):
        updater.start_job('file', make_pkg())
        st = self.wait_job()
        self.assertEqual(st['state'], 'done', st)
        self.assertEqual([x['key'] for x in st['stages']], ['check', 'backup', 'install', 'restart'])

    def test_bad_zip_job(self):
        updater.start_job('file', b'not a zip')
        st = self.wait_job()
        self.assertEqual(st['state'], 'error')
        self.assertIn('压缩包', st['error'])

    def test_one_job_at_a_time(self):
        updater._job = updater.Progress()
        with self.assertRaises(AppError):
            updater.start_job('file', make_pkg())

    def test_net_error_text(self):
        import socket
        import urllib.error
        self.assertIn('连接超时', updater._net_error(urllib.error.URLError(socket.timeout('timed out'))))
        self.assertIn('找不到服务器', updater._net_error(urllib.error.URLError(socket.gaierror(11001, 'x'))))


if __name__ == '__main__':
    unittest.main()
