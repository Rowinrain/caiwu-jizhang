"""在线更新：GitHub 连不上时改走加速地址，且安装前要多个来源一致。"""
import hashlib
import json
import tempfile
import shutil
import unittest
from unittest import mock

from app import db, updater
from app.db import AppError

GH = 'https://github.com/Rowinrain/caiwu-jizhang/releases/latest/download/latest.json'
PKG = b'fake-zip'


def manifest(sha=None, version='99.0.0'):
    return json.dumps({'version': version, 'url': '%E6%9B%B4%E6%96%B0.zip',
                       'sha256': sha or hashlib.sha256(PKG).hexdigest()}).encode()


class MirrorTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        p = mock.patch.object(updater, 'DATA_DIR', tmp)
        p.start()
        self.addCleanup(p.stop)
        self.calls = []

    def run_with(self, responses, fn, *args):
        """responses: 网址 → 内容（bytes）；没有列出的网址当作连不上。"""
        def fake(url, limit=0, timeout=30, progress=None):
            self.calls.append(url)
            if url in responses:
                return responses[url]
            raise AppError('无法连接更新地址：timed out')
        with mock.patch.object(updater, '_fetch', fake), \
                mock.patch.object(updater, 'apply_update', lambda d, p=None: {'applied': d}):
            return fn(*args)

    def test_sources(self):
        s = updater._sources(GH)
        self.assertEqual(s[0], ('直连', GH))
        self.assertEqual(s[1], ('https://ghfast.top/', 'https://ghfast.top/' + GH))
        # 已经填了加速地址的，也能还原
        s2 = updater._sources('https://example.com/' + GH)
        self.assertEqual(s2[0], ('自定义', 'https://example.com/' + GH))
        self.assertIn(('直连', GH), s2)
        # 不是 GitHub 的地址不加加速
        self.assertEqual(updater._sources('https://x.cn/latest.json'), [('直连', 'https://x.cn/latest.json')])

    def test_direct_ok(self):
        dl = 'https://github.com/Rowinrain/caiwu-jizhang/releases/latest/download/%E6%9B%B4%E6%96%B0.zip'
        r = self.run_with({GH: manifest(), dl: PKG}, updater.update_online, GH)
        self.assertEqual(r, {'applied': PKG})

    def test_direct_manifest_mirror_download(self):
        dl = 'https://github.com/Rowinrain/caiwu-jizhang/releases/latest/download/%E6%9B%B4%E6%96%B0.zip'
        r = self.run_with({GH: manifest(), 'https://gh.llkk.cc/' + dl: PKG}, updater.update_online, GH)
        self.assertEqual(r, {'applied': PKG})

    def test_check_via_single_mirror(self):
        r = self.run_with({'https://gh-proxy.com/' + GH: manifest()}, updater.check_online, GH)
        self.assertTrue(r['has_update'])
        self.assertEqual(r['source'], 'https://gh-proxy.com/')

    def test_install_needs_two_mirrors(self):
        with self.assertRaises(AppError) as cm:
            self.run_with({'https://gh-proxy.com/' + GH: manifest()}, updater.update_online, GH)
        self.assertIn('交叉确认', str(cm.exception))

    def test_install_via_mirrors(self):
        m1, m2 = 'https://ghfast.top/', 'https://ghproxy.net/'
        dl = 'https://github.com/Rowinrain/caiwu-jizhang/releases/latest/download/%E6%9B%B4%E6%96%B0.zip'
        r = self.run_with({m1 + GH: manifest(), m2 + GH: manifest(), m2 + dl: PKG},
                          updater.update_online, GH)
        self.assertEqual(r, {'applied': PKG})
        self.assertIn(m1 + dl, self.calls)  # 第一个下载失败后换下一个

    def test_mirrors_disagree(self):
        m1, m2 = 'https://ghfast.top/', 'https://ghproxy.net/'
        with self.assertRaises(AppError):
            self.run_with({m1 + GH: manifest(), m2 + GH: manifest(sha='0' * 64)}, updater.update_online, GH)

    def test_bad_package_rejected(self):
        m1, m2 = 'https://ghfast.top/', 'https://ghproxy.net/'
        dl = 'https://github.com/Rowinrain/caiwu-jizhang/releases/latest/download/%E6%9B%B4%E6%96%B0.zip'
        with self.assertRaises(AppError) as cm:
            self.run_with({m1 + GH: manifest(), m2 + GH: manifest(), m1 + dl: b'evil'},
                          updater.update_online, GH)
        self.assertIn('校验失败', str(cm.exception))

    def test_all_down(self):
        with self.assertRaises(AppError) as cm:
            self.run_with({}, updater.check_online, GH)
        self.assertIn('加速地址都连不上', str(cm.exception))


if __name__ == '__main__':
    unittest.main()
