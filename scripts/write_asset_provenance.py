"""Record the exact original inputs, licenses and checksums of demo assets."""
from pathlib import Path
import hashlib
import json
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def write_provenance():
    assets = []
    for relative in ('examples/persona.md', 'examples/demo_chat.md'):
        path = ROOT / relative
        assets.append({'file': relative, 'license': 'MIT', 'source': 'newly-written-fictional-example',
                       'uses_private_conversations': False, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()})
    stickers = json.loads((ROOT / 'assets/stickers/builtin.json').read_text(encoding='utf-8'))
    for sticker in stickers:
        for relative in ('assets/stickers/' + sticker['file'], 'assets/stickers/' + sticker['source_svg']):
            path = ROOT / relative
            assets.append({'file': relative, 'license': 'MIT', 'source': 'original-procedural-geometry',
                'generator': 'scripts/generate_builtin_stickers.py', 'uses_external_artwork': False,
                'uses_fonts': False, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()})
    record = {'version': 1, 'source_policy': 'Only original fictional examples and procedural artwork.',
              'assets': assets}
    output = ROOT / 'assets/ASSET_PROVENANCE.json'
    output.write_text(json.dumps(record, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return output


if __name__ == '__main__':
    print(write_provenance())
