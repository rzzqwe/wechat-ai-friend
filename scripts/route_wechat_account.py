"""Run after QR authorization and before starting the gateway."""
from pathlib import Path
import argparse
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import wechat_bot_app as app
from account_personas import locked, read_json, write_json
from runtime_tuning import install_windows_gateway_acceleration
from companion_speech import ensure_runtime_config


def bind(local_id, provider_id):
    store = app.persona_store()
    entry = store.assignments().get(local_id)
    if not entry:
        raise ValueError('这个用户尚未分配人格，不能启动微信。')
    store.assign(local_id, entry['persona_id'], provider_id)
    ensure_runtime_config(store)
    try:
        install_windows_gateway_acceleration(store.state, ROOT / 'scripts/windows_native_paths.cjs')
    except (OSError, ValueError) as exc:
        print('Windows runtime acceleration was not applied: ' + str(exc), file=sys.stderr)
    with locked(app.ACCOUNT_BINDINGS_FILE.with_suffix('.lock')):
        bindings = read_json(app.ACCOUNT_BINDINGS_FILE, {})
        bindings[local_id] = provider_id
        write_json(app.ACCOUNT_BINDINGS_FILE, bindings)
    # A failed scan can leave a safely disabled orphan. Enable only after its
    # confirmed identity and dedicated route have both been published.
    credential=store.state/'openclaw-weixin/accounts'/(provider_id+'.json')
    if credential.is_file() and read_json(credential,{}).get('token'):
        with locked(store.lock_path),locked(store.config.with_suffix('.wechat-ai.lock')):
            config=read_json(store.config,{})
            account=config.get('channels',{}).get('openclaw-weixin',{}).get('accounts',{}).get(provider_id)
            if isinstance(account,dict) and account.get('enabled') is False:
                account['enabled']=True
                write_json(store.config,config)
    return entry['agent_id']


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--local-id')
    parser.add_argument('--provider-id')
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--detach-all', action='store_true')
    args = parser.parse_args()
    try:
        if args.detach_all:
            store = app.persona_store()
            for local_id in list(store.assignments()):
                store.detach(local_id)
            print('账号人格路由已解除，专属文件和聊天记录保留。')
        elif args.check:
            entry = app.persona_store().assignments().get(args.local_id)
            if not entry:
                raise ValueError('请先分配人格。')
            if not app.persona_store().profile_path(entry['persona_id']).read_text(encoding='utf-8').strip():
                raise ValueError('人格内容为空。')
            print('专属人格预检通过。')
        elif args.provider_id:
            print('账号专属人格路由已就绪：' + bind(args.local_id, args.provider_id))
        else:
            raise ValueError('缺少微信账号标识。')
    except Exception as exc:
        print('人格绑定失败：' + str(exc), file=sys.stderr)
        raise SystemExit(1)
