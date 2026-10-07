from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from account_personas import PersonaStore,read_json,write_json
from scripts.configure_text_model import configure


class TextModelConfigurationTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name)
        self.store=PersonaStore(self.root/'project',self.root/'state')
        self.node=self.root/'runtime/node'
        self.binary=self.root/'runtime/node_modules/.bin'
        self.package=self.root/'runtime/node_modules/openclaw'
        self.node.mkdir(parents=True); self.binary.mkdir(parents=True); self.package.mkdir()
        (self.node/'node.exe').touch(); (self.binary/'openclaw.cmd').touch()
        write_json(self.package/'package.json',{'name':'openclaw','version':'2026.9.6'})
        write_json(self.store.root/'data/runtime.json',{'enabled':True,'node_dir':str(self.node),'bin_dir':str(self.binary)})
        self.config={'agents':{'entries':{'main':{},'private-companion':{'workspace':'private-workspace'}},'defaults':{}},
                     'gateway':{'mode':'local','auth':{'mode':'token','token':'fixture-gateway-token'}},
                     'bindings':[{'agentId':'private-companion','match':{'accountId':'private-user'}}],
                     'plugins':{'entries':{'openclaw-weixin':{'enabled':True}}},
                     'models':{'providers':{'other':{'apiKey':'other-key','models':[{'id':'other-model'}]}}}}
        write_json(self.store.config,self.config)
        (self.store.state/'gateway.cmd').write_text('@echo off')
        self.environment=dict(os.environ,WECHAT_AI_BASE_URL='https://example.invalid',WECHAT_AI_MODEL='fixture-model',WECHAT_AI_API_KEY='fixture-key')

    def runner(self, arguments, **options):
        self.assertNotIn('fixture-key',' '.join(arguments))
        self.assertEqual(options['env']['WECHAT_AI_API_KEY'],'fixture-key')
        config=json.loads(options['input'])
        config['models']['providers']['custom-fixture']={'baseUrl':'https://example.invalid','api':'openai-completions','apiKey':'fixture-key','models':[{'id':'fixture-model'}]}
        config['agents']['defaults']['model']={'primary':'custom-fixture/fixture-model'}
        config['agents']['defaults']['models']={'custom-fixture/fixture-model':{}}
        return SimpleNamespace(returncode=0,stdout=json.dumps(config))

    def test_saves_only_model_fields_and_preserves_other_users_and_providers(self):
        self.assertTrue(configure(self.store,self.environment,self.runner))
        updated=read_json(self.store.config,{})
        for field in ('gateway','bindings','plugins'):
            self.assertEqual(updated[field],self.config[field])
        self.assertEqual(updated['agents']['entries'],self.config['agents']['entries'])
        self.assertEqual(updated['models']['providers']['other'],self.config['models']['providers']['other'])
        self.assertEqual(updated['models']['providers']['custom-fixture']['auth'],'api-key')

    def test_second_unchanged_save_does_not_rewrite_config(self):
        configure(self.store,self.environment,self.runner)
        with patch('scripts.configure_text_model.write_json') as write:
            self.assertTrue(configure(self.store,self.environment,self.runner))
        write.assert_not_called()

    def test_first_uninitialized_environment_falls_back_without_mutation(self):
        for changed in ({**self.config,'gateway':{}}, {**self.config,'agents':{}}):
            write_json(self.store.config,changed)
            before=self.store.config.read_bytes()
            self.assertFalse(configure(self.store,self.environment,self.runner))
            self.assertEqual(self.store.config.read_bytes(),before)

    def test_unverified_runtime_or_include_based_config_falls_back(self):
        self.config['$include']='another-config.json'
        write_json(self.store.config,self.config)
        self.assertFalse(configure(self.store,self.environment,self.runner))
        self.config.pop('$include')
        write_json(self.store.config,self.config)
        write_json(self.package/'package.json',{'name':'openclaw','version':'2026.10.1'})
        self.assertFalse(configure(self.store,self.environment,self.runner))

    def test_builder_cannot_change_account_routes_or_gateway(self):
        before=self.store.config.read_bytes()
        def unsafe(arguments,**options):
            result=self.runner(arguments,**options)
            config=json.loads(result.stdout)
            config['bindings']=[]
            return SimpleNamespace(returncode=0,stdout=json.dumps(config))
        with self.assertRaisesRegex(ValueError,'模型之外'):
            configure(self.store,self.environment,unsafe)
        self.assertEqual(self.store.config.read_bytes(),before)

    def test_timeout_failed_builder_or_disk_error_preserves_original_config(self):
        before=self.store.config.read_bytes()
        def timeout(*arguments,**options):
            raise subprocess.TimeoutExpired('fixture',20)
        with self.assertRaises(subprocess.TimeoutExpired):
            configure(self.store,self.environment,timeout)
        with self.assertRaises(ValueError):
            configure(self.store,self.environment,lambda *args,**kwargs:SimpleNamespace(returncode=1,stdout='fixture-key'))
        with patch('account_personas.os.replace',side_effect=OSError('fixture write failure')):
            with self.assertRaises(OSError):
                configure(self.store,self.environment,self.runner)
        self.assertEqual(self.store.config.read_bytes(),before)

    def test_invalid_service_fields_are_rejected_without_exposing_key(self):
        for changed in ({'WECHAT_AI_BASE_URL':'invalid'}, {'WECHAT_AI_MODEL':''}, {'WECHAT_AI_API_KEY':''}):
            with self.assertRaises(ValueError) as error:
                configure(self.store,{**self.environment,**changed},self.runner)
            self.assertNotIn('fixture-key',str(error.exception))


if __name__=='__main__':
    unittest.main()
