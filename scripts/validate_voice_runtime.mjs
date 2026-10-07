import fs from 'node:fs';
import path from 'node:path';
import {pathToFileURL} from 'node:url';

const [configPath, dist] = process.argv.slice(2);
process.env.OPENCLAW_CONFIG_PATH = configPath;
const config = JSON.parse(fs.readFileSync(configPath, 'utf8').replace(/^\uFEFF/, ''));
const file = path.join(dist, 'io.snapshot-preparation-B6fMdY05.mjs');
const source = fs.readFileSync(file, 'utf8');
const match = source.match(/validateConfigObjectWithPlugins as ([\w$]+)/);
if (!match) throw new Error('Installed config validator export was not found.');
const validate = (await import(pathToFileURL(file).href))[match[1]];
const result = validate(config);
console.log(JSON.stringify({ok: result.ok, issues: result.issues?.map(issue => ({path: issue.path, message: issue.message}))}));
if (!result.ok) process.exitCode = 1;
