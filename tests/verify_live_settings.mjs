// Installed bootstrap cache + Python workspace updater, without model calls or WeChat sends.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import {execFileSync} from 'node:child_process';
import {fileURLToPath, pathToFileURL} from 'node:url';

const root = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const runtime = JSON.parse(fs.readFileSync(path.join(root, 'data/runtime.json'), 'utf8'));
const dist = path.resolve(runtime.bin_dir, '..', 'openclaw/dist');
const filename = fs.readdirSync(dist).find(name => name.startsWith('bootstrap-cache-') && name.endsWith('.mjs'));
const {i: load} = await import(pathToFileURL(path.join(dist, filename)).href);
const parent = path.join(root, 'data/diagnostics');
const temp = fs.mkdtempSync(path.join(parent, 'live-settings-check-'));
const python = code => JSON.parse(execFileSync('python', ['-B', '-X', 'utf8', '-c', code, temp],
  {cwd: root, encoding: 'utf8', windowsHide: true}));
try {
  const setup = python(`
from pathlib import Path
import sys,json
from PIL import Image
from account_personas import PersonaStore
from companion_media import MediaStore
folder=Path(sys.argv[1])
store=PersonaStore(folder/'project',folder/'state')
p=store.save('test','# test\\nold setting')
image=folder/'sample.png'
Image.new('RGB',(20,20),'blue').save(image)
MediaStore(store).import_stickers(p['id'],[image],{'sample.png':'old label'})
store.assign('synthetic',p['id'])
(folder/'persona-id.txt').write_text(p['id'])
workspace=store.workspace('synthetic',p['id'])
(workspace/'USER.md').write_text('user memory')
print(json.dumps({'workspace':str(workspace)}))
`);
  const params = {workspaceDir: setup.workspace, sessionKey: 'live-settings-check'};
  const first = await load(params);
  assert.ok(first.find(file => file.name === 'SOUL.md').content.includes('old setting'));
  const updated = python(`
from pathlib import Path
import sys,json
from account_personas import PersonaStore
from companion_media import MediaStore
folder=Path(sys.argv[1])
store=PersonaStore(folder/'project',folder/'state')
pid=(folder/'persona-id.txt').read_text()
before=store.config.read_bytes()
store.profile_path(pid).write_text('# test\\nnew setting')
library=MediaStore(store)
record=library.load(pid)['stickers'][0]
library.rename_sticker(pid,record['id'],'new label')
store.sync_workspace_settings(pid)
assert store.config.read_bytes()==before
assert (store.workspace('synthetic',pid)/'USER.md').read_text()=='user memory'
print(json.dumps({'config_unchanged':True,'memory_unchanged':True}))
`);
  const second = await load(params);
  assert.notEqual(first, second);
  assert.ok(second.find(file => file.name === 'SOUL.md').content.includes('new setting'));
  assert.ok(second.find(file => file.name === 'AGENTS.md').content.includes('new label'));
  assert.ok(updated.config_unchanged && updated.memory_unchanged);
  console.log('PASS: same-session native bootstrap reads new persona and sticker labels; config and memory unchanged.');
} finally {
  if (!fs.realpathSync(temp).startsWith(fs.realpathSync(parent) + path.sep)) throw Error('Unexpected test path');
  fs.rmSync(temp, {recursive: true, force: true});
}
