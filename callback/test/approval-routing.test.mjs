import {test} from 'node:test';
import assert from 'node:assert/strict';
import {spawn,spawnSync} from 'node:child_process';
import {once} from 'node:events';
import {tmpdir} from 'node:os';
import {createServer} from 'node:net';
import {randomBytes,createHash,pbkdf2Sync} from 'node:crypto';
import {mkdir,mkdtemp,writeFile,rm,realpath} from 'node:fs/promises';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {canonical} from '../src/approval.js';
const cwd=fileURLToPath(new URL('..',import.meta.url)),opaque=()=>randomBytes(32).toString('base64url');
const hash=s=>createHash('sha256').update(s).digest('hex');
const openssl=process.platform==='darwin'?'/opt/homebrew/bin/openssl':'/usr/bin/openssl';
const pause=ms=>new Promise(resolve=>setTimeout(resolve,ms));
function crypto(args,input){const result=spawnSync(openssl,args,{input,env:{PATH:'/usr/bin:/bin',OPENSSL_CONF:'/dev/null'}});assert.equal(result.status,0);return result.stdout;}
test('real approval routing bypasses assets, two DOs survive restart and consumed receipt stays consumed',{timeout:60000},async()=>{
  const base=join(cwd,'.test-tmp');await mkdir(base,{recursive:true});const dir=await realpath(await mkdtemp(join(base,'approval-routing-')));
  const vmHome=await realpath(await mkdtemp(join(tmpdir(),'thth-leave-vm-')));
  const home=join(dir,'home'),assets=join(dir,'assets'),key=join(dir,'key'),token=opaque(),readKey=opaque(),secret=opaque(),salt=opaque(),bodyText=opaque();
  const hidden=[token,readKey,secret,bodyText],logs=[];await mkdir(home);await mkdir(join(assets,'approve'),{recursive:true});
  await writeFile(join(assets,'approve',token),'shadow approval');
  await writeFile(join(assets,'data-deletion'),'shadow deletion');await writeFile(join(assets,'data-deletion-status'),'shadow status');
  const pem=crypto(['genpkey','-algorithm','RSA','-pkeyopt','rsa_keygen_bits:3072']);hidden.push(pem.toString());await writeFile(key,pem,{mode:0o600});
  const pub=crypto(['pkey','-in',key,'-pubout','-outform','DER']).toString('base64url');
  await writeFile(join(dir,'local.env'),'');
  const listener=createServer();listener.listen(0,'127.0.0.1');await once(listener,'listening');const port=listener.address().port;await new Promise(resolve=>listener.close(resolve));
  const origin='http://127.0.0.1:'+port;let child;
  const start=async()=>{
    child=spawn(process.execPath,['node_modules/wrangler/bin/wrangler.js','dev','--local','--ip','127.0.0.1','--port',String(port),'--inspector-port','0','--var','APPROVAL_PUBLIC_KEY:'+pub,'--log-level','none','--persist-to',join(dir,'state'),'--assets',assets,'--env-file',join(dir,'local.env')],{cwd,env:{PATH:process.env.PATH,HOME:home,XDG_CONFIG_HOME:home,WRANGLER_SEND_METRICS:'false',CLOUDFLARE_LOAD_DEV_VARS_FROM_DOT_ENV:'false'},stdio:['ignore','pipe','pipe']});
    child.stdout.on('data',x=>logs.push(x.toString()));child.stderr.on('data',x=>logs.push(x.toString()));
    for(let n=0;n<150;n++){if(child.exitCode!==null)throw new Error('local worker did not start: '+logs.map(line=>hidden.reduce((safe,secret)=>safe.replaceAll(secret,'[dynamic-fake-redacted]'),line)).join('').slice(-6000));try{await fetch(origin+'/health-local',{signal:AbortSignal.timeout(100)});return;}catch{await pause(50);}}
    throw new Error('local startup timeout');
  };
  const stop=async()=>{if(child&&child.exitCode===null){child.kill('SIGTERM');await once(child,'exit');}};
  const signed=async(type,id,operation,body)=>{
    const path=`/approval/${type}/${id}/${operation}`,raw=JSON.stringify(body),time=Date.now(),nonce=opaque();
    const signature=crypto(['dgst','-sha256','-sign',key,'-sigopt','rsa_padding_mode:pss','-sigopt','rsa_pss_saltlen:32'],Buffer.from(canonical('POST',path,type==='session'?'job':'operator',id,operation,time,nonce,hash(raw)))).toString('base64url');hidden.push(signature);
    return fetch(origin+path,{method:'POST',headers:{'content-type':'application/json','x-thth-time':String(time),'x-thth-nonce':nonce,'x-thth-signature':signature},body:raw});
  };
  try{
    await start();const unknown=await fetch(origin+'/approve/'+token);assert.equal(unknown.status,410);assert.ok(!(await unknown.text()).includes('shadow'));
    const verifier=pbkdf2Sync(secret,Buffer.from(salt,'base64url'),600000,32,'sha256').toString('base64url');hidden.push(verifier);
    assert.equal((await signed('person','person','set',{salt,verifier,iterations:600000})).status,200);
    assert.equal((await signed('session',token,'create',{person:'person',job_id:opaque(),digest:hash(bodyText),account:'alpha',kind:'retract',text:bodyText,read_key_hash:hash(readKey),context:{media:'threads',reply_to:null,publish_at:null,target:'123',reason:'requested',topic:null,options:null}})).status,201);
    await stop();await start();const page=await(await fetch(origin+'/approve/'+token)).text();assert.ok(page.includes(bodyText)&&page.includes('この投稿を削除'));
    const csrf=/name="csrf" value="([^"]+)"/.exec(page)[1];hidden.push(csrf);
    const approved=await fetch(origin+'/approve/'+token,{method:'POST',headers:{origin,'content-type':'application/x-www-form-urlencoded'},body:new URLSearchParams({secret,csrf})});assert.equal(approved.status,200);
    assert.ok((await approved.text()).includes('削除の承認を受け付けました'));
    await stop();await start();assert.equal((await signed('session',token,'consume',{read_key:readKey})).status,200);
    await stop();await start();assert.equal((await signed('session',token,'consume',{read_key:readKey})).status,404);
    assert.equal((await signed('account','alpha','revoke',{})).status,200);
    const blob=opaque()+'.'+opaque();hidden.push(blob);
    const reception=await fetch(origin+'/data-deletion',{method:'POST',headers:{'content-type':'application/x-www-form-urlencoded'},body:new URLSearchParams({signed_request:blob})});
    assert.equal(reception.status,200);const receipt=(await reception.json()).confirmation_code;hidden.push(receipt);
    await stop();await start();
    assert.equal((await(await signed('account','alpha','status',{})).json()).active,false);
    assert.equal((await(await fetch(origin+'/data-deletion-status?code='+receipt)).json()).status,'unverified');
    assert.equal((await(await signed('deletion',receipt,'read',{})).json()).signed_request,blob);
    assert.equal((await signed('deletion',receipt,'verify',{account:'alpha',blob_sha256:hash(blob)})).status,200);
    await stop();await start();
    assert.equal((await(await signed('deletion',receipt,'read',{})).json()).signed_request,undefined);
    const completed=Date.now(),operation=opaque();
    assert.equal((await signed('deletion',receipt,'complete',{account:'alpha',operation_id:operation,completed_at:completed})).status,200);
    await stop();await start();
    assert.equal((await(await fetch(origin+'/data-deletion-status?code='+receipt)).json()).completed_at,completed);
    const python=process.env.PYTHON_FOR_TESTS||(process.platform==='darwin'?'/opt/homebrew/Caskroom/miniforge/base/bin/python':'python3');
    const vm=spawnSync(python,['test/deletion-vm.py',key],{cwd,env:{PATH:process.env.PATH,HOME:home,PYTHONPATH:join(cwd,'..'),THTH_ROOT:join(dir,'vm'),THTH_ACCOUNTS_DIR:join(dir,'vm/accounts'),THTH_APPS_DIR:join(vmHome,'apps'),THTH_APPROVAL_BASE_URL:origin},encoding:'utf8',timeout:20000});
    assert.equal(vm.status,0,'local Python deletion/leave integration failed: '+[...vm.stderr.matchAll(/line (\d+), in ([^\n]+)/g)].map(m=>m[1]+':'+m[2]).join(',')+' '+vm.stderr.trim().split('\n').at(-1)?.split(':')[0]);assert.equal(vm.stderr,'');
    assert.deepEqual(JSON.parse(vm.stdout),{verified:1,unmatched:1,discarded_invalid_signature:1,local_completed:true,remote_completed:true,events:2});
  }finally{await stop();await rm(dir,{recursive:true,force:true});await rm(vmHome,{recursive:true,force:true});const hits=logs.filter(line=>hidden.some(secret=>line.includes(secret))).length;assert.equal(hits,0,'HTTP runtime logs contain private data');console.log('approval routing log scan: '+logs.length+' chunks, '+hits+' hits; 7 local starts');}
});
