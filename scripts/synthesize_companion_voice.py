"""Synthesize with the current companion's user-configured Fish Audio voice."""
import base64
import json
from pathlib import Path
import re
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from account_personas import PersonaStore, read_json
from companion_speech import VoiceStore
from fish_audio_client import generate_sample, load_key, PreviewError


def synthesize(store, request):
    persona_id = request.get('personaId', '')
    local_id = request.get('localId', '')
    text = request.get('text', '')
    if (not isinstance(persona_id, str) or not re.fullmatch(r'[a-f0-9]{32}', persona_id)
            or not isinstance(local_id, str) or not isinstance(text, str) or not text.strip() or len(text) > 500):
        raise PreviewError('无效的语音请求。')
    entry = store.assignments().get(local_id, {})
    expected = store.agent_id(local_id, persona_id)
    owner = read_json(store.state / 'agents' / expected / 'wechat-ai-owner.json', {})
    if (entry.get('persona_id') != persona_id or entry.get('agent_id') != expected
            or owner.get('local_id') != local_id or owner.get('persona_id') != persona_id):
        raise PreviewError('语音请求不属于当前陪伴。')
    settings = VoiceStore(store).load(persona_id)
    if settings['mode'] != 'smart':
        raise PreviewError('当前陪伴未开启语音。')
    service = VoiceStore(store).load_service(persona_id)
    if service is None:
        raise PreviewError('当前陪伴未配置语音服务。')
    directory = store.workspace(local_id, persona_id) / '.voice-output'
    if directory.resolve() != directory:
        raise PreviewError('语音目录是链接。')
    directory.mkdir(exist_ok=True)
    output = directory / (uuid.uuid4().hex + '.wav')
    try:
        result = generate_sample(load_key(), text, output, voice_config=service)
        return {'ok': True, 'audio': base64.b64encode(output.read_bytes()).decode('ascii'),
                'audio_seconds': result['audio_seconds']}
    finally:
        output.unlink(missing_ok=True)


if __name__ == '__main__':
    try:
        from wechat_bot_app import persona_store
        request = json.load(sys.stdin)
        if not isinstance(request, dict):
            raise PreviewError('无效的语音请求。')
        result = synthesize(persona_store(), request)
        sys.stdout.write(json.dumps(result))
    except Exception:
        # Do not leak keys, request text, raw API errors, or credentials to Gateway logs.
        sys.stdout.write(json.dumps({'ok': False, 'error': 'companion_voice_unavailable'}))
        sys.exit(1)
