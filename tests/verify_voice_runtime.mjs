// Native OpenClaw TTS integration with synthetic audio, no API calls or messages.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import {pathToFileURL} from 'node:url';

const [configPath, dist, projectRoot] = process.argv.slice(2);
const cfg = JSON.parse(fs.readFileSync(configPath, 'utf8').replace(/^\uFEFF/, ''));
process.env.OPENCLAW_CONFIG_PATH = configPath;
const silent = {info() {}, warn() {}, error() {}, debug() {}};
const loader = await import(pathToFileURL(path.join(dist, 'loader-runtime-load-CMBCNIWa.mjs')).href);
const id = 'wechat-companion-voice';
const runtime = await import(pathToFileURL(path.join(dist, 'runtime-api-CD1xsoYF.mjs')).href);
const directory = path.join(projectRoot, 'data/diagnostics/voice-runtime-fixture');
fs.mkdirSync(directory, {recursive: true});
const agents = Object.entries(cfg.agents.entries).filter(([, agent]) => agent.tts?.provider === id);
assert.equal(agents.length, 2);
for (const [agentId, agent] of agents) agent.tts.prefsPath = path.join(directory, agentId + '.json');
const [a, b] = agents;
const prefs = (agent, auto) => fs.writeFileSync(agent.tts.prefsPath, JSON.stringify({tts: {
  auto, provider: id, persona: agent.tts.persona, maxLength: 500, summarize: false
}}));
prefs(a[1], 'tagged');
prefs(b[1], 'off');
const registry = loader.r({config: cfg, cache: true, activate: true, logger: silent});
const entry = registry.speechProviders.find(record => record.provider.id === id);
assert.ok(entry, 'Companion speech provider was not registered');
const sdk = await import(pathToFileURL(path.join(dist, 'plugin-sdk/speech-core.js')).href);
const provider = sdk.getSpeechProvider(id, cfg);
assert.ok(provider, 'Native speech lookup did not resolve the provider');
const synthesize = provider.synthesize;
let calls = [];
const recorded = Buffer.alloc(44 + 882);
recorded.write('RIFF', 0); recorded.writeUInt32LE(recorded.length - 8, 4);
recorded.write('WAVEfmt ', 8); recorded.writeUInt32LE(16, 16);
recorded.writeUInt16LE(1, 20); recorded.writeUInt16LE(1, 22);
recorded.writeUInt32LE(44100, 24); recorded.writeUInt32LE(88200, 28);
recorded.writeUInt16LE(2, 32); recorded.writeUInt16LE(16, 34);
recorded.write('data', 36); recorded.writeUInt32LE(recorded.length - 44, 40);
provider.synthesize = async request => {
  calls.push(request);
  return {audioBuffer: recorded, outputFormat: 'wav', fileExtension: '.wav', voiceCompatible: false};
};
const persist = async ({audioBuffer}) => {
  const file = path.join(directory, 'result.wav');
  fs.writeFileSync(file, audioBuffer);
  return file;
};
const apply = (agent, payload) => runtime.u({cfg, agentId: agent[0], channel: 'openclaw-weixin',
  kind: 'final', accountId: 'voice-fixture', payload}, persist);
try {
  const text = '今天怎么样呀？';
  const tagged = {text: text + '\n[[tts:text]]' + text + '[[/tts:text]]'};
  const plain = await apply(a, {text});
  assert.equal(plain.mediaUrl, undefined);
  assert.equal(calls.length, 0);
  const voiced = await apply(a, tagged);
  if (!voiced.mediaUrl) console.log(JSON.stringify({calls: calls.length, lastAttempt: runtime.c()}));
  assert.equal(voiced.text.trim(), text);
  assert.ok(voiced.mediaUrl);
  assert.equal(calls.length, 1);
  assert.equal(calls[0].providerConfig.personaId, a[1].tts.providers[id].personaId);
  assert.equal(calls[0].providerConfig.localId, a[1].tts.providers[id].localId);
  const other = await apply(b, tagged);
  assert.equal(other.mediaUrl, undefined);
  assert.equal(calls.length, 1);
  prefs(a[1], 'off');
  assert.equal((await apply(a, tagged)).mediaUrl, undefined);
  assert.equal(calls.length, 1);
  prefs(a[1], 'tagged');
  const sticker = await apply(a, {...tagged, mediaUrl: path.join(projectRoot, 'assets/stickers/okay.png')});
  assert.equal(sticker.mediaUrl, path.join(projectRoot, 'assets/stickers/okay.png'));
  assert.equal(calls.length, 1);
  provider.synthesize = async () => { throw new Error('Test synthesis failure'); };
  const failed = await apply(a, tagged);
  assert.equal(failed.text.trim(), text);
  assert.equal(failed.mediaUrl, undefined);
  const last = runtime.c();
  assert.ok(last.attempts.every(attempt => attempt.provider === id || attempt.personaBinding === 'missing' || attempt.outcome === 'skipped'));
  console.log(JSON.stringify({ok: true, nativeProviderRegistered: true, taggedVoice: true,
    plainText: true, perCompanionIsolation: true, liveOffSwitch: true, stickersPreserved: true,
    synthesisFailureKeepsText: true}));
} finally {
  provider.synthesize = synthesize;
}
