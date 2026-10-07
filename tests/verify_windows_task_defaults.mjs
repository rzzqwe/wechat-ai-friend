import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import {pathToFileURL} from 'node:url';
const dist = path.join(process.argv[2], 'dist');
const find = prefix => pathToFileURL(path.join(dist, fs.readdirSync(dist).find(n => n.startsWith(prefix) && n.endsWith('.mjs')))).href;
const {t: audit} = await import(find('service-audit-schtasks-'));
const {n: build, f: user} = await import(find('schtasks-layout-'));
const xml = build({taskDescription: 'OpenClaw Gateway', taskUser: user(process.env), launchPath: path.join(process.env.USERPROFILE, '.openclaw/gateway.vbs')});
const findings = [];
await audit(process.env, findings, 10000, undefined, xml);
assert.deepEqual(findings.filter(f => ['outdated', 'unknown-edit'].includes(f.kind)), []);
for (const [key, before, after] of [
    ['Settings.Priority', '<Priority>7</Priority>', '<Priority>8</Priority>'],
    ['Triggers.LogonTrigger.Enabled', '<Enabled>true</Enabled>', '<Enabled>false</Enabled>']
]) {
    const changed = [];
    await audit(process.env, changed, 10000, undefined, xml.replace(before, after));
    assert.ok(changed.some(f => f.key === key && f.kind === 'outdated'), 'Changed setting must still fail: ' + key);
}
console.log('Windows task defaults: real definition accepted; changed priority and logon settings rejected. PASS');
