"""Fast checks use registered ownership and hashes, otherwise defer to the CLI."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which('node'), 'Node is required')
class FastWeixinStatusTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name)
        self.host=self.root/'host'
        (self.host/'dist').mkdir(parents=True)
        (self.host/'package.json').write_text(json.dumps({'name':'openclaw','version':'2026.9.6'}))
        (self.host/'dist/installed-plugin-index-store-fixture.mjs').write_text('''
import fs from 'node:fs';
function readPersistedInstalledPluginIndexSync(options={}) {
  if (options.artifactPreservingReadOnly!==true) throw Error('read-only required');
  return JSON.parse(fs.readFileSync(process.env.WEIXIN_INDEX_FIXTURE,'utf8'));
}
export {readPersistedInstalledPluginIndexSync as r};
''')
        self.plugin=self.root/'selected-generation/plugin'
        (self.plugin/'dist').mkdir(parents=True)
        self.package=self.plugin/'package.json'
        self.package.write_text(json.dumps({'name':'@tencent-weixin/openclaw-weixin','version':'2.4.9','dependencies':{}}))
        self.manifest=self.plugin/'openclaw.plugin.json'
        self.manifest.write_text('{"id":"openclaw-weixin"}')
        (self.plugin/'dist/index.js').write_text('// fixture')
        self.index=self.root/'index.json'
        self.record={'pluginId':'openclaw-weixin','packageVersion':'2.4.9','rootDir':str(self.plugin),
                     'source':'dist/index.js','manifestPath':'openclaw.plugin.json','manifestHash':self.hash(self.manifest),
                     'packageJson':{'path':'package.json','hash':self.hash(self.package)},'enabled':True}

    def hash(self, path):
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def run_check(self, *, rows=None, diagnostics=None):
        self.index.write_text(json.dumps({'plugins':rows if rows is not None else [self.record], 'diagnostics':diagnostics or []}))
        result=subprocess.run([shutil.which('node'), str(ROOT/'scripts/weixin_status_fast.mjs'), str(self.host)],
                              env=dict(os.environ, WEIXIN_INDEX_FIXTURE=str(self.index)), capture_output=True,
                              text=True, encoding='utf-8', errors='replace', timeout=10)
        self.assertEqual(result.returncode,0,result.stderr)
        return json.loads(result.stdout)

    def test_registered_unchanged_files_are_accepted(self):
        result=self.run_check()
        self.assertTrue(result['ok'])
        self.assertEqual(result['plugin']['rootDir'],str(self.plugin))

    def test_changed_package_or_manifest_falls_back_without_installing(self):
        self.package.write_text(self.package.read_text()+' ')
        self.assertFalse(self.run_check()['ok'])
        self.record['packageJson']['hash']=self.hash(self.package)
        self.manifest.write_text('{"id":"another-plugin"}')
        self.assertFalse(self.run_check()['ok'])

    def test_missing_runtime_entry_falls_back(self):
        (self.plugin/'dist/index.js').unlink()
        self.assertTrue(self.run_check()['fallback'])

    def test_ambiguous_ownership_or_duplicate_index_rows_falls_back(self):
        self.record['installOwnerAmbiguous']=True
        self.assertTrue(self.run_check()['fallback'])
        self.record.pop('installOwnerAmbiguous')
        self.assertTrue(self.run_check(rows=[self.record,self.record])['fallback'])

    def test_runtime_errors_and_unverified_host_versions_fall_back(self):
        self.assertTrue(self.run_check(diagnostics=[{'pluginId':'openclaw-weixin','level':'error'}])['fallback'])
        (self.host/'package.json').write_text('{"name":"openclaw","version":"2026.10.1"}')
        self.assertTrue(self.run_check()['fallback'])

    def test_missing_required_dependency_falls_back(self):
        self.package.write_text(json.dumps({'name':'@tencent-weixin/openclaw-weixin','version':'2.4.9',
                                           'dependencies':{'fixture-missing-dependency':'1.0.0'}}))
        self.record['packageJson']['hash']=self.hash(self.package)
        self.assertTrue(self.run_check()['fallback'])

    def test_paths_outside_the_registered_plugin_are_rejected(self):
        self.record['source']='../other/index.js'
        self.assertTrue(self.run_check()['fallback'])


if __name__=='__main__':
    unittest.main()
