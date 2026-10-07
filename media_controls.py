"""Workbench dialog for private sticker management."""
from pathlib import Path
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk
from companion_media import MediaStore
import display_support as display
import wechat_ui as ui


class MediaControls:
    def open_media_settings(self):
        if self._ui_busy or not self.current_persona_id:
            return
        existing = getattr(self, '_media_window', None)
        if existing and existing.winfo_exists():
            existing.lift()
            return
        store = self.services.persona_store()
        persona_id = self.current_persona_id
        library = MediaStore(store)
        try:
            saved = library.load(persona_id)
            name = store.get(persona_id)['name']
        except (OSError, ValueError) as exc:
            messagebox.showerror('无法读取表情包设置', str(exc), parent=self)
            return
        window = self._media_window = tk.Toplevel(self)
        window.title('表情包 · ' + name)
        window.configure(bg=ui.BG)
        display.size_window(window, 760, 520, 640, 460)
        frame = tk.Frame(window, bg=ui.CARD, padx=22, pady=18)
        frame.pack(fill='both', expand=True, padx=16, pady=16)
        ui._label(frame, name + '的表情包', 16, bold=True).pack(anchor='w')
        ui._label(frame, '每个陪伴独立保存表情素材', 9, ui.MUTED).pack(anchor='w', pady=(3, 12))
        enabled = tk.BooleanVar(value=saved['stickers_enabled'])
        check = ttk.Checkbutton(frame, text='允许陪伴按聊天内容选用表情包', variable=enabled, style='Card.TCheckbutton')
        check.pack(anchor='w', pady=(4, 8))
        ui._label(frame, '支持 PNG / JPG / GIF / WebP；名称写“开心”“晚安”等，便于陪伴选用。',
                  9, ui.MUTED, wraplength=640).pack(anchor='w')
        list_frame = tk.Frame(frame, bg=ui.CARD)
        list_frame.pack(fill='both', expand=True, pady=(8, 6))
        tree = ttk.Treeview(list_frame, columns=('label', 'format'), show='headings', selectmode='browse', height=7)
        tree.heading('label', text='表情含义 / 适用场景')
        tree.heading('format', text='格式')
        tree.column('label', width=500, minwidth=250)
        tree.column('format', width=80, minwidth=60)
        tree.pack(side='left', fill='both', expand=True)
        scrollbar = ttk.Scrollbar(list_frame, orient='vertical', command=tree.yview)
        scrollbar.pack(side='right', fill='y')
        tree.configure(yscrollcommand=scrollbar.set)
        actions = tk.Frame(frame, bg=ui.CARD)
        actions.pack(fill='x')
        buttons = []
        status = ui._label(frame, '开关及素材变更即时保存；从下一轮聊天生效，无需重启。', 9, ui.MUTED, wraplength=640)
        status.pack(anchor='w', pady=(12, 0))
        bottom = tk.Frame(frame, bg=ui.CARD)
        bottom.pack(fill='x', pady=(12, 0))
        apply = ttk.Button(bottom, text='重新应用设置', style='Primary.TButton')
        apply.pack(side='right')
        busy = [False]

        def refresh():
            items = library.load(persona_id)['stickers']
            selected = tree.selection()
            tree.delete(*tree.get_children())
            for item in items:
                tree.insert('', 'end', iid=item['id'], values=(item['label'], Path(item['file']).suffix.upper()[1:]))
            if selected and tree.exists(selected[0]):
                tree.selection_set(selected[0])

        def perform(action):
            def worker():
                action()
                store.sync_workspace_settings(persona_id)
            run_worker(worker, '正在保存并即时更新…', lambda _: status.configure(
                text='已保存并即时更新，下一轮聊天生效。', fg=ui.GREEN))

        def selected():
            keys = tree.selection()
            return next((item for item in library.load(persona_id)['stickers'] if keys and item['id'] == keys[0]), None)

        def import_images():
            paths = filedialog.askopenfilenames(parent=window, title='导入表情包',
                filetypes=[('表情图片', '*.png *.jpg *.jpeg *.gif *.webp')])
            if paths:
                perform(lambda: library.import_stickers(persona_id, list(map(Path, paths))))

        def rename():
            item = selected()
            if item:
                label = simpledialog.askstring('表情含义', '填写情绪或适用场景：', initialvalue=item['label'], parent=window)
                if label and label.strip():
                    perform(lambda: library.rename_sticker(persona_id, item['id'], label))

        def remove():
            item = selected()
            if item:
                perform(lambda: library.remove_sticker(persona_id, item['id']))

        def view():
            item = selected()
            if not item:
                return
            from PIL import Image, ImageTk
            with Image.open(library.directory(persona_id) / 'stickers' / item['file']) as picture:
                picture.seek(0)
                picture = picture.convert('RGBA')
                picture.thumbnail((480, 400))
                photo = ImageTk.PhotoImage(picture, master=window)
            popup = tk.Toplevel(window)
            popup.title(item['label'])
            label = tk.Label(popup, image=photo, bg='white', padx=12, pady=12)
            label.image = photo
            label.pack()

        def preview_image():
            try:
                view()
            except (OSError, ValueError) as exc:
                messagebox.showerror('无法预览表情', str(exc), parent=window)

        for title, action in [('导入表情包', import_images), ('修改含义', rename), ('预览', preview_image), ('移除', remove)]:
            button = ttk.Button(actions, text=title, command=action)
            button.pack(side='left', padx=(0, 7))
            buttons.append(button)

        def run_worker(action, message, finished):
            if busy[0]:
                return
            busy[0] = True
            self._ui_busy_message = message
            self._busy(True)
            for button in [*buttons, apply, check]:
                button.configure(state='disabled')
            status.configure(text=message, fg=ui.GREEN)

            def done(error, result):
                busy[0] = False
                self._ui_busy_message = None
                self._busy(False)
                if not window.winfo_exists():
                    return
                for button in [*buttons, apply, check]:
                    button.configure(state='normal')
                refresh()
                if error:
                    status.configure(text=str(error), fg=ui.RED)
                    messagebox.showerror('操作失败', str(error), parent=window)
                else:
                    finished(result)

            def worker():
                try:
                    result = action()
                    self.after(0, lambda: done(None, result))
                except Exception as exc:
                    self.after(0, lambda error=exc: done(error, None))
            threading.Thread(target=worker, daemon=True).start()

        def save():
            stickers = enabled.get()
            def worker():
                library.save_options(persona_id, stickers)
                store.sync_workspace_settings(persona_id)
                return any(item['persona_id'] == persona_id and item.get('provider_id')
                           for item in store.assignments().values())
            def finish(assigned):
                message = '已即时更新，下一轮聊天生效。' if assigned else '已保存，完成微信绑定后自动应用。'
                status.configure(text=message, fg=ui.GREEN)
                self.write_log(name + '的表情包：' + message)
            run_worker(worker, '正在保存并更新陪伴…', finish)

        apply.configure(command=save)
        check.configure(command=save)
        window.protocol('WM_DELETE_WINDOW', lambda: None if busy[0] else window.destroy())
        display.scale_widgets(window)
        refresh()

