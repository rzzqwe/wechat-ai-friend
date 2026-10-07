"""Evaluate real TrialSession/call_reply with isolated synthetic histories."""
from concurrent.futures import ThreadPoolExecutor, as_completed
from hashlib import sha256
import json
from pathlib import Path
import re
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from persona_chat import TrialSession, call_reply


def save(path, value):
    with path.open('x', encoding='utf-8') as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write(chr(10))


def main():
    directory = Path(sys.argv[1]).resolve()
    credentials = json.load(sys.stdin)
    from scripts.persona_evaluation_policy import enforce_models
    enforce_models(directory, {'generator':credentials['model']})
    if credentials['base_url'].rstrip('/') != 'https://api.deepseek.com':
        raise ValueError('Unexpected evaluation endpoint')
    soul = (directory / 'persona-used.md').read_text(encoding='utf-8')
    cases = json.loads((directory / 'cases.json').read_text(encoding='utf-8'))
    if (directory / 'summary.json').exists() or list(directory.glob('*.request.json')):
        raise ValueError('Do not overwrite an existing evaluation')
    ids = [case['id'] for case in cases]
    if len(ids) != len(set(ids)) or not all(re.fullmatch('[A-Z][0-9]{2}', ident) for ident in ids):
        raise ValueError('Invalid or duplicate case IDs')

    def run_case(case):
        session = TrialSession(soul, [])
        history = case.get('history', [])
        if len(history) % 2:
            raise ValueError('History must contain complete user/assistant pairs')
        for index in range(0, len(history), 2):
            if [history[index]['role'], history[index + 1]['role']] != ['user', 'assistant']:
                raise ValueError('Unexpected history roles')
            session.turns.append((history[index]['content'], history[index + 1]['content']))
        result = dict(case, results=[])
        for index, user in enumerate(case['turns'], 1):
            turn = session.prepare(user, use_retrieval=False)
            stem = directory / (case['id'] + '-' + str(index))
            request_record = {
                'entrypoint': 'persona_chat.call_reply',
                'endpoint': 'https://api.deepseek.com/v1/chat/completions',
                'model': credentials['model'],
                'temperature': 0.4, 'max_tokens': 1200,
                'messages': turn.request_messages,
            }
            if credentials['model'].strip().lower() == 'deepseek-flash':
                request_record['thinking'] = {'type': 'disabled'}
            save(stem.with_suffix('.request.json'), request_record)
            started = time.monotonic()
            reply = call_reply(credentials['base_url'], credentials['model'],
                               credentials['api_key'], turn.request_messages)
            record = dict(turn=index, user=user, reply=reply,
                          elapsed_seconds=round(time.monotonic() - started, 3))
            save(stem.with_suffix('.response.json'), record)
            with stem.with_suffix('.txt').open('x', encoding='utf-8') as handle:
                handle.write(reply)
            session.complete(turn, reply)
            result['results'].append(record)
        save(directory / (case['id'] + '.json'), result)
        return result

    completed, failures = {}, []
    with ThreadPoolExecutor(max_workers=3) as pool:
        pending = {pool.submit(run_case, case): case['id'] for case in cases}
        for future in as_completed(pending):
            try:
                result = future.result()
                completed[result['id']] = result
            except Exception as exc:
                message = str(exc).replace(credentials['api_key'], '[REDACTED]')
                failures.append(dict(id=pending[future], error=message))
    results = [completed[ident] for ident in ids if ident in completed]
    summary = dict(
        entrypoint='Actual TrialSession.prepare and call_reply; no WeChat sends or memory writes',
        role_sha256=sha256(soul.encode('utf-8')).hexdigest(),
        expected_calls=sum(len(case['turns']) for case in cases),
        completed_case_calls=sum(len(case['results']) for case in results),
        completed_cases=len(results), failures=failures, cases=results,
    )
    save(directory / 'summary.json', summary)
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == '__main__':
    main()
