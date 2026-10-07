from pathlib import Path
from types import SimpleNamespace
import json
import os
import shutil
import socket
import subprocess
import tempfile
import time
import unittest
from unittest.mock import Mock,patch

from account_personas import PersonaStore,read_json,write_json
from scripts.unbind_account import unbind,journal_path,stop_runtime
from runtime_tuning import project_runtime


class UnbindAccountTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name)
        self.store=PersonaStore(self.root/'project',self.root/'state')
        self.first=self.store.save('甲','# 甲');self.second=self.store.save('乙','# 乙')
        self.a=self.store.assign('user-a',self.first['id'],'wx-a')
        self.b=self.store.assign('user-b',self.second['id'],'wx-b')
        write_json(self.store.root/'data/accounts.json',[{'id':'user-a'},{'id':'user-b'}])
        write_json(self.store.root/'data/account-bindings.json',{'user-a':'wx-a','user-b':'wx-b'})
        write_json(self.store.state/'openclaw-weixin/accounts.json',['wx-a','wx-b'])
        self.credential=self.store.state/'openclaw-weixin/accounts/wx-a.json'
        self.other=self.credential.with_name('wx-b.json')
        write_json(self.credential,{'token':'fixture-a'});write_json(self.other,{'token':'fixture-b'})
        self.memory=self.store.workspace('user-a',self.first['id'])/'MEMORY.md'
        self.memory.write_text('保留的聊天记忆',encoding='utf-8')

    def test_single_account_stop_preserves_other_user_persona_and_history(self):
        stop=Mock()
        started=time.monotonic()
        unbind(self.store,'wx-a','user-a',stop=stop,emit=lambda text:None)
        stop.assert_called_once_with(self.store,'wx-a')
        self.assertFalse(self.credential.exists());self.assertTrue(self.other.exists())
        self.assertEqual(self.memory.read_text(encoding='utf-8'),'保留的聊天记忆')
        self.assertTrue(self.store.profile_path(self.first['id']).exists())
        self.assertEqual(set(self.store.assignments()),{'user-b'})
        self.assertEqual(read_json(self.store.root/'data/accounts.json',[]),[{'id':'user-b'}])
        self.assertEqual(read_json(self.store.root/'data/account-bindings.json',{}),{'user-b':'wx-b'})
        self.assertEqual(read_json(self.store.state/'openclaw-weixin/accounts.json',[]),['wx-b'])
        self.assertEqual([row['agentId'] for row in read_json(self.store.config,{})['bindings']],[self.b['agent_id']])
        self.assertLess(time.monotonic()-started,3)

    def test_pending_account_needs_no_gateway_or_credential_deletion(self):
        pending=self.store.save('待扫码','# 待扫码');self.store.assign('pending',pending['id'])
        before=self.credential.read_bytes();stop=Mock()
        unbind(self.store,'pending','pending',stop=stop,emit=lambda text:None)
        stop.assert_not_called();self.assertEqual(self.credential.read_bytes(),before)
        self.assertNotIn('pending',self.store.assignments())

    def test_wrong_owner_does_not_disable_or_delete_anything(self):
        before=self.store.config.read_bytes();stop=Mock()
        with self.assertRaises(ValueError):unbind(self.store,'wx-b','user-a',stop=stop)
        stop.assert_not_called();self.assertEqual(self.store.config.read_bytes(),before)
        self.assertTrue(self.credential.exists());self.assertTrue(self.other.exists())

    def test_stop_failure_retains_binding_and_credentials_for_retry(self):
        with self.assertRaises(ValueError):
            unbind(self.store,'wx-a',stop=Mock(side_effect=ValueError('接收仍在停止')),emit=lambda text:None)
        self.assertTrue(self.credential.exists());self.assertIn('user-a',self.store.assignments())
        self.assertTrue(journal_path(self.store,'user-a').exists())
        unbind(self.store,'wx-a',stop=Mock(),emit=lambda text:None)
        self.assertFalse(journal_path(self.store,'user-a').exists())

    def test_late_project_save_failure_retries_without_resurrecting_deleted_account(self):
        from scripts import unbind_account as module
        real=module.write_json
        failed=False
        def write(file,value):
            nonlocal failed
            if file==self.store.root/'data/accounts.json' and not failed:
                failed=True;raise OSError('fixture disk failure')
            real(file,value)
        with patch.object(module,'write_json',side_effect=write):
            with self.assertRaises(OSError):unbind(self.store,'wx-a',stop=Mock(),emit=lambda text:None)
        self.assertNotIn('user-a',self.store.assignments())
        retry=Mock(side_effect=AssertionError('Already stopped and cleared'))
        unbind(self.store,'wx-a',stop=retry,emit=lambda text:None)
        retry.assert_not_called()
        self.assertEqual(read_json(self.store.root/'data/accounts.json',[]),[{'id':'user-b'}])

    def test_open_scan_and_corrupt_index_block_cleanup(self):
        stop=Mock()
        write_json(self.store.state/'wechat-ai-binding-pause.json',{'pid':123})
        with self.assertRaises(ValueError):unbind(self.store,'wx-a',stop=stop)
        (self.store.state/'wechat-ai-binding-pause.json').unlink()
        write_json(self.store.state/'openclaw-weixin/accounts.json',{})
        with self.assertRaises(ValueError):unbind(self.store,'wx-a',stop=stop)
        stop.assert_not_called();self.assertTrue(self.credential.exists())

    def test_cursor_delete_failure_can_retry_after_login_credential_is_already_cleared(self):
        cursor=self.credential.with_name('wx-a.sync.json');write_json(cursor,{})
        real=Path.unlink
        def remove(file,*args,**kwargs):
            if file==cursor:raise PermissionError('fixture cursor locked')
            return real(file,*args,**kwargs)
        with patch.object(Path,'unlink',remove):
            with self.assertRaises(PermissionError):unbind(self.store,'wx-a',stop=Mock(),emit=lambda text:None)
        self.assertFalse(self.credential.exists());self.assertTrue(cursor.exists())
        retry=Mock(side_effect=AssertionError('Already stopped'))
        unbind(self.store,'wx-a',stop=retry,emit=lambda text:None)
        retry.assert_not_called();self.assertFalse(cursor.exists())

    def test_starting_service_without_listener_is_not_treated_as_stopped(self):
        runtime=(self.root/'node',self.root/'node_modules/.bin')
        with patch('scripts.unbind_account.project_runtime',return_value=runtime),\
             patch('scripts.unbind_account.project_runtime_environment',return_value={}),\
             patch('scripts.unbind_account.socket.create_connection',side_effect=ConnectionRefusedError()),\
             patch('scripts.unbind_account.subprocess.run',return_value=SimpleNamespace(returncode=1)):
            with self.assertRaises(ValueError):stop_runtime(self.store,'wx-a')
        self.assertTrue(self.credential.exists())

    def test_real_node_bridge_stops_only_target_without_restarting_gateway(self):
        runtime=project_runtime(Path(__file__).resolve().parents[1])
        if not runtime:self.skipTest('Managed Node unavailable')
        binary=self.root/'runtime/node_modules/.bin';binary.mkdir(parents=True)
        (binary/'openclaw.cmd').touch()
        dist=binary.parent/'openclaw/dist';dist.mkdir(parents=True)
        calls=self.root/'rpc-calls.jsonl'
        source='''import fs from 'node:fs';
class GatewayClient {
constructor(options){this.options=options;}
start(){queueMicrotask(()=>this.options.onHelloOk());}
stop(){}
async request(method,params){
fs.appendFileSync(PATH,JSON.stringify({method,params})+'\\n');
if(method==='channels.stop'){if(params.accountId!=='wx-a')throw Error('wrong target');return {};}
if(method==='channels.status')return {channelAccounts:{'openclaw-weixin':[
{accountId:'wx-a',running:false,restartPending:false},{accountId:'wx-b',running:true}]}};
throw Error('Gateway restart or unexpected operation');
}
}
export {GatewayClient as C};
'''.replace('PATH',json.dumps(str(calls)))
        (dist/'client-fixture.mjs').write_text(source,encoding='utf-8')
        write_json(self.store.root/'data/runtime.json',{'enabled':True,'node_dir':str(runtime[0]),'bin_dir':str(binary),'openclaw_version':'2026.9.6'})
        with socket.socket() as server:
            server.bind(('127.0.0.1',0));server.listen()
            config=read_json(self.store.config,{})
            config['gateway']={'mode':'local','port':server.getsockname()[1],'auth':{'mode':'token','token':'fixture-token'}}
            write_json(self.store.config,config)
            started=time.monotonic()
            unbind(self.store,'wx-a','user-a',emit=lambda text:None)
        records=[json.loads(line) for line in calls.read_text().splitlines()]
        self.assertEqual([row['method'] for row in records],['channels.stop','channels.status'])
        self.assertEqual(records[0]['params'],{'channel':'openclaw-weixin','accountId':'wx-a'})
        self.assertTrue(self.other.exists());self.assertFalse(self.credential.exists())
        self.assertLess(time.monotonic()-started,5)


if __name__=='__main__':unittest.main()
