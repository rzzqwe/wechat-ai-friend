"""Private voice settings, prompts, and live TTS preferences for each companion."""
from copy import deepcopy
from pathlib import Path
import sys
import re

from account_personas import locked, read_json, write_json

PROVIDER = 'wechat-companion-voice'
DEFAULT = {'version': 1, 'mode': 'text', 'voice_id': 'custom-fish'}
PREFS_NAME = '.companion-voice-prefs.json'


class VoiceStore:
    def __init__(self, personas):
        self.personas = personas

    def path(self, persona_id):
        path = self.personas.profile_path(persona_id).parent / 'voice-settings.json'
        if path.resolve() != path:
            raise ValueError('语音设置是链接，无法更新。')
        return path

    def load(self, persona_id):
        value = read_json(self.path(persona_id), DEFAULT)
        if (not isinstance(value, dict) or value.get('version') != 1
                or value.get('mode') not in ('text', 'smart')
                or not isinstance(value.get('voice_id'), str)):
            raise ValueError('陪伴的语音设置格式错误。')
        if value['voice_id'] != 'custom-fish':
            # Older local-machine settings cannot enable an online voice.
            value = dict(DEFAULT)
        return deepcopy(value)

    def service_path(self, persona_id):
        path = (self.personas.root / 'data/fish-service-default.json' if persona_id is None
                else self.personas.profile_path(persona_id).parent / 'fish-service.json')
        if path.resolve() != path:
            raise ValueError('语音配置是链接，无法更新。')
        return path

    @staticmethod
    def validate_service(value):
        if (not isinstance(value, dict) or value.get('provider') != 'fish-audio'
                or not re.fullmatch('[a-f0-9]{32}', str(value.get('reference_id', '')))):
            raise ValueError('请填写完整的 32 位音色 ID。')
        result = deepcopy(value)
        if not isinstance(result.get('name'), str) or not result['name'].strip():
            result['name'] = '音色 ' + result['reference_id'][:6]
        return result

    def load_service(self, persona_id):
        value = read_json(self.service_path(persona_id), None)
        return self.validate_service(value) if value is not None else None

    def save_service(self, persona_id, reference_id, name=None, rights_source=None, rights_confirmed=None):
        value = self.validate_service({'version': 1, 'provider': 'fish-audio',
            'reference_id': reference_id.strip().lower(), 'name': ' '.join(name.split())[:80] if name else '',
            'generation': {'format': 'wav', 'sample_rate': 44100, 'latency': 'normal',
                           'temperature': 0.7, 'top_p': 0.7, 'prosody': {'speed': 1.0}}})
        # Preserve older explicit records without inventing a declaration when
        # the user only provides the two configuration fields.
        if rights_source is not None:
            value['rights_source'] = rights_source.strip()[:1000]
        if rights_confirmed is not None:
            value['rights_confirmed'] = rights_confirmed
        with locked(self.personas.lock_path):
            write_json(self.service_path(persona_id), value)
        return value

    def apply_default_service(self, persona_id):
        """Snapshot the user's default for a new companion, leaving others alone."""
        with locked(self.personas.lock_path):
            target = self.service_path(persona_id)
            if target.exists():
                return False
            default = self.load_service(None)
            if default is None:
                return False
            write_json(target, default)
            return True

    def save(self, persona_id, mode, voice_id):
        if mode not in ('text', 'smart'):
            raise ValueError('请选择文字或文字和语音模式。')
        if voice_id != 'custom-fish':
            raise ValueError('请先配置当前陪伴的 Fish Audio 音色。')
        if mode == 'smart' and self.load_service(persona_id) is None:
            raise ValueError('请先配置当前陪伴的音色 ID。')
        with locked(self.personas.lock_path):
            write_json(self.path(persona_id), dict(DEFAULT, mode=mode, voice_id=voice_id))

    def tts_config(self, local_id, persona_id):
        workspace = self.personas.workspace(local_id, persona_id)
        persona_key = 'companion-' + persona_id
        binding = {'localId': local_id, 'personaId': persona_id}
        return {'auto': 'off', 'mode': 'final', 'provider': PROVIDER,
                'persona': persona_key, 'prefsPath': str(workspace / PREFS_NAME),
                'personas': {persona_key: {'label': '当前陪伴的声音', 'provider': PROVIDER,
                    'fallbackPolicy': 'fail', 'providers': {PROVIDER: binding}}},
                'providers': {PROVIDER: binding, 'microsoft': {'enabled': False}},
                'modelOverrides': {'enabled': True, 'allowText': True, 'allowProvider': False,
                    'allowVoice': False, 'allowModelId': False, 'allowVoiceSettings': False,
                    'allowNormalization': False, 'allowSeed': False},
                'maxTextLength': 500, 'timeoutMs': 55000}

    def sync_workspace(self, persona_id, workspace):
        settings = self.load(persona_id)
        path = workspace / PREFS_NAME
        if path.resolve() != path:
            raise ValueError('语音运行设置是链接，无法更新。')
        prefs = {'tts': {'auto': 'tagged' if settings['mode'] == 'smart' else 'off',
                        'provider': PROVIDER, 'persona': 'companion-' + persona_id,
                        'maxLength': 500, 'summarize': False}}
        if read_json(path, None) != prefs:
            write_json(path, prefs)
        if settings['mode'] == 'text':
            return '## 当前陪伴的回复模式\n当前为文字模式：可以使用文字和已启用的表情图片，不生成或发送音频，不输出语音标记。'
        service = self.load_service(persona_id)
        if service is None:
            return '## 当前陪伴的回复模式\n语音服务尚未配置，使用文字和已启用的表情，不生成语音标记。'
        name = service['name']
        return '\n'.join([
            '## 当前陪伴的回复模式',
            '当前为文字和语音模式，声音由系统固定为' + name + '。',
            '每轮根据聊天内容自行选择文字、表情图片或语音，不询问对方这一轮要哪一种。',
            '对方明确要求语音、轻松问候、亲近的接话或适合用声音安慰时，可选择语音；具体问题、步骤、长内容和不适合语音的场合用文字。',
            '不每条都发语音，不机械轮换，不连续刷音频；对方明确要求文字时只用文字。',
            '选择表情时沿用表情规则；不要在同一轮同时要求语音和发送表情图片。',
            '选择语音时，先写正常的简短正文，再追加 [[tts:text]]同一段要说的话[[/tts:text]]。',
            '语音标记只供系统处理，不解释标记，不编造音频路径；不改变声音、模型或提供商，不调用语音工具。',
            '语音文本保持口语和自然停顿，最多500字；不要加入舞台说明或括号里的动作。',
            '例：你来啦，今天怎么样呀？\n[[tts:text]]你来啦，今天怎么样呀？[[/tts:text]]',
            '语音生成失败时仍保留正文作为文字回复。',
        ])


def prepare_runtime_config(store, config):
    """One-time provider setup; later mode/voice changes only update private prefs."""
    plugin_path = store.root / 'plugins' / 'companion-voice'
    if plugin_path.resolve() != plugin_path or not all((plugin_path / name).is_file()
            for name in ('index.mjs', 'package.json', 'openclaw.plugin.json')):
        raise ValueError('语音运行组件缺失。')
    plugins = config.setdefault('plugins', {})
    paths = plugins.setdefault('load', {}).setdefault('paths', [])
    if str(plugin_path) not in paths:
        paths.append(str(plugin_path))
    if isinstance(plugins.get('allow'), list) and PROVIDER not in plugins['allow']:
        plugins['allow'].append(PROVIDER)
    python = Path(sys.executable)
    if python.name.lower() == 'pythonw.exe':
        python = python.with_name('python.exe')
    plugins.setdefault('entries', {})[PROVIDER] = {'enabled': True,
        'config': {'pythonExecutable': str(python), 'projectRoot': str(store.root)}}
    from runtime_tuning import tune_companion_plugins
    tune_companion_plugins(config, plugin_path)
    voices = VoiceStore(store)
    modern = config.get('agents', {}).get('entries')
    legacy = config.get('agents', {}).get('list', [])
    for local_id, entry in store.assignments().items():
        expected = store.agent_id(local_id, entry['persona_id'])
        if entry.get('agent_id') != expected:
            raise ValueError('语音运行目录归属不匹配。')
        agent = modern.get(expected) if isinstance(modern, dict) else next((a for a in legacy if a.get('id') == expected), None)
        if agent is None:
            raise ValueError('语音运行代理缺失，请重新应用陪伴绑定。')
        agent['tts'] = voices.tts_config(local_id, entry['persona_id'])
    return config


def ensure_runtime_config(store):
    """Connect existing companions once; leave an initialized config untouched."""
    with locked(store.lock_path), locked(store.config.with_suffix('.wechat-ai.lock')):
        if not any(entry.get('provider_id') for entry in store.assignments().values()):
            # Voice can be selected before QR binding installs the runtime.
            return False
        config = read_json(store.config, {})
        if not isinstance(config, dict) or not isinstance(config.get('agents', {}).get('entries'), dict):
            raise ValueError('语音需要 OpenClaw 2026.9.6 或更高版本，请通过重新扫码完成运行环境升级。')
        updated = prepare_runtime_config(store, deepcopy(config))
        if updated == config:
            return False
        write_json(store.config, updated)
        return True
