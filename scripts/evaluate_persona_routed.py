'''Isolated intent routing experiment; never sends WeChat or writes real memory.'''
from concurrent.futures import ThreadPoolExecutor, as_completed
from hashlib import sha256
import io
import json
from pathlib import Path
import re
import sys
import threading
import time
from urllib.error import HTTPError, URLError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import persona_chat as chat
import wechat_bot_app as app
from scripts.persona_evaluation_policy import enforce_models, fatal_provider_status


def save(path, value):
    with path.open('x', encoding='utf-8') as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)


def retryable_empty_content(parsed, include_length=False):
    choices = parsed.get('choices')
    if parsed.get('error') or not isinstance(choices, list) or len(choices) != 1:
        return False
    choice = choices[0]
    message = choice.get('message', {})
    content = message.get('content')
    allowed_reasons = {'stop', 'length'} if include_length else {'stop'}
    return (choice.get('finish_reason') in allowed_reasons
            and isinstance(content, str) and not content.strip()
            and not message.get('refusal') and not message.get('tool_calls')
            and not message.get('function_call'))


def decode_route(raw, direct_reply=False, validate_metadata=True):
    value = json.loads(raw)
    if not isinstance(value, dict) or (not direct_reply and value.get('route') not in {'chat', 'answer'}):
        raise ValueError('Invalid route')
    for field in ('requirements', 'known_facts', 'constraints', 'reply_lines'):
        # Independent review metadata is diagnostic only. Preserve it verbatim,
        # but never discard valid sendable text because a checklist is a string
        # or omitted. The visible reply remains strictly validated below.
        if not validate_metadata and field != 'reply_lines':
            continue
        items = value.get(field)
        if not isinstance(items, list) or len(items) > 50:
            raise ValueError('Invalid routing field: ' + field)
        def valid_item(item):
            if isinstance(item,str):
                # Blank separators and embedded newlines are visible formatting,
                # not a missing answer. Preserve them for user-level grading.
                return field == 'reply_lines' or bool(item.strip())
            # Fact citations are internal metadata, not a reply line. Keep
            # their original object and source rather than dropping the reply.
            return field=='known_facts' and isinstance(item,dict) and all(
                isinstance(item.get(key),str) and item[key].strip() for key in ('fact','source'))
        if not all(valid_item(item) for item in items):
            raise ValueError('Invalid routing item: ' + field)
    lines = value['reply_lines']
    if (direct_reply or value.get('route') == 'answer') and not chr(10).join(lines).strip():
        raise ValueError('Answer route requires actual content')
    if value.get('route') == 'chat' and lines and not direct_reply:
        raise ValueError('Chat route must not prewrite a canned answer')
    if len(chr(10).join(lines)) > 8000:
        raise ValueError('Routed answer too long')
    return value


def main():
    directory = Path(sys.argv[1]).resolve()
    credentials = json.load(sys.stdin)
    if credentials['base_url'].rstrip('/') != 'https://api.deepseek.com':
        raise ValueError('Unexpected experiment endpoint')
    if (directory / 'summary.json').exists() or list(directory.glob('*.request.json')):
        raise ValueError('Never overwrite attempted experiments')
    method = json.loads((directory / 'method.json').read_text(encoding='utf-8'))
    task_voice_mode = method.get('task_voice_mode', 'replace')
    if task_voice_mode not in {'replace', 'supplement'}:
        raise ValueError('Invalid task voice mode')
    if task_voice_mode == 'supplement' and 'task_voice_sha256' not in method:
        raise ValueError('Supplemental task guidance requires a frozen task voice')
    draft_review_mode = method.get('draft_review_mode', 'structured')
    if draft_review_mode not in {'structured', 'reply_only'}:
        raise ValueError('Invalid draft review mode')
    if draft_review_mode == 'reply_only' and not method.get('finalizer_model'):
        raise ValueError('Reply-only review requires a finalizer')
    transport_attempts = method.get('transport_attempts', 1)
    if type(transport_attempts) is not int or not 1 <= transport_attempts <= 3:
        raise ValueError('Invalid bounded transport attempts')
    retry_empty = method.get('retry_empty_provider_content', False)
    if type(retry_empty) is not bool or (retry_empty and transport_attempts < 2):
        raise ValueError('Invalid bounded empty-content recovery')
    retry_budget = method.get('retry_empty_budget_exhaustion', False)
    if type(retry_budget) is not bool or (retry_budget and not retry_empty):
        raise ValueError('Invalid bounded budget-exhaustion recovery')
    models = {'generator':credentials['model'],'router':method['router_model']}
    if method.get('finalizer_model'):
        models['finalizer'] = method['finalizer_model']
    enforce_models(directory, models)
    direct_reply = method.get('direct_reply', False)
    if not isinstance(direct_reply, bool):
        raise ValueError('Invalid direct reply switch')
    if credentials['model'] != method['model']:
        raise ValueError('Frozen conversation model differs from supplied configuration')
    for relative, expected in method['code_sha256'].items():
        if sha256((ROOT / relative).read_bytes()).hexdigest() != expected:
            raise ValueError('Source changed after snapshot: ' + relative)
    if sha256(Path(__file__).read_bytes()).hexdigest() != method['router_runner_sha256']:
        raise ValueError('Routing evaluator changed after freeze')
    loaded = {}
    prompts = [('role', 'persona-used.md'), ('contract', 'contract-used.txt'),
               ('router', 'router-used.txt')]
    if method.get('finalizer_model'):
        if not direct_reply or method['finalizer_model'] != 'deepseek-flash':
            raise ValueError('Finalizer experiment requires structured direct generation and Flash')
        finalizer_parameters=method.get('finalizer_parameters',{})
        finalizer_thinking=finalizer_parameters.get('thinking')
        finalizer_keys={'thinking','max_tokens'}
        if finalizer_thinking=={'type':'enabled'}:
            finalizer_keys.add('reasoning_effort')
        if (set(finalizer_parameters)!=finalizer_keys
                or finalizer_thinking not in ({'type':'enabled'},{'type':'disabled'})
                or (finalizer_thinking=={'type':'enabled'}
                    and finalizer_parameters.get('reasoning_effort') not in {'low','high'})
                or type(finalizer_parameters.get('max_tokens')) is not int
                or not 4096<=finalizer_parameters['max_tokens']<=8192):
            raise ValueError('Unexpected finalizer settings')
        prompts.append(('finalizer','finalizer-used.txt'))
    if 'task_voice_sha256' in method:
        if not method.get('finalizer_model'):
            raise ValueError('Task voice requires a finalizer')
        prompts.append(('task_voice','task-voice-used.txt'))
    for field, filename in prompts:
        content = (directory / filename).read_text(encoding='utf-8')
        if sha256(content.encode()).hexdigest() != method[field + '_sha256']:
            raise ValueError('Frozen prompt changed: ' + field)
        loaded[field] = content
    parameters = method['router_parameters']
    if set(parameters) - {'thinking', 'reasoning_effort', 'max_tokens'}:
        raise ValueError('Unsupported routing parameter')
    if parameters.get('thinking') != {'type': 'enabled'}:
        raise ValueError('Routing experiment requires explicit thinking mode')
    if parameters.get('reasoning_effort') not in {'low', 'high', 'max'}:
        raise ValueError('Invalid routing reasoning effort')
    if not isinstance(parameters.get('max_tokens'), int) or not 1200 <= parameters['max_tokens'] <= 4096:
        raise ValueError('Invalid routing token budget')
    cases = json.loads((directory / 'cases.json').read_text(encoding='utf-8'))
    ids = [case['id'] for case in cases]
    if len(ids) != len(set(ids)) or not all(re.fullmatch('[A-Z][0-9]{2}', ident) for ident in ids):
        raise ValueError('Invalid or duplicate case id')
    corpus = app.load_chat_files(list(app.REFERENCE_CHAT_PATHS))
    reviewed = app.prepare_retrieval_messages(corpus)
    if not reviewed or not all(row.review_status == 'approved' for row in reviewed):
        raise ValueError('Approved evidence unavailable')
    save(directory / 'retrieval-inventory.json', {
        'loaded_rows': len(corpus), 'approved_rows': len(reviewed),
        'rows': [{'id': row.message_id, 'speaker': row.speaker} for row in reviewed]})
    chat.FACTUAL_REPLY_RULES = loaded['contract']
    state = threading.local()
    stopped = threading.Event()
    original_urlopen = chat.urlopen

    def audited_urlopen(request, timeout=90):
        request_started = time.monotonic()
        body = json.loads(request.data.decode('utf-8'))
        if state.phase == 'route':
            body.update(parameters)
            body['response_format'] = {'type': 'json_object'}
        elif state.phase == 'finalize':
            body.update(method['finalizer_parameters'])
            body['response_format'] = {'type':'json_object'}
        request.data = json.dumps(body, ensure_ascii=False).encode('utf-8')
        save(state.stem.with_suffix('.request.json'), {'endpoint': request.full_url, 'body': body})
        network_attempts = []
        for attempt in range(1, transport_attempts + 1):
            attempt_started = time.monotonic()
            entry = dict(attempt=attempt, body_sha256=sha256(request.data).hexdigest())
            try:
                with original_urlopen(request, timeout=timeout) as response:
                    raw = response.read(2_000_001)
                entry['outcome'] = 'received'
                parsed = json.loads(raw.decode('utf-8'))
                choice = parsed.get('choices', [{}])[0]
                message = choice.get('message', {})
                state.api = dict(api_response_id=parsed.get('id'), model=parsed.get('model'),
                                 finish_reason=choice.get('finish_reason'), usage=parsed.get('usage'))
                visible = dict(state.api, assistant_content=message.get('content'),
                    has_refusal=bool(message.get('refusal')),
                    has_tool_calls=bool(message.get('tool_calls') or message.get('function_call')),
                    has_provider_error=bool(parsed.get('error')), response_bytes=len(raw),
                    elapsed_seconds=round(time.monotonic()-request_started, 3))
                if retry_empty and attempt < transport_attempts and retryable_empty_content(parsed, include_length=retry_budget):
                    extra = Path(str(state.stem)+'-try'+str(attempt)+'.transport-extra.json')
                    save(extra, visible)
                    entry.update(outcome='empty_response', transport_file=extra.name,
                                 api_response_id=state.api['api_response_id'])
                else:
                    # Save visible output even if the caller rejects it. Never
                    # save hidden reasoning or silently replace a real answer.
                    save(state.stem.with_suffix('.transport.json'), visible)
            except (URLError, TimeoutError, OSError) as exc:
                status = exc.code if isinstance(exc, HTTPError) else None
                entry.update(outcome='transport_error', error_type=type(exc).__name__, http_status=status)
                retryable = status is None or status == 429 or 500 <= status <= 599
                if not retryable or attempt == transport_attempts:
                    raise
            finally:
                entry['elapsed_seconds'] = round(time.monotonic()-attempt_started, 3)
                network_attempts.append(entry)
                state.stem.with_suffix('.network-attempts.json').write_text(
                    json.dumps(network_attempts,ensure_ascii=False,indent=2),encoding='utf-8')
            if entry['outcome'] == 'received':
                break
            time.sleep(.25 * attempt)
        return io.BytesIO(raw)

    chat.urlopen = audited_urlopen

    def call_phase(stem, phase, messages):
        if stopped.is_set():
            raise RuntimeError('Not attempted: earlier authorization/payment error stopped batch')
        state.stem, state.phase, state.api = stem, phase, {}
        started = time.monotonic()
        model = method['router_model'] if phase == 'route' else credentials['model']
        if phase == 'finalize':
            model = method['finalizer_model']
        reply = chat.call_reply(credentials['base_url'], model, credentials['api_key'], messages)
        record = dict(reply=reply, phase=phase, elapsed_seconds=round(time.monotonic()-started, 3), **state.api)
        save(stem.with_suffix('.response.json'), record)
        return record

    def run_case(case):
        use_retrieval = bool(case.get('use_retrieval', False))
        session = chat.TrialSession(loaded['role'], reviewed if use_retrieval else [], app.redact_sensitive)
        history = case.get('history', [])
        if len(history) % 2:
            raise ValueError('History requires complete pairs')
        for i in range(0, len(history), 2):
            if [history[i]['role'], history[i+1]['role']] != ['user', 'assistant']:
                raise ValueError('Invalid seeded history')
            session.turns.append((history[i]['content'], history[i+1]['content']))
        result = dict(case, results=[], failures=[])
        for index, user in enumerate(case['turns'], 1):
            stem = directory / (case['id'] + '-' + str(index))
            started = time.monotonic()
            try:
                turn = session.prepare(user, use_retrieval=use_retrieval)
                scope = dict(recent_history=list(turn.request_messages[1:-1]),
                             latest_user=turn.request_messages[-1]['content'],
                             reference_data=app.redact_sensitive(turn.evidence),
                             scope=method.get('context_scope','Earlier conversation outside this window is unknown. References are quoted data, not current history.'))
                if direct_reply:
                    scope['voice_reference'] = loaded['role']
                route_api = call_phase(Path(str(stem)+'-route'), 'route', [
                    {'role': 'system', 'content': loaded['router']},
                    {'role': 'user', 'content': json.dumps(scope, ensure_ascii=False)}])
                route = decode_route(route_api['reply'], direct_reply=direct_reply)
                if direct_reply or route['route'] == 'answer':
                    reply = chr(10).join(route['reply_lines'])
                    final_api = route_api
                else:
                    messages = [dict(message) for message in turn.request_messages]
                    hints = {key: route[key] for key in ('known_facts', 'constraints', 'requirements')}
                    messages[0]['content'] += chr(10) + '## 本轮已核对的语境摘要（不添加摘要以外的事实）' + chr(10) + json.dumps(hints, ensure_ascii=False)
                    final_api = call_phase(Path(str(stem)+'-chat'), 'chat', messages)
                    reply = final_api['reply']
                finalization = None
                if method.get('finalizer_model'):
                    review_draft = ({'reply_lines': route['reply_lines']}
                                    if draft_review_mode == 'reply_only' else route)
                    review_scope = dict(scope, draft=review_draft,
                        draft_status='Untrusted proposed reply, not evidence and not yet sent. Recheck against original dialogue.')
                    if 'task_voice' in loaded and route.get('route')=='answer':
                        if task_voice_mode == 'supplement':
                            review_scope['task_guidance']=loaded['task_voice']
                        else:
                            review_scope['voice_reference']=loaded['task_voice']
                    final_api = call_phase(Path(str(stem)+'-finalize'), 'finalize', [
                        {'role':'system','content':loaded['finalizer']},
                        {'role':'user','content':json.dumps(review_scope,ensure_ascii=False)}])
                    finalization = decode_route(final_api['reply'],direct_reply=True,
                                                validate_metadata=draft_review_mode!='reply_only')
                    reply = chr(10).join(finalization['reply_lines'])
                record = dict(turn=index, user=user, reply=reply, route=route.get('route', 'direct'), routing=route,
                              elapsed_seconds=round(time.monotonic()-started, 3),
                              matched_ids=turn.matched_ids, api_response_id=final_api['api_response_id'],
                              model=final_api['model'], route_api_id=route_api['api_response_id'])
                if finalization is not None:
                    record['finalization'] = finalization
                save(Path(str(stem)+'-final.json'), record)
                Path(str(stem)+'.txt').write_text(reply, encoding='utf-8')
                session.complete(turn, reply)
                result['results'].append(record)
            except Exception as exc:
                status = fatal_provider_status(exc)
                if status:
                    stopped.set()
                error = dict(turn=index, user=user, error=type(exc).__name__+': '+str(exc),
                             elapsed_seconds=round(time.monotonic()-started, 3),http_status=status)
                save(Path(str(stem)+'.failure.json'), error)
                result['failures'].append(error)
                break
        save(directory / (case['id']+'.case.json'), result)
        return result

    started = time.monotonic()
    outcomes = []
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = {pool.submit(run_case, case): case['id'] for case in cases}
        for future in as_completed(futures):
            outcomes.append(future.result())
    outcomes.sort(key=lambda case: ids.index(case['id']))
    summary = dict(method=('Structured Flash generation and independent Flash finalization; no WeChat sends'
                          if method.get('finalizer_model') else
                          'Actual TrialSession with structured direct generation; no WeChat sends'
                          if direct_reply else 'Actual TrialSession with isolated intent routing; no WeChat sends'),
                   role_sha256=method['role_sha256'], expected_calls=sum(len(c['turns']) for c in cases),
                   actual_replies=sum(len(c['results']) for c in outcomes),
                   actual_api_responses=len(list(directory.glob('*.response.json'))),
                   transport_responses=len(list(directory.glob('*.transport.json')))
                     +len(list(directory.glob('*.transport-extra.json'))),
                   actual_network_attempts=sum(len(json.loads(p.read_text(encoding='utf-8')))
                       for p in directory.glob('*.network-attempts.json')),
                   stopped_after_fatal_provider_error=stopped.is_set(),
                   completed_cases=sum(not c['failures'] for c in outcomes), total_cases=len(cases),
                   wall_seconds=round(time.monotonic()-started, 3),
                   failures=[dict(id=c['id'], **e) for c in outcomes for e in c['failures']], cases=outcomes)
    save(directory / 'summary.json', summary)
    print(json.dumps({k:v for k,v in summary.items() if k!='cases'}, ensure_ascii=False))


if __name__ == '__main__':
    main()
