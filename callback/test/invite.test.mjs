// 招待リンク（設計 3.10.0）の Worker 側。開く・押す・準備・認可・完了・secret の 1 回表示・410。
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
const openssl=process.platform==='darwin'?'/opt/homebrew/bin/openssl':'/usr/bin/openssl';
function run(args,input){const r=spawnSync(openssl,args,{input,env:{PATH:'/usr/bin:/bin',OPENSSL_CONF:'/dev/null'}});assert.equal(r.status,0,'OpenSSL failed');return r.stdout;}
const ORIGIN='https://invite.test';
const SCOPES=['threads_basic','threads_content_publish','threads_delete'];
before(async()=>{
  directory=await realpath(await mkdtemp(join(tmpdir(),'thth-invite-')));key=join(directory,'ephemeral.key');
  const pem=run(['genpkey','-algorithm','RSA','-pkeyopt','rsa_keygen_bits:3072']);sensitive.push(pem.toString());await writeFile(key,pem,{mode:0o600});
  const publicKey=run(['pkey','-in',key,'-pubout','-outform','DER']).toString('base64url');
  const files=['test/invite-harness.js','src/worker.js','src/invite.js','src/invite-object.js','src/activity.js','src/api.js','src/media.js','src/media-object.js','src/index.js','src/relay.js','src/relay-object.js','src/person.js','src/person-object.js','src/deletion.js','src/deletion-object.js'];
  const modules=await Promise.all(files.map(async name=>{const path=fileURLToPath(new URL('../'+name,import.meta.url));return{type:'ESModule',path,contents:await readFile(path,'utf8')};}));
  mf=new Miniflare(convertV4MiniflareOptions({modules,modulesRoot:fileURLToPath(new URL('..',import.meta.url)),compatibilityDate:'2026-09-01',cf:false,
    log:new SilentLog(),handleStructuredLogs:item=>logs.push(JSON.stringify(item)),bindings:{RELAY_PUBLIC_KEY:publicKey},
    durableObjects:{AUTH_RELAY:{className:'AuthRelay',useSQLite:true},PERSON:{className:'TestPerson',useSQLite:true},ACCOUNT:{className:'Account',useSQLite:true},MEDIA_OBJECT:{className:'MediaObject',useSQLite:true},INVITE_OBJECT:{className:'TestInvite',useSQLite:true}},r2Buckets:['MEDIA_BUCKET'],
    ratelimits:{AUTH_RATE_LIMIT:{namespace_id:'21101',simple:{limit:1000,period:60}},RELAY_PUBLIC_LIMIT:{namespace_id:'21201',simple:{limit:1000,period:60}},RELAY_VERIFY_LIMIT:{namespace_id:'21202',simple:{limit:1000,period:60}},RELAY_JOB_LIMIT:{namespace_id:'21203',simple:{limit:1000,period:60}}}}));await mf.ready;
});
after(async()=>{await mf?.dispose();await rm(directory,{recursive:true,force:true});const hits=logs.filter(line=>sensitive.some(value=>line.includes(value))).length;assert.equal(hits,0,'secret in runtime logs');});
async function signed(type,subject,op,body={}){
  const path=`/relay/v/${type}/${subject}/${op}`,raw=JSON.stringify(body),time=Date.now(),nonce=opaque();
  const signature=run(['dgst','-sha256','-sign',key,'-sigopt','rsa_padding_mode:pss','-sigopt','rsa_pss_saltlen:32'],Buffer.from(canonical('POST',path,'operator',subject,op,time,nonce,hash(raw)))).toString('base64url');
  return mf.dispatchFetch(ORIGIN+path,{method:'POST',headers:{'content-type':'application/json','cf-connecting-ip':opaque(),'x-thth-time':String(time),'x-thth-nonce':nonce,'x-thth-signature':signature},body:raw});
}
const inspect=async(type,id,settings)=>(await mf.dispatchFetch(`${ORIGIN}/__invite/${type}/${id}`,settings?{method:'POST',body:JSON.stringify(settings)}:{})).json();
const row=async id=>new Map(await inspect('invite',id)).get('invite');
async function invite(options={}){
  const code=opaque(),id=hash(code);sensitive.push(code);
  const body={media:'threads',production:true,expires_at:Date.now()+30*86_400_000,scopes:SCOPES,...options};
  const response=await signed('invite',id,'create',body);assert.equal(response.status,200);assert.deepEqual(await response.json(),{status:'open'});
  return{code,id};
}
const view=inv=>mf.dispatchFetch(ORIGIN+'/invite/'+inv.code,{headers:{'cf-connecting-ip':opaque()}});
const csrfOf=async inv=>/name="csrf" value="([^"]+)"/.exec(await(await view(inv)).text())?.[1];
async function press(inv,action,{csrf,headers}={}){
  return mf.dispatchFetch(ORIGIN+'/invite/'+inv.code,{method:'POST',redirect:'manual',headers:{'content-type':'application/x-www-form-urlencoded','cf-connecting-ip':opaque(),...(headers??{origin:ORIGIN})},
    body:new URLSearchParams({csrf:csrf??await csrfOf(inv),action})});
}
const status=async inv=>(await(await signed('invite',inv.id,'status',{})).json());
const authorizeUrl=state=>'https://threads.net/oauth/authorize?client_id=1&redirect_uri=https%3A%2F%2Fthth.me%2Fcallback%2F&scope=threads_basic&response_type=code&state='+state;
async function toReady(inv,person='inv-meta-review-a1b2c3'){
  assert.equal((await press(inv,'start')).status,303);
  assert.equal((await signed('invite',inv.id,'authorize',{authorize_url:authorizeUrl(opaque()),expires_at:Date.now()+600_000})).status,200);
  assert.equal((await signed('invite',inv.id,'complete',{person,account:person,handle:'reviewer.test'})).status,200);
  return person;
}

test('開く: 説明と権限とリンク・作法（CSP・no-store・no-referrer・script なし）・code は保存しない',async()=>{
  const inv=await invite(),response=await view(inv),html=await response.text();
  assert.equal(response.status,200);assert.equal(response.headers.get('cache-control'),'no-store');assert.equal(response.headers.get('referrer-policy'),'no-referrer');
  assert.ok(response.headers.get('content-security-policy').includes("default-src 'none'"));assert.ok(response.headers.get('content-security-policy').includes("form-action 'self'"));
  assert.ok(!html.includes('<script'));for(const scope of SCOPES)assert.ok(html.includes(scope),scope);
  assert.ok(html.includes('href="/privacy/"')&&html.includes('href="/terms/"'));assert.ok(html.includes('本当に Threads へ投稿'));assert.ok(html.includes('Authorize with Threads'));
  const stored=JSON.stringify(await inspect('invite',inv.id));assert.ok(!stored.includes(inv.code));
  const dry=await invite({production:false});assert.ok((await(await view(dry)).text()).includes('試し撃ち'));
  const unknown={code:opaque()};assert.equal((await view(unknown)).status,410);assert.deepEqual(await inspect('invite',hash(unknown.code)),[]);
  assert.equal((await mf.dispatchFetch(ORIGIN+'/invite/'+inv.code+'?x=1')).status,400);assert.equal((await mf.dispatchFetch(ORIGIN+'/invite/short')).status,404);
});

test('押す: 同一 origin と csrf だけ・clicked になり準備中のページ（自動で再読込）',async()=>{
  const inv=await invite();
  assert.equal((await press(inv,'start',{headers:{origin:'https://evil.test'}})).status,403);
  assert.equal((await press(inv,'start',{headers:{origin:'null','sec-fetch-site':'cross-site'}})).status,403);
  assert.equal((await press(inv,'start',{csrf:opaque()})).status,403);
  assert.equal((await status(inv)).status,'open');
  const pressed=await press(inv,'start',{headers:{origin:'null','sec-fetch-site':'same-origin'}});
  assert.equal(pressed.status,303);assert.equal(pressed.headers.get('location'),'/invite/'+inv.code);
  const now=await status(inv);assert.equal(now.status,'clicked');assert.ok(Number.isInteger(now.clicked_at));
  const html=await(await view(inv)).text();assert.ok(html.includes('認可の準備中'));assert.ok(html.includes('http-equiv="refresh" content="3"'));
});

test('常駐が 2 分拾わなければ「運営者に連絡を」',async()=>{
  const inv=await invite();await press(inv,'start');const clicked=(await status(inv)).clicked_at;
  await inspect('invite',inv.id,{clock:clicked+119_000});assert.ok((await(await view(inv)).text()).includes('認可の準備中'));
  await inspect('invite',inv.id,{clock:clicked+120_000});const html=await(await view(inv)).text();
  assert.ok(html.includes('運営者に連絡してください'));assert.ok(html.includes('content="15"'));
});

test('認可 URL: Threads の authorize だけ・clicked のときだけ・10 分で消える',async()=>{
  const inv=await invite(),state=opaque(),expires=Date.now()+600_000;
  assert.equal((await signed('invite',inv.id,'authorize',{authorize_url:authorizeUrl(state),expires_at:expires})).status,409);
  await press(inv,'start');
  for(const bad of ['https://evil.test/oauth/authorize?state='+state,'http://threads.net/oauth/authorize?state='+state,
    'https://threads.net/oauth/authorize?state=short','https://threads.net/oauth/authorize?state='+state+'&state='+opaque(),
    'https://threads.net.evil.test/oauth/authorize?state='+state,'https://threads.net/oauth/authorize?state='+state+'"><b>'])
    assert.equal((await signed('invite',inv.id,'authorize',{authorize_url:bad,expires_at:expires})).status,400,bad);
  assert.equal((await signed('invite',inv.id,'authorize',{authorize_url:authorizeUrl(state),expires_at:Date.now()+3_600_000})).status,400);
  assert.equal((await signed('invite',inv.id,'authorize',{authorize_url:authorizeUrl(state),expires_at:expires})).status,200);
  const html=await(await view(inv)).text();assert.ok(html.includes('target="_blank" rel="noopener noreferrer"'));
  assert.ok(html.includes(authorizeUrl(state).replaceAll('&','&amp;')));assert.ok(html.includes('content="5"'));
  await inspect('invite',inv.id,{clock:expires});const later=await(await view(inv)).text();assert.ok(!later.includes(state));assert.ok(later.includes('認可の時間が過ぎました'));
  await inspect('invite',inv.id,{clock:expires,alarm:true});assert.equal((await row(inv.id)).authorize_url,null);
});

test('やり直し（reset）: 理由を見せて open に戻る・同じ招待をもう一度押せる',async()=>{
  const inv=await invite();await press(inv,'start');
  assert.equal((await signed('invite',inv.id,'reset',{reason:'nonsense'})).status,400);
  assert.equal((await signed('invite',inv.id,'reset',{reason:'account_exists'})).status,200);
  const html=await(await view(inv)).text();assert.ok(html.includes('既に THTH にあります'));assert.ok(html.includes('value="start"'));
  assert.equal((await press(inv,'start')).status,303);assert.equal((await status(inv)).status,'clicked');
});

test('完了と口座の secret: 1 回だけ表示・verifier は PBKDF2 100,000・2 回目は 410 で secret なし',async()=>{
  const inv=await invite(),person=await toReady(inv);
  const ready=await(await view(inv)).text();assert.ok(ready.includes('@reviewer.test')&&ready.includes(person)&&ready.includes('value="reveal"'));
  assert.equal((await press(inv,'reveal',{headers:{origin:'https://evil.test'}})).status,403);
  const nonce=await csrfOf(inv),first=await press(inv,'reveal',{csrf:nonce}),html=await first.text();
  assert.equal(first.status,200);assert.equal(first.headers.get('cache-control'),'no-store');
  const secret=/class="secret">([A-Za-z0-9_-]{43})</.exec(html)?.[1];assert.ok(secret,'secret shown');sensitive.push(secret);
  assert.ok(html.includes('一度だけ')&&html.includes('<code>'+person+'</code>')&&html.includes('<a href="/activity">https://thth.me/activity</a>')&&html.includes('sign in with this username and secret'));
  // 3.13.0: 承認待ちの一覧（/pending）は無い。完了ページのリンクと form は /activity だけ。
  assert.ok(!html.includes('/pending')&&!html.includes('approval page'),html);
  assert.ok(html.includes('保存して動きの一覧を開く / Save and open your activity</button>'));
  // 3.12.0 §6-4: 口座名（username）と secret が同じ form にあり、欄の名前は動きの一覧の入口と同じ。押すとそのまま入る。
  const saved=/<form method="post" action="\/activity">(.*?)<\/form>/s.exec(html)?.[1];assert.ok(saved,'save form');
  assert.ok(saved.includes('<input name="person" autocomplete="username" value="'+person+'" readonly>'),saved);
  assert.ok(/<input type="password" name="secret" autocomplete="new-password" value="[A-Za-z0-9_-]{43}" readonly>/.test(saved));
  assert.equal(/name="secret"[^>]*value="([^"]+)"/.exec(saved)[1]===secret,true);
  const listed=await mf.dispatchFetch(ORIGIN+'/activity',{method:'POST',redirect:'manual',headers:{'content-type':'application/x-www-form-urlencoded','cf-connecting-ip':opaque(),origin:ORIGIN},body:new URLSearchParams({person,secret})});
  assert.equal(listed.status,303);assert.equal(listed.headers.get('location'),'/activity');sensitive.push(listed.headers.get('set-cookie'));
  const stored=new Map(await inspect('person',person)).get('person');
  assert.equal(stored.iterations,100_000);assert.equal(stored.active,true);
  assert.equal(pbkdf2Sync(secret,Buffer.from(stored.salt,'base64url'),100_000,32,'sha256').toString('base64url'),stored.verifier);
  assert.ok(!JSON.stringify(await inspect('invite',inv.id)).includes(secret));assert.ok(!JSON.stringify(await inspect('person',person)).includes(secret));
  // 3.14.0 §3.3: アシスタントの鍵も同じページに 1 度だけ。Worker に残るのは hash だけ（VM は status で受け取る）。
  const assistant=/class="secret key">([A-Za-z0-9_-]{43})</.exec(html)?.[1];assert.ok(assistant,'assistant key shown');sensitive.push(assistant);
  assert.notEqual(assistant,secret);
  assert.ok(html.includes('アシスタントの鍵')&&html.includes('Assistant key')&&html.includes('<code>thth login</code> で 1 度だけ入れます')&&html.includes('表示は一度だけです'),html);
  assert.ok(!JSON.stringify(await inspect('invite',inv.id)).includes(assistant)&&!JSON.stringify(await inspect('person',person)).includes(assistant));
  const done=await status(inv);assert.equal(done.status,'done');assert.equal(done.key_sha256,hash(assistant));
  const second=await press(inv,'reveal',{csrf:nonce}),again=await second.text();
  assert.equal(second.status,410);assert.ok(!/class="secret"/.test(again));assert.ok(again.includes('使用済み'));
  assert.equal((await view(inv)).status,410);assert.equal((await press(inv,'start',{csrf:nonce})).status,410);
  const approver=await(await signed('person',person,'status',{})).json();assert.equal(approver.active,true);
});

test('同時に押しても secret は 1 つだけ',async()=>{
  const inv=await invite();await toReady(inv,'inv-meta-review-cccccc');const nonce=await csrfOf(inv);
  const responses=await Promise.all(Array.from({length:6},()=>press(inv,'reveal',{csrf:nonce})));
  const shown=(await Promise.all(responses.map(r=>r.text()))).filter(text=>/class="secret"/.test(text));
  assert.equal(shown.length,1);
});

test('招待は既存の持ち主を上書きしない',async()=>{
  const secret=opaque(),salt=opaque();sensitive.push(secret);
  const verifier=pbkdf2Sync(secret,Buffer.from(salt,'base64url'),100000,32,'sha256').toString('base64url');
  assert.equal((await signed('person','masaru','set',{salt,verifier,iterations:100000})).status,200);
  const inv=await invite();await toReady(inv,'masaru');
  const response=await press(inv,'reveal');assert.equal(response.status,503);assert.ok(!/class="secret"/.test(await response.text()));
  assert.equal(new Map(await inspect('person','masaru')).get('person').verifier,verifier);assert.equal((await status(inv)).status,'ready');
});

test('1 回きり: 完了した招待にもう一度 complete しても別の口座にはならない',async()=>{
  const inv=await invite(),person=await toReady(inv,'inv-meta-review-dddddd');
  assert.equal((await signed('invite',inv.id,'complete',{person,account:person,handle:'reviewer.test'})).status,200);
  assert.equal((await signed('invite',inv.id,'complete',{person:'inv-other',account:'inv-other',handle:'other'})).status,409);
  await press(inv,'reveal');
  assert.equal((await signed('invite',inv.id,'complete',{person,account:person,handle:'reviewer.test'})).status,409);
  assert.equal((await signed('invite',inv.id,'reset',{reason:'auth_failed'})).status,409);
});

test('取り消し: 開く前・途中は 410 に・用意が済んだら 409',async()=>{
  const inv=await invite();assert.deepEqual(await(await signed('invite',inv.id,'revoke',{})).json(),{status:'revoked'});
  assert.equal((await view(inv)).status,410);assert.equal((await press(inv,'start',{csrf:(await row(inv.id)).csrf})).status,410);
  const busy=await invite();await press(busy,'start');assert.equal((await signed('invite',busy.id,'revoke',{})).status,200);
  assert.equal((await signed('invite',busy.id,'authorize',{authorize_url:authorizeUrl(opaque()),expires_at:Date.now()+600_000})).status,410);
  const ready=await invite();await toReady(ready,'inv-meta-review-eeeeee');assert.equal((await signed('invite',ready.id,'revoke',{})).status,409);
  assert.equal((await signed('invite',hash(opaque()),'revoke',{})).status,404);
});

test('期限: 過ぎたら 410・状態は expired・alarm で消える',async()=>{
  const inv=await invite({expires_at:Date.now()+86_400_000}),expires=(await row(inv.id)).expires_at;
  await inspect('invite',inv.id,{clock:expires});
  assert.equal((await view(inv)).status,410);assert.equal((await press(inv,'start',{csrf:(await row(inv.id)).csrf})).status,410);
  assert.equal((await status(inv)).status,'expired');
  await inspect('invite',inv.id,{clock:expires,alarm:true});assert.deepEqual(await inspect('invite',inv.id),[]);
  assert.equal((await signed('invite',hash(opaque()),'create',{media:'threads',production:false,expires_at:Date.now()+91*86_400_000,scopes:SCOPES})).status,400);
  assert.equal((await signed('invite',inv.id,'create',{media:'mastodon',production:false,expires_at:Date.now()+86_400_000,scopes:SCOPES})).status,400);
});

test('作り直しは断る・署名なしは 401・subject は 64 桁の hash だけ',async()=>{
  const inv=await invite();
  assert.equal((await signed('invite',inv.id,'create',{media:'threads',production:false,expires_at:Date.now()+86_400_000,scopes:SCOPES})).status,409);
  const unsigned=await mf.dispatchFetch(ORIGIN+'/relay/v/invite/'+inv.id+'/status',{method:'POST',headers:{'content-type':'application/json','cf-connecting-ip':opaque()},body:'{}'});
  assert.equal(unsigned.status,401);
  assert.equal((await signed('invite',inv.code,'status',{})).status,400);
});

test('預かり所の戻り: 招待の認可なら「招待のタブに戻って」と言う（貼り付けの画面にしない）',async()=>{
  const inv=await invite(),state=opaque(),readKey=opaque(),code=opaque();sensitive.push(state,readKey,code);
  assert.equal((await mf.dispatchFetch(ORIGIN+'/relay/'+state,{method:'POST',headers:{'content-type':'application/json','cf-connecting-ip':opaque()},body:JSON.stringify({read_key_hash:hash(readKey)})})).status,201);
  await press(inv,'start');
  assert.equal((await signed('invite',inv.id,'authorize',{authorize_url:authorizeUrl(state),expires_at:Date.now()+600_000})).status,200);
  const back=await mf.dispatchFetch(ORIGIN+'/callback/?state='+state+'&code='+code,{headers:{'cf-connecting-ip':opaque()}});
  const html=await back.text();assert.equal(back.status,200);assert.ok(html.includes('招待のページ'));assert.ok(!html.includes(code));
  const got=await mf.dispatchFetch(ORIGIN+'/relay/'+state,{headers:{authorization:'Bearer '+readKey,'cf-connecting-ip':opaque()}});
  assert.equal(got.status,200);assert.equal((await got.json()).code,code);
});

test('wrangler.jsonc: INVITE_OBJECT の binding・v5-invite の migration・/invite/* は Worker を先に通す',async()=>{
  const text=(await readFile(fileURLToPath(new URL('../wrangler.jsonc',import.meta.url)),'utf8')).split('\n').filter(line=>!line.trim().startsWith('//')).join('\n');
  const config=JSON.parse(text);
  assert.ok(config.durable_objects.bindings.some(b=>b.name==='INVITE_OBJECT'&&b.class_name==='InviteObject'));
  assert.ok(config.migrations.some(m=>JSON.stringify(m)===JSON.stringify({tag:'v5-invite',new_sqlite_classes:['InviteObject']})));
  assert.ok(config.assets.run_worker_first.includes('/invite/*'));
});

// 3.13.0: ApprovalSession（承認ページの本文の預かり）は消した。binding を外し、既存の tag の続きに deleted_classes。
test('wrangler.jsonc: no APPROVAL_SESSION binding and a deleted_classes migration after v5-invite',async()=>{
  const text=(await readFile(fileURLToPath(new URL('../wrangler.jsonc',import.meta.url)),'utf8')).split('\n').filter(line=>!line.trim().startsWith('//')).join('\n');
  const config=JSON.parse(text);
  assert.ok(!config.durable_objects.bindings.some(b=>b.name==='APPROVAL_SESSION'||b.class_name==='ApprovalSession'));
  const tags=config.migrations.map(m=>m.tag);assert.equal(new Set(tags).size,tags.length);
  assert.equal(tags.indexOf('v5-invite')+1,tags.indexOf('v6-no-approval-session'));
  assert.deepEqual(config.migrations[tags.indexOf('v6-no-approval-session')],{tag:'v6-no-approval-session',deleted_classes:['ApprovalSession']});
  assert.ok(config.migrations.some(m=>(m.new_sqlite_classes??[]).includes('ApprovalSession')),'the class was created by an earlier migration');
});

// 3.13.0: DO の class と binding の名前を改めた（ApprovalPerson → Person・ApprovalAccount → Account）。
// 中身を失わないよう renamed_classes の migration を v6 の続きに置く。回数制限は名前だけ改め namespace_id は変えない。
test('wrangler.jsonc: Person/Account are renamed with a renamed_classes migration after v6, limits keep their namespaces',async()=>{
  const text=(await readFile(fileURLToPath(new URL('../wrangler.jsonc',import.meta.url)),'utf8')).split('\n').filter(line=>!line.trim().startsWith('//')).join('\n');
  const config=JSON.parse(text);
  const tags=config.migrations.map(m=>m.tag);
  assert.equal(tags.indexOf('v6-no-approval-session')+1,tags.indexOf('v7-rename-person'));
  assert.deepEqual(config.migrations[tags.indexOf('v7-rename-person')],{tag:'v7-rename-person',renamed_classes:[{from:'ApprovalPerson',to:'Person'},{from:'ApprovalAccount',to:'Account'}]});
  const bindings=Object.fromEntries(config.durable_objects.bindings.map(b=>[b.name,b.class_name]));
  assert.equal(bindings.PERSON,'Person');assert.equal(bindings.ACCOUNT,'Account');
  assert.ok(!('APPROVAL_PERSON' in bindings)&&!('APPROVAL_ACCOUNT' in bindings));
  const limits=Object.fromEntries(config.ratelimits.map(r=>[r.name,r.namespace_id]));
  assert.deepEqual([limits.RELAY_PUBLIC_LIMIT,limits.RELAY_VERIFY_LIMIT,limits.RELAY_JOB_LIMIT],['21201','21202','21203']);
  assert.ok(!Object.keys(limits).some(name=>name.startsWith('APPROVAL_')));
  // 旧 path は 1 版の間だけ Worker を先に通す。新 path は /relay/* に含まれる。
  assert.ok(config.assets.run_worker_first.includes('/approval/*')&&config.assets.run_worker_first.includes('/relay/*'));
});
