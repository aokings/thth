import {test,before,after} from 'node:test';
import assert from 'node:assert/strict';
import {randomBytes,createHash,generateKeyPairSync,sign,constants} from 'node:crypto';
import {readFile,writeFile,mkdir,mkdtemp,rm,realpath} from 'node:fs/promises';
import {spawnSync} from 'node:child_process';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {Miniflare,Log,LogLevel,convertV4MiniflareOptions} from 'miniflare';
import {canonical} from '../src/media.js';
const opaque=()=>randomBytes(32).toString('base64url'),sha=b=>createHash('sha256').update(b).digest('hex');
const logs=[],secrets=[];let mf,privateKey,runtimeDirectory,options;
class Silent extends Log{constructor(){super(LogLevel.NONE);}log(v){logs.push(String(v));}}
before(async()=>{
  const pair=generateKeyPairSync('rsa',{modulusLength:3072});privateKey=pair.privateKey;
  const files=['test/media-harness.js','src/media-object.js','src/media.js','src/worker.js','src/index.js','src/relay.js','src/relay-object.js','src/approval.js','src/approval-object.js','src/deletion.js','src/deletion-object.js'];
  runtimeDirectory=await realpath(await mkdtemp(join(tmpdir(),'media-runtime-')));
  options=convertV4MiniflareOptions({modules:await Promise.all(files.map(async name=>({type:'ESModule',path:fileURLToPath(new URL('../'+name,import.meta.url)),contents:await readFile(new URL('../'+name,import.meta.url),'utf8')}))),compatibilityDate:'2026-09-01',cf:false,outboundService:()=>new Response('external_denied',{status:503}),log:new Silent(),bindings:{APPROVAL_PUBLIC_KEY:pair.publicKey.export({type:'spki',format:'der'}).toString('base64url')},durableObjects:{MEDIA_OBJECT:{className:'TestMedia',useSQLite:true},APPROVAL_ACCOUNT:{className:'ApprovalAccount',useSQLite:true},APPROVAL_PERSON:{className:'ApprovalPerson',useSQLite:true},APPROVAL_SESSION:{className:'ApprovalSession',useSQLite:true}},ratelimits:{APPROVAL_VERIFY_LIMIT:{namespace_id:'21202',simple:{limit:600,period:60}},APPROVAL_JOB_LIMIT:{namespace_id:'21203',simple:{limit:180,period:60}}},r2Buckets:['MEDIA_BUCKET']});options.resourcePersistencePath=join(runtimeDirectory,'storage');
  mf=new Miniflare(options);await mf.ready;
});
after(async()=>{await mf?.dispose();await rm(runtimeDirectory,{recursive:true,force:true});assert.equal(logs.filter(s=>secrets.some(x=>s.includes(x))).length,0,'secret in runtime logs');});
function wire(id,op,body,overrides={}){
  const path='/media/'+id+'/'+op,raw=JSON.stringify(body),time=Date.now(),nonce=opaque();
  const basePayload=canonical('POST',path,id,op,time,nonce,sha(raw));
  const payload=overrides.domain?[overrides.domain,...basePayload.split('\n').slice(1)].join('\n'):basePayload;
  const signature=sign('sha256',Buffer.from(overrides.canonical??payload),{key:privateKey,padding:constants.RSA_PKCS1_PSS_PADDING,saltLength:32}).toString('base64url');
  secrets.push(id,signature,nonce);
  return {url:'https://media.test'+path,init:{method:'POST',headers:{'content-type':'application/json','x-thth-time':String(time),'x-thth-nonce':nonce,'x-thth-signature':signature},body:raw}};
}
async function call(id,op,body,options){const w=wire(id,op,body,options);return mf.dispatchFetch(w.url,w.init);}
const control=async(id,body={})=>(await mf.dispatchFetch('https://media.test/__media/'+id,{method:'POST',body:JSON.stringify(body)})).json();
function data(bytes=Buffer.from('dynamic synthetic image '+opaque()),kind='source'){
 return {id:opaque(),bytes,body:{actor:'person',account:'alpha',sha256:sha(bytes),size:bytes.length,mime:'image/png',kind,part_size:null}};
}
const binding=f=>({actor:f.body.actor,account:f.body.account,sha256:f.body.sha256});
async function upload(f){return mf.dispatchFetch('https://media.test/media-upload/'+f.id,{method:'PUT',headers:{'content-length':String(f.bytes.length)},body:f.bytes});}
async function ready(f){assert.equal((await call(f.id,'create',f.body)).status,201);assert.equal((await upload(f)).status,200);assert.equal((await call(f.id,'complete',binding(f))).status,200);}

test('unknown public/read/upload do not persist records',async()=>{
 const f=data();
 for(const init of [{method:'GET'},{method:'HEAD'},{headers:{range:'bytes=0-2'}}])assert.equal((await mf.dispatchFetch('https://media.test/m/'+f.id,init)).status,410);
 assert.equal((await mf.dispatchFetch('https://media.test/m/invalid')).status,404);
 assert.equal((await upload(f)).status,404);assert.equal((await call(f.id,'read',binding(f))).status,404);
 assert.deepEqual(await control(f.id,{inspect:true}),{rows:[],alarm:null});
});
test('one file complete once, exact private bytes, owner binding and retired read',async()=>{
 const f=data();await ready(f);
 assert.equal((await call(f.id,'complete',binding(f))).status,409);
 assert.equal((await upload(f)).status,409);
 assert.equal((await call(f.id,'read',{...binding(f),account:'beta'})).status,404);
 assert.equal((await call(f.id,'read',{...binding(f),actor:'other'})).status,404);
 const read=await call(f.id,'read',binding(f));assert.equal(read.status,200);assert.ok(Buffer.from(await read.arrayBuffer()).equals(f.bytes),'private bytes mismatch');
 assert.equal((await mf.dispatchFetch('https://media.test/m/'+f.id)).status,404);
 assert.equal((await call(f.id,'ack',binding(f))).status,200);
 assert.equal((await call(f.id,'read',binding(f))).status,410);
});
test('signed domain/operation/body replay cannot confer authority',async()=>{
 const f=data(),w=wire(f.id,'create',f.body);assert.equal((await mf.dispatchFetch(w.url,w.init)).status,201);
 assert.equal((await mf.dispatchFetch(w.url,w.init)).status,409);
 const other=data();assert.equal((await call(other.id,'create',other.body,{domain:'thth-approval-v1'})).status,401);
 const changed=wire(other.id,'create',other.body);changed.init.body=JSON.stringify({...other.body,account:'beta'});assert.equal((await mf.dispatchFetch(changed.url,changed.init)).status,401);
 assert.equal((await control(other.id)).length,0);
});
test('source cannot be public and preview has separate capability/owner/TTL',async()=>{
 const f=data(undefined,'sanitized');await ready(f);const cap=opaque(),expires=Date.now()+600000;
 const body={...binding(f),media_id:f.body.sha256,source:f.id,expires_at:expires};
 assert.equal((await call(opaque(),'preview',{...body,account:'beta'})).status,404);
 assert.equal((await call(cap,'preview',body)).status,201);
 const get=await mf.dispatchFetch('https://media.test/m/'+cap);assert.equal(get.status,200);assert.equal(get.headers.get('cache-control'),'no-store');assert.ok(Buffer.from(await get.arrayBuffer()).equals(f.bytes),'public bytes mismatch');
 const head=await mf.dispatchFetch('https://media.test/m/'+cap,{method:'HEAD'});assert.equal(head.status,200);assert.equal(await head.text(),'');
 await control(cap,{clock:expires});
 for(const method of ['GET','HEAD'])assert.equal((await mf.dispatchFetch('https://media.test/m/'+cap,{method})).status,410);
 assert.equal((await mf.dispatchFetch('https://media.test/m/'+cap,{headers:{range:'bytes=0-2'}})).status,410);
});
test('simultaneous complete has exactly one success',async()=>{
 const f=data();assert.equal((await call(f.id,'create',f.body)).status,201);assert.equal((await upload(f)).status,200);
 const result=await Promise.all(Array.from({length:8},()=>call(f.id,'complete',binding(f))));assert.equal(result.filter(r=>r.status===200).length,1);
});
test('provider acknowledgement is bound and extends only once from acknowledgement',async()=>{
 const f=data(undefined,'sanitized');await ready(f);const cap=opaque();
 const created=await call(cap,'provider',{...binding(f),media_id:f.body.sha256,source:f.id,expires_at:Date.now()+600000});assert.equal(created.status,201);
 const value=await created.json(),body={...binding(f),media_id:f.body.sha256,purpose:'provider',generation:value.generation};
 assert.equal((await call(cap,'published',{...body,purpose:'preview'})).status,400);
 const ack=await call(cap,'published',body);assert.equal(ack.status,200);const expiry=(await ack.json()).expires_at;
 assert.equal((await call(cap,'published',body)).status,409);
 await control(cap,{clock:expiry});assert.equal((await mf.dispatchFetch('https://media.test/m/'+cap)).status,410);
});


test('R2 await cannot commit through expiry, generation change or revocation',async()=>{
 for(const fault of ['expire','rotate','revoke']){
  const f=data();f.body.account='fault'+fault;
  assert.equal((await call(f.id,'create',f.body)).status,201);await control(f.id,{afterPut:fault});
  assert.equal((await upload(f)).status,410);
  assert.notEqual((await call(f.id,'complete',binding(f))).status,200);
  assert.notEqual((await call(f.id,'read',binding(f))).status,200);
 }
});
test('same public hash separate grants have independent cleanup',async()=>{
 const bytes=Buffer.from('same synthetic '+opaque()),a=data(bytes,'sanitized'),b=data(bytes,'sanitized');b.body.actor='other';b.body.account='beta';await ready(a);await ready(b);
 const ca=opaque(),cb=opaque(),expiry=Date.now()+500000;
 for(const [f,cap] of [[a,ca],[b,cb]])assert.equal((await call(cap,'preview',{...binding(f),media_id:f.body.sha256,source:f.id,expires_at:expiry})).status,201);
 await control(ca,{clock:expiry,alarm:true});assert.equal((await mf.dispatchFetch('https://media.test/m/'+ca)).status,410);
 const keep=await mf.dispatchFetch('https://media.test/m/'+cb);assert.equal(keep.status,200);assert.ok(Buffer.from(await keep.arrayBuffer()).equals(bytes),'unrelated bytes changed');
 const expired=await control(ca,{clock:expiry});assert.deepEqual(expired,[]);assert.deepEqual(await control(ca,{inspect:true}),{rows:[],alarm:null});
});
test('late ack cannot revive expiry; explicit invalidation takes effect on every read method',async()=>{
 for(const mode of ['expire','invalidate']){
  const f=data(undefined,'sanitized');await ready(f);const cap=opaque();
  const created=await call(cap,'provider',{...binding(f),media_id:f.body.sha256,source:f.id,expires_at:Date.now()+600000});const value=await created.json();
  const bindingBody={...binding(f),media_id:f.body.sha256,purpose:'provider',generation:value.generation};
  if(mode==='expire')await control(cap,{clock:value.expires_at});else assert.equal((await call(cap,'invalidate',bindingBody)).status,200);
  assert.equal((await call(cap,'published',bindingBody)).status,410);
  for(const init of [{},{method:'HEAD'},{headers:{range:'bytes=0-1'}}])assert.equal((await mf.dispatchFetch('https://media.test/m/'+cap,init)).status,410);
 }
});
test('public range is bounded and returns original byte interval',async()=>{
 const f=data(undefined,'sanitized');await ready(f);const cap=opaque();assert.equal((await call(cap,'preview',{...binding(f),media_id:f.body.sha256,source:f.id,expires_at:Date.now()+500000})).status,201);
 const get=await mf.dispatchFetch('https://media.test/m/'+cap,{headers:{range:'bytes=2-5'}});assert.equal(get.status,206);assert.ok(Buffer.from(await get.arrayBuffer()).equals(f.bytes.subarray(2,6)),'range mismatch');
 assert.equal((await mf.dispatchFetch('https://media.test/m/'+cap,{headers:{range:'bytes=0-1,3-4'}})).status,416);
});
test('100MB boundary uses real multipart, part retry and complete only once',async()=>{
 const partSize=5*1024*1024,size=100000001,f=data();f.body.size=size;f.body.part_size=partSize;
 assert.equal((await call(f.id,'create',f.body)).status,201);
 const part=Buffer.alloc(partSize,73),count=Math.ceil(size/partSize);
 for(let n=1;n<=count;n++){
  const bytes=part.subarray(0,Math.min(partSize,size-(n-1)*partSize));
  const put=()=>mf.dispatchFetch('https://media.test/media-upload/'+f.id+'/'+n,{method:'PUT',headers:{'content-length':String(bytes.length)},body:bytes});
  assert.equal((await put()).status,200);if(n===1)assert.equal((await put()).status,200);
 }
 const completed=await Promise.all([call(f.id,'complete',binding(f)),call(f.id,'complete',binding(f))]);assert.equal(completed.filter(x=>x.status===200).length,1);
 const read=await call(f.id,'read',binding(f));assert.equal(read.status,200);let received=0;for await(const chunk of read.body)received+=chunk.length;assert.equal(received,size);
});


test('actual Python/OpenSSL media canonical signature is verified in workerd',async()=>{
 const apps=join(runtimeDirectory,'apps');await mkdir(apps,{mode:0o700});
 await writeFile(join(apps,'relay-signer.key'),privateKey.export({type:'pkcs8',format:'pem'}),{mode:0o600});
 const f=data();
 const script=`import json,sys,socket,subprocess
from pathlib import Path
socket.socket.connect=lambda *a,**k: (_ for _ in ()).throw(OSError('network_denied'))
from thth import media_relay
r=media_relay.request_for(sys.argv[1],'create',json.loads(sys.argv[2]))
print(json.dumps({'url':r.full_url,'headers':dict(r.header_items()),'body':r.data.decode()}))`;
 const child=spawnSync('/opt/homebrew/Caskroom/miniforge/base/bin/python',['-B','-c',script,f.id,JSON.stringify(f.body)],{cwd:fileURLToPath(new URL('../..',import.meta.url)),env:{PATH:'/usr/bin:/bin',HOME:runtimeDirectory,THTH_APPS_DIR:apps,THTH_ROOT:join(runtimeDirectory,'root'),PYTHONDONTWRITEBYTECODE:'1',PYTHONNOUSERSITE:'1'},encoding:'utf8',timeout:10000});
 assert.equal(child.status,0,'Python signing failed');assert.equal(child.stderr,'');
 const w=JSON.parse(child.stdout),res=await mf.dispatchFetch(w.url,{method:'POST',headers:w.headers,body:w.body});assert.equal(res.status,201);
});
test('real runtime restart retains ready bytes and publication ack cannot extend again',async()=>{
 const f=data(undefined,'sanitized');await ready(f);const cap=opaque();
 const made=await call(cap,'provider',{...binding(f),media_id:f.body.sha256,source:f.id,expires_at:Date.now()+600000});const grant=await made.json();
 const b={...binding(f),media_id:f.body.sha256,purpose:'provider',generation:grant.generation};const first=await call(cap,'published',b);assert.equal(first.status,200);const expiry=(await first.json()).expires_at;
 await mf.dispose();mf=new Miniflare(options);await mf.ready;
 const get=await mf.dispatchFetch('https://media.test/m/'+cap);assert.equal(get.status,200);assert.ok(Buffer.from(await get.arrayBuffer()).equals(f.bytes),'restart bytes mismatch');
 assert.equal((await call(cap,'published',b)).status,409);
 const row=(await control(cap)).find(([k])=>k==='media')[1];assert.equal(row.expires_at,expiry);
});


test('multipart allocation and private/public reads recheck account after R2 await',async()=>{
 for(const operation of ['createMultipartUpload','read','view']){
  const f=data(undefined,operation==='view'?'sanitized':'source');f.body.account='after'+operation;
  if(operation==='createMultipartUpload'){
   f.body.size=100000001;f.body.part_size=5*1024*1024;
   await control(f.id,{afterR2:{operation,action:'revoke'}});
   assert.equal((await call(f.id,'create',f.body)).status,410);
   assert.notEqual((await upload(f)).status,200);
  }else{
   await ready(f);let id=f.id;
   if(operation==='view'){
    id=opaque();assert.equal((await call(id,'preview',{...binding(f),media_id:f.body.sha256,source:f.id,expires_at:Date.now()+500000})).status,201);
   }
   await control(id,{afterR2:{operation:'get',action:'revoke'}});
   const result=operation==='view'?await mf.dispatchFetch('https://media.test/m/'+id):await call(id,'read',binding(f));
   assert.equal(result.status,410);
  }
 }
});
test('C10 video provisional TTL differs from image without enabling a video provider',async()=>{
 for(const [mime,ttl] of [['image/png',600000],['video/mp4',1800000]]){
  const f=data(undefined,'sanitized');f.body.mime=mime;await ready(f);const id=opaque(),now=Date.now();
  await control(id,{clock:now});
  const created=await call(id,'provider',{...binding(f),media_id:f.body.sha256,source:f.id,expires_at:now+500000});
  assert.equal(created.status,201);const result=await created.json();assert.equal(result.expires_at,now+ttl);
  await control(id,{clock:now+ttl-1});assert.equal((await mf.dispatchFetch('https://media.test/m/'+id)).status,200);
  await control(id,{clock:now+ttl});assert.equal((await mf.dispatchFetch('https://media.test/m/'+id)).status,410);
 }
});


test('media signature cannot provision an approval person',async()=>{
 const f=data(),w=wire(f.id,'create',f.body);
 const result=await mf.dispatchFetch('https://media.test/approval/person/person/set',w.init);
 assert.equal(result.status,401);
});

test('real Python prepared sanitizer uploads exact bytes through local Worker/R2 and acknowledges publication',async()=>{
 const apps=join(runtimeDirectory,'e2e-apps');await mkdir(apps,{mode:0o700});
 await writeFile(join(apps,'relay-signer.key'),privateKey.export({type:'pkcs8',format:'pem'}),{mode:0o600});
 const origin=String(await mf.ready).replace(/\/$/,'');
 const script=`import json,os,secrets,socket,struct,zlib,urllib.request
from pathlib import Path
real_connect=socket.socket.connect
def local_connect(self,address):
 if isinstance(address,tuple) and address[0] not in ('127.0.0.1','localhost','::1'):raise OSError('external_network_denied')
 return real_connect(self,address)
socket.socket.connect=local_connect
from thth import media,media_relay
root=Path(os.environ['HOME'])/'e2e-repo';root.mkdir()
def chunk(k,v):return struct.pack('>I',len(v))+k+v+struct.pack('>I',zlib.crc32(k+v)&0xffffffff)
raw=b'\\x89PNG\\r\\n\\x1a\\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',1,1,8,2,0,0,0))+chunk(b'tEXt',b'GPS\\x00'+secrets.token_bytes(12))+chunk(b'IDAT',zlib.compress(b'\\x00'+secrets.token_bytes(3)))+chunk(b'IEND',b'')
(root/'a.png').write_bytes(raw)
client=media_relay.MediaRelay('e2e','person')
with media.prepare(str(root),{'media':[{'file':'a.png','alt':'generated'}]},'threads') as (manifest,items):
 item=items[0];expected=b''.join(item.chunks());assert expected!=raw
 source=client.upload_prepared(item)
 preview=client.grant(item,source,purpose='preview')
 provider=client.grant(item,source)
 for grant in (preview,provider):
  with urllib.request.urlopen(grant['url'],timeout=10) as response:assert response.read()==expected
 result=client.result(provider,published=True);assert result['status']=='acknowledged'
 try:client.result(provider,published=True)
 except urllib.error.HTTPError as error:assert error.code==409
 else:raise AssertionError('ack replay accepted')
# Real Graph wire consumes the public grant from the actual local Worker/R2.
import http.server,threading
from thth import accounts,inflight,media_delivery,httpsafe
from thth.adapters import base,threads
seen=[]
class Graph(http.server.BaseHTTPRequestHandler):
 def log_message(self,*args):pass
 def reply(self,value):
  raw=json.dumps(value).encode();self.send_response(200);self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
 def do_GET(self):self.reply({'status':'FINISHED'})
 def do_POST(self):
  form=__import__('urllib.parse',fromlist=['parse_qs']).parse_qs(self.rfile.read(int(self.headers['Content-Length'])).decode())
  if self.path.endswith('/threads'):
   with httpsafe.urlopen(form['image_url'][0],timeout=10) as response:received=response.read()
   assert received==expected and received!=raw
   assert form['alt_text']==['generated']
   seen.append('image');self.reply({'id':'555'})
  else:seen.append('publish');self.reply({'id':'999'})
server=http.server.ThreadingHTTPServer(('127.0.0.1',0),Graph);worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
try:
 cfg={'account':'e2e','media':'threads','repo_dir':str(root)}
 state=accounts.state_dir_for('e2e');inflight.write(state,file='fixture',started='2030-01-01T00:00:00+09:00')
 adapter=threads.ThreadsAdapter(base_url='http://127.0.0.1:'+str(server.server_port),user_id='123',access_token=secrets.token_urlsafe(30),wait_seconds=0)
 fm={'media':[{'file':'a.png','alt':'generated'}]}
 value=media_delivery.publish(adapter,base.Post(''),cfg=cfg,fm=fm,manifest=media.manifest_for(fm,cfg),state_dir=state)
 assert value.post_id=='999' and seen==['image','publish']
 assert inflight.read(state)['media']['publication_ack']=='acknowledged'
finally:server.shutdown();server.server_close();worker.join(3)
print(json.dumps({'sanitized':True,'exact_bytes':True,'preview':True,'provider':True,'ack_once':True,'threads_graph':True}))`;
 const {spawn}=await import('node:child_process');
 const child=spawn('/opt/homebrew/Caskroom/miniforge/base/bin/python',['-B','-c',script],{cwd:fileURLToPath(new URL('../..',import.meta.url)),env:{PATH:'/usr/bin:/bin',HOME:runtimeDirectory,THTH_APPS_DIR:apps,THTH_ROOT:join(runtimeDirectory,'e2e-root'),THTH_MEDIA_BASE_URL:origin,THTH_TEST_ALLOW_HTTP:'1',TMPDIR:runtimeDirectory,PYTHONDONTWRITEBYTECODE:'1',PYTHONNOUSERSITE:'1'},stdio:['ignore','pipe','pipe']});
 const result=await new Promise(resolve=>{let out='',err='';const timer=setTimeout(()=>child.kill('SIGKILL'),20000);child.stdout.on('data',d=>out+=d);child.stderr.on('data',d=>err+=d);child.on('close',code=>{clearTimeout(timer);resolve({code,out,err});});});
 assert.equal(result.code,0,'Python local media flow failed: '+result.err.split('\n').filter(s=>/^\w+(?:Error|Exception):/.test(s)).map(s=>s.split(':')[0]).join(','));
 assert.equal(result.err,'');assert.deepEqual(JSON.parse(result.out),{sanitized:true,exact_bytes:true,preview:true,provider:true,ack_once:true,threads_graph:true});
});


test('R2 response loss and SQLite failure do not acknowledge completion or silently replay a single upload',async()=>{
 const f=data();assert.equal((await call(f.id,'create',f.body)).status,201);
 await control(f.id,{afterR2:{operation:'put',action:'fail'}});
 assert.equal((await upload(f)).status,503);
 assert.equal((await upload(f)).status,404);
 assert.equal((await call(f.id,'complete',binding(f))).status,410);
 const good=data();assert.equal((await call(good.id,'create',good.body)).status,201);assert.equal((await upload(good)).status,200);
 await control(good.id,{failSave:true});assert.equal((await call(good.id,'complete',binding(good))).status,503);
 const state=await call(good.id,'status',binding(good));assert.equal((await state.json()).status,'uploaded');
 assert.equal((await call(good.id,'complete',binding(good))).status,200);
});
test('public grant copy failure never exposes partially committed bytes',async()=>{
 const f=data(undefined,'sanitized');await ready(f);const cap=opaque();
 await control(cap,{afterR2:{operation:'put',action:'fail'}});
 const body={...binding(f),media_id:f.body.sha256,source:f.id,expires_at:Date.now()+500000};
 assert.equal((await call(cap,'provider',body)).status,503);
 assert.equal((await mf.dispatchFetch('https://media.test/m/'+cap)).status,404);
 assert.equal((await call(cap,'provider',body)).status,409);
});


test('strict upload schema and byte-count failures cannot create a readable object',async()=>{
 for(const change of [{size:0},{size:1.5},{size:'1'},{mime:'text/html'},{kind:'provider'},{actor:'a@b'},{account:[]},{sha256:'invalid'},{part_size:5242880},{extra:true}]){
  const f=data();assert.equal((await call(f.id,'create',{...f.body,...change})).status,400);
  assert.deepEqual(await control(f.id,{inspect:true}),{rows:[],alarm:null});
 }
 for(const change of [-1,1]){
  const f=data();assert.equal((await call(f.id,'create',f.body)).status,201);
  const bytes=change<0?f.bytes.subarray(1):Buffer.concat([f.bytes,Buffer.from('x')]);
  const result=await mf.dispatchFetch('https://media.test/media-upload/'+f.id,{method:'PUT',headers:{'content-length':String(bytes.length)},body:bytes});
  assert.equal(result.status,503);assert.equal((await call(f.id,'read',binding(f))).status,410);
 }
});


test('alarm failure rolls back initialization and publication extension',async()=>{
 const f=data();await control(f.id,{failAlarm:true});assert.equal((await call(f.id,'create',f.body)).status,503);
 assert.deepEqual(await control(f.id,{inspect:true}),{rows:[],alarm:null});
 const good=data(undefined,'sanitized');await ready(good);const cap=opaque();
 const made=await call(cap,'provider',{...binding(good),media_id:good.body.sha256,source:good.id,expires_at:Date.now()+500000});const value=await made.json();
 await control(cap,{failAlarm:true});const body={...binding(good),media_id:good.body.sha256,purpose:'provider',generation:value.generation};
 assert.equal((await call(cap,'published',body)).status,503);
 const row=(await control(cap)).find(([k])=>k==='media')[1];assert.equal(row.expires_at,value.expires_at);assert.equal(row.acked_at,undefined);
 assert.equal((await call(cap,'published',body)).status,200);
});


test('cleanup preserves multipart identity across abort and delete failures then retries',async()=>{
 const f=data();f.body.size=100000001;f.body.part_size=5*1024*1024;
 assert.equal((await call(f.id,'create',f.body)).status,201);
 const part=Buffer.alloc(f.body.part_size,7);
 assert.equal((await mf.dispatchFetch('https://media.test/media-upload/'+f.id+'/1',{method:'PUT',headers:{'content-length':String(part.length)},body:part})).status,200);
 const first=(await control(f.id)).find(([k])=>k==='media')[1];
 const aborted=await control(f.id,{clock:first.cleanup_at,failR2:'abort',alarm:true,inspect:true});
 let row=aborted.rows.find(([k])=>k==='media')[1];assert.equal(row.upload_id,first.upload_id);assert.equal(row.status,'retired');assert.equal(row.multipart_closed,undefined);assert.equal(aborted.alarm,first.cleanup_at+60000);
 const deleted=await control(f.id,{clock:first.cleanup_at+60000,failR2:'delete',alarm:true,inspect:true});
 row=deleted.rows.find(([k])=>k==='media')[1];assert.equal(row.multipart_closed,'aborted');assert.equal(deleted.alarm,first.cleanup_at+120000);
 assert.deepEqual(await control(f.id,{clock:first.cleanup_at+120000,failR2:'abort',alarm:true,inspect:true}),{rows:[],alarm:null});
});
test('known completed multipart cleanup never repeats abort',async()=>{
 const f=data();f.body.size=100000001;f.body.part_size=5*1024*1024;
 assert.equal((await call(f.id,'create',f.body)).status,201);
 for(let n=1;n<=Math.ceil(f.body.size/f.body.part_size);n++){
  const part=Buffer.alloc(Math.min(f.body.part_size,f.body.size-(n-1)*f.body.part_size),8);
  assert.equal((await mf.dispatchFetch('https://media.test/media-upload/'+f.id+'/'+n,{method:'PUT',headers:{'content-length':String(part.length)},body:part})).status,200);
 }
 assert.equal((await call(f.id,'complete',binding(f))).status,200);
 const row=(await control(f.id)).find(([k])=>k==='media')[1];assert.equal(row.multipart_closed,'completed');
 assert.deepEqual(await control(f.id,{clock:row.cleanup_at,failR2:'abort',alarm:true,inspect:true}),{rows:[],alarm:null});
});
test('lost empty multipart allocation cannot accept parts and malformed public routes are 404',async()=>{
 const f=data();f.body.size=100000001;f.body.part_size=5*1024*1024;
 await control(f.id,{afterR2:{operation:'createMultipartUpload',action:'fail'}});
 assert.equal((await call(f.id,'create',f.body)).status,503);
 const row=(await control(f.id)).find(([k])=>k==='media')[1];assert.equal(row.status,'initializing');assert.equal(row.upload_id,undefined);
 const part=Buffer.alloc(f.body.part_size,1);
 assert.equal((await mf.dispatchFetch('https://media.test/media-upload/'+f.id+'/1',{method:'PUT',headers:{'content-length':String(part.length)},body:part})).status,409);
 assert.deepEqual(await control(f.id,{clock:row.cleanup_at,alarm:true,inspect:true}),{rows:[],alarm:null});
 for(const suffix of ['invalid',opaque()+'?query=1','%41'+opaque().slice(1)])for(const init of [{},{method:'HEAD'},{headers:{range:'bytes=0-1'}}])assert.equal((await mf.dispatchFetch('https://media.test/m/'+suffix,init)).status,404);
});

test('workerd outbound service canary denies external fetch',async()=>{const response=await mf.dispatchFetch('https://media.test/__media-egress-canary');assert.equal(response.status,503);assert.equal(await response.text(),'external_denied');});

test('real Python video multipart retains bytes and VIDEO publication through Worker R2',async()=>{
 const apps=join(runtimeDirectory,'video-apps');await mkdir(apps,{mode:0o700});
 await writeFile(join(apps,'relay-signer.key'),privateKey.export({type:'pkcs8',format:'pem'}),{mode:0o600});
 const origin=String(await mf.ready).replace(/\/$/,'');
 const script=`import hashlib,http.server,json,os,secrets,struct,threading,time,urllib.parse
from pathlib import Path
from thth import accounts,inflight,media,media_delivery,media_relay,httpsafe
from thth.adapters import base,threads
from tests.test_v213_mastodon_media import video_timing
root=Path(os.environ['HOME'])/'video-repo';root.mkdir()
raw=video_timing([(30,1000)],scale=30000);at=raw.index(b'mdat')-4
prefix=raw[:at];size=100_000_001
with (root/'v.mp4').open('wb') as f:
 f.write(prefix+struct.pack('>I',size-len(prefix))+b'mdat');f.truncate(size)
fm={'media':[{'file':'v.mp4','alt':'generated video'}]};cfg={'account':'video-e2e','media':'threads','repo_dir':str(root)}
manifest=media.manifest_for(fm,cfg);expected=manifest['files'][0]['public_sha256'];assert manifest['files'][0]['public_size']==size
seen=[];grants=[]
original_grant=media_relay.MediaRelay.grant
def grant(self,item,source,**kw):
 start=time.time()*1000;value=original_grant(self,item,source,**kw)
 assert start+1_700_000<value['expires_at']<=time.time()*1000+1_800_000
 grants.append(value);return value
media_relay.MediaRelay.grant=grant
class Graph(http.server.BaseHTTPRequestHandler):
 def log_message(self,*args):pass
 def reply(self,value):
  b=json.dumps(value).encode();self.send_response(200);self.send_header('Content-Length',str(len(b)));self.end_headers();self.wfile.write(b)
 def do_GET(self):self.reply({'status':'FINISHED'})
 def do_POST(self):
  form=urllib.parse.parse_qs(self.rfile.read(int(self.headers['Content-Length'])).decode())
  if self.path.endswith('/threads'):
   assert form['media_type']==['VIDEO'] and form['alt_text']==['generated video'] and 'image_url' not in form
   h=hashlib.sha256();n=0
   with httpsafe.urlopen(form['video_url'][0],timeout=10) as response:
    while True:
     b=response.read(1024*1024)
     if not b:break
     n+=len(b);h.update(b)
   assert n==size and h.hexdigest()==expected
   seen.append('video');self.reply({'id':'551'})
  else:seen.append('publish');self.reply({'id':'991'})
server=http.server.ThreadingHTTPServer(('127.0.0.1',0),Graph);worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
try:
 state=accounts.state_dir_for('video-e2e');inflight.write(state,file='fixture',started='2030-01-01T00:00:00+09:00')
 adapter=threads.ThreadsAdapter(base_url='http://127.0.0.1:'+str(server.server_port),user_id='123',access_token=secrets.token_urlsafe(30),wait_seconds=0)
 result=media_delivery.publish(adapter,base.Post(''),cfg=cfg,fm=fm,manifest=manifest,state_dir=state)
 assert result.post_id=='991' and result.media[0]['kind']=='video' and seen==['video','publish']
 assert len(grants)==1 and inflight.read(state)['media']['publication_ack']=='acknowledged'
finally:server.shutdown();server.server_close();worker.join(3)
print(json.dumps({'multipart_bytes':size,'same_public_sha':True,'video_wire':True,'provider_1800':True,'published':True}))`;
 const {spawn}=await import('node:child_process');
 const child=spawn('/opt/homebrew/Caskroom/miniforge/base/bin/python',['-B','-c',script],{cwd:fileURLToPath(new URL('../..',import.meta.url)),env:{PATH:'/usr/bin:/bin',HOME:runtimeDirectory,THTH_APPS_DIR:apps,THTH_ROOT:join(runtimeDirectory,'video-root'),THTH_MEDIA_BASE_URL:origin,THTH_TEST_ALLOW_HTTP:'1',TMPDIR:runtimeDirectory,PYTHONDONTWRITEBYTECODE:'1',PYTHONNOUSERSITE:'1'},stdio:['ignore','pipe','pipe']});
 const result=await new Promise(resolve=>{let out='',err='';const timer=setTimeout(()=>child.kill('SIGKILL'),60000);child.stdout.on('data',d=>out+=d);child.stderr.on('data',d=>err+=d);child.on('close',code=>{clearTimeout(timer);resolve({code,out,err});});});
 assert.equal(result.code,0,'Python video bridge failed: '+result.err.split('\n').filter(s=>/^\w+(?:Error|Exception):/.test(s)).map(s=>s.split(':')[0]).join(','));
 assert.equal(result.err,'');assert.deepEqual(JSON.parse(result.out),{multipart_bytes:100000001,same_public_sha:true,video_wire:true,provider_1800:true,published:true});
});

test('unsatisfiable and unsafe ranges refuse without returning private bytes',async()=>{
 const f=data(Buffer.alloc(60,65),'sanitized');await ready(f);const cap=opaque();
 assert.equal((await call(cap,'preview',{...binding(f),media_id:f.body.sha256,source:f.id,expires_at:Date.now()+500000})).status,201);
 for(const range of ['bytes=60-','bytes=70-','bytes=70-80','bytes=5-4','bytes=9007199254740992-','bytes=0-9007199254740992']){
  for(const method of ['GET','HEAD']){
   const response=await mf.dispatchFetch('https://media.test/m/'+cap,{method,headers:{range}});
   assert.equal(response.status,416);assert.notEqual(response.headers.get('content-type'),'image/png');
  }
 }
 for(const range of ['bytes=59-','bytes=59-100','bytes=0-0']){
  const response=await mf.dispatchFetch('https://media.test/m/'+cap,{headers:{range}});
  assert.equal(response.status,206);assert.equal((await response.arrayBuffer()).byteLength,1);
 }
});
