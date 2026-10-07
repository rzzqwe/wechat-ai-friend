from dataclasses import replace
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import persona_memory as memory
import persona_support as support
import wechat_bot_app as app


class RetrievalBoundaryTests(unittest.TestCase):
    def test_text_exports_keep_timestamps_without_changing_speakers(self):
        text = chr(10).join(['2026-09-29 12:00 对方：积木到了', '我 2026/09/29 12:01', '零件还在袋子里'])
        rows = app.parse_lines(text)
        self.assertEqual([(r.speaker, r.content) for r in rows], [('对方', '积木到了'), ('我', '零件还在袋子里')])
        self.assertEqual([r.time_precision for r in rows], ['datetime', 'datetime'])
        self.assertEqual(rows[0].time_text, '2026-09-29 12:00')
        self.assertEqual(rows[1].time_text, '2026/09/29 12:01')

    def test_skipped_lines_are_not_presented_as_contiguous(self):
        rows = [app.Message('我', '零件找不到', 'a', line_number=1), app.Message('对方', '翻翻袋子', 'a', line_number=4)]
        self.assertEqual(memory.search(rows, '零件'), [])

    def test_no_match_does_not_receive_role_bonus(self):
        self.assertEqual(memory.search([app.Message('对方', '晚安', 'a')], '火星量子计算'), [])

    def test_tokens_never_bridge_punctuation(self):
        self.assertNotIn('木先', memory.tokenize('积木，先休息'))

    def test_no_future_message_enters_context_or_score(self):
        rows = [app.Message('对方', '先休息', 'a'), app.Message('我', '量子计算', 'a')]
        self.assertEqual(memory.search(rows, '量子计算'), [])
        hit = memory.search(rows, '先休息')[0]
        self.assertEqual(len(hit.context), 1)

    def test_source_gap_date_and_conversation_boundaries(self):
        first, last = memory.build_index([app.Message('我', '零件找不到', 'a'), app.Message('对方', '翻翻袋子', 'a')])
        pairs = [(first, replace(last, source='b')),
                 (first, replace(last, source_number=3)),
                 (replace(first, conversation_id='1'), replace(last, conversation_id='2')),
                 (replace(first, time_text='2026-01-01', time_precision='date'),
                  replace(last, time_text='2026-01-02', time_precision='date')),
                 (replace(first, time_text='2026-01-01 12:00', time_precision='datetime'),
                  replace(last, time_text='2026-01-01 18:00', time_precision='datetime'))]
        for pair in pairs:
            with self.subTest(pair=pair):
                self.assertEqual(memory.search(pair, '零件'), [])

    def test_filter_keeps_original_numbers_and_review_labels(self):
        rows = app.prepare_retrieval_messages([
            app.Message('我', '零件找不到', 'a'),
            app.Message('对方', '@全体成员 通知', 'a'),
            app.Message('对方', '翻翻袋子', 'a')])
        self.assertEqual([row.source_number for row in rows], [1, 3])
        self.assertEqual({row.review_status for row in rows}, {'unreviewed'})
        self.assertEqual(memory.search(rows, '零件'), [])


    def test_generated_excluded_uncertain_and_unreviewed_are_not_model_evidence(self):
        original = memory.build_index([app.Message('对方', '积木到了', 'a')])[0]
        for row in [replace(original, origin='ai_reply', review_status='approved'),
                    replace(original, review_status='excluded'), replace(original, review_status='uncertain'), original]:
            with self.subTest(row=row):
                self.assertEqual(memory.search([row], '积木', approved_only=True), [])

    def test_generic_source_names_do_not_collide(self):
        rows = memory.build_index([app.Message('对方', '甲', 'one/chat.txt'), app.Message('对方', '乙', 'two/chat.txt')])
        self.assertNotEqual(rows[0].message_id, rows[1].message_id)

    def test_output_length_limits_complete_snippets(self):
        hits = memory.search([app.Message('对方', '积木' * 2000, 'a')], '积木')
        for limit in (0, 10, 300, 900):
            result = memory.format_context_pack(hits, '积木', max_chars=limit)
            self.assertLessEqual(len(result), limit)
            self.assertNotIn('积木' * 20, result)

    def test_failed_atomic_write_preserves_previous_index(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'index.json'
            path.write_text('previous', encoding='utf-8')
            with patch.object(Path, 'replace', side_effect=OSError('locked')), self.assertRaises(OSError):
                memory.write_index([], path)
            self.assertEqual(path.read_text(encoding='utf-8'), 'previous')
            self.assertEqual(list(Path(directory).glob('*.tmp')), [])

    def test_index_round_trip_and_version_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            path = memory.write_index([app.Message('对方', '你好', 'a')], Path(directory) / 'index.json')
            self.assertEqual(memory.read_index(path)[0].content, '你好')
            path.write_text(json.dumps(dict(version=1, messages=[])), encoding='utf-8')
            with self.assertRaises(ValueError):
                memory.read_index(path)

    def test_malformed_structured_inputs_do_not_become_chat_lines(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'bad.json'
            path.write_text('对方：不是JSON', encoding='utf-8')
            with self.assertRaises(ValueError):
                app.parse_messages(path)
            path = Path(directory) / 'bad.csv'
            path.write_text('对方：不是CSV', encoding='utf-8')
            self.assertEqual(app.parse_messages(path), [])

    def test_side_only_csv_and_zero_content(self):
        rows = app.parse_csv_messages(chr(65279) + 'side,content' + chr(10) + 'left,0')
        self.assertEqual((rows[0].speaker, rows[0].screen_side, rows[0].content), ('对方', 'left', '0'))
        self.assertEqual(app.Message('左侧', '我不想聊').screen_side, 'left')

    def test_video_position_is_not_real_time(self):
        rows = app.parse_json_messages([dict(speaker='对方', content='好', 视频时间='00:02'),
                                        dict(speaker='对方', content='好', 时间='12:03', 视频时间='')])
        self.assertEqual([row.time_precision for row in rows], ['video_position', 'time'])
