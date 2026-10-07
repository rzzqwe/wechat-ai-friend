"""Interrupted binding recovery with real temporary files and mocked QR launch."""
from pathlib import Path
from types import SimpleNamespace
import sys
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import wechat_bot_app as app
from account_personas import PersonaStore, read_json, write_json


class AccountTree:
    def __init__(self):
        self.ids = set()
        self.selected = ()

    def exists(self, key):
        return key in self.ids

    def selection(self):
        return self.selected

    def selection_set(self, key):
        assert key in self.ids
        self.selected = (key,)

    def see(self, key):
        assert key in self.ids

    def focus_set(self):
        pass


class BindingRecoveryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.store = PersonaStore(self.root / 'project', self.root / 'state')
        self.persona = self.store.save('小伴', '简单的测试人格。')
        for name, value in (
                ('persona_store', lambda: self.store),
                ('ACCOUNTS_FILE', self.store.root / 'data/accounts.json'),
                ('ACCOUNT_BINDINGS_FILE', self.store.root / 'data/account-bindings.json')):
            patched = patch.object(app, name, value)
            patched.start()
            self.addCleanup(patched.stop)

    def state(self, accounts=None):
        state = SimpleNamespace(
            services=app, _ui_busy=False, _login_attempt=None,
            current_persona_id=self.persona['id'], soul_path=self.store.profile_path(self.persona['id']),
            accounts=list(accounts or []), account_tree=AccountTree(), _ui_canvas=Mock(),
            key_var=Mock(get=Mock(return_value='test-key')),
            base_url_var=Mock(get=Mock(return_value='https://example.invalid')),
            model_var=Mock(get=Mock(return_value='test-model')),
            config_data={}, write_log=Mock(), launch_login=Mock(), refresh_personas=Mock(), new_persona=Mock(),
        )
        def render():
            state.account_tree.ids = {account['id'] for account in state.accounts}
        state.render_accounts = Mock(side_effect=render)
        render()
        state.recover_missing_accounts = lambda: app.App.recover_missing_accounts(state)
        state.bind_selected_companion = lambda: app.App.bind_selected_companion(state)
        state.add_account = lambda: app.App.add_account(state)
        state.bind_wechat = lambda: app.App.bind_wechat(state)
        state.selected_account = lambda: app.App.selected_account(state)
        state.provider_account_id = app.App.provider_account_id
        return state

    def test_missing_pending_row_reuses_original_owner_and_opens_its_qr(self):
        entry = self.store.assign('original-user', self.persona['id'])
        workspace = self.store.workspace('original-user', self.persona['id'])
        memory = workspace / 'MEMORY.md'
        memory.write_text('原来的私有记忆', encoding='utf-8')
        before = {path: path.read_bytes() for path in (self.store.assignments_path, self.store.config, memory)}
        state = self.state()
        state.bind_selected_companion()
        self.assertEqual(len(state.accounts), 1)
        account = state.accounts[0]
        self.assertEqual(account['id'], 'original-user')
        self.assertEqual(account['status'], '等待扫码')
        self.assertEqual(account['provider_id'], '')
        self.assertEqual(state.account_tree.selection(), ('original-user',))
        state.launch_login.assert_called_once_with(account)
        self.assertEqual(app.load_accounts(), state.accounts)
        self.assertEqual(self.store.assignments()['original-user'], entry)
        self.assertEqual(before, {path: path.read_bytes() for path in before})

    def test_startup_restores_missing_bound_row_and_preserves_other_user(self):
        self.store.assign('original-user', self.persona['id'], 'wechat-a')
        other = self.store.save('另一位陪伴', '另一个人格。')
        self.store.assign('other-user', other['id'], 'wechat-b')
        existing = {'id': 'other-user', 'name': '自己的账号备注', 'provider_id': 'wechat-b', 'status': '已停用'}
        state = self.state([existing])
        config = self.store.config.read_bytes()
        app.App.initialize_companions(state)
        self.assertEqual(state.accounts[0], existing)
        self.assertEqual(state.accounts[1]['id'], 'original-user')
        self.assertEqual(state.accounts[1]['provider_id'], 'wechat-a')
        self.assertEqual(state.accounts[1]['status'], '已绑定')
        self.assertEqual(self.store.config.read_bytes(), config)
        state.launch_login.assert_not_called()
        app.App.initialize_companions(state)
        self.assertEqual(len(state.accounts), 2)

    def test_recovered_authorized_row_does_not_request_new_wechat_authorization(self):
        self.store.assign('original-user', self.persona['id'], 'wechat-a')
        state = self.state()
        state.bind_selected_companion()
        state.launch_login.assert_not_called()
        self.assertEqual(state.account_tree.selection(), ('original-user',))
        self.assertEqual(state.accounts[0]['provider_id'], 'wechat-a')
        with self.assertRaisesRegex(ValueError, '另一个微信'):
            self.store.assign('original-user', self.persona['id'], 'wechat-b')

    def test_retry_without_selected_account_recovers_existing_owner(self):
        self.store.assign('original-user', self.persona['id'])
        state = self.state()
        state.bind_wechat()
        self.assertEqual([row['id'] for row in state.accounts], ['original-user'])
        state.launch_login.assert_called_once_with(state.accounts[0])

    def test_failed_first_row_save_can_be_retried_without_duplicate_user(self):
        state = self.state()
        save = app.save_accounts
        calls = 0
        def fail_once(accounts):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise OSError('模拟账号记录保存失败')
            save(accounts)
        with patch.object(app, 'save_accounts', side_effect=fail_once), \
                patch('companion_controls.messagebox.showerror') as error:
            state.bind_selected_companion()
            error.assert_called_once()
            state.launch_login.assert_not_called()
            self.assertEqual(state.accounts, [])
            before = self.store.assignments()
            self.assertEqual(len(before), 1)
            state.bind_selected_companion()
        self.assertEqual(self.store.assignments(), before)
        self.assertEqual(state.accounts[0]['id'], next(iter(before)))
        state.launch_login.assert_called_once_with(state.accounts[0])
        self.assertEqual(app.load_accounts(), state.accounts)

    def test_recovery_save_failure_leaves_original_owner_and_no_partial_row(self):
        self.store.assign('original-user', self.persona['id'])
        before = self.store.assignments_path.read_bytes()
        state = self.state()
        with patch.object(app, 'save_accounts', side_effect=OSError('磁盘不可写')), \
                patch('companion_controls.messagebox.showerror') as error:
            state.bind_selected_companion()
        error.assert_called_once()
        self.assertEqual(state.accounts, [])
        self.assertEqual(self.store.assignments_path.read_bytes(), before)
        state.launch_login.assert_not_called()
        self.assertFalse(app.ACCOUNTS_FILE.exists())

    def test_saved_provider_mapping_is_preserved_when_qr_finished_before_assignment_save(self):
        self.store.assign('original-user', self.persona['id'])
        app.save_account_bindings({'original-user': 'wechat-a'})
        state = self.state()
        state.bind_selected_companion()
        self.assertEqual(state.accounts[0]['provider_id'], 'wechat-a')
        state.launch_login.assert_not_called()

    def test_conflicting_wechat_or_agent_ownership_is_not_recovered(self):
        entry = self.store.assign('original-user', self.persona['id'], 'wechat-a')
        owner_path = self.store.state / 'agents' / entry['agent_id'] / 'wechat-ai-owner.json'
        owner = read_json(owner_path, {})
        for changed in ({**owner, 'local_id': 'someone-else'}, {**owner, 'provider_id': 'wechat-b'}):
            with self.subTest(changed=changed):
                write_json(owner_path, changed)
                state = self.state()
                with patch('companion_controls.messagebox.showerror') as error:
                    state.bind_selected_companion()
                error.assert_called_once()
                self.assertEqual(state.accounts, [])
                state.launch_login.assert_not_called()
                self.assertFalse(app.ACCOUNTS_FILE.exists())

    def test_atomic_replace_failure_preserves_previous_account_and_provider_files(self):
        for path, save, initial, changed in (
                (app.ACCOUNTS_FILE, app.save_accounts, [{'id': 'existing', 'name': '已保存账号'}], []),
                (app.ACCOUNT_BINDINGS_FILE, app.save_account_bindings, {'existing': 'wechat-a'}, {})):
            with self.subTest(path=path.name):
                save(initial)
                before = path.read_bytes()
                with patch('account_personas.os.replace', side_effect=OSError('替换文件失败')):
                    with self.assertRaises(OSError):
                        save(changed)
                self.assertEqual(path.read_bytes(), before)
                self.assertEqual(read_json(path, None), initial)
                self.assertEqual(list(path.parent.glob(path.name + '.*.tmp')), [])

    def test_first_binding_file_replace_failure_preserves_previous_user_and_allows_retry(self):
        other = self.store.save('原来的陪伴', '原有用户的人格。')
        self.store.assign('existing-user', other['id'], 'wechat-existing')
        existing = {'id': 'existing-user', 'name': '原有备注', 'provider_id': 'wechat-existing', 'status': '已绑定'}
        app.save_accounts([existing])
        memory = self.store.workspace('existing-user', other['id']) / 'MEMORY.md'
        memory.write_text('原有用户的私有记忆', encoding='utf-8')
        before = {path: path.read_bytes() for path in (self.store.config, self.store.assignments_path, app.ACCOUNTS_FILE, memory)}
        state = self.state([existing])
        import account_personas
        replace_file = account_personas.os.replace
        def fail_binding(source, target):
            if Path(target) == self.store.assignments_path:
                raise OSError('绑定文件提交失败')
            replace_file(source, target)
        with patch('account_personas.os.replace', side_effect=fail_binding), \
                patch('companion_controls.messagebox.showerror') as error:
            state.bind_selected_companion()
        error.assert_called_once()
        self.assertEqual(before, {path: path.read_bytes() for path in before})
        self.assertEqual(state.accounts, [existing])
        state.launch_login.assert_not_called()
        state.bind_selected_companion()
        self.assertEqual(len(state.accounts), 2)
        self.assertEqual(state.accounts[0], existing)
        state.launch_login.assert_called_once_with(state.accounts[1])
        self.assertEqual(memory.read_bytes(), before[memory])

    def interrupt_binding_process(self, stage):
        # Exit the child without exception unwinding, after the runtime config was
        # saved and immediately before/after committing the companion binding file.
        program = '''
import os
from pathlib import Path
import sys
import account_personas as accounts
store = accounts.PersonaStore(Path(sys.argv[1]), Path(sys.argv[2]))
write = accounts.write_json
def interrupted_write(path, value):
    if path == store.assignments_path and sys.argv[4] == 'before_commit':
        os._exit(73)
    write(path, value)
    if path == store.assignments_path and sys.argv[4] == 'after_commit':
        os._exit(74)
accounts.write_json = interrupted_write
store.assign('interrupted-user', sys.argv[3])
'''
        result = subprocess.run(
            [sys.executable, '-B', '-X', 'utf8', '-c', program,
             str(self.store.root), str(self.store.state), self.persona['id'], stage],
            cwd=Path(app.__file__).parent, capture_output=True, text=True,
            encoding='utf-8', errors='replace', timeout=15)
        self.assertEqual(result.returncode, 73 if stage == 'before_commit' else 74, result.stderr)

    def test_process_exit_before_first_binding_commit_does_not_claim_companion(self):
        self.interrupt_binding_process('before_commit')
        self.assertEqual(self.store.assignments(), {})
        self.assertEqual(read_json(self.store.config, {})['bindings'], [])
        abandoned = self.store.workspace('interrupted-user', self.persona['id']) / 'MEMORY.md'
        abandoned.write_text('中断操作留下的记忆', encoding='utf-8')
        state = self.state()
        app.App.initialize_companions(state)
        self.assertEqual(state.accounts, [])
        state.bind_selected_companion()
        self.assertEqual(len(state.accounts), 1)
        self.assertNotEqual(state.accounts[0]['id'], 'interrupted-user')
        state.launch_login.assert_called_once_with(state.accounts[0])
        new_memory = self.store.workspace(state.accounts[0]['id'], self.persona['id']) / 'MEMORY.md'
        self.assertNotIn('中断操作', new_memory.read_text(encoding='utf-8'))
        self.assertEqual(abandoned.read_text(encoding='utf-8'), '中断操作留下的记忆')

    def test_process_exit_after_first_binding_commit_restores_original_pending_record(self):
        self.interrupt_binding_process('after_commit')
        before = self.store.assignments()
        self.assertEqual(list(before), ['interrupted-user'])
        self.assertFalse(app.ACCOUNTS_FILE.exists())
        state = self.state()
        app.App.initialize_companions(state)
        self.assertEqual([row['id'] for row in state.accounts], ['interrupted-user'])
        state.launch_login.assert_not_called()
        state.bind_selected_companion()
        self.assertEqual(self.store.assignments(), before)
        self.assertEqual(len(state.accounts), 1)
        state.launch_login.assert_called_once_with(state.accounts[0])


if __name__ == '__main__':
    unittest.main()
