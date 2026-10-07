import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from account_personas import PersonaStore, read_json
from companion_speech import VoiceStore, PROVIDER, PREFS_NAME
from fish_audio_client import PreviewError

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/synthesize_companion_voice.py'
spec = importlib.util.spec_from_file_location('companion_voice_adapter', SCRIPT)
adapter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(adapter)


class CompanionSpeechTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.store = PersonaStore(root / 'project', root / 'state')
        self.a = self.store.save('甲', '# 甲')
        self.b = self.store.save('乙', '# 乙')
        self.store.assign('a', self.a['id'], 'wx_a')
        self.store.assign('b', self.b['id'], 'wx_b')
        self.voices = VoiceStore(self.store)
        self.voices.save_service(self.a['id'], 'a' * 32, '甲音色', 'fixture owner permission', True)
        self.voices.save_service(self.b['id'], 'b' * 32, '乙音色', 'fixture owner permission', True)

    def test_settings_and_live_preferences_are_separate_without_config_rewrite(self):
        before = self.store.config.read_bytes()
        other = self.store.workspace('b', self.b['id'])
        other_prompt = (other / 'AGENTS.md').read_bytes()
        self.voices.save(self.a['id'], 'smart', 'custom-fish')
        self.store.sync_workspace_settings(self.a['id'])
        workspace = self.store.workspace('a', self.a['id'])
        self.assertEqual(read_json(workspace / PREFS_NAME, {})['tts']['auto'], 'tagged')
        self.assertIn('[[tts:text]]', (workspace / 'AGENTS.md').read_text(encoding='utf-8'))
        self.assertEqual(self.voices.load(self.b['id'])['mode'], 'text')
        self.assertEqual(read_json(other / PREFS_NAME, {})['tts']['auto'], 'off')
        self.assertEqual((other / 'AGENTS.md').read_bytes(), other_prompt)
        self.assertEqual(self.store.config.read_bytes(), before)
        self.voices.save(self.a['id'], 'text', 'custom-fish')
        self.store.sync_workspace_settings(self.a['id'])
        self.assertEqual(read_json(workspace / PREFS_NAME, {})['tts']['auto'], 'off')
        self.assertNotIn('[[tts:text]]', (workspace / 'AGENTS.md').read_text(encoding='utf-8'))

    def test_foreign_companion_request_never_reads_key_or_generates(self):
        request = {'localId': 'a', 'personaId': self.b['id'], 'text': '你好'}
        with patch.object(adapter, 'generate_sample') as generate:
            with self.assertRaisesRegex(PreviewError, '不属于'):
                adapter.synthesize(self.store, request)
            generate.assert_not_called()

    def test_off_mode_never_generates_even_with_explicit_tts_request(self):
        request = {'localId': 'a', 'personaId': self.a['id'], 'text': '你好'}
        with patch.object(adapter, 'generate_sample') as generate:
            with self.assertRaisesRegex(PreviewError, '未开启'):
                adapter.synthesize(self.store, request)
            generate.assert_not_called()

    def test_enabled_request_uses_saved_voice_and_cleans_only_its_own_temporary_audio(self):
        self.voices.save(self.a['id'], 'smart', 'custom-fish')
        workspace = self.store.workspace('a', self.a['id'])
        other_file = self.store.workspace('b', self.b['id']) / 'keep.wav'
        other_file.write_bytes(b'keep')
        def generate(key, text, destination, *, voice_config):
            self.assertEqual((key, text, voice_config['reference_id']), ('fixture-key', '你好', 'a' * 32))
            self.assertTrue(destination.is_relative_to(workspace))
            destination.write_bytes(b'fixture-audio')
            return {'audio_seconds': 1}
        with patch.object(adapter, 'load_key', return_value='fixture-key'), patch.object(adapter, 'generate_sample', side_effect=generate):
            result = adapter.synthesize(self.store, {'localId': 'a', 'personaId': self.a['id'], 'text': '你好'})
        self.assertTrue(result['ok'])
        self.assertEqual(list((workspace / '.voice-output').glob('*.wav')), [])
        self.assertEqual(other_file.read_bytes(), b'keep')

    def test_each_agent_has_private_prefs_and_only_the_approved_provider_binding(self):
        a = self.voices.tts_config('a', self.a['id'])
        b = self.voices.tts_config('b', self.b['id'])
        self.assertNotEqual(a['prefsPath'], b['prefsPath'])
        self.assertFalse(a['modelOverrides']['allowProvider'])
        self.assertFalse(a['modelOverrides']['allowModelId'])
        persona = a['personas'][a['persona']]
        self.assertEqual(persona['fallbackPolicy'], 'fail')
        self.assertEqual(list(persona['providers']), [PROVIDER])


if __name__ == '__main__':
    unittest.main()
