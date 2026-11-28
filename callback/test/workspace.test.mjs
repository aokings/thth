// 括りの段 2: 括りの束（レーンの鍵の頼み）と、括りを選ぶ login（thth login --lane）。
// - レーンの鍵は括りの鍵の表（VM が sync の workspace_keys で押し上げる）にだけ載り、括りの束からだけ通る。
// - A の括りの鍵で B の括りの束・レーンの鍵で口座の束・口座の鍵で括りの束は、どれも無い鍵と同じ 401。
// - 古い VM の押し上げ（workspace_keys の無い形）も受け、そのときは括りの束を空にし、レーンの鍵の発行も渡さない。
// - login: 括りとレーンを見せ、持ち主の名前と secret で許す。鍵は 1 度だけ、そのあと VM が作ったか（理由）を返す。
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
import {REFUSALS} from '../src/refusal-catalog.js';
const refusal=code=>({error:code,...REFUSALS[code]});
const opaque=()=>randomBytes(32).toString('base64url'),hash=s=>createHash('sha256').update(s).digest('hex');
const logs=[],sensitive=[];
class SilentLog extends Log {constructor(){super(LogLevel.NONE);}log(value){logs.push(String(value));}}
let mf,directory,key;
const ORIGIN='https://ws.test';
const HOLD=1500;
const openssl=process.platform==='darwin'?'/opt/homebrew/bin/openssl':'/usr/bin/openssl';
function run(args,input){const r=spawnSync(openssl,args,{input,env:{PATH:'/usr/bin:/bin',OPENSSL_CONF:'/dev/null'}});assert.equal(r.status,0,'OpenSSL failed');return r.stdout;}
before(async()=>{
  directory=await realpath(await mkdtemp(join(tmpdir(),'thth-ws-')));key=join(directory,'ephemeral.key');
  const pem=run(['genpkey','-algorithm','RSA','-pkeyopt','rsa_keygen_bits:3072']);sensitive.push(pem.toString());await writeFile(key,pem,{mode:0o600});
  const publicKey=run(['pkey','-in',key,'-pubout','-outform','DER']).toString('base64url');
  const files=['test/person-harness.js','src/worker.js','src/webhook-object.js','src/media.js','src/media-object.js','src/index.js','src/relay.js','src/refusal-catalog.js','src/relay-object.js','src/person.js','src/webhook.js','src/person-object.js','src/deletion.js','src/deletion-object.js','src/invite.js','src/invite-object.js','src/activity.js','src/api.js','src/login.js','src/login-object.js','src/connector-object.js','src/oauth.js','src/mcp.js','src/mcp-tools.js','src/mcp-ui.js','src/connectors.js'];
  const modules=await Promise.all(files.map(async name=>{const path=fileURLToPath(new URL('../'+name,import.meta.url));return{type:'ESModule',path,contents:await readFile(path,'utf8')};}));
  mf=new Miniflare(convertV4MiniflareOptions({modules,modulesRoot:fileURLToPath(new URL('..',import.meta.url)),compatibilityDate:'2026-09-01',cf:false,
    log:new SilentLog(),handleStructuredLogs:item=>logs.push(JSON.stringify(item)),bindings:{RELAY_PUBLIC_KEY:publicKey,API_HOLD_MS:String(HOLD)},
    durableObjects:{AUTH_RELAY:{className:'AuthRelay',useSQLite:true},PERSON:{className:'TestPerson',useSQLite:true},ACCOUNT:{className:'TestAccount',useSQLite:true},DELETION_INBOX:{className:'TestDeletion',useSQLite:true},MEDIA_OBJECT:{className:'TestMedia',useSQLite:true},LOGIN:{className:'TestLogin',useSQLite:true}},r2Buckets:['MEDIA_BUCKET'],
    ratelimits:{DELETION_PUBLIC_LIMIT:{namespace_id:'21204',simple:{limit:120,period:60}},AUTH_RATE_LIMIT:{namespace_id:'21101',simple:{limit:120,period:60}},RELAY_PUBLIC_LIMIT:{namespace_id:'21201',simple:{limit:1000,period:60}},RELAY_VERIFY_LIMIT:{namespace_id:'21202',simple:{limit:1000,period:60}},RELAY_JOB_LIMIT:{namespace_id:'21203',simple:{limit:1000,period:60}},API_KEY_LIMIT:{namespace_id:'21401',simple:{limit:60,period:60}},MEDIA_CONTROL_LIMIT:{namespace_id:'21302',simple:{limit:600,period:60}},MEDIA_UPLOAD_LIMIT:{namespace_id:'21303',simple:{limit:240,period:60}},MEDIA_UPLOAD_IP_LIMIT:{namespace_id:'21304',simple:{limit:120,period:60}},MEDIA_PUBLIC_LIMIT:{namespace_id:'21301',simple:{limit:120,period:60}}}}));await mf.ready;
});
after(async()=>{await mf?.dispose();await rm(directory,{recursive:true,force:true});const hits=logs.filter(line=>sensitive.some(value=>line.includes(value))).length;assert.equal(hits,0,'secret in runtime logs');console.log('workspace runtime log scan: '+logs.length+' chunks, '+hits+' hits');});
async function signed(type,subject,op,body={}){
  const path=`/relay/v/${type}/${subject}/${op}`,raw=JSON.stringify(body),time=Date.now(),nonce=opaque();
  const signature=run(['dgst','-sha256','-sign',key,'-sigopt','rsa_padding_mode:pss','-sigopt','rsa_pss_saltlen:32'],Buffer.from(canonical('POST',path,'operator',subject,op,time,nonce,hash(raw)))).toString('base64url');
  sensitive.push(signature,nonce);
  return mf.dispatchFetch(ORIGIN+path,{method:'POST',headers:{'content-type':'application/json','cf-connecting-ip':opaque(),'x-thth-time':String(time),'x-thth-nonce':nonce,'x-thth-signature':signature},body:raw});
}
const control=async(type,id,settings)=>(await mf.dispatchFetch(`${ORIGIN}/__person/${type}/${id}`,settings?{method:'POST',body:JSON.stringify(settings)}:{})).json();
async function person(){const id='p'+randomBytes(8).toString('hex'),secret=opaque(),salt=opaque();sensitive.push(secret);
  const data={salt,verifier:pbkdf2Sync(secret,Buffer.from(salt,'base64url'),100000,32,'sha256').toString('base64url'),iterations:100000};sensitive.push(data.verifier);
  assert.equal((await signed('person',id,'set',data)).status,200);return {id,secret};}
const account=()=>'inv-a'+randomBytes(4).toString('hex');
const workspace=()=>'ws'+randomBytes(4).toString('hex');
const later=(ms=365*86_400_000)=>new Date(Date.now()+ms).toISOString().replace(/\.\d+Z$/,'+00:00');
function bearer(){const value=opaque();sensitive.push(value);return value;}
const keyRow=(value,name)=>({sha256:hash(value),account:name,expires_at:later()});
const wsRow=(value,ws)=>({sha256:hash(value),workspace:ws,expires_at:later()});
async function sync(p,{keys=[],results=[],workspace_keys=[],workspace_results=[],completed=[]}={},shape='full'){
  const body=shape==='full'?{accounts:[],completed,keys,results,workspace_keys,workspace_results}:shape==='3.14'?{accounts:[],completed,keys,results}:{accounts:[],completed};
  const response=await signed('activity',p.id,'sync',body);
  assert.equal(response.status,200,await response.clone().text());return response.json();
}
const api=(operation,value,body)=>mf.dispatchFetch(ORIGIN+'/api/v1/'+operation,{method:'POST',
  headers:{'content-type':'application/json','cf-connecting-ip':opaque(),authorization:'Bearer '+value},body:JSON.stringify(body)});
const result=(id,value)=>mf.dispatchFetch(ORIGIN+'/api/v1/result/'+id,{headers:{'cf-connecting-ip':opaque(),authorization:'Bearer '+value}});

test('a lane request is kept in the workspace bundle and answered through workspace_results; the id names the workspace',async()=>{
  const p=await person(),ws=workspace(),name=account(),value=bearer();
  const tables={workspace_keys:[wsRow(value,ws)]};await sync(p,tables);
  const pending=api('lane_status',value,{account:name,workspace:ws});
  let item;
  for(let n=0;n<40&&!item;n++){item=(await sync(p,tables)).requests[0];if(!item)await new Promise(r=>setTimeout(r,50));}
  assert.ok(item,'the request was handed over');
  assert.deepEqual(Object.keys(item).sort(),['account','body','key_sha256','operation','request_id','workspace']);
  assert.equal(item.workspace,ws);assert.deepEqual(item.body,{account:name},'the workspace field is not passed on in the body');
  assert.match(item.request_id,new RegExp('^[A-Za-z0-9_-]{43}\\.'+name+'$'));
  await sync(p,{...tables,workspace_results:[{request_id:item.request_id,workspace:ws,account:name,status:'done',result:{workspace:ws}}]});
  const response=await pending;assert.equal(response.status,200);assert.deepEqual(await response.json(),{workspace:ws});
  // 保留を越えたときの id は `<id>~<括り>` で、同じ括りの束から引ける。
  const late=await api('lane_status',value,{account:name,workspace:ws});assert.equal(late.status,202);
  const {request_id}=await late.json();assert.match(request_id,new RegExp('^[A-Za-z0-9_-]{43}~'+ws+'$'));
  assert.equal((await result(request_id,value)).status,202);
  // 口座の束には何も置かれない。
  assert.ok(!(await control('account',name)).some(([k])=>k.startsWith('request:')));
});

test('keys do not cross: A workspace key in B bundle, a lane key in an account bundle, an account key in a workspace bundle are all 401 like an unknown key',async()=>{
  const p=await person(),q=await person(),a=workspace(),b=workspace(),name=account(),lane=bearer(),other=bearer(),plain=bearer();
  await sync(p,{keys:[keyRow(plain,name)],workspace_keys:[wsRow(lane,a)]});
  await sync(q,{workspace_keys:[wsRow(other,b)]});
  const unknown=await(await api('lane_status',bearer(),{account:name,workspace:a})).json();
  assert.deepEqual(unknown,refusal('invalid_key'));
  for(const [value,body] of [[lane,{account:name,workspace:b}],[other,{account:name,workspace:a}],[lane,{account:name}],[plain,{account:name,workspace:a}]]){
    const r=await api('lane_status',value,body);assert.equal(r.status,401);assert.deepEqual(await r.json(),unknown);
  }
  for(const bad of [{account:name,workspace:'Bad'},{account:name,workspace:'a:b'},{account:name,workspace:7}])
    assert.equal((await api('lane_status',lane,bad)).status,400);
  // 口座の鍵は今のまま口座の束で通る。
  assert.equal((await api('draft_list',plain,{account:name})).status,202);
});

test('an old VM push (3.14 or 3.13 shape) is accepted; the workspace bundle is emptied and no lane_key action is handed to it',async()=>{
  const p=await person(),ws=workspace(),name=account(),value=bearer();
  await sync(p,{workspace_keys:[wsRow(value,ws)]});
  assert.equal((await api('lane_status',value,{account:name,workspace:ws})).status,202);
  await sync(p,{keys:[]},'3.14');
  assert.equal((await api('lane_status',value,{account:name,workspace:ws})).status,401,'an old VM cannot serve the bundle, so it is closed');
  await sync(p,{},'3.13');
  // 不正な括りの行は sync ごと断る。
  for(const bad of [[{sha256:hash(value),workspace:'Bad',expires_at:later()}],[{sha256:hash(value),account:name,expires_at:later()}],[wsRow(value,ws),wsRow(value,ws)]]){
    const r=await signed('activity',p.id,'sync',{accounts:[],completed:[],keys:[],results:[],workspace_keys:bad,workspace_results:[]});
    assert.equal(r.status,400);
  }
});

const newCode=()=>{const pick=()=>Array.from({length:4},()=>CODE_ALPHABET[randomInt(CODE_ALPHABET.length)]).join('');return pick()+'-'+pick();};
const start=body=>mf.dispatchFetch(ORIGIN+'/api/v1/login/start',{method:'POST',headers:{'content-type':'application/json','cf-connecting-ip':opaque()},body:JSON.stringify(body)});
async function begin(extra){
  const code=newCode(),token=opaque();sensitive.push(token);
  const r=await start({code_hash:hash(code),poll_token_hash:hash(token),...extra});
  assert.equal(r.status,202);return {code,token};
}
const poll=(code,token)=>mf.dispatchFetch(ORIGIN+'/api/v1/login/poll/'+code,{headers:{'cf-connecting-ip':opaque(),authorization:'Bearer '+token}});
const page=code=>mf.dispatchFetch(ORIGIN+'/login/'+code,{headers:{'cf-connecting-ip':opaque()}});
const allow=(code,fields)=>mf.dispatchFetch(ORIGIN+'/login/'+code,{method:'POST',
  headers:{'content-type':'application/x-www-form-urlencoded',origin:ORIGIN,'cf-connecting-ip':opaque()},body:new URLSearchParams(fields),redirect:'manual'});

test('lane login: the page shows the workspace and lane and asks for the owner; the key comes once; the VM outcome follows',async()=>{
  const owner=await person(),ws=workspace(),{code,token}=await begin({lane:'kopicha-sns',workspace:ws});
  const r=await page(code);assert.equal(r.status,200);
  const html=await r.text();
  assert.ok(html.includes(ws)&&html.includes('kopicha-sns')&&html.includes('name="owner"')&&!html.includes('name="account"'),html);
  assert.ok(html.includes('持ち主のシークレット')&&html.includes('Owner secret')&&!/<script/i.test(html));
  // 口座の form の欄は受けない・違う secret は 403 で何も置かない。
  assert.equal((await allow(code,{account:owner.id,secret:owner.secret})).status,400);
  assert.equal((await allow(code,{owner:owner.id,secret:opaque()})).status,403);
  assert.ok(!(await control('person',owner.id)).some(([k])=>k.startsWith('action:')));
  const ok=await allow(code,{owner:owner.id,secret:owner.secret});assert.equal(ok.status,200);
  const got=await(await poll(code,token)).json();sensitive.push(got.key);
  assert.deepEqual({...got,key:undefined},{key:undefined,owner:owner.id,lane:'kopicha-sns',workspace:ws});
  assert.match(got.key,/^[A-Za-z0-9_-]{43}$/);
  assert.ok(!JSON.stringify(await control('login',hash(code))).includes(got.key),'the key is gone once taken');
  assert.deepEqual(await(await poll(code,token)).json(),{outcome:'pending',reason:null,workspace:null,account:null});
  // VM（括りの形）には lane_key が hash だけで渡る。古い VM には渡らない。
  assert.deepEqual((await sync(owner,{keys:[]},'3.14')).actions,[]);
  const {actions}=await sync(owner);
  assert.deepEqual(actions.map(({id,...a})=>a),[{kind:'lane_key',workspace:ws,lane:'kopicha-sns',sha256:hash(got.key)}]);
  await sync(owner,{completed:[{id:actions[0].id,outcome:'done',reason:null,workspace:ws,account:'inv-home'}],workspace_keys:[wsRow(got.key,ws)]});
  assert.deepEqual(await(await poll(code,token)).json(),{outcome:'done',reason:null,workspace:ws,account:'inv-home'});
  // /activity の一覧にも括りとレーンで出る（口座の名前は無い）。
  const view=JSON.stringify(await control('person',owner.id));assert.ok(!view.includes(got.key));
});

test('audit (12): /activity shows a finished lane_key as long as other operations, while its outcome row stays for the connector',async()=>{
  const owner=await person(),ws=workspace(),{code,token}=await begin({lane:'gamma-desk',workspace:ws});
  assert.equal((await allow(code,{owner:owner.id,secret:owner.secret})).status,200);
  const got=await(await poll(code,token)).json();sensitive.push(got.key);
  const {actions}=await sync(owner);
  await sync(owner,{completed:[{id:actions[0].id,outcome:'done',reason:null,workspace:ws,account:'inv-home'}],workspace_keys:[wsRow(got.key,ws)]});
  const form=fields=>({method:'POST',headers:{'content-type':'application/x-www-form-urlencoded',origin:ORIGIN,'cf-connecting-ip':opaque()},body:new URLSearchParams(fields),redirect:'manual'});
  const view=async()=>{
    const r=await mf.dispatchFetch(ORIGIN+'/activity',form({person:owner.id,secret:owner.secret}));assert.equal(r.status,303);
    const c=r.headers.get('set-cookie');sensitive.push(c);
    return (await mf.dispatchFetch(ORIGIN+'/activity',{headers:{'cf-connecting-ip':opaque(),cookie:c.split(';')[0]},redirect:'manual'})).text();
  };
  // 済んだ直後は、ほかの操作と同じく一覧に出る。
  assert.ok((await view()).includes(ws+' / gamma-desk'),'shown right after it is done');
  // 2 時間たつと一覧から消える（ほかの操作の 1 時間と同じ長さ）。Connector が結ぶための結果の行は 30 日残る。
  await control('person',owner.id,{clock:Date.now()+2*3_600_000});
  try{
    assert.ok(!(await view()).includes('gamma-desk'),'a finished lane_key is not listed after an hour');
    assert.ok((await control('person',owner.id)).some(([k,a])=>k.startsWith('action:')&&a.kind==='lane_key'&&a.status==='done'),'the outcome row stays');
    assert.deepEqual(await(await poll(code,token)).json(),{outcome:'done',reason:null,workspace:ws,account:'inv-home'});
  }finally{await control('person',owner.id,{clock:null});}
});

test('lane login without a workspace lets the VM choose, and a VM refusal reaches the terminal as the reason',async()=>{
  const owner=await person(),{code,token}=await begin({lane:'asmon-kanto'});
  assert.ok((await(await page(code)).text()).includes('あなたの括り'));
  assert.equal((await allow(code,{owner:owner.id,secret:owner.secret})).status,200);
  sensitive.push((await(await poll(code,token)).json()).key);
  const {actions}=await sync(owner);assert.equal(actions[0].workspace,null);
  await sync(owner,{completed:[{id:actions[0].id,outcome:'failed',reason:'workspace_required',workspace:null,account:null}]});
  assert.deepEqual(await(await poll(code,token)).json(),{outcome:'failed',reason:'workspace_required',workspace:null,account:null});
});

test('lane login start: closed fields only; a workspace needs a lane',async()=>{
  const bad=async extra=>(await start({code_hash:hash(newCode()),poll_token_hash:hash(opaque()),...extra})).status;
  assert.equal(await bad({workspace:'garden'}),400);
  assert.equal(await bad({lane:'Bad Lane'}),400);
  assert.equal(await bad({lane:'ok',workspace:'Bad'}),400);
  assert.equal(await bad({lane:'ok',extra:1}),400);
  assert.equal(await bad({lane:'ok',workspace:'garden'}),202);
  // 口座の login は今のまま（口座の form・poll は {key, account}・受け取ったら 410）。
  const {code}=await begin();
  const html=await(await page(code)).text();assert.ok(html.includes('name="account"')&&!html.includes('name="owner"'));
});
