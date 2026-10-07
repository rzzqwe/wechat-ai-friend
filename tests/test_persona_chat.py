from dataclasses import replace
import io
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import persona_chat as chat
import persona_memory as memory
import wechat_bot_app as app


class TrialSessionTests(unittest.TestCase):
    def test_grounding_follows_reference_data_in_both_modes(self):
        for retrieval in (False, True):
            turn = self.session().prepare('积木', use_retrieval=retrieval)
            system_messages = [m for m in turn.request_messages if m['role'] == 'system']
            self.assertEqual(len(system_messages), 1)
            self.assertTrue(system_messages[-1]['content'].endswith(chat.FACTUAL_REPLY_RULES))
            self.assertIn(turn.evidence, system_messages[-1]['content'])
            self.assertNotIn(chat.FACTUAL_REPLY_RULES, turn.evidence)

    def session(self):
        rows = memory.build_index([app.Message('我', '积木零件找不到', 'a'), app.Message('对方', '翻翻袋子', 'a')])
        return chat.TrialSession('只参考表达，号码13900001111', [replace(row, review_status='approved') for row in rows], app.redact_sensitive)

    def test_request_contains_retrieved_examples_and_actual_history(self):
        session = self.session()
        turn = session.prepare('积木零件')
        self.assertEqual(len(turn.matched_ids), 1)
        self.assertIn('翻翻袋子', turn.evidence)
        session.complete(turn, '看看袋子角落')
        next_turn = session.prepare('找到了')
        self.assertEqual([m['role'] for m in next_turn.request_messages], ['system', 'user', 'assistant', 'user'])
        self.assertEqual(next_turn.request_messages[-2]['content'], '看看袋子角落')
        self.assertNotIn('看看袋子角落', [row.content for row in session.evidence])

    def test_static_mode_and_no_match(self):
        session = self.session()
        self.assertEqual(session.prepare('积木', use_retrieval=False).matched_ids, ())
        turn = session.prepare('火星量子计算')
        self.assertEqual(turn.matched_ids, ())
        self.assertIn('无相关历史匹配', turn.evidence)

    def test_all_request_fields_are_redacted(self):
        session = self.session()
        first = session.prepare('邮箱 someone@example.com')
        session.complete(first, '号码13900001111')
        request = json.dumps(session.prepare('身份证110101199001011234').request_messages, ensure_ascii=False)
        for secret in ('someone@example.com', '13900001111', '110101199001011234'):
            self.assertNotIn(secret, request)
        self.assertIn('[手机号]', request)

    def test_reset_rejects_inflight_reply_and_sessions_are_independent(self):
        one, two = self.session(), self.session()
        turn = one.prepare('积木')
        one.reset()
        with self.assertRaises(ValueError):
            one.complete(turn, '迟到的回复')
        self.assertEqual(one.turns, [])
        self.assertEqual(two.turns, [])

    def test_history_bound_keeps_complete_turns(self):
        session = self.session()
        for i in range(15):
            session.complete(session.prepare(f'问题{i}'), f'回复{i}')
        self.assertEqual(len(session.turns), chat.HISTORY_TURNS)
        request = session.prepare('继续').request_messages
        self.assertEqual(request[1]['content'], '问题3')
        self.assertEqual(request[-2]['content'], '回复14')

    def test_invalid_query_or_reply_does_not_mutate_history(self):
        session = self.session()
        for query in (' ', '甲' * 2001):
            with self.assertRaises(ValueError):
                session.prepare(query)
        with self.assertRaises(ValueError):
            session.complete(session.prepare('你好'), '')
        self.assertEqual(session.turns, [])

    def test_unreviewed_text_is_not_injected(self):
        rows = memory.build_index([app.Message('对方', '未核验的积木原话', 'a')])
        session = chat.TrialSession('保守初稿', rows)
        self.assertNotIn('未核验的积木原话', str(session.prepare('积木').request_messages))

    def test_compatible_api_payload_and_reply(self):
        raw = json.dumps(dict(choices=[dict(message=dict(content='模型回复'))])).encode()
        with patch.object(chat, 'urlopen', return_value=io.BytesIO(raw)) as opening:
            reply = chat.call_reply('http://localhost:8080/v1', 'local-model', 'secret', self.session().prepare('积木').request_messages)
        self.assertEqual(reply, '模型回复')
        request = opening.call_args.args[0]
        self.assertEqual(request.full_url, 'http://localhost:8080/v1/chat/completions')
        self.assertEqual(request.get_header('Authorization'), 'Bearer secret')
        self.assertEqual(json.loads(request.data)['messages'][-1]['content'], '积木')
        self.assertNotIn('thinking', json.loads(request.data))

    def test_deepseek_flash_uses_explicit_non_thinking_chat_mode(self):
        raw = json.dumps(dict(choices=[dict(message=dict(content='小气'))])).encode()
        with patch.object(chat, 'urlopen', return_value=io.BytesIO(raw)) as opening:
            chat.call_reply('https://api.deepseek.com', 'deepseek-flash', 'secret', [])
        payload = json.loads(opening.call_args.args[0].data)
        self.assertEqual(payload['thinking'], {'type': 'disabled'})
        self.assertEqual(payload['temperature'], 0.4)

    def test_api_failures_are_clear_and_do_not_expose_credentials(self):
        session = self.session()
        turn = session.prepare('积木')
        error = HTTPError('http://localhost', 401, 'private-secret', {}, None)
        with patch.object(chat, 'urlopen', side_effect=error), self.assertRaises(ValueError) as caught:
            chat.call_reply('http://localhost', 'm', 'private-secret', turn.request_messages)
        self.assertIn('401', str(caught.exception))
        self.assertNotIn('private-secret', str(caught.exception))
        self.assertEqual(session.turns, [])

    def test_bad_response_and_url_fail_without_inventing_reply(self):
        with patch.object(chat, 'urlopen', return_value=io.BytesIO(b'{}')), self.assertRaises(ValueError):
            chat.call_reply('http://localhost', 'm', '', [])
        with patch.object(chat, 'urlopen') as opening, self.assertRaises(ValueError):
            chat.call_reply('file:///etc/passwd', 'm', '', [])
        opening.assert_not_called()
