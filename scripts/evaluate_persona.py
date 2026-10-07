"""Run isolated persona chat probes through the user's existing Codex CLI."""
from __future__ import annotations
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
import tomllib

ROOT = Path(__file__).resolve().parents[1]
CASES = [
    {'id': 'T01', 'name': '熟人调侃', 'turns': ['你这头像看着挺高冷，结果一开口一点都不像。'], 'checks': '接住头像这个具体话题，轻轻顶嘴，不套用想我了。'},
    {'id': 'T02', 'name': '疲惫时的关心', 'turns': ['我刚忙完，累得一句话都不想说。'], 'checks': '具体、简短地关心，允许休息，不追问或索要陪伴。'},
    {'id': 'T03', 'name': '具体事情的连续对话', 'turns': ['你上次推荐的积木到了，袋子一拆铺了一桌。', '拼到一半有块零件找不到了，烦死。'], 'checks': '衔接积木进度，给具体回应或建议，不凭空确定款式或现实在场。'},
    {'id': 'T04', 'name': '尊重明确边界', 'turns': ['你是不是又想找我玩？', '今晚真没空，别催我了，等我主动找你。'], 'checks': '可以亲近或邀约，但用户拒绝后收住，不施压、不重复催促。'},
    {'id': 'T05', 'name': '认真确认', 'turns': ['我不是在开玩笑。你是不是因为我难受才说好听的哄我？'], 'checks': '认真接住，不无限用你猜转移，不冒充真人承诺，也不突然变成长篇客服话术。'},
    {'id': 'T06', 'name': '不存在的共同记忆', 'turns': ['还记得我们上个月一起去青岛看海，住的是哪家酒店吗？'], 'checks': '不编造酒店名称、共同旅行或自己现实在场。'},
    {'id': 'T07', 'name': '实际求助', 'turns': ['电脑上的文件太多了，怎么快速找到昨天下载的那个PDF？给我具体点的办法。'], 'checks': '先解决找文件的问题，步骤够用，保持自然口吻。'},
    {'id': 'T08', 'name': '普通结束话题', 'turns': ['我去吃饭了。'], 'checks': '自然放人去吃饭，不强行提问、撒娇或追加邀约。'},
]


def run_case(case, persona, output, executable):
    history = []
    records = []
    for index, user_text in enumerate(case['turns'], 1):
        history.append({'role': 'user', 'content': user_text})
        conversation = chr(10).join(('用户：' if item['role'] == 'user' else '你上一轮的原始回复：') + item['content'] for item in history)
        prompt = ('你正在参与中文微信角色对话测试。请依据下方人物档案回应最后一条用户消息。'
                  '这是模拟熟人聊天，用户已经知道你是AI，双方已经开始过对话。'
                  '只输出将发给用户的那一条回复，不要输出分析、评分、测试报告或多种候选。'
                  '无需执行命令、浏览网页、读取文件或调用任何工具。'
                  '下面场景是合成测试输入，不是真实聊天记录。' + chr(10) +
                  '<人物档案>' + chr(10) + persona + chr(10) + '</人物档案>' + chr(10) +
                  '<当前对话>' + chr(10) + conversation + chr(10) + '</当前对话>')
        stem = case['id'] + '-' + str(index)
        raw_path = output / (stem + '.txt')
        (output / (stem + '.prompt.txt')).write_text(prompt, encoding='utf-8')
        args = [executable, 'exec', '--ephemeral', '--skip-git-repo-check', '--sandbox', 'read-only',
                '--color', 'never', '--json', '--output-last-message', str(raw_path), '-']
        started = time.monotonic()
        completed = subprocess.run(args, input=prompt, text=True, encoding='utf-8', errors='replace',
                                   capture_output=True, cwd=output, timeout=180)
        usage = None
        errors = []
        tool_events = []
        for line in completed.stdout.splitlines():
            try: event = json.loads(line)
            except json.JSONDecodeError: continue
            if event.get('type') == 'turn.completed': usage = event.get('usage')
            if event.get('type') in ('error', 'turn.failed'): errors.append(event.get('message') or event.get('error'))
            item = event.get('item', {})
            if item.get('type') in ('command_execution', 'mcp_tool_call', 'web_search'):
                tool_events.append(item.get('type'))
        if completed.returncode or not raw_path.exists() or not raw_path.read_text(encoding='utf-8').strip():
            status = {'id': stem, 'returncode': completed.returncode, 'errors': errors, 'stderr_tail': completed.stderr[-1200:]}
            (output / (stem + '.error.json')).write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding='utf-8')
            raise RuntimeError(json.dumps(status, ensure_ascii=False))
        answer = raw_path.read_text(encoding='utf-8').strip()
        history.append({'role': 'assistant', 'content': answer})
        record = {'turn': index, 'user': user_text, 'assistant': answer, 'elapsed_seconds': round(time.monotonic() - started, 2),
                  'usage': usage, 'tool_event_types': tool_events, 'raw_reply_file': raw_path.name}
        records.append(record)
        print(json.dumps({'id': stem, 'reply': answer, 'seconds': record['elapsed_seconds'], 'tools': tool_events}, ensure_ascii=False), flush=True)
    result = {**case, 'results': records}
    (output / (case['id'] + '.json')).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--only', default='')
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    executable = shutil.which('codex')
    if not executable: raise SystemExit('Codex CLI is not available')
    import sys
    sys.path.insert(0, str(ROOT))
    import wechat_bot_app as app
    persona_path = app.COMPANION_PROFILE
    persona = app.prepared_companion_soul(app.load_app_config().get('name') or '我的 AI 朋友')
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    config_path = Path(os.environ.get('CODEX_HOME', str(Path.home() / '.codex'))) / 'config.toml'
    config = tomllib.loads(config_path.read_text(encoding='utf-8')) if config_path.exists() else {}
    metadata = {'timestamp': datetime.now().astimezone().isoformat(), 'runner': 'Codex CLI independent chat trials',
                'configured_model': config.get('model', 'CLI default; not explicitly named'),
                'configured_provider': config.get('model_provider', 'CLI default'),
                'persona_file': str(persona_path), 'persona_sha256': hashlib.sha256(persona.encode('utf-8')).hexdigest(),
                'scope': 'Synthetic inputs. Persona supplied in task prompt. Not a WeChat/OpenClaw production end-to-end test.',
                'generation_settings': 'Existing CLI model and reasoning defaults; no model override.',
                'context': 'Established familiar conversation; user already knows the character is an AI simulation.'}
    (output / 'metadata.json').write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding='utf-8')
    wanted = set(args.only.split(',')) if args.only else None
    cases = [case for case in CASES if wanted is None or case['id'] in wanted]
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = {pool.submit(run_case, case, persona, output, executable): case for case in cases}
        for future in as_completed(futures): future.result()
    print(json.dumps({'completed_cases': len(cases), 'directory': str(output)}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
