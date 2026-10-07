"""Atomically change one owned account; Gateway hot reload handles its channel."""
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from account_personas import locked,read_json,write_json


def set_enabled(store, provider_id, enabled):
    with locked(store.lock_path),locked(store.config.with_suffix('.wechat-ai.lock')):
        if read_json(store.state/'wechat-ai-binding-pause.json',{}):
            raise ValueError('请先完成或关闭当前扫码窗口，再改变账号启停设置。')
        if not any(entry.get('provider_id')==provider_id for entry in store.assignments().values()):
            raise ValueError('微信账号不属于这个工作台，未修改。')
        config=read_json(store.config,{})
        config.setdefault('session',{})['dmScope']='per-account-channel-peer'
        account=config.setdefault('channels',{}).setdefault('openclaw-weixin',{}).setdefault('accounts',{}).setdefault(provider_id,{})
        account['enabled']=enabled
        write_json(store.config,config)


if __name__=='__main__':
    import wechat_bot_app as app
    try:
        if len(sys.argv)!=3 or sys.argv[2] not in ('start','stop'):
            raise ValueError('缺少账号或操作。')
        set_enabled(app.persona_store(),sys.argv[1],sys.argv[2]=='start')
        print('账号设置已保存，后台会自动应用。')
    except (OSError,ValueError,RuntimeError):
        print('账号设置保存失败，未继续启动。',file=sys.stderr)
        raise SystemExit(1)
