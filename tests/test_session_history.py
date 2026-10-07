import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
import tempfile
import unittest

from session_history import read_only, sqlite_transcripts


class SessionHistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.database = self.root / 'openclaw-agent.sqlite'
        self.db = sqlite3.connect(self.database)
        self.addCleanup(self.db.close)
        self.db.executescript('CREATE TABLE session_nodes(session_key TEXT, current_session_id TEXT, updated_at INTEGER, entry_valid INTEGER);'
                              'CREATE TABLE transcript_events(session_id TEXT, seq INTEGER, event_json TEXT, event_zstd BLOB, event_utf8_bytes INTEGER);')
        self.db.executemany('INSERT INTO session_nodes VALUES(?,?,?,1)', [('agent:owner:wechat', 'own', 2000), ('agent:other:wechat', 'other', 1000)])
        self.db.executemany('INSERT INTO transcript_events VALUES(?,?,?,NULL,NULL)', [('own', 1, json.dumps({'message': {'content': 'mine'}})), ('other', 1, json.dumps({'message': {'content': 'private'}}))])
        self.db.commit()

    def test_only_selected_agent_history_is_returned(self):
        records = sqlite_transcripts(self.database, 'owner', self.root)
        self.assertEqual([r.name for r in records], ['own.jsonl'])
        self.assertEqual(records[0].stat().st_mtime, 2)
        self.assertIn('mine', records[0].read_text())
        self.assertNotIn('private', records[0].read_text())
        self.assertEqual(sqlite_transcripts(self.database, 'unknown', self.root), [])

    def test_database_connection_rejects_writes(self):
        with read_only(self.database) as db:
            with self.assertRaises(sqlite3.OperationalError):
                db.execute('DELETE FROM session_nodes')
        self.assertEqual(self.db.execute('SELECT count(*) FROM session_nodes').fetchone()[0], 2)

    def test_latest_events_are_chronological_and_bounded(self):
        self.db.executemany('INSERT INTO transcript_events VALUES(?,?,?,NULL,NULL)', [('own', n, str(n)) for n in range(2, 302)])
        self.db.commit()
        lines = sqlite_transcripts(self.database, 'owner', self.root)[0].read_text().splitlines()
        self.assertEqual((len(lines), lines[0], lines[-1]), (240, '62', '301'))

    @unittest.skipUnless(shutil.which('node'), 'Node runtime unavailable')
    def test_compressed_event_is_decoded_without_mutating_store(self):
        source = json.dumps({'message': {'content': '压缩记录'}}, ensure_ascii=False)
        code = 'const z=require(\'node:zlib\');process.stdout.write(z.zstdCompressSync(Buffer.from(process.argv[1])))'
        packed = subprocess.run([shutil.which('node'), '-e', code, source], capture_output=True, check=True).stdout
        self.db.execute('INSERT INTO transcript_events VALUES(?,2,NULL,?,?)', ('own', packed, len(source.encode('utf-8'))))
        self.db.commit()
        self.assertIn('压缩记录', sqlite_transcripts(self.database, 'owner', self.root)[0].read_text())
        self.assertEqual(self.db.execute('SELECT count(*) FROM transcript_events').fetchone()[0], 3)
