// 招待リンク（3.10.0）の通し: 本物の VM（Python の署名・預かり所・常駐の 1 巡）と本物の Worker。
// 偽物は Threads の token 交換だけ。code は Python の試験の橋渡しだけが stdout に出す。
import {pythonForTests} from './python-runtime.js';
import {test,before,after} from 'node:test';
import assert from 'node:assert/strict';
import {randomBytes,createHash} from 'node:crypto';
import {spawnSync} from 'node:child_process';
import {mkdtemp,writeFile,readFile,rm,realpath} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {Miniflare,Log,LogLevel,convertV4MiniflareOptions} from 'miniflare';
import {canonical} from '../src/person.js';
const opaque=()=>randomBytes(32).toString('base64url'),hash=s=>createHash('sha256').update(s).digest('hex');
class SilentLog extends Log {constructor(){super(LogLevel.NONE);}log(){}}
const openssl=process.platform==='darwin'?'/opt/homebrew/bin/openssl':'/usr/bin/openssl';
function crypto(args,input){const r=spawnSync(openssl,args,{input,env:{PATH:'/usr/bin:/bin',OPENSSL_CONF:'/dev/null'}});assert.equal(r.status,0);return r.stdout;}
let mf,origin,directory,key;
before(async()=>{
  directory=await realpath(await mkdtemp(join(tmpdir(),'thth-invite-vm-')));key=join(directory,'signer.key');
  await writeFile(key,crypto(['genpkey','-algorithm','RSA','-pkeyopt','rsa_keygen_bits:3072']),{mode:0o600});
  const publicKey=crypto(['pkey','-in',key,'-pubout','-outform','DER']).toString('base64url');
  const files=['src/worker.js','src/invite.js','src/invite-object.js','src/activity.js','src/media.js','src/media-object.js','src/index.js','src/relay.js','src/relay-object.js','src/person.js','src/person-object.js','src/deletion.js','src/deletion-object.js'];
  const modules=await Promise.all(files.map(async name=>{const path=fileURLToPath(new URL('../'+name,import.meta.url));return{type:'ESModule',path,contents:await readFile(path,'utf8')};}));
  mf=new Miniflare(convertV4MiniflareOptions({modules,modulesRoot:fileURLToPath(new URL('..',import.meta.url)),compatibilityDate:'2026-09-01',cf:false,log:new SilentLog(),
    bindings:{RELAY_PUBLIC_KEY:publicKey},
    durableObjects:{AUTH_RELAY:{className:'AuthRelay',useSQLite:true},PERSON:{className:'Person',useSQLite:true},ACCOUNT:{className:'Account',useSQLite:true},MEDIA_OBJECT:{className:'MediaObject',useSQLite:true},INVITE_OBJECT:{className:'InviteObject',useSQLite:true},DELETION_INBOX:{className:'DeletionInbox',useSQLite:true}},r2Buckets:['MEDIA_BUCKET'],
    ratelimits:{AUTH_RATE_LIMIT:{namespace_id:'21101',simple:{limit:1000,period:60}},RELAY_PUBLIC_LIMIT:{namespace_id:'21201',simple:{limit:1000,period:60}},RELAY_VERIFY_LIMIT:{namespace_id:'21202',simple:{limit:1000,period:60}},RELAY_JOB_LIMIT:{namespace_id:'21203',simple:{limit:1000,period:60}}}}));
  origin=String(await mf.ready).replace(/\/$/,'');
});
after(async()=>{await mf?.dispose();await rm(directory,{recursive:true,force:true});});
function vm(...args){
  const cwd=fileURLToPath(new URL('..',import.meta.url));
  const result=spawnSync(pythonForTests(),['test/invite-vm.py',...args],{cwd,encoding:'utf8',timeout:30000,env:{PATH:process.env.PATH,HOME:join(directory,'home'),
    PYTHONPATH:join(cwd,'..'),PYTHONDONTWRITEBYTECODE:'1',THTH_ROOT:join(directory,'vm'),THTH_ACCOUNTS_DIR:join(directory,'vm/accounts'),THTH_APPS_DIR:join(directory,'apps'),
    THTH_TEST_ALLOW_HTTP:'1',THTH_RELAY_BASE_URL:origin,THTH_AUTH_RELAY_BASE_URL:origin}});
  assert.equal(result.status,0,'VM step failed: '+(result.stderr||'').trim().split('\n').at(-1));assert.equal(result.stderr,'');
  return JSON.parse(result.stdout);
}
const page=async path=>{const response=await fetch(origin+path);return{status:response.status,html:await response.text()};};
const press=async(path,action,csrf)=>fetch(origin+path,{method:'POST',redirect:'manual',headers:{'content-type':'application/x-www-form-urlencoded'},body:new URLSearchParams({csrf,action})});

test('招待の通し: create → 開く → 押す → 常駐が認可 URL → 認可 → 口座 → secret を 1 回 → approver_set',{timeout:120000},async()=>{
  const created=vm('create',key),path='/invite/'+created.code;
  let view=await page(path);assert.equal(view.status,200);assert.ok(view.html.includes('Threads で認可する'));
  const csrf=/name="csrf" value="([^"]+)"/.exec(view.html)[1];
  assert.equal((await press(path,'start',csrf)).status,303);
  assert.equal(vm('run').status,'authorizing');
  view=await page(path);const link=/class="go" href="([^"]+)"/.exec(view.html)[1].replaceAll('&amp;','&');
  const url=new URL(link);assert.equal(url.origin+url.pathname,'https://threads.net/oauth/authorize');
  assert.equal(url.searchParams.get('redirect_uri'),'https://thth.me/callback/');assert.ok(!url.searchParams.get('scope').includes('share_to_instagram'));
  // Threads で承認した戻り（/callback/）。預かり所が code を VM のために預かる。
  const back=await page('/callback/?state='+url.searchParams.get('state')+'&code='+opaque());assert.ok(back.html.includes('招待のページ'));
  const used=vm('run');assert.equal(used.status,'used');
  assert.deepEqual(used.events,['invite_created','account_added','production_enabled','token_set','invite_used']);
  view=await page(path);assert.ok(view.html.includes('@reviewer.local')&&view.html.includes(created.account));
  const shown=await(await press(path,'reveal',csrf)).text();const secret=/class="secret">([A-Za-z0-9_-]{43})</.exec(shown)[1];
  assert.equal((await page(path)).status,410);
  const done=vm('run');assert.equal(done.approver_set,true);assert.equal(done.events.at(-1),'approver_set');
  // 口座の secret の持ち主（person＝口座名）が Worker で有効になっている。
  const status=await(await signed('person',created.account,'status',{},'operator')).json();
  assert.equal(status.active,true);assert.equal(status.locked,false);
  assert.ok(secret.length===43&&!JSON.stringify(done).includes(secret));
  // 3.13.0: 承認ページは無い。完了ページの secret で動きの一覧（/activity）に入れる。
  assert.equal((await page('/approve/'+opaque())).status,404);
  const signedIn=await fetch(origin+'/activity',{method:'POST',redirect:'manual',headers:{'content-type':'application/x-www-form-urlencoded'},body:new URLSearchParams({person:created.account,secret})});
  assert.equal(signedIn.status,303);assert.equal(signedIn.headers.get('location'),'/activity');
});
async function signed(type,subject,operation,body,role){
  const path=`/relay/v/${type}/${subject}/${operation}`,raw=JSON.stringify(body),time=Date.now(),nonce=opaque();
  const signature=crypto(['dgst','-sha256','-sign',key,'-sigopt','rsa_padding_mode:pss','-sigopt','rsa_pss_saltlen:32'],Buffer.from(canonical('POST',path,role,subject,operation,time,nonce,hash(raw)))).toString('base64url');
  return fetch(origin+path,{method:'POST',headers:{'content-type':'application/json','x-thth-time':String(time),'x-thth-nonce':nonce,'x-thth-signature':signature},body:raw});
}
