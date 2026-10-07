"""Update an initialized runtime's model settings without rerunning onboarding."""
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys
from urllib.parse import urlsplit

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from account_personas import locked, read_json, write_json
from runtime_tuning import prefer_configured_api_key, project_runtime


def has_include(value):
    if isinstance(value,dict):
        return '$include' in value or any(has_include(item) for item in value.values())
    return isinstance(value,list) and any(has_include(item) for item in value)


def configure(store, environment=None, runner=subprocess.run):
    env=dict(os.environ if environment is None else environment)
    url=env.get('WECHAT_AI_BASE_URL','').strip()
    model=env.get('WECHAT_AI_MODEL','').strip()
    key=env.get('WECHAT_AI_API_KEY','').strip()
    if not url or not model or not key or urlsplit(url).scheme not in ('http','https') or not urlsplit(url).netloc:
        raise ValueError('请填写有效的模型服务地址、模型名和 Key。')
    runtime=project_runtime(store.root)
    if runtime is None:
        return False
    node,binary=runtime
    packages=[candidate.resolve() for candidate in (binary/'../openclaw',binary/'node_modules/openclaw')
              if (candidate/'package.json').is_file()]
    if len(packages)!=1 or read_json(packages[0]/'package.json',{}).get('version')!='2026.9.6':
        return False
    env.update(WECHAT_AI_BASE_URL=url,WECHAT_AI_MODEL=model,WECHAT_AI_API_KEY=key)
    with locked(store.lock_path),locked(store.config.with_suffix('.wechat-ai.lock')):
        original=read_json(store.config,{})
        gateway=original.get('gateway',{})
        auth=gateway.get('auth',{})
        if (has_include(original) or gateway.get('mode')!='local' or
                auth.get('mode') not in ('token','password') or not auth.get(auth.get('mode')) or
                not isinstance(original.get('agents',{}).get('entries'),dict) or not (store.state/'gateway.cmd').is_file()):
            return False
        process=runner([str(node/'node.exe'),str(ROOT/'scripts/text_provider_config.mjs'),str(packages[0])],
                       input=json.dumps(original,ensure_ascii=False),env=env,capture_output=True,text=True,
                       encoding='utf-8',errors='replace',timeout=20,
                       creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        if process.returncode==2:
            return False
        if process.returncode:
            raise ValueError('文本模型配置生成失败；原配置保留。')
        try:
            updated=json.loads(process.stdout)
        except (ValueError,TypeError) as exc:
            raise ValueError('文本模型配置结果无效；原配置保留。') from exc
        allowed=deepcopy(original)
        allowed['models']=updated.get('models')
        defaults=allowed.setdefault('agents',{}).setdefault('defaults',{})
        new_defaults=updated.get('agents',{}).get('defaults',{})
        for field in ('model','models'):
            if field in new_defaults:
                defaults[field]=new_defaults[field]
            else:
                defaults.pop(field,None)
        # Builder must preserve account routing, companion ownership and gateway settings.
        if updated!=allowed:
            raise ValueError('配置生成器改动了模型之外的设置；原配置保留。')
        prefer_configured_api_key(updated)
        if updated!=original:
            write_json(store.config,updated)
    return True


if __name__=='__main__':
    try:
        import wechat_bot_app as app
        if configure(app.persona_store()):
            print('文本模型配置已保存；复用现有后台服务。')
        else:
            print('运行环境尚需首次初始化，使用官方初始化流程。')
            raise SystemExit(2)
    except (OSError,ValueError,RuntimeError,subprocess.SubprocessError):
        print('文本模型保存失败；原配置仍保留，请检查模型设置和运行环境。',file=sys.stderr)
        raise SystemExit(1)
