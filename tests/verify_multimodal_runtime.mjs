// Verify channel image and sticker contexts reach native vision.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { pathToFileURL, fileURLToPath } from 'node:url';
import {execFileSync} from 'node:child_process';

const root = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const runtime = JSON.parse(fs.readFileSync(path.join(root, 'data/runtime.json'), 'utf8'));
const dist = path.join(runtime.bin_dir, '..', 'openclaw/dist');
const state = process.env.OPENCLAW_STATE_DIR || path.join(process.env.USERPROFILE, '.openclaw');
const cfg = JSON.parse(fs.readFileSync(process.env.OPENCLAW_CONFIG_PATH || path.join(state, 'openclaw.json'), 'utf8'));
const [providerId, modelId] = cfg.agents.defaults.model.primary.split('/');
const model = cfg.models.providers[providerId].models.find(item => item.id === modelId);
model.input = ['text', 'image'];

async function exported(prefix, name) {
  for (const file of fs.readdirSync(dist).filter(file => file.startsWith(prefix) && file.endsWith('.mjs'))) {
    const source = fs.readFileSync(path.join(dist, file), 'utf8');
    const alias = source.match(new RegExp(name + ' as ([a-zA-Z_$][a-zA-Z0-9_$]*)[, }]'));
    if (alias) return (await import(pathToFileURL(path.join(dist, file)).href))[alias[1]];
  }
  throw new Error('Missing installed runtime export: ' + name);
}

const finalize = await exported('inbound-context-', 'finalizeInboundContext');
const runCapability = await exported('runner-', 'runCapability');
const normalize = await exported('runner.attachments-', 'normalizeMediaAttachments');
const pluginRoot = process.argv[2];
assert.ok(pluginRoot, 'Pass the installed WeChat plugin root');
const {weixinMessageToMsgContext} = await import(pathToFileURL(path.join(pluginRoot, 'dist/src/messaging/inbound.js')).href);

const tempRoot = path.resolve(state, 'tmp');
fs.mkdirSync(tempRoot, {recursive: true});
const agentDir = fs.mkdtempSync(path.join(tempRoot, 'wechat-vision-check-'));
const agentId = 'wechat-ai-vision-test';
cfg.agents.entries[agentId] = {agentDir, workspace: agentDir};
try {
  execFileSync(process.env.WECHAT_AI_PYTHON || 'python', ['-B', '-c', `from pathlib import Path; import sys; from PIL import Image; p=Path(sys.argv[1]); image=Image.new('RGB',(32,32),'blue'); [image.save(p / ('vision-test.'+ext)) for ext in ('png','gif','webp')]`, agentDir], {windowsHide: true});
  const {readPreparedModelCatalog} = await import(pathToFileURL(path.join(dist, 'prepared-model-catalog-BO4lvuYI.mjs')).href);
  const catalog = await readPreparedModelCatalog({config: cfg, agentId, agentDir, workspaceDir: agentDir});
  const selected = catalog.find(item => item.id === modelId && item.provider === providerId);
  assert.ok(selected?.input?.includes('image'), 'Prepared model catalog must advertise image input');
  for (const filename of ['vision-test.png', 'vision-test.gif', 'vision-test.webp', 'hug.png']) {
    const image = filename.startsWith('vision-test') ? path.join(agentDir, filename) : path.join(root, 'assets/stickers', filename);
    const raw = weixinMessageToMsgContext({from_user_id: 'synthetic-test', item_list: [{type: 2, image_item: {}}]},
      'synthetic-account', {decryptedPicPath: image});
    const ctx = finalize({...raw, SessionKey: 'agent:wechat-ai-test:openclaw-weixin:test'});
    const attachments = normalize(ctx);
    assert.equal(attachments.length, 1);
    const result = await runCapability({capability: 'image', cfg, ctx, media: attachments,
      activeModel: {provider: providerId, model: modelId}, agentId, agentDir, workspaceDir: agentDir, providerRegistry: new Map(),
      attachments: {getPath: async () => image}});
    if (result.decision.nativeVisionActive !== true) console.log(JSON.stringify(result.decision));
    assert.equal(result.decision.nativeVisionActive, true, filename);
    assert.equal(result.decision.attachmentDispositions[0].kind, 'handed-to-native-vision', filename);
  }
  console.log('PASS: real WeChat image/sticker contexts reach native vision');
} finally {
  if (!fs.realpathSync(agentDir).startsWith(fs.realpathSync(tempRoot) + path.sep)) throw new Error('Unexpected test directory');
  fs.rmSync(agentDir, {recursive: true, force: true});
}
