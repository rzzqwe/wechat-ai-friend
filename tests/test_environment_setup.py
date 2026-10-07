"""Real startup installer logic with isolated files and deterministic downloads."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(os.name == 'nt' and shutil.which('powershell'), 'Windows startup integration')
class EnvironmentSetupTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        payloads = self.root / 'payloads'
        payloads.mkdir()
        with zipfile.ZipFile(payloads / 'python-3.13.16-amd64.zip', 'w') as archive:
            archive.writestr('python.exe', 'fixture executable')
        with zipfile.ZipFile(payloads / 'node-v24.16.0-win-x64.zip', 'w') as archive:
            archive.writestr('node-v24.16.0-win-x64/node.exe', 'fixture executable')
            archive.writestr('node-v24.16.0-win-x64/npm.cmd', 'fixture npm')
            archive.writestr('node-v24.16.0-win-x64/node_modules/npm/bin/npm-cli.js', '// fixture npm')

    def command(self, case, project='project'):
        return [shutil.which('powershell'), '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
                str(ROOT / 'tests/fixtures/environment_harness.ps1'),
                '-Library', str(ROOT / 'scripts/environment_setup.ps1'), '-Root', str(self.root),
                '-Case', case, '-ProjectName', project]

    def result(self, process):
        self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
        return json.loads(process.stdout.strip().splitlines()[-1])

    def run_case(self, case):
        process = subprocess.run(self.command(case), capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=40)
        return self.result(process)

    def assert_complete_downloaded_once(self, result, *, python_attempts=1, npm_attempts=1):
        self.assertEqual(result['calls'].count('download python-3.13.16-amd64.zip'), python_attempts)
        self.assertEqual(result['calls'].count('download node-v24.16.0-win-x64.zip'), 1)
        self.assertEqual(result['calls'].count('npm-install'), npm_attempts)
        self.assertEqual(result['calls'].count('plugin-install'), 1)

    def test_second_start_reuses_all_components_without_install_or_download(self):
        result = self.run_case('twice')
        self.assert_complete_downloaded_once(result)
        self.assertEqual(result['calls'].count('venv'), 1)
        self.assertEqual(result['calls'].count('pip-install'), 1)
        self.assertTrue(Path(result['python']).is_file())
        self.assertTrue(Path(result['node']).is_file())

    def test_lost_path_metadata_is_rebuilt_from_installed_files(self):
        result = self.run_case('metadata-lost')
        self.assert_complete_downloaded_once(result)
        for name in ('runtime.json', 'python-runtime.json'):
            json.loads((self.root / 'project/data' / name).read_text(encoding='utf-8-sig'))

    def test_missing_node_executable_is_restored_from_archive_without_downloading_again(self):
        result = self.run_case('cache-repair')
        self.assert_complete_downloaded_once(result)
        self.assertTrue(Path(result['node']).is_file())

    def test_interrupted_download_does_not_become_a_completed_archive(self):
        result = self.run_case('download-failure')
        self.assertEqual(result['failures'], 1)
        self.assert_complete_downloaded_once(result, python_attempts=2)
        self.assertFalse(list((self.root / 'cache/downloads').glob('*.partial')))

    def test_checksum_failure_is_retried_before_any_python_install(self):
        result = self.run_case('hash-failure')
        self.assertEqual(result['failures'], 1)
        self.assert_complete_downloaded_once(result, python_attempts=2)
        self.assertEqual(result['calls'].count('venv'), 1)

    def test_partial_openclaw_install_reuses_node_and_python_downloads_on_retry(self):
        result = self.run_case('npm-failure')
        self.assertEqual(result['failures'], 1)
        self.assert_complete_downloaded_once(result, npm_attempts=2)

    def test_temporary_version_probe_error_does_not_trigger_reinstallation(self):
        result = self.run_case('openclaw-probe-error')
        self.assertEqual(result['failures'], 1)
        self.assert_complete_downloaded_once(result)

    def test_plugin_registry_error_does_not_trigger_reinstallation(self):
        result = self.run_case('plugin-query-error')
        self.assertEqual(result['failures'], 1)
        self.assert_complete_downloaded_once(result)

    def test_node_version_in_preamble_does_not_hide_installed_openclaw_version(self):
        result = self.run_case('version-preamble')
        self.assert_complete_downloaded_once(result)

    def test_unreadable_installed_version_does_not_trigger_duplicate_download(self):
        result = self.run_case('version-unreadable')
        self.assertEqual(result['failures'], 1)
        self.assert_complete_downloaded_once(result)

    def test_concurrent_projects_share_one_download_and_installation(self):
        processes = [subprocess.Popen(self.command('concurrent', name), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                      text=True, encoding='utf-8', errors='replace') for name in ('first-project', 'second-project')]
        try:
            for process in processes:
                output, errors = process.communicate(timeout=40)
                self.assertEqual(process.returncode, 0, output + errors)
            calls = (self.root / 'calls.txt').read_text(encoding='utf-8-sig').splitlines()
            self.assert_complete_downloaded_once({'calls': calls})
            self.assertEqual(calls.count('venv'), 2)
        finally:
            for process in processes:
                if process.poll() is None:
                    process.kill()
                    process.wait()
                if process.stdout:
                    process.stdout.close()
                if process.stderr:
                    process.stderr.close()

    def test_legacy_packaged_app_cache_migrates_without_downloading_completed_archives(self):
        result = self.run_case('legacy-migration')
        self.assertFalse(any(call.startswith('download ') for call in result['calls']))
        self.assertEqual(result['calls'].count('npm-install'), 1)
        self.assertEqual(result['calls'].count('plugin-install'), 1)
        self.assertTrue((self.root / 'cache/legacy-cache-migrated.json').is_file())


if __name__ == '__main__':
    unittest.main()
