import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import persona_memory as memory
import wechat_bot_app as app


class PersonaMemoryTests(unittest.TestCase):
    def test_csv_import_preserves_provenance_and_bubble_side(self):
        text = "序号,日期,时间,发送方,内容,原文行号,时间段序号\n1,04/17,22:20,我方,先休息,7,1\n2,04/17,22:20,对方,积木到了,8,1\n"
        parsed = app.parse_csv_messages(text)
        self.assertEqual([item.speaker for item in parsed], ['我', '对方'])
        self.assertEqual(parsed[1].time_text, '04/17 22:20')
        self.assertEqual(parsed[1].time_precision, 'partial_datetime')
        self.assertEqual(parsed[1].conversation_id, '1')
        self.assertEqual(parsed[1].line_number, 3)

    def test_json_bubble_position_overrides_sender_and_keeps_metadata(self):
        parsed = app.parse_json_messages([{
            'id': 'row-7', '发送方': '我方', 'side': 'left', '内容': '左侧内容',
            '视频时间': '00:12', '时间段序号': 3,
        }])
        self.assertEqual(parsed[0].speaker, '对方')
        self.assertEqual(parsed[0].screen_side, 'left')
        self.assertEqual(parsed[0].message_id, 'row-7')
        self.assertEqual(parsed[0].time_precision, 'video_position')
        self.assertEqual(parsed[0].conversation_id, '3')

    def test_index_qualifies_repeated_numeric_ids(self):
        messages = [
            app.Message('对方', '第一条', '微信聊天记录.csv', message_id='W0031'),
            app.Message('对方', '第二条', '抖音聊天记录.csv', message_id='1'),
        ]
        indexed = memory.build_index(messages)
        self.assertEqual([item.message_id for item in indexed], ['W0031', 'D0001'])
        self.assertEqual([item.source_number for item in indexed], [31, 1])

    def test_search_returns_target_and_nearby_context_with_source_id(self):
        messages = [
            app.Message('我', '积木到了', 'chat.txt'),
            app.Message('对方', '先按颜色分一下', 'chat.txt'),
            app.Message('我', '有块零件找不到', 'chat.txt'),
            app.Message('对方', '翻翻袋子角落', 'chat.txt'),
        ]
        hits = memory.search(messages, '零件找不到', limit=1)
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0].message.content, '翻翻袋子角落')
        self.assertEqual([item.source_number for item in hits[0].context], [3, 4])
        pack = memory.format_context_pack(hits, '零件找不到')
        self.assertIn('目标回复来源：' + hits[0].message.message_id, pack)
        self.assertIn('来源编号', pack)

    def test_write_index_is_atomic_and_machine_readable(self):
        messages = [app.Message('对方', '你好', 'chat.txt')]
        with tempfile.TemporaryDirectory() as directory:
            path = memory.write_index(messages, Path(directory) / 'persona-memory.json')
            payload = json.loads(path.read_text(encoding='utf-8'))
            self.assertEqual(payload['version'], memory.INDEX_VERSION)
            self.assertEqual(payload['message_count'], 1)
            self.assertEqual(payload['messages'][0]['message_id'], memory.source_prefix('chat.txt') + '0001')
            self.assertFalse(list(Path(directory).glob('*.tmp')))

    def test_app_index_reuses_persona_noise_gate(self):
        messages = [
            app.Message('对方', '@全体成员 系统通知', 'chat.txt'),
            app.Message('对方', '好呀', 'chat.txt'),
            app.Message('未知', '不确定归属', 'chat.txt'),
            app.Message('我', '触发语境', 'chat.txt'),
        ]
        with tempfile.TemporaryDirectory() as directory, patch.object(app, 'PERSONA_INDEX_FILE', Path(directory) / 'index.json'):
            path = app.write_persona_index(messages)
            rows = json.loads(path.read_text(encoding='utf-8'))['messages']
            self.assertEqual([row['content'] for row in rows], ['好呀', '触发语境'])



if __name__ == '__main__':
    unittest.main()
