import json
from pathlib import Path
import tempfile
import unittest
from urllib.error import HTTPError

from scripts.persona_evaluation_policy import enforce_models, fatal_provider_status


class EvaluationPolicyTests(unittest.TestCase):
    def test_flash_allowed_pro_blocked_for_every_phase(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            run=root/'campaign'/'candidate'
            run.mkdir(parents=True)
            (root/'execution-policy.json').write_text(json.dumps({'allowed_models':['deepseek-flash']}))
            enforce_models(run,{'generator':'deepseek-flash','critic':'deepseek-flash'},root)
            for phase in ('generator','critic','refiner','designer'):
                with self.subTest(phase=phase), self.assertRaisesRegex(ValueError,'disallowed'):
                    enforce_models(run,{phase:'deepseek-v4-pro'},root)

    def test_child_policy_cannot_relax_parent_limit(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            child=root/'candidate'
            child.mkdir()
            (root/'execution-policy.json').write_text(json.dumps({'allowed_models':['deepseek-flash']}))
            (child/'execution-policy.json').write_text(json.dumps({'allowed_models':['deepseek-v4-pro']}))
            with self.assertRaisesRegex(ValueError,'disallowed'):
                enforce_models(child,{'critic':'deepseek-v4-pro'},root)

    def test_broken_policy_fails_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            (root/'execution-policy.json').write_text('{}')
            with self.assertRaisesRegex(ValueError,'Invalid'):
                enforce_models(root,{'critic':'deepseek-flash'},root)

    def test_payment_auth_failures_survive_wrapping_but_network_errors_do_not_trip(self):
        for status in (401,402,403):
            inner=HTTPError('https://api.deepseek.com',status,'denied',{},None)
            outer=ValueError('sanitized failure')
            outer.__cause__=inner
            self.assertEqual(fatal_provider_status(outer),status)
        self.assertIsNone(fatal_provider_status(TimeoutError('network timeout')))
        self.assertIsNone(fatal_provider_status(HTTPError('https://api.deepseek.com',429,'rate limit',{},None)))


if __name__=='__main__':
    unittest.main()
