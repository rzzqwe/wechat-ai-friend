'''Flash generation with an independent Flash constraint check; experimental only.'''
from concurrent.futures import ThreadPoolExecutor, as_completed
from hashlib import sha256
import io
import json
from pathlib import Path
import re
import sys
import threading
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import persona_chat as chat
import wechat_bot_app as app
from scripts.persona_evaluation_policy import enforce_models, fatal_provider_status


def save(path,value):
    with path.open('x',encoding='utf-8') as handle:
        json.dump(value,handle,ensure_ascii=False,indent=2)


def decode_verdict(raw):
    result=json.loads(raw)
    if not isinstance(result,dict) or not isinstance(result.get('accept'),bool) or not isinstance(result.get('violations'),list):
        raise ValueError('Invalid critic output')
    if result['accept']==bool(result['violations']):
        raise ValueError('Critic result contradicts violations')
    if len(result['violations'])>20:
        raise ValueError('Too many critic findings')
    for finding in result['violations']:
        if not isinstance(finding,dict) or not all(isinstance(finding.get(k),str) and finding[k].strip() for k in ('type','quote','evidence','reason')):
            raise ValueError('Critic finding lacks supporting evidence')
    return result


def main():
    directory=Path(sys.argv[1]).resolve()
    credentials=json.load(sys.stdin)
    if credentials['base_url'].rstrip('/')!='https://api.deepseek.com':
        raise ValueError('Unexpected endpoint')
    if (directory/'summary.json').exists() or list(directory.glob('*.request.json')):
        raise ValueError('Never overwrite an attempted experiment')
    method=json.loads((directory/'method.json').read_text(encoding='utf-8'))
    enforce_models(directory,{'generator':credentials['model'],'critic':method['critic_model']})
    if credentials['model']!='deepseek-flash' or method['critic_model']!='deepseek-flash':
        raise ValueError('This candidate uses Flash exclusively')
    if method['max_attempts'] not in {1,2,3}:
        raise ValueError('Invalid attempt bound')
    for name,expected in method['code_sha256'].items():
        if sha256((ROOT/name).read_bytes()).hexdigest()!=expected:
            raise ValueError('Frozen source changed: '+name)
    if sha256(Path(__file__).read_bytes()).hexdigest()!=method['guard_runner_sha256']:
        raise ValueError('Guarded evaluator changed after freeze')
    loaded={}
    for name,filename in [('role','persona-used.md'),('contract','contract-used.txt'),('critic','critic-used.txt')]:
        text=(directory/filename).read_text(encoding='utf-8')
        if sha256(text.encode()).hexdigest()!=method[name+'_sha256']:
            raise ValueError('Frozen prompt changed: '+name)
        loaded[name]=text
    critic_params=method['critic_parameters']
    if critic_params!={'thinking':{'type':'enabled'},'reasoning_effort':'high','max_tokens':4096}:
        raise ValueError('Unexpected frozen critic settings')
    cases=json.loads((directory/'cases.json').read_text(encoding='utf-8'))
    ids=[case['id'] for case in cases]
    if len(ids)!=len(set(ids)) or not all(re.fullmatch('[A-Z][0-9]{2}',key) for key in ids):
        raise ValueError('Invalid case identifiers')
    corpus=app.load_chat_files(list(app.REFERENCE_CHAT_PATHS))
    reviewed=app.prepare_retrieval_messages(corpus)
    if not reviewed or not all(row.review_status=='approved' for row in reviewed):
        raise ValueError('Approved evidence unavailable')
    save(directory/'retrieval-inventory.json',{'loaded_rows':len(corpus),'approved_rows':len(reviewed),
         'rows':[{'id':row.message_id,'speaker':row.speaker} for row in reviewed]})
    chat.FACTUAL_REPLY_RULES=loaded['contract']
    state=threading.local()
    stopped=threading.Event()
    original_urlopen=chat.urlopen

    def transport(request,timeout=90):
        started=time.monotonic()
        body=json.loads(request.data.decode())
        if state.phase=='judge':
            body.update(critic_params)
            body['response_format']={'type':'json_object'}
        request.data=json.dumps(body,ensure_ascii=False).encode()
        save(state.stem.with_suffix('.request.json'),{'endpoint':request.full_url,'body':body})
        with original_urlopen(request,timeout=timeout) as response:
            raw=response.read(2_000_001)
        parsed=json.loads(raw.decode())
        choice=parsed.get('choices',[{}])[0]
        state.metadata=dict(api_response_id=parsed.get('id'),model=parsed.get('model'),
                            finish_reason=choice.get('finish_reason'),usage=parsed.get('usage'))
        save(state.stem.with_suffix('.transport.json'),dict(state.metadata,
             assistant_content=choice.get('message',{}).get('content'),response_bytes=len(raw),
             elapsed_seconds=round(time.monotonic()-started,3)))
        return io.BytesIO(raw)

    chat.urlopen=transport

    def call(stem,phase,messages):
        state.stem,state.phase,state.metadata=stem,phase,{}
        started=time.monotonic()
        reply=chat.call_reply(credentials['base_url'],'deepseek-flash',credentials['api_key'],messages)
        record=dict(reply=reply,phase=phase,elapsed_seconds=round(time.monotonic()-started,3),**state.metadata)
        save(stem.with_suffix('.response.json'),record)
        return record

    def run_case(case):
        retrieval=bool(case.get('use_retrieval',False))
        session=chat.TrialSession(loaded['role'],reviewed if retrieval else [],app.redact_sensitive)
        history=case.get('history',[])
        if len(history)%2:
            raise ValueError('Incomplete seeded history')
        for i in range(0,len(history),2):
            if [history[i]['role'],history[i+1]['role']]!=['user','assistant']:
                raise ValueError('Invalid seeded roles')
            session.turns.append((history[i]['content'],history[i+1]['content']))
        result=dict(case,results=[],failures=[])
        for index,user in enumerate(case['turns'],1):
            stem=directory/(case['id']+'-'+str(index))
            started=time.monotonic()
            attempts=[]
            try:
                if stopped.is_set():
                    raise RuntimeError('Not attempted: earlier authorization/payment error stopped batch')
                turn=session.prepare(user,use_retrieval=retrieval)
                feedback=[]
                accepted=None
                for attempt in range(1,method['max_attempts']+1):
                    if stopped.is_set():
                        raise RuntimeError('Not attempted: earlier authorization/payment error stopped batch')
                    messages=[dict(m) for m in turn.request_messages]
                    if feedback:
                        messages[0]['content']+=chr(10)+'## 上一草稿的核查反馈（仅纠错参考，不是当前历史）'+chr(10)+json.dumps(feedback,ensure_ascii=False)+chr(10)+'重新回答当前用户，修掉这些问题；保留角色自然语气，不提检查过程。'
                    base=str(stem)+'-a'+str(attempt)
                    generation=call(Path(base+'-generate'),'generate',messages)
                    critic_input=dict(recent_history=list(turn.request_messages[1:-1]),
                         latest_user=turn.request_messages[-1]['content'],candidate_reply=generation['reply'],
                         reference_data=app.redact_sensitive(turn.evidence),
                         context_limit='Only supplied current dialogue and reliable reference data are available. Older user history is unknown; character quotes are not shared memories.')
                    review=call(Path(base+'-judge'),'judge',[
                        {'role':'system','content':loaded['critic']},
                        {'role':'user','content':json.dumps(critic_input,ensure_ascii=False)}])
                    verdict=decode_verdict(review['reply'])
                    attempts.append(dict(attempt=attempt,generation=generation,review=review,verdict=verdict))
                    if verdict['accept']:
                        accepted=generation
                        break
                    feedback.append({'draft':generation['reply'],'violations':verdict['violations']})
                if accepted is None:
                    raise ValueError('No accepted reply within the frozen attempt bound')
                record=dict(turn=index,user=user,reply=accepted['reply'],api_response_id=accepted['api_response_id'],
                            model=accepted['model'],elapsed_seconds=round(time.monotonic()-started,3),
                            matched_ids=turn.matched_ids,accepted_attempt=len(attempts),attempts=attempts)
                save(Path(str(stem)+'-final.json'),record)
                Path(str(stem)+'.txt').write_text(record['reply'],encoding='utf-8')
                session.complete(turn,record['reply'])
                result['results'].append(record)
            except Exception as exc:
                status=fatal_provider_status(exc)
                if status:
                    stopped.set()
                error=dict(turn=index,user=user,error=type(exc).__name__+': '+str(exc),http_status=status,
                           elapsed_seconds=round(time.monotonic()-started,3),attempts=attempts)
                save(Path(str(stem)+'.failure.json'),error)
                result['failures'].append(error)
                break
        save(directory/(case['id']+'.case.json'),result)
        return result

    started=time.monotonic()
    outcomes=[]
    with ThreadPoolExecutor(max_workers=3) as pool:
        for future in as_completed([pool.submit(run_case,case) for case in cases]):
            outcomes.append(future.result())
    outcomes.sort(key=lambda c:ids.index(c['id']))
    summary=dict(method='Flash generation with Flash constraint-check retry; no WeChat sends',
        role_sha256=method['role_sha256'],expected_calls=sum(len(c['turns']) for c in cases),
        actual_replies=sum(len(c['results']) for c in outcomes),
        actual_api_responses=len(list(directory.glob('*.response.json'))),
        transport_responses=len(list(directory.glob('*.transport.json'))),
        total_cases=len(cases),completed_cases=sum(not c['failures'] for c in outcomes),
        wall_seconds=round(time.monotonic()-started,3),stopped_after_fatal_provider_error=stopped.is_set(),
        failures=[dict(id=c['id'],**e) for c in outcomes for e in c['failures']],cases=outcomes)
    save(directory/'summary.json',summary)
    print(json.dumps({k:v for k,v in summary.items() if k!='cases'},ensure_ascii=False))


if __name__=='__main__':
    main()
