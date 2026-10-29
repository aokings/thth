// Meta 計画 段 6: 本物の Worker（wrangler dev --local・本物の wrangler.jsonc の DO と migration と run_worker_first）と
// 本物の VM の Python（thth.webhook.pull_once・relay の署名）の通し。外への接続はしない。秘密は使い捨て。
import {pythonForTests} from './python-runtime.js';
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {spawn,spawnSync} from 'node:child_process';
import {once} from 'node:events';
import {tmpdir} from 'node:os';
import {createServer} from 'node:net';
import {randomBytes} from 'node:crypto';
import {mkdir,mkdtemp,writeFile,rm,realpath} from 'node:fs/promises';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';
const cwd=fileURLToPath(new URL('..',import.meta.url)),opaque=()=>randomBytes(32).toString('base64url');
const openssl=process.platform==='darwin'?'/opt/homebrew/bin/openssl':'/usr/bin/openssl';
const pause=ms=>new Promise(resolve=>setTimeout(resolve,ms));
function crypto(args,input){const result=spawnSync(openssl,args,{input,env:{PATH:'/usr/bin:/bin',OPENSSL_CONF:'/dev/null'}});assert.equal(result.status,0);return result.stdout;}
test('real Worker receives a signed Meta delivery and the real VM pulls, routes, acks once',{timeout:60000},async()=>{
  const base=join(cwd,'.test-tmp');await mkdir(base,{recursive:true});const dir=await realpath(await mkdtemp(join(base,'webhook-vm-')));
  const vmHome=await realpath(await mkdtemp(join(tmpdir(),'thth-webhook-vm-')));
  const home=join(dir,'home'),assets=join(dir,'assets'),key=join(dir,'key'),secret=opaque(),verify=opaque();
  const hidden=[secret,verify],logs=[];await mkdir(home);await mkdir(join(assets,'webhook'),{recursive:true});
  await writeFile(join(assets,'webhook','meta'),'shadow webhook');
  const pem=crypto(['genpkey','-algorithm','RSA','-pkeyopt','rsa_keygen_bits:3072']);hidden.push(pem.toString());await writeFile(key,pem,{mode:0o600});
  const pub=crypto(['pkey','-in',key,'-pubout','-outform','DER']).toString('base64url');
  await writeFile(join(dir,'local.env'),'');
  const listener=createServer();listener.listen(0,'127.0.0.1');await once(listener,'listening');const port=listener.address().port;await new Promise(resolve=>listener.close(resolve));
  const origin='http://127.0.0.1:'+port;
  const child=spawn(process.execPath,['node_modules/wrangler/bin/wrangler.js','dev','--local','--ip','127.0.0.1','--port',String(port),'--inspector-port','0',
    '--var','RELAY_PUBLIC_KEY:'+pub,'--var','META_APP_SECRET:'+secret,'--var','META_WEBHOOK_VERIFY_TOKEN:'+verify,
    '--log-level','none','--persist-to',join(dir,'state'),'--assets',assets,'--env-file',join(dir,'local.env')],
    {cwd,env:{PATH:process.env.PATH,HOME:home,XDG_CONFIG_HOME:home,WRANGLER_SEND_METRICS:'false',CLOUDFLARE_LOAD_DEV_VARS_FROM_DOT_ENV:'false'},stdio:['ignore','pipe','pipe']});
  child.stdout.on('data',x=>logs.push(x.toString()));child.stderr.on('data',x=>logs.push(x.toString()));
  try{
    for(let n=0;;n++){if(child.exitCode!==null||n>150)throw new Error('local worker did not start');try{await fetch(origin+'/health-local',{signal:AbortSignal.timeout(100)});break;}catch{await pause(50);}}
    // run_worker_first: assets の影（public/webhook/meta）を出さず Worker が答える。
    const ok=await fetch(`${origin}/webhook/meta?hub.mode=subscribe&hub.challenge=4242&hub.verify_token=${verify}`);
    assert.equal(ok.status,200);assert.equal(await ok.text(),'4242');
    const bad=await fetch(`${origin}/webhook/meta?hub.mode=subscribe&hub.challenge=4242&hub.verify_token=${verify.slice(0,-1)}x`);
    assert.equal(bad.status,403);assert.ok(!(await bad.text()).includes('shadow'));
    const vm=spawnSync(pythonForTests(),['test/webhook-vm.py',key],{cwd,env:{PATH:process.env.PATH,HOME:join(vmHome,'home'),PYTHONPATH:join(cwd,'..'),
      THTH_ROOT:join(dir,'vm'),THTH_ACCOUNTS_DIR:join(dir,'vm/accounts'),THTH_TEST_ALLOW_HTTP:'1',THTH_APPS_DIR:join(vmHome,'apps'),
      THTH_RELAY_BASE_URL:origin,THTH_APP_ENV_PATH:join(vmHome,'absent.env'),WEBHOOK_TEST_APP_SECRET:secret,PYTHONDONTWRITEBYTECODE:'1'},encoding:'utf8',timeout:30000});
    assert.equal(vm.status,0,'VM webhook integration failed: '+[...vm.stderr.matchAll(/line (\d+), in ([^\n]+)/g)].map(m=>m[1]+':'+m[2]).join(',')+' '+vm.stderr.trim().split('\n').at(-1)?.split(':')[0]);
    assert.equal(vm.stderr,'');
    // 鍵を 1 文字変えた通知は 403・正しい通知は 200・同じ通知の送り直しも 200（重複として数えるだけ）。
    assert.deepEqual(JSON.parse(vm.stdout),{statuses:[403,200,200],first:{pulled:3,routed:{'kopicha-fb':2},unrouted:{page:1}},
      second:0,stored:2,mentions:['77_1'],replies:['9001_1'],duplicates:3});
  }finally{
    if(child.exitCode===null){child.kill('SIGTERM');await once(child,'exit');}
    await rm(dir,{recursive:true,force:true});await rm(vmHome,{recursive:true,force:true});
    const hits=logs.filter(line=>hidden.some(value=>line.includes(value))).length;assert.equal(hits,0,'runtime logs contain a secret');
  }
});
