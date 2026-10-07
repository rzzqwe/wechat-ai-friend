"""Only this attempt's durable receipt can confirm a committed QR login."""
from pathlib import Path
from datetime import datetime,timezone,timedelta
import re
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from account_personas import read_json


def verify(store,filename,attempt,local_id):
    receipt=read_json(Path(filename),{})
    provider=receipt.get('provider_id')
    if (receipt.get('attempt')!=attempt or receipt.get('local_id')!=local_id or receipt.get('saved') is not True
            or not isinstance(provider,str) or not re.fullmatch('[a-z0-9_-]{1,64}',provider)):
        raise ValueError('本次微信授权回执无效。')
    if receipt.get('agent_id')!=store.assignments().get(local_id,{}).get('agent_id') or not receipt.get('agent_id'):
        raise ValueError('扫码期间陪伴归属已改变。')
    index=read_json(store.state/'openclaw-weixin/accounts.json',[])
    credential=store.state/'openclaw-weixin/accounts'/(provider+'.json')
    if credential.resolve()!=credential or not isinstance(index,list) or provider not in index:
        raise ValueError('本次微信账号索引不完整。')
    if not read_json(credential,{}).get('token','').strip():
        raise ValueError('本次微信登录凭证不完整。')
    return provider


def recover_legacy(store,result_path):
    result_path=Path(result_path)
    result=read_json(result_path,{})
    local_id=result.get('local_id')
    if result.get('status')!='failed' or result.get('stage')!='微信授权已保存，正在准备专属人格':
        raise ValueError('旧记录没有本次授权保存的确认。')
    entry=store.assignments().get(local_id,{})
    if not entry or entry.get('provider_id'):
        raise ValueError('不是待恢复的扫码绑定。')
    end=datetime.fromtimestamp(result_path.stat().st_mtime,timezone.utc)
    start=end-timedelta(seconds=float(result.get('elapsed_seconds',0))+2)
    if store.assignments_path.stat().st_mtime>result_path.stat().st_mtime:
        raise ValueError('失败后陪伴分配已经改变，未恢复旧授权。')
    config=read_json(store.config,{})
    candidates=[]
    for provider in read_json(store.state/'openclaw-weixin/accounts.json',[]):
        if not isinstance(provider,str) or not re.fullmatch('[a-z0-9_-]{1,64}',provider):continue
        file=store.state/'openclaw-weixin/accounts'/(provider+'.json')
        if file.resolve()!=file:continue
        credential=read_json(file,{})
        try:saved=datetime.fromisoformat(credential.get('savedAt','').replace('Z','+00:00'))
        except (ValueError,TypeError):continue
        if not (credential.get('token') and start<=saved<=end+timedelta(seconds=2)):continue
        if any(row.get('match',{}).get('accountId')==provider and row.get('agentId')!=entry['agent_id'] for row in config.get('bindings',[])):
            continue
        candidates.append(provider)
    if len(candidates)!=1:
        raise ValueError('无法唯一核验这次已保存的微信凭证。')
    return local_id,candidates[0]


if __name__=='__main__':
    import wechat_bot_app as app
    try:print(verify(app.persona_store(),*sys.argv[1:]))
    except (OSError,ValueError,TypeError,AttributeError):
        print('未能核验本次微信授权回执。',file=sys.stderr)
        raise SystemExit(1)
