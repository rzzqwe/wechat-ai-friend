"""Real Tk binding buttons with temporary accounts; no Gateway or WeChat calls."""
import json
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import wechat_bot_app as api
from account_personas import PersonaStore


with tempfile.TemporaryDirectory() as directory:
    root = Path(directory)
    store = PersonaStore(root / 'project', root / 'state')
    pending = store.save('待绑定的陪伴', '简单的测试人格。')
    other = store.save('已绑定的陪伴', '另一个测试人格。')
    store.assign('pending-user', pending['id'])
    store.assign('other-user', other['id'], 'wechat-other')
    memory = store.workspace('pending-user', pending['id']) / 'MEMORY.md'
    memory.write_text('恢复前已有的私有记忆', encoding='utf-8')
    original = {path: path.read_bytes() for path in (store.config, store.assignments_path, memory)}
    existing = [{'id': 'other-user', 'name': '已绑定的用户', 'status': '已绑定',
                 'last_action': '', 'provider_id': 'wechat-other'}]
    with patch.object(api, 'persona_store', return_value=store), \
            patch.object(api, 'ACCOUNTS_FILE', store.root / 'data/accounts.json'), \
            patch.object(api, 'ACCOUNT_BINDINGS_FILE', store.root / 'data/account-bindings.json'), \
            patch.object(api, 'load_accounts', return_value=existing), \
            patch.object(api, 'load_app_config', return_value={'persona_id': pending['id']}), \
            patch.object(api, 'API_KEY_FILE', root / 'no-key.bin'), \
            patch.object(api.App, 'initialize_voice_runtime', return_value=None), \
            patch.object(api.App, 'start_runtime_monitor', return_value=None):
        workbench = api.App()
        workbench.withdraw()
        try:
            workbench.update()
            assert workbench.account_tree.exists('pending-user')
            assert workbench.account_tree.item('pending-user', 'values')[1] == pending['name']
            # Reproduce the missing row while the workbench is already open.
            workbench.accounts = list(existing)
            api.save_accounts(workbench.accounts)
            workbench.render_accounts()
            assert not workbench.account_tree.exists('pending-user')
            workbench.persona_tree.selection_set(pending['id'])
            workbench.select_persona()
            workbench.base_url_var.set('https://example.invalid')
            workbench.model_var.set('test-model')
            workbench.key_var.set('test-key')
            with patch.object(workbench, 'launch_login') as login, \
                    patch('companion_controls.messagebox.showerror') as error:
                workbench._ui_bind_companion_btn.invoke()
                workbench.update()
                error.assert_not_called()
                assert workbench.account_tree.exists('pending-user')
                assert workbench.account_tree.selection() == ('pending-user',)
                assert len(workbench.accounts) == 2
                login.assert_called_once()
                assert login.call_args.args[0]['id'] == 'pending-user'
                assert login.call_args.args[0]['provider_id'] == ''
            assert original == {path: path.read_bytes() for path in original}
            print(json.dumps({'ok': True, 'startupRestoresMissingRow': True,
                              'bindingButtonRestoresAndRetriesOriginalUser': True,
                              'otherUserAndMemoryPreserved': True}))
        finally:
            workbench.after_cancel(workbench._voice_timer)
            workbench.destroy()
