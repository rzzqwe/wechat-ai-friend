// Offline integration check against the locally installed OpenClaw package.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { pathToFileURL } from 'node:url';

const fixturePath = process.argv[2];
const packageRoot = process.argv[3];
const fixture = JSON.parse(fs.readFileSync(fixturePath, 'utf8'));
process.env.OPENCLAW_STATE_DIR = path.dirname(fixture.configPath);
process.env.OPENCLAW_CONFIG_PATH = fixture.configPath;
const dist = path.join(packageRoot, 'dist');
async function exportedFunction(prefix, name) {
  const candidates = fs.readdirSync(dist, {recursive: true}).filter(file => /.m?js$/.test(file));
  candidates.sort((a, b) => Number(path.basename(b).startsWith(prefix)) - Number(path.basename(a).startsWith(prefix)));
  for (const file of candidates) {
    const source = fs.readFileSync(path.join(dist, file), 'utf8');
    const match = source.match(new RegExp(name + ' as ([a-zA-Z_$][a-zA-Z0-9_$]*)[, }]'));
    if (match) return (await import(pathToFileURL(path.join(dist, file)).href))[match[1]];
  }
  throw new Error('Local OpenClaw export not found: ' + name);
}
const validate = await exportedFunction('auth-profiles-', 'validateConfigObjectWithPlugins');
const validated = validate(fixture.config);
assert.equal(validated.ok, true, JSON.stringify(validated.issues));
const resolve = await exportedFunction('resolve-route-', 'resolveAgentRoute');
const a = resolve({cfg: fixture.config, channel: 'openclaw-weixin', accountId: 'wechat_a', peer: {kind: 'direct', id: 'same-peer'}});
const b = resolve({cfg: fixture.config, channel: 'openclaw-weixin', accountId: 'wechat_b', peer: {kind: 'direct', id: 'same-peer'}});
assert.equal(a.agentId, fixture.a.agent_id);
assert.equal(b.agentId, fixture.b.agent_id);
assert.notEqual(a.sessionKey, b.sessionKey);
if (!fixture.config.agents.entries) {
  const fallback = resolve({cfg: fixture.config, channel: 'telegram', accountId: 'unrelated', peer: {kind: 'direct', id: 'same-peer'}});
  assert.equal(fallback.agentId, 'main');
} else {
  assert.equal(fixture.config.agents.ownership, 'explicit');
  assert.ok(fixture.config.agents.entries.main);
}
console.log('OpenClaw native schema, two-account routing, separate sessions and default preservation: PASS');
