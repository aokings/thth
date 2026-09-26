// 3.14.2 ブラウザ式の thth login（/api/v1/login/* と /login/<code>）。start・poll・form・POST・鍵は 1 度だけ・
// 期限・poll_token が違えば 401・回数制限・鍵が本文にもログにも出ない・rotate と同じ発行（VM の sync に hash だけ）。
import {test,before,after} from 'node:test';
import assert from 'node:assert/strict';
import {randomBytes,createHash,pbkdf2Sync,randomInt} from 'node:crypto';
import {spawnSync} from 'node:child_process';
import {mkdtemp,writeFile,readFile,rm,realpath} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {Miniflare,Log,LogLevel,convertV4MiniflareOptions} from 'miniflare';
import {canonical} from '../src/person.js';
import {CODE_ALPHABET} from '../src/login.js';
const opaque=()=>randomBytes(32).toString('base64url'),hash=s=>createHash('sha256').update(s).digest('hex');
const logs=[],sensitive=[];
class SilentLog extends Log {constructor(){super(LogLevel.NONE);}log(value){logs.push(String(value));}}
let mf,directory,key;
const ORIGIN='https://login.test';
const openssl=process.platform==='darwin'?'/opt/homebrew/bin/openssl':'/usr/bin/openssl';
function run(args,input){const r=spawnSync(openssl,args,{input,env:{PATH:'/usr/bin:/bin',OPENSSL_CONF:'/dev/null'}});assert.equal(r.status,0,'OpenSSL failed');return r.stdout;}
before(async()=>{
  directory=await realpath(await mkdtemp(join(tmpdir(),'thth-login-')));key=join(directory,'ephemeral.key');
  const pem=run(['genpkey','-algorithm','RSA','-pkeyopt','rsa_keygen_bits:3072']);sensitive.push(pem.toString());await writeFile(key,pem,{mode:0o600});
  const publicKey=run(['pkey','-in',key,'-pubout','-outform','DER']).toString('base64url');
  const files=['test/person-harness.js','src/worker.js','src/media.js','src/media-object.js','src/index.js','src/relay.js','src/relay-object.js','src/person.js','src/person-object.js','src/deletion.js','src/deletion-object.js','src/invite.js','src/invite-object.js','src/activity.js','src/api.js','src/login.js','src/login-object.js'];
  const modules=await Promise.all(files.map(async name=>{const path=fileURLToPath(new URL('../'+name,import.meta.url));return{type:'ESModule',path,contents:await readFile(path,'utf8')};}));
  mf=new Miniflare(convertV4MiniflareOptions({modules,modulesRoot:fileURLToPath(new URL('..',import.meta.url)),compatibilityDate:'2026-09-01',cf:false,
    log:new SilentLog(),handleStructuredLogs:item=>logs.push(JSON.stringify(item)),bindings:{RELAY_PUBLIC_KEY:publicKey},
    durableObjects:{AUTH_RELAY:{className:'AuthRelay',useSQLite:true},PERSON:{className:'TestPerson',useSQLite:true},ACCOUNT:{className:'TestAccount',useSQLite:true},DELETION_INBOX:{className:'TestDeletion',useSQLite:true},MEDIA_OBJECT:{className:'TestMedia',useSQLite:true},LOGIN:{className:'TestLogin',useSQLite:true}},r2Buckets:['MEDIA_BUCKET'],
    ratelimits:{DELETION_PUBLIC_LIMIT:{namespace_id:'21204',simple:{limit:120,period:60}},AUTH_RATE_LIMIT:{namespace_id:'21101',simple:{limit:120,period:60}},RELAY_PUBLIC_LIMIT:{namespace_id:'21201',simple:{limit:120,period:60}},RELAY_VERIFY_LIMIT:{namespace_id:'21202',simple:{limit:1000,period:60}},RELAY_JOB_LIMIT:{namespace_id:'21203',simple:{limit:1000,period:60}},API_KEY_LIMIT:{namespace_id:'21401',simple:{limit:60,period:60}},MEDIA_CONTROL_LIMIT:{namespace_id:'21302',simple:{limit:600,period:60}},MEDIA_UPLOAD_LIMIT:{namespace_id:'21303',simple:{limit:240,period:60}},MEDIA_UPLOAD_IP_LIMIT:{namespace_id:'21304',simple:{limit:120,period:60}},MEDIA_PUBLIC_LIMIT:{namespace_id:'21301',simple:{limit:120,period:60}}}}));await mf.ready;
});
after(async()=>{await mf?.dispose();await rm(directory,{recursive:true,force:true});const hits=logs.filter(line=>sensitive.some(value=>line.includes(value))).length;assert.equal(hits,0,'key or secret in runtime logs');console.log('login runtime log scan: '+logs.length+' chunks, '+hits+' hits');});
async function signed(type,subject,op,body={}){
  const path=`/relay/v/${type}/${subject}/${op}`,raw=JSON.stringify(body),time=Date.now(),nonce=opaque();
  const signature=run(['dgst','-sha256','-sign',key,'-sigopt','rsa_padding_mode:pss','-sigopt','rsa_pss_saltlen:32'],Buffer.from(canonical('POST',path,'operator',subject,op,time,nonce,hash(raw)))).toString('base64url');
  sensitive.push(signature,nonce);
  return mf.dispatchFetch(ORIGIN+path,{method:'POST',headers:{'content-type':'application/json','cf-connecting-ip':opaque(),'x-thth-time':String(time),'x-thth-nonce':nonce,'x-thth-signature':signature},body:raw});
}
const control=async(type,id,settings)=>(await mf.dispatchFetch(`${ORIGIN}/__person/${type}/${id}`,settings?{method:'POST',body:JSON.stringify(settings)}:{})).json();
const now=()=>new Date().toISOString().replace(/\.\d+Z$/,'+00:00');
function summary(name){
  return {account:name,generated_at:now(),scheduled:true,
    limits:{daily_max_posts:8,daily_max_retracts:5,burst_count:3,burst_minutes:10,hold_minutes:0,min_interval_hours:6},
    stopped:null,today:{posts:0,retracts:0},credential:null,rows:[]};
}
// 招待の口座と同じ形: ユーザ名＝口座名。VM が要約を押し上げてある。
async function owner(accounts){
  const id='inv-p'+randomBytes(6).toString('hex'),secret=opaque(),salt=opaque();sensitive.push(secret);
  const data={salt,verifier:pbkdf2Sync(secret,Buffer.from(salt,'base64url'),100000,32,'sha256').toString('base64url'),iterations:100000};sensitive.push(data.verifier);
  assert.equal((await signed('person',id,'set',data)).status,200);
  const names=accounts??[id];
  assert.equal((await signed('activity',id,'sync',{accounts:names.map(summary),completed:[]})).status,200);
  return {id,secret};
}
const newCode=()=>{const pick=()=>Array.from({length:4},()=>CODE_ALPHABET[randomInt(CODE_ALPHABET.length)]).join('');return pick()+'-'+pick();};
const start=body=>mf.dispatchFetch(ORIGIN+'/api/v1/login/start',{method:'POST',headers:{'content-type':'application/json','cf-connecting-ip':opaque()},body:typeof body==='string'?body:JSON.stringify(body)});
async function begin(){
  const code=newCode(),token=opaque();sensitive.push(token);
  const r=await start({code_hash:hash(code),poll_token_hash:hash(token)});
  assert.equal(r.status,202);
  return {code,token,body:await r.json()};
}
const poll=(code,token,ip=opaque())=>mf.dispatchFetch(ORIGIN+'/api/v1/login/poll/'+code,{headers:{'cf-connecting-ip':ip,...(token?{authorization:'Bearer '+token}:{})}});
const page=(code,ip=opaque())=>mf.dispatchFetch(ORIGIN+'/login/'+code,{headers:{'cf-connecting-ip':ip}});
const allow=(code,fields,headers={})=>mf.dispatchFetch(ORIGIN+'/login/'+code,{method:'POST',
  headers:{'content-type':'application/x-www-form-urlencoded',origin:ORIGIN,'cf-connecting-ip':opaque(),...headers},body:new URLSearchParams(fields),redirect:'manual'});
const pageHeaders=r=>{
  assert.equal(r.headers.get('cache-control'),'no-store');assert.equal(r.headers.get('referrer-policy'),'no-referrer');
  assert.match(r.headers.get('content-security-policy'),/default-src 'none'/);assert.match(r.headers.get('content-security-policy'),/form-action 'self'/);
};

test('start keeps only hashes, answers 202 with the /login/ prefix and a 10 minute expiry; a live code cannot be started twice',async()=>{
  const started=Date.now(),{code,token,body}=await begin();
  assert.deepEqual(Object.keys(body).sort(),['expires_at','url']);
  assert.equal(body.url,ORIGIN+'/login/');
  const left=Date.parse(body.expires_at)-started;assert.ok(left>590_000&&left<=601_000,String(left));
  const stored=JSON.stringify(await control('login',hash(code)));
  assert.ok(stored.includes(hash(token))&&!stored.includes(token)&&!stored.includes(code),'only the poll token hash is kept');
  assert.equal((await start({code_hash:hash(code),poll_token_hash:hash(opaque())})).status,409);
  const bad=async body=>(await start(body)).status;
  assert.equal(await bad({code_hash:hash(code)}),400);
  assert.equal(await bad({code_hash:'x',poll_token_hash:hash('y')}),400);
  assert.equal(await bad({code_hash:hash('a'),poll_token_hash:hash('b'),extra:1}),400);
  assert.equal(await bad('{broken'),400);
  assert.equal((await mf.dispatchFetch(ORIGIN+'/api/v1/login/start',{headers:{'cf-connecting-ip':opaque()}})).status,405);
});

test('the form: no script, CSP, no-store; the button says "Allow this device"; unknown and malformed codes are 404',async()=>{
  const {code}=await begin();
  const r=await page(code);assert.equal(r.status,200);pageHeaders(r);
  const html=await r.text();
  assert.ok(html.includes('この機械に鍵を渡す / Allow this device'),html);
  assert.ok(html.includes(code)&&html.includes(`action="/login/${code}"`));
  assert.ok(html.includes('autocomplete="current-password"')&&!/<script/i.test(html));
  // 手で小文字で打っても同じ code。
  assert.equal((await page(code.toLowerCase())).status,200);
  assert.equal((await page(newCode())).status,404);
  assert.equal((await page('AAAA-AAA0')).status,404);
  assert.equal((await page('not-a-code')).status,404);
  assert.equal((await mf.dispatchFetch(ORIGIN+'/login/'+code+'?x=1',{headers:{'cf-connecting-ip':opaque()}})).status,400);
});

test('allow with the account secret: the terminal gets the key once, the page never shows it, and the VM gets a rotate with its hash only',async()=>{
  const p=await owner(),{code,token}=await begin();
  const waiting=await poll(code,token);assert.equal(waiting.status,202);assert.deepEqual(await waiting.json(),{status:'waiting'});
  assert.equal(waiting.headers.get('cache-control'),'no-store');
  const r=await allow(code,{account:p.id,secret:p.secret});
  assert.equal(r.status,200);pageHeaders(r);
  const html=await r.text();assert.ok(html.includes('渡しました。ターミナルに戻ってください')&&html.includes('Done. Go back to your terminal'),html);
  const got=await poll(code,token);assert.equal(got.status,200);
  const {key:value,account}=await got.json();sensitive.push(value);
  assert.match(value,/^[A-Za-z0-9_-]{43}$/);assert.equal(account,p.id);
  assert.ok(!html.includes(value),'the key is not on the page');
  // 1 度だけ: 返したら消す。
  const again=await poll(code,token);assert.equal(again.status,410);
  const stored=JSON.stringify(await control('login',hash(code)));assert.ok(!stored.includes(value),'the key is gone once taken');
  // ページはもう使えない。
  assert.equal((await page(code)).status,410);
  assert.equal((await allow(code,{account:p.id,secret:p.secret})).status,410);
  // rotate と同じ: Person DO には hash だけ、VM の sync に rotate が渡る。
  const person=JSON.stringify(await control('person',p.id));assert.ok(!person.includes(value)&&person.includes(hash(value)));
  const synced=await(await signed('activity',p.id,'sync',{accounts:[summary(p.id)],completed:[]})).json();
  assert.equal(synced.actions.length,1);
  assert.deepEqual({kind:synced.actions[0].kind,account:synced.actions[0].account,sha256:synced.actions[0].sha256},{kind:'rotate',account:p.id,sha256:hash(value)});
});

test('a person with one account whose name differs gets that account; an unknown or ambiguous account is refused without issuing',async()=>{
  const only='inv-only'+randomBytes(3).toString('hex'),p=await owner([only]),{code,token}=await begin();
  assert.equal((await allow(code,{account:p.id,secret:p.secret})).status,200);
  const got=await(await poll(code,token)).json();sensitive.push(got.key);assert.equal(got.account,only);
  const two=await owner(['inv-a'+randomBytes(3).toString('hex'),'inv-b'+randomBytes(3).toString('hex')]),second=await begin();
  const refused=await allow(second.code,{account:two.id,secret:two.secret});assert.equal(refused.status,409);
  assert.equal((await poll(second.code,second.token)).status,202,'still waiting: it can be tried again');
  assert.ok(!(await control('person',two.id)).some(([k])=>k.startsWith('action:')));
});

test('a wrong secret is refused and the code stays usable; five failures close it for 15 minutes like /activity',async()=>{
  const p=await owner(),{code,token}=await begin();
  for(let n=0;n<5;n++){
    const r=await allow(code,{account:p.id,secret:opaque()});assert.equal(r.status,403);
    assert.ok((await r.text()).includes('Allow this device'),'the form is shown again');
  }
  assert.equal((await poll(code,token)).status,202);
  assert.equal((await allow(code,{account:p.id,secret:p.secret})).status,403,'locked even with the right secret');
  assert.ok(!(await control('person',p.id)).some(([k])=>k.startsWith('action:')),'nothing was issued');
  // 名前の形が違えば照合に進まない。
  assert.equal((await allow(code,{account:'../x',secret:p.secret})).status,403);
});

test('the poll token guards the key: missing or wrong is 401 and nothing is given away',async()=>{
  const p=await owner(),{code,token}=await begin();
  assert.equal((await allow(code,{account:p.id,secret:p.secret})).status,200);
  assert.equal((await poll(code,null)).status,401);
  const wrong=await poll(code,opaque());assert.equal(wrong.status,401);assert.deepEqual(await wrong.json(),{error:'unauthorized'});
  assert.equal((await poll(code,'short')).status,401);
  assert.equal((await poll(newCode(),token)).status,404);
  assert.equal((await poll('bad',token)).status,404);
  const got=await poll(code,token);assert.equal(got.status,200);sensitive.push((await got.json()).key);
});

test('after 10 minutes the code is expired (410) and then forgotten (404); a late allow issues nothing',async()=>{
  const p=await owner(),{code,token}=await begin();
  await control('login',hash(code),{clock:Date.now()+600_001});
  assert.equal((await poll(code,token)).status,410);
  assert.equal((await page(code)).status,410);
  assert.equal((await allow(code,{account:p.id,secret:p.secret})).status,410);
  assert.ok(!(await control('person',p.id)).some(([k])=>k.startsWith('action:')));
  await control('login',hash(code),{clock:Date.now()+600_001,alarm:true});
  assert.deepEqual(await control('login',hash(code)),[]);
  await control('login',hash(code),{clock:null});
  assert.equal((await poll(code,token)).status,404);
  // 期限が切れた code は始め直せる。
  assert.equal((await start({code_hash:hash(code),poll_token_hash:hash(opaque())})).status,202);
});

test('same-origin form POST only, closed fields, and the rate limits: poll 60 a minute per code, pages 120 a minute per IP',async()=>{
  const p=await owner(),{code,token}=await begin();
  assert.equal((await allow(code,{account:p.id,secret:p.secret},{origin:'https://evil.test','sec-fetch-site':'cross-site'})).status,403);
  assert.equal((await allow(code,{account:p.id,secret:p.secret,extra:'1'})).status,400);
  assert.equal((await allow(code,{account:p.id,secret:p.secret},{'content-type':'application/json'})).status,403);
  const statuses=[];for(let n=0;n<60;n++)statuses.push((await poll(code,token)).status);
  assert.deepEqual(statuses,Array(60).fill(202));
  const limited=await poll(code,token);assert.equal(limited.status,429);assert.deepEqual(await limited.json(),{error:'rate_limited'});
  const ip=opaque(),pages=[];for(let n=0;n<121;n++)pages.push((await page(code,ip)).status);
  assert.deepEqual(pages.slice(0,120),Array(120).fill(200));assert.equal(pages[120],429);
});

test('wrangler.jsonc: the LOGIN binding, a v8-login migration after v7 and /login/* runs the Worker first',async()=>{
  const text=(await readFile(fileURLToPath(new URL('../wrangler.jsonc',import.meta.url)),'utf8')).split('\n').filter(line=>!line.trim().startsWith('//')).join('\n');
  const config=JSON.parse(text);
  assert.ok(config.durable_objects.bindings.some(b=>b.name==='LOGIN'&&b.class_name==='Login'));
  const tags=config.migrations.map(m=>m.tag);
  assert.equal(tags.indexOf('v7-rename-person')+1,tags.indexOf('v8-login'));
  assert.deepEqual(config.migrations[tags.indexOf('v8-login')],{tag:'v8-login',new_sqlite_classes:['Login']});
  assert.ok(config.assets.run_worker_first.includes('/login/*'));
  const worker=await readFile(fileURLToPath(new URL('../src/worker.js',import.meta.url)),'utf8');
  assert.match(worker,/export \{Login\} from '\.\/login-object\.js'/);
});
