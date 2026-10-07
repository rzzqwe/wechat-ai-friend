"""Stop one owned account and clear its login; never restart the shared gateway."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import socket
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from account_personas import canonical_account,locked,read_json,write_json
from runtime_tuning import project_runtime,project_runtime_environment


def journal_path(store,local_id):
    return store.root/'data'/('unbind-'+hashlib.sha256(local_id.encode()).hexdigest()[:24]+'.json')


def resolve(store,provider_id,local_id=None):
    assignments=store.assignments()
    mappings=read_json(store.root/'data/account-bindings.json',{})
    if not local_id:
        candidates=[key for key,value in assignments.items() if
                    value.get('provider_id') and canonical_account(value['provider_id'])==canonical_account(provider_id) or
                    key==provider_id and not value.get('provider_id')]
        for file in (store.root/'data').glob('unbind-*.json'):
            operation=read_json(file,{})
            if operation.get('local_id') and canonical_account(operation.get('provider_id') or operation['local_id'])==canonical_account(provider_id):
                candidates.append(operation['local_id'])
        candidates=list(set(candidates))
        if len(candidates)!=1:
            raise ValueError('不能唯一确认这个工作台的账号，请从工作台选择账号解除。')
        local_id=candidates[0]
    if not re.fullmatch(r'[A-Za-z0-9_-]+',local_id):
        raise ValueError('本地账号标识无效。')
    saved=read_json(journal_path(store,local_id),{})
    entry=assignments.get(local_id)
    if saved:
        if saved.get('local_id')!=local_id or saved.get('state')!=str(store.state) or saved.get('config')!=str(store.config.resolve()):
            raise ValueError('解除记录归属不匹配。')
        if entry and entry.get('agent_id')!=saved['agent_id']:
            raise ValueError('陪伴归属已经改变，未继续旧清理。')
        expected=saved.get('provider_id')
        if entry and entry.get('provider_id') and canonical_account(entry['provider_id'])!=expected:
            raise ValueError('微信归属已经改变，未继续旧清理。')
        if canonical_account(expected or local_id)!=canonical_account(provider_id):
            raise ValueError('解除记录的微信账号不匹配。')
        return saved
    if not entry or entry.get('agent_id')!=store.agent_id(local_id,entry.get('persona_id')):
        raise ValueError('这个用户没有可核验的陪伴绑定。')
    values=[value for value in (entry.get('provider_id'),mappings.get(local_id)) if value]
    if values and len({canonical_account(value) for value in values+[provider_id]})!=1:
        raise ValueError('微信账号归属不一致。')
    if not values and provider_id!=local_id:
        raise ValueError('待扫码账号没有这个微信凭证。')
    owner=read_json(store.state/'agents'/entry['agent_id']/'wechat-ai-owner.json',{})
    if owner.get('local_id')!=local_id or owner.get('persona_id')!=entry['persona_id']:
        raise ValueError('运行目录归属不一致。')
    if owner.get('provider_id') and (not values or canonical_account(owner['provider_id'])!=canonical_account(values[0])):
        raise ValueError('运行目录的微信归属与绑定记录不一致，请先恢复账号记录。')
    return {'local_id':local_id,'provider_id':canonical_account(values[0]) if values else None,
            'agent_id':entry['agent_id'],'state':str(store.state),'config':str(store.config.resolve())}


def credential_paths(store,provider):
    names=[provider]
    for suffix,raw in (('-im-bot','@im.bot'),('-im-wechat','@im.wechat')):
        if provider.endswith(suffix):names.append(provider[:-len(suffix)]+raw)
    paths=[store.state/'openclaw-weixin/accounts'/(name+suffix)
           for name in names for suffix in ('.json','.sync.json','.context-tokens.json')]
    paths.extend(store.state/'credentials'/('openclaw-weixin-'+name+'-allowFrom.json') for name in names)
    for file in paths:
        if file.resolve()!=file or not file.is_relative_to(store.state):
            raise ValueError('登录凭证路径包含链接，未清理其他目录。')
    return paths


def stop_runtime(store,provider):
    config=read_json(store.config,{})
    gateway=config.get('gateway',{})
    port=gateway.get('port',18789)
    if gateway.get('mode','local')!='local' or type(port) is not int or not 1<=port<=65535:
        raise ValueError('后台连接配置无效，未删除凭证。')
    runtime=project_runtime(store.root)
    environment=project_runtime_environment(store.root)
    environment.update(OPENCLAW_STATE_DIR=str(store.state),OPENCLAW_CONFIG_PATH=str(store.config))
    try:
        with socket.create_connection(('127.0.0.1',port),timeout=1):pass
    except ConnectionRefusedError:
        if not runtime:
            raise ValueError('无法核验后台进程是否完全停止，凭证仍保留。')
        package=(runtime[1]/'../openclaw').resolve()
        inspected=subprocess.run([str(runtime[0]/'node.exe'),str(ROOT/'scripts/gateway_control.mjs'),str(package),
                                  'inactive',str(store.state),str(store.config)],env=environment,
                                 capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=15,
                                 creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        if inspected.returncode==0:return
        raise ValueError('后台尚在启动或停止中，凭证仍保留，请稍后重试。')
    except OSError as exc:
        raise ValueError('无法核验后台是否停止，凭证仍保留。') from exc
    if not runtime:raise ValueError('不能确认后台运行环境，凭证仍保留。')
    result=subprocess.run([str(runtime[0]/'node.exe'),str(ROOT/'scripts/gateway_status.mjs'),str(store.root),str(store.config),
                           '--weixin-remove-stop',provider],env=environment,
                          capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=30,
                          creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    try:stopped=result.returncode==0 and json.loads(result.stdout).get('ok') is True
    except (ValueError,AttributeError):stopped=False
    if not stopped:raise ValueError('这个账号的接收尚未确认停止，绑定和凭证保留，请稍后重试。')


def unbind(store,provider_id,local_id=None,stop=stop_runtime,emit=print):
    with locked(store.root/'data/unbind.lock'):
        with locked(store.lock_path),locked(store.config.with_suffix('.wechat-ai.lock')):
            if read_json(store.state/'wechat-ai-binding-pause.json',{}):
                raise ValueError('请先完成或关闭当前扫码窗口，再解除绑定。')
            operation=resolve(store,provider_id,local_id)
            local_id=operation['local_id'];provider=operation['provider_id'];agent=operation['agent_id']
            config=read_json(store.config,{})
            if not provider and any(row.get('agentId')==agent and row.get('match',{}).get('channel')=='openclaw-weixin'
                                    for row in config.get('bindings',[])):
                raise ValueError('待扫码记录仍有微信路由，请先恢复绑定记录。')
            if provider and any(row.get('match',{}).get('channel')=='openclaw-weixin' and
                                row.get('match',{}).get('accountId')==provider and row.get('agentId')!=agent
                                for row in config.get('bindings',[])):
                raise ValueError('微信账号属于其他陪伴，未解除。')
            paths=credential_paths(store,provider) if provider else []
            index=store.state/'openclaw-weixin/accounts.json'
            ids=read_json(index,[]) if provider else []
            if not isinstance(ids,list) or any(not isinstance(value,str) for value in ids):
                raise ValueError('微信账号索引无效，未解除绑定。')
            # Check all project records before any irreversible credential cleanup.
            accounts=read_json(store.root/'data/accounts.json',[])
            mappings=read_json(store.root/'data/account-bindings.json',{})
            if not isinstance(accounts,list) or any(not isinstance(row,dict) or not isinstance(row.get('id'),str) for row in accounts) or not isinstance(mappings,dict):
                raise ValueError('工作台账号记录无效，未解除绑定。')
            write_json(journal_path(store,local_id),operation)
            if provider:
                config.setdefault('channels',{}).setdefault('openclaw-weixin',{}).setdefault('accounts',{}).setdefault(provider,{})['enabled']=False
                write_json(store.config,config)
        credential_files=[file for file in paths if file.parent==store.state/'openclaw-weixin/accounts' and
                          not file.name.endswith(('.sync.json','.context-tokens.json'))]
        if provider and not (operation.get('stopped') and not any(file.exists() for file in credential_files) and
                             provider not in [canonical_account(value) for value in ids]):
            emit('正在停止选中账号的接收，其他账号保持运行……')
            stop(store,provider)
            operation['stopped']=True
            write_json(journal_path(store,local_id),operation)
        emit('正在清理登录凭证和本地绑定……')
        with locked(store.lock_path),locked(store.config.with_suffix('.wechat-ai.lock')):
            # Validate again after the runtime stop, before deleting anything.
            resolve(store,provider_id,local_id)
            if provider:
                current=read_json(index,[])
                write_json(index,[value for value in current if canonical_account(value)!=provider])
                for file in paths:file.unlink(missing_ok=True)
            config=read_json(store.config,{})
            config['bindings']=[row for row in config.get('bindings',[]) if not
                                (row.get('agentId')==agent and row.get('match',{}).get('channel')=='openclaw-weixin' and
                                 (not provider or row.get('match',{}).get('accountId')==provider))]
            if provider:config.get('channels',{}).get('openclaw-weixin',{}).get('accounts',{}).pop(provider,None)
            write_json(store.config,config)
            assignments=store.assignments();assignments.pop(local_id,None);write_json(store.assignments_path,assignments)
            mappings=read_json(store.root/'data/account-bindings.json',{});mappings.pop(local_id,None)
            write_json(store.root/'data/account-bindings.json',mappings)
            accounts=read_json(store.root/'data/accounts.json',[])
            write_json(store.root/'data/accounts.json',[row for row in accounts if row.get('id')!=local_id])
            journal_path(store,local_id).unlink(missing_ok=True)
        emit('解除绑定完成；人格、记忆和聊天记录保留。')


if __name__=='__main__':
    import wechat_bot_app as app
    parser=argparse.ArgumentParser();parser.add_argument('--provider-id',required=True);parser.add_argument('--local-id')
    args=parser.parse_args()
    try:unbind(app.persona_store(),args.provider_id,args.local_id)
    except (OSError,ValueError,RuntimeError,subprocess.SubprocessError) as exc:
        print('解除未完成：'+(str(exc) if isinstance(exc,ValueError) else '本机清理失败，解除记录已保留，可重试。'),file=sys.stderr)
        raise SystemExit(1)
