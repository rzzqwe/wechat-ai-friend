from copy import deepcopy
from pathlib import Path
import shutil
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from account_personas import PersonaStore, read_json, write_json
from companion_speech import PROVIDER, PREFS_NAME, VoiceStore, ensure_runtime_config
from scripts.migrate_runtime_config import migrate
from scripts import route_wechat_account as route
from voice_controls import VoiceControls

ROOT = Path(__file__).resolve().parents[1]


class VoiceHarness(VoiceControls):
    def __init__(self, store, persona):
        self.services = SimpleNamespace(persona_store=lambda: store)
        self.current_persona_id = persona['id']
        self._ui_busy = False
        self._ui_voice_mode_var = Mock(get=Mock(return_value='文字和语音'))
        self._ui_voice_choice_var = Mock(get=Mock(return_value='测试音色'))
        self._voice_choices = {'测试音色': 'custom-fish'}
        self._ui_voice_status = Mock()
        self._busy = Mock(side_effect=lambda busy: setattr(self, '_ui_busy', busy))
        self.after = Mock(return_value='voice-timer')
        self.write_log = Mock()
        self._reload_voice_runtime = Mock()
        self.initialize_voice_controls()

    def finish_worker(self, action):
        with patch('voice_controls.threading.Thread') as thread, patch('fish_audio_client.load_key', return_value='fixture-key'):
            action()
            thread.call_args.kwargs['target']()
        self._poll_voice_results()


class VoiceInitializationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        folder = Path(self.temp.name)
        self.store = PersonaStore(folder / 'project', folder / 'state')
        shutil.copytree(ROOT / 'plugins/companion-voice', self.store.root / 'plugins/companion-voice')
        self.a = self.store.save('甲', '# 甲')
        self.b = self.store.save('乙', '# 乙')
        self.first = self.store.assign('a', self.a['id'], 'wx_a')
        self.second = self.store.assign('b', self.b['id'], 'wx_b')
        VoiceStore(self.store).save_service(self.a['id'], 'a' * 32, '甲音色', 'fixture owner permission', True)
        VoiceStore(self.store).save_service(self.b['id'], 'b' * 32, '乙音色', 'fixture owner permission', True)
        config = migrate(read_json(self.store.config, {}))
        config['models'] = {'providers': {'fixture': {'apiKey': 'fixture-secret'}}}
        config['plugins'] = {'allow': ['existing'], 'entries': {'existing': {'enabled': True}}}
        write_json(self.store.config, config)

    def test_first_setup_connects_private_preferences_and_preserves_routes_credentials_and_memory(self):
        before = read_json(self.store.config, {})
        workspace = self.store.workspace('a', self.a['id'])
        memory = workspace / 'MEMORY.md'
        memory.write_text('private memory', encoding='utf-8')
        assignments = self.store.assignments_path.read_bytes()
        VoiceStore(self.store).save(self.a['id'], 'smart', 'custom-fish')
        self.store.sync_workspace_settings(self.a['id'])
        self.assertTrue(ensure_runtime_config(self.store))
        after = read_json(self.store.config, {})
        self.assertEqual(after['bindings'], before['bindings'])
        self.assertEqual(after['models'], before['models'])
        self.assertEqual(after['agents']['entries']['main'], before['agents']['entries']['main'])
        self.assertEqual(after['plugins']['entries']['existing'], before['plugins']['entries']['existing'])
        self.assertIn(PROVIDER, after['plugins']['allow'])
        self.assertEqual(self.store.assignments_path.read_bytes(), assignments)
        self.assertEqual(memory.read_text(encoding='utf-8'), 'private memory')
        for local, persona, assignment in [('a', self.a, self.first), ('b', self.b, self.second)]:
            tts = after['agents']['entries'][assignment['agent_id']]['tts']
            self.assertEqual(tts['provider'], PROVIDER)
            self.assertEqual(tts['prefsPath'], str(self.store.workspace(local, persona['id']) / PREFS_NAME))
            self.assertEqual(tts['providers'][PROVIDER], {'localId': local, 'personaId': persona['id']})
        self.assertEqual(read_json(workspace / PREFS_NAME, {})['tts']['auto'], 'tagged')

    def test_repeated_setup_does_not_rewrite_config_or_duplicate_plugin_paths(self):
        self.assertTrue(ensure_runtime_config(self.store))
        before = self.store.config.read_bytes()
        with patch('companion_speech.write_json') as write:
            self.assertFalse(ensure_runtime_config(self.store))
        write.assert_not_called()
        self.assertEqual(self.store.config.read_bytes(), before)
        paths = read_json(self.store.config, {})['plugins']['load']['paths']
        self.assertEqual(paths.count(str(self.store.root / 'plugins/companion-voice')), 1)

    def test_windowless_python_uses_console_helper(self):
        pythonw = str(Path(sys.executable).with_name('pythonw.exe'))
        with patch('companion_speech.sys.executable', pythonw):
            self.assertTrue(ensure_runtime_config(self.store))
        plugin = read_json(self.store.config, {})['plugins']['entries'][PROVIDER]
        self.assertEqual(Path(plugin['config']['pythonExecutable']).name, 'python.exe')

    def test_missing_agent_or_plugin_leaves_configuration_unchanged(self):
        original = read_json(self.store.config, {})
        broken = deepcopy(original)
        del broken['agents']['entries'][self.first['agent_id']]
        write_json(self.store.config, broken)
        before = self.store.config.read_bytes()
        with self.assertRaisesRegex(ValueError, '代理缺失'):
            ensure_runtime_config(self.store)
        self.assertEqual(self.store.config.read_bytes(), before)
        write_json(self.store.config, original)
        (self.store.root / 'plugins/companion-voice/index.mjs').unlink()
        before = self.store.config.read_bytes()
        with self.assertRaisesRegex(ValueError, '组件缺失'):
            ensure_runtime_config(self.store)
        self.assertEqual(self.store.config.read_bytes(), before)

    def test_qr_binding_initializes_speech_before_publishing_account_mapping(self):
        with patch.object(route.app, 'persona_store', return_value=self.store), \
                patch.object(route.app, 'ACCOUNT_BINDINGS_FILE', self.store.root / 'data/account-bindings.json'), \
                patch.object(route, 'install_windows_gateway_acceleration', return_value=False):
            self.assertEqual(route.bind('a', 'wx_a'), self.first['agent_id'])
        config = read_json(self.store.config, {})
        self.assertTrue(config['plugins']['entries'][PROVIDER]['enabled'])
        self.assertEqual(read_json(self.store.root / 'data/account-bindings.json', {})['a'], 'wx_a')

    def test_workbench_startup_repairs_configuration_and_reloads_once(self):
        ui = VoiceHarness(self.store, self.a)
        ui.finish_worker(ui.initialize_voice_runtime)
        ui._reload_voice_runtime.assert_called_once()
        before = self.store.config.read_bytes()
        ui.finish_worker(ui.initialize_voice_runtime)
        ui._reload_voice_runtime.assert_called_once()
        self.assertEqual(self.store.config.read_bytes(), before)

    def test_voice_selection_initializes_once_then_mode_changes_use_live_preferences(self):
        ui = VoiceHarness(self.store, self.a)
        ui.finish_worker(ui.save_voice_selection)
        ui._reload_voice_runtime.assert_called_once()
        self.assertEqual(VoiceStore(self.store).load(self.a['id'])['mode'], 'smart')
        before = self.store.config.read_bytes()
        ui._ui_voice_mode_var.get.return_value = '文字'
        ui.finish_worker(ui.save_voice_selection)
        ui._ui_voice_mode_var.get.return_value = '文字和语音'
        ui.finish_worker(ui.save_voice_selection)
        ui._reload_voice_runtime.assert_called_once()
        self.assertEqual(self.store.config.read_bytes(), before)

    def test_reload_failure_is_visible_and_next_selection_retries(self):
        ui = VoiceHarness(self.store, self.a)
        ui._reload_voice_runtime.side_effect = [RuntimeError('网关重载失败'), None]
        ui.finish_worker(ui.save_voice_selection)
        ui.write_log.assert_called_with('网关重载失败')
        self.assertTrue(ui._voice_runtime_reload_pending)
        ui.finish_worker(ui.save_voice_selection)
        self.assertEqual(ui._reload_voice_runtime.call_count, 2)
        self.assertFalse(ui._voice_runtime_reload_pending)

    def test_legacy_runtime_does_not_falsely_enable_voice(self):
        config = read_json(self.store.config, {})
        agents = config['agents']
        agents['list'] = [dict(agent, id=key) for key, agent in agents.pop('entries').items()]
        write_json(self.store.config, config)
        ui = VoiceHarness(self.store, self.a)
        ui.finish_worker(ui.save_voice_selection)
        self.assertEqual(VoiceStore(self.store).load(self.a['id'])['mode'], 'text')
        self.assertIn('2026.9.6', ui.write_log.call_args.args[0])
        ui._reload_voice_runtime.assert_not_called()

    def test_unbound_companion_can_select_voice_before_runtime_installation(self):
        folder = Path(self.temp.name) / 'unbound'
        store = PersonaStore(folder / 'project', folder / 'state')
        persona = store.save('待绑定', '# 待绑定')
        VoiceStore(store).save_service(persona['id'], 'c' * 32, '待绑定音色', 'fixture owner permission', True)
        ui = VoiceHarness(store, persona)
        ui.finish_worker(ui.save_voice_selection)
        self.assertEqual(VoiceStore(store).load(persona['id'])['mode'], 'smart')
        self.assertFalse(store.config.exists())
        ui._reload_voice_runtime.assert_not_called()

    def test_missing_key_prompts_for_manual_configuration_without_installing(self):
        ui = VoiceHarness(self.store, self.a)
        before_other = VoiceStore(self.store).load(self.b['id'])
        with patch('voice_controls.threading.Thread') as thread, \
                patch('fish_audio_client.load_key', return_value=''), \
                patch('voice_controls.messagebox.showinfo') as prompt, \
                patch('voice_controls.subprocess.run') as execute:
            ui.save_voice_selection()
        thread.assert_not_called()
        execute.assert_not_called()
        prompt.assert_called_once()
        self.assertIn('自行配置', prompt.call_args.args[0])
        self.assertEqual(VoiceStore(self.store).load(self.a['id'])['mode'], 'text')
        self.assertEqual(VoiceStore(self.store).load(self.b['id']), before_other)
        ui._reload_voice_runtime.assert_not_called()


if __name__ == '__main__':
    unittest.main()
