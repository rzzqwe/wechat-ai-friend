'''Calibrate a reply critic on frozen recorded examples; no production writes.'''
from concurrent.futures import ThreadPoolExecutor, as_completed
from hashlib import sha256
import io
import json
from pathlib import Path
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import persona_chat as chat
from scripts.persona_evaluation_policy import enforce_models, fatal_provider_status


def save(path, value):
    with path.open('x', encoding='utf-8') as out:
        json.dump(value, out, ensure_ascii=False, indent=2)


def main():
    directory = Path(sys.argv[1]).resolve()
    credentials = json.load(sys.stdin)
    if credentials['base_url'].rstrip('/') != 'https://api.deepseek.com':
        raise ValueError('Unexpected endpoint')
    if (directory/'summary.json').exists() or list(directory.glob('*.request.json')):
        raise ValueError('Never overwrite an attempted calibration')
    method = json.loads((directory/'method.json').read_text(encoding='utf-8'))
    enforce_models(directory, {'critic':method['model']})
    overrides = method.get('request_overrides', {'thinking': {'type':'disabled'}, 'max_tokens':2000})
    if set(overrides)-{'thinking','reasoning_effort','max_tokens'}:
        raise ValueError('Unsupported critic parameter')
    if overrides.get('thinking') not in ({'type':'enabled'},{'type':'disabled'}):
        raise ValueError('Invalid thinking mode')
    if overrides.get('reasoning_effort','low') not in {'low','high','max'}:
        raise ValueError('Invalid reasoning effort')
    if not isinstance(overrides.get('max_tokens'),int) or not 1200<=overrides['max_tokens']<=8192:
        raise ValueError('Invalid token budget')
    prompt = (directory/'critic-used.txt').read_text(encoding='utf-8')
    inputs_bytes = (directory/'inputs.json').read_bytes()
    for value, key in [(prompt.encode(),'prompt_sha256'),(inputs_bytes,'inputs_sha256'),
                       (Path(__file__).read_bytes(),'runner_sha256')]:
        if sha256(value).hexdigest()!=method[key]:
            raise ValueError('Frozen hash mismatch: '+key)
    inputs = json.loads(inputs_bytes)
    ids = [item['id'] for item in inputs]
    if len(ids)!=len(set(ids)):
        raise ValueError('Repeated calibration id')
    state = threading.local()
    stop_requests = threading.Event()
    original = chat.urlopen

    def transport(request, timeout=90):
        body=json.loads(request.data.decode())
        body.update(model=method['model'], response_format={'type':'json_object'})
        body.update(overrides)
        request.data=json.dumps(body,ensure_ascii=False).encode()
        save(directory/(state.ident+'.request.json'), {'endpoint':request.full_url,'body':body})
        with original(request,timeout=timeout) as response:
            raw=response.read(2_000_001)
        parsed=json.loads(raw.decode())
        choice=parsed.get('choices',[{}])[0]
        state.metadata=dict(api_response_id=parsed.get('id'),model=parsed.get('model'),
                            finish_reason=choice.get('finish_reason'),usage=parsed.get('usage'))
        save(directory/(state.ident+'.transport.json'),dict(state.metadata,
             assistant_content=choice.get('message',{}).get('content')))
        return io.BytesIO(raw)

    chat.urlopen=transport

    def run(item):
        state.ident,state.metadata=item['id'],{}
        started=time.monotonic()
        record=dict(id=item['id'])
        if stop_requests.is_set():
            record.update(error='Not attempted: an earlier authorization/payment error stopped this batch.',
                          skipped=True,elapsed_seconds=0)
            save(directory/(item['id']+'.result.json'),record)
            return record
        try:
            raw=chat.call_reply(credentials['base_url'],method['model'],credentials['api_key'],[
                {'role':'system','content':prompt},
                {'role':'user','content':json.dumps(item['input'],ensure_ascii=False)}])
            record.update(raw_output=raw,**state.metadata)
            parsed=json.loads(raw)
            if not isinstance(parsed,dict) or not isinstance(parsed.get('accept'),bool) or not isinstance(parsed.get('violations'),list):
                raise ValueError('Invalid critic result')
            if parsed['accept']==bool(parsed['violations']):
                raise ValueError('Critic accept flag contradicts violations')
            record['verdict']=parsed
        except Exception as exc:
            record['error']=type(exc).__name__+': '+str(exc)
            status=fatal_provider_status(exc)
            if status:
                stop_requests.set()
                record['http_status']=status
        record['elapsed_seconds']=round(time.monotonic()-started,3)
        save(directory/(item['id']+'.result.json'),record)
        return record

    started=time.monotonic()
    records=[]
    with ThreadPoolExecutor(max_workers=3) as pool:
        for future in as_completed([pool.submit(run,item) for item in inputs]):
            records.append(future.result())
    records.sort(key=lambda r:ids.index(r['id']))
    summary=dict(expected=len(inputs),completed=sum('verdict' in r for r in records),
                 api_responses=len(list(directory.glob('*.transport.json'))),
                 stopped_after_fatal_provider_error=stop_requests.is_set(),
                 wall_seconds=round(time.monotonic()-started,3),results=records)
    save(directory/'summary.json',summary)
    print(json.dumps({k:v for k,v in summary.items() if k!='results'},ensure_ascii=False))


if __name__=='__main__':
    main()
