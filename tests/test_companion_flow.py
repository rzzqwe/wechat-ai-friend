from pathlib import Path
from types import SimpleNamespace
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import wechat_bot_app as app
import wechat_ui as ui
from account_personas import PersonaStore


class MemoryTree:
    def __init__(self):
        self.rows = {}
        self.selected = ()

    def get_children(self):
        return tuple(self.rows)

    def delete(self, key):
        self.rows.pop(key)
        self.selected = tuple(item for item in self.selected if item != key)

    def insert(self, parent, where, iid, values, tags=()):
        self.rows[iid] = values

    def selection(self):
        return self.selected

    def selection_set(self, key):
        self.selected = (key,) if isinstance(key, str) else tuple(key)

    def see(self, key):
        pass


class CompanionFlowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.profile = self.folder / 'reference_persona.md'
        self.body = '## 说话方式' + chr(10) + '保留已复核的表达、事实和未知项。' + chr(10)
        self.profile.write_text('# 原设定标题' + chr(10) + self.body, encoding='utf-8')
        self.patch = patch.object(app, 'COMPANION_PROFILE', self.profile)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.store = PersonaStore(self.folder / 'project', self.folder / 'state')
        self.store_patch = patch.object(app, 'persona_store', return_value=self.store)
        self.store_patch.start()
        self.addCleanup(self.store_patch.stop)

    def make_state(self):
        state = SimpleNamespace(
            _ui_busy=False, soul_path=None, services=app, current_persona_id=None, selected_profile_path=self.profile,
            draft_source_persona_id=None, draft_source_name=None, accounts=[],
            persona_tree=Mock(selection=Mock(return_value=())), persona_choices=[], refresh_personas=Mock(), render_accounts=Mock(),
            _ui_canvas=Mock(), name_entry=Mock(),
            name_var=Mock(get=Mock(return_value='小伴')),
            key_var=Mock(get=Mock(return_value='fake-key')),
            base_url_var=Mock(get=Mock(return_value='unused')),
            model_var=Mock(get=Mock(return_value='test-model')),
            remember_key_var=Mock(get=Mock(return_value=False)),
            write_log=Mock(), ui_log=Mock(), _busy=Mock(), _generate_worker=Mock(),
            after=lambda delay, callback: callback(),
        )
        state.name_var.set.side_effect = lambda value: setattr(state.name_var.get, 'return_value', value)
        state.new_persona = lambda: app.App.new_persona(state)
        state.select_persona = lambda: app.App.select_persona(state)
        state.recover_missing_accounts = lambda: app.App.recover_missing_accounts(state)
        state._finish_generation = lambda item: app.App._finish_generation(state, item)
        return state

    def test_name_is_applied_without_rewriting_prepared_body(self):
        original = self.profile.read_bytes()
        soul = app.prepared_companion_soul('  小伴  ')
        self.assertTrue(soul.startswith('# 小伴' + chr(10)))
        self.assertIn('陪伴称呼是「小伴」', soul)
        self.assertTrue(soul.endswith(self.body))
        self.assertEqual(self.profile.read_bytes(), original)

    def test_blank_name_is_rejected(self):
        with self.assertRaisesRegex(ValueError, '填写陪伴名称'):
            app.prepared_companion_soul('   ' + chr(10))

    def test_generation_reads_latest_prepared_settings(self):
        app.prepared_companion_soul('小伴')
        self.profile.write_text('新的陪伴设定', encoding='utf-8')
        self.assertTrue(app.prepared_companion_soul('小伴').endswith('新的陪伴设定'))

    def test_missing_or_blank_settings_do_not_create_generic_persona(self):
        self.profile.unlink()
        with self.assertRaisesRegex(ValueError, '还没有整理好的'):
            app.prepared_companion_soul('小伴')
        self.profile.write_text('   ', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, '文件为空'):
            app.prepared_companion_soul('小伴')

    def test_name_and_file_start_new_generation_without_reusing_selected_persona_id(self):
        state = self.make_state()
        state.current_persona_id = 'existing-persona'
        with patch.object(app.threading, 'Thread') as thread:
            app.App.generate(state)
        thread.assert_called_once_with(
            target=state._generate_worker, args=('小伴', 'fake-key', 'unused', 'test-model', False, None, self.profile), daemon=True)
        thread.return_value.start.assert_called_once()
        self.assertTrue(state._ui_busy)
        state._busy.assert_called_once_with(True)

    def test_blank_name_or_busy_state_does_not_start_worker(self):
        state = self.make_state()
        state.name_var.get.return_value = '   '
        with patch.object(app.threading, 'Thread') as thread, patch.object(app.messagebox, 'showwarning') as warning:
            app.App.generate(state)
            warning.assert_called_once()
            state._ui_busy = True
            state.name_var.get.return_value = '小伴'
            app.App.generate(state)
        thread.assert_not_called()

    def test_prepared_generation_does_not_call_ai_or_import_messages(self):
        state = self.make_state()
        target = self.folder / 'SOUL.md'
        with patch.object(app, 'save_soul', return_value=target) as save, patch.object(app, 'save_app_config') as config, patch.object(app, 'API_KEY_FILE', self.folder / 'key.bin'), patch.object(app, 'model_soul') as model, patch.object(app, 'load_chat_files') as imports, patch.object(app, 'write_persona_index') as index:
            app.App._generate_worker(state, '小伴', 'fake-key', 'unused', 'test-model', False, None, self.profile)
        save.assert_not_called()
        persona = self.store.personas()[0]
        self.assertEqual(self.store.profile_path(persona['id']).read_text(encoding='utf-8'), app.prepared_companion_soul('小伴'))
        config.assert_called_once_with({'name': '小伴', 'base_url': 'unused', 'model': 'test-model', 'persona_id': persona['id']})
        model.assert_not_called()
        imports.assert_not_called()
        index.assert_not_called()
        self.assertEqual(state.soul_path, self.store.profile_path(persona['id']))
        state._busy.assert_called_once_with(False)

    def test_bad_settings_preserve_active_persona_and_unlock_controls(self):
        state = self.make_state()
        state.soul_path = self.folder / 'existing.md'
        self.profile.write_text('', encoding='utf-8')
        with patch.object(app, 'save_soul') as save, patch.object(app.messagebox, 'showerror') as error:
            app.App._generate_worker(state, '小伴', '', 'unused', 'test-model', False, None, self.profile)
        save.assert_not_called()
        error.assert_called_once()
        self.assertEqual(state.soul_path, self.folder / 'existing.md')
        state._busy.assert_called_once_with(False)

    def test_generate_button_requires_name_and_file_and_not_busy(self):
        state = self.make_state()
        state.accounts = []
        state.account_tree = Mock(selection=Mock(return_value=()))
        state._ui_selection_buttons = []
        for field in ('_ui_status', '_ui_key_status', '_ui_account_count', '_ui_empty_accounts', '_ui_step_status', 'generate_btn'):
            setattr(state, field, Mock())
        for name, source, busy, expected in [('小伴', self.profile, False, 'normal'), (' ', self.profile, False, 'disabled'), ('小伴', self.profile, True, 'disabled'), ('小伴', None, False, 'disabled')]:
            with self.subTest(name=name, source=source, busy=busy):
                state.name_var.get.return_value = name
                state.selected_profile_path = source
                state._ui_busy = busy
                ui.refresh_ui_state(state)
                state.generate_btn.configure.assert_called_with(state=expected)

    def test_file_selection_stages_new_persona_without_modifying_existing_one(self):
        existing = self.store.save('现有陪伴', '# 现有陪伴' + chr(10) + '原始性格')
        original = self.store.profile_path(existing['id']).read_bytes()
        selected = self.folder / '另一个人格.md'
        selected.write_text('# 温柔的小雨' + chr(10) + '说话温柔、简洁。', encoding='utf-8')
        state = self.make_state()
        state.current_persona_id = existing['id']
        state.soul_path = self.store.profile_path(existing['id'])
        with patch('companion_controls.filedialog.askopenfilename', return_value=str(selected)):
            app.App.choose_persona_file(state)
        self.assertEqual(state.selected_profile_path, selected.resolve())
        self.assertEqual(state.current_persona_id, existing['id'])
        self.assertEqual(state.soul_path, self.store.profile_path(existing['id']))
        self.assertEqual(state.name_var.get(), '小伴')
        state.name_var.set.assert_not_called()
        self.assertEqual(self.store.profile_path(existing['id']).read_bytes(), original)
        self.assertEqual(len(self.store.personas()), 1)

    def test_cancel_invalid_or_busy_file_selection_preserves_current_state(self):
        for selected in ('', str(self.folder / 'missing.md'), str(self.folder / 'empty.txt')):
            with self.subTest(selected=selected):
                (self.folder / 'empty.txt').write_text(' ', encoding='utf-8')
                state = self.make_state()
                state.current_persona_id = 'unchanged'
                state.soul_path = self.profile
                with patch('companion_controls.filedialog.askopenfilename', return_value=selected), patch.object(app.messagebox, 'showerror'):
                    app.App.choose_persona_file(state)
                self.assertEqual(state.current_persona_id, 'unchanged')
                self.assertEqual(state.soul_path, self.profile)
                state.name_var.set.assert_not_called()
        state._ui_busy = True
        with patch('companion_controls.filedialog.askopenfilename') as picker:
            app.App.choose_persona_file(state)
        picker.assert_not_called()

    def test_generation_captures_exact_selected_file_even_with_duplicate_names(self):
        selected = self.folder / 'second' / 'SOUL.md'
        selected.parent.mkdir()
        selected.write_text('只使用第二份文件的性格。', encoding='utf-8')
        state = self.make_state()
        state.selected_profile_path = selected
        with patch.object(app.threading, 'Thread') as thread:
            app.App.generate(state)
        self.assertEqual(thread.call_args.kwargs['args'][-1], selected)

    def test_external_file_generates_independent_copy_without_ai_or_overwriting(self):
        existing = self.store.save('现有陪伴', '# 原陪伴' + chr(10) + '保持原性格。')
        self.store.assign('user', existing['id'])
        original_profile = self.store.profile_path(existing['id']).read_bytes()
        original_bindings = self.store.assignments()
        selected = self.folder / '外部人格.txt'
        selected.write_text('这是外部人格独有的表达习惯。', encoding='utf-8')
        original_source = selected.read_bytes()
        state = self.make_state()
        state.selected_profile_path = selected
        with patch.object(app, 'save_app_config'), patch.object(app, 'API_KEY_FILE', self.folder / 'key.bin'), patch.object(app, 'model_soul') as model, patch.object(app, 'load_chat_files') as imports:
            app.App._generate_worker(state, '新陪伴', '', 'unused', 'test-model', False, existing['id'], selected)
        self.assertNotEqual(state.current_persona_id, existing['id'])
        self.assertIn('外部人格独有', state.soul_path.read_text(encoding='utf-8'))
        self.assertNotIn(self.body, state.soul_path.read_text(encoding='utf-8'))
        self.assertEqual(selected.read_bytes(), original_source)
        self.assertEqual(self.store.profile_path(existing['id']).read_bytes(), original_profile)
        self.assertEqual(self.store.assignments(), original_bindings)
        self.assertIsNone(state.selected_profile_path)
        model.assert_not_called()
        imports.assert_not_called()

    def test_missing_selected_file_does_not_fall_back_to_default_persona(self):
        state = self.make_state()
        selected = self.folder / 'deleted.md'
        state.selected_profile_path = selected
        with patch.object(app.messagebox, 'showerror') as error, patch.object(app, 'save_app_config') as config:
            app.App._generate_worker(state, '新陪伴', '', 'unused', 'test-model', False, None, selected)
        self.assertEqual(self.store.personas(), [])
        error.assert_called_once()
        config.assert_not_called()
        self.assertEqual(state.selected_profile_path, selected)

    def test_library_selection_preserves_draft_and_clear_draft_preserves_library_selection(self):
        existing = self.store.save('已保存陪伴', '独立设定')
        state = self.make_state()
        state.selected_profile_path = self.profile
        state.persona_choices = [existing]
        state.persona_tree.selection.return_value = (existing['id'],)
        app.App.select_persona(state)
        self.assertEqual(state.selected_profile_path, self.profile)
        self.assertEqual(state.current_persona_id, existing['id'])
        self.assertEqual(state.name_var.get(), '小伴')
        app.App.new_persona(state)
        self.assertIsNone(state.selected_profile_path)
        self.assertEqual(state.current_persona_id, existing['id'])
        self.assertEqual(state.soul_path, self.store.profile_path(existing['id']))
        self.assertEqual(state.name_var.get(), '')

    def test_plain_text_encodings_and_invalid_file_content(self):
        selected = self.folder / '人格.txt'
        for encoding in ('utf-8-sig', 'utf-16'):
            with self.subTest(encoding=encoding):
                selected.write_text('具体人格设定。', encoding=encoding)
                self.assertIn('具体人格设定。', app.prepared_companion_soul('小伴', selected))
        selected.write_bytes(bytes((255, 0, 128)))
        with self.assertRaisesRegex(ValueError, '编码'):
            app.prepared_companion_soul('小伴', selected)
        selected.write_text('含有' + chr(0) + '二进制', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, '纯文本'):
            app.prepared_companion_soul('小伴', selected)
        selected.write_text('# 只有标题', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, '只有标题'):
            app.prepared_companion_soul('小伴', selected)

    def test_regenerating_saved_file_replaces_name_without_duplicate_introduction(self):
        selected = self.folder / 'saved.md'
        selected.write_text(app.prepared_companion_soul('旧名字'), encoding='utf-8')
        soul = app.prepared_companion_soul('新名字', selected)
        self.assertEqual(soul.count('你的陪伴称呼是'), 1)
        self.assertNotIn('旧名字', soul)
        self.assertTrue(soul.endswith(self.body))

    def test_name_without_selected_source_cannot_create_companion(self):
        state = self.make_state()
        state.selected_profile_path = None
        with patch.object(app.threading, 'Thread') as thread, patch.object(app.messagebox, 'showwarning') as warning:
            app.App.generate(state)
        thread.assert_not_called()
        warning.assert_called_once()
        self.assertFalse(state._ui_busy)

    def test_all_saved_companions_are_listed_with_exact_owner_and_unique_id(self):
        first = self.store.save('同名陪伴', '第一份设定')
        second = self.store.save('同名陪伴', '第二份设定')
        self.store.assign('owner-a', first['id'])
        state = self.make_state()
        state.persona_tree = MemoryTree()
        state.accounts = [{'id': 'owner-a', 'name': '小王', 'status': '等待扫码'}]
        original_draft = state.selected_profile_path
        app.App.refresh_personas(state, second['id'])
        self.assertEqual(set(state.persona_tree.rows), {first['id'], second['id']})
        self.assertEqual(state.persona_tree.rows[first['id']], ('同名陪伴', first['id'][:6], '小王', '等待扫码'))
        self.assertEqual(state.persona_tree.rows[second['id']], ('同名陪伴', second['id'][:6], '未绑定', '待扫码绑定'))
        self.assertEqual(state.current_persona_id, second['id'])
        self.assertEqual(state.selected_profile_path, original_draft)
        app.App.refresh_personas(state)
        self.assertEqual(len(state.persona_tree.rows), 2)
        self.assertEqual(state.current_persona_id, second['id'])

    def test_clone_keeps_identical_persona_but_has_independent_owner_and_memory(self):
        source = self.store.save('原陪伴', app.prepared_companion_soul('原陪伴'))
        original_binding = self.store.assign('owner-a', source['id'], 'wechat-a')
        original_workspace = self.store.workspace('owner-a', source['id'])
        (original_workspace / 'MEMORY.md').write_text('甲用户的私有记忆', encoding='utf-8')
        original_profile = self.store.profile_path(source['id']).read_bytes()
        state = self.make_state()
        state.current_persona_id = source['id']
        state.soul_path = self.store.profile_path(source['id'])
        state.name_var.get.return_value = ''
        app.App.clone_selected_companion(state)
        self.assertEqual(state.name_var.get(), '原陪伴')
        self.assertEqual(state.draft_source_persona_id, source['id'])
        self.assertEqual(state.selected_profile_path, self.store.profile_path(source['id']))
        self.assertEqual(len(self.store.personas()), 1)
        with patch.object(app, 'save_app_config'), patch.object(app, 'API_KEY_FILE', self.folder / 'key.bin'):
            app.App._generate_worker(state, state.name_var.get(), '', 'unused', 'test-model', False, None, state.selected_profile_path)
        clone_id = state.current_persona_id
        self.assertNotEqual(clone_id, source['id'])
        self.assertEqual(self.store.profile_path(clone_id).read_bytes(), original_profile)
        self.assertEqual(self.store.assignments(), {'owner-a': original_binding})
        cloned_binding = self.store.assign('owner-b', clone_id, 'wechat-b')
        cloned_workspace = self.store.workspace('owner-b', clone_id)
        self.assertNotEqual(cloned_binding['agent_id'], original_binding['agent_id'])
        self.assertNotIn('甲用户', (cloned_workspace / 'MEMORY.md').read_text(encoding='utf-8'))
        (cloned_workspace / 'MEMORY.md').write_text('乙用户的记忆', encoding='utf-8')
        self.assertEqual((original_workspace / 'MEMORY.md').read_text(encoding='utf-8'), '甲用户的私有记忆')
        self.assertEqual(self.store.profile_path(source['id']).read_bytes(), original_profile)
        self.assertEqual(state.name_var.get(), '')
        self.assertIsNone(state.selected_profile_path)
        self.assertIsNone(state.draft_source_persona_id)

    def test_clone_preserves_user_entered_new_name_and_missing_source_preserves_draft(self):
        source = self.store.save('旧名字', '已有陪伴的性格')
        state = self.make_state()
        state.current_persona_id = source['id']
        state.name_var.get.return_value = '新名字'
        app.App.clone_selected_companion(state)
        self.assertEqual(state.name_var.get(), '新名字')
        old_path = state.selected_profile_path
        self.store.profile_path(source['id']).unlink()
        with patch.object(app.messagebox, 'showerror') as error:
            app.App.clone_selected_companion(state)
        error.assert_called_once()
        self.assertEqual(state.selected_profile_path, old_path)
        self.assertEqual(state.name_var.get(), '新名字')

    def test_each_new_companion_starts_qr_binding_for_its_own_new_user(self):
        original = self.store.save('陪伴', '共同的性格')
        copied = self.store.save('陪伴', '共同的性格')
        old_entry = self.store.assign('owner-a', original['id'], 'wechat-a')
        state = self.make_state()
        state.current_persona_id = copied['id']
        state.soul_path = self.store.profile_path(copied['id'])
        state.accounts = [{'id': 'owner-a', 'name': '用户甲', 'status': '已启用'}]
        state.account_tree = Mock()
        state.launch_login = Mock()
        with patch.object(app.simpledialog, 'askstring') as ask, patch.object(app, 'save_accounts'):
            app.App.add_account(state)
        new_user = state.accounts[-1]
        ask.assert_not_called()
        self.assertEqual(new_user['name'], copied['name'])
        self.assertNotEqual(new_user['id'], 'owner-a')
        self.assertEqual(self.store.assignments()[new_user['id']]['persona_id'], copied['id'])
        self.assertEqual(self.store.assignments()['owner-a'], old_entry)
        state.launch_login.assert_called_once_with(new_user)

    def test_bound_companion_does_not_create_second_user_or_qr(self):
        original = self.store.save('陪伴', '性格设定')
        self.store.assign('owner-a', original['id'])
        state = self.make_state()
        state.current_persona_id = original['id']
        state.soul_path = self.store.profile_path(original['id'])
        state.launch_login = Mock()
        state.bind_selected_companion = Mock()
        with patch.object(app.simpledialog, 'askstring') as ask:
            app.App.add_account(state)
        ask.assert_not_called()
        state.bind_selected_companion.assert_called_once()
        state.launch_login.assert_not_called()

    def test_bound_library_row_opens_its_user_and_unbound_row_starts_binding(self):
        source = self.store.save('甲', '甲的性格')
        self.store.assign('owner-a', source['id'], 'wechat-a')
        state = self.make_state()
        state.current_persona_id = source['id']
        state.accounts = [{'id': 'owner-a', 'name': source['name'], 'provider_id': 'wechat-a', 'status': '已绑定'}]
        state.provider_account_id = Mock(return_value='wechat-a')
        state.account_tree = Mock(exists=Mock(return_value=True))
        state.add_account = Mock()
        app.App.bind_selected_companion(state)
        state.account_tree.selection_set.assert_called_once_with('owner-a')
        state.add_account.assert_not_called()
        state.current_persona_id = self.store.save('乙', '乙的性格')['id']
        app.App.bind_selected_companion(state)
        state.add_account.assert_called_once()

    def test_pending_companion_retries_existing_account_without_creating_a_copy(self):
        source = self.store.save('已取好的名字', '性格设定')
        self.store.assign('owner-a', source['id'])
        before = self.store.assignments()
        state = self.make_state()
        state.current_persona_id = source['id']
        state.accounts = [{'id': 'owner-a', 'name': source['name'], 'provider_id': '', 'status': '绑定失败'}]
        state.account_tree = Mock(exists=Mock(return_value=True))
        state.provider_account_id = Mock(return_value='')
        state.bind_wechat = Mock()
        state.add_account = Mock()
        app.App.bind_selected_companion(state)
        state.account_tree.selection_set.assert_called_once_with('owner-a')
        state.bind_wechat.assert_called_once()
        state.add_account.assert_not_called()
        self.assertEqual(self.store.assignments(), before)

    def test_fresh_install_does_not_invent_already_generated_companions(self):
        state = self.make_state()
        state.config_data = {}
        app.App.initialize_companions(state)
        self.assertEqual(self.store.personas(), [])


if __name__ == '__main__':
    unittest.main()
