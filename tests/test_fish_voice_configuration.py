import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch
import wave

from account_personas import PersonaStore, read_json
from companion_speech import VoiceStore
import fish_audio_client as fish


class FishVoiceConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.store = PersonaStore(root / 'project', root / 'state')
        self.a = self.store.save('甲', '# 甲')
        self.b = self.store.save('乙', '# 乙')
        self.voices = VoiceStore(self.store)

    def test_no_remote_voice_is_preconfigured(self):
        self.assertIsNone(self.voices.load_service(self.a['id']))
        self.assertIsNone(self.voices.load_service(self.b['id']))
        self.assertEqual(self.voices.load(self.a['id'])['mode'], 'text')
        with self.assertRaisesRegex(ValueError, '音色 ID'):
            self.voices.save(self.a['id'], 'smart', 'custom-fish')

    def test_reference_id_is_required_and_validated_before_saving(self):
        for reference in ('', 'invalid', 'a' * 31, 'z' * 32):
            with self.subTest(reference=reference), self.assertRaises(ValueError):
                self.voices.save_service(self.a['id'], reference)
        self.assertIsNone(self.voices.load_service(self.a['id']))

    def test_two_field_configuration_does_not_require_hidden_fields(self):
        service = self.voices.save_service(self.a['id'], 'a' * 32)
        self.assertEqual(service['reference_id'], 'a' * 32)
        self.assertEqual(service['name'], '音色 aaaaaa')
        self.assertNotIn('rights_source', service)
        self.assertNotIn('rights_confirmed', service)
        self.assertEqual(self.voices.load_service(self.a['id']), service)

    def test_default_voice_can_be_saved_before_creating_a_companion(self):
        self.voices.save_service(None, 'c' * 32)
        created = self.store.save('新陪伴', '# 新陪伴')
        self.assertEqual(self.voices.load_service(created['id'])['reference_id'], 'c' * 32)
        self.assertEqual(self.voices.load(created['id'])['mode'], 'text')
        self.assertIsNone(self.voices.load_service(self.a['id']))

    def test_default_changes_do_not_change_existing_companion_voices(self):
        self.voices.save_service(None, 'c' * 32)
        first = self.store.save('第一个', '# 第一个')
        self.voices.save_service(None, 'd' * 32)
        second = self.store.save('第二个', '# 第二个')
        self.assertEqual(self.voices.load_service(first['id'])['reference_id'], 'c' * 32)
        self.assertEqual(self.voices.load_service(second['id'])['reference_id'], 'd' * 32)

    def test_voices_are_private_to_each_companion_and_do_not_contain_key(self):
        self.voices.save_service(self.a['id'], 'a' * 32, '甲音色', 'owned voice', True)
        self.voices.save_service(self.b['id'], 'b' * 32, '乙音色', 'owner permission', True)
        self.assertEqual(self.voices.load_service(self.a['id'])['reference_id'], 'a' * 32)
        self.assertEqual(self.voices.load_service(self.b['id'])['reference_id'], 'b' * 32)
        self.assertNotIn('api_key', read_json(self.voices.service_path(self.a['id']), {}))

    def test_actual_api_payload_uses_the_current_companion_voice(self):
        voice = self.voices.save_service(self.a['id'], 'a' * 32, '甲音色', 'owned voice', True)
        audio = io.BytesIO()
        with wave.open(audio, 'wb') as sound:
            sound.setparams((1, 2, 44100, 0, 'NONE', 'not compressed'))
            sound.writeframes(b'\x01\x00' * 441)
        data = audio.getvalue()
        response = MagicMock()
        response.__enter__.return_value = response
        response.read.side_effect = [data[:1], data[1:]]
        opener = MagicMock()
        opener.open.return_value = response
        with patch.object(fish, 'build_opener', return_value=opener):
            output = self.store.root / 'sample.wav'
            fish.generate_sample('fixture-key', '这是新回复', output, voice_config=voice)
        request = opener.open.call_args.args[0]
        self.assertEqual(request.full_url, 'https://api.fish.audio/v1/tts')
        self.assertEqual(request.get_header('Model'), 's2.1-pro-free')
        self.assertEqual(json.loads(request.data)['reference_id'], 'a' * 32)
        self.assertEqual(json.loads(request.data)['text'], '这是新回复')


if __name__ == '__main__':
    unittest.main()
