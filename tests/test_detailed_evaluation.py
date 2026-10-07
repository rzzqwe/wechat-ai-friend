import contextlib
from hashlib import sha256
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from scripts import evaluate_persona_detailed as evaluation
from scripts import audit_persona_experiment as experiment_audit


class DetailedEvaluationTests(unittest.TestCase):
    def test_frozen_thinking_parameters_and_actual_history_are_preserved(self):
        self.run_trial('success')

    def test_empty_response_is_archived_and_never_becomes_history(self):
        self.run_trial('empty')

    def test_payment_error_stops_without_retrying_or_fabricating_output(self):
        self.run_trial('payment')

    def test_invalid_override_fails_before_api(self):
        with tempfile.TemporaryDirectory() as temp:
            folder=Path(temp)
            credentials=dict(base_url='https://api.deepseek.com',model='deepseek-flash',api_key='unit-key')
            for override in ({'reasoning_effort':'high'}, {'thinking':{'type':'enabled'},'reasoning_effort':'unknown'}, {'max_tokens':999999}):
                (folder/'method.json').write_text(json.dumps({'request_overrides':override}),encoding='utf-8')
                with patch.object(evaluation.sys,'argv',['runner',temp]),patch.object(evaluation.sys,'stdin',io.StringIO(json.dumps(credentials))),patch.object(evaluation.chat,'urlopen') as network:
                    with self.assertRaises(ValueError):
                        evaluation.main()
                    network.assert_not_called()

    def run_trial(self, mode):
        with tempfile.TemporaryDirectory() as temp:
            folder=Path(temp)
            params={'thinking':{'type':'enabled'},'reasoning_effort':'high','max_tokens':4096}
            method=dict(code_sha256={},request_overrides=params,
                detailed_runner_sha256=sha256(Path(evaluation.__file__).read_bytes()).hexdigest())
            for field,filename in [('role','persona-used.md'),('contract','contract-used.txt')]:
                (folder/filename).write_text(field,encoding='utf-8')
                method[field+'_sha256']=sha256(field.encode()).hexdigest()
            (folder/'method.json').write_text(json.dumps(method),encoding='utf-8')
            (folder/'evaluate_detailed.snapshot.py').write_bytes(Path(evaluation.__file__).read_bytes())
            (folder/'cases.json').write_text(json.dumps([{'id':'A01','turns':['first user','next user']}]),encoding='utf-8')
            captured=[]

            def transport(request,timeout=90):
                captured.append(json.loads(request.data.decode()))
                if mode=='payment':
                    raise HTTPError(request.full_url,402,'payment',{},None)
                content='' if mode=='empty' else 'reply '+str(len(captured))
                return io.BytesIO(json.dumps({'id':'id-'+str(len(captured)),'model':'deepseek-flash',
                    'choices':[{'message':{'content':content},'finish_reason':'stop'}]}).encode())

            class Approved:
                review_status='approved'
                message_id='evidence'
                speaker='voice'

            credentials=dict(base_url='https://api.deepseek.com',model='deepseek-flash',api_key='never-save-unit-key')
            with contextlib.ExitStack() as stack:
                stack.enter_context(patch.object(evaluation.sys,'argv',['runner',temp]))
                stack.enter_context(patch.object(evaluation.sys,'stdin',io.StringIO(json.dumps(credentials))))
                stack.enter_context(patch.object(evaluation.app,'load_chat_files',return_value=[]))
                stack.enter_context(patch.object(evaluation.app,'prepare_retrieval_messages',return_value=[Approved()]))
                stack.enter_context(patch.object(evaluation.chat,'FACTUAL_REPLY_RULES','original'))
                stack.enter_context(patch.object(evaluation.chat,'urlopen',transport))
                stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
                evaluation.main()
            summary=json.loads((folder/'summary.json').read_text(encoding='utf-8'))
            self.assertTrue(all(all(body.get(k)==v for k,v in params.items()) for body in captured))
            self.assertEqual(summary['stopped_after_fatal_provider_error'],mode=='payment')
            if mode=='success':
                self.assertEqual(summary['actual_replies'],2)
                self.assertEqual(summary['actual_api_responses'],2)
                self.assertEqual(captured[1]['messages'][-2]['content'],'reply 1')
            else:
                self.assertEqual(summary['actual_replies'],0)
                self.assertEqual(len(captured),1)
                self.assertEqual(summary['transport_responses'],1 if mode=='empty' else 0)
                self.assertIn('elapsed_seconds',summary['failures'][0])
            self.assertTrue(experiment_audit.audit(folder)['integrity_ok'])
            for file in folder.iterdir():
                self.assertNotIn(credentials['api_key'],file.read_text(encoding='utf-8'))


if __name__=='__main__':
    unittest.main()
