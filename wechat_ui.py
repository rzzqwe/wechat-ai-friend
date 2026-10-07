"""Native presentation layer for the WeChat AI desktop workbench."""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from datetime import datetime
import display_support as _display
from ui_scrolling import SmoothScroll
from runtime_status import overview

BG = '#F2F5F3'
CARD = '#FFFFFF'
INK = '#213C32'
MUTED = '#5D7166'
LINE = '#E1E9E4'
GREEN = '#167653'
PALE = '#EAF4EE'
RED = '#B34949'
FONT = 'Microsoft YaHei UI'


def _label(parent, text='', size=10, color=INK, bold=False, **kwargs):
    return tk.Label(parent, text=text, font=(FONT, size, 'bold' if bold else 'normal'),
                    fg=color, bg=kwargs.pop('bg', CARD), anchor=kwargs.pop('anchor', 'w'), **kwargs)


def _card(parent):
    return tk.Frame(parent, bg=CARD, highlightbackground=LINE, highlightthickness=1, bd=0)


def _section(parent, number, title, subtitle):
    top = tk.Frame(parent, bg=CARD)
    top.pack(fill='x', padx=20, pady=(12, 8))
    _label(top, number, 10, GREEN, True, bg=PALE, padx=8, pady=4).pack(side='left', padx=(0, 10))
    words = tk.Frame(top, bg=CARD)
    words.pack(side='left')
    _label(words, title, 12, bold=True).pack(anchor='w')
    _label(words, subtitle, 9, MUTED).pack(anchor='w', pady=(2, 0))


def _styles(app):
    app.configure(bg=BG)
    app.option_add('*Font', (FONT, 10))
    style = ttk.Style(app)
    style.theme_use('clam')
    style.configure('.', font=(FONT, 10), background=BG, foreground=INK)
    style.configure('TButton', background=CARD, foreground=INK, bordercolor=LINE,
                    lightcolor=LINE, darkcolor=LINE, borderwidth=1, padding=(12, 7),
                    focusthickness=1, focuscolor=GREEN)
    style.map('TButton', background=[('disabled', '#F4F6F4'), ('pressed', '#DFEAE2'), ('active', '#F0F6F2')],
              foreground=[('disabled', '#A7B2AB')], bordercolor=[('focus', GREEN)])
    style.configure('Primary.TButton', background=GREEN, foreground='white',
                    bordercolor=GREEN, lightcolor=GREEN, darkcolor=GREEN, font=(FONT, 10, 'bold'))
    style.map('Primary.TButton', background=[('disabled', '#A9C4B6'), ('pressed', '#10593E'), ('active', '#1C8962')],
              foreground=[('disabled', '#F5FAF7'), ('!disabled', 'white')],
              bordercolor=[('disabled', '#A9C4B6'), ('!disabled', GREEN)])
    style.configure('Soft.TButton', background=PALE, foreground=GREEN, bordercolor='#D7E7DC')
    style.map('Soft.TButton', background=[('disabled', '#F4F6F4'), ('active', '#DCEDE2')],
              foreground=[('disabled', '#A7B2AB'), ('!disabled', GREEN)])
    style.configure('Danger.TButton', foreground=RED, background=CARD)
    style.map('Danger.TButton', background=[('active', '#FFF0EE')], foreground=[('disabled', '#B8ACAC'), ('!disabled', RED)])
    style.configure('TEntry', padding=(9, 7), fieldbackground='#FAFCFA', foreground=INK,
                    bordercolor=LINE, lightcolor=LINE, darkcolor=LINE, insertcolor=GREEN)
    style.map('TEntry', bordercolor=[('focus', GREEN)], lightcolor=[('focus', GREEN)],
              darkcolor=[('focus', GREEN)], fieldbackground=[('disabled', '#F2F5F3')])
    style.configure('Card.TCheckbutton', background=CARD, foreground=MUTED, padding=0, font=(FONT, 9))
    style.map('Card.TCheckbutton', background=[('active', CARD)])
    style.configure('Treeview', background=CARD, fieldbackground=CARD, foreground=INK,
                    rowheight=29, borderwidth=0, font=(FONT, 9))
    style.map('Treeview', background=[('selected', '#DCEEE3')], foreground=[('selected', '#145D40')])
    style.configure('Treeview.Heading', background='#F4F7F5', foreground=MUTED, relief='flat',
                    padding=(9, 8), font=(FONT, 9, 'bold'))
    style.map('Treeview.Heading', background=[('active', '#EDF2EE')])
    style.configure('Vertical.TScrollbar', background='#CFDBD3', troughcolor=BG,
                    bordercolor=BG, arrowcolor=MUTED, arrowsize=12, gripcount=0)
    style.configure('Work.Horizontal.TProgressbar', background=GREEN, troughcolor=LINE,
                    bordercolor=LINE, lightcolor=GREEN, darkcolor=GREEN, thickness=3)
    app._ui_style = style
    _display.scale_styles(app)


def build_ui(app):
    _styles(app)
    icon = tk.PhotoImage(master=app, width=32, height=32)
    for y in range(32):
        for x in range(32):
            if (x - 15.5) ** 2 + (y - 15.5) ** 2 < 245:
                color = GREEN
                if ((x - 16) / 10) ** 2 + ((y - 14) / 7) ** 2 < 1 or (8 <= x <= 13 and 17 <= y <= 25 and x + y <= 34):
                    color = 'white'
                if (x - 12) ** 2 + (y - 14) ** 2 <= 2 or (x - 20) ** 2 + (y - 14) ** 2 <= 2:
                    color = GREEN
                icon.put(color, (x, y))
    app.iconphoto(True, icon)
    app._ui_icon = icon
    app._ui_busy = False
    app._ui_mutable = []
    app.name_var = tk.StringVar(value='')
    app.base_url_var = tk.StringVar()
    app.model_var = tk.StringVar()
    app.key_var = tk.StringVar()

    header = tk.Frame(app, bg=BG)
    header.pack(fill='x', padx=26, pady=(18, 12))
    logo = tk.Canvas(header, width=48, height=48, bg=BG, highlightthickness=0)
    logo.pack(side='left', padx=(0, 13))
    logo.create_oval(2, 2, 46, 46, fill=GREEN, outline=GREEN)
    logo.create_oval(11, 13, 37, 32, fill='white', outline='white')
    logo.create_polygon(15, 27, 13, 36, 23, 30, fill='white', outline='white')
    for x in (18, 28):
        logo.create_oval(x, 20, x + 3, 23, fill=GREEN, outline=GREEN)
    titles = tk.Frame(header, bg=BG)
    titles.pack(side='left')
    _label(titles, '微信 AI 朋友', 21, bold=True, bg=BG).pack(anchor='w')
    _label(titles, '从熟悉的聊天里，留住熟悉的表达。', 10, MUTED, bg=BG).pack(anchor='w', pady=(4, 0))
    _label(header, '本 地 工 作 台', 9, GREEN, bg=PALE, padx=13, pady=8).pack(side='right')

    footer = tk.Frame(app, bg=BG)
    footer.pack(side='bottom', fill='x', padx=26, pady=(9, 14))
    app._ui_status = _label(footer, '●  正在检查运行状态', 9, MUTED, bg=BG)
    app._ui_status.pack(side='left')
    app.rollback_btn = ttk.Button(footer, text='停用全部并恢复', style='Danger.TButton', command=app.rollback_wechat)
    app.rollback_btn.pack(side='right')
    progress_track = tk.Frame(app, bg=LINE, height=3)
    progress_track.pack(side='bottom', fill='x', padx=26)
    app.progress = ttk.Progressbar(progress_track, mode='determinate', style='Work.Horizontal.TProgressbar')
    app.progress.place(x=0, y=0, relwidth=1, height=3)

    viewport = tk.Frame(app, bg=BG)
    viewport.pack(fill='both', expand=True, padx=(26, 12))
    canvas = tk.Canvas(viewport, bg=BG, bd=0, highlightthickness=0)
    scrollbar = ttk.Scrollbar(viewport, orient='vertical', command=canvas.yview)
    scrollbar.pack(side='right', fill='y', padx=(6, 0))
    canvas.pack(side='left', fill='both', expand=True)
    canvas.configure(yscrollcommand=scrollbar.set)
    body = tk.Frame(canvas, bg=BG)
    body_id = canvas.create_window((0, 0), window=body, anchor='nw')
    app._ui_canvas = canvas
    app._ui_body = body

    settings = tk.Frame(body, bg=BG)
    settings.pack(fill='x')
    settings.columnconfigure(0, weight=1, uniform='settings')
    settings.columnconfigure(1, weight=1, uniform='settings')
    persona = _card(settings)
    model = _card(settings)
    persona.grid(row=0, column=0, sticky='nsew', padx=(0, 7))
    model.grid(row=0, column=1, sticky='nsew', padx=(7, 0))
    _section(persona, '01', '新建陪伴', '填写名称 → 选择人格文件 → 生成陪伴')
    person_form = tk.Frame(persona, bg=CARD)
    person_form.pack(fill='x', padx=20, pady=(0, 16))
    _label(person_form, '名称', 9, MUTED).pack(anchor='w', pady=(0, 5))
    app.name_entry = ttk.Entry(person_form, textvariable=app.name_var)
    app.name_entry.pack(fill='x')
    app._ui_mutable.append(app.name_entry)

    source_row = tk.Frame(person_form, bg=CARD)
    source_row.pack(fill='x', pady=(12, 0))
    app.choose_persona_file_btn = ttk.Button(source_row, text='选择人格文件', command=app.choose_persona_file)
    app.choose_persona_file_btn.pack(side='left')
    app._ui_mutable.append(app.choose_persona_file_btn)
    app._ui_persona_source = _label(source_row, '', 9, MUTED, justify='left')
    app._ui_persona_source.pack(side='left', fill='x', expand=True, padx=(10, 0))
    app._ui_persona_source.bind('<Configure>', lambda event: app._ui_persona_source.configure(wraplength=max(120, event.width)))

    _section(model, '02', '模型设置', '填写后即可在微信中聊天')
    model_form = tk.Frame(model, bg=CARD)
    model_form.pack(fill='x', padx=20)
    model_form.columnconfigure(1, weight=1)
    for row, label, variable in [(0, '服务地址', app.base_url_var), (1, '模型名称', app.model_var), (2, 'API Key', app.key_var)]:
        _label(model_form, label, 9, MUTED).grid(row=row, column=0, sticky='w', padx=(0, 12), pady=5)
        entry = ttk.Entry(model_form, textvariable=variable, show='•' if row == 2 else '')
        entry.grid(row=row, column=1, columnspan=1 if row == 2 else 2, sticky='ew', pady=5)
        app._ui_mutable.append(entry)
        if row == 2:
            app._ui_key_entry = entry
    def toggle_key():
        visible = bool(app._ui_key_entry.cget('show'))
        app._ui_key_entry.configure(show='' if visible else '•')
        app._ui_key_toggle.configure(text='隐藏' if visible else '显示')
    app._ui_key_toggle = ttk.Button(model_form, text='显示', width=4, command=toggle_key)
    app._ui_key_toggle.grid(row=2, column=2, padx=(6, 0))
    app._ui_mutable.append(app._ui_key_toggle)
    key_options = tk.Frame(model, bg=CARD)
    key_options.pack(fill='x', padx=20, pady=(9, 16))
    remember = ttk.Checkbutton(key_options, text='记住 Key · 本机加密保存', variable=app.remember_key_var, style='Card.TCheckbutton')
    remember.pack(side='left')
    app._ui_mutable.append(remember)
    app._ui_key_status = _label(key_options, '未填写', 9, MUTED)
    app._ui_key_status.pack(side='right')

    actions = tk.Frame(body, bg=BG)
    actions.pack(fill='x', pady=14)
    app.generate_btn = ttk.Button(actions, text='生成陪伴', style='Primary.TButton', command=app.generate)
    app.generate_btn.pack(side='left', padx=(0, 9))
    clear_draft = ttk.Button(actions, text='清空新建内容', command=app.new_persona)
    clear_draft.pack(side='left')
    app._ui_mutable.append(clear_draft)
    app._ui_step_status = _label(actions, '先填写名称并选择人格文件', 9, MUTED, bg=BG)
    app._ui_step_status.pack(side='right')

    companion_card = _card(body)
    companion_card.pack(fill='x', pady=(0, 14))
    companion_head = tk.Frame(companion_card, bg=CARD)
    companion_head.pack(fill='x', padx=20, pady=(12, 8))
    _label(companion_head, '已生成的陪伴', 12, bold=True).pack(side='left')
    app._ui_persona_count = _label(companion_head, '0 个陪伴', 9, MUTED)
    app._ui_persona_count.pack(side='left', padx=12)
    _label(companion_head, '每份陪伴独立绑定一位用户', 9, MUTED).pack(side='right')
    companion_list = tk.Frame(companion_card, bg=CARD)
    companion_list.pack(fill='x', padx=20)
    app.persona_tree = ttk.Treeview(companion_list, columns=('name', 'id', 'user', 'status'), show='headings', height=3, selectmode='browse')
    for key, label, width, minimum in [('name', '陪伴名称', 300, 160), ('id', '独立标识', 100, 70), ('user', '绑定账号备注', 240, 100), ('status', '绑定状态', 200, 100)]:
        app.persona_tree.heading(key, text=label, anchor='w')
        app.persona_tree.column(key, width=width, minwidth=minimum, anchor='w')
    app.persona_tree.pack(side='left', fill='x', expand=True)
    companion_scroll = ttk.Scrollbar(companion_list, orient='vertical', command=app.persona_tree.yview)
    companion_scroll.pack(side='right', fill='y')
    app.persona_tree.configure(yscrollcommand=companion_scroll.set)
    app.persona_tree.tag_configure('alternate', background='#F8FAF8')
    app.persona_tree.bind('<<TreeviewSelect>>', app.select_persona)
    app._ui_empty_personas = _label(companion_list, '生成后的陪伴会显示在这里', 10, MUTED, anchor='center')
    app._ui_empty_personas.place(relx=0.5, rely=0.63, anchor='center')
    companion_actions = tk.Frame(companion_card, bg=CARD)
    companion_actions.pack(fill='x', padx=20, pady=(8, 12))
    app._ui_clone_btn = ttk.Button(companion_actions, text='基于此新建', command=app.clone_selected_companion)
    app._ui_clone_btn.pack(side='left', padx=(0, 8))
    app._ui_bind_companion_btn = ttk.Button(companion_actions, text='绑定用户 / 扫码', style='Soft.TButton', command=app.bind_selected_companion)
    app._ui_bind_companion_btn.pack(side='left')
    app._ui_media_btn = ttk.Button(companion_actions, text='表情包', command=app.open_media_settings)
    app._ui_media_btn.pack(side='left', padx=(8, 0))
    app._ui_delete_companion_btn = ttk.Button(companion_actions, text='删除陪伴', style='Danger.TButton', command=app.delete_selected_companion)
    app._ui_delete_companion_btn.pack(side='left', padx=(8, 0))
    _label(companion_actions, '副本保留人格设定，记忆与聊天各自独立', 9, MUTED).pack(side='right')

    voice_row = tk.Frame(companion_card, bg=CARD)
    voice_row.pack(fill='x', padx=20, pady=(0, 6))
    _label(voice_row, '当前陪伴语音', 10).pack(side='left', padx=(0, 10))
    app._ui_voice_mode_var = tk.StringVar(value='文字')
    app._ui_voice_mode = ttk.Combobox(voice_row, textvariable=app._ui_voice_mode_var,
        values=('文字', '文字和语音'), state='disabled', width=13)
    app._ui_voice_mode.pack(side='left')
    app._ui_voice_mode.bind('<<ComboboxSelected>>', app.save_voice_selection)
    _label(voice_row, '音色', 10).pack(side='left', padx=(16, 8))
    app._ui_voice_choice_var = tk.StringVar()
    app._ui_voice_choice = ttk.Combobox(voice_row, textvariable=app._ui_voice_choice_var,
        state='disabled', width=13)
    app._ui_voice_choice.pack(side='left')
    app._ui_voice_choice.bind('<<ComboboxSelected>>', app.save_voice_selection)
    app._ui_voice_preview = ttk.Button(voice_row, text='试听音频', command=app.preview_selected_voice, state='disabled')
    app._ui_voice_preview.pack(side='left', padx=(10, 8))
    app._ui_voice_key = ttk.Button(voice_row, text='语音配置', command=app.open_voice_key_settings)
    app._ui_voice_key.pack(side='left')
    app._ui_voice_status = _label(companion_card,
        '每个陪伴独立设置；文字和语音模式由模型按内容选择。微信目前以音频附件发送。',
        9, MUTED, wraplength=1000)
    app._ui_voice_status.pack(anchor='w', padx=20, pady=(0, 12))

    account_card = _card(body)
    account_card.pack(fill='x', pady=(0, 14))
    account_head = tk.Frame(account_card, bg=CARD)
    account_head.pack(fill='x', padx=20, pady=(12, 8))
    _label(account_head, '微信账号', 12, bold=True).pack(side='left')
    app._ui_account_count = _label(account_head, '0 个账号', 9, MUTED)
    app._ui_account_count.pack(side='left', padx=12)
    app.bind_btn = ttk.Button(account_head, text='重新扫码', style='Soft.TButton', command=app.bind_wechat)
    app.bind_btn.pack(side='left')
    refresh_button = ttk.Button(account_head, text='检查状态', width=8, command=app.refresh_runtime_status)
    refresh_button.pack(side='right', padx=(0, 8))
    app._ui_mutable.append(refresh_button)
    _label(account_card, '账号备注默认沿用陪伴名；当前微信渠道未提供对方昵称。', 9, MUTED).pack(anchor='w', padx=20, pady=(0, 6))
    _label(account_card, '运行状态每 10 秒自动检查；“接收服务运行”表示微信接收程序已启动。',
           9, MUTED, wraplength=1000).pack(anchor='w', padx=20, pady=(0, 6))
    tree_frame = tk.Frame(account_card, bg=CARD)
    tree_frame.pack(fill='x', padx=20)
    app.account_tree = ttk.Treeview(tree_frame, columns=('name', 'persona', 'id', 'status', 'last'), show='headings', height=3, selectmode='browse')
    for key, label, width, minimum in [('name', '账号备注', 120, 80), ('persona', '专属人格', 170, 100), ('id', '账号标识', 160, 100), ('status', '实际状态', 130, 90), ('last', '操作 / 状态说明', 270, 140)]:
        app.account_tree.heading(key, text=label, anchor='w')
        app.account_tree.column(key, width=width, minwidth=minimum, anchor='w')
    app.account_tree.pack(side='left', fill='x', expand=True)
    tree_scroll = ttk.Scrollbar(tree_frame, orient='vertical', command=app.account_tree.yview)
    tree_scroll.pack(side='right', fill='y')
    app.account_tree.configure(yscrollcommand=tree_scroll.set)
    app.account_tree.tag_configure('alternate', background='#F8FAF8')
    app.account_tree.tag_configure('active', foreground=GREEN)
    app.account_tree.tag_configure('waiting', foreground='#A4702F')
    app.account_tree.tag_configure('failed', foreground=RED)
    app._ui_empty_accounts = _label(tree_frame, '在上方选择一个陪伴，再点击“绑定用户 / 扫码”', 10, MUTED, justify='center', anchor='center')
    app._ui_empty_accounts.place(relx=0.5, rely=0.63, anchor='center')
    account_actions = tk.Frame(account_card, bg=CARD)
    account_actions.pack(fill='x', padx=20, pady=(8, 12))
    app._ui_selection_buttons = []
    for title, command in [('分配选中的陪伴', app.assign_selected_persona), ('启动账号', lambda: app.toggle_selected_account(True)), ('停用账号', lambda: app.toggle_selected_account(False)), ('查看聊天记录', app.view_selected_logs)]:
        button = ttk.Button(account_actions, text=title, command=command, state='disabled')
        button.pack(side='left', padx=(0, 8))
        app._ui_selection_buttons.append(button)
    remove = ttk.Button(account_actions, text='解除绑定', style='Danger.TButton', command=app.remove_selected_account, state='disabled')
    remove.pack(side='right')
    app._ui_selection_buttons.append(remove)
    app.account_tree.bind('<<TreeviewSelect>>', lambda event: refresh_ui_state(app))

    log_card = _card(body)
    log_card.pack(fill='both', expand=True, pady=(0, 4))
    log_head = tk.Frame(log_card, bg=CARD)
    log_head.pack(fill='x', padx=20, pady=(13, 9))
    _label(log_head, '运行日志', 12, bold=True).pack(side='left')
    _label(log_head, '生成与账号操作会显示在这里', 9, MUTED).pack(side='right')
    log_body = tk.Frame(log_card, bg=CARD)
    log_body.pack(fill='both', expand=True, padx=20, pady=(0, 15))
    app.log = tk.Text(log_body, height=4, wrap='word', state='disabled', font=(FONT, 9),
                      bg='#F7F9F7', fg='#4B6256', relief='flat', bd=0, padx=12, pady=8,
                      spacing1=3, spacing3=3, selectbackground='#DCEEE3', insertbackground=GREEN)
    app.log.pack(side='left', fill='both', expand=True)
    log_scroll = ttk.Scrollbar(log_body, orient='vertical', command=app.log.yview)
    log_scroll.pack(side='right', fill='y')
    app.log.configure(yscrollcommand=log_scroll.set)
    app.log.tag_configure('time', foreground='#8A9990')
    app.log.tag_configure('success', foreground=GREEN)
    app.log.tag_configure('error', foreground=RED)

    app._ui_two_columns = True
    def fit_body(event):
        canvas.itemconfigure(body_id, width=event.width)
        wide = event.width >= _display.px(app, 980)
        if wide != app._ui_two_columns:
            app._ui_two_columns = wide
            persona.grid_configure(row=0, column=0, columnspan=1 if wide else 2, padx=_display.px(app, (0, 7)) if wide else 0, pady=0)
            model.grid_configure(row=0 if wide else 1, column=1 if wide else 0, columnspan=1 if wide else 2, padx=_display.px(app, (7, 0)) if wide else 0, pady=0 if wide else _display.px(app, (12, 0)))
        if event.width < _display.px(app, 870):
            app._ui_step_status.pack_forget()
        elif not app._ui_step_status.winfo_manager():
            app._ui_step_status.pack(side='right')
    canvas.bind('<Configure>', fit_body)
    _display.scale_widgets(app)
    app._ui_scroll = SmoothScroll(canvas, body, scrollbar, pixels_per_notch=_display.px(app, 48),
                                  nested={app.persona_tree: app.persona_tree, companion_scroll: app.persona_tree,
                                          app.account_tree: app.account_tree, tree_scroll: app.account_tree,
                                          app.log: app.log, log_scroll: app.log})
    app.key_var.trace_add('write', lambda *args: refresh_ui_state(app))
    def name_changed(*args):
        refresh_ui_state(app)
    app.name_var.trace_add('write', name_changed)
    app.write_log('填写名称、选择人格文件后生成。已生成的陪伴可绑定用户，也可基于它新建独立副本。')
    refresh_ui_state(app)


def refresh_ui_state(app):
    if not hasattr(app, '_ui_status'):
        return
    has_name = bool(app.name_var.get().strip())
    source_path = getattr(app, 'selected_profile_path', None)
    if hasattr(app, '_ui_persona_source'):
        if getattr(app, 'draft_source_persona_id', None):
            source_text = '基于：' + app.draft_source_name + ' · 独立副本'
        elif source_path:
            source_text = '已选：' + source_path.name
        else:
            source_text = '请选择 .md / .txt 人格文件'
        app._ui_persona_source.configure(text=source_text)
    if hasattr(app, '_ui_persona_count'):
        persona_count = len(getattr(app, 'persona_choices', []))
        app._ui_persona_count.configure(text=f'{persona_count} 个陪伴')
        if persona_count:
            app._ui_empty_personas.place_forget()
        else:
            app._ui_empty_personas.place(relx=0.5, rely=0.63, anchor='center')
        can_use_persona = bool(getattr(app, 'current_persona_id', None)) and not app._ui_busy
        app._ui_clone_btn.configure(state='normal' if can_use_persona else 'disabled')
        app._ui_delete_companion_btn.configure(state='normal' if can_use_persona else 'disabled')
        if hasattr(app, '_ui_media_btn'):
            app._ui_media_btn.configure(state='normal' if can_use_persona else 'disabled')
        if hasattr(app, '_ui_voice_mode'):
            for combo in (app._ui_voice_mode, app._ui_voice_choice):
                combo.configure(state='readonly' if can_use_persona else 'disabled')
            app._ui_voice_preview.configure(state='disabled' if app._ui_busy else 'normal')
            app._ui_voice_key.configure(state='disabled' if app._ui_busy else 'normal')
        owner = getattr(app, 'persona_owners', {}).get(app.current_persona_id)
        owner_account = next((item for item in getattr(app, 'accounts', []) if item['id'] == owner), None)
        pending = owner_account and (not owner_account.get('provider_id') or '失败' in owner_account.get('status', ''))
        bind_text = '继续扫码' if pending else '查看绑定用户' if owner else '绑定用户 / 扫码'
        app._ui_bind_companion_btn.configure(state='normal' if can_use_persona else 'disabled', text=bind_text)
    if hasattr(app, '_ui_key_status'):
        app._ui_key_status.configure(text='已填写' if app.key_var.get().strip() else '未填写',
                                     fg=GREEN if app.key_var.get().strip() else MUTED)
    if not hasattr(app, '_ui_account_count'):
        return
    count = len(app.accounts)
    app._ui_account_count.configure(text=f'{count} 个账号')
    if count:
        app._ui_empty_accounts.place_forget()
    else:
        app._ui_empty_accounts.place(relx=0.5, rely=0.63, anchor='center')
    selected = bool(app.account_tree.selection()) and not app._ui_busy
    if hasattr(app, 'bind_btn'):
        app.bind_btn.configure(state='normal' if selected else 'disabled')
    for button in app._ui_selection_buttons:
        button.configure(state='normal' if selected else 'disabled')
    if app._ui_busy:
        busy_message = getattr(app, '_ui_busy_message', None)
        app._ui_step_status.configure(text=busy_message or '正在应用陪伴设定…', fg=GREEN)
    elif has_name and source_path:
        app._ui_step_status.configure(text='点击生成陪伴，创建新的独立副本', fg=GREEN)
    else:
        app._ui_step_status.configure(text='先填写名称并选择人格文件', fg=MUTED)
    runtime = overview(getattr(app, '_runtime_snapshot', None), app.accounts)
    color = GREEN if runtime.tone == 'active' else RED if runtime.tone == 'failed' else '#A4702F'
    detail = ' · ' + runtime.detail if runtime.detail else ''
    app._ui_status.configure(text='●  ' + runtime.title + detail, fg=color)
    app.generate_btn.configure(state='disabled' if app._ui_busy or not has_name or not source_path else 'normal')


def append_log(app, message):
    app.log.configure(state='normal')
    app.log.insert('end', datetime.now().strftime('%H:%M:%S') + '   ', 'time')
    severity = 'error' if any(word in message for word in ('失败', '错误', '异常')) else 'success' if any(word in message for word in ('完成', '已保存', '已匹配')) else ''
    app.log.insert('end', str(message) + chr(10), severity)
    app.log.see('end')
    app.log.configure(state='disabled')
    refresh_ui_state(app)


def set_busy(app, busy):
    app._ui_busy = busy
    state = 'disabled' if busy else 'normal'
    for widget in [app.generate_btn, app.bind_btn, app.rollback_btn, *app._ui_mutable]:
        widget.configure(state=state)
    app.generate_btn.configure(text='正在生成陪伴…' if busy else '生成陪伴')
    if busy:
        app.progress.configure(mode='indeterminate')
        app.progress.start(12)
    else:
        app.progress.stop()
        app.progress.configure(mode='determinate', value=0)
    refresh_ui_state(app)


def build_history_window(app, account, paths):
    window = tk.Toplevel(app)
    window.title('聊天记录 · ' + account['name'])
    _display.size_window(window, 900, 680, 700, 480)
    window.configure(bg=BG)
    header = tk.Frame(window, bg=BG)
    header.pack(fill='x', padx=24, pady=20)
    titles = tk.Frame(header, bg=BG)
    titles.pack(side='left')
    _label(titles, account['name'] + '的聊天记录', 17, bold=True, bg=BG).pack(anchor='w')
    _label(titles, account['id'], 9, MUTED, bg=BG).pack(anchor='w', pady=(4, 0))
    content = _card(window)
    content.pack(fill='both', expand=True, padx=24, pady=(0, 24))
    text = tk.Text(content, wrap='word', font=(FONT, 10), bg=CARD, fg=INK, bd=0, relief='flat',
                   padx=18, pady=16, spacing1=3, spacing3=5, selectbackground='#DCEEE3')
    text.pack(side='left', fill='both', expand=True)
    scrollbar = ttk.Scrollbar(content, orient='vertical', command=text.yview)
    scrollbar.pack(side='right', fill='y')
    text.configure(yscrollcommand=scrollbar.set)
    ttk.Button(header, text='刷新记录', style='Soft.TButton', command=lambda: app.refresh_log_window(window, text, account['id'])).pack(side='right')
    ttk.Button(header, text='打开记录目录', command=app.open_sessions_directory).pack(side='right', padx=8)
    text.insert('1.0', app.compose_account_logs(paths))
    text.configure(state='disabled')
    _display.scale_widgets(window)
    return window
