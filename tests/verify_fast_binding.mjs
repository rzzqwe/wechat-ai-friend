import assert from 'node:assert/strict';
import fs from 'node:fs';
import http from 'node:http';
import os from 'node:os';
import path from 'node:path';
import {runLogin} from '../scripts/weixin_login_fast.mjs';
import {serviceMatches,waitReady} from '../scripts/gateway_control.mjs';

function fixture(result) {
  const calls=[];
  return {calls,options:{
    qr:{DEFAULT_ILINK_BOT_TYPE:'3',startWeixinLoginWithQr:async()=>({qrcodeUrl:'fixture-qr',sessionKey:'fixture-session'}),
        displayQRCode:async()=>calls.push('qr'),waitForWeixinLogin:async()=>result},
    accounts:{saveWeixinAccount:(id,value)=>calls.push(['save',id,value]),registerWeixinAccountId:id=>calls.push(['index',id]),
      clearStaleAccountsForUserId:(id,user)=>calls.push(['cleanup',id,user]),listIndexedWeixinAccountIds:()=>['existing-id']},
    normalizeAccountId:id=>id.replace('@','-'),clearContextTokensForAccount:()=>{},accountId:'local',emit:value=>calls.push(value)
  }};
}
{
  const test=fixture({connected:true,botToken:'fixture-secret',accountId:'bot@id',baseUrl:'https://example.invalid',userId:'fixture-user'});
  assert.equal(await runLogin(test.options),'bot-id');
  assert.ok(test.calls.indexOf('qr')<test.calls.findIndex(value=>Array.isArray(value)&&value[0]==='save'));
  assert.deepEqual(test.calls.find(value=>Array.isArray(value)&&value[0]==='save'),['save','bot-id',{token:'fixture-secret',baseUrl:'https://example.invalid',userId:'fixture-user'}]);
  assert.ok(test.calls.includes('__WECHAT_PROVIDER_ID__bot-id'));
  assert.ok(!test.calls.filter(value=>typeof value==='string').join(' ').includes('fixture-secret'));
}
{
  const test=fixture({connected:true,botToken:'fixture-secret',accountId:'bot@id'});
  test.options.accounts.saveWeixinAccount=()=>{throw Error('fixture failure');};
  await assert.rejects(runLogin(test.options));
  assert.ok(!test.calls.includes('__WECHAT_LOGIN_SAVED__'));
}
{
  const test=fixture({connected:true,botToken:'fixture-secret',accountId:'bot@id'});
  let committed=null;
  test.options.saved=id=>{committed=id;};
  const emit=test.options.emit;
  test.options.emit=value=>{emit(value);if(value==='__WECHAT_LOGIN_SAVED__')throw Error('post-save output failure');};
  await assert.rejects(runLogin(test.options));
  assert.equal(committed,'bot-id');
}
for (const result of [{connected:false},{alreadyConnected:true}]) {
  const test=fixture(result);
  await assert.rejects(runLogin(test.options));
  assert.ok(!test.calls.includes('__WECHAT_LOGIN_SAVED__'));
}
{
  const test=fixture({alreadyConnected:true});test.options.existingId='existing-id';
  assert.equal(await runLogin(test.options),'existing-id');
  assert.ok(!test.calls.some(value=>Array.isArray(value)&&value[0]==='save'));
}
const temporary=fs.mkdtempSync(path.join(os.tmpdir(),'wechat-fast-binding-'));
try {
  const config=path.join(temporary,'openclaw.json');fs.writeFileSync(config,'{}');
  assert.deepEqual(await serviceMatches({readCommand:async()=>({environment:{}})}, {}, temporary,config),{ok:false,repair:true});
  const service={readCommand:async()=>({environment:{OPENCLAW_STATE_DIR:temporary,OPENCLAW_CONFIG_PATH:config}})};
  assert.deepEqual(await serviceMatches(service,{},temporary,config),{ok:true,repair:false});
  const other=path.join(temporary,'other');fs.mkdirSync(other);
  await assert.rejects(serviceMatches(service,{},other,config));
} finally {
  assert.ok(path.resolve(temporary).startsWith(path.resolve(os.tmpdir())+path.sep));
  assert.ok(path.basename(temporary).startsWith('wechat-fast-binding-'));
  fs.rmSync(temporary,{recursive:true,force:true});
}
let ready=false;
const server=http.createServer((request,response)=>{response.writeHead(request.url==='/healthz'||ready?200:503);response.end(JSON.stringify(request.url==='/healthz'?{ok:true}:{ready}));});
await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
try {
  const port=server.address().port;
  assert.equal(await waitReady(port,100),false);
  ready=true;assert.equal(await waitReady(port,1000),true);
} finally {await new Promise(resolve=>server.close(resolve));}
console.log('Fast binding: credential persistence, account isolation, service paths and actual readiness PASS.');
