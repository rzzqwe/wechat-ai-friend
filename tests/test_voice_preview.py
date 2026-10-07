from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from account_personas import PersonaStore
from companion_speech import VoiceStore
import fish_audio_client as fish
from voice_controls import VoiceControls, PREVIEW_TEXT


class PreviewHarness(VoiceControls):
    def __init__(self, store, persona_id):
        self.services = SimpleNamespace(persona_store=lambda: store)
        self.current_persona_id = persona_id
        self._ui_busy = False
        self._ui_voice_status = Mock()
        self.after = Mock(return_value='timer')
        self._busy = Mock(side_effect=lambda busy: setattr(self, '_ui_busy', busy))
        self.refresh_voice_selection = Mock()
        self.write_log = Mock()
        self._show_voice_preview = Mock()
        self.initialize_voice_controls()


class VoicePreviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.store = PersonaStore(root / 'project', root / 'state')
        self.persona = self.store.save('试听', '# 试听')
        VoiceStore(self.store).save_service(self.persona['id'], 'a' * 32)
        self.ui = PreviewHarness(self.store, self.persona['id'])

    def test_preview_generates_with_configured_voice_and_only_updates_ui_on_poll(self):
        def generate(key, text, output, *, voice_config):
            self.assertEqual(key, 'fixture-key')
            self.assertEqual(text, PREVIEW_TEXT)
            self.assertLessEqual(len(text), 15)
            self.assertEqual(voice_config['reference_id'], 'a' * 32)
            output.write_bytes(b'fixture-audio')
            return {'audio_seconds': 3.2}
        with patch('voice_controls.threading.Thread') as thread, \
                patch.object(fish, 'load_key', return_value='fixture-key'), \
                patch.object(fish, 'generate_sample', side_effect=generate) as request:
            self.ui.preview_selected_voice()
            self.ui.preview_selected_voice()
            self.assertEqual(thread.call_count, 1, 'Prevent duplicate API requests while generating')
            thread.call_args.kwargs['target']()
            self.ui._show_voice_preview.assert_not_called()
            self.ui._poll_voice_results()
        request.assert_called_once()
        preview = self.ui._show_voice_preview.call_args.args[0]
        self.assertEqual(preview['audio_seconds'], 3.2)
        self.assertTrue(Path(preview['path']).is_file())
        self.assertFalse(self.ui._voice_preview_busy)

    def test_preview_can_use_default_configuration_without_a_companion(self):
        VoiceStore(self.store).save_service(None, 'b' * 32)
        self.ui.current_persona_id = None
        def generate(key, text, output, *, voice_config):
            self.assertEqual(voice_config['reference_id'], 'b' * 32)
            output.write_bytes(b'fixture-audio')
            return {'audio_seconds': 2.5}
        with patch('voice_controls.threading.Thread') as thread, \
                patch.object(fish, 'load_key', return_value='fixture-key'), \
                patch.object(fish, 'generate_sample', side_effect=generate):
            self.ui.preview_selected_voice()
            thread.call_args.kwargs['target']()
        self.ui._poll_voice_results()
        self.ui._show_voice_preview.assert_called_once()

    def test_missing_key_does_not_call_api_or_play_builtin_sample(self):
        with patch('voice_controls.threading.Thread') as thread, \
                patch.object(fish, 'load_key', return_value=''), \
                patch('voice_controls.messagebox.showinfo') as prompt:
            self.ui.preview_selected_voice()
        thread.assert_not_called()
        prompt.assert_called_once()
        self.ui._show_voice_preview.assert_not_called()

    def test_failure_is_reported_and_not_replaced_with_another_voice(self):
        with patch('voice_controls.threading.Thread') as thread, \
                patch.object(fish, 'load_key', return_value='fixture-key'), \
                patch.object(fish, 'generate_sample', side_effect=fish.PreviewError('当前音色无使用权限')) as request:
            self.ui.preview_selected_voice()
            thread.call_args.kwargs['target']()
        self.ui._poll_voice_results()
        request.assert_called_once()
        self.ui.write_log.assert_called_with('当前音色无使用权限')
        self.ui._show_voice_preview.assert_not_called()
        self.assertFalse(self.ui._voice_preview_busy)


if __name__ == '__main__':
    unittest.main()
