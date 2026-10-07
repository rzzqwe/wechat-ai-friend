from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from account_personas import PersonaStore,read_json,write_json
from runtime_tuning import tune_companion_plugins
from scripts.prepare_gateway_config import prepare
from scripts.set_account_enabled import set_enabled


class GatewayPreparationTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name)
        self.store=PersonaStore(self.root/'project',self.root/'state')
        self.plugin=self.store.root/'plugins/companion-voice'
        self.plugin.mkdir(parents=True)
        for name in ('package.json','index.mjs'):
            (self.plugin/name).write_text('{}')
        write_json(self.plugin/'openclaw.plugin.json',{'id':'wechat-companion-voice'})
        self.config={'agents':{'entries':{'main':{},'wechat-ai-fixture':{}},'defaults':{'model':{'primary':'custom-fixture/model'}}},
                     'models':{'providers':{'custom-fixture':{'apiKey':'fixture-key'}}},
                     'bindings':[{'agentId':'wechat-ai-fixture','match':{'accountId':'fixture-id'}}],
                     'gateway':{'mode':'local','auth':{'mode':'token','token':'fixture-token'}},
                     'plugins':{'entries':{'wechat-companion-voice':{'enabled':True,'config':{'projectRoot':str(self.store.root)}}},
                                'load':{'paths':[str(self.plugin)]}}}

    def test_only_needed_plugins_are_implicit_and_repeat_is_unchanged(self):
        original=deepcopy(self.config)
        self.assertTrue(tune_companion_plugins(self.config,self.plugin))
        self.assertEqual(self.config['plugins']['allow'],['memory-core','openclaw-weixin','wechat-companion-voice'])
        for key in ('agents','models','bindings','gateway'):
            self.assertEqual(self.config[key],original[key])
        self.assertFalse(tune_companion_plugins(self.config,self.plugin))

    def test_explicit_plugins_channels_and_allow_list_are_preserved(self):
        self.config['plugins']['entries']['operator-extra']={'enabled':True}
        self.config['channels']={'telegram':{'enabled':True}}
        tune_companion_plugins(self.config,self.plugin)
        self.assertIn('operator-extra',self.config['plugins']['allow'])
        self.assertIn('telegram',self.config['plugins']['allow'])
        self.config['plugins']['allow']=['operator-selection']
        tune_companion_plugins(self.config,self.plugin)
        self.assertEqual(self.config['plugins']['allow'],['operator-selection'])

    def test_other_agents_and_native_model_providers_keep_implicit_capabilities(self):
        for changes in ('foreign-agent','native-model','native-provider','utility-model'):
            config=deepcopy(self.config)
            if changes=='foreign-agent':config['agents']['entries']['coding-agent']={}
            elif changes=='native-model':config['agents']['defaults']['model']='openai/gpt-model'
            elif changes=='utility-model':config['agents']['defaults']['utilityModel']='anthropic/model'
            else:config['models']['providers']['openai']={}
            tune_companion_plugins(config,self.plugin)
            self.assertNotIn('allow',config['plugins'])

    def test_duplicate_voice_sources_removed_without_removing_other_plugins(self):
        duplicate=self.root/'old-voice';duplicate.mkdir()
        other=self.root/'other';other.mkdir()
        write_json(duplicate/'openclaw.plugin.json',{'id':'wechat-companion-voice'})
        write_json(other/'openclaw.plugin.json',{'id':'other-plugin'})
        self.config['plugins']['load']['paths']=[str(duplicate),str(other),str(self.plugin),str(self.plugin)]
        tune_companion_plugins(self.config,self.plugin)
        self.assertEqual(self.config['plugins']['load']['paths'],[str(other),str(self.plugin)])
        self.assertTrue((duplicate/'openclaw.plugin.json').is_file())
        self.assertIn('other-plugin',self.config['plugins']['allow'])

    def test_preparation_backups_original_config_only_once(self):
        write_json(self.store.config,self.config)
        self.assertTrue(prepare(self.store))
        backup=self.store.config.with_suffix('.json.before-companion-startup')
        self.assertEqual(read_json(backup,{}),self.config)
        self.assertFalse(prepare(self.store))
        self.assertEqual(read_json(backup,{}),self.config)

    def test_one_account_change_preserves_other_accounts_and_routes(self):
        persona=self.store.save('Fixture','# Fixture')
        self.store.assign('local',persona['id'],'fixture-id')
        write_json(self.store.config,self.config)
        set_enabled(self.store,'fixture-id',False)
        config=read_json(self.store.config,{})
        self.assertFalse(config['channels']['openclaw-weixin']['accounts']['fixture-id']['enabled'])
        self.assertEqual(config['bindings'],self.config['bindings'])
        with self.assertRaises(ValueError):set_enabled(self.store,'foreign-id',True)
        self.assertEqual(read_json(self.store.config,{}),config)


if __name__=='__main__':unittest.main()
