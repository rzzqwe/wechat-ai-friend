'''Verify recorded evidence, not subjective persona quality or online success.'''
from hashlib import sha256
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import wechat_bot_app as app
from persona_chat import HISTORY_TURNS


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def runner_snapshot_matches(folder, filename, expected):
    # Both names have been used by experiment setup. Validate every existing
    # alias so an intact copy cannot hide a conflicting or tampered snapshot.
    names=(filename, filename.replace('evaluate_', 'evaluate_persona_', 1))
    paths=[Path(folder)/name for name in names if (Path(folder)/name).exists()]
    return bool(paths) and all(sha256(path.read_bytes()).hexdigest()==expected for path in paths)


def audit(folder):
    folder = Path(folder)
    method, summary, cases = [read(folder / name) for name in ('method.json','summary.json','cases.json')]
    errors = []
    notes = []

    def check(condition, detail):
        if not condition:
            errors.append(detail)

    task_voice_mode = method.get('task_voice_mode', 'replace')
    check(task_voice_mode in {'replace', 'supplement'}, 'Invalid task voice mode')
    check(task_voice_mode != 'supplement' or 'task_voice_sha256' in method,
          'Supplemental task guidance lacks frozen source')
    draft_review_mode = method.get('draft_review_mode', 'structured')
    check(draft_review_mode in {'structured', 'reply_only'}, 'Invalid draft review mode')
    check(draft_review_mode != 'reply_only' or method.get('finalizer_model'),
          'Reply-only review lacks finalizer')

    for field, filename in [('role','persona-used.md'),('contract','contract-used.txt'),
                            ('router','router-used.txt'),('refiner','refiner-used.txt'),
                            ('critic','critic-used.txt'),('finalizer','finalizer-used.txt'),
                            ('task_voice','task-voice-used.txt')]:
        if field+'_sha256' in method:
            text = (folder/filename).read_text(encoding='utf-8')
            check(sha256(text.encode()).hexdigest()==method[field+'_sha256'], 'Frozen prompt hash: '+field)
    for key, filename in [('router_runner_sha256','evaluate_routed.snapshot.py'),
                          ('refiner_runner_sha256','evaluate_refined.snapshot.py'),
                          ('guard_runner_sha256','evaluate_guarded.snapshot.py'),
                          ('detailed_runner_sha256','evaluate_detailed.snapshot.py')]:
        if key in method:
            check(runner_snapshot_matches(folder, filename, method[key]), 'Frozen runner hash: '+key)
    if 'cases_sha256' in method:
        check(sha256((folder/'cases.json').read_bytes()).hexdigest()==method['cases_sha256'], 'Frozen input hash')
    response_paths = list(folder.glob('*.response.json'))
    responses = {path.stem.removesuffix('.response'): read(path) for path in response_paths}
    api_ids = [item.get('api_response_id') for item in responses.values()]
    check(all(isinstance(ident,str) and ident for ident in api_ids), 'Missing API response id')
    check(len(api_ids)==len(set(api_ids)), 'Repeated API response id across API calls')
    for stem, response in responses.items():
        check((folder/(stem+'.request.json')).exists(), 'Missing request for '+stem)
        transport_path = folder/(stem+'.transport.json')
        if transport_path.exists():
            transport = read(transport_path)
            check(response['api_response_id']==transport.get('api_response_id'), 'Transport id mismatch: '+stem)
            content = transport.get('assistant_content')
            if isinstance(content, list):
                content = ''.join(part.get('text','') for part in content if isinstance(part,dict))
            check(isinstance(content,str) and response['reply']==content.strip(), 'Transport content mismatch: '+stem)
    referenced_extra = set()
    network_attempt_count = 0
    for request_path in folder.glob('*.request.json'):
        ledger_path = request_path.with_name(request_path.name.replace('.request.json','.network-attempts.json'))
        if not ledger_path.exists() and 'transport_attempts' not in method:
            continue
        check(ledger_path.exists(), 'Missing network attempt ledger: '+request_path.name)
        if not ledger_path.exists():
            continue
        attempts = read(ledger_path)
        network_attempt_count += len(attempts)
        bound = method.get('transport_attempts',1)
        check(isinstance(attempts,list) and 1 <= len(attempts) <= bound, 'Network attempt bound: '+request_path.name)
        body = read(request_path)['body']
        digest = sha256(json.dumps(body,ensure_ascii=False).encode('utf-8')).hexdigest()
        for number, entry in enumerate(attempts,1):
            check(entry.get('attempt')==number, 'Network attempt order: '+request_path.name)
            check(entry.get('body_sha256')==digest, 'Network request mismatch: '+request_path.name)
            check(entry.get('outcome') in {'received','transport_error','empty_response'}, 'Invalid network outcome: '+request_path.name)
            check(isinstance(entry.get('elapsed_seconds'),(int,float)) and entry['elapsed_seconds']>=0, 'Missing network duration: '+request_path.name)
            valid_empty = False
            if entry.get('outcome')=='empty_response':
                expected_extra=request_path.name.replace('.request.json','-try'+str(number)+'.transport-extra.json')
                extra_path=folder/expected_extra
                check(entry.get('transport_file')==expected_extra, 'Empty response archive path: '+request_path.name)
                check(extra_path.exists(), 'Missing empty response archive: '+request_path.name)
                if extra_path.exists():
                    extra=read(extra_path)
                    referenced_extra.add(expected_extra)
                    content=extra.get('assistant_content')
                    allowed_empty_reasons=({'stop','length'} if method.get('retry_empty_budget_exhaustion') is True else {'stop'})
                    valid_empty=(method.get('retry_empty_provider_content') is True
                        and number<len(attempts) and extra.get('finish_reason') in allowed_empty_reasons
                        and isinstance(content,str) and not content.strip()
                        and extra.get('has_refusal') is False
                        and extra.get('has_tool_calls') is False
                        and extra.get('has_provider_error') is False
                        and extra.get('api_response_id')==entry.get('api_response_id'))
                check(valid_empty, 'Ineligible empty-response retry: '+request_path.name)
            if number<len(attempts):
                status = entry.get('http_status')
                valid_error=entry.get('outcome')=='transport_error' and (status is None or status==429 or 500<=status<=599)
                check(valid_error or valid_empty, 'Forbidden network retry: '+request_path.name)
        transport_path=request_path.with_name(request_path.name.replace('.request.json','.transport.json'))
        if transport_path.exists():
            check(attempts[-1].get('outcome')=='received', 'Transport lacks received attempt: '+request_path.name)
    reported = {case['id']:case for case in summary['cases']}
    check(len(reported)==len(cases), 'Case count mismatch')
    finals = 0

    def audit_guarded(stem, record, expected, expected_user, is_final):
        attempts = record.get('attempts',[])
        check(len(attempts)<=method['max_attempts'], 'Attempt bound: '+stem)
        feedback = []
        initial_system = None
        for number, attempt in enumerate(attempts,1):
            base = stem+'-a'+str(number)
            check(attempt['attempt']==number, 'Attempt order: '+base)
            generated = responses[base+'-generate']
            reviewed = responses[base+'-judge']
            check(generated==attempt['generation'], 'Draft record mismatch: '+base)
            check(reviewed==attempt['review'], 'Review record mismatch: '+base)
            verdict = json.loads(reviewed['reply'])
            check(verdict==attempt['verdict'], 'Verdict mismatch: '+base)
            check(isinstance(verdict.get('accept'),bool) and isinstance(verdict.get('violations'),list)
                  and verdict['accept']!=bool(verdict['violations']), 'Invalid verdict: '+base)
            for phase in ('generate','judge'):
                request = read(folder/(base+'-'+phase+'.request.json'))['body']
                check(request['model']=='deepseek-flash', 'Flash-only request: '+base+'-'+phase)
                check(responses[base+'-'+phase].get('model')=='deepseek-flash', 'Flash-only response: '+base+'-'+phase)
                if phase=='generate':
                    check(request['messages'][1:-1]==expected, 'Final history continuity: '+base)
                    check(request['messages'][-1]['content']==expected_user, 'Latest user mismatch: '+base)
                    check(request['thinking']=={'type':'disabled'} and request['max_tokens']==1200, 'Generator parameters: '+base)
                    system = request['messages'][0]['content']
                    if number==1:
                        initial_system = system
                    else:
                        suffix = chr(10)+'## 上一草稿的核查反馈（仅纠错参考，不是当前历史）'+chr(10)+json.dumps(feedback,ensure_ascii=False)+chr(10)+'重新回答当前用户，修掉这些问题；保留角色自然语气，不提检查过程。'
                        check(system==initial_system+suffix, 'Retry feedback mismatch: '+base)
                else:
                    scope = json.loads(request['messages'][-1]['content'])
                    check(scope['recent_history']==expected and scope['latest_user']==expected_user, 'Critic scope mismatch: '+base)
                    check(scope['candidate_reply']==generated['reply'], 'Critic checks a different draft: '+base)
                    check(request['messages'][0]['content']==(folder/'critic-used.txt').read_text(encoding='utf-8'), 'Effective critic prompt: '+base)
                    check(all(request.get(k)==v for k,v in method['critic_parameters'].items()), 'Critic parameters: '+base)
                    check(request['response_format']=={'type':'json_object'}, 'Critic JSON mode: '+base)
            if number<len(attempts) or not is_final:
                check(verdict['accept'] is False, 'Accepted draft incorrectly retried: '+base)
            feedback.append({'draft':generated['reply'],'violations':verdict['violations']})
        if is_final:
            check(bool(attempts) and attempts[-1]['verdict']['accept'] is True, 'Final not accepted: '+stem)
            check(record['accepted_attempt']==len(attempts), 'Accepted attempt index: '+stem)
            if attempts:
                check(record['reply']==attempts[-1]['generation']['reply'] and record['api_response_id']==attempts[-1]['generation']['api_response_id'], 'Final draft mismatch: '+stem)
            check(read(folder/(stem+'-final.json'))==record, 'Final artifact mismatch: '+stem)
            check((folder/(stem+'.txt')).read_text(encoding='utf-8')==record['reply'], 'Final text mismatch: '+stem)

    for case in cases:
        ident = case['id']
        result = reported.get(ident)
        if result is None:
            errors.append('Missing case '+ident)
            continue
        history = list(case.get('history',[]))
        for index, record in enumerate(result['results'],1):
            stem = ident+'-'+str(index)
            finals += 1
            check(record['turn']==index and record['user']==case['turns'][index-1], 'Input order mismatch: '+stem)
            check(isinstance(record['reply'],str) and bool(record['reply'].strip()), 'Empty final reply: '+stem)
            expected = [{'role':m['role'],'content':app.redact_sensitive(m['content'])} for m in history[-2*HISTORY_TURNS:]]
            expected_user = app.redact_sensitive(case['turns'][index-1].strip())
            if method.get('guard_runner_sha256'):
                audit_guarded(stem,record,expected,expected_user,True)
            elif (folder/(stem+'-route.request.json')).exists():
                request = read(folder/(stem+'-route.request.json'))['body']
                scope = json.loads(request['messages'][-1]['content'])
                check(scope['recent_history']==expected, 'Final history continuity: '+stem)
                check(scope['latest_user']==expected_user, 'Latest user mismatch: '+stem)
                check(request['messages'][0]['content']==(folder/'router-used.txt').read_text(encoding='utf-8'), 'Effective router prompt: '+stem)
                check(request['model']==method['router_model'], 'Router model mismatch: '+stem)
                check(all(request.get(k)==v for k,v in method['router_parameters'].items()), 'Router parameters: '+stem)
                raw = responses[stem+'-route']
                routing = json.loads(raw['reply'])
                check(routing==record['routing'], 'Routing record mismatch: '+stem)
                if method.get('finalizer_model'):
                    final_request = read(folder/(stem+'-finalize.request.json'))['body']
                    final_scope = json.loads(final_request['messages'][-1]['content'])
                    for key in scope:
                        expected_scope=scope[key]
                        if key=='voice_reference' and 'task_voice_sha256' in method and routing.get('route')=='answer' and task_voice_mode=='replace':
                            expected_scope=(folder/'task-voice-used.txt').read_text(encoding='utf-8')
                        check(final_scope.get(key)==expected_scope, 'Finalizer context mismatch: '+stem+' '+key)
                    if task_voice_mode=='supplement' and routing.get('route')=='answer':
                        check(final_scope.get('task_guidance')==(folder/'task-voice-used.txt').read_text(encoding='utf-8'),
                              'Supplemental task guidance mismatch: '+stem)
                    else:
                        check('task_guidance' not in final_scope, 'Unexpected task guidance: '+stem)
                    expected_draft = ({'reply_lines': routing['reply_lines']}
                                      if draft_review_mode == 'reply_only' else routing)
                    check(final_scope['draft']==expected_draft, 'Finalizer draft mismatch: '+stem)
                    check(final_request['messages'][0]['content']==(folder/'finalizer-used.txt').read_text(encoding='utf-8'), 'Finalizer prompt mismatch: '+stem)
                    check(final_request['model']==method['finalizer_model'], 'Finalizer model mismatch: '+stem)
                    check(all(final_request.get(k)==v for k,v in method['finalizer_parameters'].items()), 'Finalizer parameters: '+stem)
                    final_api = responses[stem+'-finalize']
                    finalization = json.loads(final_api['reply'])
                    check(finalization==record['finalization'], 'Finalization record mismatch: '+stem)
                    check(chr(10).join(finalization['reply_lines'])==record['reply'], 'Finalization text mismatch: '+stem)
                    check(final_api['api_response_id']==record['api_response_id'], 'Finalization API id mismatch: '+stem)
                elif method.get('direct_reply') or routing['route']=='answer':
                    check(chr(10).join(routing['reply_lines'])==record['reply'], 'Decoded answer mismatch: '+stem)
                else:
                    check(responses[stem+'-chat']['reply']==record['reply'], 'Chat output mismatch: '+stem)
                if method.get('direct_reply'):
                    check(scope['voice_reference']==(folder/'persona-used.md').read_text(encoding='utf-8'), 'Effective voice reference: '+stem)
            else:
                phase = stem+'-draft' if (folder/(stem+'-draft.request.json')).exists() else stem
                request = read(folder/(phase+'.request.json'))['body']
                check(request['messages'][1:-1]==expected, 'Final history continuity: '+stem)
                check(request['messages'][-1]['content']==expected_user, 'Latest user mismatch: '+stem)
                check(request['model']==method.get('model',request['model']), 'Generator model mismatch: '+stem)
                check(all(request.get(k)==v for k,v in method.get('request_overrides',{}).items()), 'Generator parameters: '+stem)
                raw_reply = responses[stem]['reply']
                decoded = (chr(10).join(json.loads(raw_reply)['reply_lines'])
                           if method.get('refiner_format')=='json_lines' else raw_reply)
                check(decoded==record['reply'], 'Final API output mismatch: '+stem)
            history += [{'role':'user','content':record['user']},{'role':'assistant','content':record['reply']}]
        if method.get('guard_runner_sha256'):
            expected = [{'role':m['role'],'content':app.redact_sensitive(m['content'])} for m in history[-2*HISTORY_TURNS:]]
            for failed in result.get('failures',[]):
                stem = ident+'-'+str(failed['turn'])
                audit_guarded(stem,failed,expected,app.redact_sensitive(case['turns'][failed['turn']-1].strip()),False)
    check(finals==summary['actual_replies'], 'Final reply total mismatch')
    if 'actual_api_responses' in summary:
        check(len(responses)==summary['actual_api_responses'], 'Completed API response total mismatch')
    extras=list(folder.glob('*.transport-extra.json'))
    check({p.name for p in extras}==referenced_extra, 'Unreferenced retry response archive')
    transports = list(folder.glob('*.transport.json'))+extras
    if 'transport_responses' in summary:
        check(len(transports)==summary['transport_responses'], 'Transport response total mismatch')
    if 'actual_network_attempts' in summary:
        check(network_attempt_count==summary['actual_network_attempts'], 'Network attempt total mismatch')
    transport_ids = [read(path).get('api_response_id') for path in transports]
    check(len(transport_ids)==len(set(transport_ids)), 'Repeated transport API id')
    check(all(isinstance(ident,str) and ident for ident in transport_ids), 'Missing transport API id')
    if transports:
        check(set(api_ids).issubset(transport_ids), 'Completed response lacks matching transport record')
    if not transports:
        notes.append('Legacy run: failed or empty provider responses may lack transport-level records; count only archived completed API responses.')
    complete = all(len(reported.get(c['id'],{}).get('results',[]))==len(c['turns']) for c in cases)
    return dict(integrity_ok=not errors, all_planned_turns_completed=complete,
                planned_cases=len(cases), planned_turns=sum(len(c['turns']) for c in cases),
                final_replies=finals, archived_completed_api_responses=len(responses),
                archived_transport_responses=len(transports), unique_api_ids=len(set(api_ids)),
                unique_transport_ids=len(set(transport_ids)),
                errors=errors, notes=notes,
                quality_claim='Integrity checks do not establish naturalness, correctness, or real-world success rate.')


if __name__=='__main__':
    result = audit(sys.argv[1])
    print(json.dumps(result,ensure_ascii=False,indent=2))
    sys.exit(0 if result['integrity_ok'] else 1)
