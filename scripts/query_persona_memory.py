"""Query the local persona index without contacting a model service."""
from __future__ import annotations
import argparse
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import persona_memory as memory

def main() -> int:
    if not sys.stdout.isatty():
        sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description='从本地人格索引检索有来源的历史片段')
    parser.add_argument('query', help='当前消息或检索关键词')
    parser.add_argument('--index', type=Path, default=ROOT / 'data' / 'persona-memory.json')
    parser.add_argument('--limit', type=int, default=5)
    parser.add_argument('--approved-only', action='store_true', help='只检索已复核片段，与模型试聊一致')
    args = parser.parse_args()
    try:
        indexed = memory.read_index(args.index)
    except (OSError, TypeError, ValueError) as exc:
        parser.error(f'索引读取失败：{exc}。请在工作台生成一次人格以更新索引。')
    hits = memory.search(indexed, args.query, limit=max(0, args.limit), approved_only=args.approved_only)
    sys.stdout.write(memory.format_context_pack(hits, args.query))
    return 0

if __name__ == '__main__':
    raise SystemExit(main())

