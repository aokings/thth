// Meta 計画 段 6: Meta の Webhook の受け口（/webhook/meta）と VM が取りに来る relay（/relay/v/webhook/meta/…）。
// 本物の Meta は叩かない。秘密は試験の中で作る使い捨ての値（log に出ないことも見る）。
//
// 柵（わざと壊して赤を見る対象）: 署名の照合（鍵を 1 文字変えると 403）・verify_token の照合・重複の弾き・
// 1000 件の束を全部積めること・VM の署名が無い pull は 401。
import {test,before,after} from 'node:test';
import assert from 'node:assert/strict';
import {randomBytes,createHash,createHmac} from 'node:crypto';
import {spawnSync} from 'node:child_process';
import {mkdtemp,writeFile,readFile,rm,realpath} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {Miniflare,Log,LogLevel,convertV4MiniflareOptions} from 'miniflare';
import {canonical} from '../src/person.js';
import {pythonForTests} from './python-runtime.js';
const opaque=()=>randomBytes(32).toString('base64url'),hash=s=>createHash('sha256').update(s).digest('hex');
const logs=[],sensitive=[];
class SilentLog extends Log {constructor(){super(LogLevel.NONE);}log(value){logs.push(String(value));}}
let mf,directory,key;
const APP_SECRET=opaque(),VERIFY=opaque();sensitive.push(APP_SECRET,VERIFY);
const PAGE='1361492233719922',IG='17841437318552220';
const openssl=process.platform==='darwin'?'/opt/homebrew/bin/openssl':'/usr/bin/openssl';
function run(args,input){const r=spawnSync(openssl,args,{input,env:{PATH:'/usr/bin:/bin',OPENSSL_CONF:'/dev/null'}});assert.equal(r.status,0,'OpenSSL failed');return r.stdout;}
before(async()=>{
  directory=await realpath(await mkdtemp(join(tmpdir(),'thth-webhook-')));key=join(directory,'ephemeral.key');
  const pem=run(['genpkey','-algorithm','RSA','-pkeyopt','rsa_keygen_bits:3072']);sensitive.push(pem.toString());await writeFile(key,pem,{mode:0o600});
  const publicKey=run(['pkey','-in',key,'-pubout','-outform','DER']).toString('base64url');
  const files=['test/webhook-harness.js','src/worker.js','src/webhook-object.js','src/webhook.js','src/media.js','src/media-object.js','src/index.js','src/relay.js','src/relay-object.js','src/person.js','src/person-object.js','src/deletion.js','src/deletion-object.js','src/invite.js','src/invite-object.js','src/activity.js','src/api.js','src/login.js','src/login-object.js'];
  const modules=await Promise.all(files.map(async name=>{const path=fileURLToPath(new URL('../'+name,import.meta.url));return{type:'ESModule',path,contents:await readFile(path,'utf8')};}));
  mf=new Miniflare(convertV4MiniflareOptions({modules,modulesRoot:fileURLToPath(new URL('..',import.meta.url)),compatibilityDate:'2026-09-01',cf:false,
    log:new SilentLog(),handleStructuredLogs:item=>logs.push(JSON.stringify(item)),
    bindings:{RELAY_PUBLIC_KEY:publicKey,META_APP_SECRET:APP_SECRET,META_WEBHOOK_VERIFY_TOKEN:VERIFY},
    durableObjects:{AUTH_RELAY:{className:'AuthRelay',useSQLite:true},PERSON:{className:'Person',useSQLite:true},ACCOUNT:{className:'Account',useSQLite:true},WEBHOOK_INBOX:{className:'TestWebhook',useSQLite:true}},
    ratelimits:{RELAY_VERIFY_LIMIT:{namespace_id:'21202',simple:{limit:100000,period:60}},RELAY_JOB_LIMIT:{namespace_id:'21203',simple:{limit:100000,period:60}}}}));
  await mf.ready;
});
after(async()=>{await mf?.dispose();await rm(directory,{recursive:true,force:true});const hits=logs.filter(line=>sensitive.some(value=>line.includes(value))).length;assert.equal(hits,0,'secret in runtime logs');});

const sign=(raw,secret=APP_SECRET)=>'sha256='+createHmac('sha256',secret).update(raw).digest('hex');
// 鍵を 1 文字だけ変える（柵のわざと壊すの型）。
const oneOff=secret=>secret.slice(0,-1)+(secret.at(-1)==='A'?'B':'A');
async function deliver(payload,{secret=APP_SECRET,header,raw}={}){
  const body=raw??JSON.stringify(payload);
  return mf.dispatchFetch('https://thth.test/webhook/meta',{method:'POST',headers:{'content-type':'application/json','x-hub-signature-256':header??sign(body,secret)},body});
}
async function control(settings){return(await mf.dispatchFetch('https://thth.test/__webhook',settings?{method:'POST',body:JSON.stringify(settings)}:{})).json();}
const rows=async()=>(await control()).filter(([name])=>name.startsWith('row:')).map(([,row])=>row);
const stats=async()=>Object.fromEntries(await control()).stats;
function wire(subject,op,body,options={}){
  const path=`/relay/v/webhook/${subject}/${op}`,raw=JSON.stringify(body),time=Date.now(),nonce=opaque();
  const signature=run(['dgst','-sha256','-sign',key,'-sigopt','rsa_padding_mode:pss','-sigopt','rsa_pss_saltlen:32'],Buffer.from(canonical('POST',path,options.role??'operator',subject,op,time,nonce,hash(raw)))).toString('base64url');
  sensitive.push(signature);
  return{url:'https://thth.test'+path,init:{method:'POST',headers:{'content-type':'application/json','cf-connecting-ip':opaque(),'x-thth-time':String(time),'x-thth-nonce':nonce,'x-thth-signature':signature},body:raw}};
}
async function relay(op,body,options){const w=wire(options?.subject??'meta',op,body,options);return mf.dispatchFetch(w.url,w.init);}
async function drain(){
  const got=[];let after=null;
  for(let n=0;n<100;n++){
    const page=await(await relay('pull',{after})).json();got.push(...page.rows);
    for(let i=0;i<page.rows.length;i+=200){const keys=page.rows.slice(i,i+200).map(r=>r.key);assert.equal((await relay('ack',{keys})).status,200);}
    if(!page.next)break;after=page.next;
  }
  return got;
}
const comment=(n,{verb='add',post=`${PAGE}_9001`}={})=>({field:'feed',value:{item:'comment',verb,post_id:post,comment_id:`9001_${n}`,parent_id:post,
  from:{id:'555',name:'訪問者'},message:'こんにちは '+n,created_time:1793000000+n}});
const feed=(changes,time=1793000000)=>({object:'page',entry:[{id:PAGE,time,changes}]});

test('GET の確認: verify_token が合えば hub.challenge をそのまま返す・1 文字違えば 403',async()=>{
  const q=(token,mode='subscribe',challenge='1158201444')=>mf.dispatchFetch(`https://thth.test/webhook/meta?hub.mode=${mode}&hub.challenge=${challenge}&hub.verify_token=${encodeURIComponent(token)}`);
  const ok=await q(VERIFY);assert.equal(ok.status,200);assert.equal(await ok.text(),'1158201444');
  assert.equal(ok.headers.get('cache-control'),'no-store');assert.match(ok.headers.get('content-type'),/^text\/plain/);
  assert.equal((await q(oneOff(VERIFY))).status,403);
  assert.equal((await q(VERIFY+'x')).status,403);
  assert.equal((await q('')).status,403);
  assert.equal((await q(VERIFY,'unsubscribe')).status,400);
  assert.equal((await q(VERIFY,'subscribe','<script>')).status,400);
  assert.equal((await mf.dispatchFetch(`https://thth.test/webhook/meta?hub.mode=subscribe&hub.challenge=1`)).status,400);
  assert.equal((await mf.dispatchFetch('https://thth.test/webhook/other')).status,404);
  assert.equal((await mf.dispatchFetch('https://thth.test/webhook/meta',{method:'PUT'})).status,405);
});

test('POST の署名: 正しい sha なら積んで 200・鍵を 1 文字変えると 403 で何も積まない',async()=>{
  const before=(await rows()).length;
  const payload=feed([comment(1)]);
  const bad=await deliver(payload,{secret:oneOff(APP_SECRET)});
  assert.equal(bad.status,403);assert.deepEqual(await bad.json(),{error:'forbidden'});
  assert.equal((await rows()).length,before,'署名の違う通知を積んだ');
  for(const header of ['', 'sha1=abc', 'sha256=', 'sha256='+'0'.repeat(64), sign(JSON.stringify(payload)).slice(0,-1)])
    assert.equal((await deliver(payload,{header})).status,403,header);
  // 署名のあとに本文を 1 字変える（生のバイト列で照合している）。
  const raw=JSON.stringify(payload);assert.equal((await deliver(null,{raw:raw.replace('こんにちは','こんばんは'),header:sign(raw)})).status,403);
  const ok=await deliver(payload);assert.equal(ok.status,200);assert.deepEqual(await ok.json(),{ok:true});
  const stored=(await rows()).filter(r=>JSON.parse(r.value).comment_id==='9001_1');
  assert.equal(stored.length,1);assert.equal(stored[0].object,'page');assert.equal(stored[0].entry_id,PAGE);assert.equal(stored[0].field,'feed');
  assert.match(stored[0].key,/^[0-9a-f]{64}$/);
  assert.equal(stored[0].key,hash(['page',PAGE,'1793000000','feed','9001_1','add'].join('|')),'key は object|entry.id|time|field|変化の id|verb');
  await drain();
});

test('重複は弾く: 同じ通知の送り直し・受け取り（ack）のあとの送り直し・同じ束の中の 2 度',async()=>{
  const payload=feed([comment(2),comment(2)]);
  const s0=await stats();
  assert.equal((await deliver(payload)).status,200);
  assert.equal((await deliver(payload)).status,200);
  let s=await stats();assert.equal(s.received-s0.received,1);assert.equal(s.duplicates-s0.duplicates,3);
  const got=await drain();assert.equal(got.filter(r=>JSON.parse(r.value).comment_id==='9001_2').length,1);
  assert.equal((await deliver(payload)).status,200);
  s=await stats();assert.equal(s.received-s0.received,1,'ack のあとの送り直しを積んだ');assert.equal(s.pending,0);
  // 同じコメントでも verb が違えば別の変化（edit）。
  assert.equal((await deliver(feed([comment(2,{verb:'edited'})]))).status,200);
  assert.equal((await stats()).received-s0.received,2);await drain();
});

test('1000 件の束を全部積み・VM は頁を辿って全部受け取り・ack で空になる',async()=>{
  const entry=Array.from({length:1000},(_,i)=>({id:PAGE,time:1793100000+i,changes:[comment(10000+i)]}));
  const raw=JSON.stringify({object:'page',entry});assert.ok(raw.length>200_000);
  assert.equal((await deliver(null,{raw})).status,200);
  const s=await stats();assert.equal(s.pending,1000);
  const got=await drain();assert.equal(got.length,1000);assert.equal(new Set(got.map(r=>r.key)).size,1000);
  assert.equal((await stats()).pending,0);assert.equal((await rows()).length,0);
});

test('受ける欄は FB の feed・mention と IG の mentions だけ。IG の comments とほかは数だけ',async()=>{
  const s0=await stats();
  const payload={object:'page',entry:[{id:PAGE,time:1793200000,changes:[
    {field:'mention',value:{item:'post',verb:'add',post_id:'77_1',sender_id:'556',sender_name:'言及した人',message:'@こぴちゃ'}},
    {field:'ratings',value:{verb:'add'}},{field:'messages',value:{}}]}]};
  assert.equal((await deliver(payload)).status,200);
  const ig={object:'instagram',entry:[{id:IG,time:1793200001,changes:[{field:'mentions',value:{media_id:'1799',comment_id:'1800'}},
    {field:'comments',value:{id:'1801',text:'x'}},{field:'live_comments',value:{id:'1802'}}]}]};
  assert.equal((await deliver(ig)).status,200);
  assert.equal((await deliver({object:'user',entry:[{id:'1',time:1,changes:[{field:'feed',value:{}}]}]})).status,200);
  const s=await stats();assert.equal(s.received-s0.received,2);assert.equal(s.dropped-s0.dropped,5);
  const got=await drain();assert.deepEqual(got.map(r=>[r.object,r.field]).sort(),[['instagram','mentions'],['page','mention']]);
  assert.equal((await deliver(null,{raw:'{"object":'})).status,400);
});

test('VM の relay: 署名の無い pull は 401・role が違えば 401・subject は meta だけ・使い回しは 409',async()=>{
  const raw=JSON.stringify({after:null});
  assert.equal((await mf.dispatchFetch('https://thth.test/relay/v/webhook/meta/pull',{method:'POST',headers:{'content-type':'application/json'},body:raw})).status,401);
  assert.equal((await relay('pull',{after:null},{role:'job'})).status,401);
  assert.equal((await relay('pull',{after:null},{subject:'other'})).status,400);
  assert.equal((await relay('list',{after:null})).status,400);
  assert.equal((await relay('pull',{after:'x'})).status,400);
  assert.equal((await relay('ack',{keys:['nothex']})).status,400);
  assert.equal((await relay('ack',{keys:[]})).status,400);
  const w=wire('meta','pull',{after:null});assert.equal((await mf.dispatchFetch(w.url,w.init)).status,200);
  assert.equal((await mf.dispatchFetch(w.url,w.init)).status,409);
});

test('積める数を越えたら 503（Meta が送り直す）で何も積まず overflow に数える',async()=>{
  await control({limit:1});
  try{
    const s0=await stats();
    assert.equal((await deliver(feed([comment(20),comment(21)],1793300000))).status,503);
    const s=await stats();assert.equal(s.overflow-s0.overflow,2);assert.equal(s.pending,0);
  }finally{await control({});}
  assert.equal((await deliver(feed([comment(20),comment(21)],1793300000))).status,200);await drain();
});

test('VM が 7 日取りに来なかった行は消えて expired に数える（過去の分は取り直せない）',async()=>{
  const now=Date.now();await control({clock:now});
  assert.equal((await deliver(feed([comment(30)],1793400000))).status,200);
  const s0=await stats();
  await control({clock:now+7*24*3600*1000-1,alarm:true});assert.equal((await stats()).pending,1);
  await control({clock:now+7*24*3600*1000+1,alarm:true});
  const s=await stats();assert.equal(s.expired-s0.expired,1);assert.equal(s.pending,0);assert.equal((await rows()).length,0);
  await control({});
  assert.equal((await deliver(feed([comment(30)],1793400000))).status,200,'消えた行の送り直しは重複（seen）');
  assert.equal((await stats()).pending,0);
});

test('product Python の署名（relay.signed_request の webhook）を本物の Worker が受ける',async()=>{
  await writeFile(join(directory,'relay-signer.key'),await readFile(key),{mode:0o600});
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
r.signed_request('webhook','meta','pull',{'after':None})`;
  const child=spawnSync(pythonForTests(),['-c',script],{cwd:root,env:{PATH:'/usr/bin:/bin:/opt/homebrew/bin',PYTHONPATH:root,HOME:directory,THTH_APPS_DIR:directory,PYTHONDONTWRITEBYTECODE:'1'}});
  assert.equal(child.status,0,'Python signer failed: '+child.stderr.toString().split('\n').at(-2));
  const w=JSON.parse(child.stdout);for(const [k,v]of Object.entries(w.headers))if(k.toLowerCase().includes('signature'))sensitive.push(v);
  const response=await mf.dispatchFetch(w.url,{method:'POST',headers:{...w.headers,'cf-connecting-ip':opaque()},body:w.body});
  assert.equal(response.status,200);const body=await response.json();assert.ok(Array.isArray(body.rows));assert.equal(typeof body.stats.received,'number');
});
