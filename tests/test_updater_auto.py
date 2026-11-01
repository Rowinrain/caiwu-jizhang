"""打开软件时自动检查新版本：只查一次、失败不报错、可以关掉。"""
import json
import shutil
import tempfile
import unittest
from unittest import mock

from app import updater, VERSION
from app.db import AppError


class AutoCheckTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        p = mock.patch.object(updater, 'DATA_DIR', tmp)
        p.start()
        self.addCleanup(p.stop)
        p2 = mock.patch.dict(updater._auto, {'done': False, 'result': None})
        p2.start()
        self.addCleanup(p2.stop)
        self.calls = 0

    def fake_check(self, result=None, error=None):
        def f(url):
            self.calls += 1
            if error:
                raise error
            return result
        return mock.patch.object(updater, 'check_online', f)

    def test_default_on_and_new_version(self):
        self.assertTrue(updater.get_auto_check())
        res = {'current': VERSION, 'latest': '99.0.0', 'notes': '新功能', 'has_update': True, 'download': 'x'}
        with self.fake_check(res):
            r1 = updater.auto_check()
            r2 = updater.auto_check()
        self.assertTrue(r1['has_update'])
        self.assertEqual(r1['latest'], '99.0.0')
        self.assertEqual(r1, r2)
        self.assertEqual(self.calls, 1)  # 每次启动只联网一次

    def test_network_failure_is_silent(self):
        with self.fake_check(error=AppError('无法连接更新地址：timed out')):
            r = updater.auto_check()
            updater.auto_check()
        self.assertEqual(r, {'enabled': True, 'has_update': False, 'current': VERSION})
        self.assertEqual(self.calls, 1)  # 失败了也不反复重试，免得拖慢

    def test_unexpected_error_is_silent(self):
        with self.fake_check(error=ValueError('boom')):
            self.assertFalse(updater.auto_check()['has_update'])

    def test_already_latest(self):
        with self.fake_check({'current': VERSION, 'latest': VERSION, 'has_update': False}):
            self.assertFalse(updater.auto_check()['has_update'])

    def test_turn_off(self):
        updater.set_update_url('https://example.com/latest.json')
        updater.set_auto_check(False)
        self.assertFalse(updater.get_auto_check())
        with self.fake_check(error=AssertionError('不应该联网')):
            r = updater.auto_check()
        self.assertEqual(r['enabled'], False)
        self.assertEqual(self.calls, 0)
        # 关掉开关不会弄丢其它设置
        with open(updater._settings_path(), encoding='utf-8') as f:
            s = json.load(f)
        self.assertEqual(s['update_url'], 'https://example.com/latest.json')
        updater.set_auto_check(True)
        self.assertTrue(updater.get_auto_check())


if __name__ == '__main__':
    unittest.main()
