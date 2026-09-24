// 3.11.0 承認待ちの一覧（thth.me/pending）。本人がユーザ名と承認 secret で入り、自分あての
// 承認待ちだけを見て、そこから承認ページを開く（docs/設計_3.11.0_承認待ちの一覧ページ_2026-09-25.md）。
import {test,before,after} from 'node:test';
import assert from 'node:assert/strict';
import {randomBytes,createHash,pbkdf2Sync} from 'node:crypto';
import {spawnSync} from 'node:child_process';
import {mkdtemp,writeFile,readFile,rm,realpath} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {Miniflare,Log,LogLevel,convertV4MiniflareOptions} from 'miniflare';
import {canonical,LIST_TTL,TTL} from '../src/approval.js';
const opaque=()=>randomBytes(32).toString('base64url'),hash=s=>createHash('sha256').update(s).digest('hex');
const logs=[],sensitive=[];
class SilentLog extends Log {constructor(){super(LogLevel.NONE);}log(value){logs.push(String(value));}}
let mf,directory,key;
const ORIGIN='https://approval.test';
const openssl=process.platform==='darwin'?'/opt/homebrew/bin/openssl':'/usr/bin/openssl';
function run(args,input){const r=spawnSync(openssl,args,{input,env:{PATH:'/usr/bin:/bin',OPENSSL_CONF:'/dev/null'}});assert.equal(r.status,0,'OpenSSL failed');return r.stdout;}
before(async()=>{
  directory=await realpath(await mkdtemp(join(tmpdir(),'thth-pending-')));key=join(directory,'ephemeral.key');
  const pem=run(['genpkey','-algorithm','RSA','-pkeyopt','rsa_keygen_bits:3072']);sensitive.push(pem.toString());await writeFile(key,pem,{mode:0o600});
  const publicKey=run(['pkey','-in',key,'-pubout','-outform','DER']).toString('base64url');
  const files=['test/approval-harness.js','src/worker.js','src/media.js','src/media-object.js','src/index.js','src/relay.js','src/relay-object.js','src/approval.js','src/approval-object.js','src/deletion.js','src/deletion-object.js','src/invite.js','src/invite-object.js'];
  const modules=await Promise.all(files.map(async name=>{const path=fileURLToPath(new URL('../'+name,import.meta.url));return{type:'ESModule',path,contents:await readFile(path,'utf8')};}));
  mf=new Miniflare(convertV4MiniflareOptions({modules,modulesRoot:fileURLToPath(new URL('..',import.meta.url)),compatibilityDate:'2026-09-01',cf:false,
    log:new SilentLog(),handleStructuredLogs:item=>logs.push(JSON.stringify(item)),bindings:{APPROVAL_PUBLIC_KEY:publicKey},
    durableObjects:{AUTH_RELAY:{className:'AuthRelay',useSQLite:true},APPROVAL_PERSON:{className:'TestPerson',useSQLite:true},APPROVAL_SESSION:{className:'TestSession',useSQLite:true},APPROVAL_ACCOUNT:{className:'TestAccount',useSQLite:true},DELETION_INBOX:{className:'TestDeletion',useSQLite:true},MEDIA_OBJECT:{className:'TestMedia',useSQLite:true}},r2Buckets:['MEDIA_BUCKET'],
    ratelimits:{DELETION_PUBLIC_LIMIT:{namespace_id:'21204',simple:{limit:120,period:60}},AUTH_RATE_LIMIT:{namespace_id:'21101',simple:{limit:120,period:60}},APPROVAL_PUBLIC_LIMIT:{namespace_id:'21201',simple:{limit:120,period:60}},APPROVAL_VERIFY_LIMIT:{namespace_id:'21202',simple:{limit:600,period:60}},APPROVAL_JOB_LIMIT:{namespace_id:'21203',simple:{limit:180,period:60}},MEDIA_CONTROL_LIMIT:{namespace_id:'21302',simple:{limit:600,period:60}},MEDIA_UPLOAD_LIMIT:{namespace_id:'21303',simple:{limit:240,period:60}},MEDIA_UPLOAD_IP_LIMIT:{namespace_id:'21304',simple:{limit:120,period:60}},MEDIA_PUBLIC_LIMIT:{namespace_id:'21301',simple:{limit:120,period:60}}}}));await mf.ready;
});
after(async()=>{await mf?.dispose();await rm(directory,{recursive:true,force:true});const hits=logs.filter(line=>sensitive.some(value=>line.includes(value))).length;assert.equal(hits,0,'secret in runtime logs');console.log('pending runtime log scan: '+logs.length+' chunks, '+hits+' hits');});
async function signed(type,subject,op,body={}){
  const path=`/approval/${type}/${subject}/${op}`,raw=JSON.stringify(body),time=Date.now(),nonce=opaque();
  const signature=run(['dgst','-sha256','-sign',key,'-sigopt','rsa_padding_mode:pss','-sigopt','rsa_pss_saltlen:32'],Buffer.from(canonical('POST',path,type==='session'?'job':'operator',subject,op,time,nonce,hash(raw)))).toString('base64url');
  sensitive.push(signature,nonce);
  return mf.dispatchFetch(ORIGIN+path,{method:'POST',headers:{'content-type':'application/json','cf-connecting-ip':opaque(),'x-thth-time':String(time),'x-thth-nonce':nonce,'x-thth-signature':signature},body:raw});
}
async function control(type,id,settings){return(await mf.dispatchFetch(`${ORIGIN}/__approval/${type}/${id}`,settings?{method:'POST',body:JSON.stringify(settings)}:{})).json();}
async function person(){const id='p'+randomBytes(8).toString('hex'),secret=opaque(),salt=opaque();sensitive.push(secret);
  const data={salt,verifier:pbkdf2Sync(secret,Buffer.from(salt,'base64url'),100000,32,'sha256').toString('base64url'),iterations:100000};sensitive.push(data.verifier);
  assert.equal((await signed('person',id,'set',data)).status,200);return{id,secret,data};}
// 一覧に出す（listed: true）のが既定。VM と同じ形の create。
async function session(p,options={}){const token=opaque(),readKey=opaque(),text=options.text??('本文 '+opaque());sensitive.push(token,readKey,text);
  const body={person:p.id,job_id:opaque(),digest:hash(text),account:options.account??p.account??'alpha',kind:'send',text,read_key_hash:hash(readKey),
    context:{media:'threads',topic:null,options:null,reply_to:null,publish_at:null,target:null,reason:null},listed:true,...options.extra};
  if(options.listed===false)delete body.listed;
  const created=await signed('session',token,'create',body);assert.equal(created.status,201);
  return{token,readKey,body,created:await created.json()};}
const form=(fields,headers={})=>({method:'POST',headers:{'content-type':'application/x-www-form-urlencoded',origin:ORIGIN,'cf-connecting-ip':opaque(),...headers},body:new URLSearchParams(fields),redirect:'manual'});
async function signIn(p,secret=p.secret,headers={}){return mf.dispatchFetch(ORIGIN+'/pending',form({person:p.id,secret},headers));}
async function cookieOf(p){const r=await signIn(p);assert.equal(r.status,303);const c=r.headers.get('set-cookie');sensitive.push(c);return c.split(';')[0];}
const get=(path,cookie,ip=opaque())=>mf.dispatchFetch(ORIGIN+path,{headers:{'cf-connecting-ip':ip,...(cookie?{cookie}:{})},redirect:'manual'});
const csrf=html=>/name="csrf" value="([^"]+)"/.exec(html)?.[1];
const account=()=>'a'+randomBytes(6).toString('hex');

test('sign-in: only the right secret opens the list; the cookie is short, strict and scoped to /pending',async()=>{
  const p={...await person(),account:account()},s=await session(p);
  const blank=await(await get('/pending')).text();
  assert.ok(blank.includes('autocomplete="username"')&&blank.includes('Pending approvals')&&!blank.includes(s.body.text.slice(0,10)));
  for(const response of [await signIn(p,'wrong-'+opaque()),await signIn({id:'nobody-'+randomBytes(4).toString('hex')},p.secret),await signIn(p,'short')]){
    assert.equal(response.status,403);assert.equal(response.headers.get('set-cookie'),null);
    const html=await response.text();assert.ok(html.includes('Sign-in failed')&&!html.includes(s.body.text));
  }
  const ok=await signIn(p);assert.equal(ok.status,303);assert.equal(ok.headers.get('location'),'/pending');
  const raw=ok.headers.get('set-cookie');sensitive.push(raw);
  for(const part of ['Max-Age='+TTL/1000,'Path=/pending','Secure','HttpOnly','SameSite=Strict'])assert.ok(raw.split('; ').includes(part),part);
  const cookie=raw.split(';')[0],html=await(await get('/pending',cookie)).text();
  assert.ok(html.includes(s.body.text)&&html.includes('href="/pending/'+s.body.job_id+'"'),html);
  // A cookie that names the person but carries a token that never signed in shows nothing.
  const forged=await get('/pending','thth_pending='+opaque()+p.id);const forgedHtml=await forged.text();
  assert.ok(!forgedHtml.includes(s.body.text)&&forgedHtml.includes('autocomplete="username"'));
  assert.ok(forged.headers.get('set-cookie').includes('Max-Age=0'));
});

test('five wrong sign-ins close the list only: approvals still work, the operator unlock reopens it',async()=>{
  const p={...await person(),account:account()},s=await session(p);
  for(let n=0;n<5;n++)assert.equal((await signIn(p,'wrong-'+opaque())).status,403);
  assert.equal((await signIn(p)).status,403);
  const state=new Map(await control('person',p.id)).get('person');assert.equal(state.failures,0);assert.equal(state.list_failures,5);
  assert.equal((await(await signed('person',p.id,'status')).json()).locked,false);
  const direct=await(await get('/approve/'+s.token)).text();
  const approved=await mf.dispatchFetch(ORIGIN+'/approve/'+s.token,form({secret:p.secret,csrf:csrf(direct)}));assert.equal(approved.status,200);
  assert.equal((await signed('person',p.id,'unlock')).status,200);assert.equal((await signIn(p)).status,303);
});

test('the list shares the approval page quota: a flood from one address gets 429',async()=>{
  const ip=opaque();let limited=0;
  for(let n=0;n<125;n++)if((await get('/pending',null,ip)).status===429)limited++;
  assert.ok(limited>0,'no 429 after 125 requests');
});

test('only your own approvals: A never sees B, even when a foreign row sits in the index',async()=>{
  const a={...await person(),account:account()},b={...await person(),account:account()};
  const sa=await session(a),sb=await session(b),ca=await cookieOf(a),cb=await cookieOf(b);
  const listA=await(await get('/pending',ca)).text(),listB=await(await get('/pending',cb)).text();
  assert.ok(listA.includes(sa.body.text)&&!listA.includes(sb.body.text));assert.ok(listB.includes(sb.body.text)&&!listB.includes(sa.body.text));
  let opened=await get('/pending/'+sb.body.job_id,ca);assert.equal(opened.status,404);assert.ok(!(await opened.text()).includes(sb.body.text));
  // B's session in A's index (a broken index): the session itself refuses to show it to A.
  await control('person',a.id,{inject:{token:sb.token,job_id:sb.body.job_id,expires_at:sb.created.expires_at}});
  const again=await(await get('/pending',ca)).text();assert.ok(again.includes(sa.body.text)&&!again.includes(sb.body.text),again);
  opened=await get('/pending/'+sb.body.job_id,ca);assert.equal(opened.status,404);assert.ok(!(await opened.text()).includes(sb.body.text));
  const pageB=await(await get('/approve/'+sb.token)).text();
  const posted=await mf.dispatchFetch(ORIGIN+'/pending/'+sb.body.job_id,form({secret:a.secret,csrf:csrf(pageB)},{cookie:ca}));
  assert.equal(posted.status,404);assert.equal(new Map(await control('session',sb.token)).get('session').status,'pending');
});

test('open from the list: the page restarts its 10 minutes, approval leaves the list and the receipt still binds',async()=>{
  const p={...await person(),account:account()},before=Date.now(),s=await session(p),after=Date.now();
  assert.ok(s.created.expires_at>=before+LIST_TTL&&s.created.expires_at<=after+LIST_TTL,'listed job waits 24 hours');
  const plain=await session(p,{listed:false});assert.ok(plain.created.expires_at<=Date.now()+TTL);
  const cookie=await cookieOf(p),list=await(await get('/pending',cookie)).text();
  assert.ok(list.includes('期限まで / Expires in: 1440 分 / min'),list);assert.ok(!list.includes(plain.body.text),'unlisted job stays off the list');
  let page=await(await get('/pending/'+s.body.job_id,cookie)).text();
  assert.ok(page.includes('残り 10 分')&&page.includes('10 minutes after it was opened')&&page.includes('<pre>'+s.body.text+'</pre>'),page);
  const row=new Map(await control('session',s.token)).get('session');
  await control('session',s.token,{clock:row.page_until+1});
  const late=await mf.dispatchFetch(ORIGIN+'/pending/'+s.body.job_id,form({secret:p.secret,csrf:csrf(page)},{cookie}));
  assert.equal(late.status,410);assert.ok((await late.text()).includes('Open it again from the list'));
  page=await(await get('/pending/'+s.body.job_id,cookie)).text();assert.ok(page.includes('残り 10 分'));
  const approved=await mf.dispatchFetch(ORIGIN+'/pending/'+s.body.job_id,form({secret:p.secret,csrf:csrf(page)},{cookie}));
  assert.equal(approved.status,200);const done=await approved.text();assert.ok(done.includes('Publication approval received')&&done.includes('href="/pending"'));
  await control('session',s.token,{});
  const empty=await(await get('/pending',cookie)).text();assert.ok(empty.includes('Nothing is waiting')&&!empty.includes(s.body.text));
  const receipt=await signed('session',s.token,'consume',{read_key:s.readKey});assert.equal(receipt.status,200);
  const value=await receipt.json();assert.equal(value.expires_at,s.created.expires_at);assert.equal(value.approver,p.id);
});

test('24 hours is the ceiling: opened near the end, the page keeps only what is left; images keep 10 minutes',async()=>{
  const p={...await person(),account:account()},s=await session(p),cookie=await cookieOf(p);
  await control('session',s.token,{clock:s.created.expires_at-5*60_000});
  const list=await(await get('/pending',cookie)).text();assert.ok(list.includes('期限まで / Expires in: 5 分 / min'),list);
  const page=await(await get('/pending/'+s.body.job_id,cookie)).text();assert.ok(page.includes('残り 5 分'),page);
  assert.equal(new Map(await control('session',s.token)).get('session').page_until,s.created.expires_at);
  await control('session',s.token,{clock:s.created.expires_at});
  assert.ok(!(await(await get('/pending',cookie)).text()).includes(s.body.text));assert.equal((await get('/pending/'+s.body.job_id,cookie)).status,410);
  // A preview capability cannot outlive 10 minutes, so a page that shows images is not extended.
  const image={index:1,role:'media',kind:'image',format:'png',public_sha256:hash('x'),public_size:1,width:1,height:1,duration:null,alt:'a',preview:opaque()};
  const before=Date.now(),withImage=await session(p,{extra:{attachments:[image]}});
  assert.ok(withImage.created.expires_at<=Date.now()+TTL&&withImage.created.expires_at>=before+TTL);
  const shown=await(await get('/pending',cookie)).text();assert.ok(shown.includes('添付 / Attachments: あり / yes')&&shown.includes(withImage.body.text));
});

test('the list shows at most the first 60 characters of the text, escaped',async()=>{
  const p={...await person(),account:account()},tail='TAIL'+randomBytes(4).toString('hex');
  const text='<b>'+'あ'.repeat(56)+'い'+'う'.repeat(30)+tail;
  const s=await session(p,{text}),html=await(await get('/pending',await cookieOf(p))).text();
  assert.ok(html.includes('<pre>&lt;b&gt;'+'あ'.repeat(56)+'い…</pre>'),html);
  assert.ok(!html.includes('いう')&&!html.includes(tail)&&!html.includes('<b>'));
  assert.ok((await(await get('/pending/'+s.body.job_id,await cookieOf(p))).text()).includes(tail),'the approval page shows the whole text');
});

test('same origin only: cross-site sign-in and approval POSTs are refused; headers match the approval page',async()=>{
  const p={...await person(),account:account()},s=await session(p);
  for(const headers of [{origin:'null','sec-fetch-site':'cross-site'},{origin:'https://evil.test'},{'sec-fetch-site':'cross-site',origin:undefined}]){
    const init=form({person:p.id,secret:p.secret},headers);if(headers.origin===undefined)delete init.headers.origin;
    assert.equal((await mf.dispatchFetch(ORIGIN+'/pending',init)).status,403,JSON.stringify(headers));
  }
  const nullOrigin=form({person:p.id,secret:p.secret},{origin:'null','sec-fetch-site':'same-origin'});
  const ok=await mf.dispatchFetch(ORIGIN+'/pending',nullOrigin);assert.equal(ok.status,303);const cookie=ok.headers.get('set-cookie').split(';')[0];sensitive.push(cookie);
  const page=await(await get('/pending/'+s.body.job_id,cookie)).text();
  assert.equal((await mf.dispatchFetch(ORIGIN+'/pending/'+s.body.job_id,form({secret:p.secret,csrf:csrf(page)},{cookie,origin:'null','sec-fetch-site':'cross-site'}))).status,403);
  const approvalHeaders=(await get('/approve/'+s.token)).headers;
  for(const path of ['/pending','/pending/'+s.body.job_id]){
    const response=await get(path,cookie),html=await response.text();
    for(const name of ['content-security-policy','cache-control','referrer-policy','x-frame-options','x-content-type-options'])assert.equal(response.headers.get(name),approvalHeaders.get(name),name);
    assert.ok(!html.includes('<script'));
  }
  assert.equal((await get('/pending?x=1')).status,400);assert.equal((await get('/pending/abc')).status,404);
  assert.equal((await mf.dispatchFetch(ORIGIN+'/pending',{method:'HEAD',headers:{'cf-connecting-ip':opaque()}})).status,405);
});

test('without a list session nothing opens; sign-out and a reissued secret close the list; cancel and revoke empty it',async()=>{
  const p={...await person(),account:account()},s=await session(p);
  const bare=await get('/pending/'+s.body.job_id);assert.equal(bare.status,401);assert.ok(!(await bare.text()).includes(s.body.text));
  let cookie=await cookieOf(p);
  const left=await mf.dispatchFetch(ORIGIN+'/pending',form({leave:'1'},{cookie}));assert.equal(left.status,303);assert.ok(left.headers.get('set-cookie').includes('Max-Age=0'));
  assert.ok(!(await(await get('/pending',cookie)).text()).includes(s.body.text));
  cookie=await cookieOf(p);assert.ok((await(await get('/pending',cookie)).text()).includes(s.body.text));
  assert.equal((await signed('session',s.token,'cancel',{read_key:s.readKey})).status,200);
  assert.ok(!(await(await get('/pending',cookie)).text()).includes(s.body.text),'cancelled approval leaves the list');
  const t=await session(p);assert.ok((await(await get('/pending',cookie)).text()).includes(t.body.text));
  assert.equal((await signed('account',p.account,'revoke',{})).status,200);
  assert.ok(!(await(await get('/pending',cookie)).text()).includes(t.body.text),'revoked account leaves the list');
  const u=await session({...p,account:account()});assert.ok((await(await get('/pending',cookie)).text()).includes(u.body.text));
  assert.equal((await signed('person',p.id,'set',p.data)).status,200);
  const reissued=await(await get('/pending',cookie)).text();assert.ok(!reissued.includes(u.body.text)&&reissued.includes('autocomplete="username"'));
});
