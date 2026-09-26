// 3.14.0 段 2 遠くの道（/api/v1/*）の Worker 側。鍵の照合（VM が sync で押し上げた表）・期限・回数制限・
// 20 秒の保留（試験では短く）・202 と /api/v1/result・結果は 1 回きり・3.13.0 の VM の sync・記録しないこと。
import {test,before,after} from 'node:test';
import assert from 'node:assert/strict';
import {randomBytes,createHash,pbkdf2Sync} from 'node:crypto';
import {spawnSync} from 'node:child_process';
import {mkdtemp,writeFile,readFile,rm,realpath} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {Miniflare,Log,LogLevel,convertV4MiniflareOptions} from 'miniflare';
import {canonical} from '../src/person.js';
const opaque=()=>randomBytes(32).toString('base64url'),hash=s=>createHash('sha256').update(s).digest('hex');
const logs=[],sensitive=[];
class SilentLog extends Log {constructor(){super(LogLevel.NONE);}log(value){logs.push(String(value));}}
let mf,directory,key;
const ORIGIN='https://api.test';
const HOLD=1500;
const openssl=process.platform==='darwin'?'/opt/homebrew/bin/openssl':'/usr/bin/openssl';
function run(args,input){const r=spawnSync(openssl,args,{input,env:{PATH:'/usr/bin:/bin',OPENSSL_CONF:'/dev/null'}});assert.equal(r.status,0,'OpenSSL failed');return r.stdout;}
before(async()=>{
  directory=await realpath(await mkdtemp(join(tmpdir(),'thth-api-')));key=join(directory,'ephemeral.key');
  const pem=run(['genpkey','-algorithm','RSA','-pkeyopt','rsa_keygen_bits:3072']);sensitive.push(pem.toString());await writeFile(key,pem,{mode:0o600});
  const publicKey=run(['pkey','-in',key,'-pubout','-outform','DER']).toString('base64url');
  const files=['test/person-harness.js','src/worker.js','src/media.js','src/media-object.js','src/index.js','src/relay.js','src/relay-object.js','src/person.js','src/person-object.js','src/deletion.js','src/deletion-object.js','src/invite.js','src/invite-object.js','src/activity.js','src/api.js'];
  const modules=await Promise.all(files.map(async name=>{const path=fileURLToPath(new URL('../'+name,import.meta.url));return{type:'ESModule',path,contents:await readFile(path,'utf8')};}));
  mf=new Miniflare(convertV4MiniflareOptions({modules,modulesRoot:fileURLToPath(new URL('..',import.meta.url)),compatibilityDate:'2026-09-01',cf:false,
    log:new SilentLog(),handleStructuredLogs:item=>logs.push(JSON.stringify(item)),bindings:{RELAY_PUBLIC_KEY:publicKey,API_HOLD_MS:String(HOLD)},
    durableObjects:{AUTH_RELAY:{className:'AuthRelay',useSQLite:true},PERSON:{className:'TestPerson',useSQLite:true},ACCOUNT:{className:'TestAccount',useSQLite:true},DELETION_INBOX:{className:'TestDeletion',useSQLite:true},MEDIA_OBJECT:{className:'TestMedia',useSQLite:true}},r2Buckets:['MEDIA_BUCKET'],
    ratelimits:{DELETION_PUBLIC_LIMIT:{namespace_id:'21204',simple:{limit:120,period:60}},AUTH_RATE_LIMIT:{namespace_id:'21101',simple:{limit:120,period:60}},RELAY_PUBLIC_LIMIT:{namespace_id:'21201',simple:{limit:1000,period:60}},RELAY_VERIFY_LIMIT:{namespace_id:'21202',simple:{limit:1000,period:60}},RELAY_JOB_LIMIT:{namespace_id:'21203',simple:{limit:1000,period:60}},API_KEY_LIMIT:{namespace_id:'21401',simple:{limit:60,period:60}},MEDIA_CONTROL_LIMIT:{namespace_id:'21302',simple:{limit:600,period:60}},MEDIA_UPLOAD_LIMIT:{namespace_id:'21303',simple:{limit:240,period:60}},MEDIA_UPLOAD_IP_LIMIT:{namespace_id:'21304',simple:{limit:120,period:60}},MEDIA_PUBLIC_LIMIT:{namespace_id:'21301',simple:{limit:120,period:60}}}}));await mf.ready;
});
after(async()=>{await mf?.dispose();await rm(directory,{recursive:true,force:true});const hits=logs.filter(line=>sensitive.some(value=>line.includes(value))).length;assert.equal(hits,0,'secret or request body in runtime logs');console.log('api runtime log scan: '+logs.length+' chunks, '+hits+' hits');});
async function signed(type,subject,op,body={}){
  const path=`/relay/v/${type}/${subject}/${op}`,raw=JSON.stringify(body),time=Date.now(),nonce=opaque();
  const signature=run(['dgst','-sha256','-sign',key,'-sigopt','rsa_padding_mode:pss','-sigopt','rsa_pss_saltlen:32'],Buffer.from(canonical('POST',path,'operator',subject,op,time,nonce,hash(raw)))).toString('base64url');
  sensitive.push(signature,nonce);
  return mf.dispatchFetch(ORIGIN+path,{method:'POST',headers:{'content-type':'application/json','cf-connecting-ip':opaque(),'x-thth-time':String(time),'x-thth-nonce':nonce,'x-thth-signature':signature},body:raw});
}
const control=async(type,id,settings)=>(await mf.dispatchFetch(`${ORIGIN}/__person/${type}/${id}`,settings?{method:'POST',body:JSON.stringify(settings)}:{})).json();
async function person(){const id='p'+randomBytes(8).toString('hex'),secret=opaque(),salt=opaque();sensitive.push(secret);
  const data={salt,verifier:pbkdf2Sync(secret,Buffer.from(salt,'base64url'),100000,32,'sha256').toString('base64url'),iterations:100000};sensitive.push(data.verifier);
  assert.equal((await signed('person',id,'set',data)).status,200);return id;}
const account=()=>'inv-a'+randomBytes(4).toString('hex');
const later=(ms=365*86_400_000)=>new Date(Date.now()+ms).toISOString().replace(/\.\d+Z$/,'+00:00');
function bearer(){const value=opaque();sensitive.push(value);return value;}
const keyRow=(value,name,expires_at=later())=>({sha256:hash(value),account:name,expires_at});
async function sync(p,keys,results=[],extra={}){
  const response=await signed('activity',p,'sync',{accounts:[],completed:[],keys,results,...extra});
  assert.equal(response.status,200,await response.clone().text());return response.json();
}
const api=(operation,value,body,headers={})=>mf.dispatchFetch(ORIGIN+'/api/v1/'+operation,{method:'POST',
  headers:{'content-type':'application/json','cf-connecting-ip':opaque(),...(value?{authorization:'Bearer '+value}:{}),...headers},body:typeof body==='string'?body:JSON.stringify(body)});
const result=(id,value)=>mf.dispatchFetch(ORIGIN+'/api/v1/result/'+id,{headers:{'cf-connecting-ip':opaque(),authorization:'Bearer '+value}});
// VM の役: 待っている依頼が渡されるまで sync を回し、結果を返す。
async function answer(p,keys,respond){
  for(let n=0;n<40;n++){
    const got=await sync(p,keys);
    if(got.requests.length){const results=got.requests.map(item=>({request_id:item.request_id,account:item.account,status:'done',result:respond(item)}));await sync(p,keys,results);return got.requests;}
    await new Promise(resolve=>setTimeout(resolve,50));
  }
  throw new Error('no request handed over');
}

test('the key must be in the table the VM pushed; unknown, missing and expired keys are refused before any request is kept',async()=>{
  const p=await person(),name=account(),value=bearer(),stale=bearer();
  assert.equal((await api('send_request',null,{account:name,body:'x'})).status,401);
  const unknown=await api('send_request',value,{account:name,body:'x'});
  assert.equal(unknown.status,401);assert.deepEqual(await unknown.json(),{error:'invalid_key'});
  assert.equal(unknown.headers.get('cache-control'),'no-store');assert.equal(unknown.headers.get('referrer-policy'),'no-referrer');
  await sync(p,[keyRow(value,name),keyRow(stale,name,later(-60_000))]);
  const expired=await api('send_request',stale,{account:name,body:'x'});
  assert.equal(expired.status,401);assert.deepEqual(await expired.json(),{error:'key_expired'});
  // 鍵は口座に結びつく: 別の口座には使えない。
  assert.deepEqual(await(await api('send_request',value,{account:account(),body:'x'})).json(),{error:'invalid_key'});
  assert.ok(!(await control('account',name)).some(([k])=>k.startsWith('request:')));
  // 取り消し・発行し直しは次の sync で揃う（表から外れた鍵は断る）。
  await sync(p,[]);
  assert.deepEqual(await(await api('send_request',value,{account:name,body:'x'})).json(),{error:'invalid_key'});
});

test('one key may serve several accounts of the same person; a duplicated row is refused',async()=>{
  const p=await person(),one=account(),two=account(),value=bearer();
  await sync(p,[keyRow(value,one),keyRow(value,two)]);
  for(const name of [one,two])assert.equal((await api('draft_list',value,{account:name})).status,202);
  const doubled=await signed('activity',p,'sync',{accounts:[],completed:[],keys:[keyRow(value,one),keyRow(value,one)],results:[]});
  assert.equal(doubled.status,400);
});

test('the request is held until the VM answers; the answer is the VM JSON as is, and the request reaches only the key owner',async()=>{
  const p=await person(),other=await person(),name=account(),value=bearer();
  const keys=[keyRow(value,name)];await sync(p,keys);
  const body={account:name,body:'遠くの道の本文だけに出る語',reply_to:null};sensitive.push('遠くの道の本文だけに出る語');
  const pending=api('send_request',value,body);
  assert.deepEqual((await sync(other,[])).requests,[]);
  const handed=await answer(p,keys,()=>({account:name,status:'published',post_id:'1789',permalink:null,via:'api'}));
  assert.equal(handed.length,1);
  assert.deepEqual(Object.keys(handed[0]).sort(),['account','body','key_sha256','operation','request_id']);
  assert.equal(handed[0].operation,'send_request');assert.deepEqual(handed[0].body,body);assert.equal(handed[0].key_sha256,hash(value));
  assert.match(handed[0].request_id,new RegExp('^[A-Za-z0-9_-]{43}\\.'+name.replaceAll('.','\\.')+'$'));
  const response=await pending;
  assert.equal(response.status,200);assert.equal(response.headers.get('cache-control'),'no-store');
  assert.deepEqual(await response.json(),{account:name,status:'published',post_id:'1789',permalink:null,via:'api'});
  // 結果は入ってから 10 分は同じ鍵で引ける・本文はもう無い。
  const again=await result(handed[0].request_id,value);assert.equal(again.status,200);assert.equal((await again.json()).post_id,'1789');
  const stored=JSON.stringify(await control('account',name));
  assert.ok(!stored.includes('遠くの道の本文だけに出る語')&&!stored.includes(value),'the body is dropped once answered; the bearer is never kept');
});

test('beyond the hold the answer is 202 pending; result/<id> is 202 until the VM answers, then 200; unknown, foreign and expired ids are 404',async()=>{
  const p=await person(),name=account(),value=bearer(),second=bearer();
  const keys=[keyRow(value,name),keyRow(second,name)];await sync(p,keys);
  const started=Date.now(),held=await api('draft_list',value,{account:name});
  assert.equal(held.status,202);assert.ok(Date.now()-started>=HOLD-100,'held for the whole window');
  const {status,request_id}=await held.json();assert.equal(status,'pending');
  const waiting=await result(request_id,value);assert.equal(waiting.status,202);assert.deepEqual(await waiting.json(),{status:'pending',request_id});
  assert.equal((await result(request_id,second)).status,404,'another key of the same account cannot read it');
  assert.equal((await result(opaque()+'.'+name,value)).status,404);
  assert.equal((await result('not-an-id',value)).status,404);
  const [item]=(await sync(p,keys)).requests;assert.equal(item.request_id,request_id);
  await sync(p,keys,[{request_id,account:name,status:'done',result:{account:name,drafts:[]}}]);
  const done=await result(request_id,value);assert.equal(done.status,200);assert.deepEqual(await done.json(),{account:name,drafts:[]});
  // 同じ依頼の結果をもう一度（VM の送り直し）・他の持ち主から送られても変わらない。
  await sync(p,keys,[{request_id,account:name,status:'done',result:{error:'outcome_unknown'}}]);
  assert.deepEqual(await(await result(request_id,value)).json(),{account:name,drafts:[]});
  // 結果は 10 分で消える。
  const [,id]=/^([^.]+)\./.exec(request_id);
  await control('account',name,{clock:Date.now()+600_001,alarm:true});
  assert.ok(!(await control('account',name)).some(([k])=>k==='request:'+id));
  await control('account',name,{clock:null});
  assert.equal((await result(request_id,value)).status,404);
});

test('an unanswered request loses its body after 120 seconds and is forgotten after the grace; a late answer is refused',async()=>{
  const p=await person(),name=account(),value=bearer();
  const keys=[keyRow(value,name)];await sync(p,keys);
  const {request_id}=await(await api('draft_list',value,{account:name,marker:'期限で消える本文'})).json();sensitive.push('期限で消える本文');
  await control('account',name,{clock:Date.now()+120_001,alarm:true});
  const rows=new Map(await control('account',name));const [,id]=/^([^.]+)\./.exec(request_id);
  assert.equal(rows.get('request:'+id).body,null);
  assert.deepEqual((await sync(p,keys)).requests,[],'an expired request is not handed over');
  await control('account',name,{clock:Date.now()+180_001,alarm:true});
  assert.ok(!(await control('account',name)).some(([k])=>k.startsWith('request:')));
  await control('account',name,{clock:null});
  await sync(p,keys,[{request_id,account:name,status:'done',result:{ok:true}}]);
  assert.equal((await result(request_id,value)).status,404);
});

test('eight pending requests per account; the key limit is 60 a minute',async()=>{
  const p=await person(),name=account(),value=bearer();
  await sync(p,[keyRow(value,name)]);
  await control('account',name,{clock:null});
  const all=await Promise.all(Array.from({length:9},(_,n)=>api('draft_list',value,{account:name,n})));
  const codes=all.map(r=>r.status).sort();assert.deepEqual(codes,[...Array(8).fill(202),429]);
  const refused=all.find(r=>r.status===429);assert.deepEqual(await refused.json(),{error:'too_many_requests'});
  const limited=bearer(),other=account();await sync(p,[keyRow(value,name),keyRow(limited,other)]);
  const statuses=[];
  for(let n=0;n<61;n++)statuses.push((await result(opaque()+'.'+other,limited)).status);
  assert.deepEqual(statuses.slice(0,60),Array(60).fill(404));
  const last=await result(opaque()+'.'+other,limited);assert.equal(last.status,429);assert.deepEqual(await last.json(),{error:'rate_limited'});
  assert.equal(last.headers.get('retry-after'),'2');
});

test('closed shapes: operation from the path only, JSON only, 48 KB, POST for operations and GET for results',async()=>{
  const p=await person(),name=account(),value=bearer();await sync(p,[keyRow(value,name)]);
  const code=async response=>[response.status,(await response.json()).error];
  assert.deepEqual(await code(await api('send_request',value,{account:name,operation:'retract_request'})),[400,'invalid_request']);
  assert.deepEqual(await code(await api('send_request',value,{body:'x'})),[400,'invalid_request']);
  assert.deepEqual(await code(await api('send_request',value,[name])),[400,'invalid_request']);
  assert.deepEqual(await code(await api('send_request',value,'{broken')),[400,'invalid_request']);
  assert.deepEqual(await code(await api('send_request',value,{account:name},{'content-type':'text/plain'})),[400,'invalid_request']);
  assert.deepEqual(await code(await api('send_request',value,{account:name,body:'あ'.repeat(17_000)})),[413,'too_large']);
  assert.deepEqual(await code(await api('Send-Request',value,{account:name})),[404,'not_found']);
  assert.deepEqual(await code(await api('result',value,{account:name})),[405,'method_not_allowed']);
  assert.deepEqual(await code(await mf.dispatchFetch(ORIGIN+'/api/v1/send_request',{headers:{authorization:'Bearer '+value}})),[405,'method_not_allowed']);
  assert.deepEqual(await code(await mf.dispatchFetch(ORIGIN+'/api/v1/send_request?x=1',{method:'POST'})),[400,'invalid_request']);
  assert.deepEqual(await code(await mf.dispatchFetch(ORIGIN+'/api/v2/send_request',{method:'POST'})),[404,'not_found']);
  assert.ok(!(await control('account',name)).some(([k])=>k.startsWith('request:')),'nothing is kept for a refused shape');
});

// deploy は Worker が先・VM があと。3.13.0 の VM の sync（keys も results も無い）は今の形のまま通り、鍵の表に触らない。
test('a 3.13.0 VM sync has no keys: the answer has no requests, the key table is untouched, so the API refuses with 401',async()=>{
  const p=await person(),name=account(),value=bearer();
  const old=await signed('activity',p,'sync',{accounts:[],completed:[]});assert.equal(old.status,200);
  assert.deepEqual(await old.json(),{status:'synced',actions:[]});
  assert.deepEqual(await(await api('draft_list',value,{account:name})).json(),{error:'invalid_key'});
  await sync(p,[keyRow(value,name)]);
  assert.equal((await signed('activity',p,'sync',{accounts:[],completed:[]})).status,200);
  assert.equal((await api('draft_list',value,{account:name})).status,202,'an old-shape sync does not clear the table');
  // 片方だけ（keys だけ）・形の違う鍵・結果は断る。
  assert.equal((await signed('activity',p,'sync',{accounts:[],completed:[],keys:[]})).status,400);
  assert.equal((await signed('activity',p,'sync',{accounts:[],completed:[],keys:[{sha256:'x',account:name,expires_at:later()}],results:[]})).status,400);
  assert.equal((await signed('activity',p,'sync',{accounts:[],completed:[],keys:[],results:[{request_id:opaque()+'.other',account:name,status:'done',result:{}}]})).status,400);
  assert.equal((await signed('activity',p,'sync',{accounts:[],completed:[],keys:[],results:[{request_id:opaque()+'.'+name,account:name,status:'done',result:{big:'x'.repeat(131_073)}}]})).status,400);
});

test('wrangler.jsonc: API_KEY_LIMIT is 60 a minute on its own namespace and /api/* runs the Worker first',async()=>{
  const text=(await readFile(fileURLToPath(new URL('../wrangler.jsonc',import.meta.url)),'utf8')).split('\n').filter(line=>!line.trim().startsWith('//')).join('\n');
  const config=JSON.parse(text);
  const limit=config.ratelimits.find(r=>r.name==='API_KEY_LIMIT');
  assert.deepEqual(limit,{name:'API_KEY_LIMIT',namespace_id:'21401',simple:{limit:60,period:60}});
  assert.equal(new Set(config.ratelimits.map(r=>r.namespace_id)).size,config.ratelimits.length);
  assert.ok(config.assets.run_worker_first.includes('/api/*'));
  assert.equal(config.observability.enabled,false);
});
