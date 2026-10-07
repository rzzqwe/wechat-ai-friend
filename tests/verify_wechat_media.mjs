// Offline sticker directive integration; no WeChat sends.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { pathToFileURL, fileURLToPath } from 'node:url';

const root = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const runtime = JSON.parse(fs.readFileSync(path.join(root, 'data/runtime.json'), 'utf8'));
const dist = path.join(runtime.bin_dir, '..', 'openclaw/dist');

async function exported(prefix, name) {
  const files = fs.readdirSync(dist).filter(file => file.startsWith(prefix) && file.endsWith('.mjs'));
  for (const file of files) {
    const source = fs.readFileSync(path.join(dist, file), 'utf8');
    const alias = source.match(new RegExp(name + ' as ([a-zA-Z_$][a-zA-Z0-9_$]*)[, }]'));
    if (alias) return (await import(pathToFileURL(path.join(dist, file)).href))[alias[1]];
  }
  throw new Error('Installed export not found: ' + name);
}

const parse = await exported('reply-directives-', 'parseReplyDirectives');
const sticker = 'C:/wechat-fixture/stickers/aaaaaaaaaaaaaaaaaaaaaaaa.png';
const parsed = parse('开心呀\nMEDIA:' + sticker);
assert.equal(parsed.text.trim(), '开心呀');
assert.deepEqual(parsed.mediaUrls, [sticker]);
const imageOnly = parse('MEDIA:' + sticker);
assert.equal(imageOnly.text.trim(), '');
assert.deepEqual(imageOnly.mediaUrls, [sticker]);

console.log('PASS: Windows sticker directives support image-only and text-plus-image replies');
