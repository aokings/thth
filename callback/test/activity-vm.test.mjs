// 動きの一覧（3.12.0 段 3）の通し: 本物の VM（Python の署名・要約・操作）と本物の Worker（/activity）。
import {pythonForTests} from './python-runtime.js';
import {test,before,after} from 'node:test';
import assert from 'node:assert/strict';
import {randomBytes,createHash,pbkdf2Sync} from 'node:crypto';
import {spawnSync} from 'node:child_process';
import {mkdtemp,writeFile,readFile,rm,realpath} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {Miniflare,Log,LogLevel,convertV4MiniflareOptions} from 'miniflare';
import {canonical} from '../src/approval.js';
const opaque=()=>randomBytes(32).toString('base64url'),hash=s=>createHash('sha256').update(s).digest('hex');
class SilentLog extends Log {constructor(){super(LogLevel.NONE);}log(){}}
const openssl=process.platform==='darwin'?'/opt/homebrew/bin/openssl':'/usr/bin/openssl';
function crypto(args,input){const r=spawnSync(openssl,args,{input,env:{PATH:'/usr/bin:/bin',OPENSSL_CONF:'/dev/null'}});assert.equal(r.status,0);return r.stdout;}
let mf,origin,directory,key;
before(async()=>{
  directory=await realpath(await mkdtemp(join(tmpdir(),'thth-activity-vm-')));key=join(directory,'signer.key');
  await writeFile(key,crypto(['genpkey','-algorithm','RSA','-pkeyopt','rsa_keygen_bits:3072']),{mode:0o600});
  const publicKey=crypto(['pkey','-in',key,'-pubout','-outform','DER']).toString('base64url');
  const files=['src/worker.js','src/invite.js','src/invite-object.js','src/activity.js','src/media.js','src/media-object.js','src/index.js','src/relay.js','src/relay-object.js','src/approval.js','src/approval-object.js','src/deletion.js','src/deletion-object.js'];
  const modules=await Promise.all(files.map(async name=>{const path=fileURLToPath(new URL('../'+name,import.meta.url));return{type:'ESModule',path,contents:await readFile(path,'utf8')};}));
  mf=new Miniflare(convertV4MiniflareOptions({modules,modulesRoot:fileURLToPath(new URL('..',import.meta.url)),compatibilityDate:'2026-09-01',cf:false,log:new SilentLog(),
    bindings:{APPROVAL_PUBLIC_KEY:publicKey},
    durableObjects:{AUTH_RELAY:{className:'AuthRelay',useSQLite:true},APPROVAL_PERSON:{className:'ApprovalPerson',useSQLite:true},APPROVAL_SESSION:{className:'ApprovalSession',useSQLite:true},APPROVAL_ACCOUNT:{className:'ApprovalAccount',useSQLite:true},MEDIA_OBJECT:{className:'MediaObject',useSQLite:true},INVITE_OBJECT:{className:'InviteObject',useSQLite:true},DELETION_INBOX:{className:'DeletionInbox',useSQLite:true}},r2Buckets:['MEDIA_BUCKET'],
    ratelimits:{AUTH_RATE_LIMIT:{namespace_id:'21101',simple:{limit:1000,period:60}},APPROVAL_PUBLIC_LIMIT:{namespace_id:'21201',simple:{limit:1000,period:60}},APPROVAL_VERIFY_LIMIT:{namespace_id:'21202',simple:{limit:1000,period:60}},APPROVAL_JOB_LIMIT:{namespace_id:'21203',simple:{limit:1000,period:60}}}}));
  origin=String(await mf.ready).replace(/\/$/,'');
});
after(async()=>{await mf?.dispose();await rm(directory,{recursive:true,force:true});});
function vm(...args){
  const cwd=fileURLToPath(new URL('..',import.meta.url));
  const result=spawnSync(pythonForTests(),['test/activity-vm.py',...args],{cwd,encoding:'utf8',timeout:60000,env:{PATH:process.env.PATH,HOME:join(directory,'home'),
    PYTHONPATH:join(cwd,'..'),PYTHONDONTWRITEBYTECODE:'1',THTH_ROOT:join(directory,'vm'),THTH_ACCOUNTS_DIR:join(directory,'vm/accounts'),THTH_APPS_DIR:join(directory,'apps'),
    THTH_TEST_ALLOW_HTTP:'1',THTH_APPROVAL_BASE_URL:origin}});
  assert.equal(result.status,0,'VM step failed: '+(result.stderr||'').trim().split('\n').at(-1));
  return JSON.parse(result.stdout);
}
async function signed(type,subject,op,body={}){
  const path=`/approval/${type}/${subject}/${op}`,raw=JSON.stringify(body),time=Date.now(),nonce=opaque();
  const signature=crypto(['dgst','-sha256','-sign',key,'-sigopt','rsa_padding_mode:pss','-sigopt','rsa_pss_saltlen:32'],Buffer.from(canonical('POST',path,'operator',subject,op,time,nonce,hash(raw)))).toString('base64url');
  return fetch(origin+path,{method:'POST',headers:{'content-type':'application/json','x-thth-time':String(time),'x-thth-nonce':nonce,'x-thth-signature':signature},body:raw});
}
const post=(fields,cookie)=>fetch(origin+'/activity',{method:'POST',redirect:'manual',headers:{'content-type':'application/x-www-form-urlencoded',origin,...(cookie?{cookie}:{})},body:new URLSearchParams(fields)});

test('VM pushes the summary through the real signer; the owner stops, tightens and re-keys; the VM carries it out',async()=>{
  const {account}=vm('setup',key);
  const secret=opaque(),salt=opaque();
  const verifier=pbkdf2Sync(secret,Buffer.from(salt,'base64url'),100000,32,'sha256').toString('base64url');
  assert.equal((await signed('person',account,'set',{salt,verifier,iterations:100000})).status,200);
  assert.deepEqual(vm('run').completed,[]);
  const signIn=await post({person:account,secret});assert.equal(signIn.status,303);
  const cookie=signIn.headers.get('set-cookie').split(';')[0];
  let html=await(await fetch(origin+'/activity',{headers:{cookie}})).text();
  assert.ok(html.includes(account)&&html.includes('一本目の本文です🙂')&&html.includes('二本目'),html);
  assert.ok(!html.includes('ここは出ない'),'only the first 60 characters reach the Worker');
  assert.ok(html.includes('予約の timer なし'),'scheduled: false is shown');
  assert.equal((await post({act:'stop',account,secret},cookie)).status,200);
  assert.equal((await post({act:'settings',account,key:'daily_max_posts',value:'3',secret},cookie)).status,200);
  const keyPage=await(await post({act:'rotate',account,secret},cookie)).text();
  const bearer=/<pre>([A-Za-z0-9_-]{43})<\/pre>/.exec(keyPage)[1];
  assert.ok(keyPage.includes('THTH_REPORT_TOKEN='+bearer),'without THTH_MCP_COMMAND the page gives the variable only');
  const done=vm('run');
  assert.equal(done.completed.length,3);assert.ok(done.completed.every(row=>row.outcome==='done'),JSON.stringify(done.completed));
  assert.equal(done.stopped,'owner');assert.equal(done.daily_max_posts,3);assert.deepEqual(done.credential_sha256,[hash(bearer)]);
  vm('run');
  html=await(await fetch(origin+'/activity',{headers:{cookie}})).text();
  assert.ok(html.indexOf('は止まっています')<html.indexOf('<section>')&&html.includes('あなたが止めました'),html);
  assert.ok(html.includes('済み / Done')&&!html.includes(bearer));
  assert.equal((await post({act:'resume',account,secret},cookie)).status,200);
  assert.equal(vm('run').stopped,null);
});
