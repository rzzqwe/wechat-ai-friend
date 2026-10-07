from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock, patch

from account_personas import PersonaStore, read_json, write_json
from companion_controls import CompanionControls
import test_companion_flow as flow


class CompanionDeletionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.store = PersonaStore(self.folder / 'project', self.folder / 'state')
        self.original_file = self.folder / 'input.md'
        self.original_file.write_text('导入的原始人格', encoding='utf-8')
        self.first = self.store.save('陪伴甲', '相同人格设定')
        self.second = self.store.save('陪伴乙', '相同人格设定')
        self.first_path = self.store.profile_path(self.first['id'])
        self.second_path = self.store.profile_path(self.second['id'])
        self.app_config = self.folder / 'config.json'
        write_json(self.app_config, {'persona_id': self.first['id'], 'model': 'saved-model'})

    def state(self):
        services = SimpleNamespace(
            persona_store=lambda: self.store,
            load_account_bindings=lambda: {},
            load_app_config=lambda: read_json(self.app_config, {}),
            save_app_config=lambda value: write_json(self.app_config, value),
        )
        state = SimpleNamespace(
            services=services, _ui_busy=False, current_persona_id=self.first['id'],
            soul_path=self.first_path, selected_profile_path=self.original_file,
            draft_source_persona_id=None, draft_source_name=None,
            accounts=[], config_data={}, persona_tree=flow.MemoryTree(), persona_choices=[],
            account_tree=Mock(exists=Mock(return_value=True)), _ui_canvas=Mock(),
            name_var=Mock(), write_log=Mock(),
        )
        state.new_persona = lambda: CompanionControls.new_persona(state)
        state.recover_missing_accounts = lambda: CompanionControls.recover_missing_accounts(state)
        state.select_persona = lambda: CompanionControls.select_persona(state)
        state.refresh_personas = lambda preferred=None: CompanionControls.refresh_personas(state, preferred)
        state.refresh_personas(self.first['id'])
        return state

    def test_delete_removes_only_selected_generated_folder_and_catalog_entry(self):
        source = self.original_file.read_bytes()
        copied = self.second_path.read_bytes()
        result = self.store.delete(self.first['id'])
        self.assertEqual(result['id'], self.first['id'])
        self.assertNotIn('cleanup_pending', result)
        self.assertFalse(self.first_path.parent.exists())
        self.assertEqual(self.store.personas(), [self.second])
        self.assertEqual(self.original_file.read_bytes(), source)
        self.assertEqual(self.second_path.read_bytes(), copied)
        self.assertEqual(list(self.second_path.parent.parent.glob('.deleting-*')), [])

    def test_bound_companion_cannot_be_deleted_or_modify_other_routes(self):
        self.store.assign('user-a', self.first['id'], 'wechat-a')
        self.store.assign('user-b', self.second['id'], 'wechat-b')
        before = [path.read_bytes() for path in (self.store.catalog_path, self.store.assignments_path, self.store.config, self.first_path, self.second_path)]
        with self.assertRaisesRegex(ValueError, '解除微信绑定'):
            self.store.delete(self.first['id'])
        self.assertEqual(before, [path.read_bytes() for path in (self.store.catalog_path, self.store.assignments_path, self.store.config, self.first_path, self.second_path)])

    def test_delete_after_detach_preserves_previous_memory_and_history(self):
        entry = self.store.assign('user-a', self.first['id'], 'wechat-a')
        memory = self.store.workspace('user-a', self.first['id']) / 'MEMORY.md'
        memory.write_text('历史记忆', encoding='utf-8')
        transcript = self.store.state / 'agents' / entry['agent_id'] / 'sessions' / 'previous.jsonl'
        transcript.parent.mkdir()
        transcript.write_text('历史聊天', encoding='utf-8')
        self.store.detach('user-a')
        config = self.store.config.read_bytes()
        self.store.delete(self.first['id'])
        self.assertEqual(memory.read_text(encoding='utf-8'), '历史记忆')
        self.assertEqual(transcript.read_text(encoding='utf-8'), '历史聊天')
        self.assertEqual(self.store.config.read_bytes(), config)

    def test_catalog_failure_restores_original_persona_folder(self):
        before = self.store.catalog_path.read_bytes()
        with patch('account_personas.write_json', side_effect=OSError('无法保存目录')):
            with self.assertRaisesRegex(OSError, '无法保存目录'):
                self.store.delete(self.first['id'])
        self.assertEqual(self.store.catalog_path.read_bytes(), before)
        self.assertEqual(self.first_path.read_text(encoding='utf-8'), '相同人格设定')
        self.assertEqual(list(self.first_path.parent.parent.glob('.deleting-*')), [])

    def test_busy_file_failure_does_not_remove_catalog_entry(self):
        before = self.store.catalog_path.read_bytes()
        with patch('account_personas.os.replace', side_effect=PermissionError('文件被占用')):
            with self.assertRaises(PermissionError):
                self.store.delete(self.first['id'])
        self.assertEqual(self.store.catalog_path.read_bytes(), before)
        self.assertTrue(self.first_path.exists())

    def test_cleanup_failure_is_reported_with_remaining_directory(self):
        with patch('account_personas.shutil.rmtree', side_effect=PermissionError('占用')):
            result = self.store.delete(self.first['id'])
        self.assertEqual(self.store.personas(), [self.second])
        self.assertTrue(Path(result['cleanup_pending']).is_dir())
        self.assertTrue(Path(result['cleanup_pending']).is_relative_to(self.store.root))
        self.assertEqual(result['cleanup_error'], '占用')
        self.assertTrue(self.second_path.exists())

    def test_missing_profile_can_still_be_removed_from_catalog(self):
        self.first_path.unlink()
        self.first_path.parent.rmdir()
        self.store.delete(self.first['id'])
        self.assertEqual(self.store.personas(), [self.second])

    def test_unknown_or_path_like_identifier_never_removes_files(self):
        before = self.store.catalog_path.read_bytes()
        for identity in ('../input.md', '0' * 32):
            with self.subTest(identity=identity), self.assertRaises(ValueError):
                self.store.delete(identity)
        self.assertEqual(self.store.catalog_path.read_bytes(), before)
        self.assertTrue(self.first_path.exists())
        self.assertTrue(self.original_file.exists())

    def test_cancel_keeps_saved_persona_and_draft(self):
        state = self.state()
        with patch('companion_controls.messagebox.askyesno', return_value=False):
            CompanionControls.delete_selected_companion(state)
        self.assertEqual(state.current_persona_id, self.first['id'])
        self.assertEqual(state.selected_profile_path, self.original_file)
        self.assertEqual(len(self.store.personas()), 2)
        self.assertTrue(self.first_path.exists())

    def test_confirm_refreshes_list_and_preserves_unrelated_draft_and_model_config(self):
        state = self.state()
        with patch('companion_controls.messagebox.askyesno', return_value=True):
            CompanionControls.delete_selected_companion(state)
        self.assertEqual(set(state.persona_tree.rows), {self.second['id']})
        self.assertEqual(state.current_persona_id, self.second['id'])
        self.assertEqual(state.selected_profile_path, self.original_file)
        self.assertEqual(read_json(self.app_config, {}), {'model': 'saved-model'})

    def test_draft_based_on_deleted_companion_is_cleared(self):
        state = self.state()
        state.draft_source_persona_id = self.first['id']
        state.draft_source_name = self.first['name']
        state.selected_profile_path = self.first_path
        with patch('companion_controls.messagebox.askyesno', return_value=True):
            CompanionControls.delete_selected_companion(state)
        self.assertIsNone(state.selected_profile_path)
        self.assertIsNone(state.draft_source_persona_id)
        state.name_var.set.assert_called_with('')

    def test_bound_companion_guides_to_unbind_without_confirmation_or_mutation(self):
        self.store.assign('user-a', self.first['id'])
        state = self.state()
        with patch('companion_controls.messagebox.askyesno') as confirm, patch('companion_controls.messagebox.showinfo') as info:
            CompanionControls.delete_selected_companion(state)
        confirm.assert_not_called()
        info.assert_called_once()
        state.account_tree.selection_set.assert_called_once_with('user-a')
        self.assertTrue(self.first_path.exists())
        self.assertEqual(len(self.store.personas()), 2)

    def test_delete_last_companion_remains_empty_after_initializing_again(self):
        self.store.delete(self.second['id'])
        state = self.state()
        with patch('companion_controls.messagebox.askyesno', return_value=True):
            CompanionControls.delete_selected_companion(state)
        self.assertEqual(state.persona_tree.rows, {})
        self.assertIsNone(state.current_persona_id)
        self.assertIsNone(state.soul_path)
        CompanionControls.initialize_companions(state)
        self.assertEqual(self.store.personas(), [])

    def test_delete_failure_keeps_selected_persona_and_reports_error(self):
        state = self.state()
        with patch('companion_controls.messagebox.askyesno', return_value=True), patch.object(self.store, 'delete', side_effect=OSError('被占用')), patch('companion_controls.messagebox.showerror') as error:
            CompanionControls.delete_selected_companion(state)
        error.assert_called_once()
        self.assertEqual(state.current_persona_id, self.first['id'])
        self.assertTrue(self.first_path.exists())

    def test_busy_or_empty_selection_cannot_delete(self):
        state = self.state()
        with patch('companion_controls.messagebox.askyesno') as confirm:
            state._ui_busy = True
            CompanionControls.delete_selected_companion(state)
            state._ui_busy = False
            state.current_persona_id = None
            CompanionControls.delete_selected_companion(state)
        confirm.assert_not_called()
        self.assertEqual(len(self.store.personas()), 2)


if __name__ == '__main__':
    unittest.main()
