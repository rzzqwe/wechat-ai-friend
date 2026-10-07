"""Temporary local setup dialog for the selected Qwen reference-audio tool."""
from pathlib import Path
import json
import queue
import sys
import threading
import tkinter as tk
from tkinter import ttk
import webbrowser

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import qwen_omni_audio as audio
import display_support as display


def main():
    display.enable_dpi_awareness()
    app = tk.Tk()
    app.title('千问 · 三段原声音频分析')
    display.configure_root(app)
    display.size_window(app, 740, 620, 650, 550)
    frame = ttk.Frame(app, padding=20)
    frame.pack(fill='both', expand=True)
    ttk.Label(frame, text='填写百炼 Key，分析这三段原声', font=('Microsoft YaHei UI', 15, 'bold')).pack(anchor='w')
    ttk.Label(frame, text='模型：Qwen3.8-Omni-Flash · 北京地域 · 仅使用已确认的免费额度', padding=(0, 8)).pack(anchor='w')
    ttk.Button(frame, text='打开百炼北京地域的 API Key 页面', command=lambda: webbrowser.open(audio.KEY_URL)).pack(anchor='w', pady=6)
    ttk.Label(frame, text='百炼 API Key（sk- 开头；Google 和 Fish 的 Key 不能代用）').pack(anchor='w')
    value = tk.StringVar()
    entry = ttk.Entry(frame, textvariable=value, show='•', width=68)
    entry.pack(fill='x', pady=(5, 8))
    ttk.Label(frame, text='此模型需要免费额度和控制台“用完即停”保护。额度未确认时停止分析。', wraplength=680).pack(anchor='w', pady=5)
    ttk.Button(frame, text='打开北京免费额度页，查看余量和用完即停', command=lambda: webbrowser.open(audio.QUOTA_URL)).pack(anchor='w', pady=4)
    ttk.Label(frame, text='将发送：原声1（4秒）、原声2（3.6秒）、原声3（1.3秒）。', padding=(0, 10)).pack(anchor='w')
    ttk.Label(frame, text='点击分析后，三段音频会分别提交到阿里云北京地域的音频模型。', wraplength=680).pack(anchor='w')
    ttk.Label(frame, text='Key 使用 Windows 当前用户加密保存，只在本机填写。', wraplength=680).pack(anchor='w', pady=(5, 10))
    status = tk.StringVar(value='等待填写北京地域的百炼 Key。')
    if audio.KEY_FILE.is_file():
        status.set('百炼 Key 已加密保存，可以使用现有 Key 分析。')
    try:
        previous = json.loads(audio.LAST_ERROR_FILE.read_text(encoding='utf-8'))
        if previous.get('model') == audio.MODEL and previous.get('message'):
            status.set(previous['message'])
    except (OSError, ValueError, KeyError):
        pass
    ttk.Label(frame, textvariable=status, wraplength=680).pack(anchor='w', pady=6)
    result_text = tk.Text(frame, height=9, wrap='word', state='disabled', font=('Microsoft YaHei UI', 10))
    result_text.pack(fill='both', expand=True, pady=8)
    results = queue.SimpleQueue()
    busy = [False]

    def start():
        if busy[0]:
            return
        try:
            key = value.get().strip() or audio.load_key()
            if not key:
                status.set('请先填写百炼北京地域的 API Key。')
                entry.focus_set()
                return
            if key != audio.load_key():
                audio.save_key(key)
            audio.check_free_tier()
        except audio.AudioAnalysisError as error:
            status.set(str(error))
            return
        except Exception:
            status.set('无法加密保存百炼 Key，请检查本机权限。')
            return
        value.set('')
        busy[0] = True
        button.configure(state='disabled')
        status.set('正在分析原声……')

        def worker():
            try:
                report = audio.analyze(key, audio.reference_files(), audio_submission_authorized=True,
                    progress=lambda index: results.put(('progress', index)))
                results.put(('complete', report))
            except audio.AudioAnalysisError as error:
                results.put(('error', str(error)))
            except Exception:
                results.put(('error', '本机分析或保存失败，请检查文件后重试。'))
        threading.Thread(target=worker, daemon=True).start()

    def poll():
        while not results.empty():
            kind, result = results.get_nowait()
            if kind == 'progress':
                status.set(f'正在分析原声 {result}/3……')
                continue
            busy[0] = False
            button.configure(state='normal')
            if kind == 'error':
                status.set(result)
                continue
            status.set('三段分析完成，已保存结果。可以回到聊天告诉助手“分析好了”。')
            show_result(result)
        app.after(100, poll)

    def show_result(result):
        lines = []
        for clip in result['analysis']['clips']:
            lines.append(f"原声{clip['clip']}：{clip.get('timbre', '')}\n语气与节奏：{clip.get('delivery', '')}\n不确定处：{clip.get('uncertainty', '')}\n")
        lines.append('筛选描述词：' + '、'.join(result['analysis']['search_terms']))
        result_text.configure(state='normal')
        result_text.delete('1.0', 'end')
        result_text.insert('end', '\n'.join(lines))
        result_text.configure(state='disabled')

    button = ttk.Button(frame, text='加密保存百炼 Key 并分析三段原声', command=start)
    button.pack(anchor='e', pady=(8, 0))
    try:
        report = json.loads(audio.REPORT_FILE.read_text(encoding='utf-8'))
        if report.get('model') == audio.MODEL and report.get('status') == 'complete':
            show_result(report)
            status.set('三段原声已分析成功，下面显示保存的结果。')
    except (OSError, ValueError, KeyError, TypeError):
        pass
    display.scale_widgets(frame)
    app.after(100, poll)
    app.mainloop()


if __name__ == '__main__':
    main()
