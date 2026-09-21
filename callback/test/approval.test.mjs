import {test,before,after} from 'node:test';
import assert from 'node:assert/strict';
import {randomBytes,createHash,pbkdf2Sync} from 'node:crypto';
import {spawnSync} from 'node:child_process';
import {mkdtemp,writeFile,readFile,rm,realpath} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {Miniflare,Log,LogLevel,convertV4MiniflareOptions} from 'miniflare';
import {canonical,ITERATIONS} from '../src/approval.js';
import {canonical as mediaCanonical} from '../src/media.js';
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
  const files=['test/approval-harness.js','src/worker.js','src/media.js','src/media-object.js','src/index.js','src/relay.js','src/relay-object.js','src/approval.js','src/approval-object.js','src/deletion.js','src/deletion-object.js'];
  const modules=await Promise.all(files.map(async name=>{const path=fileURLToPath(new URL('../'+name,import.meta.url));return{type:'ESModule',path,contents:await readFile(path,'utf8')};}));
  mf=new Miniflare(convertV4MiniflareOptions({modules,modulesRoot:fileURLToPath(new URL('..',import.meta.url)),compatibilityDate:'2026-09-01',cf:false,
    log:new SilentLog(),handleStructuredLogs:item=>logs.push(JSON.stringify(item)),bindings:{APPROVAL_PUBLIC_KEY:publicKey},
    durableObjects:{AUTH_RELAY:{className:'AuthRelay',useSQLite:true},APPROVAL_PERSON:{className:'TestPerson',useSQLite:true},APPROVAL_SESSION:{className:'TestSession',useSQLite:true},APPROVAL_ACCOUNT:{className:'TestAccount',useSQLite:true},DELETION_INBOX:{className:'TestDeletion',useSQLite:true},MEDIA_OBJECT:{className:'TestMedia',useSQLite:true}},r2Buckets:['MEDIA_BUCKET'],
    ratelimits:{DELETION_PUBLIC_LIMIT:{namespace_id:'21204',simple:{limit:120,period:60}},AUTH_RATE_LIMIT:{namespace_id:'21101',simple:{limit:120,period:60}},APPROVAL_PUBLIC_LIMIT:{namespace_id:'21201',simple:{limit:120,period:60}},APPROVAL_VERIFY_LIMIT:{namespace_id:'21202',simple:{limit:600,period:60}},APPROVAL_JOB_LIMIT:{namespace_id:'21203',simple:{limit:180,period:60}},MEDIA_CONTROL_LIMIT:{namespace_id:'21302',simple:{limit:600,period:60}},MEDIA_UPLOAD_LIMIT:{namespace_id:'21303',simple:{limit:240,period:60}},MEDIA_UPLOAD_IP_LIMIT:{namespace_id:'21304',simple:{limit:120,period:60}},MEDIA_PUBLIC_LIMIT:{namespace_id:'21301',simple:{limit:120,period:60}}}}));await mf.ready;
});
after(async()=>{await mf?.dispose();await rm(directory,{recursive:true,force:true});const hits=logs.filter(line=>sensitive.some(value=>line.includes(value))).length;assert.equal(hits,0,'secret in runtime logs');console.log('approval runtime log scan: '+logs.length+' chunks, '+hits+' hits');});
function wire(type,subject,op,body={},options={}){
  const path=`/approval/${type}/${subject}/${op}`,raw=JSON.stringify(body),time=Date.now(),nonce=opaque();
  const signature=run(['dgst','-sha256','-sign',key,'-sigopt','rsa_padding_mode:pss','-sigopt','rsa_pss_saltlen:32'],Buffer.from(canonical('POST',path,options.role??(type==='session'?'job':'operator'),subject,op,time,nonce,hash(raw)))).toString('base64url');
  sensitive.push(signature,nonce);
  return{url:'https://approval.test'+path,init:{method:'POST',headers:{'content-type':'application/json','cf-connecting-ip':options.ip??opaque(),'x-thth-time':String(time),'x-thth-nonce':nonce,'x-thth-signature':signature},body:raw}};
}
async function signed(type,id,op,body={},options={}){const w=wire(type,id,op,body,options);return mf.dispatchFetch(w.url,w.init);}
async function control(type,id,settings){return(await mf.dispatchFetch(`https://approval.test/__approval/${type}/${id}`,settings?{method:'POST',body:JSON.stringify(settings)}:{})).json();}
async function person(){const id='p'+randomBytes(8).toString('hex'),secret=opaque(),salt=opaque();sensitive.push(secret);
  const data={salt,verifier:pbkdf2Sync(secret,Buffer.from(salt,'base64url'),100000,32,'sha256').toString('base64url'),iterations:100000};sensitive.push(data.verifier);
  assert.equal((await signed('person',id,'set',data)).status,200);return{id,secret,data};}
async function session(p,options={}){const token=opaque(),readKey=opaque(),text='本文 '+opaque();sensitive.push(token,readKey,text);
  const body={person:p.id,job_id:opaque(),digest:hash(text),account:'alpha',kind:'send',text,read_key_hash:hash(readKey),context:{media:'threads',topic:null,options:null,reply_to:'reply-1',publish_at:'2026-09-20T12:00:00Z',target:null,reason:null},...options};
  sensitive.push(body.text);assert.equal((await signed('session',token,'create',body)).status,201);return{token,readKey,body};}
async function page(s){return mf.dispatchFetch('https://approval.test/approve/'+s.token,{headers:{'cf-connecting-ip':opaque()}});}
async function csrf(s){return /name="csrf" value="([^"]+)"/.exec(await(await page(s)).text())?.[1];}
async function approve(s,p,secret=p.secret,nonce){return mf.dispatchFetch('https://approval.test/approve/'+s.token,{method:'POST',headers:{'content-type':'application/x-www-form-urlencoded',origin:'https://approval.test','cf-connecting-ip':opaque()},body:new URLSearchParams({secret,csrf:nonce??await csrf(s)})});}
const consume=s=>signed('session',s.token,'consume',{read_key:s.readKey});
// 第 8-10 段の直し F1/F2: the page's images are real capabilities in these tests,
// so liveness, revocation and the bounded expiry are observed, not asserted.
async function mediaCall(subject,op,body){
  const path='/media/'+subject+'/'+op,raw=JSON.stringify(body),time=Date.now(),nonce=opaque();
  const signature=run(['dgst','-sha256','-sign',key,'-sigopt','rsa_padding_mode:pss','-sigopt','rsa_pss_saltlen:32'],
    Buffer.from(mediaCanonical('POST',path,subject,op,time,nonce,hash(raw)))).toString('base64url');
  sensitive.push(signature,nonce);
  return mf.dispatchFetch('https://approval.test'+path,{method:'POST',headers:{'content-type':'application/json',
    'cf-connecting-ip':opaque(),'x-thth-time':String(time),'x-thth-nonce':nonce,'x-thth-signature':signature},body:raw});
}
async function preview(account='alpha'){
  const bytes=Buffer.from('synthetic preview '+opaque()),source=opaque(),capability=opaque();
  const bound={actor:'operator',account,sha256:hash(bytes)};
  sensitive.push(source,capability);
  assert.equal((await mediaCall(source,'create',{...bound,size:bytes.length,mime:'image/png',kind:'sanitized',part_size:null})).status,201);
  assert.equal((await mf.dispatchFetch('https://approval.test/media-upload/'+source,{method:'PUT',
    headers:{'content-length':String(bytes.length),'cf-connecting-ip':opaque()},body:bytes})).status,200);
  assert.equal((await mediaCall(source,'complete',bound)).status,200);
  assert.equal((await mediaCall(capability,'preview',{...bound,media_id:hash(bytes),source,expires_at:Date.now()+600_000})).status,201);
  return capability;
}
const shown=capability=>mf.dispatchFetch('https://approval.test/m/'+capability,{headers:{'cf-connecting-ip':opaque()}});
const previewRow=async capability=>(await control('media',capability)).find(([k])=>k==='media')?.[1];
test('OpenSSL PSS to Worker; exact escaped body/context; parallel one-shot receipt',async()=>{
  const p=await person(),s=await session(p,{text:'<script>private & text</script>'}),response=await page(s),html=await response.text();
  assert.equal(response.headers.get('cache-control'),'no-store');assert.ok(response.headers.get('content-security-policy').includes("frame-ancestors 'none'"));
  assert.ok(html.includes('&lt;script&gt;private &amp; text&lt;/script&gt;'));assert.ok(html.includes('reply-1'));assert.ok(html.includes('2026-09-20T12:00:00Z'));
  assert.equal((await approve(s,p)).status,200);const stored=JSON.stringify(await control('session',s.token));assert.ok(!stored.includes(s.body.text));assert.ok(!stored.includes('publish_at'));
  const responses=await Promise.all(Array.from({length:12},()=>consume(s)));assert.equal(responses.filter(r=>r.status===200).length,1);
  const receipt=await responses.find(r=>r.status===200).json();assert.equal(receipt.digest,s.body.digest);assert.equal(receipt.job_id,s.body.job_id);assert.equal(receipt.approver,p.id);
  assert.equal((await page(s)).status,410);assert.equal((await consume(s)).status,404);
});
test('signature role/path/op/body/time/nonce binding and replay',async()=>{
  const p=await person();assert.equal((await signed('person',p.id,'unlock',{}, {role:'job'})).status,401);
  const good=wire('person',p.id,'unlock');assert.equal((await mf.dispatchFetch(good.url,good.init)).status,200);assert.equal((await mf.dispatchFetch(good.url,good.init)).status,409);
  for(const field of ['path','body','time','method']){const w=wire('person',p.id,'unlock');
    if(field==='path')w.url=w.url.replace('/unlock','/revoke');if(field==='body')w.init.body=' { }';
    if(field==='time')w.init.headers['x-thth-time']=String(Date.now()-61000);if(field==='method')w.init.method='PUT';
    assert.notEqual((await mf.dispatchFetch(w.url,w.init)).status,200);}
});
test('five wrong secrets across sessions lock person; unlock; reissue invalidates generation',async()=>{
  const p=await person(),sessions=await Promise.all(Array.from({length:5},()=>session(p)));
  for(const s of sessions)assert.equal((await approve(s,p,'short')).status,403);
  assert.equal((await(await signed('person',p.id,'status')).json()).locked,true);assert.equal((await page(sessions[0])).status,410);
  assert.equal((await signed('person',p.id,'unlock')).status,200);const fresh=await session(p);
  assert.equal((await signed('person',p.id,'set',p.data)).status,200);assert.equal((await page(fresh)).status,410);
});
test('same session at most five concurrent attempts',async()=>{
  const p=await person(),s=await session(p),nonce=await csrf(s);
  const r=await Promise.all(Array.from({length:10},()=>approve(s,p,opaque(),nonce)));assert.ok(r.every(x=>x.status!==200));assert.equal((await page(s)).status,410);
  assert.equal(new Map(await control('person',p.id)).get('person').failures,5);
});
test('revoke in cross-DO gap after secret verification blocks receipt',async()=>{
  const p=await person(),s=await session(p);await control('person',p.id,{revokeAfterCheck:true});await approve(s,p);assert.equal((await consume(s)).status,410);
});
test('TTL600 boundary scrubs body; unknown GET/HEAD does not persist',async()=>{
  const unknown={token:opaque()};assert.equal((await page(unknown)).status,410);assert.equal((await mf.dispatchFetch('https://approval.test/approve/'+unknown.token,{method:'HEAD'})).status,405);assert.deepEqual(await control('session',unknown.token),[]);
  const p=await person(),s=await session(p),row=new Map(await control('session',s.token)).get('session');await control('session',s.token,{clock:row.expires_at});assert.equal((await page(s)).status,410);assert.ok(!JSON.stringify(await control('session',s.token)).includes(s.body.text));
});
test('CSRF/origin/query rejection; incorrect read key; revoke approved receipt',async()=>{
  const p=await person(),s=await session(p);assert.equal((await approve(s,p,p.secret,opaque())).status,403);
  assert.equal((await mf.dispatchFetch('https://approval.test/approve/'+s.token+'?token=x')).status,400);
  assert.equal((await mf.dispatchFetch('https://approval.test/approve/'+s.token,{method:'POST',headers:{origin:'https://evil.test','content-type':'application/x-www-form-urlencoded'},body:new URLSearchParams({secret:p.secret,csrf:await csrf(s)})})).status,403);
  assert.equal((await approve(s,p)).status,200);assert.equal((await signed('session',s.token,'consume',{read_key:opaque()})).status,401);await signed('person',p.id,'revoke');assert.equal((await consume(s)).status,410);
});
test('storage failure does not acknowledge provisioning or approval',async()=>{
  const p=await person();await control('person',p.id,{fault:true});assert.equal((await signed('person',p.id,'set',p.data)).status,503);await control('person',p.id,{});
  const s=await session(p);await control('session',s.token,{fault:true});assert.notEqual((await approve(s,p)).status,200);await control('session',s.token,{});assert.equal((await consume(s)).status,404);
});
test('four flows poll 30 times each; existing OAuth IP quota remains available',async()=>{
  const p=await person(),sessions=await Promise.all(Array.from({length:4},()=>session(p))),ip=opaque();
  for(let n=0;n<30;n++)for(const s of sessions)assert.equal((await signed('session',s.token,'status',{read_key:s.readKey},{ip})).status,200);
  assert.equal((await mf.dispatchFetch('https://approval.test/relay/'+opaque(),{method:'POST',headers:{'content-type':'application/json','cf-connecting-ip':ip},body:JSON.stringify({read_key_hash:hash(opaque())})})).status,201);
});

test('product Python canonical signer is accepted by the real Worker',async()=>{
  const p=await person();await writeFile(join(directory,'relay-signer.key'),await readFile(key),{mode:0o600});
  const root=fileURLToPath(new URL('../..',import.meta.url));
  const script=`import json,sys
from thth import approval_relay as r
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
test('human operation labels distinguish delete, send and approve; body wraps on phones',async()=>{
  const p=await person();for(const [kind,label]of [['approve','原稿を承認'],['send','この内容を公開'],['retract','この投稿を削除']]){
    const s=await session(p,{kind}),html=await(await page(s)).text();assert.ok(html.includes(label));assert.ok(html.includes('white-space:pre-wrap'));assert.ok(html.includes('媒体'));
    const result=await(await approve(s,p)).text();assert.ok(result.includes('操作の完了'));assert.ok(!result.includes('公開の完了'));
  }
});

test('strict schema rejects coerced arrays, missing context, duplicate form fields',async()=>{
  const p=await person();assert.equal((await signed('person',p.id,'set',{...p.data,salt:[p.data.salt]})).status,400);
  const s=await session(p);
  for(const body of [{...s.body,account:['alpha']},{...s.body,context:null},{...s.body,extra:true}])assert.equal((await signed('session',opaque(),'create',body)).status,400);
  assert.equal((await signed('session',s.token,'consume',{read_key:[s.readKey]})).status,400);
  const nonce=await csrf(s);
  assert.equal((await mf.dispatchFetch('https://approval.test/approve/'+s.token,{method:'POST',headers:{origin:'https://approval.test','content-type':'application/x-www-form-urlencoded'},body:new URLSearchParams([['csrf',nonce],['secret',p.secret],['secret',p.secret]])})).status,400);
});
test('person correct attempt resets consecutive failures and successful receipt cannot be reinjected',async()=>{
  const p=await person();for(let n=0;n<4;n++){const s=await session(p);assert.equal((await approve(s,p,'wrong')).status,403);}
  const s=await session(p);assert.equal((await approve(s,p)).status,200);assert.equal(new Map(await control('person',p.id)).get('person').failures,0);
  assert.equal((await consume(s)).status,200);assert.equal((await signed('session',s.token,'create',s.body)).status,409);
  assert.equal((await approve(s,p,p.secret,opaque())).status,410);
});


test('long digest and URL metadata inherit wrapping without changing visible values',async()=>{
  const p=await person(),url='https://example.test/'+opaque().repeat(8);
  const s=await session(p,{context:{media:'threads',topic:null,options:null,reply_to:url,publish_at:null,target:null,reason:null}});
  const response=await page(s),html=await response.text();
  assert.equal(response.status,200);
  assert.match(html, /body\{[^}]*overflow-wrap:anywhere/);
  assert.ok(html.includes('<p>digest: '+s.body.digest+'</p>'));
  assert.ok(html.includes('<p>返信先: '+url+'</p>'));
  assert.ok(response.headers.get('content-security-policy').includes("default-src 'none'"));
});

test('account revoke invalidates A pending and approved sessions, preserves shared-person B',async()=>{
  const p=await person(),account='a'+randomBytes(8).toString('hex');
  const pending=await session(p,{account}),approved=await session(p,{account}),other=await session(p,{account:'b'+randomBytes(8).toString('hex')});
  assert.equal((await approve(approved,p)).status,200);
  assert.equal((await signed('account',account,'revoke',{}, {role:'job'})).status,401);
  assert.equal((await signed('account',account,'revoke')).status,200);
  assert.equal((await(await signed('account',account,'status')).json()).active,false);
  for(const s of [pending,approved]){
    assert.equal((await page(s)).status,410);
    assert.notEqual((await consume(s)).status,200);
    assert.ok(!JSON.stringify(await control('session',s.token)).includes(s.body.text));
  }
  assert.equal((await approve(other,p)).status,200);assert.equal((await consume(other)).status,200);
  assert.equal((await(await signed('person',p.id,'status')).json()).active,true);
  assert.equal((await signed('session',opaque(),'create',pending.body)).status,410);
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

test('account revoke after Person consume wins before final Account gate; shared-person B still works',async()=>{
  const p=await person(),account='r'+randomBytes(8).toString('hex'),a=await session(p,{account}),b=await session(p,{account:'b'+randomBytes(8).toString('hex')});
  assert.equal((await approve(a,p)).status,200);assert.equal((await approve(b,p)).status,200);
  await control('person',p.id,{revokeAccountAfterConsume:account});
  assert.notEqual((await consume(a)).status,200);assert.notEqual((await consume(a)).status,200);
  assert.equal((await consume(b)).status,200);
});
test('Account gate storage failure after Person consume cannot redeliver on retry',async()=>{
  const p=await person(),account='f'+randomBytes(8).toString('hex'),a=await session(p,{account});
  assert.equal((await approve(a,p)).status,200);await control('account',account,{fault:true});
  assert.notEqual((await consume(a)).status,200);await control('account',account,{fault:false});
  assert.notEqual((await consume(a)).status,200);
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

// 第 8 段: the approval page shows the attachments a human must look at.
const attachment=(over={})=>({index:1,role:'media',kind:'image',format:'jpeg',public_sha256:'ab'.repeat(32),
  public_size:1200,width:1200,height:800,duration:null,alt:'湯呑みに注いだ玉露',preview:opaque(),...over});
test('approval page renders images, video/audio/caption lines and typed attachments under img-src self',async()=>{
  const p=await person();
  const first=attachment(),second=attachment({index:2,format:'png',public_sha256:'cd'.repeat(32),width:640,height:640,alt:'茶葉の拡大'});
  const video=attachment({index:3,kind:'video',format:'mp4',public_sha256:'ef'.repeat(32),width:1920,height:1080,duration:72,alt:'湯を注ぐ',preview:null});
  const audio=attachment({index:4,kind:'audio',format:'m4a',public_sha256:'12'.repeat(32),width:null,height:null,duration:5.4,alt:'注ぐ音',preview:null});
  const caption=attachment({index:1,role:'caption',kind:'caption',format:'vtt',public_sha256:'34'.repeat(32),width:null,height:null,duration:null,alt:'ja',preview:null});
  const typed=JSON.stringify({attachments:[{type:'poll',options:['はい','いいえ']}],post_options:{},captions:[]});
  sensitive.push(first.preview,second.preview);
  const s=await session(p,{attachments:[first,second,video,audio,caption],typed});
  const response=await page(s),html=await response.text();
  assert.equal(response.status,200);
  assert.ok(response.headers.get('content-security-policy').includes("img-src 'self'"));
  assert.ok(response.headers.get('content-security-policy').includes("default-src 'none'"));
  assert.ok(html.includes('<img src="/m/'+first.preview+'" alt="湯呑みに注いだ玉露">'),'first preview img');
  assert.ok(html.includes('<img src="/m/'+second.preview+'" alt="茶葉の拡大">'),'second preview img');
  assert.ok(html.includes('<figcaption>添付 1: JPEG 1200×800 · sha abababababab · alt: 湯呑みに注いだ玉露</figcaption>'),html);
  assert.ok(html.includes('<figcaption>添付 2: PNG 640×640 · sha cdcdcdcdcdcd · alt: 茶葉の拡大</figcaption>'),html);
  assert.ok(html.includes('<p>添付 3: 動画 01:12 · sha efefefefefef · alt: 湯を注ぐ</p>'),html);
  assert.ok(html.includes('<p>添付 4: 音声 00:05 · sha 121212121212 · alt: 注ぐ音</p>'),html);
  assert.ok(html.includes('<p>字幕 (ja) sha 343434343434</p>'),html);
  assert.ok(html.includes('<p>型付き添付／公開設定</p><pre>'+typed.replaceAll('"','&quot;')+'</pre>'),html);
  assert.ok(html.indexOf('</pre>')<html.indexOf('<figure>'),'attachments follow the body');
  assert.ok(html.includes('<p>digest: '+s.body.digest+'</p>'));
  // The capability is page-only: never in status, receipt or the stored row after resolution.
  const pending=await(await signed('session',s.token,'status',{read_key:s.readKey})).json();
  assert.equal(JSON.stringify(pending).includes(first.preview),false);
  assert.equal(pending.attachments,undefined);
  assert.equal((await approve(s,p)).status,200);
  const receipt=await(await consume(s)).json();
  const text=JSON.stringify(receipt)+JSON.stringify(await control('session',s.token));
  for(const cap of [first.preview,second.preview])assert.equal(text.includes(cap),false,'capability leaked');
  assert.equal(text.includes('attachments'),false);assert.equal(text.includes('typed'),false);
  assert.equal(receipt.attachments,undefined);assert.equal(receipt.typed,undefined);
  assert.equal((await page(s)).status,410);
});
test('attachment rows are strictly validated and a rejected create registers nothing',async()=>{
  const p=await person();
  const base={person:p.id,job_id:opaque(),digest:hash('x'),account:'alpha',kind:'send',text:'本文',
    read_key_hash:hash(opaque()),context:{media:'threads',topic:null,options:null,reply_to:null,publish_at:null,target:null,reason:null}};
  const bad=[
    {attachments:[{...attachment(),extra:1}]},
    {attachments:[{...attachment(),preview:undefined}]},
    {attachments:Array.from({length:21},(_,i)=>attachment({index:i+1}))},
    {attachments:[attachment({public_sha256:'zz'.repeat(32)})]},
    {attachments:[attachment({kind:'video',duration:3})]},
    {attachments:[attachment({role:'poll'})]},
    {attachments:[attachment({index:0})]},
    {attachments:[attachment({public_size:-1})]},
    {attachments:[attachment({width:1.5})]},
    {attachments:[attachment({alt:'x'.repeat(4097)})]},
    {attachments:attachment()},
    {typed:'x'.repeat(16_385)},
    {typed:42},
  ];
  for(const [i,over] of bad.entries()){
    const token=opaque();
    assert.equal((await signed('session',token,'create',{...base,job_id:opaque(),...over})).status,400,'case '+i);
    assert.equal((await mf.dispatchFetch('https://approval.test/approve/'+token,{headers:{'cf-connecting-ip':opaque()}})).status,410,'case '+i);
  }
  assert.equal((await signed('session',opaque(),'create',{...base,job_id:opaque(),attachments:[],typed:''})).status,201);
});
test('attachment alt and typed JSON are escaped like the body',async()=>{
  const p=await person(),preview=opaque();sensitive.push(preview);
  const evil='"><script>alert(1)</script>';
  const s=await session(p,{attachments:[attachment({alt:evil,preview})],typed:'{"note":"'+evil+'"}'});
  const html=await(await page(s)).text();
  assert.equal(html.includes('<script>alert(1)</script>'),false);
  assert.ok(html.includes('<img src="/m/'+preview+'" alt="&quot;&gt;&lt;script&gt;alert(1)&lt;/script&gt;">'),html);
  assert.ok(html.includes('&quot;&gt;&lt;script&gt;alert(1)&lt;/script&gt;</figcaption>'),html);
});
// F1: a resolved or expired page must stop showing its images.
test('consuming an approval revokes every preview the page showed',async()=>{
  const p=await person(),first=await preview(),second=await preview();
  const s=await session(p,{attachments:[attachment({preview:first}),attachment({index:2,preview:second})]});
  for(const capability of [first,second])assert.equal((await shown(capability)).status,200,'preview not live before approval');
  assert.equal((await approve(s,p)).status,200);
  const receipt=await consume(s);assert.equal(receipt.status,200);
  for(const capability of [first,second])assert.equal((await shown(capability)).status,410,'preview outlived the consumed session');
});
test('an expired approval page revokes its previews before its own clock kills them',async()=>{
  const p=await person(),capability=await preview();
  const s=await session(p,{attachments:[attachment({preview:capability})]});
  assert.equal((await shown(capability)).status,200);
  // Only the session's clock moves: the grant is still live by the media
  // object's own clock, so a 410 here can only come from the revocation.
  await control('session',s.token,{clock:Date.now()+700_000});
  assert.equal((await page(s)).status,410);
  assert.equal((await shown(capability)).status,410,'preview outlived the expired session');
});
test('approval still completes when one preview object is already gone',async()=>{
  const p=await person(),live=await preview(),gone=await preview();
  const s=await session(p,{attachments:[attachment({preview:live}),attachment({index:2,preview:gone})]});
  const nonce=await csrf(s);
  await control('media',gone,{clock:Date.now()+700_000});
  assert.equal((await shown(gone)).status,410);
  assert.equal((await approve(s,p,p.secret,nonce)).status,200);
  assert.equal((await shown(live)).status,410,'the surviving preview was not revoked');
});
test('a preview never outlives the approval session that shows it',async()=>{
  const p=await person(),capability=await preview(),token=opaque(),readKey=opaque();
  const granted=(await previewRow(capability)).expires_at;
  // The session's own clock is 300 s behind, so its deadline lands before the
  // grant's: `create` must pull the grant back to it.
  await control('session',token,{clock:Date.now()-300_000});
  const text='本文 '+opaque();sensitive.push(token,readKey,text);
  const created=await signed('session',token,'create',{person:p.id,job_id:opaque(),digest:hash(text),account:'alpha',
    kind:'send',text,read_key_hash:hash(readKey),attachments:[attachment({preview:capability})],
    context:{media:'threads',topic:null,options:null,reply_to:null,publish_at:null,target:null,reason:null}});
  assert.equal(created.status,201);
  const deadline=(await created.json()).expires_at;
  assert.ok(deadline<granted,'fixture did not produce a session shorter than its grant');
  const row=await previewRow(capability);
  assert.equal(row.expires_at,deadline,'preview expiry was not bounded to the session');
  assert.equal(row.cleanup_at,deadline,'preview cleanup was not bounded to the session');
});

// 第 9 段の後追い: only session `create` carries attachments, so only it takes 96 KiB.
test('session create accepts a 70 KiB body and still refuses 100 KiB',async()=>{
  const p=await person();
  const wide=n=>({...attachment({index:n,alt:'ぬ'.repeat(4000),preview:null})});
  const build=count=>({person:p.id,job_id:opaque(),digest:hash('x'),account:'alpha',kind:'send',
    text:'本'.repeat(15_000),read_key_hash:hash(opaque()),typed:'{"note":"'+'x'.repeat(16_000)+'"}',
    attachments:Array.from({length:count},(_,i)=>wide(i+1)),
    context:{media:'threads',topic:null,options:null,reply_to:null,publish_at:null,target:null,reason:null}});
  const small=build(1),large=build(4);
  const bytes=body=>Buffer.byteLength(JSON.stringify(body));
  assert.ok(bytes(small)>71_680&&bytes(small)<98_304,'70 KiB band: '+bytes(small));
  assert.ok(bytes(large)>98_304,'over the cap: '+bytes(large));
  const token=opaque();
  assert.equal((await signed('session',token,'create',small)).status,201);
  assert.equal((await mf.dispatchFetch('https://approval.test/approve/'+token,{headers:{'cf-connecting-ip':opaque()}})).status,200);
  const refused=opaque();
  assert.equal((await signed('session',refused,'create',large)).status,503);
  assert.equal((await mf.dispatchFetch('https://approval.test/approve/'+refused,{headers:{'cf-connecting-ip':opaque()}})).status,410);
  // Every other JSON route keeps the 64 KiB default.
  assert.equal((await signed('person',p.id,'status',{note:'x'.repeat(70_000)})).status,503);
});
