# Isolated draft/refine evaluation. Never changes production configuration.
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
from scripts.persona_evaluation_policy import enforce_models


def save(path, data):
    with path.open('x', encoding='utf-8') as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)


def decode_refinement(raw, output_format):
    if output_format == 'text':
        return raw
    if output_format != 'json_lines':
        raise ValueError('Unsupported refinement format')
    value = json.loads(raw)
    lines = value.get('reply_lines') if isinstance(value, dict) else None
    if not isinstance(lines, list) or not 1 <= len(lines) <= 50:
        raise ValueError('Refiner must return reply_lines')
    if any(not isinstance(line, str) or not line.strip() or chr(10) in line or chr(13) in line for line in lines):
        raise ValueError('Each reply line must be a nonempty single line')
    reply = chr(10).join(lines)
    if len(reply) > 8000:
        raise ValueError('Refined reply too long')
    return reply


def main():
    directory = Path(sys.argv[1]).resolve()
    credentials = json.load(sys.stdin)
    if credentials['base_url'].rstrip('/') != 'https://api.deepseek.com':
        raise ValueError('Unexpected evaluation endpoint')
    if (directory / 'summary.json').exists() or list(directory.glob('*.request.json')):
        raise ValueError('Never overwrite an attempted evaluation')
    method = json.loads((directory / 'method.json').read_text(encoding='utf-8'))
    enforce_models(directory, {'generator':credentials['model'],
                              'refiner':method.get('refiner_model',credentials['model'])})
    output_format = method.get('refiner_format', 'text')
    if output_format not in ('text', 'json_lines'):
        raise ValueError('Unsupported refinement format')
    refine_parameters = method.get('refiner_parameters', {})
    if set(refine_parameters) - {'thinking', 'reasoning_effort', 'max_tokens'}:
        raise ValueError('Unsupported refiner parameter')
    if refine_parameters.get('thinking', {'type': 'disabled'}) not in ({'type': 'disabled'}, {'type': 'enabled'}):
        raise ValueError('Unsupported thinking mode')
    if refine_parameters.get('reasoning_effort', 'low') not in ('low', 'high', 'max'):
        raise ValueError('Unsupported reasoning effort')
    if not 1200 <= refine_parameters.get('max_tokens', 1200) <= 4096:
        raise ValueError('Unsupported refiner token budget')
    for relative, expected in method['code_sha256'].items():
        if sha256((ROOT / relative).read_bytes()).hexdigest() != expected:
            raise ValueError('Source changed after snapshot: ' + relative)
    if sha256(Path(__file__).read_bytes()).hexdigest() != method['refiner_runner_sha256']:
        raise ValueError('Refinement evaluator changed after snapshot')
    soul = (directory / 'persona-used.md').read_text(encoding='utf-8')
    contract = (directory / 'contract-used.txt').read_text(encoding='utf-8')
    repair = (directory / 'refiner-used.txt').read_text(encoding='utf-8')
    for text, field in [(soul, 'role_sha256'), (contract, 'contract_sha256'),
                        (repair, 'refiner_sha256')]:
        if sha256(text.encode()).hexdigest() != method[field]:
            raise ValueError('Frozen prompt hash mismatch: ' + field)
    chat.FACTUAL_REPLY_RULES = contract
    cases = json.loads((directory / 'cases.json').read_text(encoding='utf-8'))
    ids = [case['id'] for case in cases]
    if len(ids) != len(set(ids)) or not all(re.fullmatch('[A-Z][0-9]{2}', x) for x in ids):
        raise ValueError('Invalid case IDs')
    corpus = app.load_chat_files(list(app.REFERENCE_CHAT_PATHS))
    reviewed = app.prepare_retrieval_messages(corpus)
    if not reviewed or not all(row.review_status == 'approved' for row in reviewed):
        raise ValueError('Unreviewed evidence')
    save(directory / 'retrieval-inventory.json', dict(
        loaded_rows=len(corpus), approved_rows=len(reviewed),
        rows=[dict(id=row.message_id, speaker=row.speaker) for row in reviewed]))
    state = threading.local()
    original_urlopen = chat.urlopen

    def audited_urlopen(request, timeout=90):
        body = json.loads(request.data.decode('utf-8'))
        if state.phase == 'refine' and refine_parameters:
            body.update(refine_parameters)
        if state.phase == 'refine' and output_format == 'json_lines':
            body['response_format'] = dict(type='json_object')
        request.data = json.dumps(body, ensure_ascii=False).encode('utf-8')
        save(state.stem.with_suffix('.request.json'), dict(
            endpoint=request.full_url, body=body,
            phase=state.phase, matched_ids=state.matched_ids))
        with original_urlopen(request, timeout=timeout) as response:
            raw = response.read(2_000_001)
        data = json.loads(raw.decode('utf-8'))
        choice = data.get('choices', [{}])[0]
        state.api = dict(api_response_id=data.get('id'), model=data.get('model'),
                         finish_reason=choice.get('finish_reason'), usage=data.get('usage'))
        return io.BytesIO(raw)

    chat.urlopen = audited_urlopen

    def call_phase(stem, phase, messages, matched_ids):
        state.stem, state.phase = stem, phase
        state.matched_ids, state.api = matched_ids, {}
        started = time.monotonic()
        model = method.get('refiner_model', credentials['model']) if phase == 'refine' else credentials['model']
        reply = chat.call_reply(credentials['base_url'], model,
                                credentials['api_key'], messages)
        record = dict(reply=reply, elapsed_seconds=round(time.monotonic()-started, 3),
                      phase=phase, **state.api)
        save(stem.with_suffix('.response.json'), record)
        with stem.with_suffix('.txt').open('x', encoding='utf-8') as handle:
            handle.write(reply)
        return record

    def run_case(case):
        retrieval = bool(case.get('use_retrieval', False))
        session = chat.TrialSession(soul, reviewed if retrieval else [], app.redact_sensitive)
        history = case.get('history', [])
        if len(history) % 2:
            raise ValueError('History must contain complete pairs')
        for i in range(0, len(history), 2):
            if [history[i]['role'], history[i+1]['role']] != ['user', 'assistant']:
                raise ValueError('Invalid history roles')
            session.turns.append((history[i]['content'], history[i+1]['content']))
        result = dict(case, results=[], failures=[])
        for index, user in enumerate(case['turns'], 1):
            started = time.monotonic()
            stem = directory / (case['id'] + '-' + str(index))
            try:
                turn = session.prepare(user, use_retrieval=retrieval)
                draft = call_phase(stem.with_name(stem.name+'-draft'), 'draft',
                                   turn.request_messages, turn.matched_ids)
                repair_input = dict(
                    dialogue=[m for m in turn.request_messages if m['role'] != 'system'],
                    draft=draft['reply'])
                if output_format == 'json_lines':
                    repair_input = dict(recent_history=list(turn.request_messages[1:-1]),
                        latest_user=turn.request_messages[-1]['content'], draft=draft['reply'],
                        scope='Only the supplied recent dialogue is available; earlier history is unknown.')
                repair_messages = [dict(role='system', content=repair), dict(
                    role='user', content=json.dumps(repair_input, ensure_ascii=False))]
                final = call_phase(stem, 'refine', repair_messages, ())
                record = dict(turn=index, user=user, **final)
                record['reply'] = decode_refinement(final['reply'], output_format)
                record['raw_refiner_output'] = final['reply']
                record.update(draft=draft['reply'], draft_api=draft,
                              matched_ids=turn.matched_ids,
                              elapsed_seconds=round(time.monotonic()-started, 3),
                              refined=(record['reply'] != draft['reply']))
                save(stem.with_name(stem.name+'-final').with_suffix('.json'), record)
                result['results'].append(record)
                session.complete(turn, record['reply'])
            except Exception as exc:
                error = dict(id=case['id'], turn=index, phase=getattr(state, 'phase', None),
                             error=str(exc).replace(credentials['api_key'], '[REDACTED]'))
                save(stem.with_suffix('.error.json'), error)
                result['failures'].append(error)
                break
        save(directory / (case['id']+'.json'), result)
        print(json.dumps(dict(case_complete=case['id'], replies=len(result['results']),
                              failures=len(result['failures'])), ensure_ascii=False), flush=True)
        return result

    started, completed = time.monotonic(), {}
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = [pool.submit(run_case, case) for case in cases]
        for future in as_completed(futures):
            result = future.result()
            completed[result['id']] = result
    ordered = [completed[ident] for ident in ids]
    summary = dict(method='Actual TrialSession generation then isolated refinement; no WeChat sends',
                   role_sha256=method['role_sha256'], expected_calls=sum(len(c['turns']) for c in cases),
                   actual_replies=sum(len(c['results']) for c in ordered),
                   actual_api_responses=len(list(directory.glob('*.response.json'))),
                   completed_cases=sum(not c['failures'] for c in ordered), total_cases=len(cases),
                   wall_seconds=round(time.monotonic()-started, 3),
                   failures=[f for c in ordered for f in c['failures']], cases=ordered)
    save(directory / 'summary.json', summary)
    print(json.dumps({k:v for k,v in summary.items() if k != 'cases'}, ensure_ascii=False))


if __name__ == '__main__':
    main()
