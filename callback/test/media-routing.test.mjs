import {test} from 'node:test';
import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {once} from 'node:events';
import {createServer} from 'node:net';
import {generateKeyPairSync,randomBytes,createHash,sign,constants} from 'node:crypto';
import {mkdir,mkdtemp,writeFile,rm,realpath} from 'node:fs/promises';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {canonical} from '../src/media.js';
const cwd=fileURLToPath(new URL('..',import.meta.url)),opaque=()=>randomBytes(32).toString('base64url');
const sha=b=>createHash('sha256').update(b).digest('hex'),pause=n=>new Promise(r=>setTimeout(r,n));
test('Wrangler media routes bypass assets, persist R2 and do not log capabilities',{timeout:60000},async()=>{
 const base=join(cwd,'.test-tmp');await mkdir(base,{recursive:true});const dir=await realpath(await mkdtemp(join(base,'media-routing-')));
 const home=join(dir,'home'),assets=join(dir,'assets'),id=opaque(),cap=opaque(),bytes=Buffer.from('generated media '+opaque()),logs=[],hidden=[id,cap,bytes.toString()];
 await mkdir(home);await mkdir(join(assets,'m'),{recursive:true});await writeFile(join(assets,'m',cap),'asset shadow');await writeFile(join(dir,'empty.env'),'');
 const pair=generateKeyPairSync('rsa',{modulusLength:3072}),pub=pair.publicKey.export({type:'spki',format:'der'}).toString('base64url');
 const listener=createServer();listener.listen(0,'127.0.0.1');await once(listener,'listening');const port=listener.address().port;await new Promise(r=>listener.close(r));const origin='http://127.0.0.1:'+port;let child;
 const start=async()=>{
  child=spawn(process.execPath,['node_modules/wrangler/bin/wrangler.js','dev','--local','--ip','127.0.0.1','--port',String(port),'--inspector-port','0','--log-level','none','--var','RELAY_PUBLIC_KEY:'+pub,'--persist-to',join(dir,'state'),'--assets',assets,'--env-file',join(dir,'empty.env')],{cwd,env:{PATH:process.env.PATH,HOME:home,XDG_CONFIG_HOME:home,XDG_CACHE_HOME:home,XDG_DATA_HOME:home,TMPDIR:dir,WRANGLER_SEND_METRICS:'false',CLOUDFLARE_LOAD_DEV_VARS_FROM_DOT_ENV:'false'},stdio:['ignore','pipe','pipe']});
  child.stdout.on('data',d=>logs.push(d.toString()));child.stderr.on('data',d=>logs.push(d.toString()));
  for(let n=0;n<180;n++){if(child.exitCode!==null)throw Error('local media runtime exited: '+logs.map(line=>hidden.reduce((safe,secret)=>safe.replaceAll(secret,'[fake-redacted]'),line)).join('').slice(-4000));try{await fetch(origin+'/health-local',{signal:AbortSignal.timeout(100)});return;}catch{await pause(50);}}
  throw Error('local media startup timeout');
 };
 const stop=async()=>{if(child&&child.exitCode===null){child.kill('SIGTERM');await once(child,'exit');}};
 const control=async(subject,op,body)=>{
  const path='/media/'+subject+'/'+op,raw=JSON.stringify(body),time=Date.now(),nonce=opaque();
  const signature=sign('sha256',Buffer.from(canonical('POST',path,subject,op,time,nonce,sha(raw))),{key:pair.privateKey,padding:constants.RSA_PKCS1_PSS_PADDING,saltLength:32}).toString('base64url');hidden.push(nonce,signature);
  return fetch(origin+path,{method:'POST',headers:{'content-type':'application/json','x-thth-time':String(time),'x-thth-nonce':nonce,'x-thth-signature':signature},body:raw});
 };
 try{
  await start();assert.equal((await fetch(origin+'/m/'+cap)).status,410);
  const binding={actor:'operator',account:'alpha',sha256:sha(bytes)};
  assert.equal((await control(id,'create',{...binding,size:bytes.length,mime:'image/png',kind:'sanitized',part_size:null})).status,201);
  assert.equal((await fetch(origin+'/media-upload/'+id,{method:'PUT',headers:{'content-length':String(bytes.length)},body:bytes})).status,200);
  assert.equal((await control(id,'complete',binding)).status,200);
  const made=await control(cap,'provider',{...binding,media_id:sha(bytes),source:id,expires_at:Date.now()+500000});assert.equal(made.status,201);const generation=(await made.json()).generation;hidden.push(generation);
  await stop();await start();const get=await fetch(origin+'/m/'+cap);assert.equal(get.status,200);assert.ok(Buffer.from(await get.arrayBuffer()).equals(bytes),'persisted bytes differ');
  const result={...binding,media_id:sha(bytes),generation,purpose:'provider'};assert.equal((await control(cap,'published',result)).status,200);assert.equal((await control(cap,'published',result)).status,409);
  assert.equal((await control(cap,'invalidate',result)).status,200);
  for(const init of [{},{method:'HEAD'},{headers:{range:'bytes=0-1'}}])assert.equal((await fetch(origin+'/m/'+cap,init)).status,410);
 }finally{
  await stop();await rm(dir,{recursive:true,force:true});assert.equal(logs.filter(line=>hidden.some(secret=>line.includes(secret))).length,0,'HTTP runtime logs contain media authority');
  console.log('media routing log scan: '+logs.length+' chunks, no private values; two local starts');
 }
});
