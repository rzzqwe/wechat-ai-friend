# 重建复核索引，不联网，不覆盖人工人格正文。
import hashlib
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import wechat_bot_app as app
import persona_support as support


def build():
    folder = ROOT / 'profiles'
    rules_raw = (folder / 'reference_rules.json').read_bytes()
    rules = json.loads(rules_raw.decode('utf-8'))
    if rules.get('target_role') != support.TARGET_ROLE:
        raise ValueError('角色配置必须指定左侧为模仿对象、右侧仅作上下文。')
    profile_raw = (folder / 'reference_persona.md').read_bytes()
    profile = profile_raw.decode('utf-8')
    source_hashes = {prefix: hashlib.sha256(path.read_bytes()).hexdigest()
                     for prefix, path in zip(('W', 'D'), app.REFERENCE_CHAT_PATHS)}
    if source_hashes != rules.get('source_sha256'):
        raise ValueError('原始聊天记录已变化，需要重新复核来源并更新规则中的 SHA-256。')
    messages = app.load_chat_files(list(app.REFERENCE_CHAT_PATHS))
    index = {}
    source_counts = {}
    for prefix, path in zip(('W', 'D'), app.REFERENCE_CHAT_PATHS):
        parsed = app.parse_messages(path)
        number = 0
        time = ''
        for line_number, line in enumerate(app.read_text(path).splitlines(), 1):
            if line.startswith('### '):
                time = line[4:]
            if not line.startswith(('[视频约 ', '我：', '我方：', '对方：')):
                continue
            local = app.parse_lines(line)
            if len(local) != 1:
                raise ValueError(f'无法唯一定位：{path.name}:{line_number}')
            message = local[0]
            number += 1
            expected = parsed[number - 1]
            if (message.speaker, message.content) != (expected.speaker, expected.content):
                raise ValueError(f'解析不一致：{path.name}:{line_number}')
            if line.startswith('[视频约 '):
                time = line[1:line.index(']')]
            index[f'{prefix}{number:04d}'] = dict(source=path.name, path=path.relative_to(ROOT).as_posix(), number=number, line=line_number, time=time, speaker=message.speaker, text=message.content)
        if number != len(parsed):
            raise ValueError(f'消息数量不一致：{path.name}')
        source_counts[prefix] = dict(total=number, target=sum(m.speaker == '对方' for m in parsed))
    excluded = {ref for group in rules['excluded_from_persona'] for ref in group['ids']}
    if excluded - index.keys():
        raise ValueError(f'无效排除编号：{sorted(excluded - index.keys())}')
    approved = set()
    for trait in rules['traits']:
        for ref in trait['evidence']:
            if ref not in index or index[ref]['speaker'] != '对方' or ref in excluded:
                raise ValueError(f'人物证据角色或范围错误：{ref}')
            approved.add(ref)
    for scene in rules['scenarios']:
        for field, speaker in [('context', '我'), ('reply', '对方')]:
            for ref in scene[field]:
                if ref not in index or index[ref]['speaker'] != speaker or ref in excluded:
                    raise ValueError(f'场景角色或范围错误：{ref}')
                approved.add(ref)
        refs = scene['context'] + scene['reply']
        if len({ref[0] for ref in refs}) != 1 or refs != sorted(refs):
            raise ValueError('场景跨来源或顺序错误：' + scene['id'])
    profile_refs = set(re.findall(r'[WD][0-9]{4}', profile))
    if profile_refs - (approved | excluded):
        raise ValueError(f'正文有未审查引用：{sorted(profile_refs - approved - excluded)}')
    for speaker, text, ref in re.findall(r'^- (我|对方)：(.*)（([WD][0-9]{4})）$', profile, re.MULTILINE):
        if ref not in approved or (speaker, text) != (index[ref]['speaker'], index[ref]['text']):
            raise ValueError(f'正文引用不是对应角色原句：{ref}')
    payload = dict(version=2, review_date=rules['review_date'], scope=rules['scope'], input_signature=app.corpus_signature(messages), message_count=len(messages), target_speaker='对方', source_counts=source_counts, profile_file='reference_persona.md', profile_sha256=hashlib.sha256(profile_raw).hexdigest(), rules_sha256=hashlib.sha256(rules_raw).hexdigest(), traits=rules['traits'], scenarios=rules['scenarios'], excluded_from_persona=rules['excluded_from_persona'], approved_message_ids=sorted(approved), evidence={ref: index[ref] for ref in sorted(approved | excluded)})
    payload['target_role'] = rules['target_role']
    payload['source_sha256'] = source_hashes
    payload['review_method'] = rules['review_method']
    temporary = folder / 'reference_evidence.json.tmp'
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + chr(10), encoding='utf-8')
    temporary.replace(folder / 'reference_evidence.json')
    print(json.dumps(dict(imported=len(messages), reviewed=len(approved), reviewed_target=sum(index[ref]['speaker'] == '对方' for ref in approved), excluded=len(excluded), traits=len(rules['traits']), scenarios=len(rules['scenarios'])), ensure_ascii=False))


if __name__ == '__main__':
    build()
