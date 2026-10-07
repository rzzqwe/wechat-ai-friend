"""Companion selection and account ownership UI actions."""
from pathlib import Path
from datetime import datetime
import os
import subprocess
import sys
import threading
from tkinter import filedialog, messagebox
import wechat_ui as ui
from runtime_status import account_status


class CompanionControls:
    def recover_saved_bindings(self):
        api=self.services
        store=api.persona_store()
        mappings=api.load_account_bindings()
        assignments=store.assignments()
        accounts=[dict(account) for account in self.accounts]
        recovered=0
        for account in accounts:
            if account.get('status')!='绑定失败' or '正在启动微信网关' not in account.get('last_action',''):
                continue
            provider=mappings.get(account['id'])
            if provider and assignments.get(account['id'],{}).get('provider_id')==provider and api.provider_account_exists(provider):
                account.update(provider_id=provider,status='已绑定',last_action='扫码与专属人格已保存，请查看后台接收状态。')
                recovered+=1
        if recovered:
            api.save_accounts(accounts)
            self.accounts=accounts
            self.write_log(f'已恢复 {recovered} 条扫码成功但后台启动超时的绑定，无需重新扫码。')

    def recover_missing_accounts(self):
        api = self.services
        restored = api.persona_store().recover_account_records(self.accounts, api.load_account_bindings())
        count = len(restored) - len(self.accounts)
        if count:
            api.save_accounts(restored)
            self.accounts = restored
            self.write_log(f'已恢复 {count} 条缺失的账号记录，原陪伴归属和记忆保持不变。')
        return bool(count)

    def initialize_companions(self):
        api = self.services
        store = api.persona_store()
        try:
            self.recover_missing_accounts()
            CompanionControls.recover_saved_bindings(self)
        except (OSError, ValueError) as exc:
            self.write_log('恢复账号记录失败，原绑定仍保留；可选中陪伴后重试绑定：' + str(exc))
        # Old accounts each get their own copy, never a shared workspace.
        for account in self.accounts:
            if account['id'] in store.assignments():
                continue
            name = account['name'] + '的陪伴'
            item = store.save(name, api.prepared_companion_soul(name))
            store.assign(account['id'], item['id'], self.provider_account_id(account) or None)
        self.refresh_personas(self.config_data.get('persona_id'))
        self.new_persona()

    def refresh_personas(self, preferred=None):
        store = self.services.persona_store()
        self.persona_choices = store.personas()
        owners = {entry['persona_id']: local_id for local_id, entry in store.assignments().items()}
        accounts = {account['id']: account for account in self.accounts}
        self.persona_owners = owners
        chosen = preferred or self.current_persona_id
        for item in self.persona_tree.get_children():
            self.persona_tree.delete(item)
        for index, persona in enumerate(self.persona_choices):
            owner = owners.get(persona['id'])
            account = accounts.get(owner, {})
            user_name = account.get('name', owner or '未绑定')
            status = account_status(account, getattr(self, '_runtime_snapshot', None)).title if owner else '待扫码绑定'
            self.persona_tree.insert('', 'end', iid=persona['id'],
                                     values=(persona['name'], persona['id'][:6], user_name, status),
                                     tags=('alternate',) if index % 2 else ())
        if chosen not in {p['id'] for p in self.persona_choices}:
            chosen = self.persona_choices[0]['id'] if self.persona_choices else None
        if chosen:
            self.persona_tree.selection_set(chosen)
            self.persona_tree.see(chosen)
        else:
            self.current_persona_id = None
            self.soul_path = None
        self.select_persona()

    def select_persona(self, event=None):
        selected = self.persona_tree.selection()
        self.current_persona_id = selected[0] if selected else None
        self.soul_path = self.services.persona_store().profile_path(self.current_persona_id) if self.current_persona_id else None
        if hasattr(self, 'refresh_voice_selection'):
            self.refresh_voice_selection()
        ui.refresh_ui_state(self)

    def new_persona(self):
        self.selected_profile_path = None
        self.draft_source_persona_id = None
        self.draft_source_name = None
        self.name_var.set('')
        ui.refresh_ui_state(self)

    def clone_selected_companion(self):
        if self._ui_busy or not self.current_persona_id:
            return
        store = self.services.persona_store()
        try:
            source = store.get(self.current_persona_id)
            path = store.profile_path(source['id'])
            self.services.prepared_companion_soul(source['name'], path)
        except (OSError, ValueError) as exc:
            messagebox.showerror('无法复制陪伴', str(exc))
            return
        self.selected_profile_path = path
        self.draft_source_persona_id = source['id']
        self.draft_source_name = source['name']
        if not self.name_var.get().strip():
            self.name_var.set(source['name'])
        self._ui_canvas.yview_moveto(0)
        self.name_entry.focus_set()
        ui.refresh_ui_state(self)
        self.write_log('已以「' + source['name'] + '」为基础。点击生成陪伴后会得到独立副本，原用户的记忆和聊天不会复制。')

    def bind_selected_companion(self):
        if self._ui_busy or not self.current_persona_id:
            return
        owners = self.services.persona_store().assignments()
        owner = next((local_id for local_id, entry in owners.items() if entry['persona_id'] == self.current_persona_id), None)
        if owner:
            account = next((item for item in self.accounts if item['id'] == owner), None)
            if account is None:
                try:
                    self.recover_missing_accounts()
                except (OSError, ValueError) as exc:
                    messagebox.showerror('恢复绑定记录失败', '原绑定和记忆仍保留，未打开扫码窗口。请解决保存问题后重试：' + str(exc), parent=self)
                    return
                account = next((item for item in self.accounts if item['id'] == owner), None)
                if account is None:
                    messagebox.showerror('绑定记录已变化', '请刷新后重新选择陪伴。', parent=self)
                    return
                self.render_accounts()
            elif not self.account_tree.exists(owner):
                self.render_accounts()
            if self.account_tree.exists(owner):
                self.account_tree.selection_set(owner)
                self.account_tree.see(owner)
                self.account_tree.focus_set()
                self._ui_canvas.yview_moveto(1)
            if account and (not self.provider_account_id(account) or '失败' in account.get('status', '')):
                self.bind_wechat()
                return
            self.write_log('这个陪伴已有绑定用户。要绑定另一位用户，请先点击基于此新建，生成独立副本。')
            return
        self.add_account()

    def choose_persona_file(self):
        if self._ui_busy:
            return
        selected = filedialog.askopenfilename(
            parent=self, title='选择已整理的人格文件',
            initialdir=str(self.services.ROOT / 'profiles'),
            filetypes=[('人格文件', '*.md *.txt'), ('Markdown 文件', '*.md'), ('文本文件', '*.txt')],
        )
        if not selected:
            return
        path = Path(selected).resolve()
        try:
            self.services.prepared_companion_soul(self.name_var.get().strip() or '文件校验', path)
        except (OSError, ValueError) as exc:
            messagebox.showerror('无法使用人格文件', str(exc))
            return
        self.selected_profile_path = path
        self.draft_source_persona_id = None
        self.draft_source_name = None
        ui.refresh_ui_state(self)
        self.write_log('已选择人格文件：' + str(path))
        self.write_log('确认名称后点击生成陪伴，再为对应用户分配；原文件保持不变。')

    def delete_selected_companion(self):
        if self._ui_busy or not self.current_persona_id:
            return
        api = self.services
        store = api.persona_store()
        persona_id = self.current_persona_id
        try:
            persona = store.get(persona_id)
            owner = next((local_id for local_id, entry in store.assignments().items() if entry.get('persona_id') == persona_id), None)
            if owner:
                if self.account_tree.exists(owner):
                    self.account_tree.selection_set(owner)
                    self.account_tree.see(owner)
                    self._ui_canvas.yview_moveto(1)
                messagebox.showinfo('请先解除绑定', '「' + persona['name'] + '」仍绑定着用户。请先在微信账号列表中点击解除绑定，再删除陪伴。其他用户和陪伴不受影响。', parent=self)
                return
            source_path = store.profile_path(persona_id)
            if not messagebox.askyesno(
                '删除陪伴',
                '确定删除「' + persona['name'] + '」（' + persona_id[:6] + '）吗？' + chr(10) + chr(10) +
                '将从陪伴列表移除，并删除它在项目中的人格文件。' + chr(10) +
                '原始导入文件、其他陪伴及其副本、历史聊天记录都会保留。',
                parent=self, icon='warning', default='no',
            ):
                return
            result = store.delete(persona_id)
        except (OSError, ValueError) as exc:
            messagebox.showerror('删除失败', str(exc), parent=self)
            return
        if self.draft_source_persona_id == persona_id or self.selected_profile_path == source_path:
            self.new_persona()
        self.current_persona_id = None
        self.soul_path = None
        self.refresh_personas()
        try:
            config = api.load_app_config()
            if config.get('persona_id') == persona_id:
                config.pop('persona_id', None)
                api.save_app_config(config)
        except (OSError, ValueError) as exc:
            self.write_log('陪伴已删除，但最近选择的配置更新失败：' + str(exc))
        self.write_log('已删除陪伴「' + persona['name'] + '」。其他陪伴和原始人格文件保持不变。')
        if result.get('cleanup_pending'):
            self.write_log('人格已从列表删除，但文件清理未完成：' + result['cleanup_pending'])
            messagebox.showwarning('文件清理未完成', '陪伴已从列表移除，但部分副本文件正被占用。关闭占用程序后可清理：' + chr(10) + result['cleanup_pending'], parent=self)

    def account_persona_name(self, local_id):
        store = self.services.persona_store()
        entry = store.assignments().get(local_id)
        return store.get(entry['persona_id'])['name'] if entry else '待分配人格'

    def assign_selected_persona(self):
        account = self.selected_account()
        if not account:
            return
        if not self.current_persona_id or not self.soul_path:
            messagebox.showwarning('请先选择陪伴', '在上方选择或生成要绑定给这个用户的陪伴。')
            return
        try:
            entry = self.services.persona_store().assign(account['id'], self.current_persona_id, self.provider_account_id(account) or None)
            self.update_account(account['id'], last_action='已分配：' + self.services.persona_store().get(self.current_persona_id)['name'])
            self.write_log('已为这个账号分配独立人格、记忆和聊天目录。')
            if entry['provider_id']:
                threading.Thread(target=self._reload_gateway_worker, daemon=True).start()
        except (OSError, ValueError) as exc:
            messagebox.showerror('分配失败', str(exc))

    def _reload_gateway_worker(self):
        api = self.services
        try:
            result = subprocess.run(api.powershell_command(api.ROOT / 'scripts/重载微信网关.ps1'), cwd=str(api.ROOT), capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=90, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            if result.returncode:
                raise RuntimeError((result.stderr or result.stdout).strip())
            self.ui_log('独立人格路由已应用，网关已重载。')
        except Exception as exc:
            self.ui_log('设定已保存，但网关重载失败；请点击启动账号重试：' + str(exc))

    def add_account(self):
        api = self.services
        if self._ui_busy:
            return
        attempt = getattr(self, '_login_attempt', None)
        if attempt and attempt[1].poll() is None:
            messagebox.showinfo('正在绑定', '请先完成或关闭当前扫码窗口，再绑定下一个陪伴。', parent=self)
            return
        if not self.current_persona_id or not self.soul_path or not self.soul_path.exists():
            messagebox.showwarning('还差一步', '请先选择或生成这个用户的专属陪伴。')
            return
        if not all(variable.get().strip() for variable in (self.base_url_var, self.model_var, self.key_var)):
            messagebox.showwarning('请先自行配置文本模型', '请填写自己使用的模型服务地址、模型名称和 API Key，再绑定微信。')
            return
        if any(entry['persona_id'] == self.current_persona_id for entry in api.persona_store().assignments().values()):
            self.bind_selected_companion()
            return
        companion = api.persona_store().get(self.current_persona_id)
        display_name = companion['name']
        account_id = api.account_id_from_name(display_name, {item['id'] for item in self.accounts} | api.persona_store().used_local_ids())
        try:
            api.persona_store().assign(account_id, self.current_persona_id)
        except (OSError, ValueError) as exc:
            messagebox.showerror('无法分配陪伴', str(exc))
            return
        account = {'id': account_id, 'name': display_name, 'status': '等待扫码', 'last_action': '已分配专属陪伴', 'created_at': datetime.now().isoformat(timespec='seconds'), 'provider_id': ''}
        updated = self.accounts + [account]
        try:
            api.save_accounts(updated)
        except (OSError, ValueError) as exc:
            messagebox.showerror('保存账号记录失败', '未打开扫码窗口，陪伴归属已保留。请解决保存问题后再次点击绑定，程序会恢复原记录：' + str(exc), parent=self)
            return
        self.accounts = updated
        self.render_accounts()
        self.account_tree.selection_set(account_id)
        self.launch_login(account)

    def bind_wechat(self):
        if not self.account_tree.selection():
            self.add_account()
            return
        account = self.selected_account()
        if not account:
            return
        if not all(variable.get().strip() for variable in (self.base_url_var, self.model_var, self.key_var)):
            messagebox.showwarning('请先自行配置文本模型', '请填写自己使用的模型服务地址、模型名称和 API Key，再绑定微信。')
            return
        if account['id'] not in self.services.persona_store().assignments():
            messagebox.showwarning('还差一步', '先选一个已生成的陪伴并点击分配选中的陪伴。')
            return
        self.launch_login(account)

    def generate(self):
        if self._ui_busy:
            return
        name = ' '.join(self.name_var.get().split())
        if not name:
            messagebox.showwarning('还差一步', '请填写陪伴名称。')
            return
        if not self.selected_profile_path:
            messagebox.showwarning('还差一步', '请选择人格文件，或在已生成的陪伴中点击基于此新建。')
            return
        args = (name, self.key_var.get().strip(), self.base_url_var.get().strip(), self.model_var.get().strip(), bool(self.remember_key_var.get()), None, self.selected_profile_path)
        self._ui_busy = True
        self._busy(True)
        threading.Thread(target=self._generate_worker, args=args, daemon=True).start()

    def _generate_worker(self, name, api_key, base_url, model, remember_key, persona_id=None, source_path=None):
        api = self.services
        try:
            store = api.persona_store()
            if not source_path:
                raise ValueError('请选择人格文件，或使用基于此新建。')
            soul = api.prepared_companion_soul(name, source_path)
            item = store.save(name, soul)
            api.save_app_config({'name': name, 'base_url': base_url, 'model': model, 'persona_id': item['id']})
            if remember_key and api_key:
                api.API_KEY_FILE.write_bytes(api._protect_secret(api_key.encode('utf-8')))
            elif not remember_key and api.API_KEY_FILE.exists():
                api.API_KEY_FILE.unlink()
            self.after(0, lambda: self._finish_generation(item))
            self.ui_log('陪伴「' + name + '」已保存为独立人格。')
            self.ui_log('已添加到已生成的陪伴。选中它并点击绑定用户 / 扫码，为新用户生成二维码。')
        except Exception as exc:
            self.ui_log('生成失败：' + str(exc))
            self.after(0, lambda error=str(exc): messagebox.showerror('生成失败', error))
        finally:
            self._busy(False)

    def _finish_generation(self, item):
        self.current_persona_id = item['id']
        self.soul_path = self.services.persona_store().profile_path(item['id'])
        self.new_persona()
        self.refresh_personas(item['id'])
        self.render_accounts()
