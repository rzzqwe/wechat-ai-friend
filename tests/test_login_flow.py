from pathlib import Path
from types import SimpleNamespace
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import wechat_bot_app as app


class LoginResultTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'result.json'
        self.state = SimpleNamespace(after=lambda delay, callback: callback(), update_account=Mock(), ui_log=Mock())

    def result(self, status, message='', local_id='user'):
        self.path.write_text(json.dumps(dict(local_id=local_id, status=status, message=message)), encoding='utf-8-sig')

    def test_current_success_requires_saved_mapping(self):
        self.result('succeeded')
        with patch.object(app, 'load_account_bindings', return_value={'user': 'wechat-a'}):
            app.App._watch_login_result(self.state, 'user', Mock(poll=Mock(return_value=None)), self.path)
        self.assertEqual(self.state.update_account.call_args.kwargs['provider_id'], 'wechat-a')
        self.assertEqual(self.state.update_account.call_args.kwargs['status'], '已绑定')

    def test_failed_attempt_is_not_completed_by_old_mapping(self):
        self.result('failed', '模型配置失败')
        with patch.object(app, 'load_account_bindings', return_value={'user': 'old-wechat'}) as mapping:
            app.App._watch_login_result(self.state, 'user', Mock(poll=Mock(return_value=None)), self.path)
        mapping.assert_not_called()
        self.assertEqual(self.state.update_account.call_args.kwargs, dict(status='绑定失败', last_action='模型配置失败'))

    def test_saved_binding_survives_backend_failure_without_requiring_another_qr(self):
        self.path.write_text(json.dumps(dict(local_id='user',status='bound',binding_saved=True,provider_id='wechat-a',message='后台启动中')))
        with patch.object(app,'load_account_bindings',return_value={'user':'wechat-a'}),patch.object(app,'provider_account_exists',return_value=True):
            app.App._watch_login_result(self.state,'user',Mock(poll=Mock(return_value=1)),self.path)
        self.assertEqual(self.state.update_account.call_args.kwargs,dict(provider_id='wechat-a',status='已绑定',last_action='后台启动中'))

    def test_bound_result_cannot_use_an_old_other_account_mapping(self):
        self.path.write_text(json.dumps(dict(local_id='user',status='bound',binding_saved=True,provider_id='wechat-new',message='后台启动中')))
        with patch.object(app,'load_account_bindings',return_value={'user':'wechat-old'}),patch.object(app,'provider_account_exists',return_value=True):
            app.App._watch_login_result(self.state,'user',Mock(poll=Mock(return_value=1)),self.path)
        self.assertEqual(self.state.update_account.call_args.kwargs['status'],'绑定失败')

    def test_older_gateway_timeout_record_is_recovered_only_with_saved_matching_credentials(self):
        from companion_controls import CompanionControls
        account=dict(id='user',status='绑定失败',last_action='绑定失败（正在启动微信网关）')
        store=Mock(assignments=Mock(return_value={'user':{'provider_id':'wechat-a'}}))
        services=SimpleNamespace(persona_store=lambda:store,load_account_bindings=lambda:{'user':'wechat-a'},
                                 provider_account_exists=lambda value:True,save_accounts=Mock())
        state=SimpleNamespace(services=services,accounts=[account],write_log=Mock())
        CompanionControls.recover_saved_bindings(state)
        self.assertEqual(state.accounts[0]['status'],'已绑定')
        self.assertEqual(state.accounts[0]['provider_id'],'wechat-a')
        state.accounts=[dict(account,last_action='绑定失败（正在配置模型服务）')]
        services.save_accounts.reset_mock()
        CompanionControls.recover_saved_bindings(state)
        services.save_accounts.assert_not_called()

    def test_closed_console_reports_failure_immediately(self):
        with patch.object(app.time, 'sleep') as sleep:
            app.App._watch_login_result(self.state, 'user', Mock(poll=Mock(return_value=1)), self.path)
        sleep.assert_not_called()
        self.assertEqual(self.state.update_account.call_args.kwargs['status'], '绑定失败')

    def test_success_without_mapping_reports_failure(self):
        self.result('succeeded')
        with patch.object(app, 'load_account_bindings', return_value={}):
            app.App._watch_login_result(self.state, 'user', Mock(poll=Mock(return_value=0)), self.path)
        self.assertEqual(self.state.update_account.call_args.kwargs['status'], '绑定失败')

    def test_wrong_accounts_result_is_ignored(self):
        self.result('succeeded', local_id='other')
        with patch.object(app, 'load_account_bindings') as mapping:
            app.App._watch_login_result(self.state, 'user', Mock(poll=Mock(return_value=1)), self.path)
        mapping.assert_not_called()
        self.assertEqual(self.state.update_account.call_args.kwargs['status'], '绑定失败')

    def test_progress_is_shown_until_current_attempt_finishes(self):
        self.result('running', '正在准备微信渠道插件')
        def finish(_seconds):
            self.result('succeeded')
        with patch.object(app.time, 'sleep', side_effect=finish), patch.object(app, 'load_account_bindings', return_value={'user': 'wechat-a'}):
            app.App._watch_login_result(self.state, 'user', Mock(poll=Mock(return_value=None)), self.path)
        self.state.ui_log.assert_any_call('正在准备微信渠道插件')
        self.assertEqual(self.state.update_account.call_args.kwargs['status'], '已绑定')

    def test_existing_scan_prevents_another_launch(self):
        state = SimpleNamespace(_login_attempt=('user', Mock(poll=Mock(return_value=None)), self.path))
        with patch.object(app.messagebox, 'showinfo') as info, patch.object(app.subprocess, 'Popen') as launch:
            app.App.launch_login(state, {'id': 'user'})
        info.assert_called_once()
        launch.assert_not_called()


@unittest.skipUnless(os.name == 'nt' and shutil.which('powershell'), 'Windows PowerShell integration')
class LoginScriptTests(unittest.TestCase):
    def prepare_existing_setup(self, folder, case):
        if not case.startswith('setup_'):
            return
        state = folder / 'state'
        plugin = state / 'npm/node_modules/@tencent-weixin/openclaw-weixin'
        if case.startswith('setup_plugin_isolated'):
            plugin = state / 'npm/projects/fixture generation/node_modules/@tencent-weixin/openclaw-weixin'
        (plugin / 'dist').mkdir(parents=True)
        version = '2.4.8' if case == 'setup_plugin_outdated' else '2.4.9'
        if case == 'setup_plugin_newer':
            version = '2.5.1'
        name = 'another-package' if case == 'setup_plugin_wrong_package' else '@tencent-weixin/openclaw-weixin'
        if case != 'setup_plugin_missing_package':
            (plugin / 'package.json').write_text(json.dumps({'name': name, 'version': version}))
        manifest_id = 'another-plugin' if case == 'setup_plugin_wrong_manifest' else 'openclaw-weixin'
        (plugin / 'openclaw.plugin.json').write_text(json.dumps({'id': manifest_id}))
        if case == 'setup_plugin_alt_entry':
            (plugin / 'lib').mkdir()
            (plugin / 'lib/channel.js').write_text('// declared entry')
        elif case not in ('setup_plugin_broken', 'setup_plugin_postcheck_failed'):
            (plugin / 'dist/index.js').write_text('// fixture runtime')
        if case == 'setup_plugin_isolated_stale_legacy':
            legacy = state / 'npm/node_modules/@tencent-weixin/openclaw-weixin'
            (legacy / 'dist').mkdir(parents=True)
            (legacy / 'package.json').write_text(json.dumps({'name': '@tencent-weixin/openclaw-weixin', 'version': '2.4.8'}))
            (legacy / 'openclaw.plugin.json').write_text(json.dumps({'id': 'openclaw-weixin'}))
            (legacy / 'dist/index.js').write_text('// obsolete generation')
        dependencies = {} if case == 'setup_plugin_unregistered' else {'@tencent-weixin/openclaw-weixin': '^2.4.9'}
        (state / 'npm/package.json').write_text(json.dumps({'dependencies': dependencies}))
        provider = {'baseUrl': 'https://example.invalid', 'api': 'openai-completions',
                    'apiKey': 'fixture-secret-never-log', 'models': [{'id': 'fixture-model'}]}
        config = {'plugins': {'entries': {'openclaw-weixin': {'enabled': True}}},
                  'session': {'dmScope': 'per-account-channel-peer'},
                  'agents': {'defaults': {'model': {'primary': 'fixture/fixture-model'}}},
                  'models': {'providers': {'fixture': provider}}}
        if case == 'setup_key_changed':
            provider['apiKey'] = 'Fixture-secret-never-log'
        elif case.startswith('setup_model_fast'):
            provider['apiKey'] = 'previous-fixture-key'
        elif case == 'setup_model_changed':
            provider['models'] = [{'id': 'another-model'}]
        elif case == 'setup_url_changed':
            provider['baseUrl'] = 'https://previous.invalid'
        elif case == 'setup_url_slash':
            provider['baseUrl'] += '/'
        elif case == 'setup_primary_changed':
            config['agents']['defaults']['model']['primary'] = 'another/another-model'
        elif case == 'setup_plugin_disabled':
            config['plugins']['entries']['openclaw-weixin']['enabled'] = False
        elif case == 'setup_scope_changed':
            config['session']['dmScope'] = 'main'
        elif case == 'setup_model_missing':
            config.pop('models')
        elif case in ('setup_legacy_agents', 'setup_migration_fail'):
            config['agents']['list'] = [{'id': 'main', 'default': True}]
        text = '{invalid' if case == 'setup_config_invalid' else json.dumps(config)
        (state / 'openclaw.json').write_text(text, encoding='utf-8-sig')

    def test_script_handles_success_and_each_failure_before_reporting_completion(self):
        cases = [('success', 0, 'succeeded'), ('upgrade_ok', 0, 'succeeded'),
                 ('version_native', 0, 'succeeded'), ('version_preamble', 0, 'succeeded'),
                 ('version_invalid', 1, 'failed'), ('version_exit_fail', 1, 'failed'),
                 ('upgrade_fail', 1, 'failed'), ('upgrade_stale', 1, 'failed'),
                 ('plugin_fail', 1, 'failed'), ('model_fail', 1, 'failed'),
                 ('gateway_fail', 1, 'failed'), ('setup_config_invalid', 1, 'failed'),
                 ('setup_migration_fail', 1, 'failed'), ('node_upgrade_ok', 0, 'succeeded'),
                 ('setup_windows_compat', 0, 'succeeded'), ('setup_windows_compat_fail', 1, 'failed'),
                 ('setup_preflight_verified', 0, 'succeeded'),
                 ('setup_model_fast', 0, 'succeeded'), ('setup_model_fast_fail', 1, 'failed'),
                 ('node_24_stale', 1, 'failed'), ('node_25_stale', 1, 'failed'),
                 ('node_26_stale', 1, 'failed'), ('node_invalid', 1, 'failed'),
                 ('node_exit_fail', 1, 'failed')]
        cases += [(case, 1, 'failed') for case in ('model_url_missing', 'model_name_missing', 'model_key_missing')]
        cases += [(case, 1, 'failed') for case in (
            'setup_plugin_query_failed', 'setup_plugin_invalid_info', 'setup_plugin_wrong_id',
            'setup_plugin_query_nonzero', 'setup_plugin_no_root', 'setup_plugin_runtime_error',
            'setup_plugin_postcheck_failed')]
        cases += [(case, 0, 'succeeded') for case in (
            'setup_ready', 'setup_key_changed', 'setup_model_changed', 'setup_url_changed',
            'setup_url_slash', 'setup_primary_changed', 'setup_plugin_disabled', 'setup_scope_changed',
            'setup_plugin_outdated', 'setup_plugin_broken', 'setup_plugin_unregistered', 'setup_model_missing',
            'setup_plugin_isolated', 'setup_plugin_isolated_stale_legacy', 'setup_plugin_flat_info',
            'setup_plugin_install_path', 'setup_plugin_alt_entry', 'setup_plugin_newer',
            'setup_plugin_missing_dependency', 'setup_plugin_missing_package',
            'setup_plugin_wrong_package', 'setup_plugin_wrong_manifest', 'setup_legacy_agents')]
        for case, exit_code, status in cases:
            with self.subTest(case=case), tempfile.TemporaryDirectory() as temporary:
                folder = Path(temporary)
                self.prepare_existing_setup(folder, case)
                script = folder / 'login.ps1'
                shutil.copyfile(ROOT / 'scripts' / '启动微信ClawBot.ps1', script)
                shutil.copyfile(ROOT / 'scripts/weixin_environment.ps1', folder / 'weixin_environment.ps1')
                (folder / 'environment_setup.ps1').write_text('''function Invoke-ProjectEnvironment {
    param([switch]$Python, [switch]$OpenClaw, [switch]$Weixin)
    Add-Content -LiteralPath $env:LOGIN_CALLS -Value 'environment-preflight' -Encoding UTF8
    if ($env:LOGIN_CASE -eq 'setup_preflight_verified') {
        return @{Python=$env:WECHAT_AI_PYTHON; Runtime=@{Version='2026.9.6'}; WeixinVerified=$true}
    }
    return @{Python=$env:WECHAT_AI_PYTHON}
}
''', encoding='utf-8-sig')
                fixture_bin = folder / 'bin'
                if case.startswith('setup_windows_compat'):
                    shutil.copyfile(ROOT / 'scripts/apply_openclaw_windows_compat.py', folder / 'apply_openclaw_windows_compat.py')
                    package = fixture_bin / 'node_modules/openclaw'
                    (package / 'dist').mkdir(parents=True)
                    (fixture_bin / 'openclaw.cmd').write_text('@echo off\nexit /b 0\n')
                    (package / 'package.json').write_text(json.dumps({'name': 'openclaw', 'version': '2026.9.6'}))
                    audit = '\tconst nativeDefaults = {\n};\n\t\tif (seen.has(key) || node.children.length || true) {}\n'
                    if case.endswith('_fail'):
                        audit = '// unsupported audit implementation'
                    (package / 'dist/service-audit-schtasks-fixture.mjs').write_bytes(audit.encode('utf-8'))
                (folder / 'route_wechat_account.py').write_text('print("fixture route ready")', encoding='utf-8')
                if case.startswith('setup_model_fast'):
                    code=1 if case.endswith('_fail') else 0
                    (folder / 'configure_text_model.py').write_text(
                        'import sys\nprint("fixture model save")\nsys.exit('+str(code)+')\n',encoding='utf-8')
                result_path = folder / 'result.json'
                calls_path = folder / 'calls.txt'
                calls_path.write_text('', encoding='utf-8')
                version_done = folder / 'version-done.txt'
                env = dict(os.environ, LOGIN_CASE=case, LOGIN_CALLS=str(calls_path),
                           LOGIN_VERSION_SCRIPT=str(ROOT / 'tests/fixtures/version_command.py'),
                           LOGIN_VERSION_DONE=str(version_done),
                           OPENCLAW_STATE_DIR=str(folder / 'state'), WECHAT_AI_PYTHON=sys.executable,
                           OPENCLAW_CONFIG_PATH=str(folder / 'state/openclaw.json'),
                           WECHAT_AI_API_KEY='fixture-secret-never-log', WECHAT_AI_BASE_URL='https://example.invalid',
                           WECHAT_AI_MODEL='fixture-model', PYTHONUTF8='1', PYTHONIOENCODING='utf-8')
                missing_fields = {'model_url_missing': 'WECHAT_AI_BASE_URL',
                                  'model_name_missing': 'WECHAT_AI_MODEL', 'model_key_missing': 'WECHAT_AI_API_KEY'}
                if case in missing_fields:
                    env[missing_fields[case]] = ''
                if case.startswith('setup_windows_compat'):
                    env['PATH'] = str(fixture_bin) + os.pathsep + env.get('PATH', '')
                process = subprocess.run([shutil.which('powershell'), '-NoProfile', '-ExecutionPolicy', 'Bypass',
                                          '-File', str(ROOT / 'tests/fixtures/login_harness.ps1'),
                                          '-ScriptPath', str(script), '-ResultPath', str(result_path)],
                                         env=env, capture_output=True, text=True, encoding='utf-8', errors='replace',
                                         timeout=25, creationflags=subprocess.CREATE_NO_WINDOW)
                self.assertEqual(process.returncode, exit_code, process.stdout + process.stderr)
                self.assertTrue(result_path.exists(), process.stdout + process.stderr)
                raw = result_path.read_text(encoding='utf-8-sig')
                result = json.loads(raw)
                self.assertEqual(result['status'], 'succeeded' if case=='gateway_fail' else status, result)
                self.assertEqual(result['local_id'], 'user')
                if case == 'version_native':
                    self.assertEqual(version_done.read_text(), 'completed')
                expected_stage = {'plugin_fail': '正在准备微信渠道插件', 'model_fail': '首次初始化模型配置与后台服务',
                                  'gateway_fail': '正在启动微信网关',
                                  'upgrade_fail': '正在升级 OpenClaw 到 2026.9.6，请等待安装完成',
                                  'upgrade_stale': '正在升级 OpenClaw 到 2026.9.6，请等待安装完成',
                                  'setup_migration_fail': '正在迁移 OpenClaw 配置'}
                expected_stage['setup_windows_compat_fail'] = '正在配置 Windows 运行兼容性'
                expected_stage['setup_model_fast_fail'] = '正在配置模型服务'
                if case in expected_stage:
                    self.assertEqual(result['stage'], expected_stage[case], result)
                if env['WECHAT_AI_API_KEY']:
                    self.assertNotIn(env['WECHAT_AI_API_KEY'], raw)
                    self.assertNotIn(env['WECHAT_AI_API_KEY'], process.stdout + process.stderr)
                calls = calls_path.read_text(encoding='utf-8-sig')
                if case in missing_fields:
                    self.assertIn('自行配置文本模型', result['message'])
                    self.assertEqual(calls, '')
                else:
                    self.assertEqual(calls.count('environment-preflight'), 1)
                if case in ('setup_legacy_agents', 'setup_migration_fail'):
                    self.assertIn('doctor --fix --non-interactive', calls)
                if case == 'setup_windows_compat':
                    module = fixture_bin / 'node_modules/openclaw/dist/service-audit-schtasks-fixture.mjs'
                    self.assertIn('Windows omits task fields', module.read_text())
                    self.assertTrue(module.with_suffix('.mjs.before-wechat-windows-defaults').is_file())
                if case.startswith('setup_'):
                    reinstall = case in ('setup_plugin_outdated', 'setup_plugin_broken', 'setup_plugin_unregistered',
                                         'setup_plugin_missing_dependency', 'setup_plugin_missing_package',
                                         'setup_plugin_wrong_package', 'setup_plugin_wrong_manifest',
                                         'setup_plugin_postcheck_failed')
                    reconfigure = case in ('setup_key_changed', 'setup_model_changed', 'setup_url_changed',
                                           'setup_primary_changed', 'setup_model_missing')
                    self.assertEqual('plugins install' in calls, reinstall, calls)
                    self.assertLessEqual(calls.count('plugins install'), 1, calls)
                    if case == 'setup_preflight_verified':
                        self.assertNotIn('plugins info', calls)
                        self.assertNotIn('--version', calls)
                        self.assertNotIn('onboard', calls)
                    if case.startswith('setup_plugin_') and not reinstall and status == 'succeeded':
                        self.assertIn('微信渠道插件已安装，跳过重复安装。', process.stdout)
                    if case == 'setup_plugin_postcheck_failed':
                        self.assertEqual(calls.count('plugins info'), 2, calls)
                    self.assertEqual('onboard' in calls, reconfigure, calls.replace(env['WECHAT_AI_API_KEY'], '[redacted]'))
                    self.assertEqual('config set plugins.entries' in calls, case == 'setup_plugin_disabled', calls)
                    self.assertEqual('config set session.dmScope' in calls, case == 'setup_scope_changed', calls)
                if case in ('upgrade_fail', 'upgrade_stale', 'version_invalid', 'version_exit_fail') or (case.startswith('node_') and status == 'failed'):
                    self.assertNotIn('plugins install', calls)
                if status == 'succeeded':
                    self.assertIn('channels login --channel openclaw-weixin --account user', calls)
                    self.assertIn('gateway start', calls)
                    self.assertIn('gateway stop --force', calls)
                    self.assertNotIn('--agent-name', calls)
                elif case != 'gateway_fail':
                    self.assertNotIn('channels login', calls)
