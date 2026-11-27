// 段 K: リモート MCP（/mcp）と自前の認可サーバ（/oauth/*・/.well-known/*）・コネクタの頁（/connectors）。
// 設計 共通の層 §6.1。柵（どれも検めを外して赤を見た・外した所は設計 §10 の段 K の行）:
//  1. token 無し・形の違う・期限切れ・取り消し済みは 401 で本文に中身を出さない。別の括りの結果は 403 相当の断り（forbidden）・
//     答えの括りが違えば中身を出さない（workspace_mismatch）。
//  2. 読むだけの token では書く道具が一覧に無く、呼べば writes_not_allowed（VM に頼まない）。VM への頼みにも writes: false。
//  3. 書く道具は 1 段目を受け取っていなければ 2 段目が通らない（VM に頼まない）。
//  4. PKCE の無い・S256 でない要求、登録に無い戻り先は断る。code は 1 回だけ・verifier が違えば invalid_grant。
//  5. 道具の表から Worker が組む argv を、VM の通り道（server_passthrough._parse）がそのまま受ける。
//  6. 答え・頁・記録・保存のどこにも token・鍵・secret・code が出ない（token の答えと戻り先の code を除く）。
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
import TABLE from '../src/mcp-tools.js';
const TOOLS=TABLE.tools;
// buildArgv は cloudflare: を読む mcp.js の中にあるので、器の /__argv を通して呼ぶ。
const argvOf=async items=>(await (await mf.dispatchFetch(ORIGIN+'/__argv',{method:'POST',body:JSON.stringify(items)})).json());

const opaque=()=>randomBytes(32).toString('base64url'),hash=s=>createHash('sha256').update(s).digest('hex');
const b64=buf=>Buffer.from(buf).toString('base64url');
const logs=[],sensitive=[],answers=[];
class SilentLog extends Log {constructor(){super(LogLevel.NONE);}log(value){logs.push(String(value));}}
let mf,directory,key;
const ORIGIN='https://k.test';
const HOLD=1500;
const CLAUDE='https://claude.ai/oauth/mcp-oauth-client-metadata',CALLBACK='https://claude.ai/api/mcp/auth_callback';
const LANE_CLAUDE='connector-claude-ai-'+createHash('sha256').update(CLAUDE).digest('hex').slice(0,6);
const openssl=process.platform==='darwin'?'/opt/homebrew/bin/openssl':'/usr/bin/openssl';
const repo=fileURLToPath(new URL('../..',import.meta.url));
function run(args,input){const r=spawnSync(openssl,args,{input,env:{PATH:'/usr/bin:/bin',OPENSSL_CONF:'/dev/null'}});assert.equal(r.status,0,'OpenSSL failed');return r.stdout;}
// CIMD: claude.ai の身元の文書（monban 検証 10/5 の形）と、名乗りの違う文書・3xx。
function outbound(request){
  const url=new URL(request.url);
  if(request.url===CLAUDE)return Response.json({client_id:CLAUDE,client_name:'Claude',redirect_uris:[CALLBACK],token_endpoint_auth_method:'none'});
  if(url.pathname==='/liar')return Response.json({client_id:'https://claude.ai/other',redirect_uris:[CALLBACK]});
  if(url.pathname==='/moved')return new Response(null,{status:302,headers:{location:CLAUDE}});
  // 監査 4: 戻り先の形の違う CIMD（http の外のホスト・javascript:・自前の scheme）。
  const odd={'/odd-http':'http://evil.example/cb','/odd-js':'javascript:alert(1)','/odd-app':'myapp://cb'}[url.pathname];
  if(odd)return Response.json({client_id:request.url,redirect_uris:[odd]});
  return new Response('external_denied',{status:503});
}
before(async()=>{
  directory=await realpath(await mkdtemp(join(tmpdir(),'thth-k-')));key=join(directory,'ephemeral.key');
  const pem=run(['genpkey','-algorithm','RSA','-pkeyopt','rsa_keygen_bits:3072']);sensitive.push(pem.toString());await writeFile(key,pem,{mode:0o600});
  const publicKey=run(['pkey','-in',key,'-pubout','-outform','DER']).toString('base64url');
  const files=['test/connector-harness.js','test/person-harness.js','src/worker.js','src/webhook-object.js','src/media.js','src/media-object.js','src/index.js','src/relay.js','src/refusal-catalog.js','src/relay-object.js','src/person.js','src/webhook.js','src/person-object.js','src/deletion.js','src/deletion-object.js','src/invite.js','src/invite-object.js','src/activity.js','src/api.js','src/login.js','src/login-object.js','src/connector-object.js','src/oauth.js','src/mcp.js','src/mcp-tools.js','src/connectors.js'];
  const modules=await Promise.all(files.map(async name=>{const path=fileURLToPath(new URL('../'+name,import.meta.url));return{type:'ESModule',path,contents:await readFile(path,'utf8')};}));
  mf=new Miniflare(convertV4MiniflareOptions({modules,modulesRoot:fileURLToPath(new URL('..',import.meta.url)),compatibilityDate:'2026-09-01',cf:false,
    outboundService:outbound,log:new SilentLog(),handleStructuredLogs:item=>logs.push(JSON.stringify(item)),bindings:{RELAY_PUBLIC_KEY:publicKey,API_HOLD_MS:String(HOLD)},
    durableObjects:{AUTH_RELAY:{className:'AuthRelay',useSQLite:true},PERSON:{className:'TestPersonK',useSQLite:true},ACCOUNT:{className:'TestAccount',useSQLite:true},DELETION_INBOX:{className:'TestDeletion',useSQLite:true},MEDIA_OBJECT:{className:'TestMedia',useSQLite:true},LOGIN:{className:'TestLogin',useSQLite:true},CONNECTOR:{className:'TestConnector',useSQLite:true}},r2Buckets:['MEDIA_BUCKET'],
    ratelimits:{DELETION_PUBLIC_LIMIT:{namespace_id:'21204',simple:{limit:120,period:60}},AUTH_RATE_LIMIT:{namespace_id:'21101',simple:{limit:120,period:60}},RELAY_PUBLIC_LIMIT:{namespace_id:'21201',simple:{limit:5000,period:60}},RELAY_VERIFY_LIMIT:{namespace_id:'21202',simple:{limit:5000,period:60}},RELAY_JOB_LIMIT:{namespace_id:'21203',simple:{limit:5000,period:60}},API_KEY_LIMIT:{namespace_id:'21401',simple:{limit:600,period:60}},MEDIA_CONTROL_LIMIT:{namespace_id:'21302',simple:{limit:600,period:60}},MEDIA_UPLOAD_LIMIT:{namespace_id:'21303',simple:{limit:240,period:60}},MEDIA_UPLOAD_IP_LIMIT:{namespace_id:'21304',simple:{limit:120,period:60}},MEDIA_PUBLIC_LIMIT:{namespace_id:'21301',simple:{limit:120,period:60}}}}));await mf.ready;
});
after(async()=>{
  await mf?.dispose();await rm(directory,{recursive:true,force:true});
  const hits=logs.filter(line=>sensitive.some(value=>line.includes(value))).length;
  assert.equal(hits,0,'secret in runtime logs');
  const leaked=answers.filter(text=>sensitive.some(value=>text.includes(value))).length;
  assert.equal(leaked,0,'secret in an answer');
  console.log('connector scan: '+logs.length+' log chunks, '+answers.length+' answers, '+sensitive.length+' values, 0 hits');
});
// 答えを控える（柵 6 で全部を照らす）。token の答えと戻り先の Location は控えない（そこにだけ出てよい）。
async function keep(response){answers.push(await response.clone().text());for(const [k,v] of response.headers)if(k!=='location')answers.push(k+': '+v);return response;}
const ip=()=>opaque();
async function signed(type,subject,op,body={}){
  const path=`/relay/v/${type}/${subject}/${op}`,raw=JSON.stringify(body),time=Date.now(),nonce=opaque();
  const signature=run(['dgst','-sha256','-sign',key,'-sigopt','rsa_padding_mode:pss','-sigopt','rsa_pss_saltlen:32'],Buffer.from(canonical('POST',path,'operator',subject,op,time,nonce,hash(raw)))).toString('base64url');
  return mf.dispatchFetch(ORIGIN+path,{method:'POST',headers:{'content-type':'application/json','cf-connecting-ip':ip(),'x-thth-time':String(time),'x-thth-nonce':nonce,'x-thth-signature':signature},body:raw});
}
async function person(){const id='p'+randomBytes(6).toString('hex'),secret=opaque(),salt=opaque();sensitive.push(secret);
  const data={salt,verifier:pbkdf2Sync(secret,Buffer.from(salt,'base64url'),100000,32,'sha256').toString('base64url'),iterations:100000};sensitive.push(data.verifier);
  assert.equal((await signed('person',id,'set',data)).status,200);return {id,secret};}
const workspace=()=>'ws'+randomBytes(4).toString('hex');
const later=(ms=365*86_400_000)=>new Date(Date.now()+ms).toISOString().replace(/\.\d+Z$/,'+00:00');
async function sync(p,{workspace_keys=[],workspace_results=[],completed=[]}={}){
  const response=await signed('activity',p.id,'sync',{accounts:[],completed,keys:[],results:[],workspace_keys,workspace_results});
  assert.equal(response.status,200,await response.clone().text());return response.json();
}
const control=async(settings)=>(await mf.dispatchFetch(ORIGIN+'/__connector',settings?{method:'POST',body:JSON.stringify(settings)}:{})).json();
const issuedBearer=async p=>(await (await mf.dispatchFetch(ORIGIN+'/__bearer/'+p.id)).json()).bearer;
function pkce(){const verifier=opaque()+opaque().slice(0,10);sensitive.push(verifier);return {verifier,challenge:b64(createHash('sha256').update(verifier).digest())};}
function authorizeUrl(params){const u=new URL(ORIGIN+'/oauth/authorize');for(const [k,v] of Object.entries(params))if(v!==undefined)u.searchParams.set(k,v);return u.toString();}
const baseParams=(challenge,extra={})=>({response_type:'code',client_id:CLAUDE,redirect_uri:CALLBACK,code_challenge:challenge,code_challenge_method:'S256',
  scope:'read write offline_access',state:'st-'+randomBytes(4).toString('hex'),resource:ORIGIN+'/mcp',...extra});
const form=(url,fields)=>mf.dispatchFetch(url,{method:'POST',redirect:'manual',headers:{'content-type':'application/x-www-form-urlencoded',origin:ORIGIN,'cf-connecting-ip':ip()},body:new URLSearchParams(fields).toString()});
// 持ち主が許すまで（code を受け取るまで）。
async function consent(p,{readonly=false,ws=null,params}={}){
  const {verifier,challenge}=pkce();
  const q=params??baseParams(challenge);
  const shown=await keep(await mf.dispatchFetch(authorizeUrl(q),{headers:{'cf-connecting-ip':ip()}}));
  assert.equal(shown.status,200,await shown.clone().text());
  const fields={...q,owner:p.id,secret:p.secret,workspace:ws??''};if(readonly)fields.readonly='1';
  const allowed=await form(ORIGIN+'/oauth/authorize',fields);
  await keep(allowed);
  assert.equal(allowed.status,303,await allowed.clone().text());
  const back=new URL(allowed.headers.get('location'));
  assert.equal(back.origin+back.pathname,CALLBACK);assert.equal(back.searchParams.get('state'),q.state);
  const code=back.searchParams.get('code');sensitive.push(code);
  return {code,verifier,q};
}
async function tokenFor(body){
  const response=await mf.dispatchFetch(ORIGIN+'/oauth/token',{method:'POST',headers:{'content-type':'application/x-www-form-urlencoded','cf-connecting-ip':ip()},body:new URLSearchParams(body).toString()});
  return {status:response.status,body:await response.json()};
}
async function connect(p,options={}){
  const {code,verifier}=await consent(p,options);
  const got=await tokenFor({grant_type:'authorization_code',code,code_verifier:verifier,client_id:CLAUDE,redirect_uri:CALLBACK,resource:ORIGIN+'/mcp'});
  assert.equal(got.status,200,JSON.stringify(got.body));
  sensitive.push(got.body.access_token,got.body.refresh_token);
  sensitive.push(await issuedBearer(p));
  return got.body;
}
let seq=0;
async function mcp(token,method,params={},raw=false){
  const response=await keep(await mf.dispatchFetch(ORIGIN+'/mcp',{method:'POST',headers:{'content-type':'application/json','cf-connecting-ip':ip(),...(token?{authorization:'Bearer '+token}:{})},
    body:JSON.stringify({jsonrpc:'2.0',id:++seq,method,params})}));
  return raw?response:(await response.json());
}
const callTool=async(token,name,args)=>(await mcp(token,'tools/call',{name,arguments:args})).result;
const content=result=>JSON.parse(result.content[0].text);
// VM の役: 発行の頼みを受け、鍵を作ったと返す（括り・頼みの宛先の口座）。
async function vmIssue(p,ws,account){
  const {actions}=await sync(p);
  const action=actions.find(a=>a.kind==='lane_key');assert.ok(action,'lane_key handed');
  const tables={workspace_keys:[{sha256:action.sha256,workspace:ws,expires_at:later()}]};
  await sync(p,{...tables,completed:[{id:action.id,outcome:'done',reason:null,workspace:ws,account}]});
  return {action,tables};
}
// VM の役: 括りの束の頼みを 1 つ受け取り、答えを返す。
async function vmAnswer(p,tables,answer){
  let item;
  for(let n=0;n<60&&!item;n++){item=(await sync(p,tables)).requests[0];if(!item)await new Promise(r=>setTimeout(r,25));}
  assert.ok(item,'request handed to the VM');
  const result=typeof answer==='function'?answer(item):answer;
  await sync(p,{...tables,workspace_results:[{request_id:item.request_id,workspace:item.workspace,account:item.account,status:'done',result}]});
  return item;
}

test('discovery: the resource and authorization server tables, and a 401 that starts sign-in without content',async()=>{
  const pr=await (await mf.dispatchFetch(ORIGIN+'/.well-known/oauth-protected-resource/mcp')).json();
  assert.equal(pr.resource,ORIGIN+'/mcp');assert.deepEqual(pr.authorization_servers,[ORIGIN]);
  const as=await (await mf.dispatchFetch(ORIGIN+'/.well-known/oauth-authorization-server')).json();
  assert.equal(as.issuer,ORIGIN);assert.deepEqual(as.code_challenge_methods_supported,['S256']);
  assert.deepEqual(as.token_endpoint_auth_methods_supported,['none']);assert.equal(as.client_id_metadata_document_supported,true);
  assert.deepEqual(as.response_types_supported,['code']);
  for(const token of [null,'x'.repeat(66),'A'.repeat(22)+'.'+'B'.repeat(43)]){
    const r=await mcp(token,'initialize',{protocolVersion:'2025-11-25'},true);
    assert.equal(r.status,401);
    assert.equal(r.headers.get('www-authenticate'),`Bearer resource_metadata="${ORIGIN}/.well-known/oauth-protected-resource/mcp"`);
    assert.equal(r.headers.get('cache-control'),'no-store');
    assert.deepEqual(await r.json(),{error:'unauthorized'});
  }
});

test('fence 4: PKCE is required and S256 only; unregistered return addresses and lying client documents are refused',async()=>{
  const {challenge,verifier}=pkce();
  for(const bad of [{code_challenge:undefined},{code_challenge_method:'plain'},{code_challenge_method:undefined},{code_challenge:'short'}]){
    const r=await keep(await mf.dispatchFetch(authorizeUrl(baseParams(challenge,bad)),{headers:{'cf-connecting-ip':ip()}}));
    assert.equal(r.status,400,JSON.stringify(bad));assert.equal(r.headers.get('location'),null,'never sent back to the client');
  }
  const elsewhere=await mf.dispatchFetch(authorizeUrl(baseParams(challenge,{redirect_uri:'https://evil.example/cb'})),{headers:{'cf-connecting-ip':ip()}});
  assert.equal(elsewhere.status,400);assert.match(await elsewhere.text(),/return address|戻り先/);
  for(const id of ['https://claude.ai/liar','https://claude.ai/moved','http://claude.ai/x']){
    const r=await mf.dispatchFetch(authorizeUrl(baseParams(challenge,{client_id:id})),{headers:{'cf-connecting-ip':ip()}});
    assert.equal(r.status,400,id);
  }
  const other=await mf.dispatchFetch(authorizeUrl(baseParams(challenge,{resource:'https://k.test/other'})),{headers:{'cf-connecting-ip':ip()}});
  assert.equal(other.status,400);
  // POST でも同じ検め（頁を通さずに許しを送っても通らない）。
  const p=await person();
  const posted=await form(ORIGIN+'/oauth/authorize',{...baseParams(challenge,{code_challenge_method:'plain'}),owner:p.id,secret:p.secret});
  assert.equal(posted.status,400);assert.equal((await sync(p)).actions.length,0,'no key issued');
  // code は 1 回だけ・verifier が違えば invalid_grant（その預かりは捨てる）。
  const {code,verifier:v2}=await consent(p);
  assert.equal((await tokenFor({grant_type:'authorization_code',code,code_verifier:verifier,client_id:CLAUDE,redirect_uri:CALLBACK})).body.error,'invalid_grant');
  assert.equal((await tokenFor({grant_type:'authorization_code',code,code_verifier:v2,client_id:CLAUDE,redirect_uri:CALLBACK})).body.error,'invalid_grant');
  const ok=await consent(p);
  const wrongClient=await tokenFor({grant_type:'authorization_code',code:ok.code,code_verifier:ok.verifier,client_id:'https://claude.ai/other',redirect_uri:CALLBACK});
  assert.equal(wrongClient.body.error,'invalid_grant');
  assert.equal((await tokenFor({grant_type:'password',username:'x'})).body.error,'unsupported_grant_type');
  // 捨てた預かりの鍵は VM でも取り消す（Connector の alarm が Person DO に置く）。
  await control({alarm:true});
  const revokes=(await sync(p)).actions.filter(a=>a.kind==='lane_revoke');
  assert.ok(revokes.length>=1,'the forfeited keys are revoked on the VM too');
});

test('the owner allows; the key binds after the VM sync; a read tool goes through the passthrough; key_pending before',async()=>{
  const p=await person(),ws=workspace(),account='acct-'+randomBytes(3).toString('hex');
  const tokens=await connect(p);
  assert.equal(tokens.token_type,'Bearer');assert.equal(tokens.scope,'read write offline_access');assert.equal(tokens.expires_in,3600);
  const init=await mcp(tokens.access_token,'initialize',{protocolVersion:'2025-11-25'});
  assert.equal(init.result.protocolVersion,'2025-11-25');assert.equal(init.result.serverInfo.name,'thth');
  const listed=(await mcp(tokens.access_token,'tools/list')).result.tools.map(t=>t.name);
  assert.deepEqual(listed,[...TOOLS.map(t=>t.name),'thth_result']);
  // VM がまだ鍵を作っていない: 401 にせず道具の結果で「数十秒後に」。
  const early=await callTool(tokens.access_token,'thth_board',{});
  assert.equal(early.isError,true);assert.equal(content(early).error,'key_pending');
  const {action,tables}=await vmIssue(p,ws,account);
  assert.equal(action.lane,LANE_CLAUDE);assert.equal(action.workspace,null);assert.equal('writes' in action,false,'a writing key keeps the stage-2 shape');
  const pending=callTool(tokens.access_token,'thth_board',{});
  const item=await vmAnswer(p,tables,it=>({command:'board',rc:0,stdout:JSON.stringify({accounts:2}),stderr:'',truncated:false,workspace:ws}));
  assert.equal(item.operation,'ws_command');assert.equal(item.workspace,ws);
  assert.deepEqual(item.body,{account,argv:['board','--json']});
  const answer=await pending;
  assert.notEqual(answer.isError,true);assert.deepEqual(content(answer),{command:'board',rc:0,output:{accounts:2},workspace:ws});
  // refresh は回す（古い refresh は使えない）。
  const next=await tokenFor({grant_type:'refresh_token',refresh_token:tokens.refresh_token});
  assert.equal(next.status,200);sensitive.push(next.body.access_token,next.body.refresh_token);
  assert.notEqual(next.body.refresh_token,tokens.refresh_token);
  assert.equal((await tokenFor({grant_type:'refresh_token',refresh_token:tokens.refresh_token})).body.error,'invalid_grant');
  assert.equal((await mcp(tokens.access_token,'ping',{},true)).status,401,'the old access token stops with the rotation');
  assert.equal((await mcp(next.body.access_token,'ping',{},true)).status,200);
});

test('fence 1: an expired access token is 401; another workspace\'s result is refused; a VM answer for another workspace shows nothing',async()=>{
  const p=await person(),ws=workspace(),account='acct-'+randomBytes(3).toString('hex');
  const tokens=await connect(p);const {tables}=await vmIssue(p,ws,account);
  const other=await callTool(tokens.access_token,'thth_result',{request_id:'A'.repeat(43)+'~'+workspace()});
  assert.equal(content(other).error,'forbidden');assert.equal(other.isError,true);
  const pending=callTool(tokens.access_token,'thth_morning',{name:'proj'});
  await vmAnswer(p,tables,{command:'morning',rc:0,stdout:'SECRET-OTHER-WORKSPACE',stderr:'',truncated:false,workspace:'elsewhere'});
  const mismatch=await pending;
  assert.equal(content(mismatch).error,'workspace_mismatch');assert.doesNotMatch(mismatch.content[0].text,/SECRET-OTHER-WORKSPACE/);
  // access の期限（1 時間）を過ぎると 401。refresh は生きている。
  await control({clock:Date.now()+3_600_001});
  const expired=await mcp(tokens.access_token,'tools/list',{},true);
  assert.equal(expired.status,401);assert.deepEqual(await expired.json(),{error:'unauthorized'});
  const again=await tokenFor({grant_type:'refresh_token',refresh_token:tokens.refresh_token});assert.equal(again.status,200);
  sensitive.push(again.body.access_token,again.body.refresh_token);
  await control({clock:null});
});

test('fence 2: a read-only connector has no write tools, is refused before the VM, and the VM key is made read-only',async()=>{
  const p=await person(),ws=workspace(),account='acct-'+randomBytes(3).toString('hex');
  const tokens=await connect(p,{readonly:true});
  assert.equal(tokens.scope,'read offline_access');
  const {action,tables}=await vmIssue(p,ws,account);
  assert.equal(action.writes,false,'the VM is asked for a key that cannot write');
  const names=(await mcp(tokens.access_token,'tools/list')).result.tools.map(t=>t.name);
  for(const name of ['thth_approve','thth_revoke','thth_withdraw'])assert.equal(names.includes(name),false,name);
  for(const [name,args] of [['thth_withdraw',{account,files:['queue/a.md']}],['thth_approve',{account,files:['queue/a.md'],confirm:'d'.repeat(64)}]]){
    const r=await callTool(tokens.access_token,name,args);
    assert.equal(content(r).error,'writes_not_allowed',name);
  }
  assert.equal((await sync(p,tables)).requests.length,0,'nothing reached the VM');
});

test('fence 3: write tools need the first step on this connection; the second step goes to the VM once',async()=>{
  const p=await person(),ws=workspace(),account='acct-'+randomBytes(3).toString('hex');
  const tokens=await connect(p);const {tables}=await vmIssue(p,ws,account);
  const args={account,files:['queue/a.md'],reason:'not today'};
  // 1 段目を受け取らずに confirm を付けても通らない（VM に頼まない）。
  const skipped=await callTool(tokens.access_token,'thth_withdraw',{...args,confirm:opaque()});
  assert.equal(content(skipped).error,'confirm_required');
  const approveSkipped=await callTool(tokens.access_token,'thth_approve',{account,files:['queue/a.md'],confirm:'d'.repeat(64)});
  assert.equal(content(approveSkipped).error,'confirm_required');
  assert.equal((await sync(p,tables)).requests.length,0,'nothing reached the VM');
  // 1 段目: Worker が何をするかと確認の値を返す（VM には頼まない）。
  const first=content(await callTool(tokens.access_token,'thth_withdraw',args));
  assert.equal(first.status,'confirm_required');assert.deepEqual(first.files,['queue/a.md']);assert.equal(first.account,account);
  assert.equal((await sync(p,tables)).requests.length,0);
  // 引数を変えた 2 段目・違う値は通らない。
  assert.equal(content(await callTool(tokens.access_token,'thth_withdraw',{...args,files:['queue/b.md'],confirm:first.confirm})).error,'confirm_required');
  assert.equal(content(await callTool(tokens.access_token,'thth_withdraw',{...args,confirm:opaque()})).error,'confirm_mismatch');
  const second=callTool(tokens.access_token,'thth_withdraw',{...args,confirm:first.confirm});
  const item=await vmAnswer(p,tables,{command:'withdraw',rc:0,stdout:'{"withdrawn":1}',stderr:'',truncated:false,workspace:ws});
  assert.deepEqual(item.body.argv,['withdraw','--account='+account,'--reason=not today','--json','--','queue/a.md']);
  assert.equal(content(await second).output.withdrawn,1);
  // 控えは 1 度だけ。
  assert.equal(content(await callTool(tokens.access_token,'thth_withdraw',{...args,confirm:first.confirm})).error,'confirm_required');
  // approve: 1 段目は VM が本文と digest を返す（--confirm なし）。2 段目は digest を VM に渡す。
  const a1=callTool(tokens.access_token,'thth_approve',{account,files:['queue/a.md']});
  const i1=await vmAnswer(p,tables,{command:'approve',rc:0,stdout:'{"digest":"'+'e'.repeat(64)+'","body":"本文"}',stderr:'',truncated:false,workspace:ws});
  assert.deepEqual(i1.body.argv,['approve','--account='+account,'--json','--','queue/a.md']);
  assert.equal(content(await a1).output.digest,'e'.repeat(64));
  const a2=callTool(tokens.access_token,'thth_approve',{account,files:['queue/a.md'],confirm:'e'.repeat(64)});
  const i2=await vmAnswer(p,tables,{command:'approve',rc:0,stdout:'{"approved":true}',stderr:'',truncated:false,workspace:ws});
  assert.deepEqual(i2.body.argv,['approve','--account='+account,'--confirm='+'e'.repeat(64),'--json','--','queue/a.md']);
  assert.equal(content(await a2).output.approved,true);
});

test('revocation: the owner page and RFC 7009 drop the connector (401 at the next call) and ask the VM to revoke the lane key',async()=>{
  const p=await person(),ws=workspace(),account='acct-'+randomBytes(3).toString('hex');
  const tokens=await connect(p);const {tables}=await vmIssue(p,ws,account);
  const bearer=await issuedBearer(p);
  // 一度呼んで括りに結ぶ（一覧に括りの名が出る）。
  const call=callTool(tokens.access_token,'thth_board',{});
  await vmAnswer(p,tables,{command:'board',rc:0,stdout:'{}',stderr:'',truncated:false,workspace:ws});await call;
  // 持ち主の頁: secret が違えば 403、合えば一覧（鍵も token も無い）。
  assert.equal((await keep(await form(ORIGIN+'/connectors',{owner:p.id,secret:opaque()}))).status,403);
  const listed=await keep(await form(ORIGIN+'/connectors',{owner:p.id,secret:p.secret}));
  assert.equal(listed.status,200);const page=await listed.text();
  assert.ok(page.includes(LANE_CLAUDE));assert.match(page,new RegExp(ws));
  const device=/name="device" value="([^"]+)"/.exec(page)[1];
  const other=await person();
  assert.equal((await form(ORIGIN+'/connectors',{owner:other.id,secret:other.secret,device})).status,404,'another owner cannot revoke it');
  const done=await keep(await form(ORIGIN+'/connectors',{owner:p.id,secret:p.secret,device}));
  assert.equal(done.status,200);
  assert.equal((await mcp(tokens.access_token,'tools/list',{},true)).status,401);
  assert.equal((await tokenFor({grant_type:'refresh_token',refresh_token:tokens.refresh_token})).body.error,'invalid_grant');
  const revoke=(await sync(p)).actions.find(a=>a.kind==='lane_revoke');
  assert.deepEqual(revoke&&{...revoke,id:undefined},{id:undefined,kind:'lane_revoke',workspace:ws,sha256:hash(bearer)});
  // RFC 7009: token で取り消す（知らない token にも 200）。
  const p2=await person();const t2=await connect(p2);
  const unknown=await mf.dispatchFetch(ORIGIN+'/oauth/revoke',{method:'POST',headers:{'content-type':'application/x-www-form-urlencoded','cf-connecting-ip':ip()},body:'token='+'A'.repeat(22)+'.'+'B'.repeat(43)});
  assert.equal(unknown.status,200);
  const r=await mf.dispatchFetch(ORIGIN+'/oauth/revoke',{method:'POST',headers:{'content-type':'application/x-www-form-urlencoded','cf-connecting-ip':ip()},body:'token='+t2.refresh_token});
  assert.equal(r.status,200);assert.equal((await mcp(t2.access_token,'ping',{},true)).status,401);
  assert.ok((await sync(p2)).actions.some(a=>a.kind==='lane_revoke'));
});

test('thth logout --lane: the lane key itself asks for its own revocation through the workspace bundle',async()=>{
  const p=await person(),ws=workspace(),value=opaque();sensitive.push(value);
  await sync(p,{workspace_keys:[{sha256:hash(value),workspace:ws,expires_at:later()}]});
  const ask=(bearer,body)=>mf.dispatchFetch(ORIGIN+'/api/v1/login/revoke',{method:'POST',headers:{'content-type':'application/json','cf-connecting-ip':ip(),authorization:'Bearer '+bearer},body:JSON.stringify(body)});
  assert.equal((await keep(await ask(opaque(),{workspace:ws}))).status,401,'an unknown key');
  assert.equal((await ask(value,{workspace:workspace()})).status,401,'the key of another workspace bundle');
  assert.equal((await ask(value,{workspace:ws,extra:1})).status,400);
  assert.equal((await keep(await ask(value,{workspace:ws}))).status,202);
  const actions=(await sync(p,{workspace_keys:[{sha256:hash(value),workspace:ws,expires_at:later()}]})).actions.filter(a=>a.kind==='lane_revoke');
  assert.deepEqual(actions.map(({id,...a})=>a),[{kind:'lane_revoke',workspace:ws,sha256:hash(value)}]);
  // 同じ頼みを重ねない。
  assert.equal((await ask(value,{workspace:ws})).status,202);
  assert.equal((await sync(p)).actions.filter(a=>a.kind==='lane_revoke').length,1);
});

test('fence 5: every tool\'s argv built by the Worker is accepted as is by the VM passthrough table',async()=>{
  const items=[];
  for(const tool of TOOLS){
    const props=tool.inputSchema.properties,args={};
    for(const [name,schema] of Object.entries(props)){
      if(name==='confirm')continue;
      if(schema.type==='boolean')args[name]=true;
      else if(schema.type==='array')args[name]=['queue/a.md'];
      else if(schema.enum)args[name]=schema.enum[0];
      else if(name==='name')args[name]='acct-a';
      else args[name]='7';
    }
    items.push([tool.name,args,false]);
    if(tool.confirm==='vm')items.push([tool.name,{...args,confirm:'e'.repeat(64)},true]);
  }
  const samples=await argvOf(items);
  samples.forEach((argv,i)=>assert.ok(Array.isArray(argv),items[i][0]));
  const script='import json,sys\nfrom thth import server_passthrough as sp\nfor argv in json.load(sys.stdin):\n    sp._parse(argv)\nprint("ok")\n';
  const r=spawnSync('python3',['-c',script],{cwd:repo,input:JSON.stringify(samples),encoding:'utf8'});
  assert.equal(r.status,0,r.stderr);assert.equal(r.stdout.trim(),'ok');
  // 表に無い欄・形の違う値は Worker でも組まない。
  assert.deepEqual(await argvOf([['thth_board',{by:'x'},false],['thth_morning',{name:'../x'},false],['thth_morning',{},false]]),[null,null,null]);
});

test('audit 1: a file that looks like a flag (the approve skip) is invalid_request and never reaches the VM',async()=>{
  const p=await person(),ws=workspace(),account='acct-'+randomBytes(3).toString('hex');
  const tokens=await connect(p);const {tables}=await vmIssue(p,ws,account);
  for(const [name,files] of [['thth_approve',['queue/a.md','--confirm='+'e'.repeat(64)]],['thth_approve',['--confirm='+'e'.repeat(64)]],
    ['thth_withdraw',['../secrets/x']],['thth_withdraw',['/etc/passwd']],['thth_hold',['-']],['thth_revoke',['a//b']]]){
    const r=await callTool(tokens.access_token,name,{account,files});
    assert.equal(content(r).error,'invalid_request',name+' '+files.join(' '));
  }
  assert.equal((await sync(p,tables)).requests.length,0,'nothing reached the VM');
});

test('audit 2: a connector that waits more than an hour before its first tool still binds',async()=>{
  const p=await person(),ws=workspace(),account='acct-'+randomBytes(3).toString('hex');
  const tokens=await connect(p);const {tables}=await vmIssue(p,ws,account);
  const later2h=Date.now()+2*3_600_000;
  // Person DO と Connector の時計だけ 2 時間進める（署名つきの sync は本物の時計なので、この間は VM を呼ばない）。
  await mf.dispatchFetch(ORIGIN+'/__person/person/'+p.id,{method:'POST',body:JSON.stringify({clock:later2h})});
  await control({clock:later2h});
  let first;
  try{
    const next=await tokenFor({grant_type:'refresh_token',refresh_token:tokens.refresh_token});
    assert.equal(next.status,200);sensitive.push(next.body.access_token,next.body.refresh_token);
    tokens.access_token=next.body.access_token;
    // 最初の道具: 結べて頼みが VM へ出る（VM が答えないので pending で返る）。
    first=content(await callTool(tokens.access_token,'thth_board',{}));
    assert.equal(first.status,'pending',JSON.stringify(first));
  }finally{
    await mf.dispatchFetch(ORIGIN+'/__person/person/'+p.id,{method:'POST',body:JSON.stringify({clock:null})});
    await control({clock:null});
  }
  const item=await vmAnswer(p,tables,{command:'board',rc:0,stdout:'{}',stderr:'',truncated:false,workspace:ws});
  assert.equal(item.workspace,ws);
  assert.equal(content(await callTool(tokens.access_token,'thth_result',{request_id:first.request_id})).workspace,ws);
});

test('audit 3: the same AI connected to two workspaces works in both; dropping one asks the VM to revoke only that key',async()=>{
  const p=await person(),A=workspace(),B=workspace(),acctA='acct-'+randomBytes(3).toString('hex'),acctB='acct-'+randomBytes(3).toString('hex');
  const keys=[];
  const issue=async(ws,account)=>{
    const tokens=await connect(p,{ws});
    const {actions}=await sync(p,{workspace_keys:keys});
    const action=actions.find(a=>a.kind==='lane_key');assert.equal(action.workspace,ws);
    keys.push({sha256:action.sha256,workspace:ws,expires_at:later()});
    await sync(p,{workspace_keys:keys,completed:[{id:action.id,outcome:'done',reason:null,workspace:ws,account}]});
    return {tokens,sha256:action.sha256};
  };
  const a=await issue(A,acctA),b=await issue(B,acctB);
  const tables={workspace_keys:keys};
  const board=async(t,ws)=>{
    const pending=callTool(t,'thth_board',{});
    const item=await vmAnswer(p,tables,it=>({command:'board',rc:0,stdout:'{}',stderr:'',truncated:false,workspace:it.workspace}));
    assert.equal(item.workspace,ws);return pending;
  };
  assert.equal(content(await board(a.tokens.access_token,A)).workspace,A);
  assert.equal(content(await board(b.tokens.access_token,B)).workspace,B);
  // 2 本とも一覧にあり、A だけ取り消す。
  const devices=(await control()).filter(([k,d])=>k.startsWith('device:')&&d.owner===p.id);
  assert.equal(devices.length,2);
  const deviceA=devices.find(([,d])=>d.workspace===A)[0].slice(7);
  assert.equal((await form(ORIGIN+'/connectors',{owner:p.id,secret:p.secret,device:deviceA})).status,200);
  const revokes=(await sync(p,tables)).actions.filter(x=>x.kind==='lane_revoke').map(({id,...x})=>x);
  assert.deepEqual(revokes,[{kind:'lane_revoke',workspace:A,sha256:a.sha256}]);
  assert.equal((await mcp(a.tokens.access_token,'ping',{},true)).status,401);
  assert.equal(content(await board(b.tokens.access_token,B)).workspace,B,'B still works');
  // 同じ括り B につなぎ直すと、前の B は結んだときに外れ、VM の取り消しも置かれる（黙って消さない）。
  const b2=await issue(B,acctB);
  assert.equal(content(await board(b2.tokens.access_token,B)).workspace,B);
  await control({alarm:true});
  assert.equal((await mcp(b.tokens.access_token,'ping',{},true)).status,401);
  assert.ok((await sync(p,tables)).actions.some(x=>x.kind==='lane_revoke'&&x.sha256===b.sha256),'the old B key is revoked on the VM');
});

test('audit 4: a client document whose return addresses are not https or loopback http is refused',async()=>{
  const {challenge}=pkce();
  for(const [path,redirect] of [['/odd-http','http://evil.example/cb'],['/odd-js','javascript:alert(1)'],['/odd-app','myapp://cb']]){
    const id='https://claude.ai'+path;
    const r=await keep(await mf.dispatchFetch(authorizeUrl(baseParams(challenge,{client_id:id,redirect_uri:redirect})),{headers:{'cf-connecting-ip':ip()}}));
    assert.equal(r.status,400,path);assert.equal(r.headers.get('location'),null);
  }
});

test('the code expires after a minute, and a correct code works only once',async()=>{
  const p=await person();
  const late=await consent(p);
  await control({clock:Date.now()+61_000});
  try{assert.equal((await tokenFor({grant_type:'authorization_code',code:late.code,code_verifier:late.verifier,client_id:CLAUDE,redirect_uri:CALLBACK})).body.error,'invalid_grant');}
  finally{await control({clock:null});}
  const once=await consent(p);
  const body={grant_type:'authorization_code',code:once.code,code_verifier:once.verifier,client_id:CLAUDE,redirect_uri:CALLBACK};
  const first=await tokenFor(body);assert.equal(first.status,200);sensitive.push(first.body.access_token,first.body.refresh_token);
  assert.equal((await tokenFor(body)).body.error,'invalid_grant');
});

test('the token endpoint refuses a resource other than this MCP',async()=>{
  const p=await person();const {code,verifier}=await consent(p);
  const got=await tokenFor({grant_type:'authorization_code',code,code_verifier:verifier,client_id:CLAUDE,redirect_uri:CALLBACK,resource:'https://k.test/other'});
  assert.equal(got.status,400);assert.equal(got.body.error,'invalid_target');
});

test('a confirm value from one connection does not open the second step on another',async()=>{
  const p1=await person(),p2=await person(),ws1=workspace(),ws2=workspace(),account='acct-'+randomBytes(3).toString('hex');
  const t1=await connect(p1);const {tables:tables1}=await vmIssue(p1,ws1,account);
  const t2=await connect(p2);const {tables:tables2}=await vmIssue(p2,ws2,account);
  const args={account,files:['queue/a.md']};
  const first=content(await callTool(t1.access_token,'thth_withdraw',args));assert.equal(first.status,'confirm_required');
  assert.equal(content(await callTool(t2.access_token,'thth_withdraw',{...args,confirm:first.confirm})).error,'confirm_required');
  assert.equal((await sync(p2,tables2)).requests.length,0);assert.equal((await sync(p1,tables1)).requests.length,0);
});

test('the connectors page lists only the owner\'s own connectors',async()=>{
  const p1=await person(),p2=await person();
  await connect(p1);await connect(p2);
  const ids=async p=>[...(await (await form(ORIGIN+'/connectors',{owner:p.id,secret:p.secret})).text()).matchAll(/name="device" value="([^"]+)"/g)].map(m=>m[1]);
  const mine=(await control()).filter(([k,d])=>k.startsWith('device:')&&d.owner===p1.id).map(([k])=>k.slice(7));
  assert.deepEqual(await ids(p1),mine);
  assert.equal((await ids(p2)).some(id=>mine.includes(id)),false);
});

test('the refresh token expires after 30 days',async()=>{
  const p=await person();const tokens=await connect(p);
  await control({clock:Date.now()+30*86_400_000+1000});
  try{assert.equal((await tokenFor({grant_type:'refresh_token',refresh_token:tokens.refresh_token})).body.error,'invalid_grant');}
  finally{await control({clock:null});}
});

test('fence 6: nothing secret is stored in the clear in the connector object',async()=>{
  const rows=JSON.stringify(await control());
  for(const value of sensitive)assert.equal(rows.includes(value),false,'a secret value is stored in the clear');
});
