"""Portable runtime selection in isolated Windows PowerShell processes."""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(os.name == 'nt' and shutil.which('powershell'), 'Windows PowerShell integration')
class ProjectRuntimeSelectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        candidates = [Path(shutil.which('node') or 'missing-node')]
        state = Path(os.environ.get('USERPROFILE', '')) / '.openclaw/wechat-runtime'
        candidates += list(state.glob('node-v*-win-*/node.exe'))
        cls.node = None
        for candidate in candidates:
            if not candidate.is_file():
                continue
            result = subprocess.run([str(candidate), '--version'], capture_output=True, text=True, timeout=5)
            match = re.fullmatch(r'v(\d+)\.(\d+)\.(\d+)\s*', result.stdout)
            if result.returncode or not match:
                continue
            version = tuple(map(int, match.groups()))
            if (version[0] == 24 and version >= (24, 16, 0)) or version >= (26, 1, 0):
                cls.node = candidate
                cls.version = '.'.join(match.groups())
                break
        if cls.node is None:
            raise unittest.SkipTest('A compatible Node executable is needed for native selection tests')

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.helper = self.root / 'project/scripts/use_project_runtime.ps1'
        self.helper.parent.mkdir(parents=True)
        shutil.copyfile(ROOT / 'scripts/use_project_runtime.ps1', self.helper)
        self.state = self.root / 'state'
        managed = self.state / 'wechat-runtime'
        self.node_dir = managed / ('node-v' + self.version + '-win-x64')
        self.node_dir.mkdir(parents=True)
        try:
            os.link(self.node, self.node_dir / 'node.exe')
        except OSError:
            shutil.copyfile(self.node, self.node_dir / 'node.exe')
        self.bin_dir = managed / 'openclaw-2026.9.6/node_modules/.bin'
        self.bin_dir.mkdir(parents=True)
        (self.bin_dir / 'openclaw.cmd').write_text('@echo off\nexit /b 0\n')
        self.package = managed / 'openclaw-2026.9.6/node_modules/openclaw/package.json'
        self.package.parent.mkdir(parents=True)
        self.package.write_text(json.dumps({'name': 'openclaw', 'version': '2026.9.6'}))
        self.manifest = self.root / 'project/data/runtime.json'

    def run_helper(self):
        probe = self.root / 'probe.ps1'
        probe.write_text('''param([string]$Helper)
$ErrorActionPreference = 'Stop'
. $Helper
$manifest = Join-Path (Split-Path (Split-Path $Helper -Parent) -Parent) 'data/runtime.json'
@{manifestExists=(Test-Path -LiteralPath $manifest); node=(Get-Command node.exe).Source} | ConvertTo-Json -Compress
''', encoding='utf-8-sig')
        environment = dict(os.environ, OPENCLAW_STATE_DIR=str(self.state))
        result = subprocess.run(
            [shutil.which('powershell'), '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(probe), str(self.helper)],
            env=environment, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return json.loads(result.stdout.strip().splitlines()[-1])

    def test_reuses_compatible_managed_runtime_and_records_exact_paths(self):
        report = self.run_helper()
        self.assertTrue(report['manifestExists'])
        saved = json.loads(self.manifest.read_text(encoding='utf-8-sig'))
        self.assertTrue(saved['enabled'])
        self.assertEqual(Path(saved['node_dir']), self.node_dir)
        self.assertEqual(Path(saved['bin_dir']), self.bin_dir)
        self.assertEqual(saved['node_version'], self.version)
        self.assertEqual(Path(report['node']), self.node_dir / 'node.exe')
        before = self.manifest.read_bytes()
        self.run_helper()
        self.assertEqual(self.manifest.read_bytes(), before)

    def test_explicit_disabled_manifest_is_not_enabled_by_discovery(self):
        self.manifest.parent.mkdir()
        self.manifest.write_text('{"enabled": false}', encoding='utf-8')
        before = self.manifest.read_bytes()
        self.run_helper()
        self.assertEqual(self.manifest.read_bytes(), before)

    def test_explicit_runtime_manifest_keeps_user_settings(self):
        self.manifest.parent.mkdir()
        self.manifest.write_text(json.dumps({'enabled': True, 'node_dir': str(self.node_dir),
                                             'bin_dir': str(self.bin_dir), 'openclaw_version': '2026.9.6',
                                             'reason': 'User-selected runtime'}), encoding='utf-8')
        before = self.manifest.read_bytes()
        report = self.run_helper()
        self.assertEqual(Path(report['node']), self.node_dir / 'node.exe')
        self.assertEqual(self.manifest.read_bytes(), before)

    def test_unverified_openclaw_package_is_not_selected(self):
        self.package.write_text(json.dumps({'name': 'another-package', 'version': '2026.9.6'}))
        report = self.run_helper()
        self.assertFalse(report['manifestExists'])

    def test_missing_managed_runtime_leaves_global_installation_available(self):
        self.state = self.root / 'empty-state'
        report = self.run_helper()
        self.assertFalse(report['manifestExists'])


@unittest.skipUnless(os.name == 'nt' and shutil.which('powershell'), 'Windows PowerShell integration')
class NodeUpgradeFailureTests(unittest.TestCase):
    def test_failed_package_manager_is_reported_before_openclaw_install_or_qr(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            script = folder / 'login.ps1'
            shutil.copyfile(ROOT / 'scripts/启动微信ClawBot.ps1', script)
            shutil.copyfile(ROOT / 'scripts/weixin_environment.ps1', folder / 'weixin_environment.ps1')
            (folder / 'route_wechat_account.py').write_text('print("fixture route ready")', encoding='utf-8')
            result_path = folder / 'result.json'
            calls = folder / 'calls.txt'
            calls.write_text('', encoding='utf-8')
            environment = dict(os.environ, LOGIN_CASE='node_24_stale', LOGIN_CALLS=str(calls),
                               OPENCLAW_STATE_DIR=str(folder / 'state'), WECHAT_AI_PYTHON=sys.executable,
                               WECHAT_AI_API_KEY='fixture-key', WECHAT_AI_BASE_URL='https://example.invalid',
                               WECHAT_AI_MODEL='fixture-model')
            result = subprocess.run(
                [shutil.which('powershell'), '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
                 str(ROOT / 'tests/fixtures/login_harness.ps1'), '-ScriptPath', str(script), '-ResultPath', str(result_path)],
                env=environment, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=15)
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            saved = json.loads(result_path.read_text(encoding='utf-8-sig'))
            self.assertEqual(saved['status'], 'failed')
            self.assertIn('Node.js 自动更新失败', saved['message'])
            self.assertIn('winget 退出码', saved['message'])
            self.assertNotIn('channels login', calls.read_text(encoding='utf-8-sig'))
            self.assertNotIn('plugins install', calls.read_text(encoding='utf-8-sig'))


if __name__ == '__main__':
    unittest.main()
