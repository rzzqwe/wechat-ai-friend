from pathlib import Path
import json
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from account_personas import PersonaStore, read_json, write_json
from reply_contract import FACTUAL_REPLY_RULES


class AccountPersonaTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = PersonaStore(self.root / 'project', self.root / 'state')
        self.a = self.store.save('小晴', '# 小晴' + chr(10) + '喜欢散步。')
        self.b = self.store.save('小雨', '# 小雨' + chr(10) + '喜欢读书。')

    def assign_two(self):
        a = self.store.assign('user', self.a['id'], 'wechat_a')
        b = self.store.assign('user-2', self.b['id'], 'wechat_b')
        return a, b

    def test_two_users_have_distinct_agents_workspaces_and_memory_indexes(self):
        a, b = self.assign_two()
        self.assertNotEqual(a['agent_id'], b['agent_id'])
        cfg = read_json(self.store.config, {})
        agents = {item['id']: item for item in cfg['agents']['list']}
        self.assertNotEqual(agents[a['agent_id']]['workspace'], agents[b['agent_id']]['workspace'])
        self.assertNotEqual(agents[a['agent_id']]['agentDir'], agents[b['agent_id']]['agentDir'])
        self.assertNotEqual(agents[a['agent_id']]['memorySearch']['store']['path'], agents[b['agent_id']]['memorySearch']['store']['path'])
        routes = {row['match']['accountId']: row['agentId'] for row in cfg['bindings']}
        self.assertEqual(routes, {'wechat_a': a['agent_id'], 'wechat_b': b['agent_id']})
        self.assertEqual(cfg['session']['dmScope'], 'per-account-channel-peer')
        self.assertTrue(agents['main']['default'])

    def test_duplicate_wechat_or_persona_is_rejected_without_mutation(self):
        self.store.assign('user', self.a['id'], 'wechat_a')
        before = self.store.config.read_bytes()
        for persona, provider in [(self.b['id'], 'wechat_a'), (self.a['id'], 'wechat_b')]:
            with self.assertRaises(ValueError):
                self.store.assign('other', persona, provider)
            self.assertEqual(self.store.config.read_bytes(), before)

    def test_updating_one_persona_leaves_other_memory_and_soul_unchanged(self):
        self.assign_two()
        workspace = self.store.workspace('user-2', self.b['id'])
        (workspace / 'MEMORY.md').write_text('用户 B 的个人记忆', encoding='utf-8')
        before = {f.name: f.read_bytes() for f in workspace.iterdir() if f.is_file()}
        self.store.save('小晴新版', '# 新设定', self.a['id'])
        self.store.sync(self.a['id'])
        after = {f.name: f.read_bytes() for f in workspace.iterdir() if f.is_file()}
        self.assertEqual(before, after)
        self.assertEqual((self.store.workspace('user', self.a['id']) / 'SOUL.md').read_text(encoding='utf-8'), '# 新设定')

    def test_switch_persona_gets_separate_history_and_restores_previous_memory(self):
        original = self.store.assign('user', self.a['id'], 'wechat_a')
        memory = self.store.workspace('user', self.a['id']) / 'MEMORY.md'
        memory.write_text('原人格的记忆', encoding='utf-8')
        switched = self.store.assign('user', self.b['id'])
        self.assertNotEqual(original['agent_id'], switched['agent_id'])
        self.assertNotIn('原人格', (self.store.workspace('user', self.b['id']) / 'MEMORY.md').read_text(encoding='utf-8'))
        restored = self.store.assign('user', self.a['id'])
        self.assertEqual(restored['agent_id'], original['agent_id'])
        self.assertEqual(memory.read_text(encoding='utf-8'), '原人格的记忆')
        self.assertEqual(len(read_json(self.store.config, {})['bindings']), 1)

    def test_existing_non_wechat_settings_and_default_agent_are_preserved(self):
        original = {'models': {'providers': {}}, 'agents': {'list': [{'id': 'work', 'default': True, 'workspace': 'elsewhere'}]}, 'bindings': [{'agentId': 'work', 'match': {'channel': 'telegram', 'accountId': 'existing'}}]}
        write_json(self.store.config, original)
        self.store.assign('user', self.a['id'], 'wechat_a')
        cfg = read_json(self.store.config, {})
        self.assertEqual(cfg['agents']['list'][0], original['agents']['list'][0])
        self.assertEqual(cfg['models'], original['models'])
        self.assertIn(original['bindings'][0], cfg['bindings'])

    def test_conflicting_external_route_is_not_overwritten(self):
        for match in [{'channel': 'openclaw-weixin', 'accountId': 'wechat_a'}, {'channel': 'openclaw-weixin', 'accountId': '*', 'peer': {'kind': 'direct', 'id': 'someone'}}]:
            write_json(self.store.config, {'bindings': [{'agentId': 'external', 'match': match}]})
            before = self.store.config.read_bytes()
            with self.assertRaisesRegex(ValueError, '路由'):
                self.store.assign('user', self.a['id'], 'wechat_a')
            self.assertEqual(before, self.store.config.read_bytes())

    def test_file_and_cross_agent_tools_are_restricted(self):
        a = self.store.assign('user', self.a['id'])
        agent = next(x for x in read_json(self.store.config, {})['agents']['list'] if x['id'] == a['agent_id'])
        self.assertTrue(agent['tools']['fs']['workspaceOnly'])
        for tool in ['exec', 'sessions_history', 'sessions_send', 'sessions_spawn']:
            self.assertIn(tool, agent['tools']['deny'])
        self.assertEqual(agent['memorySearch']['extraPaths'], [])

    def test_default_user_memory_is_never_copied(self):
        default = self.store.state / 'workspace'
        default.mkdir(parents=True)
        (default / 'MEMORY.md').write_text('别人的秘密', encoding='utf-8')
        self.store.assign('user', self.a['id'])
        self.assertNotIn('别人的秘密', (self.store.workspace('user', self.a['id']) / 'MEMORY.md').read_text(encoding='utf-8'))

    def test_reassign_refreshes_reply_rules_without_replacing_private_memory(self):
        self.store.assign('user', self.a['id'])
        workspace = self.store.workspace('user', self.a['id'])
        (workspace / 'AGENTS.md').write_text('旧运行约定', encoding='utf-8')
        (workspace / 'MEMORY.md').write_text('用户自己的已确认信息', encoding='utf-8')
        self.store.assign('user', self.a['id'])
        self.assertIn(FACTUAL_REPLY_RULES, (workspace / 'AGENTS.md').read_text(encoding='utf-8'))
        self.assertEqual((workspace / 'MEMORY.md').read_text(encoding='utf-8'), '用户自己的已确认信息')

    def test_history_uses_exact_agent_not_account_substring(self):
        a, b = self.assign_two()
        for entry, session in [(a, 'aaa'), (b, 'bbb')]:
            folder = self.store.state / 'agents' / entry['agent_id'] / 'sessions'
            folder.mkdir(parents=True)
            write_json(folder / 'sessions.json', {'anything': {'sessionId': session}, 'unsafe': {'sessionId': '../outside'}})
            (folder / (session + '.jsonl')).write_text('{}', encoding='utf-8')
        self.assertEqual([p.name for p in self.store.transcripts('user')], ['aaa.jsonl'])
        self.assertEqual([p.name for p in self.store.transcripts('user-2')], ['bbb.jsonl'])
        self.assertEqual(self.store.transcripts('unknown'), [])

    def test_detach_preserves_other_routes_and_all_private_memories(self):
        a, b = self.assign_two()
        self.store.detach('user')
        cfg = read_json(self.store.config, {})
        self.assertEqual([row['agentId'] for row in cfg['bindings']], [b['agent_id']])
        self.assertTrue(self.store.workspace('user', self.a['id']).is_dir())
        self.assertNotIn('user', self.store.assignments())

    def test_pending_qr_does_not_route_default_or_wildcard_account(self):
        self.store.assign('user', self.a['id'])
        self.assertEqual(read_json(self.store.config, {})['bindings'], [])

    def test_new_runtime_preserves_explicit_routes_and_private_memory(self):
        write_json(self.store.config, {'agents': {'entries': {'main': {'workspace': str(self.store.state / 'workspace')}}},
                                      'memory': {'search': {'extraPaths': ['/shared']}}})
        a, b = self.assign_two()
        cfg = read_json(self.store.config, {})
        self.assertNotIn('list', cfg['agents'])
        self.assertEqual(cfg['agents']['ownership'], 'explicit')
        for entry in (a, b):
            agent = cfg['agents']['entries'][entry['agent_id']]
            self.assertNotIn('id', agent)
            self.assertNotIn('memorySearch', agent)
            self.assertFalse(agent['memory']['search']['enabled'])
            self.assertFalse(agent['memory']['search']['rememberAcrossConversations'])
            self.assertEqual(agent['memory']['search']['extraPaths'], [])
            self.assertNotIn('store', agent['memory']['search'])
            self.assertTrue(agent['tools']['fs']['workspaceOnly'])
        self.assertNotEqual(cfg['agents']['entries'][a['agent_id']]['agentDir'],
                            cfg['agents']['entries'][b['agent_id']]['agentDir'])
        self.store.assign('user', self.a['id'], 'wechat_a')
        self.assertEqual(len(read_json(self.store.config, {})['bindings']), 2)

    def test_invalid_paths_and_corrupt_config_are_rejected(self):
        for local in ['../escape', '', 'a/b']:
            with self.assertRaises(ValueError):
                self.store.assign(local, self.a['id'])
        self.store.config.parent.mkdir(parents=True, exist_ok=True)
        self.store.config.write_text('broken', encoding='utf-8')
        with self.assertRaises(ValueError):
            self.store.assign('user', self.a['id'])
        self.assertEqual(self.store.config.read_text(encoding='utf-8'), 'broken')

    def test_normalized_account_collision_is_rejected(self):
        self.store.assign('user', self.a['id'], 'We.Chat')
        before = self.store.config.read_bytes()
        with self.assertRaisesRegex(ValueError, '重复分配'):
            self.store.assign('other', self.b['id'], 'we-chat')
        self.assertEqual(self.store.config.read_bytes(), before)

    def test_same_user_cannot_be_silently_rebound_to_another_wechat(self):
        self.store.assign('user', self.a['id'], 'wechat_a')
        original = self.store.config.read_bytes()
        with self.assertRaisesRegex(ValueError, '另一个微信'):
            self.store.assign('user', self.a['id'], 'wechat_b')
        self.assertEqual(self.store.config.read_bytes(), original)

    def test_deleted_user_id_stays_reserved_and_old_memory_cannot_be_reassigned(self):
        self.store.assign('user', self.a['id'], 'wechat_a')
        self.store.detach('user')
        self.assertIn('user', self.store.used_local_ids())
        with self.assertRaisesRegex(ValueError, '另一个微信'):
            self.store.assign('user', self.a['id'], 'wechat_b')

    def test_shared_memory_paths_disable_shared_search_only_for_managed_agent(self):
        defaults = {'memorySearch': {'extraPaths': ['/shared/private-notes']}}
        write_json(self.store.config, {'agents': {'defaults': defaults}})
        entry = self.store.assign('user', self.a['id'])
        cfg = read_json(self.store.config, {})
        agent = next(item for item in cfg['agents']['list'] if item['id'] == entry['agent_id'])
        self.assertFalse(agent['memorySearch']['enabled'])
        self.assertEqual(cfg['agents']['defaults'], defaults)

    def test_binding_write_failure_restores_routing_config(self):
        self.store.assign('user', self.a['id'])
        original = self.store.config.read_bytes()
        from account_personas import write_json as actual
        def fail_assignment(path, value):
            if path == self.store.assignments_path:
                raise OSError('simulated write failure')
            return actual(path, value)
        with patch('account_personas.write_json', side_effect=fail_assignment):
            with self.assertRaises(OSError):
                self.store.assign('user', self.b['id'], 'wechat_a')
        self.assertEqual(original, self.store.config.read_bytes())


if __name__ == '__main__':
    unittest.main()
