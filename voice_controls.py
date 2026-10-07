"""Voice choices on the existing companion workbench."""
from pathlib import Path
import queue
import subprocess
import threading
import uuid
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from companion_speech import VoiceStore, ensure_runtime_config
import fish_audio_client as fish
import wechat_ui as ui
import display_support as display

MODE_NAMES = {'text': '文字', 'smart': '文字和语音'}
PREVIEW_TEXT = '你好，这是我的声音。'


class VoiceControls:
    def initialize_voice_controls(self):
        self._voice_apply_busy = False
        self._voice_runtime_reload_pending = False
        self._voice_results = queue.SimpleQueue()
        self._voice_preview_results = queue.SimpleQueue()
        self._voice_preview_busy = False
        self._voice_timer = self.after(100, self._poll_voice_results)

    def initialize_voice_runtime(self):
        if self._ui_busy or self._voice_apply_busy:
            return
        store = self.services.persona_store()
        if not any(entry.get('provider_id') for entry in store.assignments().values()):
            return
        self._voice_apply_busy = True
        self._ui_busy = True
        self._ui_busy_message = '正在检查语音运行组件…'
        self._busy(True)

        def worker():
            try:
                changed = ensure_runtime_config(store)
                self._voice_runtime_reload_pending |= changed
                if self._voice_runtime_reload_pending:
                    self._reload_voice_runtime()
                    self._voice_runtime_reload_pending = False
                self._voice_results.put((None, '语音运行组件已自动配置。' if changed else None))
            except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
                self._voice_results.put((str(error), None))
        threading.Thread(target=worker, daemon=True, name='companion-voice-initialize').start()

    def _reload_voice_runtime(self):
        api = self.services
        result = subprocess.run(api.powershell_command(api.ROOT / 'scripts/重载微信网关.ps1'),
            cwd=str(api.ROOT), env=api.project_runtime_environment(api.ROOT),
            capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=90,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        if result.returncode:
            raise RuntimeError('语音组件已保存，但网关重载失败，请点击启动账号重试。')

    def refresh_voice_selection(self):
        if not hasattr(self, '_ui_voice_mode'):
            return
        try:
            self._voice_choices = {}
            if not self.current_persona_id:
                self._ui_voice_choice.configure(values=[])
                self._ui_voice_mode_var.set('文字')
                self._ui_voice_choice_var.set('未配置')
                return
            voices = VoiceStore(self.services.persona_store())
            saved = voices.load(self.current_persona_id)
            service = voices.load_service(self.current_persona_id)
            name = service['name'] if service else '未配置'
            if service:
                self._voice_choices[name] = 'custom-fish'
            self._ui_voice_choice.configure(values=list(self._voice_choices))
            self._ui_voice_mode_var.set(MODE_NAMES[saved['mode']])
            self._ui_voice_choice_var.set(name)
        except (ValueError, OSError):
            self._ui_voice_status.configure(text='语音设置读取失败，请检查语音配置。', fg=ui.RED)

    def save_voice_selection(self, event=None):
        if self._ui_busy or self._voice_apply_busy or not self.current_persona_id:
            return
        persona_id = self.current_persona_id
        mode = next((key for key, name in MODE_NAMES.items() if name == self._ui_voice_mode_var.get()), 'text')
        voice_id = 'custom-fish'
        store = self.services.persona_store()
        try:
            configured = store and VoiceStore(store).load_service(persona_id) is not None and bool(fish.load_key())
        except (OSError, ValueError):
            configured = False
        if mode == 'smart' and not configured:
            self.refresh_voice_selection()
            messagebox.showinfo('请先自行配置语音',
                '请点击“语音配置”，填写自己的 Fish Audio Key 和音色 ID。\n配置完成后再开启文字和语音模式，无需安装本地语音程序。', parent=self)
            return
        name = store.get(persona_id)['name']
        self._ui_busy_message = '正在保存当前陪伴的语音设置…'
        self._voice_apply_busy = True
        self._busy(True)

        def worker():
            try:
                changed = ensure_runtime_config(store) if mode == 'smart' else False
                self._voice_runtime_reload_pending |= changed
                VoiceStore(store).save(persona_id, mode, voice_id)
                store.sync_workspace_settings(persona_id)
                if self._voice_runtime_reload_pending:
                    self._reload_voice_runtime()
                    self._voice_runtime_reload_pending = False
                self._voice_results.put((None, name + '：' + MODE_NAMES[mode] + '，下一轮聊天生效。'))
            except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
                self._voice_results.put((str(error), None))
        threading.Thread(target=worker, daemon=True, name='companion-voice-settings').start()

    def _poll_voice_results(self):
        while not self._voice_results.empty():
            error, message = self._voice_results.get_nowait()
            self._voice_apply_busy = False
            self._ui_busy_message = None
            self._busy(False)
            self.refresh_voice_selection()
            message = error or message
            if message:
                self._ui_voice_status.configure(text=message, fg=ui.RED if error else ui.GREEN)
                self.write_log(message)
        while not self._voice_preview_results.empty():
            error, preview = self._voice_preview_results.get_nowait()
            self._voice_preview_busy = False
            self._ui_busy_message = None
            self._busy(False)
            if error:
                self._ui_voice_status.configure(text=error, fg=ui.RED)
                self.write_log(error)
            else:
                self._show_voice_preview(preview)
        self._voice_timer = self.after(100, self._poll_voice_results)

    def preview_selected_voice(self):
        if self._ui_busy or self._voice_preview_busy or self._voice_apply_busy:
            return
        store = self.services.persona_store()
        try:
            service = VoiceStore(store).load_service(self.current_persona_id)
            key = fish.load_key()
            if service is None or not key:
                raise ValueError('请先在“语音配置”填写 Key 和音色 ID，再试听。')
        except (OSError, ValueError) as error:
            messagebox.showinfo('请先配置语音', str(error), parent=self)
            return
        self._voice_preview_busy = True
        self._ui_busy = True
        self._ui_busy_message = '正在用配置的音色生成试听音频…'
        self._ui_voice_status.configure(text=self._ui_busy_message, fg=ui.MUTED)
        self._busy(True)
        results = self._voice_preview_results

        def worker():
            output = None
            succeeded = False
            try:
                directory = store.root / 'data/voice-previews'
                directory.mkdir(parents=True, exist_ok=True)
                if directory.resolve() != directory:
                    raise ValueError('试听目录是链接，无法保存。')
                output = directory / (uuid.uuid4().hex + '.wav')
                result = fish.generate_sample(key, PREVIEW_TEXT, output, voice_config=service)
                results.put((None, {'path': str(output), 'audio_seconds': result['audio_seconds'],
                                    'text': PREVIEW_TEXT, 'voice_name': service['name']}))
                succeeded = True
            except fish.PreviewError as error:
                results.put((str(error), None))
            except Exception:
                results.put(('试听音频生成失败，请检查网络和语音配置。', None))
            finally:
                # Failed requests do not leave a misleading playable preview.
                if output is not None and not succeeded:
                    output.unlink(missing_ok=True)
                    output.with_suffix('.wav.tmp').unlink(missing_ok=True)
        threading.Thread(target=worker, daemon=True, name='configured-voice-preview').start()

    def _show_voice_preview(self, preview):
        import winsound
        existing = getattr(self, '_voice_preview_window', None)
        if existing and existing.winfo_exists():
            existing.destroy()
        window = self._voice_preview_window = tk.Toplevel(self)
        window.title('配置音色试听')
        display.size_window(window, 540, 230, 480, 210)
        frame = ttk.Frame(window, padding=20)
        frame.pack(fill='both', expand=True)
        ttk.Label(frame, text=preview['voice_name'] + ' · ' + str(preview['audio_seconds']) + ' 秒').pack(anchor='w')
        ttk.Label(frame, text=preview['text']).pack(anchor='w', pady=14)
        status = ttk.Label(frame, text='音频已生成，可以播放或再次播放。')
        status.pack(anchor='w')
        buttons = ttk.Frame(frame)
        buttons.pack(anchor='e', pady=15)
        def play():
            try:
                winsound.PlaySound(preview['path'], winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
                status.configure(text='正在播放…')
            except (OSError, RuntimeError):
                status.configure(text='音频已生成，但本机播放失败。')
        def stop():
            winsound.PlaySound(None, 0)
            status.configure(text='已停止，可以再次播放。')
        ttk.Button(buttons, text='播放', command=play).pack(side='left', padx=8)
        ttk.Button(buttons, text='停止', command=stop).pack(side='left')
        def close():
            winsound.PlaySound(None, 0)
            window.destroy()
        window.protocol('WM_DELETE_WINDOW', close)
        display.scale_widgets(window)
        self._ui_voice_status.configure(text='配置音色的试听音频已生成：' + str(preview['audio_seconds']) + ' 秒。', fg=ui.GREEN)
        play()

    def open_voice_key_settings(self):
        """Configure the same Fish Audio API form used by the local project."""
        if self._ui_busy:
            return
        existing = getattr(self, '_voice_config_window', None)
        if existing and existing.winfo_exists():
            existing.lift()
            return
        window = self._voice_config_window = tk.Toplevel(self)
        persona_id = self.current_persona_id
        store = self.services.persona_store()
        voices = VoiceStore(store)
        try:
            service = voices.load_service(persona_id) or {}
        except (OSError, ValueError):
            service = {}
        window.title('语音配置：Key 与音色 ID（可直接填写）')
        display.size_window(window, 650, 295, 560, 270)
        frame = ttk.Frame(window, padding=20)
        frame.pack(fill='both', expand=True)
        current_name = store.get(persona_id)['name'] if persona_id else '默认配置，之后新建的陪伴会带入'
        ttk.Label(frame, text='当前陪伴：' + current_name).pack(anchor='w')
        ttk.Label(frame, text='Key 在本机加密保存并共用；音色 ID 按陪伴独立保存。').pack(anchor='w', pady=(4, 10))
        key = tk.StringVar()
        reference = tk.StringVar(value=service.get('reference_id', ''))
        for label, variable, secret in [('Fish Audio API Key（已保存时留空保留）', key, True),
                ('音色 ID（32 位）', reference, False)]:
            ttk.Label(frame, text=label).pack(anchor='w')
            ttk.Entry(frame, textvariable=variable, show='•' if secret else '',
                      state='normal').pack(fill='x', pady=(3, 8))
        ttk.Label(frame, text='语音模型：s2.1-pro-free。失败保留文字，不自动切换模型。').pack(anchor='w', pady=8)

        def save():
            try:
                value = {'provider': 'fish-audio', 'reference_id': reference.get().strip().lower(),
                         'name': '音色 ' + reference.get().strip().lower()[:6]}
                voices.validate_service(value)
                new_key = key.get().strip()
                if not new_key and not fish.load_key():
                    raise ValueError('请填写自己的 Fish Audio API Key。')
                if new_key and any(character.isspace() for character in new_key):
                    raise ValueError('请填写完整的 Fish Audio API Key。')
                if new_key:
                    fish.save_key(new_key)
                voices.save_service(persona_id, value['reference_id'])
                if persona_id:
                    store.sync_workspace_settings(persona_id)
            except (OSError, ValueError) as error:
                messagebox.showerror('语音配置未保存', str(error), parent=window)
                return
            key.set('')
            window.destroy()
            self.refresh_voice_selection()
            message = 'Fish Audio 配置已保存，现在可以开启文字和语音模式。' if persona_id else 'Key 和默认音色已保存，新建陪伴会自动带入。'
            self._ui_voice_status.configure(text=message, fg=ui.GREEN)
            self.initialize_voice_runtime()
        ttk.Button(frame, text='保存配置', command=save).pack(anchor='e')
        display.scale_widgets(window)

