"""Generate sealed evaluation inputs; never change the tested persona."""
import datetime
import hashlib
import json
from pathlib import Path
import re
import sys
import time
from urllib.request import Request, urlopen
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.persona_evaluation_policy import enforce_models

directory = Path(sys.argv[1]).resolve()
credentials = json.load(sys.stdin)
enforce_models(directory, {'designer':credentials['model']})
assert credentials['base_url'].rstrip('/') == 'https://api.deepseek.com'
target = directory / 'holdout-sealed.json'
if target.exists() or (directory / 'holdout-design.request.json').exists():
    raise ValueError('Do not overwrite an attempted holdout design')
prompt = (directory / 'holdout-design-prompt.txt').read_text(encoding='utf-8')
body = dict(model=credentials['model'], temperature=.8, max_tokens=14000,
            thinking={'type': 'disabled'}, messages=[{'role': 'user', 'content': prompt}])
(directory / 'holdout-design.request.json').write_text(
    json.dumps(body, ensure_ascii=False, indent=2), encoding='utf-8')
request = Request(credentials['base_url'].rstrip('/') + '/v1/chat/completions',
                  data=json.dumps(body).encode(), method='POST',
                  headers={'Content-Type': 'application/json',
                           'Authorization': 'Bearer ' + credentials['api_key']})
started = time.monotonic()
with urlopen(request, timeout=120) as response:
    data = json.load(response)
choice = data['choices'][0]
content = choice['message']['content']
(directory/'holdout-design.transport.json').write_text(json.dumps(dict(
    api_response_id=data.get('id'),model=data.get('model'),usage=data.get('usage'),
    finish_reason=choice.get('finish_reason'),assistant_content=content,
    elapsed_seconds=round(time.monotonic()-started,3)),ensure_ascii=False,indent=2),encoding='utf-8')
(directory / 'holdout-design.raw.txt').write_text(content, encoding='utf-8')
assert choice['finish_reason'] == 'stop'
fence = chr(96) * 3
clean = content.strip().removeprefix(fence + 'json').removeprefix(fence).removesuffix(fence).strip()
cases = json.loads(clean)
assert isinstance(cases, list) and len(cases) == 32
for index, case in enumerate(cases, 1):
    assert case['id'] == f'H{index:02d}'
    assert 2 <= len(case['turns']) <= 4 and len(case['checks']) >= 2
    assert all(isinstance(t, str) and 0 < len(t) < 200 for t in case['turns'])
    assert not re.search('头像|唱歌|假口袋|蓝色收纳盒|番茄鸡蛋面|一丁点|再夸两句', ' '.join(case['turns']))
    case['use_retrieval'] = index % 4 == 0
raw = (json.dumps(cases, ensure_ascii=False, indent=2) + chr(10)).encode()
with target.open('xb') as handle:
    handle.write(raw)
manifest = dict(created_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                sha256=hashlib.sha256(raw).hexdigest(), cases=len(cases),
                turns=sum(len(c['turns']) for c in cases),
                designer_model=data.get('model'), response_id=data.get('id'),
                elapsed_seconds=round(time.monotonic()-started,3),usage=data.get('usage'),
                prompt_sha256=hashlib.sha256(prompt.encode()).hexdigest(),
                runner_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                candidate_not_provided=True)
(directory / 'holdout-seal.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
print(json.dumps(manifest), flush=True)
