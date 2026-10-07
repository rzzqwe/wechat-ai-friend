"""Tk trial window; background workers never read or mutate Tk objects."""
from __future__ import annotations

import json
import queue
import threading
import tkinter as tk
from tkinter import ttk
from tkinter.scrolledtext import ScrolledText

import display_support as display
import persona_chat as chat


class TrialWindow(tk.Toplevel):
    def __init__(self, parent, session: chat.TrialSession, model_config):
        super().__init__(parent)
        self.title('检索与试聊 · 当前导入记录')
        display.size_window(self, 960, 720, 720, 540)
        self.session = session
        self.model_config = model_config
        self.results = queue.Queue()
        self.pending = None
        self.busy = False
        self.closed = False
        self.poll_id = None
        self.use_retrieval = tk.BooleanVar(value=True)
        self.status = tk.StringVar(value='输入内容后可离线查看依据，或点击模型试聊。')
        approved = sum(row.review_status == 'approved' for row in session.evidence)
        outer = ttk.Frame(self, padding=16)
        outer.pack(fill='both', expand=True)
        ttk.Label(outer, text=f'本地样本 {len(session.evidence)} 条 · 已复核 {approved} 条 · 会话仅保存在此窗口',
                  font=('Microsoft YaHei UI', 11, 'bold')).pack(anchor='w')
        ttk.Label(outer, text='仅检索不联网；模型试聊会将人格、命中片段和最近 12 轮对话发给主窗口配置的模型。',
                  wraplength=820).pack(anchor='w', pady=(6, 3))
        ttk.Label(outer, text='使用当前记录的复核人格或本地保守初稿；模型检索只采用已复核片段。',
                  wraplength=820).pack(anchor='w', pady=(0, 8))
        self.mode = ttk.Checkbutton(outer, text='逐轮检索（取消勾选可对照静态人格）', variable=self.use_retrieval)
        self.mode.pack(anchor='w', pady=(0, 8))
        self.tabs = ttk.Notebook(outer)
        self.tabs.pack(fill='both', expand=True)
        self.transcript = self._tab('试聊记录')
        self.evidence = self._tab('本轮检索依据')
        self.preview = self._tab('发送预览（常见敏感信息已遮盖）')
        self.query = ScrolledText(outer, height=3, wrap='word', font=('Microsoft YaHei UI', 10))
        self.query.pack(fill='x', pady=(12, 8))
        self.query.bind('<Control-Return>', lambda event: self.send())
        buttons = ttk.Frame(outer)
        buttons.pack(fill='x')
        self.inspect_btn = ttk.Button(buttons, text='仅检索 / 预览', command=self.inspect)
        self.inspect_btn.pack(side='left')
        self.send_btn = ttk.Button(buttons, text='模型试聊', command=self.send)
        self.send_btn.pack(side='left', padx=8)
        ttk.Button(buttons, text='清空本轮会话', command=self.reset).pack(side='left')
        ttk.Label(outer, textvariable=self.status, wraplength=820).pack(anchor='w', pady=(8, 0))
        self.protocol('WM_DELETE_WINDOW', self.close)
        display.scale_widgets(outer)
        self.poll_id = self.after(100, self._poll)
        self.query.focus_set()

    def _tab(self, title):
        page = ttk.Frame(self.tabs)
        text = ScrolledText(page, wrap='word', font=('Microsoft YaHei UI', 10), state='disabled')
        text.pack(fill='both', expand=True)
        self.tabs.add(page, text=title)
        return text

    @staticmethod
    def _show(widget, content):
        widget.configure(state='normal')
        widget.delete('1.0', 'end')
        widget.insert('1.0', content)
        widget.configure(state='disabled')

    def _prepare(self):
        turn = self.session.prepare(self.query.get('1.0', 'end'), use_retrieval=self.use_retrieval.get())
        self._show(self.evidence, turn.evidence)
        self._show(self.preview, json.dumps(turn.request_messages, ensure_ascii=False, indent=2))
        return turn

    def inspect(self):
        try:
            turn = self._prepare()
            self.tabs.select(1)
            self.status.set(f'离线完成：{len(turn.matched_ids)} 个匹配。未向模型发送。')
        except ValueError as exc:
            self.status.set(str(exc))

    def _set_busy(self, busy):
        self.busy = busy
        for widget in (self.send_btn, self.inspect_btn, self.mode, self.query):
            widget.configure(state='disabled' if busy else 'normal')

    def send(self):
        if self.busy or self.closed:
            return
        try:
            turn = self._prepare()
            config = self.model_config()  # Read Tk variables on the UI thread.
            if not config['base_url'].strip() or not config['model'].strip():
                raise ValueError('请先在主窗口填写模型地址和名称。')
        except ValueError as exc:
            self.status.set(str(exc))
            return
        self.pending = turn
        self._set_busy(True)
        self.tabs.select(0)
        self.status.set('正在请求模型；成功后才加入会话。')
        results = self.results

        def worker():
            try:
                reply = chat.call_reply(**config, messages=turn.request_messages)
                results.put((turn, reply, None))
            except Exception as exc:
                # Never display an arbitrary exception that could include a Key.
                error = str(exc) if isinstance(exc, ValueError) else '试聊请求失败，请检查模型配置后重试。'
                results.put((turn, None, error))

        threading.Thread(target=worker, daemon=True).start()

    def _poll(self):
        if self.closed:
            return
        try:
            turn, reply, error = self.results.get_nowait()
        except queue.Empty:
            pass
        else:
            self._set_busy(False)
            self.pending = None
            if turn.revision != self.session.revision:
                self.status.set('旧请求已结束；其回复未加入清空后的会话。')
            elif error:
                self.status.set(error + ' 本轮未写入会话，可重试。')
            else:
                self.session.complete(turn, reply)
                nl = chr(10)
                transcript = (nl * 2).join(f'我：{user}{nl}AI：{assistant}' for user, assistant in self.session.turns)
                self._show(self.transcript, transcript)
                self.transcript.see('end')
                self.query.delete('1.0', 'end')
                self.status.set(f'已完成；本轮引用 {len(turn.matched_ids)} 个片段。AI回复不会写入人物索引。')
        self.poll_id = self.after(100, self._poll)

    def reset(self):
        self.session.reset()
        for widget in (self.transcript, self.evidence, self.preview):
            self._show(widget, '')
        if not self.busy:
            self.query.delete('1.0', 'end')
        self.status.set('会话已清空。' + ('正在进行的请求结束后会丢弃其回复。' if self.busy else ''))

    def close(self):
        self.closed = True
        self.session.reset()
        if self.poll_id is not None:
            self.after_cancel(self.poll_id)
        self.destroy()
