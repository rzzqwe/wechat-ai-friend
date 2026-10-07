// Read-only local Gateway status. Never print credentials or raw backend errors.
import fs from 'node:fs';
import path from 'node:path';
import {pathToFileURL} from 'node:url';

let client, timer, finished = false;
function finish(value) {
  if (finished) return;
  finished = true;
  clearTimeout(timer);
  process.stdout.write(JSON.stringify(value) + '\n');
  client?.stop();
  setTimeout(() => process.exit(0), 30);
}
function secret(value) {
  if (typeof value === 'string') {
    const env = value.match(/^\$\{([A-Za-z_][A-Za-z0-9_]*)\}$/);
    return env ? process.env[env[1]] : value;
  }
  if (value?.source === 'env' && typeof value.id === 'string') return process.env[value.id];
  return undefined;
}
function errorKind(value) {
  if (!value) return null;
  const text = String(value).toLowerCase();
  if (/deepseek|provider|model|api.?key|insufficient.*credit/.test(text)) return 'model';
  if (/40014|login|session.*expir|token.*expir|登录.*失效|重新扫码/.test(text)) return 'login';
  if (/network|timeout|fetch|connect|socket|网络|超时/.test(text)) return 'network';
  return 'runtime';
}
const bool = value => typeof value === 'boolean' ? value : null;
const stamp = value => typeof value === 'number' && Number.isFinite(value) ? value : null;

try {
  const root = path.resolve(process.argv[2]);
  const configPath = path.resolve(process.argv[3]);
  const config = JSON.parse(fs.readFileSync(configPath, 'utf8').replace(/^\uFEFF/, ''));
  const manifest = path.join(root, 'data/runtime.json');
  let runtime = fs.existsSync(manifest) ? JSON.parse(fs.readFileSync(manifest, 'utf8').replace(/^\uFEFF/, '')) : null;
  let dist;
  if (runtime?.enabled === true) {
    dist = path.resolve(runtime.bin_dir, '..', 'openclaw/dist');
  } else {
    const shim = process.env.WECHAT_AI_OPENCLAW_COMMAND;
    if (typeof shim !== 'string' || !path.isAbsolute(shim)) throw Error('runtime unavailable');
    const base = path.dirname(shim);
    const candidates = [path.join(base, 'node_modules/openclaw'), path.resolve(base, '../openclaw')]
      .filter(directory => fs.existsSync(path.join(directory, 'package.json')))
      .map(directory => ({directory, metadata: JSON.parse(fs.readFileSync(path.join(directory, 'package.json'), 'utf8'))}))
      .filter(item => item.metadata.name === 'openclaw');
    if (candidates.length !== 1) throw Error('ambiguous runtime');
    runtime = {openclaw_version: candidates[0].metadata.version};
    dist = path.join(candidates[0].directory, 'dist');
  }
  const port = config.gateway?.port ?? 18789;
  if (!Number.isInteger(port) || port < 1 || port > 65535) throw Error('invalid port');
  const auth = config.gateway?.auth ?? {};
  const token = secret(auth.token), password = secret(auth.password);
  if (auth.mode === 'token' && !token || auth.mode === 'password' && !password) {
    finish({ok: false, error: 'auth_unavailable'});
  } else {
    const filename = fs.readdirSync(dist).find(name => name.startsWith('client-') && name.endsWith('.mjs') &&
      fs.readFileSync(path.join(dist, name), 'utf8').includes('GatewayClient as'));
    if (!filename) throw Error('client unavailable');
    const source = fs.readFileSync(path.join(dist, filename), 'utf8');
    const alias = source.match(/GatewayClient as ([\w$]+)/)?.[1];
    const Client = (await import(pathToFileURL(path.join(dist, filename)).href))[alias];
    client = new Client({
      url: `${config.gateway?.tls?.enabled ? 'wss' : 'ws'}://127.0.0.1:${port}`,
      token, password, deviceIdentity: null,
      clientName: 'cli', clientVersion: runtime.openclaw_version, mode: 'cli',
      role: 'operator', scopes: process.argv[4]?.startsWith('--weixin-') ? ['operator.admin'] : ['operator.read'],
      requestTimeoutMs: process.argv[4]?.startsWith('--weixin-') ? 12000 : 3500, connectChallengeTimeoutMs: 3000,
      hostDeps: {logDebug: () => {}, logError: () => {}},
      onHelloOk: async () => {
        try {
          if (process.argv[4] === '--weixin-remove-stop') {
            const accountId=process.argv[5];
            if (!/^[a-z0-9_-]{1,64}$/.test(accountId || '')) throw Error('invalid account');
            await client.request('channels.stop',{channel:'openclaw-weixin',accountId});
            const body=await client.request('channels.status',{probe:false,timeoutMs:1000});
            const rows=body.channelAccounts?.['openclaw-weixin'];
            const row=Array.isArray(rows)?rows.find(value=>value.accountId===accountId):null;
            return finish({ok:Array.isArray(rows) && (!row || row.running!==true && row.restartPending!==true)});
          }
          if (process.argv[4] === '--weixin-pause' || process.argv[4] === '--weixin-resume') {
            await client.request(process.argv[4]==='--weixin-pause'?'channels.stop':'channels.start',{channel:'openclaw-weixin'});
            const deadline=Date.now()+8000;
            while (Date.now()<deadline) {
              const body=await client.request('channels.status',{probe:false,timeoutMs:1000});
              const rows=body.channelAccounts?.['openclaw-weixin'];
              const channel=config.channels?.['openclaw-weixin'] || {};
              const active=row=>channel.enabled!==false && channel.accounts?.[row.accountId]?.enabled!==false;
              if (Array.isArray(rows) && (process.argv[4]==='--weixin-pause'
                  ? rows.every(row=>row.enabled===false && row.running!==true)
                  : rows.every(row=>!active(row) || row.configured===false || row.enabled===true && row.running===true) &&
                    (!process.argv[5] || rows.some(row=>row.accountId===process.argv[5] && row.configured===true && (!active(row) || row.running===true))))) {
                return finish({ok:true});
              }
              await new Promise(resolve=>setTimeout(resolve,200));
            }
            return finish({ok:false,error:'channel_pending'});
          }
          if (process.argv[4] === '--voice') {
            const body = await client.request('tts.providers', {});
            const voice = body.providers?.find(row => row.id === 'wechat-companion-voice');
            return finish({ok: Boolean(voice), registered: Boolean(voice), configured: bool(voice?.configured)});
          }
          const body = await client.request('channels.status', {probe: false, timeoutMs: 2000});
          const rows = body.channelAccounts?.['openclaw-weixin'];
          if (!Array.isArray(rows)) return finish({ok: false, error: 'channel_unavailable'});
          finish({ok: true, accounts: rows.slice(0, 500).map(row => ({
            accountId: typeof row.accountId === 'string' ? row.accountId.slice(0, 160) : '',
            enabled: bool(row.enabled), configured: bool(row.configured), running: bool(row.running),
            connected: bool(row.connected), restartPending: bool(row.restartPending),
            lifecycle: typeof row.lifecycle === 'string' ? row.lifecycle.slice(0, 40) : null,
            errorKind: errorKind(row.lastError), lastInboundAt: stamp(row.lastInboundAt),
            lastOutboundAt: stamp(row.lastOutboundAt), lastStartAt: stamp(row.lastStartAt)
          }))});
        } catch { finish({ok: false, error: 'status_unavailable'}); }
      },
      onConnectError: () => finish({ok: false, error: 'connect_failed'})
    });
    timer = setTimeout(() => finish({ok: false, error: 'timeout'}), process.argv[4]?.startsWith('--weixin-')?26000:6000);
    client.start();
  }
} catch { finish({ok: false, error: 'runtime_unavailable'}); }
