import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from account_personas import PersonaStore,read_json,write_json
from scripts.binding_channel import pause,resume,marker_path,owner_alive


class BindingChannelTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.store=PersonaStore(Path(temporary.name)/'project',Path(temporary.name)/'state')
        self.config={'gateway':{'mode':'local'},'agents':{'entries':{'owned':{}}},'bindings':[],
                     'channels':{'openclaw-weixin':{'accounts':{'old':{'enabled':True,'name':'Keep'}}}}}
        write_json(self.store.config,self.config)
        write_json(self.store.state/'openclaw-weixin/accounts.json',['old'])

    def test_pause_disables_global_and_explicit_accounts_then_restores_flags(self):
        pause(self.store,os.getpid())
        channel=read_json(self.store.config,{})['channels']['openclaw-weixin']
        self.assertFalse(channel['enabled']);self.assertFalse(channel['accounts']['old']['enabled'])
        resume(self.store,os.getpid())
        self.assertEqual(read_json(self.store.config,{}),self.config)
        self.assertFalse(marker_path(self.store).exists())

    def test_interrupted_scan_disables_new_unrouted_credential_before_restoring_others(self):
        pause(self.store,123)
        write_json(self.store.state/'openclaw-weixin/accounts.json',['old','new'])
        with patch('scripts.binding_channel.owner_alive',return_value=False):resume(self.store)
        channel=read_json(self.store.config,{})['channels']['openclaw-weixin']
        self.assertTrue(channel['accounts']['old']['enabled'])
        self.assertFalse(channel['accounts']['new']['enabled'])
        self.assertNotIn('enabled',channel)

    def test_committed_route_survives_window_close_without_disabling_bound_account(self):
        pause(self.store,123)
        write_json(self.store.state/'openclaw-weixin/accounts.json',['old','new'])
        config=read_json(self.store.config,{})
        config['bindings']=[{'agentId':'owned','match':{'channel':'openclaw-weixin','accountId':'new'}}]
        write_json(self.store.config,config)
        with patch('scripts.binding_channel.owner_alive',return_value=False):resume(self.store)
        self.assertNotIn('new',read_json(self.store.config,{})['channels']['openclaw-weixin']['accounts'])

    def test_active_scan_and_wrong_owner_cannot_be_resumed_or_replaced(self):
        pause(self.store,os.getpid())
        with self.assertRaises(ValueError):resume(self.store)
        with self.assertRaises(ValueError):resume(self.store,123)
        with self.assertRaises(ValueError):pause(self.store,123)
        self.assertTrue(marker_path(self.store).exists())

    def test_process_check_does_not_signal_or_terminate_its_owner(self):
        self.assertTrue(owner_alive(os.getpid()))
        self.assertFalse(owner_alive(-1))


if __name__=='__main__':unittest.main()
