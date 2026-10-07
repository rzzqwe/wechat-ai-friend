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

import qwen_audio as audio


class QwenAudioTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.files = []
        for index in (1, 2, 3):
            path = Path(self.temp.name) / f'{index}.wav'
            with wave.open(str(path), 'wb') as file:
                file.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
                file.writeframes(b'\x01\x00' * 160)
            self.files.append(path)
        for name, filename in (('LAST_ERROR_FILE', 'error.json'), ('REPORT_FILE', 'report.json')):
            patcher = patch.object(audio, name, Path(self.temp.name) / filename)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_native_requests_contain_one_real_audio_and_fixed_free_model(self):
        requests = audio.build_requests(self.files)
        self.assertEqual(len(requests), 3)
        for (body, meta), path in zip(requests, self.files):
            self.assertEqual(body['model'], 'qwen-audio-turbo')
            content = body['input']['messages'][1]['content']
            self.assertEqual(base64.b64decode(content[0]['audio'].split(',', 1)[1]), path.read_bytes())
            self.assertEqual(meta['file'], path.name)

    def test_no_upload_without_authorization(self):
        with patch.object(audio, 'build_opener') as opener:
            with self.assertRaises(audio.AudioAnalysisError):
                audio.analyze('sk-private-test-key', self.files)
            opener.assert_not_called()

    def test_quota_failure_stops_before_other_clips_without_retry_or_paid_fallback(self):
        opener = MagicMock()
        body = {'code': 'FreeQuotaExhausted', 'message': 'sk-private-test-key'}
        opener.open.side_effect = HTTPError(audio.ENDPOINT, 429, '', {}, io.BytesIO(json.dumps(body).encode('utf-8')))
        with patch.object(audio, 'build_opener', return_value=opener):
            with self.assertRaises(audio.AudioAnalysisError) as error:
                audio.analyze('sk-private-test-key', self.files, audio_submission_authorized=True)
        self.assertEqual(opener.open.call_count, 1)
        self.assertIn('付费', str(error.exception))
        self.assertNotIn('sk-private-test-key', audio.LAST_ERROR_FILE.read_text(encoding='utf-8'))
        report = json.loads(audio.REPORT_FILE.read_text(encoding='utf-8'))
        self.assertEqual(report['status'], 'failed')
        self.assertEqual(report['failed_clip'], 1)

    def test_actual_free_quota_error_is_distinct_from_token_rate_limit(self):
        error, diagnostic = audio.describe_error(403, {'code': 'Throttling.AllocationQuota',
            'message': 'Free allocated quota exceeded.'}, 'sk-private-test-key')
        self.assertEqual(diagnostic['category'], 'free_quota_unavailable')
        self.assertNotIn('暂时限流', str(error))
        error, diagnostic = audio.describe_error(429, {'code': 'Throttling.AllocationQuota',
            'message': 'Allocated quota exceeded, please increase your quota limit.'}, 'sk-private-test-key')
        self.assertEqual(diagnostic['category'], 'rate_limited')
        self.assertIn('暂时限流', str(error))

    def test_complete_native_audio_responses_are_saved_for_all_three_clips(self):
        item = {'timbre': '明亮、轻薄', 'delivery': '轻声接话', 'confidence': 'medium',
                'uncertainty': '片段短', 'search_terms': ['轻声接话']}
        body = {'output': {'choices': [{'finish_reason': 'stop', 'message': {'content': [{'text': json.dumps(item)}]}}]}, 'usage': {'audio_tokens': 10}}
        response = MagicMock()
        response.__enter__.return_value = response
        response.read.return_value = json.dumps(body).encode('utf-8')
        opener = MagicMock()
        opener.open.return_value = response
        with patch.object(audio, 'build_opener', return_value=opener):
            report = audio.analyze('sk-private-test-key', self.files, audio_submission_authorized=True)
        self.assertEqual(report['status'], 'complete')
        self.assertEqual(opener.open.call_count, 3)
        self.assertEqual([clip['clip'] for clip in report['analysis']['clips']], [1, 2, 3])
        self.assertNotIn('sk-private-test-key', audio.REPORT_FILE.read_text(encoding='utf-8'))

    def test_reply_without_audio_usage_is_not_a_listening_result(self):
        response = {'output': {'choices': [{'message': {'content': [{'text': '描述'}]}}]}, 'usage': {'audio_tokens': 0}}
        with self.assertRaises(audio.AudioAnalysisError):
            audio.parse_clip(response, 1, 'sk-private-test-key')

    @unittest.skipUnless(os.name == 'nt', 'Windows DPAPI')
    def test_key_is_encrypted_for_current_windows_user(self):
        with patch.object(audio, 'KEY_FILE', Path(self.temp.name) / 'qwen-key.bin'):
            audio.save_key('sk-private-test-key')
            self.assertNotIn(b'sk-private-test-key', audio.KEY_FILE.read_bytes())
            self.assertEqual(audio.load_key(), 'sk-private-test-key')


if __name__ == '__main__':
    unittest.main()
