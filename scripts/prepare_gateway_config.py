"""Keep explicit capabilities; avoid duplicate companion providers and unused auto plugins."""
from copy import deepcopy
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from account_personas import locked,read_json,write_json
from runtime_tuning import tune_companion_plugins


def prepare(store):
    from scripts.binding_channel import resume
    resume(store)
    with locked(store.lock_path),locked(store.config.with_suffix('.wechat-ai.lock')):
        config=read_json(store.config,{})
        voice=config.get('plugins',{}).get('entries',{}).get('wechat-companion-voice',{})
        project=voice.get('config',{}).get('projectRoot')
        if not project or not isinstance(config.get('agents',{}).get('entries'),dict):
            return False
        plugin=Path(project)/'plugins/companion-voice'
        if not all((plugin/name).is_file() for name in ('index.mjs','package.json','openclaw.plugin.json')):
            return False
        original=deepcopy(config)
        if tune_companion_plugins(config,plugin):
            backup=store.config.with_suffix('.json.before-companion-startup')
            if not backup.exists():
                write_json(backup,original)
            write_json(store.config,config)
            return True
    return False


if __name__=='__main__':
    import wechat_bot_app as app
    try:
        changed=prepare(app.persona_store())
        print('后台组件配置已精简，账号路由与明确配置的功能保留。' if changed else '后台组件配置已就绪。')
    except (OSError,ValueError,RuntimeError):
        print('后台组件配置检查失败，原配置保留。',file=sys.stderr)
        raise SystemExit(1)
