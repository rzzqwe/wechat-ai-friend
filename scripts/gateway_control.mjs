// Pinned native service API retains OpenClaw's locks, ownership and update guards.
import fs from 'node:fs';
import path from 'node:path';
import {pathToFileURL} from 'node:url';

const canonical=value=>fs.realpathSync.native(path.resolve(value)).toLowerCase();
export async function serviceMatches(service, env, state, config) {
  const command=await service.readCommand(env,{requireEffective:true,timeoutMs:3000});
  if (!command) return {ok:false,repair:true};
  const saved=command.environment || {};
  if (!saved.OPENCLAW_STATE_DIR || !saved.OPENCLAW_CONFIG_PATH) {
    // An old default-user launcher can be stopped before repair. Custom homes,
    // profiles, executables or state directories must never be inferred.
    const defaultState=env.USERPROFILE && path.join(env.USERPROFILE,'.openclaw');
    const identityKeys=['OPENCLAW_STATE_DIR','OPENCLAW_CONFIG_PATH','OPENCLAW_HOME','HOME','USERPROFILE','OPENCLAW_PROFILE'];
    const stopSafe=defaultState && command.sourcePath && command.programArguments?.[0] &&
      !identityKeys.some(key=>saved[key]) && canonical(state)===canonical(defaultState) &&
      canonical(config)===canonical(path.join(defaultState,'openclaw.json')) &&
      canonical(command.sourcePath)===canonical(path.join(defaultState,'gateway.cmd')) &&
      canonical(command.programArguments[0])===canonical(process.execPath);
    return {ok:false,repair:true,...stopSafe?{stopSafe:true}:{}};
  }
  if (canonical(saved.OPENCLAW_STATE_DIR)!==canonical(state) || canonical(saved.OPENCLAW_CONFIG_PATH)!==canonical(config)) {
    throw Error('后台的数据或配置路径与工作台不同，未操作其他后台。');
  }
  return {ok:true,repair:false};
}
export async function waitReady(port, timeoutMs=45000) {
  const deadline=Date.now()+timeoutMs;
  while (Date.now()<deadline) {
    try {
      const replies=await Promise.all(['/healthz','/readyz'].map(endpoint=>fetch(`http://127.0.0.1:${port}${endpoint}`,
        {signal:AbortSignal.timeout(1500)})));
      const bodies=await Promise.all(replies.map(response=>response.json().catch(()=>null)));
      const ready=replies.every(response=>response.ok) && bodies[0]?.ok===true && bodies[1]?.ready===true;
      if (ready) return true;
    } catch {}
    await new Promise(resolve=>setTimeout(resolve,500));
  }
  return false;
}
if (process.argv[1] && pathToFileURL(path.resolve(process.argv[1])).href===import.meta.url) {
  try {
    const [packageRoot,action,state,config]=process.argv.slice(2);
    const metadata=JSON.parse(fs.readFileSync(path.join(packageRoot,'package.json'),'utf8'));
    if (metadata.name!=='openclaw' || metadata.version!=='2026.9.6') process.exit(2);
    const dist=path.join(packageRoot,'dist');
    const modules=fs.readdirSync(dist).filter(name=>/^service-[\w-]+\.mjs$/.test(name))
      .map(name=>({name,source:fs.readFileSync(path.join(dist,name),'utf8')}))
      .filter(item=>item.source.includes('function resolveGatewayService('));
    if (modules.length!==1) process.exit(2);
    const alias=modules[0].source.match(/resolveGatewayService as ([\w$]+)/)?.[1];
    if (!alias) process.exit(2);
    const module=await import(pathToFileURL(path.join(dist,modules[0].name)).href);
    const service=module[alias]();
    const matches=await serviceMatches(service,process.env,state,config);
    if (action==='inactive') {
      if (!matches.ok && !matches.stopSafe) process.exit(2);
      const runtime=await service.readRuntime(process.env,{timeoutMs:3000});
      console.log(JSON.stringify({ok:runtime.status==='stopped'}));
      process.exit(runtime.status==='stopped'?0:1);
    }
    if (action==='check') {console.log(JSON.stringify(matches));process.exit(matches.ok?0:2);}
    if (!matches.ok && !(action==='stop' && matches.stopSafe)) {console.error('后台服务路径需要先修复。');process.exit(2);}
    if (action==='stop') {
      await service.stop({env:process.env,stdout:process.stdout});
      console.log('后台已暂停，可以开始独立扫码。');
    } else if (action==='start') {
      const cfg=JSON.parse(fs.readFileSync(config,'utf8').replace(/^\uFEFF/,''));
      const port=cfg.gateway?.port ?? 18789;
      if (cfg.gateway?.mode!=='local' || cfg.gateway?.tls?.enabled || !Number.isInteger(port) || port<1 || port>65535) process.exit(2);
      if (await waitReady(port,1000)) {console.log('后台已就绪，复用当前进程。');process.exit(0);}
      await service.start({env:process.env,stdout:process.stdout});
      if (!await waitReady(port)) {
        console.log('微信绑定已保存；后台仍在初始化，工作台会继续显示实际运行状态。');
        process.exit(3);
      }
      console.log('后台已就绪。');
    } else throw Error('未知后台操作');
    process.exit(0);
  } catch (error) {
    let detail=String(error?.message || '未知后台错误');
    try {
      const config=JSON.parse(fs.readFileSync(process.argv[5],'utf8').replace(/^\uFEFF/,''));
      const scrub=value=>{
        if (!value || typeof value!=='object')return;
        for(const [key,item] of Object.entries(value)) {
          if(/key|token|password|secret/i.test(key) && typeof item==='string' && item.length>3) detail=detail.replaceAll(item,'[已隐藏]');
          else scrub(item);
        }
      };
      scrub(config);
    } catch {}
    for(const [key,value] of Object.entries(process.env)) if(/KEY|TOKEN|PASSWORD|SECRET/.test(key) && value.length>3) detail=detail.replaceAll(value,'[已隐藏]');
    console.error('后台操作失败：'+detail.slice(0,1200)+'；已保存的绑定仍保留。');
    process.exit(1);
  }
}
