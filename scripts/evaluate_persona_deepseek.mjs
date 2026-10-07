import * as fs from 'node:fs/promises';
import { createHash } from 'node:crypto';
import { join } from 'node:path';

const NL = String.fromCharCode(10);
const hash = (text) => createHash('sha256').update(text).digest('hex');
const save = (path, value) => fs.writeFile(path, JSON.stringify(value, null, 2) + NL, { encoding: 'utf8', flag: 'wx' });

// All state is explicit. Importing this module never calls the API.
// The caller supplies the key in memory; authorization headers are never saved.
export async function runEvaluation({ directory, apiKey, concurrency = 3 }) {
  if (!apiKey) throw new Error('An API key is required in memory.');
  if (!Number.isInteger(concurrency) || concurrency < 1 || concurrency > 3) throw new Error('Concurrency must be 1 to 3.');
  const metadata = JSON.parse(await fs.readFile(join(directory, 'metadata.json'), 'utf8'));
  const cases = JSON.parse(await fs.readFile(join(directory, 'cases.json'), 'utf8'));
  const persona = await fs.readFile(join(directory, 'persona-used.md'), 'utf8');
  if (hash(persona) !== metadata.persona_sha256) throw new Error('Persona snapshot hash mismatch.');
  const system = persona + NL + NL + metadata.fixture;
  if (hash(system) !== metadata.system_sha256) throw new Error('System message hash mismatch.');
  if (metadata.endpoint !== 'https://api.deepseek.com/chat/completions') throw new Error('Unexpected API endpoint.');
  if (new Set(cases.map(c => c.id)).size !== cases.length) throw new Error('Duplicate case IDs.');
  const expected = cases.reduce((sum, item) => sum + item.turns.length, 0);
  for (const entry of await fs.readdir(directory)) {
    if (entry.endsWith('.request.json') || entry === 'summary.json') throw new Error('Run directory already contains calls; do not overwrite.');
  }
  async function runCase(testCase) {
    if (!/^[A-Z][0-9]{2}$/.test(testCase.id)) throw new Error('Invalid case ID.');
    const messages = [{ role: 'system', content: system }, ...(testCase.history || [])];
    const result = { ...testCase, results: [] };
    for (let index = 0; index < testCase.turns.length; index++) {
      const user = testCase.turns[index];
      messages.push({ role: 'user', content: user });
      const stem = join(directory, testCase.id + '-' + (index + 1));
      const body = { ...metadata.settings, messages };
      if (hash(body.messages[0].content) !== metadata.system_sha256) throw new Error('Wrong system message before send.');
      await save(stem + '.request.json', body);
      const started = Date.now();
      const response = await fetch(metadata.endpoint, {
        method: 'POST',
        headers: { Authorization: 'Bearer ' + apiKey, 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
        signal: AbortSignal.timeout(60000),
      });
      if (!response.ok) throw new Error('DeepSeek HTTP ' + response.status);
      const payload = await response.json();
      const choice = payload.choices?.[0];
      if (!choice?.message?.content) throw new Error('No final reply returned.');
      const record = {
        api_response_id: payload.id,
        api_model: payload.model,
        finish_reason: choice.finish_reason,
        reply: choice.message.content,
        usage: payload.usage,
        elapsed_seconds: (Date.now() - started) / 1000,
        request_system_sha256: metadata.system_sha256,
      };
      await save(stem + '.response.json', record);
      await fs.writeFile(stem + '.txt', record.reply, { encoding: 'utf8', flag: 'wx' });
      result.results.push({ turn: index + 1, user, ...record });
      messages.push({ role: 'assistant', content: record.reply });
    }
    await save(join(directory, testCase.id + '.json'), result);
    return result;
  }
  const results = [];
  const failures = [];
  for (let offset = 0; offset < cases.length; offset += concurrency) {
    const group = cases.slice(offset, offset + concurrency);
    const settled = await Promise.allSettled(group.map(runCase));
    for (let index = 0; index < settled.length; index++) {
      const item = settled[index];
      if (item.status === 'fulfilled') results.push(item.value);
      else failures.push({ id: group[index].id, error: String(item.reason?.message || item.reason).split(apiKey).join('[REDACTED]') });
    }
  }
  const records = results.flatMap(c => c.results);
  const summary = {
    metadata,
    expected_calls: expected,
    completed_case_calls: records.length,
    completed_cases: results.length,
    failures,
    usage: {
      prompt_tokens: records.reduce((n, r) => n + (r.usage?.prompt_tokens || 0), 0),
      completion_tokens: records.reduce((n, r) => n + (r.usage?.completion_tokens || 0), 0),
    },
    cases: results,
  };
  await save(join(directory, 'summary.json'), summary);
  return summary;
}
