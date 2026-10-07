from datetime import datetime,timezone,timedelta
import os
from pathlib import Path
import tempfile
import time
import unittest
import json
import shutil
import subprocess
import sys
from runtime_tuning import project_runtime

from account_personas import PersonaStore,read_json,write_json
from scripts.verify_login_receipt import verify,recover_legacy


class LoginReceiptTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        root=Path(temporary.name)
        self.store=PersonaStore(root/'project',root/'state')
        persona=self.store.save('Fixture','# Fixture')
        self.entry=self.store.assign('pending',persona['id'])
        self.receipt=root/'receipt.json'
        self.saved={'attempt':'current-attempt','local_id':'pending','agent_id':self.entry['agent_id'],'provider_id':'wx-fixture','saved':True}
        write_json(self.receipt,self.saved)
        write_json(self.store.state/'openclaw-weixin/accounts.json',['wx-fixture'])
        self.credential=self.store.state/'openclaw-weixin/accounts/wx-fixture.json'
        write_json(self.credential,{'token':'fixture-token','savedAt':datetime.now(timezone.utc).isoformat()})
        self.result=root/'result.json'
        write_json(self.result,{'status':'failed','stage':'微信授权已保存，正在准备专属人格','elapsed_seconds':72,'local_id':'pending'})

    def test_this_attempts_saved_credential_is_accepted_after_process_failure(self):
        self.assertEqual(verify(self.store,self.receipt,'current-attempt','pending'),'wx-fixture')

    def test_old_nonce_other_user_or_changed_companion_cannot_supply_a_receipt(self):
        for changed in ({'attempt':'old-attempt'},{'local_id':'other-user'},{'agent_id':'other-companion'},{'saved':False}):
            write_json(self.receipt,{**self.saved,**changed})
            with self.assertRaises(ValueError):verify(self.store,self.receipt,'current-attempt','pending')

    def test_missing_index_or_credential_is_not_a_success(self):
        write_json(self.store.state/'openclaw-weixin/accounts.json',[])
        with self.assertRaises(ValueError):verify(self.store,self.receipt,'current-attempt','pending')
        write_json(self.store.state/'openclaw-weixin/accounts.json',['wx-fixture'])
        write_json(self.credential,{'token':''})
        with self.assertRaises(ValueError):verify(self.store,self.receipt,'current-attempt','pending')

    def test_previous_saved_stage_can_recover_only_unique_fresh_unrouted_credential(self):
        self.assertEqual(recover_legacy(self.store,self.result),('pending','wx-fixture'))
        write_json(self.store.state/'openclaw-weixin/accounts.json',['wx-fixture','wx-other'])
        write_json(self.credential.with_name('wx-other.json'),read_json(self.credential,{}))
        with self.assertRaises(ValueError):recover_legacy(self.store,self.result)

    def test_old_credential_or_foreign_route_cannot_be_guessed_as_this_login(self):
        value=read_json(self.credential,{})
        write_json(self.credential,{**value,'savedAt':(datetime.now(timezone.utc)-timedelta(days=1)).isoformat()})
        with self.assertRaises(ValueError):recover_legacy(self.store,self.result)
        write_json(self.credential,value)
        config=read_json(self.store.config,{})
        config['bindings']=[{'agentId':'foreign','match':{'accountId':'wx-fixture'}}]
        write_json(self.store.config,config)
        with self.assertRaises(ValueError):recover_legacy(self.store,self.result)

    def test_changed_assignment_or_stage_before_saving_cannot_be_recovered(self):
        timestamp=self.result.stat().st_mtime+1
        os.utime(self.store.assignments_path,(timestamp,timestamp))
        with self.assertRaises(ValueError):recover_legacy(self.store,self.result)
        os.utime(self.store.assignments_path,(timestamp-2,timestamp-2))
        result=read_json(self.result,{})
        write_json(self.result,{**result,'stage':'等待扫码，请在二维码窗口中完成微信授权'})
        with self.assertRaises(ValueError):recover_legacy(self.store,self.result)


class LoginReceiptLauncherTests(unittest.TestCase):
    @unittest.skipUnless(os.name=='nt' and shutil.which('powershell'),'Windows launcher')
    def test_saved_receipt_wins_over_exit_error_but_unsaved_error_cannot_bind(self):
        source=Path(__file__).resolve().parents[1]
        runtime=project_runtime(source)
        if not runtime:self.skipTest('Managed Node unavailable')
        for committed in (True,False):
            with self.subTest(committed=committed),tempfile.TemporaryDirectory() as temporary:
                root=Path(temporary);scripts=root/'scripts';scripts.mkdir();data=root/'data';data.mkdir();state=root/'state';state.mkdir()
                for name in ('启动微信ClawBot.ps1','startup_progress.ps1','verify_login_receipt.py'):
                    shutil.copyfile(source/'scripts'/name,scripts/name)
                write_json(data/'persona-bindings.json',{'user':{'agent_id':'fixture-agent'}})
                config={'agents':{'entries':{},'defaults':{'model':{'primary':'fixture/model'}}},
                        'models':{'providers':{'fixture':{'baseUrl':'https://example.invalid','api':'openai-completions','apiKey':'fixture-key','models':[{'id':'model'}]}}},
                        'plugins':{'entries':{'openclaw-weixin':{'enabled':True}}},'session':{'dmScope':'per-account-channel-peer'}}
                write_json(state/'openclaw.json',config);write_json(state/'openclaw-weixin/accounts.json',[])
                (root/'wechat_bot_app.py').write_text("from pathlib import Path\nimport os\nfrom account_personas import PersonaStore\ndef persona_store():return PersonaStore(Path(__file__).parent,Path(os.environ['OPENCLAW_STATE_DIR']))\n")
                (scripts/'route_wechat_account.py').write_text("import sys,json\nfrom pathlib import Path\nroot=Path(__file__).resolve().parents[1]\nif '--provider-id' in sys.argv:(root/'data/account-bindings.json').write_text(json.dumps({'user':'fixture-id'}))\nprint('Fixture persona ready')\n")
                (scripts/'weixin_environment.ps1').write_text('function Test-WeixinPluginInstalled {return $true}',encoding='utf-8-sig')
                (scripts/'gateway_environment.ps1').write_text("function Set-ProjectGatewayEnvironment {}\nfunction Initialize-ProjectGateway {return $true}\nfunction Invoke-ProjectGateway($Action){return @{Code=0}}\n",encoding='utf-8-sig')
                (scripts/'environment_setup.ps1').write_text(""". (Join-Path $PSScriptRoot 'startup_progress.ps1')
function Invoke-ProjectEnvironment {
param([switch]$Python,[switch]$OpenClaw,[switch]$Weixin)
function global:openclaw {throw 'Unexpected full CLI operation'}
return @{Python=$env:WECHAT_AI_PYTHON;Runtime=@{Version='2026.9.6'};WeixinVerified=$true}
}
""",encoding='utf-8-sig')
                node_script="""import fs from 'node:fs';import path from 'node:path';
if(process.env.FIXTURE_COMMITTED==='1') {
 const base=path.join(process.env.OPENCLAW_STATE_DIR,'openclaw-weixin');fs.mkdirSync(path.join(base,'accounts'),{recursive:true});
 fs.writeFileSync(path.join(base,'accounts.json'),JSON.stringify(['fixture-id']));
 fs.writeFileSync(path.join(base,'accounts/fixture-id.json'),JSON.stringify({token:'fixture-token'}));
 fs.writeFileSync(process.env.WECHAT_AI_LOGIN_RECEIPT,JSON.stringify({attempt:process.env.WECHAT_AI_LOGIN_ATTEMPT,local_id:'user',agent_id:process.env.WECHAT_AI_BINDING_AGENT,provider_id:'fixture-id',saved:true}));
 console.log('__WECHAT_LOGIN_SAVED__');console.log('__WECHAT_PROVIDER_ID__fixture-id');
}
process.exit(1);
"""
                (scripts/'weixin_login_fast.mjs').write_text(node_script)
                result_path=root/'result.json'
                env=dict(os.environ,PYTHONPATH=str(source),WECHAT_AI_PYTHON=sys.executable,
                         WECHAT_AI_NODE=str(runtime[0]/'node.exe'),WECHAT_AI_OPENCLAW_ENTRY=str(root/'fake/openclaw.mjs'),
                         OPENCLAW_STATE_DIR=str(state),OPENCLAW_CONFIG_PATH=str(state/'openclaw.json'),
                         WECHAT_AI_BASE_URL='https://example.invalid',WECHAT_AI_MODEL='model',WECHAT_AI_API_KEY='fixture-key',
                         FIXTURE_COMMITTED='1' if committed else '0')
                process=subprocess.run(['powershell','-NoProfile','-ExecutionPolicy','Bypass','-File',str(scripts/'启动微信ClawBot.ps1'),
                                        '-AccountId','user','-ResultPath',str(result_path),'-NoPause'],env=env,capture_output=True,
                                       text=True,encoding='utf-8',errors='replace',timeout=25)
                result=read_json(result_path,{})
                self.assertEqual(result['status'],'succeeded' if committed else 'failed',process.stdout+process.stderr)
                self.assertEqual((data/'account-bindings.json').exists(),committed)
                self.assertNotIn('fixture-key',process.stdout+process.stderr)


if __name__=='__main__':unittest.main()
