'''Detailed isolated tests; audit transport without changing reply generation.'''
from concurrent.futures import ThreadPoolExecutor, as_completed
from hashlib import sha256
import io
import json
from pathlib import Path
import re
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import persona_chat as chat
import wechat_bot_app as app
from scripts.persona_evaluation_policy import enforce_models, fatal_provider_status


def save(path, value):
    with path.open('x', encoding='utf-8') as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write(chr(10))


def main():
    directory = Path(sys.argv[1]).resolve()
    credentials = json.load(sys.stdin)
    if credentials['base_url'].rstrip('/') != 'https://api.deepseek.com':
        raise ValueError('Unexpected test endpoint')
    if (directory / 'summary.json').exists() or list(directory.glob('*.request.json')):
        raise ValueError('Never overwrite completed or partial requests')
    method = json.loads((directory / 'method.json').read_text(encoding='utf-8'))
    enforce_models(directory, {'generator':credentials['model']})
    overrides = method.get('request_overrides', {})
    if set(overrides) - {'thinking', 'max_tokens', 'reasoning_effort'}:
        raise ValueError('Unsupported evaluation parameter override')
    if 'thinking' in overrides and overrides['thinking'] not in (
            {'type': 'enabled'}, {'type': 'disabled'}):
        raise ValueError('Unexpected thinking mode')
    if 'reasoning_effort' in overrides and overrides['reasoning_effort'] not in {'low', 'high', 'max'}:
        raise ValueError('Unexpected reasoning effort')
    if 'reasoning_effort' in overrides and overrides.get('thinking') != {'type':'enabled'}:
        raise ValueError('Reasoning effort requires enabled thinking')
    if 'max_tokens' in overrides and not 1200 <= overrides['max_tokens'] <= 8192:
        raise ValueError('Unexpected evaluation token budget')
    for relative, expected in method['code_sha256'].items():
        if sha256((ROOT / relative).read_bytes()).hexdigest() != expected:
            raise ValueError('Source changed after snapshot: ' + relative)
    soul = (directory / 'persona-used.md').read_text(encoding='utf-8')
    assert sha256(soul.encode()).hexdigest() == method['role_sha256']
    contract_path = directory / 'contract-used.txt'
    if method.get('contract_sha256'):
        contract = contract_path.read_text(encoding='utf-8')
        assert sha256(contract.encode()).hexdigest() == method['contract_sha256']
        # Only this isolated process uses the selected evaluation version.
        chat.FACTUAL_REPLY_RULES = contract
    elif contract_path.exists():
        raise ValueError('Contract override needs an explicit frozen hash')
    cases = json.loads((directory / 'cases.json').read_text(encoding='utf-8'))
    ids = [case['id'] for case in cases]
    assert len(ids) == len(set(ids)) and all(re.fullmatch('[A-Z][0-9]{2}', ident) for ident in ids)
    corpus = app.load_chat_files(list(app.REFERENCE_CHAT_PATHS))
    reviewed = app.prepare_retrieval_messages(corpus)
    assert reviewed and all(row.review_status == 'approved' for row in reviewed)
    save(directory / 'retrieval-inventory.json', {
        'loaded_rows': len(corpus), 'approved_rows': len(reviewed),
        'rows': [{'id': row.message_id, 'speaker': row.speaker} for row in reviewed],
    })

    state = threading.local()
    stopped = threading.Event()
    original_urlopen = chat.urlopen

    def audited_urlopen(request, timeout=90):
        started = time.monotonic()
        body = json.loads(request.data.decode('utf-8'))
        if overrides:
            body.update(overrides)
            request.data = json.dumps(body, ensure_ascii=False).encode('utf-8')
        # Authorization headers are deliberately never written.
        save(state.stem.with_suffix('.request.json'), {
            'endpoint': request.full_url, 'body': body,
            'use_retrieval': state.use_retrieval, 'matched_ids': state.matched_ids,
        })
        with original_urlopen(request, timeout=timeout) as response:
            raw = response.read(2_000_001)
        try:
            data = json.loads(raw.decode('utf-8'))
            choice = data.get('choices', [{}])[0]
            state.api = dict(api_response_id=data.get('id'), model=data.get('model'),
                             finish_reason=choice.get('finish_reason'), usage=data.get('usage'))
            save(state.stem.with_suffix('.transport.json'), dict(state.api,
                 assistant_content=choice.get('message',{}).get('content'), response_bytes=len(raw),
                 elapsed_seconds=round(time.monotonic()-started,3)))
        except (ValueError, IndexError, AttributeError):
            state.api = {}
        return io.BytesIO(raw)

    chat.urlopen = audited_urlopen

    def run_case(case):
        use_retrieval = bool(case.get('use_retrieval', False))
        session = chat.TrialSession(soul, reviewed if use_retrieval else [], app.redact_sensitive)
        history = case.get('history', [])
        if len(history) % 2:
            raise ValueError('History must have complete user/assistant pairs')
        for i in range(0, len(history), 2):
            assert [history[i]['role'], history[i+1]['role']] == ['user', 'assistant']
            session.turns.append((history[i]['content'], history[i+1]['content']))
        result = dict(case, results=[], failures=[])
        for index, user in enumerate(case['turns'], 1):
            state.stem = directory / (case['id'] + '-' + str(index))
            state.use_retrieval = use_retrieval
            state.api = {}
            started = time.monotonic()
            try:
                if stopped.is_set():
                    raise RuntimeError('Not attempted: earlier authorization/payment error stopped batch')
                turn = session.prepare(user, use_retrieval=use_retrieval)
                state.matched_ids = turn.matched_ids
                reply = chat.call_reply(credentials['base_url'], credentials['model'],
                                        credentials['api_key'], turn.request_messages)
                record = dict(turn=index, user=user, reply=reply, matched_ids=turn.matched_ids,
                              elapsed_seconds=round(time.monotonic()-started, 3), **state.api)
                save(state.stem.with_suffix('.response.json'), record)
                with state.stem.with_suffix('.txt').open('x', encoding='utf-8') as handle:
                    handle.write(reply)
                session.complete(turn, reply)
                result['results'].append(record)
            except Exception as exc:
                status = fatal_provider_status(exc)
                if status:
                    stopped.set()
                message = str(exc).replace(credentials['api_key'], '[REDACTED]')
                error = dict(id=case['id'], turn=index, error=message, http_status=status,
                             elapsed_seconds=round(time.monotonic()-started,3))
                save(state.stem.with_suffix('.error.json'), error)
                result['failures'].append(error)
                break
        save(directory / (case['id'] + '.json'), result)
        print(json.dumps({'case_complete': case['id'], 'replies': len(result['results']),
                          'failures': len(result['failures'])}, ensure_ascii=False), flush=True)
        return result

    started = time.monotonic()
    completed = {}
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = {pool.submit(run_case, case): case['id'] for case in cases}
        for future in as_completed(futures):
            result = future.result()
            completed[result['id']] = result
    ordered = [completed[ident] for ident in ids]
    summary = dict(
        method='Actual TrialSession.prepare and call_reply; synthetic histories; no WeChat sends',
        role_sha256=method['role_sha256'], expected_calls=sum(len(c['turns']) for c in cases),
        actual_replies=sum(len(c['results']) for c in ordered),
        actual_api_responses=len(list(directory.glob('*.response.json'))),
        transport_responses=len(list(directory.glob('*.transport.json'))),
        stopped_after_fatal_provider_error=stopped.is_set(),
        completed_cases=sum(not c['failures'] for c in ordered), total_cases=len(cases),
        wall_seconds=round(time.monotonic()-started, 3),
        failures=[error for c in ordered for error in c['failures']], cases=ordered,
    )
    save(directory / 'summary.json', summary)
    print(json.dumps({key: value for key, value in summary.items() if key != 'cases'}, ensure_ascii=False))


if __name__ == '__main__':
    main()
