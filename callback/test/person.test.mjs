import {test,before,after} from 'node:test';
import assert from 'node:assert/strict';
import {randomBytes,createHash,pbkdf2Sync} from 'node:crypto';
import {spawnSync} from 'node:child_process';
import {mkdtemp,writeFile,readFile,rm,realpath} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {Miniflare,Log,LogLevel,convertV4MiniflareOptions} from 'miniflare';
import {canonical,ITERATIONS,relayPublicKey} from '../src/person.js';
const opaque=()=>randomBytes(32).toString('base64url'),hash=s=>createHash('sha256').update(s).digest('hex');
const logs=[],sensitive=[];

// Production workerd refuses PBKDF2 above 100,000 iterations (NotSupportedError); Miniflare does not enforce it.
test('PBKDF2 iterations stay within the Workers production cap',()=>{assert.ok(Number.isInteger(ITERATIONS)&&ITERATIONS>0&&ITERATIONS<=100_000,String(ITERATIONS));});
class SilentLog extends Log {constructor(){super(LogLevel.NONE);}log(value){logs.push(String(value));}}
let mf,directory,key;
const openssl=process.platform==='darwin'?'/opt/homebrew/bin/openssl':'/usr/bin/openssl';
function run(args,input){const r=spawnSync(openssl,args,{input,env:{PATH:'/usr/bin:/bin',OPENSSL_CONF:'/dev/null'}});assert.equal(r.status,0,'OpenSSL failed');return r.stdout;}
before(async()=>{
  directory=await realpath(await mkdtemp(join(tmpdir(),'thth-approval-')));key=join(directory,'ephemeral.key');
  const pem=run(['genpkey','-algorithm','RSA','-pkeyopt','rsa_keygen_bits:3072']);sensitive.push(pem.toString());await writeFile(key,pem,{mode:0o600});
  const publicKey=run(['pkey','-in',key,'-pubout','-outform','DER']).toString('base64url');
  const files=['test/person-harness.js','src/worker.js','src/media.js','src/media-object.js','src/index.js','src/relay.js','src/relay-object.js','src/person.js','src/person-object.js','src/deletion.js','src/deletion-object.js','src/invite.js','src/invite-object.js','src/activity.js','src/api.js'];
  const modules=await Promise.all(files.map(async name=>{const path=fileURLToPath(new URL('../'+name,import.meta.url));return{type:'ESModule',path,contents:await readFile(path,'utf8')};}));
  mf=new Miniflare(convertV4MiniflareOptions({modules,modulesRoot:fileURLToPath(new URL('..',import.meta.url)),compatibilityDate:'2026-09-01',cf:false,
    log:new SilentLog(),handleStructuredLogs:item=>logs.push(JSON.stringify(item)),bindings:{RELAY_PUBLIC_KEY:publicKey},
    durableObjects:{AUTH_RELAY:{className:'AuthRelay',useSQLite:true},PERSON:{className:'TestPerson',useSQLite:true},ACCOUNT:{className:'TestAccount',useSQLite:true},DELETION_INBOX:{className:'TestDeletion',useSQLite:true},MEDIA_OBJECT:{className:'TestMedia',useSQLite:true}},r2Buckets:['MEDIA_BUCKET'],
    ratelimits:{DELETION_PUBLIC_LIMIT:{namespace_id:'21204',simple:{limit:120,period:60}},AUTH_RATE_LIMIT:{namespace_id:'21101',simple:{limit:120,period:60}},RELAY_PUBLIC_LIMIT:{namespace_id:'21201',simple:{limit:120,period:60}},RELAY_VERIFY_LIMIT:{namespace_id:'21202',simple:{limit:600,period:60}},RELAY_JOB_LIMIT:{namespace_id:'21203',simple:{limit:180,period:60}},MEDIA_CONTROL_LIMIT:{namespace_id:'21302',simple:{limit:600,period:60}},MEDIA_UPLOAD_LIMIT:{namespace_id:'21303',simple:{limit:240,period:60}},MEDIA_UPLOAD_IP_LIMIT:{namespace_id:'21304',simple:{limit:120,period:60}},MEDIA_PUBLIC_LIMIT:{namespace_id:'21301',simple:{limit:120,period:60}}}}));await mf.ready;
});
after(async()=>{await mf?.dispose();await rm(directory,{recursive:true,force:true});const hits=logs.filter(line=>sensitive.some(value=>line.includes(value))).length;assert.equal(hits,0,'secret in runtime logs');console.log('approval runtime log scan: '+logs.length+' chunks, '+hits+' hits');});
function wire(type,subject,op,body={},options={}){
  const path=`${options.prefix??'/relay/v'}/${type}/${subject}/${op}`,raw=JSON.stringify(body),time=Date.now(),nonce=opaque();
  const signature=run(['dgst','-sha256','-sign',key,'-sigopt','rsa_padding_mode:pss','-sigopt','rsa_pss_saltlen:32'],Buffer.from(canonical('POST',path,options.role??'operator',subject,op,time,nonce,hash(raw)))).toString('base64url');
  sensitive.push(signature,nonce);
  return{url:'https://approval.test'+path,init:{method:'POST',headers:{'content-type':'application/json','cf-connecting-ip':options.ip??opaque(),'x-thth-time':String(time),'x-thth-nonce':nonce,'x-thth-signature':signature},body:raw}};
}
async function signed(type,id,op,body={},options={}){const w=wire(type,id,op,body,options);return mf.dispatchFetch(w.url,w.init);}
async function control(type,id,settings){return(await mf.dispatchFetch(`https://approval.test/__person/${type}/${id}`,settings?{method:'POST',body:JSON.stringify(settings)}:{})).json();}
async function person(){const id='p'+randomBytes(8).toString('hex'),secret=opaque(),salt=opaque();sensitive.push(secret);
  const data={salt,verifier:pbkdf2Sync(secret,Buffer.from(salt,'base64url'),100000,32,'sha256').toString('base64url'),iterations:100000};sensitive.push(data.verifier);
  assert.equal((await signed('person',id,'set',data)).status,200);return{id,secret,data};}
test('signature role/path/op/body/time/nonce binding and replay',async()=>{
  const p=await person();assert.equal((await signed('person',p.id,'unlock',{}, {role:'job'})).status,401);
  const good=wire('person',p.id,'unlock');assert.equal((await mf.dispatchFetch(good.url,good.init)).status,200);assert.equal((await mf.dispatchFetch(good.url,good.init)).status,409);
  for(const field of ['path','body','time','method']){const w=wire('person',p.id,'unlock');
    if(field==='path')w.url=w.url.replace('/unlock','/revoke');if(field==='body')w.init.body=' { }';
    if(field==='time')w.init.headers['x-thth-time']=String(Date.now()-61000);if(field==='method')w.init.method='PUT';
    assert.notEqual((await mf.dispatchFetch(w.url,w.init)).status,200);}
});
test('storage failure does not acknowledge provisioning',async()=>{
  const p=await person();await control('person',p.id,{fault:true});assert.equal((await signed('person',p.id,'set',p.data)).status,503);await control('person',p.id,{});
  assert.equal((await signed('person',p.id,'set',p.data)).status,200);
});
// 3.13.0: 承認ページ・承認待ちの一覧・承認の session は無い（404）。secret の登録と relay は残る。
test('no approval page, no pending list and no approval session',async()=>{
  const p=await person(),token=opaque();
  for(const init of [{},{method:'POST',headers:{origin:'https://approval.test','content-type':'application/x-www-form-urlencoded'},body:new URLSearchParams({secret:p.secret,csrf:opaque()})}]){
    for(const path of ['/approve/'+token,'/pending','/pending/'+token,'/pending/']){
      const response=await mf.dispatchFetch('https://approval.test'+path,{...init,headers:{...(init.headers??{}),'cf-connecting-ip':opaque()}});
      assert.equal(response.status,404,path);assert.equal(response.headers.get('cache-control'),'no-store');assert.ok(!(await response.text()).includes('secret'));
    }
  }
  const body={person:p.id,job_id:opaque(),digest:hash('x'),account:'alpha',kind:'send',text:'x',read_key_hash:hash(opaque()),
    context:{media:'threads',topic:null,options:null,reply_to:null,publish_at:null,target:null,reason:null}};
  for(const role of ['job','operator'])assert.equal((await signed('session',token,'create',body,{role})).status,404);
  assert.equal((await signed('session',token,'consume',{read_key:opaque()},{role:'job'})).status,404);
  assert.equal((await(await signed('person',p.id,'status')).json()).active,true);
});
test('persons poll status 30 times each; existing OAuth IP quota remains available',async()=>{
  const persons=await Promise.all(Array.from({length:4},()=>person())),ip=opaque();
  for(let n=0;n<30;n++)for(const p of persons)assert.equal((await signed('person',p.id,'status',{},{ip})).status,200);
  assert.equal((await mf.dispatchFetch('https://approval.test/relay/'+opaque(),{method:'POST',headers:{'content-type':'application/json','cf-connecting-ip':ip},body:JSON.stringify({read_key_hash:hash(opaque())})})).status,201);
});

test('product Python canonical signer is accepted by the real Worker',async()=>{
  const p=await person();await writeFile(join(directory,'relay-signer.key'),await readFile(key),{mode:0o600});
  const root=fileURLToPath(new URL('../..',import.meta.url));
  const script=`import json,sys
from thth import relay as r
class Response:
 status=200
 def __enter__(self):return self
 def __exit__(self,*a):pass
 def read(self,n):return b'{}'
class Opener:
 def open(self,request,timeout):
  print(json.dumps({'url':request.full_url,'headers':dict(request.header_items()),'body':request.data.decode()}));return Response()
r.httpsafe.build_opener=lambda *a:Opener()
r.signed_request('person',sys.argv[1],'status',{})`;
  // This test-only wire capture stays in RAM; product CLI never prints signatures.
  const child=spawnSync(process.env.THTH_TEST_PYTHON||'python3',['-c',script,p.id],{cwd:root,env:{PATH:'/usr/bin:/bin',PYTHONPATH:root,HOME:directory,THTH_APPS_DIR:directory,PYTHONDONTWRITEBYTECODE:'1'}});
  assert.equal(child.status,0,'Python signer failed');assert.equal(child.stderr.length,0);
  const w=JSON.parse(child.stdout);for(const [k,v]of Object.entries(w.headers))if(k.toLowerCase().includes('signature'))sensitive.push(v);
  const response=await mf.dispatchFetch(w.url,{method:'POST',headers:{...w.headers,'cf-connecting-ip':opaque()},body:w.body});assert.equal(response.status,200);assert.equal((await response.json()).active,true);
});
test('strict schema rejects coerced arrays and extra fields',async()=>{
  const p=await person();assert.equal((await signed('person',p.id,'set',{...p.data,salt:[p.data.salt]})).status,400);
  assert.equal((await signed('person',p.id,'set',{...p.data,extra:true})).status,400);
  assert.equal((await signed('person',p.id,'status',{extra:true})).status,400);
});
test('account revoke is operator-only and leaves the shared person and other accounts alone',async()=>{
  const p=await person(),account='a'+randomBytes(8).toString('hex'),other='b'+randomBytes(8).toString('hex');
  assert.equal((await signed('account',account,'revoke',{}, {role:'job'})).status,401);
  assert.equal((await signed('account',account,'revoke')).status,200);
  assert.equal((await(await signed('account',account,'status')).json()).active,false);
  assert.equal((await(await signed('account',other,'status')).json()).active,true);
  assert.equal((await(await signed('person',p.id,'status')).json()).active,true);
});
test('deletion receipt is unverified until signed VM matching; completed only after verified leave',async()=>{
  const blob=opaque()+'.'+opaque();sensitive.push(blob);
  const response=await mf.dispatchFetch('https://approval.test/data-deletion',{method:'POST',headers:{'content-type':'application/x-www-form-urlencoded','cf-connecting-ip':opaque()},body:new URLSearchParams({signed_request:blob})});
  assert.equal(response.status,200);const initial=await response.json(),code=initial.confirmation_code;sensitive.push(code);
  assert.equal(initial.url,'https://thth.me/data-deletion-status?code='+code);
  const status=()=>mf.dispatchFetch('https://approval.test/data-deletion-status?code='+code,{headers:{'cf-connecting-ip':opaque()}});
  let observed=await(await status()).json();assert.equal(observed.status,'unverified');assert.equal(observed.completed_at,undefined);assert.equal(observed.account,undefined);
  assert.equal((await signed('deletion',code,'read',{}, {role:'job'})).status,401);
  const operation=opaque(),completed=Date.now()-1;
  assert.equal((await signed('deletion',code,'complete',{account:'alpha',operation_id:operation,completed_at:completed})).status,409);
  const privateRow=await(await signed('deletion',code,'read')).json();assert.equal(privateRow.signed_request,blob);assert.equal(privateRow.expires_at-privateRow.received_at,30*86400000);
  assert.equal((await signed('deletion',code,'verify',{account:'alpha',blob_sha256:hash('different')})).status,409);
  assert.equal((await signed('deletion',code,'verify',{account:'alpha',blob_sha256:hash(blob)})).status,200);
  assert.equal((await(await signed('deletion',code,'read')).json()).signed_request,undefined);
  assert.equal((await signed('deletion',code,'complete',{account:'beta',operation_id:operation,completed_at:completed})).status,409);
  assert.equal((await signed('deletion',code,'complete',{account:'alpha',operation_id:operation,completed_at:completed})).status,200);
  observed=await(await status()).json();assert.equal(observed.status,'completed');assert.equal(observed.completed_at,completed);
  assert.equal((await signed('deletion',code,'complete',{account:'alpha',operation_id:operation,completed_at:completed})).status,200);
  assert.equal((await signed('deletion',code,'complete',{account:'alpha',operation_id:opaque(),completed_at:completed})).status,409);
});

test('receipt logical thirty-day expiry and alarm erase blob and binding',async()=>{
  const blob=opaque()+'.'+opaque(),response=await mf.dispatchFetch('https://approval.test/data-deletion',{method:'POST',headers:{'content-type':'application/x-www-form-urlencoded','cf-connecting-ip':opaque()},body:new URLSearchParams({signed_request:blob})});
  const code=(await response.json()).confirmation_code,read=await(await signed('deletion',code,'read')).json();
  await control('deletion','inbox',{clock:read.expires_at});
  assert.equal((await mf.dispatchFetch('https://approval.test/data-deletion-status?code='+code,{headers:{'cf-connecting-ip':opaque()}})).status,404);
  const rows=await control('deletion','inbox',{clock:read.expires_at,alarm:true});
  assert.ok(!JSON.stringify(rows).includes(blob));assert.ok(!new Map(rows).has('receipt:'+code));
  await control('deletion','inbox',{});
});
test('invalid-signature discard is operator-only, hash-bound, retry-safe and frees full inbox capacity',async()=>{
  const submit=blob=>mf.dispatchFetch('https://approval.test/data-deletion',{method:'POST',headers:{'content-type':'application/x-www-form-urlencoded','cf-connecting-ip':opaque()},body:new URLSearchParams({signed_request:blob})});
  for(const bad of ['.','x.y',opaque()+'.a.b',opaque()+'.'])assert.equal((await submit(bad)).status,400);
  const blob=opaque()+'.'+Buffer.from('synthetic-payload').toString('base64url');sensitive.push(blob);
  const codes=[];
  for(let i=0;i<1000;i++){const r=await submit(blob);assert.equal(r.status,200);codes.push((await r.json()).confirmation_code);}
  assert.equal((await control('deletion','inbox')).filter(([k])=>k.startsWith('receipt:')).length,1000);
  assert.equal((await submit(blob)).status,503);
  const code=codes[0],body={blob_sha256:hash(blob)};
  assert.equal((await signed('deletion',code,'discard',body,{role:'job'})).status,401);
  assert.equal((await signed('deletion',code,'discard',{blob_sha256:hash('other')})).status,409);
  assert.equal((await submit(blob)).status,503);
  const w=wire('deletion',code,'discard',body);
  assert.equal((await mf.dispatchFetch(w.url,w.init)).status,200);
  assert.equal((await mf.dispatchFetch(w.url,w.init)).status,409);
  assert.equal((await signed('deletion',code,'discard',body)).status,200);
  assert.equal((await signed('deletion',code,'discard',{blob_sha256:hash('other')})).status,404);
  assert.equal((await signed('deletion',code,'read')).status,404);
  assert.equal((await mf.dispatchFetch('https://approval.test/data-deletion-status?code='+code,{headers:{'cf-connecting-ip':opaque()}})).status,404);
  assert.equal((await signed('deletion',code,'complete',{account:'alpha',operation_id:opaque(),completed_at:Date.now()})).status,404);
  const accepted=await submit(blob);assert.equal(accepted.status,200);
  const legitimate=(await accepted.json()).confirmation_code;
  assert.equal((await signed('deletion',legitimate,'verify',{account:'alpha',blob_sha256:hash(blob)})).status,200);
  assert.equal((await signed('deletion',legitimate,'discard',body)).status,409);
  const rows=await control('deletion','inbox');
  const marker=rows.find(([k])=>k==='discarded:'+code)[1];
  assert.deepEqual(Object.keys(marker).sort(),['blob_sha256','expires_at']);
  assert.equal(rows.filter(([k])=>k.startsWith('receipt:')).length,1000);
  await control('deletion','inbox',{clock:Date.now()+31*86400000,alarm:true});
  assert.equal((await control('deletion','inbox')).filter(([k])=>k.startsWith('discarded:')||k.startsWith('receipt:')).length,0);
  await control('deletion','inbox',{});
});

// 3.13.0: /approval/… を /relay/v/… に改めた。3.12.0 の VM が deploy の間に叩くので旧 path も 1 版の間だけ受ける。
// 署名は叩かれた path に縛られる（旧 path の署名を新 path に付け替えても通らない）。
test('relay: new /relay/v/ path, old /approval/ path still accepted for one release, signatures stay path-bound',async()=>{
  const p=await person();
  assert.equal((await signed('person',p.id,'status',{})).status,200);
  assert.equal((await signed('person',p.id,'status',{},{prefix:'/approval'})).status,200);
  const old=wire('person',p.id,'status',{},{prefix:'/approval'});
  assert.equal((await mf.dispatchFetch(old.url.replace('/approval/','/relay/v/'),old.init)).status,401);
  const moved=wire('person',p.id,'status');
  assert.equal((await mf.dispatchFetch(moved.url.replace('/relay/v/','/approval/'),moved.init)).status,401);
  // 署名の文字列は変えない（VM と Worker の版ずれで合わなくならないように）。
  assert.ok(canonical('POST','/relay/v/person/x/status','operator','x','status',1,'n','h').startsWith('thth-approval-v1\n'));
});
// 運営者が Worker の変数を RELAY_PUBLIC_KEY に置き替えるまでは、旧名の APPROVAL_PUBLIC_KEY で動く。
test('relay public key: RELAY_PUBLIC_KEY first, falls back to the old APPROVAL_PUBLIC_KEY',()=>{
  assert.equal(relayPublicKey({APPROVAL_PUBLIC_KEY:'old'}),'old');
  assert.equal(relayPublicKey({RELAY_PUBLIC_KEY:'new',APPROVAL_PUBLIC_KEY:'old'}),'new');
  assert.equal(relayPublicKey({}),undefined);
});
