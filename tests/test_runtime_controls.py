from pathlib import Path
from types import SimpleNamespace
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from account_personas import PersonaStore
from runtime_controls import RuntimeControls
from runtime_status import RuntimeSnapshot


class Harness(RuntimeControls):
    def __init__(self, store):
        self.services = SimpleNamespace(ROOT=store.root, persona_store=lambda: store)
        self.after = Mock(return_value='timer')
        self.after_cancel = Mock()
        self.protocol = Mock()
        self.destroy = Mock()
        self.render_accounts = Mock()
        self.write_log = Mock()
        self.start_runtime_monitor()


class RuntimeControlsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.store = PersonaStore(root / 'project', root / 'state')
        self.persona = self.store.save('测试', '# 测试\n原来的语气')
        self.store.assign('user', self.persona['id'])
        self.ui = Harness(self.store)

    def run_worker(self):
        before_tk_calls = self.ui.after.call_count
        with patch('runtime_controls.threading.Thread') as thread, patch('runtime_controls.probe_runtime', return_value=RuntimeSnapshot('ready')):
            self.ui._request_runtime_check()
            worker = thread.call_args.kwargs['target']
            worker()
        self.assertEqual(self.ui.after.call_count, before_tk_calls, 'Background checks must not call Tk')
        self.ui._poll_runtime_results()

    def test_source_edit_is_applied_without_changing_configuration(self):
        self.run_worker()
        before = self.store.config.read_bytes()
        self.store.profile_path(self.persona['id']).write_text('# 测试\n更新后的语气', encoding='utf-8')
        self.run_worker()
        workspace = self.store.workspace('user', self.persona['id'])
        self.assertIn('更新后的语气', (workspace / 'SOUL.md').read_text(encoding='utf-8'))
        self.assertEqual(self.store.config.read_bytes(), before)
        self.ui.write_log.assert_any_call('测试的设定已自动同步，下一轮聊天生效。')

    def test_checks_do_not_overlap_and_manual_refresh_is_not_lost(self):
        self.ui._runtime_check_busy = True
        with patch('runtime_controls.threading.Thread') as thread:
            self.ui.refresh_runtime_status()
            thread.assert_not_called()
        self.assertTrue(self.ui._runtime_refresh_again)
        self.ui._runtime_results.put((RuntimeSnapshot('ready'), {}, {}, []))
        with patch.object(self.ui, '_request_runtime_check') as again:
            self.ui._poll_runtime_results()
        again.assert_called_once()

    def test_close_cancels_ui_timer_and_never_stops_gateway(self):
        self.ui.close_workbench()
        self.assertTrue(self.ui._runtime_closed)
        self.ui.after_cancel.assert_called_once_with('timer')
        self.ui.destroy.assert_called_once()
        with patch('runtime_controls.threading.Thread') as thread:
            self.ui._request_runtime_check()
            thread.assert_not_called()

    def test_slow_check_turns_previous_green_sample_stale(self):
        self.ui._runtime_snapshot = RuntimeSnapshot('ready', observed_at=time.monotonic() - 40)
        self.ui._runtime_check_busy = True
        self.ui._poll_runtime_results()
        self.ui.render_accounts.assert_called_once()
        self.assertTrue(self.ui._runtime_stale_shown)


if __name__ == '__main__':
    unittest.main()
