"""Reference voice analysis using the Beijing Qwen-Audio free-trial-only model."""
from __future__ import annotations

import base64
import hashlib
import io
import json
import os
from pathlib import Path
import re
import time
import wave
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener

from gemini_audio import AudioAnalysisError, NoRedirects, SYSTEM, reference_files

ROOT = Path(__file__).resolve().parent
MODEL = 'qwen-audio-turbo'
ENDPOINT = 'https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation'
KEY_FILE = ROOT / 'data/qwen-audio-key.bin'
REPORT_FILE = ROOT / 'data/diagnostics/qwen-reference-analysis.json'
LAST_ERROR_FILE = ROOT / 'data/diagnostics/qwen-audio-last-error.json'
KEY_URL = 'https://bailian.console.aliyun.com/cn-beijing/model/settings/api-key'
QUOTA_URL = 'https://bailian.console.aliyun.com/cn-beijing/costing-balance/free-quota'


def load_key():
    if KEY_FILE.is_file():
        from wechat_bot_app import _unprotect_secret
        return _unprotect_secret(KEY_FILE.read_bytes()).decode('utf-8')
    return os.environ.get('DASHSCOPE_API_KEY', '').strip() or os.environ.get('QWEN_API_KEY', '').strip()


def save_key(key):
    key = key.strip()
    if not key.startswith('sk-') or len(key) < 12 or any(char.isspace() for char in key):
        raise AudioAnalysisError('请填写百炼北京地域的 API Key（sk- 开头），不要填写 Google 或 Fish 的 Key。')
    from wechat_bot_app import _protect_secret
    from companion_media import atomic_bytes
    KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
    atomic_bytes(KEY_FILE, _protect_secret(key.encode('utf-8')))


def build_requests(files):
    files = list(map(Path, files))
    if len(files) != 3:
        raise AudioAnalysisError('本次分析需要三段原声音频。')
    requests = []
    for index, path in enumerate(files, 1):
        payload = path.read_bytes()
        if not payload or len(payload) > 2 * 1024 * 1024:
            raise AudioAnalysisError('音频文件为空或过大。')
        try:
            with wave.open(io.BytesIO(payload), 'rb') as source:
                duration = source.getnframes() / source.getframerate()
                if not 0 < duration <= 30:
                    raise AudioAnalysisError('每段音频需要在 30 秒以内。')
                if not source.readframes(source.getnframes()):
                    raise AudioAnalysisError('音频文件没有可读取的声音帧。')
        except (wave.Error, EOFError, ZeroDivisionError):
            raise AudioAnalysisError('请使用有效的 WAV 原声音频。') from None
        prompt = f'''这是原声{index}。只分析实际听到的音色和表达风格，不转录私人对话。
请描述音色明暗、厚薄、气声、鼻音、共鸣位置，以及音高起伏、语速、节奏和尾音。
不得推测说话者身份、性别、年龄、健康或个人性格。短片段和压缩影响需明确说明。
只输出一个JSON对象，字段为：timbre（音色特点）、delivery（语气和节奏）、
recording_quality（录音质量）、confidence（high/medium/low）、uncertainty（不确定处）、
search_terms（适合查找相近合成声音的描述词数组）。描述字段必须是字符串，不要使用嵌套对象。
不要引用音频中说出的词，不要推测饮酒或健康状态。不要声称能找到完全相同的音色。'''
        body = {'model': MODEL, 'input': {'messages': [
            {'role': 'system', 'content': [{'text': SYSTEM}]},
            {'role': 'user', 'content': [
                {'audio': 'data:;base64,' + base64.b64encode(payload).decode('ascii')},
                {'text': prompt}]}]},
            'parameters': {'result_format': 'message', 'max_tokens': 1200, 'temperature': .2}}
        meta = {'clip': index, 'file': path.name, 'duration_seconds': round(duration, 3),
                'sha256': hashlib.sha256(payload).hexdigest()}
        requests.append((body, meta))
    return requests


def safe_provider_text(value, key):
    text = str(value)
    if key:
        text = text.replace(key, '[REDACTED]')
    text = re.sub(r'sk-[A-Za-z0-9_-]+', '[REDACTED]', text)
    return ' '.join(text.split())[:800]


def describe_error(status, body, key, *, model=MODEL):
    if not isinstance(body, dict):
        body = {}
    code = safe_provider_text(body.get('code', ''), key)
    provider_message = safe_provider_text(body.get('message', ''), key)
    raw = provider_message.lower()
    lower_code = code.lower()
    category = 'request_failed'
    if (lower_code in ('allocationquota.freetieronly', 'freequotaexhausted', 'freetierexhausted')
            or 'free allocated quota exceeded' in raw or 'free tier of the model has been exhausted' in raw):
        category = 'free_quota_unavailable'
        message = (f'{model} 的免费额度已到期或耗尽；等待或更换 Key 不会重置额度。'
                   '请在百炼北京免费额度页查看余量和到期时间。已停止，不会转用付费模型。')
    elif status == 429 or lower_code.startswith('throttling.') and status != 403:
        category = 'rate_limited'
        message = '千问暂时限流，请稍后手动重试；这次错误不代表免费额度耗尽。已停止，不会转用付费模型。'
    elif 'quota' in lower_code or 'quota' in raw:
        category = 'quota_unknown'
        message = '千问拒绝了当前额度请求，请在免费额度页核对余量、到期时间和模型授权。已停止，不会转用付费模型。'
    elif status == 401 or 'invalidapikey' in code.lower():
        category = 'invalid_key'
        message = '百炼 Key 无效，请检查是否完整复制了北京地域的 API Key。'
    elif status == 403 or 'accessdenied' in code.lower():
        category = 'access_denied'
        message = '这个百炼 Key 暂无模型调用权限，请检查北京地域、业务空间和模型授权。'
    elif status == 404 or 'model' in code.lower() and 'invalid' in code.lower():
        message = '当前账号暂时不能使用所选千问免费音频模型。'
    else:
        message = f'千问请求失败（HTTP {status}），请检查免费模型是否已开通。'
    return AudioAnalysisError(message), {'model': model, 'http_status': status,
                                         'provider_code': code[:100], 'provider_message': provider_message,
                                         'category': category, 'message': message}


def parse_clip(response, index, key):
    try:
        choice = response['output']['choices'][0]
        content = choice['message']['content']
        text = '\n'.join(item['text'] for item in content if isinstance(item, dict) and isinstance(item.get('text'), str)).replace(key, '[REDACTED]')
        usage = response.get('usage', {})
        audio_tokens = usage.get('audio_tokens', 0)
        if not text.strip() or not isinstance(audio_tokens, (int, float)) or audio_tokens <= 0:
            raise AudioAnalysisError('千问未确认处理音频输入，不能把结果当作听辨结论。')
        if choice.get('finish_reason') not in ('stop', None):
            raise AudioAnalysisError('千问尚未完整分析这段声音。')
        if text.startswith('```'):
            text = '\n'.join(text.splitlines()[1:-1])
        try:
            item = json.loads(text)
        except ValueError:
            item = {'timbre': text, 'delivery': '', 'recording_quality': '', 'confidence': 'low',
                    'uncertainty': '模型未按结构化格式返回，保留描述供人工核对。', 'search_terms': []}
        if not isinstance(item, dict):
            raise AudioAnalysisError('千问的声音分析格式不正确。')
        result = {name: description_text(item.get(name, ''), key) for name in
                  ('timbre', 'delivery', 'recording_quality', 'confidence', 'uncertainty')}
        if not result['timbre'].strip():
            raise AudioAnalysisError('千问没有提供可读取的音色分析。')
        terms = item.get('search_terms', [])
        if not isinstance(terms, list):
            terms = []
        result.update(clip=index, search_terms=[str(term).replace(key, '[REDACTED]') for term in terms][:8],
                      audio_tokens=audio_tokens)
        return result
    except (TypeError, KeyError, IndexError):
        raise AudioAnalysisError('千问没有返回可读取的声音分析结果。') from None


def description_text(value, key):
    if isinstance(value, dict):
        value = '；'.join(str(part) for part in value.values())
    return str(value).replace(key, '[REDACTED]')


def analyze(key, files, *, audio_submission_authorized=False, progress=None):
    if not audio_submission_authorized:
        raise AudioAnalysisError('需要明确选择发送三段原声到阿里云分析。')
    key = key.strip()
    if not key.startswith('sk-') or len(key) < 12 or any(char.isspace() for char in key):
        raise AudioAnalysisError('请填写百炼北京地域的 API Key。')
    if MODEL != 'qwen-audio-turbo':
        raise AudioAnalysisError('这个工具仅允许使用所选的免费体验模型。')
    planned = build_requests(files)
    report = {'model': MODEL, 'provider': 'aliyun', 'region': 'cn-beijing',
              'status': 'running', 'inputs': [],
              'analysis': {'clips': [], 'common_features': '', 'search_terms': [],
                  'selection_advice': '根据各段的音色和表达特点继续筛选，需要实际试听确认。',
                  'limitations': '片段较短，音色描述仅作筛选参考，不代表精确匹配。'}}
    started = time.perf_counter()
    save_report(report)
    for body, meta in planned:
        if progress:
            progress(meta['clip'])
        request = Request(ENDPOINT, data=json.dumps(body, ensure_ascii=False).encode('utf-8'),
                          headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + key}, method='POST')
        try:
            with build_opener(NoRedirects()).open(request, timeout=45) as response:
                raw = response.read(2 * 1024 * 1024 + 1)
            if len(raw) > 2 * 1024 * 1024:
                raise AudioAnalysisError('千问返回的内容超过大小限制。')
            data = json.loads(raw.decode('utf-8'))
            if not isinstance(data, dict):
                raise AudioAnalysisError('千问返回的响应格式不正确。')
        except HTTPError as error:
            try:
                data = json.loads(error.read(32768).decode('utf-8'))
            except Exception:
                data = {}
            failure, diagnostic = describe_error(error.code, data, key)
            from account_personas import write_json
            write_json(LAST_ERROR_FILE, diagnostic)
            mark_failed(report, str(failure), meta['clip'], started)
            raise failure from None
        except (URLError, TimeoutError, OSError):
            message = '连接千问失败或超时，请检查网络后手动重试。'
            mark_failed(report, message, meta['clip'], started)
            raise AudioAnalysisError(message) from None
        except (ValueError, UnicodeError):
            message = '千问返回的响应格式不正确。'
            mark_failed(report, message, meta['clip'], started)
            raise AudioAnalysisError(message) from None
        except AudioAnalysisError as error:
            mark_failed(report, str(error), meta['clip'], started)
            raise
        if data.get('code') and not data.get('output'):
            failure, diagnostic = describe_error(400, data, key)
            from account_personas import write_json
            write_json(LAST_ERROR_FILE, diagnostic)
            mark_failed(report, str(failure), meta['clip'], started)
            raise failure
        try:
            item = parse_clip(data, meta['clip'], key)
        except AudioAnalysisError as error:
            mark_failed(report, str(error), meta['clip'], started)
            raise
        report['inputs'].append(meta)
        report['analysis']['clips'].append(item)
        report['analysis']['search_terms'].extend(item['search_terms'])
        report.update(status='partial', elapsed_seconds=round(time.perf_counter() - started, 3))
        save_report(report)
    report['status'] = 'complete'
    report['analysis']['search_terms'] = list(dict.fromkeys(report['analysis']['search_terms']))
    save_report(report)
    return report


def save_report(report):
    from account_personas import write_json
    write_json(REPORT_FILE, report)


def mark_failed(report, message, clip, started):
    report.update(status='partial' if report['analysis']['clips'] else 'failed',
                  failed_clip=clip, error=message,
                  elapsed_seconds=round(time.perf_counter() - started, 3))
    save_report(report)
