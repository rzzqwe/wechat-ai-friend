"""Reference audio analysis with Qwen Omni and verified free-tier protection."""
from datetime import datetime, date, timezone
import hashlib
import json
from pathlib import Path
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener

import qwen_audio as shared
from qwen_audio import AudioAnalysisError, NoRedirects, load_key, save_key, reference_files

ROOT = shared.ROOT
MODEL = 'qwen3.8-omni-flash'
ENDPOINT = 'https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions'
KEY_FILE = shared.KEY_FILE
KEY_URL = shared.KEY_URL
QUOTA_URL = shared.QUOTA_URL
GUARD_FILE = ROOT / 'data/qwen-omni-free-tier.json'
REPORT_FILE = ROOT / 'data/diagnostics/qwen-omni-reference-analysis.json'
LAST_ERROR_FILE = ROOT / 'data/diagnostics/qwen-omni-last-error.json'
MAX_RESPONSE = 2 * 1024 * 1024


def confirm_free_tier(*, expires, evidence, remaining_tokens):
    """Only called after the user or visible console confirms the actual switch."""
    from account_personas import write_json
    if date.fromisoformat(expires) <= date.today() or remaining_tokens < 10000:
        raise AudioAnalysisError('请先确认未到期的免费额度足够，且用完即停已开启。')
    settings = {'model': MODEL, 'region': 'cn-beijing', 'free_tier_only': True,
                'expires': expires, 'remaining_tokens_when_checked': remaining_tokens,
                'verified_at': datetime.now(timezone.utc).isoformat(), 'evidence': evidence,
                'encrypted_key_sha256': hashlib.sha256(KEY_FILE.read_bytes()).hexdigest()}
    write_json(GUARD_FILE, settings)
    return settings


def check_free_tier():
    try:
        settings = json.loads(GUARD_FILE.read_text(encoding='utf-8'))
        checked = datetime.fromisoformat(settings['verified_at'])
        age = (datetime.now(timezone.utc) - checked).total_seconds()
        valid = (settings['model'] == MODEL == 'qwen3.8-omni-flash'
                 and settings['region'] == 'cn-beijing' and settings['free_tier_only'] is True
                 and date.fromisoformat(settings['expires']) > date.today()
                 and settings['remaining_tokens_when_checked'] >= 10000
                 and 0 <= age <= 24 * 3600
                 and settings['encrypted_key_sha256'] == hashlib.sha256(KEY_FILE.read_bytes()).hexdigest())
    except (OSError, KeyError, TypeError, ValueError):
        valid = False
    if not valid:
        raise AudioAnalysisError('调用前需要确认这个模型仍有免费额度，且百炼控制台“用完即停”已开启。')
    return settings


def build_requests(files):
    requests = []
    for native, meta in shared.build_requests(files):
        content = native['input']['messages'][1]['content']
        body = {'model': MODEL, 'messages': [
            {'role': 'system', 'content': shared.SYSTEM},
            {'role': 'user', 'content': [
                {'type': 'input_audio', 'input_audio': {'data': content[0]['audio'], 'format': 'wav'}},
                {'type': 'text', 'text': content[1]['text']}]}],
            'modalities': ['text'], 'stream': True, 'stream_options': {'include_usage': True},
            'reasoning_effort': 'none', 'max_tokens': 1200}
        requests.append((body, meta))
    return requests


def provider_failure(status, body, key):
    if isinstance(body, dict) and isinstance(body.get('error'), dict):
        body = body['error']
    failure, diagnostic = shared.describe_error(status, body, key, model=MODEL)
    from account_personas import write_json
    write_json(LAST_ERROR_FILE, diagnostic)
    return failure


def read_stream(response, key):
    text = []
    usage = {}
    finish = None
    size = 0
    started = time.perf_counter()
    for raw in response:
        size += len(raw)
        if size > MAX_RESPONSE or time.perf_counter() - started > 55:
            raise AudioAnalysisError('千问返回内容过大或分析超时，已停止。')
        line = raw.decode('utf-8').strip()
        if not line or line.startswith(':'):
            continue
        if not line.startswith('data:'):
            if line.startswith('{'):
                try:
                    body = json.loads(line)
                except ValueError:
                    raise AudioAnalysisError('千问返回的流式响应格式不正确。') from None
                if body.get('error') or body.get('code'):
                    raise provider_failure(400, body, key)
            continue
        payload = line[5:].strip()
        if payload == '[DONE]':
            break
        try:
            chunk = json.loads(payload)
        except ValueError:
            raise AudioAnalysisError('千问返回的流式响应格式不正确。') from None
        if not isinstance(chunk, dict):
            raise AudioAnalysisError('千问返回的流式响应格式不正确。')
        if chunk.get('error') or chunk.get('code'):
            raise provider_failure(400, chunk, key)
        if isinstance(chunk.get('usage'), dict):
            usage = chunk['usage']
        for choice in chunk.get('choices') or []:
            if choice.get('index', 0) != 0:
                continue
            delta = choice.get('delta') or {}
            if delta.get('tool_calls'):
                raise AudioAnalysisError('音色分析不允许调用工具，已停止。')
            if isinstance(delta.get('content'), str):
                text.append(delta['content'])
            if choice.get('finish_reason'):
                finish = choice['finish_reason']
    if finish != 'stop':
        raise AudioAnalysisError('千问没有完整返回音色分析，不能当作成功结果。')
    audio_tokens = (usage.get('prompt_tokens_details') or {}).get('audio_tokens', 0)
    native = {'output': {'choices': [{'finish_reason': finish, 'message': {
        'content': [{'text': ''.join(text)}]}}]}, 'usage': {'audio_tokens': audio_tokens}}
    safe_usage = {field: value for field in ('prompt_tokens', 'completion_tokens', 'total_tokens')
                  if isinstance((value := usage.get(field)), int) and value >= 0}
    safe_usage['audio_tokens'] = audio_tokens if isinstance(audio_tokens, (int, float)) else 0
    return native, safe_usage


def analyze(key, files, *, audio_submission_authorized=False, progress=None):
    if not audio_submission_authorized:
        raise AudioAnalysisError('需要明确选择发送三段原声到阿里云分析。')
    check_free_tier()
    key = key.strip()
    if key != load_key():
        raise AudioAnalysisError('Key 已改变，需要重新确认该账号的免费额度与用完即停。')
    planned = build_requests(files)
    report = {'model': MODEL, 'provider': 'aliyun', 'region': 'cn-beijing',
              'status': 'running', 'inputs': [], 'free_tier_only_verified': True,
              'analysis': {'clips': [], 'common_features': '', 'search_terms': [],
                           'selection_advice': '根据实际音色描述筛选相近声音，需要试听确认。',
                           'limitations': '片段较短且有压缩，分析不能保证音色精确匹配。'}}
    started = time.perf_counter()
    save_report(report)
    for body, meta in planned:
        try:
            check_free_tier()
            if progress:
                progress(meta['clip'])
            req = Request(ENDPOINT, data=json.dumps(body, ensure_ascii=False).encode('utf-8'),
                          headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + key}, method='POST')
            with build_opener(NoRedirects()).open(req, timeout=45) as response:
                native, usage = read_stream(response, key)
            clip = shared.parse_clip(native, meta['clip'], key)
        except HTTPError as error:
            try:
                error_body = json.loads(error.read(32768).decode('utf-8'))
            except (ValueError, UnicodeError):
                error_body = {}
            failure = provider_failure(error.code, error_body, key)
            fail_report(report, str(failure), meta['clip'], started)
            raise failure from None
        except (URLError, OSError, TimeoutError):
            message = '连接千问失败或超时，已停止；请稍后手动重试。'
            fail_report(report, message, meta['clip'], started)
            raise AudioAnalysisError(message) from None
        except (AudioAnalysisError, ValueError, UnicodeError, TypeError, AttributeError) as error:
            message = str(error) if isinstance(error, AudioAnalysisError) else '千问返回的分析格式不正确。'
            fail_report(report, message, meta['clip'], started)
            raise AudioAnalysisError(message) from None
        report['inputs'].append(meta)
        clip['usage'] = usage
        report['analysis']['clips'].append(clip)
        report['analysis']['search_terms'].extend(clip['search_terms'])
        report.update(status='partial', elapsed_seconds=round(time.perf_counter() - started, 3))
        save_report(report)
    report['status'] = 'complete'
    report['analysis']['search_terms'] = list(dict.fromkeys(report['analysis']['search_terms']))
    from account_personas import write_json
    write_json(LAST_ERROR_FILE, {'model': MODEL, 'status': 'resolved', 'message': ''})
    save_report(report)
    return report


def save_report(report):
    from account_personas import write_json
    write_json(REPORT_FILE, report)


def fail_report(report, message, clip, started):
    report.update(status='partial' if report['analysis']['clips'] else 'failed', error=message,
                  failed_clip=clip, elapsed_seconds=round(time.perf_counter() - started, 3))
    save_report(report)
