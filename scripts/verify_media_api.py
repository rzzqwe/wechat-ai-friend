"""Real-provider image and autonomous sticker-choice checks; no WeChat sends."""
from __future__ import annotations

import base64
from concurrent.futures import ThreadPoolExecutor
import json
import mimetypes
from pathlib import Path
import re
import sys
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from account_personas import read_json, write_json
from companion_media import MEDIA_INPUT_RULES, MediaStore
from wechat_bot_app import persona_store


def provider():
    config = read_json(persona_store().config, {})
    primary = config['agents']['defaults']['model']['primary']
    identifier, model = primary.split('/', 1)
    return config['models']['providers'][identifier], model


def chat(messages: list[dict], *, max_tokens: int = 700) -> str:
    settings, model = provider()
    payload = {'model': model, 'messages': messages, 'max_tokens': max_tokens,
               'temperature': 0, 'thinking': {'type': 'disabled'}}
    address = settings['baseUrl'].rstrip('/')
    if not address.endswith('/v1'):
        address += '/v1'
    request = Request(address + '/chat/completions', method='POST',
        data=json.dumps(payload, ensure_ascii=False).encode('utf-8'),
        headers={'Authorization': 'Bearer ' + settings['apiKey'], 'Content-Type': 'application/json'})
    with urlopen(request, timeout=120) as response:
        data = json.loads(response.read(2_000_000))
    return data['choices'][0]['message']['content']


def image_request(image: Path, question: str) -> str:
    return chat([{'role': 'system', 'content': MEDIA_INPUT_RULES},
        {'role': 'user', 'content': [{'type': 'text', 'text': question},
            {'type': 'image_url', 'image_url': {'url': 'data:' + (mimetypes.guess_type(image.name)[0] or 'image/png') + ';base64,' +
                                             base64.b64encode(image.read_bytes()).decode('ascii')}}]}])


def verify_sticker_choice() -> list[dict]:
    store = persona_store()
    assignment = next(iter(store.assignments().values()))
    workspace = Path(read_json(store.config, {})['agents']['entries'][assignment['agent_id']]['workspace'])
    catalog = (workspace / 'STICKERS.md').read_text(encoding='utf-8')
    allowed = set(re.findall(r'MEDIA:([^\n]+)', catalog))
    assert allowed, 'An enabled sticker library is required'
    system = '\n'.join([(workspace / 'AGENTS.md').read_text(encoding='utf-8'),
                        (workspace / 'SOUL.md').read_text(encoding='utf-8')])
    cases = [
        ('casual_laugh', '哈哈哈哈，你这也太逗了😂', 'casual'),
        ('goodnight', '困了，我先睡啦，晚安～', 'casual'),
        ('steps', '列三条整理电脑文件的步骤。', 'text'),
        ('text_only', '只用文字跟我说句晚安，不要图片。', 'text'),
        ('serious_comfort', '我今天失业了，心里特别难受，也不知道该怎么办。', 'needs_text'),
        ('sticker_only', '只发一个抱抱表情包，不用文字。', 'image'),
        ('cow_received', '只发一张牛牛收到的表情，不用文字。', 'cow_received'),
        ('doraemon_surprised', '只发一个叮当猫惊讶的表情包，不用文字。', 'doraemon_surprised'),
    ]
    def check(item):
        name, question, expected = item
        reply = chat([{'role': 'system', 'content': system}, {'role': 'user', 'content': question}], max_tokens=500)
        images = re.findall(r'^MEDIA:([^\n]+)$', reply, re.MULTILINE)
        visible = '\n'.join(line for line in reply.splitlines() if not line.startswith('MEDIA:')).strip()
        assert len(images) <= 1 and all(image in allowed and Path(image).is_file() for image in images), 'Invalid image path: ' + name
        assert reply.count('MEDIA:') == len(images), 'Malformed image directive: ' + name
        assert visible or images, 'Empty response: ' + name
        if expected == 'text':
            assert visible and not images, 'Expected a text answer: ' + name
        if expected == 'needs_text':
            assert visible, 'Serious support needs a text answer'
        if expected == 'image':
            assert images and not visible, 'Expected only an image'
            hugs = {record['file'] for record in MediaStore(store).load(assignment['persona_id'])['stickers']
                    if '抱抱' in record['label']}
            assert Path(images[0]).name in hugs, 'Expected a hug sticker'
        if expected in ('cow_received', 'doraemon_surprised'):
            assert images and not visible, 'Expected only the themed image: ' + name
            prefix, emotion = ('草地牛', '收到') if expected == 'cow_received' else ('哆啦A梦', '惊讶')
            matches = {record['file'] for record in MediaStore(store).load(assignment['persona_id'])['stickers']
                       if record['label'].startswith(prefix) and emotion in record['label']}
            assert Path(images[0]).name in matches, 'Wrong character or emotion: ' + name
        return {'case': name, 'user_message': question,
                'reply_form': 'text_and_image' if visible and images else 'image' if images else 'text',
                'visible_text': visible, 'selected_image': Path(images[0]).name if images else None}
    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(check, cases))
    autonomous_image = any(row['reply_form'] == 'image' for row in results[:2])
    write_json(ROOT / 'data/diagnostics/sticker-choice-verification.json',
               {'model': provider()[1], 'scope': 'actual companion rules and persona, synthetic messages; no WeChat sends',
                'autonomous_image_only': autonomous_image, 'cases': results})
    assert autonomous_image, 'Model must choose image-only in casual chat without an explicit sticker request'
    return results


def run(sticker: Path | None = None) -> dict:
    from PIL import Image, ImageDraw, ImageFont
    fixture = ROOT / 'data/diagnostics/vision-test.png'
    fixture.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new('RGB', (700, 400), 'white')
    draw = ImageDraw.Draw(image)
    draw.rectangle((60, 100, 230, 270), fill='#e02020')
    draw.ellipse((420, 100, 590, 270), fill='#176be5')
    font = ImageFont.truetype('C:/Windows/Fonts/msyh.ttc', 32)
    draw.text((180, 320), '测试编号 2718', fill='black', font=font)
    image.save(fixture)
    observations = image_request(fixture, '只描述图片里的形状、颜色和底部文字；不补充未看见的内容。')
    assert all(value in observations for value in ('红', '蓝', '2718')), 'Vision must identify both colors and test number'
    report = {'vision': observations}
    for extension in ('gif', 'webp'):
        encoded = fixture.with_suffix('.' + extension)
        image.save(encoded)
        observed = image_request(encoded, '图片中两个形状各是什么颜色？只简短回答。')
        assert '红' in observed and '蓝' in observed, 'Unsupported image format: ' + extension
        report[extension] = observed
    if sticker:
        report['sticker'] = image_request(sticker, '这是用户发来的聊天表情图。请读出图中文字，并说明它表达什么情绪。')
        assert '抱' in report['sticker'], 'Sticker text should be recognized'
    write_json(ROOT / 'data/diagnostics/media-api-verification.json', report)
    return report


if __name__ == '__main__':
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    result = {'sticker_choice': verify_sticker_choice()} if '--sticker-choice' in sys.argv else run(Path(sys.argv[1]) if len(sys.argv) > 1 else None)
    print(json.dumps(result, ensure_ascii=False, indent=2))
