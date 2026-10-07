import importlib.util
import io
import json
import os
import struct
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, MagicMock
from urllib.error import HTTPError
import wave

SCRIPT = Path(__file__).resolve().parents[1] / "fish_audio_client.py"
spec = importlib.util.spec_from_file_location("fish_audio_preview", SCRIPT)
preview = importlib.util.module_from_spec(spec)
spec.loader.exec_module(preview)


def wav_audio():
    target = io.BytesIO()
    with wave.open(target, "wb") as sound:
        sound.setparams((1, 2, 44100, 0, "NONE", "not compressed"))
        sound.writeframes(b"\x00\x00" * 441)
    return target.getvalue()


class PreviewTests(unittest.TestCase):
    def setUp(self):
        # Synthetic test metadata only; no real person's sound ID is provided.
        self.voice = {'provider': 'fish-audio', 'reference_id': '0' * 32,
                 'generation': {'format': 'wav', 'sample_rate': 44100}}

    def test_missing_voice_configuration_does_not_request_or_save_audio(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(preview, 'build_opener') as opening:
            path = Path(directory) / 'sample.wav'
            with self.assertRaisesRegex(preview.PreviewError, '请先配置'):
                preview.generate_sample('private-test-key', '你好', path)
            opening.assert_not_called()
            self.assertFalse(path.exists())
    def test_streaming_header_is_finalized_without_changing_voice_samples(self):
        original = wav_audio()
        streaming = bytearray(original)
        struct.pack_into("<I", streaming, 4, 0xFFFFFF24)
        struct.pack_into("<I", streaming, 40, 0xFFFFFF00)
        normalized, duration = preview.finalize_wav(bytes(streaming))
        self.assertEqual(normalized[44:], original[44:])
        self.assertEqual(struct.unpack_from("<I", normalized, 4)[0], len(normalized) - 8)
        self.assertEqual(struct.unpack_from("<I", normalized, 40)[0], len(normalized) - 44)
        with wave.open(io.BytesIO(normalized), "rb") as sound:
            self.assertEqual(sound.getnframes(), 441)
        self.assertEqual(duration, 0.01)

    def test_empty_wav_is_not_reported_as_success(self):
        empty = wav_audio()[:44]
        struct.pack_into("<I", empty := bytearray(empty), 40, 0)
        with self.assertRaises(preview.PreviewError):
            preview.finalize_wav(bytes(empty))

    def test_free_api_uses_selected_voice_and_saves_actual_wav(self):
        audio = wav_audio()
        response = MagicMock()
        response.__enter__.return_value = response
        response.read.side_effect = [audio[:1], audio[1:]]
        opener = MagicMock()
        opener.open.return_value = response
        with tempfile.TemporaryDirectory() as directory, patch.object(preview, "build_opener", return_value=opener):
            path = Path(directory) / "sample.wav"
            result = preview.generate_sample("private-test-key", "你今天怎么样？", path, voice_config=self.voice)
            request = opener.open.call_args.args[0]
            self.assertEqual(request.full_url, "https://api.fish.audio/v1/tts")
            self.assertEqual(request.get_header("Model"), "s2.1-pro-free")
            self.assertEqual(json.loads(request.data)["reference_id"], '0' * 32)
            self.assertEqual(path.read_bytes(), audio)
            with wave.open(str(path)) as sound:
                self.assertEqual(sound.getnframes(), 441)
            self.assertNotIn("private-test-key", json.dumps(result))

    def test_payment_error_never_retries_or_exposes_secret(self):
        opener = MagicMock()
        opener.open.side_effect = HTTPError("https://api.fish.audio/v1/tts", 402, "private-test-key", {}, None)
        with tempfile.TemporaryDirectory() as directory, patch.object(preview, "build_opener", return_value=opener):
            path = Path(directory) / "sample.wav"
            with self.assertRaises(preview.PreviewError) as caught:
                preview.generate_sample("private-test-key", "你好", path, voice_config=self.voice)
            self.assertEqual(opener.open.call_count, 1)
            self.assertIn("未尝试收费模型", str(caught.exception))
            self.assertNotIn("private-test-key", str(caught.exception))
            self.assertFalse(path.exists())

    def test_non_audio_response_is_not_saved(self):
        response = MagicMock()
        response.__enter__.return_value = response
        response.read.side_effect = [b"{", b'"error":"invalid response"}']
        opener = MagicMock()
        opener.open.return_value = response
        with tempfile.TemporaryDirectory() as directory, patch.object(preview, "build_opener", return_value=opener):
            path = Path(directory) / "sample.wav"
            with self.assertRaises(preview.PreviewError):
                preview.generate_sample("private-test-key", "你好", path, voice_config=self.voice)
            self.assertFalse(path.exists())

    def test_redirect_never_forwards_authorization(self):
        handler = preview.NoRedirects()
        self.assertIsNone(handler.redirect_request(None, None, 302, "", {}, "https://untrusted.example"))

    @unittest.skipUnless(os.name == "nt", "Windows DPAPI")
    def test_saved_key_is_encrypted_for_current_windows_user(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(preview, "KEY_FILE", Path(directory) / "fish-key.bin"), patch.dict(os.environ, {"FISH_API_KEY": "", "FISH_AUDIO_API_KEY": ""}):
            preview.save_key("private-test-key")
            self.assertNotIn(b"private-test-key", preview.KEY_FILE.read_bytes())
            self.assertEqual(preview.load_key(), "private-test-key")


if __name__ == "__main__":
    unittest.main()
