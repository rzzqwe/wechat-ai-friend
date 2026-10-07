import json
from hashlib import sha256
from pathlib import Path
import tempfile
import unittest

from scripts.export_persona_experiments import export


class ExperimentExportTests(unittest.TestCase):
    def export_fixture(self, review):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / 'trial'
            source.mkdir()
            reply = 'first line' + chr(10) + 'second line'
            summary = dict(actual_replies=1, expected_calls=2,
                           actual_api_responses=2, wall_seconds=1.25,
                           cases=[dict(id='A01', name='fixture', turns=['first', 'second'],
                                       results=[dict(turn=1, elapsed_seconds=0.5,
                                                     user='first', reply=reply,
                                                     api_response_id='fixture-api')],
                                       failures=[dict(turn=2, error='fixture failure')])])
            for name, value in [('summary.json', summary),
                                ('method.json', {'role_sha256': 'fixture-role'}),
                                ('decision.json', {'status': 'rejected-development-candidate',
                                                   'counts': {'pass': 0, 'style': 1, 'fail': 0},
                                                   'cases': [dict(id='A01', **review)]})]:
                (source / name).write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
            report = root / 'report.txt'
            result = export([source], report)
            content = report.read_text(encoding='utf-8')
            index = json.loads(report.with_suffix('.index.json').read_text(encoding='utf-8'))
            self.assertEqual(result['final_replies'], 1)
            self.assertEqual(index['report_sha256'], sha256(report.read_bytes()).hexdigest())
            self.assertEqual(index['experiments'][0]['decision_sha256'],
                             sha256((source / 'decision.json').read_bytes()).hexdigest())
            self.assertIn('小登：' + reply, content)
            self.assertIn('第 2 轮失败：fixture failure', content)
            original = report.read_bytes()
            with self.assertRaises(FileExistsError):
                export([source], report)
            self.assertEqual(report.read_bytes(), original)
            return content

    def test_current_grade_and_reason_survive_structured_evidence(self):
        content = self.export_fixture(dict(grade='style', reason='Repeated phrasing',
                                           evidence={'turn': 1, 'actual_reply': 'first line'}))
        self.assertIn('[A01] fixture / style', content)
        self.assertIn('评语：Repeated phrasing', content)
        self.assertNotIn('未评审', content)

    def test_legacy_status_and_text_evidence_remain_supported(self):
        content = self.export_fixture(dict(status='style', evidence='Legacy assessment'))
        self.assertIn('[A01] fixture / style', content)
        self.assertIn('评语：Legacy assessment', content)

    def test_structured_evidence_without_reason_is_rendered_not_discarded(self):
        content = self.export_fixture(dict(grade='fail', evidence={'turn': 2, 'issue': 'incomplete'}))
        self.assertIn('[A01] fixture / fail', content)
        self.assertIn('incomplete', content)

    def test_missing_review_is_explicit(self):
        content = self.export_fixture({})
        self.assertIn('[A01] fixture / 未评审', content)
        self.assertIn('评语：无', content)


if __name__ == '__main__':
    unittest.main()
