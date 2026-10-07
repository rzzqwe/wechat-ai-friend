"""微信 AI 朋友：一个面向小白的本地配置器。

功能：
1. 填写陪伴名称，应用事先整理好的陪伴设定；
2. 配置微信聊天使用的兼容模型；
3. 一键安装/启用 OpenClaw 微信渠道；
4. 按账号生成二维码，分别启停和解除微信 ClawBot 绑定；
5. 按账号查看 OpenClaw 在本机保存的聊天记录。

主界面不导入或提取资料。生成陪伴直接使用本地已整理好的设定，不调用模型。
"""

from __future__ import annotations

import html
import base64
import csv
import hashlib
import io
import json
import mimetypes
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
import ctypes
from datetime import datetime
from ctypes import wintypes
from collections import Counter
import persona_support as _persona
import persona_memory as _memory
import wechat_ui as _ui
import display_support as _display
from account_personas import PersonaStore, read_json, write_json
from companion_controls import CompanionControls
from media_controls import MediaControls
from voice_controls import VoiceControls
from runtime_controls import RuntimeControls
from runtime_status import account_status
from runtime_tuning import project_runtime, project_runtime_environment
from dataclasses import dataclass, replace
from html.parser import HTMLParser
from pathlib import Path
from tkinter import END, BOTH, LEFT, RIGHT, X, Y, W, messagebox, simpledialog
from typing import Callable
import tkinter as tk
from tkinter import ttk
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
DATA_DIR.mkdir(exist_ok=True)
APP_CONFIG = DATA_DIR / "config.json"
API_KEY_FILE = DATA_DIR / "api-key.bin"
ACCOUNTS_FILE = DATA_DIR / "accounts.json"
ACCOUNT_BINDINGS_FILE = DATA_DIR / "account-bindings.json"
PERSONA_INDEX_FILE = DATA_DIR / "persona-memory.json"
OPENCLAW_HOME = Path(os.environ.get("USERPROFILE", str(Path.home()))) / ".openclaw"
WEIXIN_CHANNEL = "openclaw-weixin"
REFERENCE_CHAT_PATHS = (
    ROOT / "video_extract" / "微信" / "微信聊天记录.txt",
    ROOT / "video_extract" / "抖音" / "抖音聊天记录.txt",
)


def normalize_speaker(speaker: str) -> str:
    value = speaker.strip()
    aliases = {'我方': '我', '对': '对方',
               '左': '对方', '左侧': '对方', '左边': '对方', 'left': '对方',
               '右': '我', '右侧': '我', '右边': '我', 'right': '我'}
    return aliases.get(value.casefold(), value)


@dataclass
class Message:
    speaker: str
    content: str
    source: str = ""
    message_id: str = ""
    line_number: int = 0
    time_text: str = ""
    time_precision: str = "unknown"
    screen_side: str = "unknown"
    conversation_id: str = ""
    source_number: int = 0

    def __post_init__(self) -> None:
        side = str(self.screen_side or "").strip().casefold()
        if side in {"", "unknown"}:
            side = self.speaker.strip().casefold()
        if side in {'left', '左', '左侧', '左边'}:
            self.speaker = '对方'
            self.screen_side = 'left'
        elif side in {'right', '右', '右侧', '右边'}:
            self.speaker = '我'
            self.screen_side = 'right'
        else:
            self.speaker = normalize_speaker(self.speaker)
            self.screen_side = 'unknown'


class TextCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        value = data.strip()
        if value:
            self.parts.append(value)


def read_text(path: Path) -> str:
    for encoding in ("utf-8-sig", "utf-8", "gb18030", "gbk"):
        try:
            return path.read_text(encoding=encoding)
        except UnicodeDecodeError:
            continue
    return path.read_text(errors="ignore")


def clean_text(value: str) -> str:
    value = html.unescape(value)
    value = re.sub(r"\s+", " ", value).strip()
    return value


def redact_sensitive(value: str) -> str:
    """在发给在线模型前，遮掉几类常见的直接身份信息。"""
    value = re.sub(r"(?<!\d)1[3-9]\d{9}(?!\d)", "[手机号]", value)
    value = re.sub(r"(?<!\w)[\w.+-]+@[\w-]+(?:\.[\w-]+)+(?!\w)", "[邮箱]", value)
    value = re.sub(r"(?<!\d)\d{17}[\dXx](?!\d)", "[身份证号]", value)
    return value


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]


def _protect_secret(data: bytes) -> bytes:
    """使用 Windows 当前用户的 DPAPI 加密，不把 API Key 明文写入项目。"""
    if os.name != "nt":
        raise OSError("Windows DPAPI only")
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    source = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
    source_blob = _DataBlob(len(data), ctypes.cast(source, ctypes.POINTER(ctypes.c_ubyte)))
    output_blob = _DataBlob()
    if not crypt32.CryptProtectData(ctypes.byref(source_blob), None, None, None, None, 0, ctypes.byref(output_blob)):
        raise ctypes.WinError()
    try:
        return ctypes.string_at(output_blob.pbData, output_blob.cbData)
    finally:
        kernel32.LocalFree(output_blob.pbData)


def _unprotect_secret(data: bytes) -> bytes:
    if os.name != "nt":
        raise OSError("Windows DPAPI only")
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    source = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
    source_blob = _DataBlob(len(data), ctypes.cast(source, ctypes.POINTER(ctypes.c_ubyte)))
    output_blob = _DataBlob()
    if not crypt32.CryptUnprotectData(ctypes.byref(source_blob), None, None, None, None, 0, ctypes.byref(output_blob)):
        raise ctypes.WinError()
    try:
        return ctypes.string_at(output_blob.pbData, output_blob.cbData)
    finally:
        kernel32.LocalFree(output_blob.pbData)


def _record_value(record: dict, *names: str) -> str:
    for name in names:
        value = record.get(name)
        if isinstance(value, (str, int, float)) and str(value).strip():
            return clean_text(str(value))
    return ''


def _record_time(record: dict) -> tuple[str, str]:
    value = _record_value(record, '日期时间', 'datetime', 'timestamp')
    if not value:
        date = _record_value(record, '日期', 'date')
        clock = _record_value(record, '时间', 'time')
        value = ' '.join(part for part in (date, clock) if part)
    if value:
        full_date = bool(re.search(r'[0-9]{4}[-/.][0-9]{1,2}[-/.][0-9]{1,2}', value))
        partial_date = bool(re.search(r'[0-9]{1,2}[-/月][0-9]{1,2}', value))
        clock = bool(re.search(r'[0-9]{1,2}:[0-9]{2}', value))
        if full_date:
            return value, 'datetime' if clock else 'date'
        if partial_date:
            return value, 'partial_datetime' if clock else 'partial_date'
        return value, 'time' if clock else 'unknown'
    video = _record_value(record, '视频时间', '秒数')
    return (video, 'video_position') if video else ('', 'unknown')


def parse_json_messages(obj: object) -> list[Message]:
    if isinstance(obj, dict):
        for key in ('messages', 'items', 'data', 'records', 'chat'):
            if key in obj:
                return parse_json_messages(obj[key])
        return []
    if not isinstance(obj, list):
        return []
    result = []
    for raw in obj:
        if not isinstance(raw, dict):
            continue
        item = {str(key).lstrip(chr(65279)).strip().casefold(): value for key, value in raw.items()}
        speaker = _record_value(item, 'speaker', 'sender', 'from', 'name', 'nickname', 'talker', '发送方', '说话人')
        side = _record_value(item, 'screen_side', 'side', '气泡位置').casefold()
        if side in {'left', '左', '左侧', '左边', 'right', '右', '右侧', '右边'}:
            speaker = normalize_speaker(side)
        content = _record_value(item, 'content', 'text', 'message', 'body', 'value', '内容')
        if not speaker or not content:
            continue
        try:
            line_number = max(0, int(_record_value(item, 'line_number', 'line', '原文行号') or 0))
        except ValueError:
            line_number = 0
        time_text, precision = _record_time(item)
        result.append(Message(speaker, content,
            message_id=_record_value(item, 'message_id', 'id', '序号', '编号'),
            line_number=line_number, time_text=time_text, time_precision=precision, screen_side=side,
            conversation_id=_record_value(item, 'conversation_id', 'conversation', '会话', '时间段序号')))
    return result


def parse_csv_messages(text: str) -> list[Message]:
    result = []
    reader = csv.DictReader(io.StringIO(text.lstrip(chr(65279))), strict=True)
    for row in reader:
        parsed = parse_json_messages([row])
        if parsed:
            parsed[0].line_number = reader.line_num
            result.extend(parsed)
    return result


def parse_lines(text: str) -> list[Message]:
    """解析常见的微信/QQ 导出文本，尽量宽容。"""
    result: list[Message] = []
    # 本项目导出的微信核对版： [视频约 00:02] 我方：内容
    video_line = re.compile(r"^\[视频约\s+\d{2}:\d{2}\]\s*(我方|对方|我|对|左侧|左边|左|右侧|右边|右|left|right)\s*[:：]\s*(.+)$")
    # 本项目导出的抖音 Markdown/TXT：- **对方**：内容 或 对方：内容
    markdown_line = re.compile(r"^-\s*\*\*(我方|对方|我|对|左侧|左边|左|右侧|右边|右|left|right)\*\*\s*[:：]\s*(.+)$")
    speaker_line = re.compile(r"^(我方|对方|我|对|左侧|左边|左|右侧|右边|右|left|right)\s*[:：]\s*(.+)$")
    standard_export = bool(re.search(
        r"^# (?:微信|抖音)?聊天记录(?:$|[ （(])", text, re.MULTILINE
    ))
    # 日期开头格式：2024-01-01 12:01:01 张三：你好
    date_first = re.compile(
        r"^(?:\[?\d{4}[-/.]\d{1,2}[-/.]\d{1,2}(?:\s+\d{1,2}:\d{2}(?::\d{2})?)?\]?\s+)?"
        r"([^:\n：]{1,40})\s*[:：]\s*(.+)$"
    )
    # 名称在前、时间在后：张三 2024/01/01 12:01\n你好
    name_first = re.compile(r"^([^\s:：]{1,30})\s+\d{4}[-/.]\d{1,2}[-/.]\d{1,2}.*$")
    current: Message | None = None
    current_time = ""
    current_time_precision = "unknown"
    current_conversation = ""
    for line_number, raw in enumerate(text.splitlines(), 1):
        line = clean_text(raw)
        if not line:
            continue
        match = video_line.match(line) or markdown_line.match(line) or speaker_line.match(line)
        if match:
            speaker, content = match.groups()
            video_match = video_line.match(line)
            time_text = video_match.group(0).split(']', 1)[0].lstrip('[') if video_match else current_time
            time_precision = 'video_position' if video_match else current_time_precision
            current = Message(clean_text(speaker), clean_text(content), line_number=line_number,
                              time_text=time_text, time_precision=time_precision, conversation_id=current_conversation)
            result.append(current)
            continue
        if line.startswith(('## ', '### ')):
            current_conversation = f'heading:{line_number}'
            current_time = clean_text(line.lstrip('# '))
            current_time, current_time_precision = _record_time({'datetime': current_time})
            current = None
            continue
        # 已知导出格式只收显式消息行，说明、日期标题不进入样本。
        if standard_export or line.startswith(("#", "来源视频", "已逐段", "核对版文件", "核对画面", "核对版：")):
            current = None
            continue
        if name_first.match(line):
            name = line.split()[0]
            current_time, current_time_precision = _record_time({'datetime': line[len(name):].strip()})
            current = Message(name, "", line_number=line_number, time_text=current_time,
                              time_precision=current_time_precision)
            result.append(current)
            continue
        match = date_first.match(line)
        if match:
            stamp = re.match(r'^([0-9]{4}[-/.][0-9]{1,2}[-/.][0-9]{1,2}(?: +[0-9]{1,2}:[0-9]{2}(?::[0-9]{2})?)?)', line.lstrip('['))
            if stamp:
                current_time, current_time_precision = _record_time({'datetime': stamp.group(1)})
            speaker, content = match.groups()
            # 过滤明显的系统行
            if speaker.lower() in {"system", "系统", "微信"} and content:
                current = None
                continue
            current = Message(clean_text(speaker), clean_text(content), line_number=line_number,
                              time_text=current_time, time_precision=current_time_precision)
            result.append(current)
        elif current and len(line) < 2000:
            current.content = clean_text(f"{current.content} {line}")
    return [m for m in result if m.speaker and m.content]


def parse_messages(path: Path) -> list[Message]:
    text = read_text(path)
    suffix = path.suffix.lower()
    if suffix == '.json':
        try:
            parsed = parse_json_messages(json.loads(text))
        except json.JSONDecodeError as exc:
            raise ValueError(f'{path.name} 不是有效的 JSON：第 {exc.lineno} 行。') from exc
    elif suffix == '.csv':
        try:
            parsed = parse_csv_messages(text)
        except csv.Error as exc:
            raise ValueError(f'{path.name} CSV 格式错误：{exc}') from exc
    else:
        if suffix in {'.html', '.htm'}:
            collector = TextCollector()
            collector.feed(text)
            text = chr(10).join(collector.parts)
        parsed = parse_lines(text)
    for index, message in enumerate(parsed, 1):
        message.source = str(path.resolve())
        message.source_number = index
        message.message_id = f'{_memory.source_prefix(message.source)}{index:04d}'
    return parsed


def merge_chat_paths(existing: list[Path], additions: list[Path]) -> list[Path]:
    result: list[Path] = []
    seen: set[Path] = set()
    for path in [*existing, *additions]:
        resolved = path.resolve()
        if resolved not in seen:
            seen.add(resolved)
            result.append(resolved)
    return result


def load_chat_files(paths: list[Path]) -> list[Message]:
    messages: list[Message] = []
    for path in paths:
        parsed = parse_messages(path)
        if not parsed:
            raise ValueError(f"{path.name} 没有识别出消息。请使用“说话人：内容”格式的 TXT。")
        messages.extend(parsed)
    return messages


def corpus_signature(messages: list[Message]) -> str:
    return _persona.corpus_signature(messages)

def select_style_sample(messages: list[Message], limit: int = 240) -> list[Message]:
    """每份文件分配样本，分段保留连续对话，避免仅取最后导入的文件。"""
    if limit <= 0:
        return []
    groups: dict[str, list[Message]] = {}
    for message in messages:
        groups.setdefault(message.source, []).append(message)
    budgets = {source: 0 for source in groups}
    remaining = min(limit, len(messages))
    while remaining:
        for source, group in groups.items():
            if remaining and budgets[source] < len(group):
                budgets[source] += 1
                remaining -= 1
    selected: list[Message] = []
    for source, group in groups.items():
        budget = budgets[source]
        if not budget:
            continue
        if budget == len(group):
            selected.extend(group)
            continue
        blocks = min(10, max(1, budget // 6))
        used = 0
        for i in range(blocks):
            size = budget // blocks + (i < budget % blocks)
            gap = (len(group) - budget) * i // (blocks - 1) if blocks > 1 else (len(group) - budget) // 2
            start = used + gap
            selected.extend(group[start:start + size])
            used += size
    return selected


def top_words(messages: list[Message], limit: int = 16) -> list[str]:
    return _persona.top_words(messages, limit)

def build_local_profile(messages: list[Message]) -> dict[str, object]:
    return _persona.build_local_profile(messages)

def profile_text(profile: dict[str, object]) -> str:
    return _persona.profile_text(profile)

def topic_profile_text(profile: dict[str, object]) -> str:
    return _persona.topic_profile_text(profile)

def fallback_soul(name: str, messages: list[Message]) -> str:
    return _persona.fallback_soul(name, messages)

def call_model(base_url: str, model: str, api_key: str, prompt: str) -> str:
    base_url = base_url.rstrip("/")
    if not base_url.endswith("/v1"):
        base_url += "/v1"
    endpoint = base_url + "/chat/completions"
    payload = {
        "model": model,
        "temperature": 0.4,
        "messages": [
            {"role": "system", "content": "你是一个人格档案整理器。只输出可直接保存为 SOUL.md 的中文 Markdown。不要泄露原始聊天记录，不要杜撰聊天中没有的事实。"},
            {"role": "user", "content": prompt},
        ],
    }
    request = Request(endpoint, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"), method="POST")
    request.add_header("Content-Type", "application/json")
    request.add_header("Authorization", f"Bearer {api_key}")
    with urlopen(request, timeout=90) as response:
        data = json.loads(response.read().decode("utf-8"))
    return str(data["choices"][0]["message"]["content"]).strip()


def call_vision_model(base_url: str, model: str, api_key: str, image_path: Path) -> str:
    """让兼容 OpenAI 图片输入的模型从一张聊天截图中提取文字。"""
    mime = mimetypes.guess_type(image_path.name)[0] or "image/png"
    image_data = base64.b64encode(image_path.read_bytes()).decode("ascii")
    data_url = f"data:{mime};base64,{image_data}"
    prompt = (
        "请读取这张聊天截图中的文字。按聊天气泡从上到下输出，每行一条，格式严格为：\n"
        "对方：消息内容\n我：消息内容\n未知：消息内容\n"
        "优先使用截图里显示的昵称；看不出说话人时使用未知。左侧气泡通常是对方，右侧气泡通常是我。"
        "跳过时间、按钮、系统通知和图片占位文字，不要总结，不要编造，不要加 Markdown。"
    )
    base_url = base_url.rstrip("/")
    if not base_url.endswith("/v1"):
        base_url += "/v1"
    payload = {
        "model": model,
        "temperature": 0,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": data_url}},
            ],
        }],
    }
    request = Request(
        base_url + "/chat/completions",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        method="POST",
    )
    request.add_header("Content-Type", "application/json")
    request.add_header("Authorization", f"Bearer {api_key}")
    with urlopen(request, timeout=120) as response:
        data = json.loads(response.read().decode("utf-8"))
    return message_content_text(data["choices"][0]["message"]["content"]).strip()


def parse_ocr_messages(text: str) -> list[Message]:
    """解析视觉模型按“说话人：内容”输出的截图文字。"""
    result: list[Message] = []
    speaker_re = re.compile(r"^\s*(?:\[([^\]]+)\]|([^:：]{1,30}))\s*[:：]\s*(.+?)\s*$")
    for raw in text.splitlines():
        line = clean_text(raw).strip("-•* ")
        if not line or line.startswith("```"):
            continue
        match = speaker_re.match(line)
        if not match:
            continue
        speaker = clean_text(match.group(1) or match.group(2) or "未知")
        content = clean_text(match.group(3))
        if content and speaker not in {"时间", "系统", "通知"}:
            result.append(Message(speaker, content))
    return result


def deduplicate_messages(messages: list[Message]) -> list[Message]:
    """去掉滚动截图之间常见的完全重复气泡。"""
    result: list[Message] = []
    for message in messages:
        if result and result[-1].speaker == message.speaker and result[-1].content == message.content:
            continue
        result.append(message)
    return result


def ocr_screenshot_messages(
    image_paths: list[Path],
    base_url: str,
    model: str,
    api_key: str,
    progress: Callable[[str], None] | None = None,
) -> list[Message]:
    """按文件名顺序批量识别截图；图片只在用户点击生成时发送给所选模型。"""
    messages: list[Message] = []
    seen_hashes: set[str] = set()
    import hashlib

    for index, path in enumerate(sorted(image_paths, key=lambda item: item.name.lower()), start=1):
        digest = hashlib.sha1(path.read_bytes()).hexdigest()
        if digest in seen_hashes:
            continue
        seen_hashes.add(digest)
        if progress:
            progress(f"正在识别截图 {index}/{len(image_paths)}：{path.name}")
        text = call_vision_model(base_url, model, api_key, path)
        messages.extend(parse_ocr_messages(text))
    return deduplicate_messages(messages)


def model_soul(name: str, messages: list[Message], base_url: str, model: str, api_key: str) -> str:
    reviewed = _persona.reference_soul(name, messages)
    if reviewed is not None:
        return reviewed
    prepared = _persona.prepare_persona_messages(messages)
    selected = select_style_sample(prepared, 480)
    prompt = _persona.generation_prompt(name, messages, selected)
    return call_model(base_url, model, api_key, redact_sensitive(prompt))


def prepare_retrieval_messages(messages: list[Message]) -> list[_memory.IndexedMessage]:
    # Number the complete source BEFORE the persona gate removes rows.
    indexed = _memory.build_index(messages)
    allowed = {id(m) for m in _persona.prepare_persona_messages(messages)}
    reviewed = _persona.load_reference(messages) is not None
    return [replace(row, review_status='approved' if reviewed else 'unreviewed')
            for message, row in zip(messages, indexed) if id(message) in allowed]


def write_persona_index(messages: list[Message]) -> Path:
    return _memory.write_index(prepare_retrieval_messages(messages), PERSONA_INDEX_FILE)


def retrieve_persona_context(messages: list[Message], query: str, limit: int = 5) -> str:
    hits = _memory.search(prepare_retrieval_messages(messages), query, limit=limit)
    return _memory.format_context_pack(hits, query)


def save_soul(content: str) -> Path:
    state = Path(os.environ.get("USERPROFILE", str(Path.home()))) / ".openclaw" / "workspace"
    state.mkdir(parents=True, exist_ok=True)
    target = state / "SOUL.md"
    backup = state / "SOUL.md.before-wechat-ai"
    had_target = target.exists()
    if had_target and not backup.exists():
        backup.write_bytes(target.read_bytes())
    elif not target.exists() and backup.exists():
        # 之前已经恢复过原文件，再次生成时继续保留那份原始备份。
        pass
    (state / "SOUL.md.wechat-ai-managed").write_text("backup" if had_target else "created", encoding="utf-8")
    target.write_text(content, encoding="utf-8")
    return target


def save_app_config(config: dict) -> None:
    safe = {key: config[key] for key in ('name', 'base_url', 'model', 'persona_id') if key in config}
    write_json(APP_CONFIG, safe)


def load_app_config() -> dict[str, str]:
    if not APP_CONFIG.exists():
        return {}
    try:
        value = json.loads(APP_CONFIG .read_text(encoding='utf-8-sig'))
        return value if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def load_accounts() -> list[dict[str, str]]:
    """读取本工具维护的账号别名，不把微信凭证复制到项目目录。"""
    if not ACCOUNTS_FILE.exists():
        return []
    try:
        value = json.loads(ACCOUNTS_FILE .read_text(encoding='utf-8-sig'))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(value, list):
        return []
    result: list[dict[str, str]] = []
    for item in value:
        if not isinstance(item, dict) or not item.get("id"):
            continue
        result.append({
            "id": str(item.get("id")),
            "name": str(item.get("name") or item.get("id")),
            "status": str(item.get("status") or "未绑定"),
            "last_action": str(item.get("last_action") or ""),
            "created_at": str(item.get("created_at") or ""),
            "provider_id": str(item.get("provider_id") or ""),
        })
    return result


def save_accounts(accounts: list[dict[str, str]]) -> None:
    write_json(ACCOUNTS_FILE, accounts)


def load_account_bindings() -> dict[str, str]:
    if not ACCOUNT_BINDINGS_FILE.exists():
        return {}
    try:
        value = json.loads(ACCOUNT_BINDINGS_FILE .read_text(encoding='utf-8-sig'))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(value, dict):
        return {}
    return {str(key): str(val) for key, val in value.items() if key and val}


def save_account_bindings(bindings: dict[str, str]) -> None:
    write_json(ACCOUNT_BINDINGS_FILE, bindings)


def openclaw_state_dir() -> Path:
    configured = os.environ.get("OPENCLAW_STATE_DIR", "").strip()
    return Path(configured) if configured else OPENCLAW_HOME


def openclaw_config_path() -> Path:
    configured = (os.environ.get('OPENCLAW_CONFIG_PATH') or os.environ.get('OPENCLAW_CONFIG') or '').strip()
    return Path(configured) if configured else openclaw_state_dir() / "openclaw.json"


def persona_store() -> PersonaStore:
    return PersonaStore(ROOT, openclaw_state_dir(), openclaw_config_path())


def account_is_enabled(provider_id: str) -> bool:
    """读取账号启停配置；没有显式配置时，插件默认启用。"""
    try:
        config = json.loads(openclaw_config_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return True
    if not isinstance(config, dict):
        return True
    channels = config.get("channels")
    if not isinstance(channels, dict):
        return True
    section = channels.get(WEIXIN_CHANNEL)
    if not isinstance(section, dict):
        return True
    accounts = section.get("accounts")
    if not isinstance(accounts, dict):
        return True
    entry = accounts.get(provider_id)
    return not isinstance(entry, dict) or entry.get("enabled") is not False


def provider_account_exists(provider_id: str) -> bool:
    if not provider_id:
        return False
    account_file = openclaw_state_dir() / WEIXIN_CHANNEL / "accounts" / f"{provider_id}.json"
    return account_file.exists()


def account_id_from_name(name: str, existing: set[str]) -> str:
    """生成适合 OpenClaw --account 参数的稳定别名。"""
    base = re.sub(r"[^A-Za-z0-9_-]+", "-", name.strip()).strip("-").lower()
    if not base:
        base = "user"
    candidate = base[:32]
    index = 2
    while candidate in existing:
        candidate = f"{base[:27]}-{index}"
        index += 1
    return candidate


def powershell_command(script: Path, *arguments: str) -> list[str]:
    return [
        "powershell",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(script),
        *arguments,
    ]


def openclaw_command() -> str:
    runtime = project_runtime(ROOT)
    if runtime:
        return str(runtime[1] / 'openclaw.cmd')
    return shutil.which("openclaw") or "openclaw"


def run_openclaw(arguments: list[str], timeout: float = 20) -> subprocess.CompletedProcess[str]:
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return subprocess.run(
        [openclaw_command(), *arguments],
        env=project_runtime_environment(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        creationflags=creationflags,
    )


def parse_json_from_output(output: str) -> object | None:
    """兼容 OpenClaw 在 JSON 前输出版本横幅的情况。"""
    text = output.strip()
    if not text:
        return None
    for start_char, end_char in (("{", "}"), ("[", "]")):
        start = text.find(start_char)
        end = text.rfind(end_char)
        if start >= 0 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                continue
    return None


def message_content_text(value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = item.get("text") or item.get("content")
                if isinstance(text, str):
                    parts.append(text)
        return "".join(parts)
    if isinstance(value, dict):
        text = value.get("text") or value.get("content")
        return text if isinstance(text, str) else ""
    return ""


def session_transcripts_for_account(account_ids: str | list[str]) -> list[Path]:
    ids = {account_ids} if isinstance(account_ids, str) else set(account_ids)
    store = persona_store()
    found = []
    for local_id, entry in store.assignments().items():
        if local_id in ids or (entry.get('provider_id') and entry['provider_id'] in ids):
            found.extend(store.transcripts(local_id))
    return sorted(set(found), key=lambda path: path.stat().st_mtime, reverse=True)


def format_transcript(path: Path, max_lines: int = 240) -> str:
    lines: list[str] = []
    try:
        raw_lines = path.read_text(encoding="utf-8", errors="replace").splitlines()[-max_lines:]
    except OSError as exc:
        return f"读取失败：{exc}"
    for raw in raw_lines:
        try:
            item = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if not isinstance(item, dict):
            continue
        message = item.get("message")
        if not isinstance(message, dict):
            continue
        role = str(message.get("role") or "event")
        text = message_content_text(message.get("content"))
        if text:
            label = {"user": "对方", "assistant": "AI", "system": "系统"}.get(role, role)
            lines.append(f"[{label}] {text.strip()}")
    return "\n\n".join(lines) if lines else "（这个会话暂时没有可显示的文字消息）"


COMPANION_PROFILE = ROOT / 'examples' / 'persona.md'


def read_persona_profile(path: Path) -> str:
    path = Path(path)
    if path.suffix.lower() not in {'.md', '.txt'}:
        raise ValueError('请选择已经整理好的 .md 或 .txt 人格文件。')
    data = path.read_bytes()
    encoding = 'utf-16' if data.startswith((bytes((255, 254)), bytes((254, 255)))) else 'utf-8-sig'
    try:
        profile = data.decode(encoding)
    except UnicodeError as exc:
        raise ValueError('无法读取人格文件的文字编码，请另存为 UTF-8 文本后重试。') from exc
    if chr(0) in profile:
        raise ValueError('请选择纯文本人格文件，图片和文档请先交给助手整理。')
    if not profile.strip():
        raise ValueError('陪伴设定文件为空，请先让我补全后再生成陪伴。')
    return profile.replace(chr(13) + chr(10), chr(10)).replace(chr(13), chr(10))


def prepared_companion_soul(name: str, profile_path: Path | None = None) -> str:
    safe_name = ' '.join(name.split())
    if not safe_name:
        raise ValueError('请填写陪伴名称。')
    try:
        profile = read_persona_profile(profile_path or COMPANION_PROFILE)
    except FileNotFoundError as exc:
        raise ValueError('还没有整理好的陪伴设定，请先让我整理好人格后再生成陪伴。') from exc
    if not profile.strip():
        raise ValueError('陪伴设定文件为空，请先让我补全后再生成陪伴。')
    profile = profile.lstrip()
    if profile.startswith('# '):
        profile = profile.partition(chr(10))[2]
    if profile_path:
        parts = profile.lstrip().splitlines(keepends=True)
        if parts and parts[0].startswith('你的陪伴称呼是「'):
            profile = ''.join(parts[1:]).lstrip(chr(10))
    if not profile.strip():
        raise ValueError('人格文件只有标题，没有具体设定，请先补全内容。')
    return chr(10).join([
        f'# {safe_name}', '',
        f'你的陪伴称呼是「{safe_name}」。日常用这个名字自然接话，不把身份说明当开场白。',
        '', profile,
    ])


class App(CompanionControls, MediaControls, VoiceControls, RuntimeControls, tk.Tk):
    def __init__(self) -> None:
        _display.enable_dpi_awareness()
        super().__init__()
        _display.configure_root(self)
        self.title("微信 AI 朋友 · 开源工作台")
        _display.size_window(self, 1200, 920, 860, 640)
        self.services = sys.modules[__name__]
        self._login_attempt = None
        self.current_persona_id = None
        self.selected_profile_path: Path | None = None
        self.draft_source_persona_id = None
        self.draft_source_name = None
        self.soul_path: Path | None = None
        self.config_data = load_app_config()
        self.accounts = load_accounts()
        self.remember_key_var = tk.BooleanVar(value=API_KEY_FILE.exists())
        self._build_ui()
        self.initialize_voice_controls()
        self._load_saved()
        self.initialize_companions()
        self.render_accounts()
        self.initialize_voice_runtime()
        self.start_runtime_monitor()

    def _build_ui(self) -> None:
        _ui.build_ui(self)

    def render_accounts(self) -> None:
        """Refresh account rows while retaining the current selection."""
        if not hasattr(self, "account_tree"):
            return
        selected = self.account_tree.selection()
        self.refresh_accounts_from_disk()
        for item in self.account_tree.get_children():
            self.account_tree.delete(item)
        for index, account in enumerate(self.accounts):
            view = account_status(account, getattr(self, '_runtime_snapshot', None))
            status = view.title
            tags = ["alternate"] if index % 2 else []
            if view.tone == 'active':
                tags.append("active")
            elif view.tone == 'failed':
                tags.append("failed")
            elif view.tone == 'waiting':
                tags.append("waiting")
            self.account_tree.insert("", "end", iid=account["id"],
                                     values=(account['name'], self.account_persona_name(account['id']), account['id'], status,
                                             view.detail if view.tone in ('waiting', 'failed') and view.detail else account['last_action']),
                                     tags=tags)
        retained = [item for item in selected if self.account_tree.exists(item)]
        if retained:
            self.account_tree.selection_set(retained)
        self.refresh_personas()
        _ui.refresh_ui_state(self)

    def refresh_accounts_from_disk(self) -> None:
        """把扫码脚本写入的真实账号 ID 和启停配置同步回界面。"""
        bindings = load_account_bindings()
        try:
            modern_state = isinstance(read_json(openclaw_config_path(), {}).get('agents', {}).get('entries'), dict)
        except (OSError, ValueError, AttributeError):
            modern_state = True  # Missing files are not evidence that a SQLite credential was removed.
        changed = False
        for account in self.accounts:
            provider_id = account.get("provider_id") or bindings.get(account["id"], "")
            if provider_id and account.get("provider_id") != provider_id:
                account["provider_id"] = provider_id
                changed = True
            if not provider_id:
                continue
            if modern_state:
                continue  # Runtime RPC reports authoritative account state; legacy JSON files do not.
            if provider_account_exists(provider_id):
                next_status = "已启用" if account_is_enabled(provider_id) else "已停用"
                if account.get("status") in {"等待扫码", "未绑定", "已绑定", "已启动", "已停用", "已启用"} and account.get("status") != next_status:
                    account["status"] = next_status
                    account["last_action"] = "从本机配置刷新"
                    changed = True
            elif account.get("status") != "未绑定":
                account["status"] = "未绑定"
                account["last_action"] = "未找到本机凭证"
                changed = True
        if changed:
            save_accounts(self.accounts)

    def selected_account(self) -> dict[str, str] | None:
        selected = self.account_tree.selection()
        if not selected:
            messagebox.showinfo("请选择账号", "先在账号列表中选中一个微信账号。")
            return None
        account_id = selected[0]
        return next((item for item in self.accounts if item["id"] == account_id), None)

    def update_account(self, account_id: str, **changes: str) -> None:
        for account in self.accounts:
            if account["id"] == account_id:
                account.update(changes)
                break
        save_accounts(self.accounts)
        self.render_accounts()

    @staticmethod
    def provider_account_id(account: dict[str, str]) -> str:
        return account.get("provider_id") or load_account_bindings().get(account["id"]) or ""


    def launch_login(self, account: dict[str, str]) -> None:
        attempt = getattr(self, '_login_attempt', None)
        if attempt and attempt[1].poll() is None:
            messagebox.showinfo('正在绑定', '请先完成或关闭当前扫码窗口，再重新扫码。', parent=self)
            return
        if not all(variable.get().strip() for variable in (self.base_url_var, self.model_var, self.key_var)):
            messagebox.showwarning('请先自行配置文本模型', '请填写自己使用的模型服务地址、模型名称和 API Key，再绑定微信。')
            return
        if account['id'] not in persona_store().assignments():
            messagebox.showwarning('未分配人格', '请先为此用户分配一个陪伴。')
            return
        script = ROOT / "scripts" / "启动微信ClawBot.ps1"
        if not script.exists():
            messagebox.showerror("文件缺失", f"找不到 {script}")
            return
        env = os.environ.copy()
        python_exe = Path(sys.executable)
        if python_exe.name.lower() == 'pythonw.exe':
            python_exe = python_exe.with_name('python.exe')
        env['WECHAT_AI_PYTHON'] = str(python_exe)
        env['PYTHONUTF8'] = '1'
        env['PYTHONIOENCODING'] = 'utf-8'
        env["WECHAT_AI_BASE_URL"] = self.base_url_var.get().strip()
        env["WECHAT_AI_MODEL"] = self.model_var.get().strip()
        env["WECHAT_AI_API_KEY"] = self.key_var.get().strip()
        self.write_log(f"正在为“{account['name']}”打开扫码窗口。请让该账号本人扫码并确认授权。")
        try:
            result_dir = DATA_DIR / 'login-results'
            result_dir.mkdir(parents=True, exist_ok=True)
            result_path = result_dir / (uuid.uuid4().hex + '.json')
            process = subprocess.Popen(
                powershell_command(script, '-AccountId', account['id'], '-ResultPath', str(result_path)),
                env=env,
                cwd=str(ROOT),
                creationflags=subprocess.CREATE_NEW_CONSOLE,
            )
            self._login_attempt = (account['id'], process, result_path)
            self.update_account(account["id"], status="等待扫码", last_action="扫码窗口已打开")
            threading.Thread(target=self._watch_login_result, args=(account['id'], process, result_path), daemon=True).start()
        except OSError as exc:
            self.update_account(account["id"], status="打开失败", last_action=str(exc))
            messagebox.showerror("打开失败", str(exc))

    def _watch_login_result(self, local_id: str, process, result_path: Path) -> None:
        """Only this launch's result can complete the QR attempt."""
        previous_message = None
        while True:
            exit_code = process.poll()
            try:
                result = json.loads(result_path.read_text(encoding='utf-8-sig'))
            except (OSError, ValueError):
                result = {}
            if not isinstance(result, dict) or result.get('local_id') != local_id:
                result = {}
            status = result.get('status')
            message = str(result.get('message') or '')
            if status in ('bound', 'succeeded') and result.get('binding_saved') is True:
                actual_id = load_account_bindings().get(local_id)
                if actual_id and actual_id == result.get('provider_id') and provider_account_exists(actual_id):
                    self.after(0, lambda value=actual_id, detail=message: self.update_account(local_id, provider_id=value, status='已绑定', last_action=detail))
                    self.ui_log(message or f'账号 {local_id} 已绑定，后台仍在准备。')
                    return
                status, message = 'failed', '本次扫码的绑定记录不完整，请检查账号映射后重试。'
            if status == 'succeeded':
                actual_id = load_account_bindings().get(local_id)
                if actual_id:
                    self.after(0, lambda value=actual_id: self.update_account(local_id, provider_id=value, status='已绑定', last_action='扫码及专属人格配置完成'))
                    self.ui_log(f'账号 {local_id} 已完成绑定。')
                    return
                status, message = 'failed', '扫码脚本已结束，但未保存真实微信账号映射，请重新扫码。'
            if status == 'failed' or exit_code is not None:
                if status != 'failed':
                    message = f'扫码窗口提前结束（退出码 {exit_code}），未完成本次绑定。请点击重新扫码。'
                elif not message:
                    message = '绑定失败，请点击重新扫码。'
                self.after(0, lambda error=message: self.update_account(local_id, status='绑定失败', last_action=error))
                self.ui_log(f'账号 {local_id} 绑定失败：{message}')
                return
            if status == 'running' and message and message != previous_message:
                previous_message = message
                self.ui_log(message)
            time.sleep(1)

    def toggle_selected_account(self, enabled: bool) -> None:
        account = self.selected_account()
        if not account:
            return
        provider_id = self.provider_account_id(account)
        if not provider_id:
            messagebox.showinfo("还没有完成绑定", "请先点击“添加账号并生成二维码”，让对方完成扫码。")
            return
        script = ROOT / "scripts" / "切换微信账号.ps1"
        if not script.exists():
            messagebox.showerror("文件缺失", f"找不到 {script}")
            return
        if enabled:
            try:
                binding = persona_store().assignments().get(account['id'])
                if not binding:
                    raise ValueError('请先为此账号分配人格。')
                persona_store().assign(account['id'], binding['persona_id'], provider_id)
            except (OSError, ValueError) as exc:
                messagebox.showerror('启动失败', str(exc))
                return
        action = 'start' if enabled else 'stop' 
        self.update_account(account["id"], provider_id=provider_id, status="启动中" if enabled else "停用中", last_action=f"正在{('启动' if enabled else '停用')}并重载网关")
        self.write_log(f"正在{('启动' if enabled else '停用')}账号“{account['name']}”。")
        threading.Thread(target=self._toggle_account_worker, args=(account["id"], provider_id, action, script), daemon=True).start()

    def _toggle_account_worker(self, local_id: str, provider_id: str, action: str, script: Path) -> None:
        try:
            result = subprocess.run(
                powershell_command(script, "-AccountId", provider_id, "-Action", action),
                cwd=str(ROOT),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=90,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            if result.returncode != 0:
                raise RuntimeError((result.stderr or result.stdout or "PowerShell 执行失败").strip())
            enabled = action == "start"
            self.after(0, lambda: self.update_account(local_id, status="已启动" if enabled else "已停用", last_action=datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
            self.ui_log(f"账号 {local_id} 已{('启动' if enabled else '停用')}。")
        except Exception as exc:  # noqa: BLE001
            error_text = str(exc)
            self.after(0, lambda: self.update_account(local_id, status="操作失败", last_action=error_text))
            self.ui_log(f"账号 {local_id} 操作失败：{exc}")

    def view_selected_logs(self) -> None:
        account = self.selected_account()
        if not account:
            return
        ids = [account["id"]]
        provider_id = self.provider_account_id(account)
        if provider_id:
            ids.append(provider_id)
        paths = session_transcripts_for_account(ids)
        _ui.build_history_window(self, account, paths)

    def refresh_log_window(self, window: tk.Toplevel, text: tk.Text, account_id: str) -> None:
        if not window.winfo_exists():
            return
        text.configure(state="normal")
        text.delete("1.0", END)
        account = next((item for item in self.accounts if item["id"] == account_id), None)
        ids = [account_id] if not account else [account_id, self.provider_account_id(account)]
        text.insert("1.0", self.compose_account_logs(session_transcripts_for_account(ids)))
        text.configure(state="disabled")

    @staticmethod
    def compose_account_logs(paths: list[Path]) -> str:
        if not paths:
            return "尚未找到这个账号的聊天记录。\n\n请先完成扫码，并至少发送一条消息。记录由 OpenClaw 保存在本机。"
        parts = [f"===== {path.name} =====\n{format_transcript(path)}" for path in paths[:10]]
        return "\n\n".join(parts)

    @staticmethod
    def open_sessions_directory() -> None:
        directory = OPENCLAW_HOME / "agents"
        directory.mkdir(parents=True, exist_ok=True)
        os.startfile(directory)  # type: ignore[attr-defined]

    def remove_selected_account(self) -> None:
        account = self.selected_account()
        if not account:
            return
        pending=getattr(self,'_removing_accounts',set())
        if account['id'] in pending:
            messagebox.showinfo('正在解除绑定','请等待当前账号解除完成。',parent=self)
            return
        if not messagebox.askyesno(
            "解除绑定",
            f"确定解除“{account['name']}”的微信绑定并从列表移除吗？\n\n这会删除本机保存的登录凭证；聊天记录不会自动删除。",
        ):
            return
        script = ROOT / "scripts" / "移除微信账号.ps1"
        if not script.exists():
            messagebox.showerror("文件缺失", f"找不到 {script}")
            return
        provider_id = self.provider_account_id(account) or account["id"]
        self.write_log(f"正在解除账号“{account['name']}”的绑定……")
        self._removing_accounts=pending
        pending.add(account['id'])
        threading.Thread(target=self._remove_account_worker, args=(account, provider_id, script), daemon=True).start()

    def _remove_account_worker(self, account: dict[str, str], provider_id: str, script: Path) -> None:
        try:
            result = subprocess.run(
                powershell_command(script, "-AccountId", provider_id, "-LocalId", account['id']),
                cwd=str(ROOT),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=90,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                env=dict(os.environ,WECHAT_AI_PYTHON=str(Path(sys.executable).with_name('python.exe'))),
            )
            if result.returncode != 0:
                raise RuntimeError((result.stderr or result.stdout or "PowerShell 执行失败").strip())
            if not (ROOT/'scripts/unbind_account.py').is_file():
                persona_store().detach(account['id'])
                self.accounts = [item for item in self.accounts if item['id'] != account['id']]
                save_accounts(self.accounts)
                bindings = load_account_bindings()
                bindings.pop(account["id"], None)
                save_account_bindings(bindings)
            self.ui_log(f"账号 {account['name']} 已解除绑定并移除。聊天记录仍保留在本机。")
            def refresh():
                self.accounts=load_accounts()
                self.render_accounts()
                self.refresh_personas(getattr(self,'current_persona_id',None))
            self.after(0, refresh)
        except Exception as exc:  # noqa: BLE001
            self.ui_log(f"解除账号失败：{exc}")
        finally:
            getattr(self,'_removing_accounts',set()).discard(account['id'])

    @staticmethod
    def _row(parent: ttk.Widget, row: int, label: str, variable: tk.StringVar, show: str | None) -> None:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky=W, padx=10, pady=7)
        entry = ttk.Entry(parent, textvariable=variable, show=show or "")
        entry.grid(row=row, column=1, columnspan=2, sticky="ew", padx=10, pady=7)
        parent.columnconfigure(1, weight=1)

    def _load_saved(self) -> None:
        for var, key in ((self.name_var, "name"), (self.base_url_var, "base_url"), (self.model_var, "model")):
            if self.config_data.get(key):
                var.set(self.config_data[key])
        if API_KEY_FILE.exists():
            try:
                self.key_var.set(_unprotect_secret(API_KEY_FILE.read_bytes()).decode("utf-8"))
            except (OSError, ValueError, UnicodeDecodeError):
                self.remember_key_var.set(False)

    def write_log(self, text: str) -> None:
        _ui.append_log(self, text)

    def ui_log(self, text: str) -> None:
        """从后台线程安全地写日志。"""
        self.after(0, lambda: self.write_log(text))



    def _busy(self, busy: bool) -> None:
        self.after(0, lambda: _ui.set_busy(self, busy))


    def rollback_wechat(self) -> None:
        if not messagebox.askyesno("停用并恢复", "将停止所有微信账号，并恢复生成陪伴前的 SOUL.md。确定继续吗？"):
            return
        script = ROOT / "scripts" / "停止并恢复微信AI.ps1"
        if not script.exists():
            messagebox.showerror("文件缺失", f"找不到 {script}")
            return
        self.write_log("正在停用微信通道并恢复原人格文件……")
        subprocess.Popen(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)], cwd=str(ROOT), creationflags=subprocess.CREATE_NEW_CONSOLE)


if __name__ == "__main__":
    workbench = App()
    workbench.update_idletasks()
    if os.environ.get('WECHAT_AI_STARTUP_READY') == '1':
        print('__WECHAT_WORKBENCH_READY__', flush=True)
    workbench.mainloop()
