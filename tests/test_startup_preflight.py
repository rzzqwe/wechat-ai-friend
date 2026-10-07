"""Startup entry points stop before launch when the common preflight fails."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(os.name == 'nt' and shutil.which('powershell'), 'Windows startup integration')
class StartupPreflightTests(unittest.TestCase):
    def run_entry(self, entry, fail):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            scripts = root / 'scripts'
            scripts.mkdir()
            script = scripts / entry
            shutil.copyfile(ROOT / 'scripts' / entry, script)
            shutil.copyfile(ROOT / 'scripts/startup_progress.ps1', scripts / 'startup_progress.ps1')
            shutil.copyfile(ROOT / 'scripts/gateway_environment.ps1', scripts / 'gateway_environment.ps1')
            binary = root / 'bin'
            binary.mkdir()
            (binary / 'openclaw.cmd').write_text('@echo off\nexit /b 0\n')
            (root / 'wechat_bot_app.py').write_text("import os\nfrom pathlib import Path\nwith Path(os.environ['FIXTURE_CALLS']).open('a',encoding='utf-8') as f: f.write('python-launch\\n')\nprint('__WECHAT_WORKBENCH_READY__',flush=True)\n", encoding='utf-8')
            (root / 'fake_openclaw.py').write_text("import os,sys\nfrom pathlib import Path\nwith Path(os.environ['FIXTURE_CALLS']).open('a',encoding='utf-8') as f: f.write('openclaw '+' '.join(sys.argv[1:])+'\\n')\nprint('fixture gateway ready',flush=True)\n", encoding='utf-8')
            (scripts / 'environment_setup.ps1').write_text('''
. (Join-Path $PSScriptRoot 'startup_progress.ps1')
function Invoke-ProjectEnvironment {
    param([switch]$Python, [switch]$OpenClaw, [switch]$Weixin)
    Add-Content -LiteralPath $env:FIXTURE_CALLS -Value 'preflight' -Encoding UTF8
    if ($env:FIXTURE_FAIL -eq '1') { throw 'fixture preflight failed' }
    $env:PATH=(Join-Path $PSScriptRoot '../bin')+';'+$env:PATH
    $env:WECHAT_AI_NODE=$env:FIXTURE_PYTHON
    $env:WECHAT_AI_OPENCLAW_ENTRY=Join-Path $PSScriptRoot '../fake_openclaw.py'
    $script:ProjectRuntimeRequiresStopForce=$true
    function global:openclaw {
        Add-Content -LiteralPath $env:FIXTURE_CALLS -Value ('openclaw '+($args -join ' ')) -Encoding UTF8
        $global:LASTEXITCODE=0
    }
    return @{Python=$env:FIXTURE_PYTHON}
}
''', encoding='utf-8-sig')
            calls = root / 'calls.txt'
            calls.write_text('', encoding='utf-8')
            environment = dict(os.environ, FIXTURE_CALLS=str(calls), FIXTURE_FAIL='1' if fail else '0', FIXTURE_PYTHON=sys.executable)
            arguments = ['-AccountId', 'fixture-user', '-Action', 'start'] if entry == '切换微信账号.ps1' else []
            result = subprocess.run([shutil.which('powershell'), '-NoProfile', '-ExecutionPolicy', 'Bypass',
                                     '-File', str(script), *arguments], env=environment, capture_output=True,
                                    text=True, encoding='utf-8', errors='replace', timeout=15)
            self.assertEqual(result.returncode, 1 if fail else 0, result.stdout + result.stderr)
            lines = calls.read_text(encoding='utf-8-sig').splitlines()
            self.assertEqual(lines[0], 'preflight')
            if fail:
                self.assertEqual(lines, ['preflight'])
            else:
                if entry == 'setup_python.ps1':
                    self.assertIn('[完成]', result.stdout)
                else:
                    self.assertGreater(len(lines), 1)

    def test_dependency_installer_checks_environment_before_reporting_success(self):
        for fail in (True, False):
            with self.subTest(fail=fail):
                self.run_entry('setup_python.ps1', fail)

    def test_workbench_launch_checks_environment_before_starting_python(self):
        for fail in (True, False):
            with self.subTest(fail=fail):
                self.run_entry('start_workbench.ps1', fail)

    def test_standalone_gateway_checks_environment_before_starting(self):
        for fail in (True, False):
            with self.subTest(fail=fail):
                self.run_entry('start_openclaw.ps1', fail)

    def test_account_start_checks_environment_before_configuration_or_restart(self):
        for fail in (True, False):
            with self.subTest(fail=fail):
                self.run_entry('切换微信账号.ps1', fail)

    def test_gateway_reload_checks_environment_before_stopping_running_service(self):
        for fail in (True, False):
            with self.subTest(fail=fail):
                self.run_entry('重载微信网关.ps1', fail)


if __name__ == '__main__':
    unittest.main()
