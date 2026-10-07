"""Real child output and local HTTP download progress; no external API calls."""
import http.server
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(os.name == 'nt' and shutil.which('powershell'), 'Windows progress integration')
class StartupProgressTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def command(self, body, **environment):
        probe = self.root / 'probe.ps1'
        probe.write_text("$ErrorActionPreference='Stop'\n. $env:PROGRESS_LIBRARY\n" + body, encoding='utf-8-sig')
        env = dict(os.environ, PROGRESS_LIBRARY=str(ROOT / 'scripts/startup_progress.ps1'), **environment)
        return [shutil.which('powershell'), '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(probe)], env

    def test_process_logs_are_emitted_before_the_child_exits(self):
        child = self.root / 'child.py'
        child.write_text("import time\nprint('FIRST_PROGRESS_LINE',flush=True)\ntime.sleep(2)\nprint('SECOND_PROGRESS_LINE',flush=True)\n", encoding='utf-8')
        command, env = self.command("Start-StartupProgress '测试安装'\nSet-StartupStage 3 '安装依赖'\n$r=Invoke-StartupProcess $env:CHILD_PYTHON @($env:CHILD_SCRIPT)\nexit $r.Code\n",
                                    CHILD_PYTHON=sys.executable, CHILD_SCRIPT=str(child))
        process = subprocess.Popen(command, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   text=True, encoding='utf-8', errors='replace')
        try:
            lines = []
            for line in process.stdout:
                lines.append(line)
                if 'FIRST_PROGRESS_LINE' in line:
                    self.assertIsNone(process.poll())
                    break
            else:
                self.fail('No live output was received')
            output, errors = process.communicate(timeout=15)
            self.assertEqual(process.returncode, 0, ''.join(lines) + output + errors)
            self.assertIn('SECOND_PROGRESS_LINE', output)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
            process.stdout.close()
            process.stderr.close()

    def test_native_argument_quoting_preserves_spaces_quotes_unicode_and_trailing_slashes(self):
        arguments = ['', 'a b', '他说"你好"', r'C:\path with spaces' + chr(92), 'literal%value&name']
        body = """$values=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($env:ARGUMENTS_DATA)) | ConvertFrom-Json
$r=Invoke-StartupProcess $env:CHILD_PYTHON (@('-c','import json,sys; print(json.dumps(sys.argv[1:],ensure_ascii=False))') + @($values))
exit $r.Code
"""
        import base64
        command, env = self.command(body, CHILD_PYTHON=sys.executable,
                                    ARGUMENTS_DATA=base64.b64encode(json.dumps(arguments).encode()).decode())
        result = subprocess.run(command, env=env, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(json.loads(result.stdout.strip().splitlines()[-1]), arguments)

    def test_readiness_marker_reports_completion_and_is_not_printed_as_a_log(self):
        command, env = self.command("Start-StartupProgress '启动工作台'\nSet-StartupStage 7 '打开工作台'\n$r=Invoke-StartupProcess $env:CHILD_PYTHON @('-c','print(''__WECHAT_WORKBENCH_READY__'',flush=True)') '__WECHAT_WORKBENCH_READY__'\nexit $r.Code\n", CHILD_PYTHON=sys.executable)
        result = subprocess.run(command, env=env, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('[完成]', result.stdout)
        self.assertNotIn('__WECHAT_WORKBENCH_READY__', result.stdout)

    def test_live_logs_do_not_expose_api_keys_from_the_environment(self):
        secret = 'fixture-api-secret-123'
        command, env = self.command("$r=Invoke-StartupProcess $env:CHILD_PYTHON @('-c','import os; print(os.environ[''WECHAT_AI_API_KEY''])')\nexit $r.Code\n",
                                    CHILD_PYTHON=sys.executable, WECHAT_AI_API_KEY=secret)
        result = subprocess.run(command, env=env, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn(secret, result.stdout + result.stderr)
        self.assertIn('[已隐藏]', result.stdout)

    def test_scan_stage_markers_are_consumed_without_hiding_qr_output(self):
        body="""$r=Invoke-StartupProcess $env:CHILD_PYTHON @('-c','print("__QR_READY__"); print("VISIBLE_QR")') -LineHandler {
param($line)
if($line -ceq '__QR_READY__'){Write-Host 'SCAN_STAGE_READY';return $true};return $false
}
exit $r.Code
"""
        command,env=self.command(body,CHILD_PYTHON=sys.executable)
        result=subprocess.run(command,env=env,capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=15)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertIn('SCAN_STAGE_READY',result.stdout)
        self.assertIn('VISIBLE_QR',result.stdout)
        self.assertNotIn('__QR_READY__',result.stdout)

    def test_quiet_checks_show_a_heartbeat_without_printing_captured_output(self):
        command, env = self.command("Start-StartupProgress '检查组件'\nSet-StartupStage 6 '检查微信插件'\n$r=Invoke-StartupProcess $env:CHILD_PYTHON @('-c','import time; time.sleep(5.2); print(''PRIVATE_PROBE_RESULT'')') -Quiet\nif ($r.Text -notlike '*PRIVATE_PROBE_RESULT*') { exit 2 }; exit $r.Code\n", CHILD_PYTHON=sys.executable)
        result = subprocess.run(command, env=env, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('仍在处理', result.stdout)
        self.assertNotIn('PRIVATE_PROBE_RESULT', result.stdout)

    def test_download_reports_real_bytes_and_handles_unknown_content_length(self):
        payload = b'fixture-data-' * 65536
        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                if self.path == '/known':
                    self.send_header('Content-Length', str(len(payload)))
                self.send_header('Connection', 'close')
                self.end_headers()
                for offset in range(0, len(payload), 65536):
                    self.wfile.write(payload[offset:offset + 65536])
                    self.wfile.flush()
                    time.sleep(.08)
            def log_message(self, *args):
                pass
        server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            for kind in ('known', 'unknown'):
                with self.subTest(kind=kind):
                    target = self.root / (kind + '.download')
                    command, env = self.command(". $env:ENVIRONMENT_LIBRARY\nStart-StartupProgress '测试下载'\nSet-StartupStage 2 '下载组件'\nRequest-SetupDownload $env:DOWNLOAD_URL $env:DOWNLOAD_TARGET\n",
                                                ENVIRONMENT_LIBRARY=str(ROOT / 'scripts/environment_setup.ps1'),
                                                DOWNLOAD_URL=f'http://127.0.0.1:{server.server_port}/{kind}', DOWNLOAD_TARGET=str(target))
                    result = subprocess.run(command, env=env, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=15)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    self.assertEqual(target.read_bytes(), payload)
                    self.assertIn('MB/s', result.stdout)
                    self.assertIn('下载完成', result.stdout)
                    if kind == 'unknown':
                        self.assertNotIn('%', result.stdout)
        finally:
            server.shutdown()
            server.server_close()
            worker.join(timeout=5)


if __name__ == '__main__':
    unittest.main()
