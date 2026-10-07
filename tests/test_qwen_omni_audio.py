import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import qwen_omni_audio as audio


def sse(content, audio_tokens=28, finish='stop'):
    chunks = [
        {'choices': [{'index': 0, 'delta': {'content': content}, 'finish_reason': None}]},
        {'choices': [{'index': 0, 'delta': {}, 'finish_reason': finish}]},
        {'choices': [], 'usage': {'prompt_tokens': 300, 'completion_tokens': 200,
            'total_tokens': 500, 'prompt_tokens_details': {'audio_tokens': audio_tokens}}},
    ]
    return io.BytesIO((''.join('data: ' + json.dumps(c, ensure_ascii=False) + '\n\n' for c in chunks)
                       + 'data: [DONE]\n\n').encode('utf-8'))


class QwenOmniTests(unittest.TestCase):
    def test_no_request_without_verified_free_tier_switch(self):
        with tempfile.TemporaryDirectory() as temp:
            with patch.object(audio, 'GUARD_FILE', Path(temp) / 'missing.json'), patch.object(audio, 'build_opener') as opener:
                with self.assertRaises(audio.AudioAnalysisError):
                    audio.analyze('sk-private-test-key', [], audio_submission_authorized=True)
                opener.assert_not_called()

    def test_complete_stream_preserves_actual_audio_usage_and_redacts_text(self):
        content = json.dumps({'timbre': '柔亮，带气声 sk-private-test-key', 'search_terms': ['柔亮']}, ensure_ascii=False)
        response, usage = audio.read_stream(sse(content), 'sk-private-test-key')
        clip = audio.shared.parse_clip(response, 1, 'sk-private-test-key')
        self.assertEqual(clip['audio_tokens'], 28)
        self.assertEqual(usage['total_tokens'], 500)
        self.assertNotIn('sk-private-test-key', json.dumps(clip))

    def test_text_only_response_is_not_a_listening_result(self):
        response, _ = audio.read_stream(sse('{"timbre":"描述"}', audio_tokens=0), 'sk-private-test-key')
        with self.assertRaises(audio.AudioAnalysisError):
            audio.shared.parse_clip(response, 1, 'sk-private-test-key')

    def test_interrupted_stream_is_not_marked_complete(self):
        with self.assertRaises(audio.AudioAnalysisError):
            audio.read_stream(sse('描述', finish='length'), 'sk-private-test-key')

    def test_guard_cannot_be_reused_for_another_key_or_model(self):
        with tempfile.TemporaryDirectory() as temp:
            guard = Path(temp) / 'guard.json'
            key = Path(temp) / 'key.bin'
            key.write_bytes(b'encrypted-key-one')
            with patch.object(audio, 'GUARD_FILE', guard), patch.object(audio, 'KEY_FILE', key):
                audio.confirm_free_tier(expires='2099-01-01', evidence='test screenshot', remaining_tokens=1000000)
                self.assertTrue(audio.check_free_tier()['free_tier_only'])
                key.write_bytes(b'encrypted-key-two')
                with self.assertRaises(audio.AudioAnalysisError):
                    audio.check_free_tier()


if __name__ == '__main__':
    unittest.main()
