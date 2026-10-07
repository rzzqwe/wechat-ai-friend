import base64
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, MagicMock
from urllib.error import HTTPError
import wave

import gemini_audio as audio


class GeminiAudioTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.files = []
        self.error_patch = patch.object(audio, 'LAST_ERROR_FILE', Path(self.temp.name) / 'last-error.json')
        self.error_patch.start()
        self.addCleanup(self.error_patch.stop)
        for index in (1, 2, 3):
            path = Path(self.temp.name) / f'{index}.wav'
            with wave.open(str(path), 'wb') as file:
                file.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
                file.writeframes(b'\x01\x00' * 160)
            self.files.append(path)

    def test_request_contains_actual_audio_and_disables_server_conversation_storage(self):
        body, inputs = audio.build_request(self.files)
        self.assertEqual(body['model'], 'gemini-3.8-flash')
        self.assertFalse(body['store'])
        self.assertNotIn('tools', body)
        clips = [item for item in body['input'] if item['type'] == 'audio']
        self.assertEqual(len(clips), 3)
        for clip, path in zip(clips, self.files):
            self.assertEqual(base64.b64decode(clip['data']), path.read_bytes())
        self.assertEqual(len(inputs), 3)

    def test_no_upload_without_explicit_authorization_and_free_project_confirmation(self):
        with patch.object(audio, 'build_opener') as opener:
            with self.assertRaises(audio.AudioAnalysisError):
                audio.analyze('test-key', self.files, free_project_confirmed=True)
            with self.assertRaises(audio.AudioAnalysisError):
                audio.analyze('test-key', self.files, audio_submission_authorized=True)
            opener.assert_not_called()

    def test_quota_error_does_not_retry_or_leak_key(self):
        opener = MagicMock()
        opener.open.side_effect = HTTPError(audio.ENDPOINT, 429, 'private-test-key', {}, None)
        with patch.object(audio, 'build_opener', return_value=opener):
            with self.assertRaises(audio.AudioAnalysisError) as error:
                audio.analyze('private-test-key', self.files, free_project_confirmed=True, audio_submission_authorized=True)
        self.assertEqual(opener.open.call_count, 1)
        self.assertNotIn('private-test-key', str(error.exception))
        self.assertIn('未升级', str(error.exception))

    def test_native_steps_response_is_parsed_and_audio_usage_verified(self):
        analysis = {'clips': [{'clip': index, 'timbre': '清晰'} for index in (1, 2, 3)]}
        response = {'status': 'completed', 'steps': [{'type': 'model_output', 'content': [
            {'type': 'text', 'text': json.dumps(analysis)}]}],
            'usage': {'input_tokens_by_modality': [{'modality': 'audio', 'tokens': 5}]}}
        parsed, usage = audio.parse_analysis(response, 'private-test-key')
        self.assertEqual(parsed, analysis)
        self.assertTrue(usage['audio_tokens_reported'])
        response['usage']['input_tokens_by_modality'] = [{'modality': 'text', 'tokens': 5}]
        with self.assertRaises(audio.AudioAnalysisError):
            audio.parse_analysis(response, 'private-test-key')

    def test_redirect_cannot_forward_auth_to_another_host(self):
        self.assertIsNone(audio.NoRedirects().redirect_request(None, None, 302, '', {}, 'https://other.example'))

    def test_project_denial_is_not_misreported_as_region_or_invalid_key(self):
        body = {'error': {'status': 'PERMISSION_DENIED', 'message': 'Your project has been denied access. Please contact support. private-test-key'}}
        error = HTTPError(audio.ENDPOINT, 403, '', {}, io.BytesIO(json.dumps(body).encode('utf-8')))
        failure, diagnostic = audio.describe_http_error(error, 'private-test-key')
        self.assertEqual(diagnostic['reason'], 'project_denied')
        self.assertIn('项目', str(failure))
        self.assertNotIn('地区受限', str(failure))
        self.assertNotIn('private-test-key', json.dumps(diagnostic))

    @unittest.skipUnless(os.name == 'nt', 'Windows DPAPI')
    def test_key_is_saved_encrypted_and_restored_for_current_windows_user(self):
        with patch.object(audio, 'KEY_FILE', Path(self.temp.name) / 'gemini-key.bin'):
            audio.save_key('private-test-key')
            self.assertNotIn(b'private-test-key', audio.KEY_FILE.read_bytes())
            self.assertEqual(audio.load_key(), 'private-test-key')

    @unittest.skipUnless(os.name == 'nt', 'Windows DPAPI')
    def test_free_project_confirmation_is_bound_to_the_saved_encrypted_key(self):
        with patch.object(audio, 'KEY_FILE', Path(self.temp.name) / 'gemini-key.bin'), patch.object(audio, 'SETTINGS_FILE', Path(self.temp.name) / 'settings.json'):
            audio.save_key('private-test-key')
            audio.save_free_project_confirmation(True)
            self.assertTrue(audio.saved_free_project_confirmation())
            audio.KEY_FILE.write_bytes(b'changed encrypted credential')
            self.assertFalse(audio.saved_free_project_confirmation())


if __name__ == '__main__':
    unittest.main()
