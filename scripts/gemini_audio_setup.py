"""Temporary local credential/analysis dialog for the current audio task."""
from pathlib import Path
import queue
import json
import sys
import threading
import tkinter as tk
from tkinter import ttk
import webbrowser

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import gemini_audio as audio
import display_support as display


def main():
    display.enable_dpi_awareness()
    app = tk.Tk()
    app.title('Gemini · 三段原声音频分析')
    display.configure_root(app)
    display.size_window(app, 740, 620, 650, 550)
    frame = ttk.Frame(app, padding=20)
    frame.pack(fill='both', expand=True)
    ttk.Label(frame, text='填写 Gemini Key，分析这三段原声', font=('Microsoft YaHei UI', 15, 'bold')).pack(anchor='w')
    ttk.Label(frame, text='模型：Gemini 3.8 Flash。当前只是辅助分析工具。', padding=(0, 8)).pack(anchor='w')
    ttk.Button(frame, text='打开 Google AI Studio 获取 Key', command=lambda: webbrowser.open('https://aistudio.google.com/apikey')).pack(anchor='w', pady=6)
    value = tk.StringVar()
    ttk.Label(frame, text='API Key（只在本机填写，不发到聊天里）').pack(anchor='w')
    entry = ttk.Entry(frame, textvariable=value, show='•', width=68)
    entry.pack(fill='x', pady=(5, 8))
    free = tk.BooleanVar(value=audio.saved_free_project_confirmation())
    value.trace_add('write', lambda *_: free.set(False) if value.get().strip() else None)
    ttk.Checkbutton(frame, text='这个 Key 来自未启用结算的免费项目', variable=free).pack(anchor='w')
    ttk.Label(frame, text='将发送：原声1（4秒）、原声2（3.6秒）、原声3（1.3秒）。', padding=(0, 10)).pack(anchor='w')
    ttk.Label(frame, text='点击下方按钮后，三段音频将发到 Google 分析。免费层输入可能用于产品改进。', wraplength=680).pack(anchor='w')
    ttk.Label(frame, text='Key 使用 Windows 当前用户加密保存；免费额度不足时停止，不自动升级。', wraplength=680).pack(anchor='w', pady=(5, 10))
    status = tk.StringVar(value='等待填写 Key，尚未发送音频。')
    try:
        previous = json.loads(audio.LAST_ERROR_FILE.read_text(encoding='utf-8'))
        status.set(previous['message'])
    except (OSError, ValueError, KeyError):
        if audio.KEY_FILE.is_file():
            status.set('Key 已加密保存，可以使用现有 Key 手动分析。')
    ttk.Label(frame, textvariable=status, wraplength=680).pack(anchor='w', pady=6)
    result_text = tk.Text(frame, height=9, wrap='word', state='disabled', font=('Microsoft YaHei UI', 10))
    result_text.pack(fill='both', expand=True, pady=8)
    results = queue.SimpleQueue()

    def start():
        key = value.get().strip()
        if not key:
            try:
                key = audio.load_key()
            except Exception:
                status.set('已保存的 Key 无法读取，请重新填写。')
                return
        if not key:
            status.set('请先填写 Gemini API Key。')
            entry.focus_set()
            return
        if not free.get():
            status.set('请确认使用的是未启用结算的免费项目。')
            return
        try:
            audio.save_key(key)
            audio.save_free_project_confirmation(True)
        except Exception:
            status.set('无法加密保存 Key，请检查 Key 格式和本机权限。')
            return
        value.set('')
        button.configure(state='disabled')
        status.set('正在发送三段原声并分析声音特征……')

        def worker():
            try:
                report = audio.analyze(key, audio.reference_files(), free_project_confirmed=True,
                                       audio_submission_authorized=True)
                audio.save_report(report)
                results.put((None, report))
            except audio.AudioAnalysisError as error:
                results.put((str(error), None))
            except Exception:
                results.put(('本机分析或保存结果失败，请检查文件后重试。', None))
        threading.Thread(target=worker, daemon=True).start()

    def poll():
        while not results.empty():
            error, report = results.get_nowait()
            button.configure(state='normal')
            if error:
                status.set(error)
                continue
            status.set('分析完成，结果已保存。可以回到聊天告诉助手“分析好了”。')
            analysis = report['analysis']
            lines = []
            for clip in analysis['clips']:
                lines.append(f"原声{clip['clip']}：{clip.get('timbre', '')}\n语气与节奏：{clip.get('delivery', '')}\n不确定处：{clip.get('uncertainty', '')}\n")
            lines.extend(['共同特点：' + analysis.get('common_features', ''),
                          '选音建议：' + analysis.get('selection_advice', ''),
                          '限制：' + analysis.get('limitations', '')])
            result_text.configure(state='normal')
            result_text.delete('1.0', 'end')
            result_text.insert('end', '\n'.join(lines))
            result_text.configure(state='disabled')
        app.after(100, poll)

    button = ttk.Button(frame, text='加密保存 Key 并分析这三段原声', command=start)
    button.pack(anchor='e', pady=(8, 0))
    display.scale_widgets(frame)
    app.after(100, poll)
    app.mainloop()


if __name__ == '__main__':
    main()
