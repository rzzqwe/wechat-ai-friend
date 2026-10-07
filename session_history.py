from dataclasses import dataclass
from contextlib import contextmanager
import base64
import json
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
from types import SimpleNamespace

from runtime_tuning import project_runtime


@contextmanager
def read_only(database):
    connection = sqlite3.connect(database.resolve().as_uri() + '?mode=ro', uri=True, timeout=2)
    try:
        connection.execute('PRAGMA query_only=ON')
        yield connection
    finally:
        connection.close()


@dataclass(frozen=True)
class SQLiteTranscript:
    database: Path
    session_id: str
    updated_at: int
    node_executable: str | None

    @property
    def name(self):
        return self.session_id + '.jsonl'

    def stat(self):
        return SimpleNamespace(st_mtime=self.updated_at / 1000)

    def read_text(self, encoding='utf-8', errors='strict'):
        try:
            with read_only(self.database) as db:
                rows = list(reversed(db.execute(
                    'SELECT event_json, event_zstd, event_utf8_bytes FROM transcript_events '
                    'WHERE session_id=? ORDER BY seq DESC LIMIT 240', (self.session_id,)).fetchall()))
            compressed = [{'data': base64.b64encode(row[1]).decode('ascii'), 'size': row[2]}
                          for row in rows if row[0] is None and row[1] is not None]
            decoded = iter(())
            if compressed:
                if not self.node_executable:
                    raise OSError('读取压缩聊天记录需要项目中的 Node 运行环境。')
                payload = json.dumps(compressed)
                if len(payload) > 24 * 1024 * 1024:
                    raise OSError('聊天记录片段过大，未读取。')
                result = subprocess.run(
                    [self.node_executable, str(Path(__file__).parent / 'scripts/decode_transcript_zstd.cjs')],
                    input=payload, capture_output=True, text=True, encoding='utf-8',
                    timeout=15, check=True, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                decoded = iter(json.loads(result.stdout))
            return chr(10).join(row[0] if row[0] is not None else next(decoded) for row in rows)
        except (sqlite3.Error, subprocess.SubprocessError, ValueError, StopIteration) as exc:
            raise OSError('无法读取新版聊天记录：' + type(exc).__name__) from exc


def sqlite_transcripts(database: Path, agent_id: str, project_root: Path):
    runtime = project_runtime(project_root)
    node = str(runtime[0] / 'node.exe') if runtime else shutil.which('node')
    prefix = 'agent:' + agent_id + ':'
    with read_only(database) as db:
        rows = db.execute(
            'SELECT current_session_id, updated_at FROM session_nodes '
            'WHERE substr(session_key,1,?)=? AND entry_valid>=0 ORDER BY updated_at DESC',
            (len(prefix), prefix)).fetchall()
    return [SQLiteTranscript(database, sid, updated, node) for sid, updated in rows
            if isinstance(sid, str) and re.fullmatch('[A-Za-z0-9_-]+', sid)]
