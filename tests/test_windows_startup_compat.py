import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from scripts.apply_openclaw_windows_compat import patch_windows_entry


class WindowsStartupCompatibilityTests(unittest.TestCase):
    def test_unrecognized_source_is_not_modified(self):
        with self.assertRaises(ValueError):patch_windows_entry('unknown implementation','task-supervisor.mjs')

    def test_patch_is_idempotent_and_keeps_normal_cli_dispatch(self):
        source='import process from "node:process";\nasync function loadLegacyCliDeps() {\nconst {runCli}=await import("./cli/run-main.js");return {runCli};\n}\n'
        updated=patch_windows_entry(source,'task-supervisor-fixture.mjs')
        self.assertIn('import("./cli/run-main.js")',updated)
        self.assertEqual(patch_windows_entry(updated,'task-supervisor-fixture.mjs'),updated)
        self.assertIn('!process.argv.some(value => value.startsWith("--task-supervisor-child"))',updated)

    @unittest.skipUnless(os.name=='nt','Windows managed-service dispatcher')
    def test_only_owned_supervisor_skips_cli_child_and_other_processes_keep_validation(self):
        candidates=[Path.home()/'.openclaw/wechat-runtime/node-v24.16.0-win-x64/node.exe',Path(shutil.which('node') or 'missing')]
        node=next((file for file in candidates if file.is_file()),None)
        if node is None:self.skipTest('Node unavailable')
        with tempfile.TemporaryDirectory() as temporary:
            folder=Path(temporary);(folder/'cli').mkdir()
            (folder/'package.json').write_text('{"type":"module"}')
            (folder/'cli/run-main.js').write_text('export async function runCli(){console.log("NORMAL_CLI")}')
            (folder/'task-supervisor-fixture.mjs').write_text('export async function runWindowsGatewayTaskSupervisor(){console.log("SUPERVISOR_ONLY")}')
            source='import process from "node:process";\nasync function loadLegacyCliDeps() {\nconst {runCli}=await import("./cli/run-main.js");return {runCli};\n}\nconst {runCli}=await loadLegacyCliDeps();await runCli();\n'
            entry=folder/'index.js';entry.write_text(patch_windows_entry(source,'task-supervisor-fixture.mjs'),encoding='utf-8')
            for owned,flag,expected in [(True,'--task-supervisor','SUPERVISOR_ONLY'),(True,'--task-supervisor-child=1','NORMAL_CLI'),(False,'--task-supervisor','NORMAL_CLI')]:
                env=dict(os.environ,OPENCLAW_SERVICE_MARKER='openclaw' if owned else 'other',OPENCLAW_SERVICE_KIND='gateway',OPENCLAW_STATE_DIR=str(folder))
                result=subprocess.run([str(node),str(entry),flag],env=env,capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=15)
                self.assertEqual(result.returncode,0,result.stdout+result.stderr)
                self.assertIn(expected,result.stdout)
