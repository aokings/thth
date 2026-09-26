// 3.12.0 段 3 動きの一覧（thth.me/activity）。持ち主がユーザ名と承認 secret で入り、VM が押し上げた自分の
// 口座の要約を見て、secret をもう一度入れて止める・戻す・予約の取り消し・設定・鍵の発行し直し/取り消しを頼む。
import {test,before,after} from 'node:test';
import assert from 'node:assert/strict';
import {randomBytes,createHash,pbkdf2Sync} from 'node:crypto';
import {spawnSync} from 'node:child_process';
import {mkdtemp,writeFile,readFile,rm,realpath} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {Miniflare,Log,LogLevel,convertV4MiniflareOptions} from 'miniflare';
import {canonical,TTL} from '../src/person.js';
const opaque=()=>randomBytes(32).toString('base64url'),hash=s=>createHash('sha256').update(s).digest('hex');
const logs=[],sensitive=[];
class SilentLog extends Log {constructor(){super(LogLevel.NONE);}log(value){logs.push(String(value));}}
let mf,directory,key;
const ORIGIN='https://approval.test';
const MCP_COMMAND='ssh example.test thth-mcp';
const openssl=process.platform==='darwin'?'/opt/homebrew/bin/openssl':'/usr/bin/openssl';
function run(args,input){const r=spawnSync(openssl,args,{input,env:{PATH:'/usr/bin:/bin',OPENSSL_CONF:'/dev/null'}});assert.equal(r.status,0,'OpenSSL failed');return r.stdout;}
before(async()=>{
  directory=await realpath(await mkdtemp(join(tmpdir(),'thth-activity-')));key=join(directory,'ephemeral.key');
  const pem=run(['genpkey','-algorithm','RSA','-pkeyopt','rsa_keygen_bits:3072']);sensitive.push(pem.toString());await writeFile(key,pem,{mode:0o600});
  const publicKey=run(['pkey','-in',key,'-pubout','-outform','DER']).toString('base64url');
  const files=['test/person-harness.js','src/worker.js','src/media.js','src/media-object.js','src/index.js','src/relay.js','src/relay-object.js','src/person.js','src/person-object.js','src/deletion.js','src/deletion-object.js','src/invite.js','src/invite-object.js','src/activity.js','src/api.js','src/login.js','src/login-object.js'];
  const modules=await Promise.all(files.map(async name=>{const path=fileURLToPath(new URL('../'+name,import.meta.url));return{type:'ESModule',path,contents:await readFile(path,'utf8')};}));
  mf=new Miniflare(convertV4MiniflareOptions({modules,modulesRoot:fileURLToPath(new URL('..',import.meta.url)),compatibilityDate:'2026-09-01',cf:false,
    log:new SilentLog(),handleStructuredLogs:item=>logs.push(JSON.stringify(item)),bindings:{RELAY_PUBLIC_KEY:publicKey,THTH_MCP_COMMAND:MCP_COMMAND},
    durableObjects:{AUTH_RELAY:{className:'AuthRelay',useSQLite:true},PERSON:{className:'TestPerson',useSQLite:true},ACCOUNT:{className:'TestAccount',useSQLite:true},DELETION_INBOX:{className:'TestDeletion',useSQLite:true},MEDIA_OBJECT:{className:'TestMedia',useSQLite:true}},r2Buckets:['MEDIA_BUCKET'],
    ratelimits:{DELETION_PUBLIC_LIMIT:{namespace_id:'21204',simple:{limit:120,period:60}},AUTH_RATE_LIMIT:{namespace_id:'21101',simple:{limit:120,period:60}},RELAY_PUBLIC_LIMIT:{namespace_id:'21201',simple:{limit:120,period:60}},RELAY_VERIFY_LIMIT:{namespace_id:'21202',simple:{limit:600,period:60}},RELAY_JOB_LIMIT:{namespace_id:'21203',simple:{limit:180,period:60}},MEDIA_CONTROL_LIMIT:{namespace_id:'21302',simple:{limit:600,period:60}},MEDIA_UPLOAD_LIMIT:{namespace_id:'21303',simple:{limit:240,period:60}},MEDIA_UPLOAD_IP_LIMIT:{namespace_id:'21304',simple:{limit:120,period:60}},MEDIA_PUBLIC_LIMIT:{namespace_id:'21301',simple:{limit:120,period:60}}}}));await mf.ready;
});
after(async()=>{await mf?.dispose();await rm(directory,{recursive:true,force:true});const hits=logs.filter(line=>sensitive.some(value=>line.includes(value))).length;assert.equal(hits,0,'secret in runtime logs');console.log('activity runtime log scan: '+logs.length+' chunks, '+hits+' hits');});
async function signed(type,subject,op,body={}){
  const path=`/relay/v/${type}/${subject}/${op}`,raw=JSON.stringify(body),time=Date.now(),nonce=opaque();
  const signature=run(['dgst','-sha256','-sign',key,'-sigopt','rsa_padding_mode:pss','-sigopt','rsa_pss_saltlen:32'],Buffer.from(canonical('POST',path,'operator',subject,op,time,nonce,hash(raw)))).toString('base64url');
  sensitive.push(signature,nonce);
  return mf.dispatchFetch(ORIGIN+path,{method:'POST',headers:{'content-type':'application/json','cf-connecting-ip':opaque(),'x-thth-time':String(time),'x-thth-nonce':nonce,'x-thth-signature':signature},body:raw});
}
async function control(type,id,settings){return(await mf.dispatchFetch(`${ORIGIN}/__person/${type}/${id}`,settings?{method:'POST',body:JSON.stringify(settings)}:{})).json();}
async function person(){const id='p'+randomBytes(8).toString('hex'),secret=opaque(),salt=opaque();sensitive.push(secret);
  const data={salt,verifier:pbkdf2Sync(secret,Buffer.from(salt,'base64url'),100000,32,'sha256').toString('base64url'),iterations:100000};sensitive.push(data.verifier);
  assert.equal((await signed('person',id,'set',data)).status,200);return{id,secret};}
const account=()=>'inv-a'+randomBytes(4).toString('hex');
const now=()=>new Date().toISOString().replace(/\.\d+Z$/,'+00:00');
function summary(name,extra={}){
  return {account:name,generated_at:now(),scheduled:true,
    limits:{daily_max_posts:8,daily_max_retracts:5,burst_count:3,burst_minutes:10,hold_minutes:0,min_interval_hours:6},
    stopped:null,today:{posts:1,retracts:0},credential:{id:'0123456789ab',expires_at:'2027-09-26T00:00:00+09:00',revoked:false},
    rows:[{kind:'published',at:now(),head:'出た投稿の先頭',post_id:'1789',draft_id:null,reason:null,reply:false},
          {kind:'held',at:now(),head:'猶予中の投稿',post_id:null,draft_id:hash('held'),reason:null,reply:false}],...extra};
}
async function sync(p,accounts,completed=[]){return signed('activity',p.id,'sync',{accounts,completed});}
const form=(fields,headers={})=>({method:'POST',headers:{'content-type':'application/x-www-form-urlencoded',origin:ORIGIN,'cf-connecting-ip':opaque(),...headers},body:new URLSearchParams(fields),redirect:'manual'});
const post=(fields,cookie)=>mf.dispatchFetch(ORIGIN+'/activity',form(fields,cookie?{cookie}:{}));
async function cookieOf(p){const r=await post({person:p.id,secret:p.secret});assert.equal(r.status,303);const c=r.headers.get('set-cookie');sensitive.push(c);return c.split(';')[0];}
const get=(path,cookie)=>mf.dispatchFetch(ORIGIN+path,{headers:{'cf-connecting-ip':opaque(),...(cookie?{cookie}:{})},redirect:'manual'});
const actions=async p=>new Map(await control('person',p.id));

test('sync is signed, closed-form and only for an existing approver',async()=>{
  const p=await person(),name=account();
  const unsigned=await mf.dispatchFetch(ORIGIN+`/approval/activity/${p.id}/sync`,{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({accounts:[],completed:[]})});
  assert.equal(unsigned.status,401);
  assert.equal((await signed('activity','nobody-'+randomBytes(4).toString('hex'),'sync',{accounts:[],completed:[]})).status,409);
  // 本文の全文・知らない項目・61 字の先頭は受けない。
  assert.equal((await sync(p,[summary(name,{text:'全文'})])).status,400);
  assert.equal((await sync(p,[summary(name,{rows:[{kind:'published',at:now(),head:'あ'.repeat(61),post_id:'1',draft_id:null,reason:null,reply:false}]})])).status,400);
  const ok=await sync(p,[summary(name)]);assert.equal(ok.status,200);assert.deepEqual(await ok.json(),{status:'synced',actions:[]});
});

// 3.13.0: deploy は Worker が先・VM があと。その間 3.12.0 の VM が要約に付けてくる approval は受けるが、置かない・見せない。
test('a 3.12.0 VM summary with approval is still accepted, but approval is neither kept nor shown',async()=>{
  const p=await person(),name=account();
  assert.equal((await sync(p,[summary(name,{approval:'all'})])).status,200);
  assert.equal((await sync(p,[summary(name,{approval:{mode:'all'}})])).status,400);
  const stored=JSON.stringify(await control('person',p.id));assert.ok(stored.includes(name)&&!stored.includes('"approval"'));
  const html=await(await get('/activity',await cookieOf(p))).text();
  assert.ok(html.includes(name)&&!html.includes('Approval:')&&!html.includes('承認 / Approval'),html);
});

test('sign-in shows only the pushed summary; no link to the removed /pending',async()=>{
  const p=await person(),name=account();await sync(p,[summary(name)]);
  const blank=await(await get('/activity')).text();assert.ok(blank.includes('autocomplete="username"')&&blank.includes('Activity')&&!blank.includes(name));
  assert.equal((await post({person:p.id,secret:'wrong-'+opaque()})).status,403);
  const ok=await post({person:p.id,secret:p.secret});assert.equal(ok.status,303);assert.equal(ok.headers.get('location'),'/activity');
  const raw=ok.headers.get('set-cookie');sensitive.push(raw);
  for(const part of ['Max-Age='+TTL/1000,'Path=/activity','Secure','HttpOnly','SameSite=Strict'])assert.ok(raw.split('; ').includes(part),part);
  const html=await(await get('/activity',raw.split(';')[0])).text();
  assert.ok(html.includes(name)&&html.includes('出た投稿の先頭')&&html.includes('猶予中の投稿')&&!html.includes('/pending'),html);
  assert.ok(!blank.includes('/pending'));
  assert.ok(html.includes('LLM の鍵を発行する / Issue a key for your LLM')&&html.includes('この口座を止める / Stop this account'));
  assert.equal((await get('/activity/')).status,308);
});

test('a stopped account shows its reason at the top of the page',async()=>{
  const p=await person(),name=account();
  await sync(p,[summary(name,{stopped:{reason:'burst',at:now()},rows:[{kind:'stopped',at:now(),head:'',post_id:null,draft_id:null,reason:'burst',reply:false}]})]);
  const html=await(await get('/activity',await cookieOf(p))).text();
  const banner=html.indexOf('は止まっています'),first=html.indexOf('<section>');
  assert.ok(banner>0&&banner<first,html);assert.ok(html.includes('急な連投で安全装置が止めました')&&html.includes('止めたのを戻す / Resume'));
});

test('operations need the secret again; nothing is queued without it, and the lock is the same five-in-a-row',async()=>{
  const p=await person(),name=account();await sync(p,[summary(name)]);const cookie=await cookieOf(p);
  // cookie だけ・secret なし・違う secret: どれも効かない。
  assert.equal((await post({act:'stop',account:name},cookie)).status,400);
  assert.equal((await post({act:'stop',account:name,secret:'wrong-'+opaque()},cookie)).status,403);
  assert.equal((await post({act:'stop',account:name,secret:p.secret})).status,401,'no cookie: sign in first');
  assert.equal((await post({act:'stop',account:name,secret:p.secret},'thth_activity='+opaque()+p.id)).status,401,'forged cookie');
  assert.ok(![...(await actions(p)).keys()].some(k=>k.startsWith('action:')));
  for(let n=0;n<4;n++)assert.equal((await post({act:'stop',account:name,secret:'wrong-'+opaque()},cookie)).status,403);
  assert.equal((await post({act:'stop',account:name,secret:p.secret},cookie)).status,403,'locked after five failures');
  assert.equal((await signed('person',p.id,'unlock')).status,200);
  const done=await post({act:'stop',account:name,secret:p.secret},cookie);assert.equal(done.status,200);
  assert.ok((await done.text()).includes('受け付けました'));
});

test('only your own accounts: B cannot see or act on A, and a cancel must name a listed draft',async()=>{
  const a=await person(),b=await person(),na=account(),nb=account();
  await sync(a,[summary(na)]);await sync(b,[summary(nb)]);
  const ca=await cookieOf(a),cb=await cookieOf(b);
  const pageB=await(await get('/activity',cb)).text();assert.ok(pageB.includes(nb)&&!pageB.includes(na));
  assert.equal((await post({act:'stop',account:na,secret:b.secret},cb)).status,400);
  assert.equal((await post({act:'cancel',account:na,draft_id:hash('held'),secret:b.secret},cb)).status,400);
  assert.equal((await post({act:'cancel',account:na,draft_id:hash('other'),secret:a.secret},ca)).status,400);
  assert.ok(![...(await actions(a)).keys(),...(await actions(b)).keys()].some(k=>k.startsWith('action:')));
  assert.equal((await post({act:'cancel',account:na,draft_id:hash('held'),secret:a.secret},ca)).status,200);
  assert.equal((await post({act:'settings',account:na,key:'owner',value:'1',secret:a.secret},ca)).status,400);
  // 3.13.0: approval の設定は無い。
  assert.equal((await post({act:'settings',account:na,key:'approval',value:'none',secret:a.secret},ca)).status,400);
  assert.equal((await post({act:'settings',account:na,key:'daily_max_posts',value:'none',secret:a.secret},ca)).status,400);
  assert.equal((await post({act:'settings',account:na,key:'daily_max_posts',value:'4',secret:a.secret},ca)).status,200);
});

test('requested operations reach the VM on sync and show their outcome; a finished one is not handed out again',async()=>{
  const p=await person(),name=account();await sync(p,[summary(name)]);const cookie=await cookieOf(p);
  assert.equal((await post({act:'settings',account:name,key:'daily_max_posts',value:'20',secret:p.secret},cookie)).status,200);
  assert.equal((await post({act:'resume',account:name,secret:p.secret},cookie)).status,200);
  const first=await(await sync(p,[summary(name)])).json();
  assert.equal(first.actions.length,2);
  const settings=first.actions.find(a=>a.kind==='settings');assert.deepEqual(Object.keys(settings).sort(),['account','id','key','kind','value']);
  assert.equal(settings.value,'20');
  let html=await(await get('/activity',cookie)).text();assert.ok(html.includes('反映待ち'),html);
  const second=await(await sync(p,[summary(name)],first.actions.map(a=>({id:a.id,outcome:a.kind==='settings'?'done':'failed',reason:a.kind==='settings'?null:'not_owner'})))).json();
  assert.deepEqual(second.actions,[]);
  html=await(await get('/activity',cookie)).text();assert.ok(html.includes('済み / Done')&&html.includes('(not_owner)'),html);
});

test('issuing a key shows it once with the MCP line; only its SHA-256 stays, and only until the VM takes it',async()=>{
  const p=await person(),name=account();await sync(p,[summary(name)]);const cookie=await cookieOf(p);
  const r=await post({act:'rotate',account:name,secret:p.secret},cookie);assert.equal(r.status,200);
  assert.equal(r.headers.get('cache-control'),'no-store');
  const html=await r.text(),bearer=/<pre>([A-Za-z0-9_-]{43})<\/pre>/.exec(html)?.[1];sensitive.push(bearer);
  assert.ok(bearer,html);assert.ok(html.includes(`claude mcp add thth -e THTH_REPORT_TOKEN=${bearer} -- ${MCP_COMMAND}`),html);
  assert.ok(html.includes('1 度だけ'));
  const stored=[...(await actions(p)).entries()].filter(([k])=>k.startsWith('action:'));
  assert.equal(stored.length,1);assert.equal(stored[0][1].sha256,hash(bearer));
  assert.ok(!JSON.stringify([...(await actions(p)).entries()]).includes(bearer),'bearer never stored');
  const handed=await(await sync(p,[summary(name)])).json();
  assert.deepEqual(handed.actions,[{id:stored[0][1].id,account:name,kind:'rotate',sha256:hash(bearer)}]);
  await sync(p,[summary(name)],[{id:stored[0][1].id,outcome:'done',reason:null}]);
  assert.ok(!JSON.stringify([...(await actions(p)).entries()]).includes(hash(bearer)),'hash dropped once done');
  const again=await(await get('/activity',cookie)).text();assert.ok(!again.includes(bearer));
  // ページから sha256 を持ち込めない。
  assert.equal((await post({act:'rotate',account:name,secret:p.secret,sha256:hash('x')},cookie)).status,400);
});

test('cross-site posts are refused',async()=>{
  const p=await person(),name=account();await sync(p,[summary(name)]);const cookie=await cookieOf(p);
  const init=form({act:'stop',account:name,secret:p.secret},{cookie,origin:'https://evil.test'});
  assert.equal((await mf.dispatchFetch(ORIGIN+'/activity',init)).status,403);
});
