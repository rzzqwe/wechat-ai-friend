import json
from pathlib import Path
import tempfile
import unittest

from scripts.generate_builtin_stickers import generate

ROOT = Path(__file__).resolve().parents[1]


class BuiltinStickerTests(unittest.TestCase):
    def test_original_stickers_regenerate_from_project_geometry(self):
        with tempfile.TemporaryDirectory() as folder:
            records = generate(Path(folder))
            self.assertEqual(len(records), 6)
            committed = json.loads((ROOT / 'assets/stickers/builtin.json').read_text(encoding='utf-8'))
            self.assertEqual([r['sha256'] for r in records], [r['sha256'] for r in committed])
            for record in records:
                self.assertEqual(record['license'], 'MIT')
                self.assertFalse(record['uses_external_artwork'])
                self.assertFalse(record['uses_fonts'])


if __name__ == '__main__':
    unittest.main()
