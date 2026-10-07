// Use Tencent's verified QR and credential-storage implementations directly.
// The launcher stops the gateway first and publishes routing before restarting it.
import fs from 'node:fs';
import path from 'node:path';
import net from 'node:net';
import {createRequire} from 'node:module';
import {pathToFileURL} from 'node:url';
import {readVerifiedWeixin} from './weixin_status_fast.mjs';

export async function runLogin({qr, accounts, normalizeAccountId, clearContextTokensForAccount, accountId, existingId, emit=console.log, saved=()=>{}}) {
  const start=await qr.startWeixinLoginWithQr({accountId,botType:qr.DEFAULT_ILINK_BOT_TYPE,verbose:false});
  if (!start.qrcodeUrl) throw Error('二维码获取失败，请检查网络后重试。');
  emit('__WECHAT_QR_READY__');
  emit('\n用手机微信扫描以下二维码，并在手机上确认：\n');
  await qr.displayQRCode(start.qrcodeUrl);
  const result=await qr.waitForWeixinLogin({sessionKey:start.sessionKey,timeoutMs:480000,
    botType:qr.DEFAULT_ILINK_BOT_TYPE,verbose:false});
  if (result.connected && result.botToken && result.accountId) {
    const parent=Number(process.env.WECHAT_AI_LOGIN_PARENT_PID);
    if (parent>0) process.kill(parent,0);
    const id=normalizeAccountId(result.accountId);
    accounts.saveWeixinAccount(id,{token:result.botToken,baseUrl:result.baseUrl,userId:result.userId});
    accounts.registerWeixinAccountId(id);
    if (result.userId) accounts.clearStaleAccountsForUserId(id,result.userId,clearContextTokensForAccount);
    saved(id);
    emit('__WECHAT_LOGIN_SAVED__');
    emit('__WECHAT_PROVIDER_ID__'+id);
    return id;
  }
  if (result.alreadyConnected && existingId && accounts.listIndexedWeixinAccountIds().includes(existingId)) {
    saved(existingId);
    emit('__WECHAT_LOGIN_SAVED__');
    emit('__WECHAT_PROVIDER_ID__'+existingId);
    return existingId;
  }
  throw Error('微信授权尚未完成，请重新扫码并在手机上确认。');
}

export async function prepare(packageRoot) {
  const status=await readVerifiedWeixin(packageRoot);
  if (!status.ok || status.plugin.version!=='2.4.9') return null;
  const root=status.plugin.rootDir;
  const file=name=>pathToFileURL(path.join(root,'dist/src',name)).href;
  const require=createRequire(path.join(root,'package.json'));
  const [qr,accounts,context,ids]=await Promise.all([
    import(file('auth/login-qr.js')),import(file('auth/accounts.js')),
    import(file('messaging/inbound.js')),import(pathToFileURL(require.resolve('openclaw/plugin-sdk/account-id')).href)
  ]);
  return {qr,accounts,normalizeAccountId:ids.normalizeAccountId,
    clearContextTokensForAccount:context.clearContextTokensForAccount};
}

async function isListening(port) {
  return new Promise(resolve=>{
    const socket=net.connect({host:'127.0.0.1',port});
    let done=false;
    const finish=value=>{if(done)return;done=true;socket.destroy();resolve(value);};
    socket.once('connect',()=>finish(true));socket.once('error',()=>finish(false));
    socket.setTimeout(1500,()=>finish(true));
  });
}
if (process.argv[1] && pathToFileURL(path.resolve(process.argv[1])).href===import.meta.url) {
  try {
    const ready=await prepare(process.argv[2]);
    if (!ready) {console.log('扫码组件需要使用官方兼容入口。');process.exit(2);}
    if (process.argv[3]==='--check') {console.log('快速扫码组件已就绪。');process.exit(0);}
    const config=JSON.parse(fs.readFileSync(process.env.OPENCLAW_CONFIG_PATH,'utf8').replace(/^\uFEFF/,''));
    const port=config.gateway?.port ?? 18789;
    if (config.gateway?.mode!=='local' || !Number.isInteger(port) || port<1 || port>65535 ||
        !(process.env.WECHAT_AI_GATEWAY_PAUSED==='1' && !await isListening(port) ||
          process.env.WECHAT_AI_CHANNEL_PAUSED==='1' && config.channels?.['openclaw-weixin']?.enabled===false)) {
      throw Error('后台尚未暂停，未开始扫码。');
    }
    const parent=Number(process.env.WECHAT_AI_LOGIN_PARENT_PID);
    if (parent>0) setInterval(()=>{try{process.kill(parent,0);}catch{process.exit(1);}},500).unref();
    await runLogin({...ready,accountId:process.argv[3],existingId:process.env.WECHAT_AI_EXISTING_PROVIDER,
      saved:id=>{
        const filename=process.env.WECHAT_AI_LOGIN_RECEIPT;
        if(!filename)return;
        if(!ready.accounts.listIndexedWeixinAccountIds().includes(id) || !ready.accounts.loadWeixinAccount(id)?.token?.trim()) {
          throw Error('登录凭证尚未完整保存。');
        }
        const temporary=filename+'.tmp';
        fs.writeFileSync(temporary,JSON.stringify({attempt:process.env.WECHAT_AI_LOGIN_ATTEMPT,
          local_id:process.argv[3],agent_id:process.env.WECHAT_AI_BINDING_AGENT,provider_id:id,saved:true}),{encoding:'utf8',mode:0o600});
        fs.renameSync(temporary,filename);
      }});
    process.exit(0);
  } catch {
    console.error('微信扫码或凭证保存失败，请检查网络和本机文件权限后重试。');
    process.exit(1);
  }
}
