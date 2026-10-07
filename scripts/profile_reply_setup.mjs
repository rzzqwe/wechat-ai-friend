// Read-only latency probe: no model requests, chat messages, or credential output.
import fs from 'node:fs';
import path from 'node:path';
import { pathToFileURL } from 'node:url';

const [packageRoot, configPath, agentDir] = process.argv.slice(2);
const dist = path.join(packageRoot, 'dist');
const file = fs.readdirSync(dist).find(name => name.startsWith('model-auth-') &&
    name.endsWith('.js') && fs.readFileSync(path.join(dist, name), 'utf8').includes('async function getApiKeyForModel('));
if (!file) throw new Error('Installed OpenClaw model authentication module was not found.');
const source = fs.readFileSync(path.join(dist, file), 'utf8');
const exported = source.match(/getApiKeyForModel as ([A-Za-z_$][A-Za-z0-9_$]*)/);
if (!exported) throw new Error('Installed OpenClaw authentication export was not found.');
const resolve = (await import(pathToFileURL(path.join(dist, file)).href))[exported[1]];
const original = JSON.parse(fs.readFileSync(configPath, 'utf8').replace(/^﻿/, ''));
const selected = original.agents.defaults.model;
const primary = typeof selected === 'string' ? selected : selected.primary;
const slash = primary.indexOf('/');
const provider = primary.slice(0, slash);
const id = primary.slice(slash + 1);
for (const explicitAuth of [false, true]) {
    const cfg = structuredClone(original);
    if (explicitAuth) cfg.models.providers[provider].auth = 'api-key';
    else delete cfg.models.providers[provider].auth;
    for (let attempt = 1; attempt <= 2; attempt += 1) {
        const start = performance.now();
        const result = await resolve({model: {id, provider}, cfg, agentDir});
        console.log(JSON.stringify({explicitAuth, attempt, durationMs: Math.round(performance.now() - start), resolved: Boolean(result?.apiKey)}));
    }
}
