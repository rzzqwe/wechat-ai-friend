// Read the verified 2026.9.6 installed-plugin index without booting every plugin.
// Any stale/ambiguous state falls back to the official CLI; never install here.
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import {createRequire} from 'node:module';
import {pathToFileURL} from 'node:url';

const start=performance.now();
const fallback=()=>({ok:false, fallback:true, elapsed_ms:Math.round(performance.now()-start)});
function inside(root, filename) {
  const resolved=path.resolve(root, filename);
  const relative=path.relative(root, resolved);
  if (relative.startsWith('..') || path.isAbsolute(relative)) throw Error('outside plugin');
  return resolved;
}
const hash=filename=>crypto.createHash('sha256').update(fs.readFileSync(filename)).digest('hex');
export async function readVerifiedWeixin(packageDirectory) {
try {
  const packageRoot=path.resolve(packageDirectory);
  const host=JSON.parse(fs.readFileSync(path.join(packageRoot,'package.json'),'utf8'));
  if (host.name!=='openclaw' || host.version!=='2026.9.6') throw Error('unverified host');
  const dist=path.join(packageRoot,'dist');
  const candidates=fs.readdirSync(dist).filter(name=>/^installed-plugin-index-store-[\w-]+\.mjs$/.test(name));
  const readers=candidates.map(name=>({name, source:fs.readFileSync(path.join(dist,name),'utf8')}))
    .filter(item=>item.source.includes('function readPersistedInstalledPluginIndexSync('));
  if (readers.length!==1) throw Error('ambiguous reader');
  const alias=readers[0].source.match(/readPersistedInstalledPluginIndexSync as ([\w$]+)/)?.[1];
  if (!alias) throw Error('reader unavailable');
  const module=await import(pathToFileURL(path.join(dist,readers[0].name)).href);
  const index=module[alias]({artifactPreservingReadOnly:true});
  const rows=index?.plugins?.filter(row=>row.pluginId==='openclaw-weixin');
  if (rows?.length!==1 || index.diagnostics?.some(row=>row.pluginId==='openclaw-weixin' && row.level==='error')) throw Error('unknown plugin state');
  const row=rows[0];
  if (row.installOwnerAmbiguous || !path.isAbsolute(row.rootDir) || !row.packageJson?.hash || !row.manifestHash) throw Error('unknown owner');
  const root=path.resolve(row.rootDir);
  const packageFile=inside(root,row.packageJson.path);
  const manifestFile=inside(root,row.manifestPath);
  if (hash(packageFile)!==row.packageJson.hash || hash(manifestFile)!==row.manifestHash) throw Error('stale index');
  const pkg=JSON.parse(fs.readFileSync(packageFile,'utf8'));
  const manifest=JSON.parse(fs.readFileSync(manifestFile,'utf8'));
  const version=pkg.version?.match(/^(\d+)\.(\d+)\.(\d+)$/)?.slice(1).map(Number);
  if (pkg.name!=='@tencent-weixin/openclaw-weixin' || manifest.id!=='openclaw-weixin' || row.packageVersion!==pkg.version ||
      !version || !(version[0]>2 || version[0]===2 && (version[1]>4 || version[1]===4 && version[2]>=9))) throw Error('incompatible plugin');
  const entry=inside(root,row.source || 'dist/index.js');
  if (!fs.statSync(entry).isFile()) throw Error('entry missing');
  const require=createRequire(packageFile);
  for (const dependency of Object.keys(pkg.dependencies || {})) require.resolve(dependency);
  return {ok:true, plugin:{id:'openclaw-weixin', rootDir:root, source:entry, version:pkg.version,
    status:row.enabled===false?'disabled':'registered', dependencyStatus:{requiredInstalled:true}}, elapsed_ms:Math.round(performance.now()-start)};
} catch { return fallback(); }
}
if (process.argv[1] && pathToFileURL(path.resolve(process.argv[1])).href===import.meta.url) {
  process.stdout.write(JSON.stringify(await readVerifiedWeixin(process.argv[2]))+'\n');
  process.exit(0);
}
