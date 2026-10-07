import contextlib
from hashlib import sha256
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from scripts import evaluate_persona_routed as evaluation
from scripts import audit_persona_experiment as experiment_audit


def route(kind='answer', lines=None):
    return dict(route=kind, requirements=['respond'], known_facts=[], constraints=[],
                reply_lines=lines if lines is not None else ['第一行', '第二行', '第三行'])


class RoutedEvaluationTests(unittest.TestCase):
    def test_only_successful_empty_text_is_eligible_for_recovery(self):
        def response(content='', reason='stop', **message):
            return {'choices':[{'finish_reason':reason,'message':dict(content=content,**message)}]}
        self.assertTrue(evaluation.retryable_empty_content(response('  ')))
        for value in [response('a real answer'),response(reason='length'),
                      response(reason='content_filter'),response(None),
                      response(refusal='refused'),response(tool_calls=[{'id':'tool'}]),
                      response(function_call={'name':'tool'}),
                      dict(response(),error={'message':'error'}),{'choices':[]}]:
            with self.subTest(value=value):
                self.assertFalse(evaluation.retryable_empty_content(value))

    def test_empty_success_is_retried_once_and_both_responses_are_audited(self):
        self.run_trial(True, empty_prefix=1)

    def test_two_empty_successes_stop_at_frozen_bound(self):
        self.run_trial(True, empty_prefix=2)

    def test_budget_retry_does_not_retry_partial_text_refusal_or_tools(self):
        def response(content='', **message):
            return {'choices':[{'finish_reason':'length','message':dict(content=content,**message)}]}
        self.assertTrue(evaluation.retryable_empty_content(response(),include_length=True))
        for value in [response('partial answer'),response(None),response(refusal='refused'),
                      response(tool_calls=[{'id':'tool'}]),response(function_call={'name':'tool'}),
                      dict(response(),error={'message':'provider error'})]:
            with self.subTest(value=value):
                self.assertFalse(evaluation.retryable_empty_content(value,include_length=True))

    def test_budget_exhaustion_retries_identical_request_and_archives_both(self):
        self.run_trial(True,empty_prefix=1,empty_finish='length',retry_length=True)

    def test_repeated_budget_exhaustion_stops_at_frozen_bound(self):
        self.run_trial(True,empty_prefix=2,empty_finish='length',retry_length=True)

    def test_snapshot_alias_is_verified_and_cannot_mask_tampering(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp)
            expected=sha256(b'frozen source').hexdigest()
            check=lambda: experiment_audit.runner_snapshot_matches(folder,'evaluate_routed.snapshot.py',expected)
            self.assertFalse(check())
            (folder/'evaluate_persona_routed.snapshot.py').write_bytes(b'frozen source')
            self.assertTrue(check())
            (folder/'evaluate_routed.snapshot.py').write_bytes(b'changed source')
            self.assertFalse(check())
            (folder/'evaluate_routed.snapshot.py').write_bytes(b'frozen source')
            self.assertTrue(check())

    def test_missing_internal_route_keeps_exact_sendable_text_in_direct_mode(self):
        value=route(lines=['这个我真不知道诶。'])
        del value['route']
        self.assertEqual(evaluation.decode_route(json.dumps(value),direct_reply=True),value)
        with self.assertRaises(ValueError):
            evaluation.decode_route(json.dumps(value))
        self.run_trial(True,finalized=True,missing_route=True)

    def test_transient_transport_retry_is_bounded_and_audited(self):
        self.run_trial(True,network_error=URLError('connection reset'))

    def test_payment_error_is_never_retried(self):
        self.run_trial(True,network_error=HTTPError('https://api.deepseek.com/v1/chat/completions',402,'payment',{},None))

    def test_source_attributed_fact_metadata_does_not_discard_valid_reply(self):
        value=route(lines=['specific answer'])
        value['known_facts']=[{'fact':'user asks for an opinion','source':'latest_user'}]
        self.assertEqual(evaluation.decode_route(json.dumps(value),direct_reply=True),value)
        value['reply_lines']=[{'fact':'not a sendable line','source':'draft'}]
        with self.assertRaises(ValueError):
            evaluation.decode_route(json.dumps(value),direct_reply=True)
        value['reply_lines']=['specific answer']
        value['known_facts']=[{'fact':'missing source'}]
        with self.assertRaises(ValueError):
            evaluation.decode_route(json.dumps(value),direct_reply=True)

    def test_direct_mode_requires_and_preserves_chat_reply(self):
        value = route('chat', ['少得意'])
        self.assertEqual(evaluation.decode_route(json.dumps(value), direct_reply=True)['reply_lines'], ['少得意'])
        with self.assertRaises(ValueError):
            evaluation.decode_route(json.dumps(route('chat', [])), direct_reply=True)

    def test_parser_rejects_silent_loss_of_structure(self):
        invalid = [route(lines=[]), route(lines=[{'text':'not a reply string'}]),
                   route('chat', ['premature answer']), dict(route='unknown')]
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(ValueError):
                evaluation.decode_route(json.dumps(value))

    def test_paragraph_separators_and_embedded_lines_preserve_exact_text(self):
        lines=['主题：退款申请', '', '您好：'+chr(10)+'订单晚到三天。', '', '请协助退款。']
        value=route(lines=lines)
        decoded=evaluation.decode_route(json.dumps(value),direct_reply=True)
        self.assertEqual(decoded,value)
        self.assertEqual(chr(10).join(decoded['reply_lines']),chr(10).join(lines))

    def test_formatting_only_response_is_still_rejected(self):
        for lines in ([],[''],[' ','',chr(10)]):
            with self.subTest(lines=lines), self.assertRaises(ValueError):
                evaluation.decode_route(json.dumps(route(lines=lines)),direct_reply=True)

    def test_only_actual_final_reply_enters_next_turn_and_requests_are_audited(self):
        self.run_trial(False)

    def test_direct_generation_never_calls_a_second_model_or_stores_json_as_history(self):
        self.run_trial(True)

    def test_empty_provider_content_is_recorded_and_does_not_enter_history(self):
        self.run_trial(True, empty_response=True)

    def test_finalizer_uses_raw_context_and_only_its_result_becomes_history(self):
        self.run_trial(True, finalized=True)

    def test_task_voice_applies_only_to_actual_answer_turns(self):
        self.run_trial(True,finalized=True,task_voice=True)

    def test_task_guidance_preserves_persona_and_only_applies_to_answers(self):
        self.run_trial(True,finalized=True,task_voice=True,task_voice_mode='supplement')

    def test_unknown_task_voice_mode_is_rejected_before_requests(self):
        with self.assertRaisesRegex(ValueError, 'Invalid task voice mode'):
            self.run_trial(True,finalized=True,task_voice=True,task_voice_mode='unknown')

    def test_reply_only_review_does_not_inherit_draft_route_or_self_certified_facts(self):
        self.run_trial(True, finalized=True, draft_review_mode='reply_only')

    def test_unknown_draft_review_mode_is_rejected_before_requests(self):
        with self.assertRaisesRegex(ValueError, 'Invalid draft review mode'):
            self.run_trial(True, finalized=True, draft_review_mode='unknown')

    def test_reply_only_review_requires_finalizer(self):
        with self.assertRaisesRegex(ValueError, 'Reply-only review requires a finalizer'):
            self.run_trial(True, draft_review_mode='reply_only')

    def test_independent_review_preserves_text_when_internal_metadata_is_opaque(self):
        for metadata in ({}, {'requirements':'respond'}, {'known_facts':{'note':'opaque'}}):
            value=dict(metadata,reply_lines=['有效正文', '', '第二段'])
            self.assertEqual(evaluation.decode_route(json.dumps(value),direct_reply=True,
                                                     validate_metadata=False),value)
        self.run_trial(True,finalized=True,draft_review_mode='reply_only',opaque_review_metadata=True)

    def test_relaxed_review_metadata_never_accepts_missing_or_invalid_visible_text(self):
        for lines in (None, [], [''], ['  '], [{'text':'not a string'}], ['x'*8001]):
            with self.subTest(lines=lines), self.assertRaises(ValueError):
                evaluation.decode_route(json.dumps({'reply_lines':lines}),direct_reply=True,
                                        validate_metadata=False)

    def test_lower_frozen_review_effort_reaches_actual_request(self):
        self.run_trial(True,finalized=True,draft_review_mode='reply_only',finalizer_effort='low')

    def test_non_thinking_review_preserves_history_and_audits_actual_request(self):
        self.run_trial(True,finalized=True,draft_review_mode='reply_only',finalizer_thinking='disabled')

    def test_larger_frozen_finalizer_budget_reaches_actual_request(self):
        self.run_trial(True,finalized=True,finalizer_tokens=8192)

    def run_trial(self, direct, empty_response=False, finalized=False, missing_route=False, network_error=None,task_voice=False,finalizer_tokens=4096,empty_prefix=0,task_voice_mode=None,draft_review_mode=None,opaque_review_metadata=False,finalizer_effort='high',finalizer_thinking='enabled',empty_finish='stop',retry_length=False):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            method = dict(model='deepseek-flash', router_model='deepseek-v4-pro', code_sha256={},
                          direct_reply=direct,
                          router_runner_sha256=sha256(Path(evaluation.__file__).read_bytes()).hexdigest(),
                          router_parameters=dict(thinking={'type':'enabled'}, reasoning_effort='low', max_tokens=4096))
            if network_error:
                method['transport_attempts']=2
            if empty_prefix:
                method.update(transport_attempts=2,retry_empty_provider_content=True)
            if retry_length:
                method['retry_empty_budget_exhaustion']=True
            if finalized:
                method.update(router_model='deepseek-flash',finalizer_model='deepseek-flash',
                    finalizer_parameters={'thinking':{'type':finalizer_thinking},'max_tokens':finalizer_tokens})
                if finalizer_thinking=='enabled':
                    method['finalizer_parameters']['reasoning_effort']=finalizer_effort
                (folder/'finalizer-used.txt').write_text('finalizer',encoding='utf-8')
                method['finalizer_sha256']=sha256(b'finalizer').hexdigest()
            if task_voice:
                method['task_voice_sha256']=sha256(b'task voice').hexdigest()
                (folder/'task-voice-used.txt').write_text('task voice',encoding='utf-8')
            if task_voice_mode is not None:
                method['task_voice_mode']=task_voice_mode
            if draft_review_mode is not None:
                method['draft_review_mode']=draft_review_mode
            for key, filename in [('role','persona-used.md'),('contract','contract-used.txt'),('router','router-used.txt')]:
                (folder/filename).write_text(key, encoding='utf-8')
                method[key+'_sha256'] = sha256(key.encode()).hexdigest()
            (folder/'method.json').write_text(json.dumps(method), encoding='utf-8')
            (folder/'evaluate_routed.snapshot.py').write_bytes(Path(evaluation.__file__).read_bytes())
            (folder/'cases.json').write_text(json.dumps([dict(id='A01', turns=['three lines','chat now','follow up'])]), encoding='utf-8')
            replies = ([json.dumps(route()), json.dumps(route('chat', ['actual banter'])), json.dumps(route(lines=['done']))]
                       if direct else [json.dumps(route()), json.dumps(route('chat', [])), 'actual banter', json.dumps(route(lines=['done']))])
            if empty_response:
                replies[0] = ''
            if finalized:
                replies=[item for reply in replies for item in [json.dumps(route(json.loads(reply)['route'],lines=['unapproved draft'])),reply]]
            if opaque_review_metadata:
                for index in range(1,len(replies),2):
                    value=json.loads(replies[index])
                    value['requirements']='current request'
                    value.pop('constraints',None)
                    replies[index]=json.dumps(value)
            if missing_route:
                value=json.loads(replies[1])
                del value['route']
                replies[1]=json.dumps(value)
            replies=['']*empty_prefix+replies
            captured = []
            attempted = []

            def transport(request, timeout=90):
                attempted.append(json.loads(request.data.decode()))
                if network_error and len(attempted)==1:
                    raise network_error
                captured.append(json.loads(request.data.decode()))
                return io.BytesIO(json.dumps({'id':str(len(captured)), 'model':captured[-1]['model'],
                    'choices':[{'message':{'content':replies[len(captured)-1]},
                                'finish_reason':empty_finish if len(captured)<=empty_prefix else 'stop'}]}).encode())

            class Approved:
                review_status='approved'
                message_id='reference-id'
                speaker='reference-speaker'

            credentials = dict(base_url='https://api.deepseek.com', model='deepseek-flash', api_key='never-save-this-secret')
            with contextlib.ExitStack() as stack:
                stack.enter_context(patch.object(evaluation.sys, 'argv', ['runner', str(folder)]))
                stack.enter_context(patch.object(evaluation.sys, 'stdin', io.StringIO(json.dumps(credentials))))
                stack.enter_context(patch.object(evaluation.app, 'load_chat_files', return_value=[]))
                stack.enter_context(patch.object(evaluation.app, 'prepare_retrieval_messages', return_value=[Approved()]))
                stack.enter_context(patch.object(evaluation.chat, 'urlopen', transport))
                stack.enter_context(patch.object(evaluation.chat, 'FACTUAL_REPLY_RULES', 'original'))
                stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
                evaluation.main()
            summary = json.loads((folder/'summary.json').read_text(encoding='utf-8'))
            if empty_prefix:
                ledger_path=folder/'A01-1-route.network-attempts.json'
                ledger=json.loads(ledger_path.read_text())
                self.assertEqual([item['outcome'] for item in ledger],['empty_response','received'])
                self.assertEqual(attempted[0],attempted[1])
                extra_path=folder/'A01-1-route-try1.transport-extra.json'
                extra=json.loads(extra_path.read_text())
                self.assertEqual(extra['assistant_content'],'')
                self.assertEqual(extra['api_response_id'],'1')
                self.assertTrue(experiment_audit.audit(folder)['integrity_ok'])
                extra['assistant_content']='not actually empty'
                extra_path.write_text(json.dumps(extra))
                self.assertFalse(experiment_audit.audit(folder)['integrity_ok'])
                extra['assistant_content']=''
                extra_path.write_text(json.dumps(extra))
                if empty_prefix==2:
                    self.assertEqual(summary['actual_replies'],0)
                    self.assertEqual(summary['actual_api_responses'],0)
                    self.assertEqual(summary['transport_responses'],2)
                    self.assertEqual(len(attempted),2)
                    return
                self.assertEqual(summary['transport_responses'],4)
                self.assertEqual(summary['actual_network_attempts'],4)
                captured=captured[1:]
            if network_error:
                ledger=json.loads((folder/'A01-1-route.network-attempts.json').read_text())
                self.assertEqual(ledger[0]['outcome'],'transport_error')
                if isinstance(network_error,HTTPError):
                    self.assertEqual(len(attempted),1)
                    self.assertEqual(summary['actual_replies'],0)
                    self.assertTrue(summary['stopped_after_fatal_provider_error'])
                    self.assertEqual(len(ledger),1)
                    return
                self.assertEqual(len(ledger),2)
                self.assertEqual(ledger[1]['outcome'],'received')
                self.assertEqual(attempted[0],attempted[1])
                self.assertEqual(len(attempted),len(captured)+1)
            if empty_response:
                self.assertEqual(summary['actual_replies'], 0)
                self.assertEqual(summary['actual_api_responses'], 0)
                self.assertEqual(summary['transport_responses'], 1)
                self.assertEqual(len(captured), 1)
                self.assertIn('elapsed_seconds', summary['failures'][0])
                self.assertEqual(json.loads((folder/'A01-1-route.transport.json').read_text())['assistant_content'], '')
                return
            self.assertEqual(summary['actual_replies'], 3)
            self.assertEqual(summary['actual_api_responses'], 6 if finalized else 3 if direct else 4)
            self.assertEqual(summary['transport_responses'], (6 if finalized else 3 if direct else 4)+empty_prefix)
            self.assertEqual(summary['failures'], [])
            self.assertEqual(captured[0]['response_format'], {'type':'json_object'})
            self.assertEqual(captured[0]['thinking'], {'type':'enabled'})
            second_scope = json.loads(captured[2 if finalized else 1]['messages'][-1]['content'])
            self.assertEqual(second_scope['recent_history'][-1]['content'], '第一行'+chr(10)+'第二行'+chr(10)+'第三行')
            third_scope = json.loads(captured[4 if finalized else 2 if direct else 3]['messages'][-1]['content'])
            self.assertEqual(third_scope['recent_history'][-1]['content'], 'actual banter')
            if direct:
                self.assertTrue(all(item['model']==('deepseek-flash' if finalized else 'deepseek-v4-pro') for item in captured))
                if finalized:
                    self.assertNotIn('unapproved draft',json.dumps(second_scope['recent_history']))
                    self.assertEqual(captured[1]['thinking'],{'type':finalizer_thinking})
                    if finalizer_thinking=='enabled':
                        self.assertEqual(captured[1]['reasoning_effort'],finalizer_effort)
                    else:
                        self.assertNotIn('reasoning_effort',captured[1])
                    self.assertEqual(captured[1]['max_tokens'],finalizer_tokens)
                    self.assertEqual(summary['cases'][0]['results'][0]['api_response_id'],'2')
                    if draft_review_mode == 'reply_only':
                        first_review=json.loads(captured[1]['messages'][-1]['content'])
                        self.assertEqual(first_review['draft'], {'reply_lines':['unapproved draft']})
                        self.assertNotIn('known_facts',first_review['draft'])
                        self.assertNotIn('route',first_review['draft'])
                        self.assertEqual(first_review['latest_user'],'three lines')
                        self.assertEqual(first_review['voice_reference'],'role')
                    if task_voice:
                        first_final=json.loads(captured[1]['messages'][-1]['content'])
                        second_final=json.loads(captured[3]['messages'][-1]['content'])
                        if task_voice_mode=='supplement':
                            self.assertEqual(first_final['voice_reference'],'role')
                            self.assertEqual(first_final['task_guidance'],'task voice')
                        else:
                            self.assertEqual(first_final['voice_reference'],'task voice')
                            self.assertNotIn('task_guidance',first_final)
                        self.assertEqual(second_final['voice_reference'],'role')
                        self.assertNotIn('task_guidance',second_final)
            else:
                self.assertEqual(captured[2]['thinking'], {'type':'disabled'})
                self.assertNotIn('response_format', captured[2])
            for file in folder.iterdir():
                self.assertNotIn(credentials['api_key'], file.read_text(encoding='utf-8'))
            self.assertTrue(experiment_audit.audit(folder)['integrity_ok'])
            if network_error:
                ledger_path=folder/'A01-1-route.network-attempts.json'
                original=ledger_path.read_text()
                altered_ledger=json.loads(original)
                altered_ledger[0]['http_status']=402
                ledger_path.write_text(json.dumps(altered_ledger))
                self.assertFalse(experiment_audit.audit(folder)['integrity_ok'])
                ledger_path.write_text(original)
            request_path = folder/'A01-2-route.request.json'
            altered = json.loads(request_path.read_text(encoding='utf-8'))
            scope = json.loads(altered['body']['messages'][-1]['content'])
            scope['recent_history'][-1]['content'] = 'wrong prior answer'
            altered['body']['messages'][-1]['content'] = json.dumps(scope)
            request_path.write_text(json.dumps(altered), encoding='utf-8')
            self.assertFalse(experiment_audit.audit(folder)['integrity_ok'])


if __name__ == '__main__':
    unittest.main()
