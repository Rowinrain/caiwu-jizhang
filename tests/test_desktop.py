"""独立桌面窗口：窗口子进程的管理（打开、关掉即退出、打不开改用浏览器、再次双击调到前面）。
用一个假的子进程代替真正的窗口，所以在没有图形界面的电脑上也能测。真正打开窗口的测试见 tools/desktop_smoke.py。"""
import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
from unittest import mock

from app import db, desktop


class FakeWindow(desktop.WindowProcess):
    """子进程换成一段 Python 小程序。"""

    def __init__(self, script, **kw):
        self.script = script
        self.closed = threading.Event()
        self.fallen = threading.Event()
        super().__init__('http://127.0.0.1:1/', on_closed=self.closed.set,
                         fallback=lambda url: self.fallen.set(), **kw)

    def command(self):
        return [sys.executable, '-c', self.script]


READY_THEN_WAIT = (
    "import sys\n"
    "print('ready', flush=True)\n"
    "for line in sys.stdin:\n"
    "    if line.strip() == 'show': print('got show', flush=True)\n"
    "    if line.strip() == 'quit': break\n"
)


class WindowProcessTest(unittest.TestCase):
    def test_user_closes_window_quits_app(self):
        w = FakeWindow("print('ready', flush=True)").start()
        self.assertTrue(w.closed.wait(10), '关掉窗口后应退出软件')
        self.assertFalse(w.fallen.is_set())

    def test_unavailable_falls_back_to_browser(self):
        w = FakeWindow("import sys; print('unavailable 没有 WebView2', flush=True); sys.exit(3)").start()
        self.assertTrue(w.fallen.wait(10), '窗口打不开时应改用浏览器')
        self.assertFalse(w.closed.is_set(), '窗口打不开不能把软件关掉')
        self.assertEqual(w.reason, '没有 WebView2')

    def test_crash_before_ready_falls_back(self):
        w = FakeWindow("import os; os._exit(1)").start()
        self.assertTrue(w.fallen.wait(10))
        self.assertFalse(w.closed.is_set())

    def test_hang_falls_back_after_timeout(self):
        w = FakeWindow("import time; time.sleep(30)", timeout=1).start()
        self.assertTrue(w.fallen.wait(10))
        self.assertFalse(w.closed.is_set())
        w.proc.wait(10)  # 卡住的子进程被结束

    def test_show_and_stop(self):
        w = FakeWindow(READY_THEN_WAIT).start()
        self.assertTrue(w.ready.wait(10))
        self.assertTrue(w.show())
        w.stop()
        self.assertIsNotNone(w.proc.poll(), '退出软件时窗口子进程应结束')
        self.assertFalse(w.closed.is_set(), '主动退出时不再重复关闭服务')
        self.assertFalse(w.fallen.is_set())
        self.assertFalse(w.show())

    def test_fallback_only_once(self):
        calls = []
        w = desktop.WindowProcess('u', on_closed=lambda: None, fallback=calls.append)
        w._fail('a')
        w._fail('b')
        self.assertEqual(calls, ['u'])


class SettingsTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        p = mock.patch.object(db, 'DATA_DIR', tmp)
        p.start()
        self.addCleanup(p.stop)
        self.tmp = tmp

    def test_prefer_browser_blocks_window(self):
        self.assertEqual(desktop.get_prefer(), 'window')
        desktop.set_prefer('browser')
        self.assertEqual(desktop.get_prefer(), 'browser')
        self.assertIn('浏览器', desktop.window_blocker())
        desktop.set_prefer('window')
        self.assertEqual(desktop.get_prefer(), 'window')

    def test_prefer_keeps_other_settings(self):
        with open(os.path.join(self.tmp, 'settings.json'), 'w', encoding='utf-8') as f:
            json.dump({'update_url': 'https://example.com/latest.json'}, f)
        desktop.set_prefer('browser')
        with open(os.path.join(self.tmp, 'settings.json'), encoding='utf-8') as f:
            s = json.load(f)
        self.assertEqual(s['update_url'], 'https://example.com/latest.json')
        self.assertEqual(s['ui_mode'], 'browser')

    def test_no_component_uses_browser(self):
        with mock.patch.object(desktop, 'has_window_component', return_value=False), \
                mock.patch.dict(os.environ, {'RENTAL_BROWSER': ''}):
            self.assertIn('窗口组件', desktop.window_blocker())

    def test_open_ui_falls_back_without_component(self):
        with mock.patch.object(desktop, 'has_window_component', return_value=False), \
                mock.patch.object(desktop, 'open_browser') as ob:
            self.assertIsNone(desktop.open_ui('http://127.0.0.1:9/', on_closed=lambda: None))
        ob.assert_called_once_with('http://127.0.0.1:9/')
        self.assertEqual(desktop.status()['mode'], 'browser')


class HelpersTest(unittest.TestCase):
    def test_filename(self):
        f = desktop.filename_from_disposition
        self.assertEqual(f("attachment; filename*=UTF-8''%E5%85%AC%E5%8F%B8-%E5%A4%87%E4%BB%BD.db"), '公司-备份.db')
        self.assertEqual(f('attachment; filename="a.xlsx"'), 'a.xlsx')
        self.assertEqual(f("attachment; filename*=UTF-8''..%2F..%2Fevil.txt"), 'evil.txt')
        self.assertEqual(f(''), '')

    def test_save_file_rejects_other_urls(self):
        api = desktop.Api('http://127.0.0.1:1/')
        with mock.patch.dict(sys.modules, {'webview': mock.MagicMock()}):
            self.assertIn('error', api.save_file('https://evil.example/x'))
            self.assertIn('error', api.save_file('/index.html'))

    def test_open_external_only_http(self):
        api = desktop.Api('http://127.0.0.1:1/')
        with mock.patch('webbrowser.open') as wo:
            self.assertFalse(api.open_external('file:///etc/passwd'))
            self.assertTrue(api.open_external('https://github.com/'))
        wo.assert_called_once_with('https://github.com/')


if __name__ == '__main__':
    unittest.main()
