// Only the host's pure custom-provider config builder; no onboarding or API calls.
import fs from 'node:fs';
import path from 'node:path';
import {pathToFileURL} from 'node:url';

try {
  const root=path.resolve(process.argv[2]);
  const metadata=JSON.parse(fs.readFileSync(path.join(root,'package.json'),'utf8'));
  if (metadata.name!=='openclaw' || metadata.version!=='2026.9.6') process.exit(2);
  const dist=path.join(root,'dist');
  const modules=fs.readdirSync(dist).filter(name=>/^onboard-custom-config-[\w-]+\.mjs$/.test(name))
    .map(name=>({name, source:fs.readFileSync(path.join(dist,name),'utf8')}))
    .filter(item=>item.source.includes('function applyCustomApiConfig('));
  if (modules.length!==1) process.exit(2);
  const alias=modules[0].source.match(/applyCustomApiConfig as ([\w$]+)/)?.[1];
  if (!alias) process.exit(2);
  let input='';
  for await (const chunk of process.stdin) input+=chunk;
  const config=JSON.parse(input);
  const module=await import(pathToFileURL(path.join(dist,modules[0].name)).href);
  const result=module[alias]({config, baseUrl:process.env.WECHAT_AI_BASE_URL, modelId:process.env.WECHAT_AI_MODEL,
    apiKey:process.env.WECHAT_AI_API_KEY, compatibility:'openai', setAsPrimary:true});
  process.stdout.write(JSON.stringify(result.config)+'\n');
  process.exit(0);
} catch {
  process.stderr.write('文本模型配置生成失败；原配置未修改。\n');
  process.exit(1);
}
