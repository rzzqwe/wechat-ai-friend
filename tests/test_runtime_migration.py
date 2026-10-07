from copy import deepcopy
import unittest
from scripts.migrate_runtime_config import migrate


class RuntimeMigrationTests(unittest.TestCase):
    def test_migration_preserves_sources_routes_and_auth_without_mutating_input(self):
        cfg = {'meta': {'lastTouchedAt': 'old', 'lastTouchedVersion': 'old'},
               'models': {'providers': {'custom-test': {'apiKey': 'fixture'}}},
               'gateway': {'tailscale': {'mode': 'off', 'resetOnExit': False}},
               'agents': {'list': [{'id': 'main', 'default': True},
                                  {'id': 'wechat-ai-test', 'workspace': 'private-workspace', 'agentDir': 'private-agent',
                                   'memorySearch': {'sources': ['memory'], 'extraPaths': [], 'store': {'path': 'old.sqlite'}}}]},
               'bindings': [{'agentId': 'wechat-ai-test', 'match': {'channel': 'openclaw-weixin', 'accountId': 'test'}}]}
        original = deepcopy(cfg)
        result = migrate(cfg)
        self.assertEqual(cfg, original)
        self.assertEqual(result['models'], original['models'])
        self.assertEqual(result['bindings'], original['bindings'])
        agent = result['agents']['entries']['wechat-ai-test']
        self.assertEqual(agent['workspace'], 'private-workspace')
        self.assertEqual(agent['agentDir'], 'private-agent')
        self.assertEqual(agent['memory']['search']['sources'], ['memory'])
        self.assertFalse(agent['memory']['search']['rememberAcrossConversations'])
        self.assertNotIn('path', agent['memory']['search']['store'])
        self.assertEqual(result['agents']['ownership'], 'explicit')
        self.assertEqual(migrate(result), result)

    def test_conflicts_and_active_reset_policy_are_not_silently_discarded(self):
        for cfg in [{'agents': {'list': [], 'entries': {}}},
                    {'agents': {'list': [{'id': 'x'}, {'id': 'x'}]}},
                    {'gateway': {'tailscale': {'mode': 'serve', 'resetOnExit': True}}}]:
            with self.assertRaises(ValueError):
                migrate(cfg)
