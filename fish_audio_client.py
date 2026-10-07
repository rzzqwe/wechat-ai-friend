"""Fish Audio free synthesis and encrypted local credentials."""
from __future__ import annotations

import json
import io
import os
from pathlib import Path
import queue
import socket
import sys
import threading
import time
import wave
from datetime import datetime, timezone, timedelta
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener, HTTPRedirectHandler

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

FREE_MODEL = 's2.1-pro-free'
KEY_FILE = ROOT / "data" / "fish-audio-key.bin"
REPORT_FILE = ROOT / "data" / "diagnostics" / "fish-audio-preview.json"
SAMPLES = ()


class PreviewError(Exception):
    pass


class NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def finalize_wav(audio: bytes) -> tuple[bytes, float]:
    """Turn the API's streaming WAV into a finite PCM file without altering samples."""
    if len(audio) < 44 or audio[:4] != b"RIFF" or audio[8:12] != b"WAVE":
        raise PreviewError("接口没有返回可播放的 WAV 音频；未保存无效结果。")
    try:
        with wave.open(io.BytesIO(audio), "rb") as source:
            channels = source.getnchannels()
            width = source.getsampwidth()
            rate = source.getframerate()
            if channels not in (1, 2) or width != 2 or rate <= 0:
                raise PreviewError("返回的音频格式不适合 Windows 试听播放。")
            frame_bytes = channels * width
            # Fish streams an unknown-length RIFF/data header. Bound the read by
            # the actual response, then let wave write the correct final sizes.
            pcm = source.readframes(min(source.getnframes(), len(audio) // frame_bytes))
        if not pcm or len(pcm) % frame_bytes:
            raise PreviewError("返回的音频为空或包含不完整的声音帧。")
        output = io.BytesIO()
        with wave.open(output, "wb") as target:
            target.setnchannels(channels)
            target.setsampwidth(width)
            target.setframerate(rate)
            target.writeframes(pcm)
        return output.getvalue(), round(len(pcm) / frame_bytes / rate, 3)
    except (wave.Error, EOFError, ValueError):
        raise PreviewError("无法解析返回的 WAV 音频；未保存无效结果。") from None


def generate_sample(key: str, text: str, destination: Path, *, voice_config=None) -> dict:
    key = key.strip()
    if not key or any(char.isspace() for char in key):
        raise PreviewError("请填写完整的 Fish Audio API Key。")
    if not text.strip() or len(text) > 500:
        raise PreviewError("试听文本需要在 1 到 500 字以内。")
    if voice_config is None:
        raise PreviewError('请先配置自己有权使用的 Fish Audio 音色。')
    from companion_speech import VoiceStore
    selected_voice = VoiceStore.validate_service(voice_config)
    body = dict(selected_voice["generation"], text=text, reference_id=selected_voice["reference_id"])
    request = Request("https://api.fish.audio/v1/tts",
                      data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
                      headers={"Authorization": "Bearer " + key,
                               "Content-Type": "application/json", "model": FREE_MODEL},
                      method="POST")
    started = time.perf_counter()
    try:
        # No redirects, automatic retries, or fallback to a billable model.
        with build_opener(NoRedirects()).open(request, timeout=45) as response:
            first = response.read(1)
            first_audio_seconds = time.perf_counter() - started
            audio = first + response.read(10 * 1024 * 1024)
    except HTTPError as exc:
        messages = {
            401: "Key 无效或已失效，请检查 Fish Audio API Key。",
            402: "免费接口返回了额度/付费限制，需要核实账号；未尝试收费模型。",
            403: "账号或这个音色的使用权限受限。",
            404: "接口或音色当前不可用。",
            429: "免费接口暂时限流，请稍后手动重试。",
        }
        raise PreviewError(messages.get(exc.code, f"Fish Audio 返回 HTTP {exc.code}，未尝试收费模型。")) from None
    except (URLError, TimeoutError, socket.timeout):
        raise PreviewError("连接失败或请求超时，请检查网络后手动重试。") from None
    if len(audio) > 10 * 1024 * 1024:
        raise PreviewError("返回的音频超过试听大小限制。")
    audio, audio_seconds = finalize_wav(audio)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(".wav.tmp")
    temporary.write_bytes(audio)
    temporary.replace(destination)
    return {"path": str(destination), "text": text,
            "audio_seconds": audio_seconds, "wav_header_normalized": True,
            "first_audio_seconds": round(first_audio_seconds, 3),
            "total_seconds": round(time.perf_counter() - started, 3), "bytes": len(audio)}


def load_key() -> str:
    for name in ("FISH_API_KEY", "FISH_AUDIO_API_KEY"):
        if os.environ.get(name, "").strip():
            return os.environ[name].strip()
    if KEY_FILE.exists():
        from wechat_bot_app import _unprotect_secret
        return _unprotect_secret(KEY_FILE.read_bytes()).decode("utf-8")
    return ""


def save_key(key: str):
    from wechat_bot_app import _protect_secret
    KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
    encrypted = _protect_secret(key.encode("utf-8"))
    temporary = KEY_FILE.with_suffix(".bin.tmp")
    temporary.write_bytes(encrypted)
    temporary.replace(KEY_FILE)


def write_report(report: dict):
    REPORT_FILE.parent.mkdir(parents=True, exist_ok=True)
    temporary = REPORT_FILE.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(REPORT_FILE)

