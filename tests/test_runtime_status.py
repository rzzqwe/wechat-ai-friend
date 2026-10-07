from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from account_personas import write_json
from runtime_status import RuntimeSnapshot, account_status, overview, probe_runtime, _channel_snapshot


class RuntimeStatusTests(unittest.TestCase):
    def test_global_install_can_probe_without_a_local_runtime_manifest(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            node = str(root / 'node.exe')
            command = str(root / 'npm/openclaw.cmd')
            with patch('runtime_status.project_runtime', return_value=None), \
                    patch('runtime_status.shutil.which', side_effect=lambda name: node if name == 'node' else command), \
                    patch('runtime_status.subprocess.run', return_value=SimpleNamespace(returncode=0, stdout='{"ok":true,"accounts":[]}')) as execute:
                self.assertTrue(_channel_snapshot(root, root / 'openclaw.json')['ok'])
            self.assertEqual(execute.call_args.args[0][0], node)
            self.assertEqual(execute.call_args.kwargs['env']['WECHAT_AI_OPENCLAW_COMMAND'], command)

    def account(self, status='已启用'):
        return {'id': 'local', 'provider_id': 'wechat-a', 'status': status, 'last_action': '历史操作成功'}

    def snapshot(self, **changes):
        row = {'enabled': True, 'configured': True, 'running': True, 'connected': None,
               'restartPending': False, 'lifecycle': 'starting', 'errorKind': None}
        row.update(changes)
        return RuntimeSnapshot('ready', {'wechat-a': row})

    def test_configured_and_historical_success_never_imply_running(self):
        for saved in ('已启用', '已启动', '启动中', '操作失败'):
            with self.subTest(saved=saved):
                self.assertEqual(account_status(self.account(saved), RuntimeSnapshot('unavailable')).title, '网关未连接')
                self.assertNotEqual(account_status(self.account(saved), None).tone, 'active')

    def test_weixin_long_lived_start_callback_is_not_a_reconnect_signal(self):
        view = account_status(self.account('启动中'), self.snapshot())
        self.assertEqual(view.title, '接收服务运行')
        self.assertEqual(view.tone, 'active')
        self.assertNotIn('在线', view.title)

    def test_disabled_not_configured_and_missing_account_are_distinct(self):
        self.assertEqual(account_status(self.account(), self.snapshot(enabled=False, running=False)).title, '已停用')
        self.assertEqual(account_status(self.account(), self.snapshot(configured=False)).title, '需重新扫码')
        self.assertEqual(account_status(self.account(), RuntimeSnapshot('ready')).title, '账号未就绪')

    def test_running_receiver_does_not_override_disconnected_or_error(self):
        self.assertNotEqual(account_status(self.account(), self.snapshot(connected=False)).tone, 'active')
        self.assertEqual(account_status(self.account(), self.snapshot(errorKind='login')).title, '需重新扫码')
        self.assertEqual(account_status(self.account(), self.snapshot(errorKind='network')).title, '连接异常')
        self.assertEqual(account_status(self.account(), self.snapshot(errorKind='model')).title, '回复服务异常')

    def test_stale_samples_stop_showing_green(self):
        snapshot = self.snapshot()
        now = snapshot.observed_at + 36
        self.assertEqual(account_status(self.account(), snapshot, now=now).title, '状态待确认')
        self.assertNotEqual(overview(snapshot, [self.account()], now=now).tone, 'active')

    def test_rpc_auth_problem_does_not_claim_weixin_running(self):
        snapshot = RuntimeSnapshot('ready', rpc_error='auth_unavailable')
        self.assertEqual(account_status(self.account(), snapshot).title, '状态待确认')
        self.assertIn('微信状态待确认', overview(snapshot, [self.account()]).title)

    def test_incomplete_runtime_fields_are_not_treated_as_online(self):
        snapshot = RuntimeSnapshot('ready', {'wechat-a': {'enabled': True, 'configured': True}})
        self.assertEqual(account_status(self.account(), snapshot).title, '状态待确认')

    def test_unbound_user_does_not_inflate_receiving_account_count(self):
        accounts = [self.account(), {'id': 'new', 'provider_id': '', 'status': '未绑定'}]
        self.assertIn('1/1', overview(self.snapshot(), accounts).title)
        self.assertEqual(account_status(accounts[1], self.snapshot()).title, '未绑定')

    def fixture_config(self, folder):
        path = Path(folder) / 'openclaw.json'
        write_json(path, {'gateway': {'port': 18789, 'auth': {'mode': 'token', 'token': 'fixture-secret'}}})
        return path

    def test_ready_http_without_channel_status_keeps_accounts_unconfirmed(self):
        with tempfile.TemporaryDirectory() as folder:
            path = self.fixture_config(folder)
            def http(url):
                return {'ready': True} if url.endswith('/readyz') else {'ok': True, 'status': 'started'}
            with patch('runtime_status._http_json', side_effect=http), patch('runtime_status._channel_snapshot', return_value={'ok': False, 'error': 'auth_unavailable'}):
                snapshot = probe_runtime(Path(folder), path)
            self.assertEqual(snapshot.gateway, 'ready')
            self.assertEqual(snapshot.rpc_error, 'auth_unavailable')
            self.assertNotEqual(account_status(self.account(), snapshot).tone, 'active')

    def test_startup_probe_skips_rpc_and_is_not_reported_as_failure(self):
        with tempfile.TemporaryDirectory() as folder:
            path = self.fixture_config(folder)
            with patch('runtime_status._http_json', return_value={'ok': False, 'status': 'starting'}), patch('runtime_status._channel_snapshot') as rpc:
                snapshot = probe_runtime(Path(folder), path)
            rpc.assert_not_called()
            self.assertEqual(snapshot.gateway, 'starting')

    def test_bad_or_missing_configuration_does_not_probe_unrelated_service(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'missing.json'
            with patch('runtime_status._http_json') as http:
                self.assertEqual(probe_runtime(Path(folder), path).gateway, 'unconfigured')
                write_json(path, {'gateway': {'port': True}})
                self.assertEqual(probe_runtime(Path(folder), path).gateway, 'unknown')
            http.assert_not_called()


if __name__ == '__main__':
    unittest.main()
