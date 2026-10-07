from contextlib import ExitStack
from hashlib import sha256
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from scripts import evaluate_persona_refined as evaluation


class RefinedEvaluationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        texts = {'persona-used.md': '测试角色', 'contract-used.txt': '事实优先',
                 'refiner-used.txt': '只校对必要错误'}
        for name, text in texts.items():
            (self.folder / name).write_text(text, encoding='utf-8')
        self.method = dict(code_sha256={},
            role_sha256=sha256(texts['persona-used.md'].encode()).hexdigest(),
            contract_sha256=sha256(texts['contract-used.txt'].encode()).hexdigest(),
            refiner_sha256=sha256(texts['refiner-used.txt'].encode()).hexdigest(),
            refiner_runner_sha256=sha256(Path(evaluation.__file__).read_bytes()).hexdigest(),
            refiner_model='deepseek-v4-pro',
            refiner_parameters=dict(thinking=dict(type='enabled'), reasoning_effort='low', max_tokens=4096))
        self.write_method()
        (self.folder / 'cases.json').write_text(json.dumps([
            dict(id='Z01', name='history check', turns=['第一条', '第二条'], use_retrieval=False)
        ]), encoding='utf-8')
        self.requests = []

    def write_method(self):
        (self.folder / 'method.json').write_text(json.dumps(self.method), encoding='utf-8')

    def fake_open(self, request, timeout=90):
        self.requests.append(json.loads(request.data))
        index = len(self.requests)
        content = ['初稿甲', '最终甲', '初稿乙', '最终乙'][index-1]
        return io.BytesIO(json.dumps(dict(id='test-'+str(index), model=self.requests[-1]['model'],
            choices=[dict(finish_reason='stop', message=dict(content=content))])).encode())

    def run_evaluation(self):
        with ExitStack() as stack:
            stack.enter_context(patch.object(evaluation.sys, 'argv', ['eval', str(self.folder)]))
            stack.enter_context(patch.object(evaluation.sys, 'stdin', io.StringIO(json.dumps(dict(
                base_url='https://api.deepseek.com', model='deepseek-flash', api_key='unit-test-private-key')))))
            stack.enter_context(patch.object(evaluation.sys, 'stdout', io.StringIO()))
            stack.enter_context(patch.object(evaluation.chat, 'urlopen', side_effect=self.fake_open))
            stack.enter_context(patch.object(evaluation.chat, 'FACTUAL_REPLY_RULES', 'original'))
            stack.enter_context(patch.object(evaluation.app, 'load_chat_files', return_value=[]))
            stack.enter_context(patch.object(evaluation.app, 'prepare_retrieval_messages', return_value=[
                SimpleNamespace(review_status='approved', message_id='test', speaker='对方')]))
            evaluation.main()

    def test_final_output_not_draft_enters_next_turn_and_every_phase_is_audited(self):
        self.run_evaluation()
        self.assertEqual(len(self.requests), 4)
        history = self.requests[2]['messages']
        self.assertIn(dict(role='assistant', content='最终甲'), history)
        self.assertNotIn(dict(role='assistant', content='初稿甲'), history)
        repair_input = json.loads(self.requests[1]['messages'][-1]['content'])
        self.assertEqual(repair_input['draft'], '初稿甲')
        self.assertEqual(repair_input['dialogue'], [dict(role='user', content='第一条')])
        self.assertEqual(self.requests[0]['model'], 'deepseek-flash')
        self.assertEqual(self.requests[1]['model'], 'deepseek-v4-pro')
        self.assertEqual(self.requests[1]['thinking'], dict(type='enabled'))
        self.assertEqual(self.requests[1]['max_tokens'], 4096)
        summary = json.loads((self.folder / 'summary.json').read_text(encoding='utf-8'))
        self.assertEqual(summary['actual_api_responses'], 4)
        self.assertEqual(summary['actual_replies'], 2)
        self.assertEqual(summary['failures'], [])
        for file in self.folder.glob('*.json'):
            self.assertNotIn('unit-test-private-key', file.read_text(encoding='utf-8'))

    def test_changed_frozen_prompt_fails_before_any_api_call(self):
        (self.folder / 'refiner-used.txt').write_text('changed', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'Frozen prompt hash mismatch'):
            self.run_evaluation()
        self.assertEqual(self.requests, [])

    def test_attempted_run_cannot_overwrite_original_evidence(self):
        original = self.folder / 'Z01-1.request.json'
        original.write_text('original evidence', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'Never overwrite'):
            self.run_evaluation()
        self.assertEqual(original.read_text(encoding='utf-8'), 'original evidence')
        self.assertEqual(self.requests, [])

    def test_invalid_refiner_parameters_fail_before_api_call(self):
        self.method['refiner_parameters']['max_tokens'] = 80000
        self.write_method()
        with self.assertRaisesRegex(ValueError, 'token budget'):
            self.run_evaluation()
        self.assertEqual(self.requests, [])

    def test_structured_reply_emits_only_reply_lines(self):
        raw = json.dumps(dict(requirements=['three lines'], issues=['format'], reply_lines=['甲', '乙', '丙']))
        self.assertEqual(evaluation.decode_refinement(raw, 'json_lines').splitlines(), ['甲', '乙', '丙'])

    def test_malformed_structured_reply_is_not_sent_as_chat_text(self):
        for value in [dict(reply_lines=[]), dict(reply_lines=['']), dict(reply_lines=[3]),
                      dict(reply_lines=['甲'+chr(10)+'乙']), dict(explanation='not a reply')]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                evaluation.decode_refinement(json.dumps(value), 'json_lines')


if __name__ == '__main__':
    unittest.main()
