import contextlib
from hashlib import sha256
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts import evaluate_persona_guarded as evaluation
from scripts import audit_persona_experiment as experiment_audit


ACCEPT=json.dumps({'accept':True,'violations':[]})
REJECT=json.dumps({'accept':False,'violations':[{'type':'fact','quote':'bad draft',
    'evidence':'user supplied no such fact','reason':'invented action'}]})


class GuardedEvaluationTests(unittest.TestCase):
    def test_rejected_draft_is_not_sent_or_used_as_next_turn_history(self):
        self.run_trial(False)

    def test_attempt_bound_does_not_silently_send_a_rejected_answer(self):
        self.run_trial(True)

    def test_critic_without_evidence_is_not_treated_as_success(self):
        for result in ({'accept':True,'violations':[{}]}, {'accept':False,'violations':[]},
                       {'accept':False,'violations':[{'type':'fact'}]}):
            with self.subTest(result=result),self.assertRaises(ValueError):
                evaluation.decode_verdict(json.dumps(result))

    def test_pro_is_rejected_before_any_api_call(self):
        with tempfile.TemporaryDirectory() as temp:
            folder=Path(temp)
            (folder/'method.json').write_text(json.dumps({'critic_model':'deepseek-v4-pro'}))
            credentials={'base_url':'https://api.deepseek.com','model':'deepseek-flash','api_key':'unit-secret'}
            with patch.object(evaluation.sys,'argv',['runner',temp]),patch.object(evaluation.sys,'stdin',io.StringIO(json.dumps(credentials))),patch.object(evaluation.chat,'urlopen') as network:
                with self.assertRaisesRegex(ValueError,'Flash exclusively'):
                    evaluation.main()
                network.assert_not_called()

    def run_trial(self, always_reject):
        with tempfile.TemporaryDirectory() as temp:
            folder=Path(temp)
            method=dict(critic_model='deepseek-flash',max_attempts=3,code_sha256={},
                guard_runner_sha256=sha256(Path(evaluation.__file__).read_bytes()).hexdigest(),
                critic_parameters={'thinking':{'type':'enabled'},'reasoning_effort':'high','max_tokens':4096})
            for field,filename in [('role','persona-used.md'),('contract','contract-used.txt'),('critic','critic-used.txt')]:
                (folder/filename).write_text(field,encoding='utf-8')
                method[field+'_sha256']=sha256(field.encode()).hexdigest()
            (folder/'method.json').write_text(json.dumps(method),encoding='utf-8')
            (folder/'evaluate_guarded.snapshot.py').write_bytes(Path(evaluation.__file__).read_bytes())
            (folder/'cases.json').write_text(json.dumps([{'id':'A01','turns':['first user','follow-up user']}]),encoding='utf-8')
            replies=['bad draft',REJECT,'bad draft',REJECT,'bad draft',REJECT] if always_reject else ['bad draft',REJECT,'corrected reply',ACCEPT,'follow-up reply',ACCEPT]
            captured=[]

            def transport(request,timeout=90):
                captured.append(json.loads(request.data.decode()))
                content=replies[len(captured)-1]
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
            self.assertEqual(len(captured),6)
            self.assertEqual(summary['actual_api_responses'],6)
            self.assertEqual(summary['transport_responses'],6)
            self.assertTrue(all(x['model']=='deepseek-flash' for x in captured))
            self.assertEqual(captured[0]['thinking'],{'type':'disabled'})
            self.assertEqual(captured[1]['thinking'],{'type':'enabled'})
            self.assertEqual(captured[1]['reasoning_effort'],'high')
            self.assertIn('bad draft',captured[2]['messages'][0]['content'])
            self.assertEqual(captured[2]['messages'][1:-1],[])
            if always_reject:
                self.assertEqual(summary['actual_replies'],0)
                self.assertEqual(len(summary['failures'][0]['attempts']),3)
                self.assertFalse((folder/'A01-1-final.json').exists())
            else:
                self.assertEqual(summary['actual_replies'],2)
                self.assertEqual(summary['failures'],[])
                self.assertEqual(captured[4]['messages'][-2]['content'],'corrected reply')
                self.assertNotIn('bad draft',json.dumps(captured[4],ensure_ascii=False))
                self.assertEqual(summary['cases'][0]['results'][0]['accepted_attempt'],2)
            for file in folder.iterdir():
                self.assertNotIn(credentials['api_key'],file.read_text(encoding='utf-8'))
            self.assertTrue(experiment_audit.audit(folder)['integrity_ok'])
            path=folder/'A01-1-a2-generate.request.json'
            altered=json.loads(path.read_text(encoding='utf-8'))
            altered['body']['messages'].insert(1,{'role':'assistant','content':'rejected draft leaked'})
            path.write_text(json.dumps(altered),encoding='utf-8')
            self.assertFalse(experiment_audit.audit(folder)['integrity_ok'])


if __name__=='__main__':
    unittest.main()
