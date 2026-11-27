// thth.me の自前の認可サーバ（段 K・設計 共通の層 §6.1。決め: THTH開発 第24 11/27「認可サーバは thth.me に自前」）。
// 形は monban の遠隔 MCP の入口（monban edge/src/oauth.js・10/5 に claude.ai のコネクタで受入済み）に合わせた。
//
//   GET  /.well-known/oauth-protected-resource[/mcp]   守る物の表（RFC 9728）。resource は https://thth.me/mcp
//   GET  /.well-known/oauth-authorization-server       認可サーバの表（RFC 8414）。issuer は https://thth.me
//   GET  /.well-known/openid-configuration             同じ表（OIDC の探し方で来る client のため）
//   POST /oauth/register                               DCR（RFC 7591）。CIMD を持たない client の予備
//   GET  /oauth/authorize?...                          同意の頁（括りを選ぶ login の頁の延長: 持ち主の名前と secret）
//   POST /oauth/authorize                              許す → 段 2 と同じ道でレーンの鍵を 1 本発行 → code
//   POST /oauth/token                                  code → token（PKCE S256）・refresh → 回した token
//   POST /oauth/revoke                                 token の取り消し（RFC 7009）。預かりを消し、VM の鍵も取り消す
//
// - code と PKCE S256 だけ。client の秘密は持たない（`none`）。client の名乗りは CIMD（client_id が https の URL）が第一。
//   claude.ai（Web・デスクトップ・スマホ）は `https://claude.ai/oauth/mcp-oauth-client-metadata`・戻り先は
//   `https://claude.ai/api/mcp/auth_callback`（monban 検証 10/5）。戻り先は登録の通りに一致（loopback だけ port を見ない）。
// - OAuth の断りは OAuth の形（RFC 6749）。THTH の断りの JSON に包まない。
// - 頁の作法は login と同じ（script なし・CSP・no-store・no-referrer・同一 origin の POST）。form-action だけ戻り先の origin を足す。
import {PERSON,WORKSPACE,boundedBody,personStub,fromSameOrigin,page,t,escape,publicQuota} from './person.js';
import {digest,reply} from './relay.js';
import {SECRET,SCOPES,ACCESS_TTL,splitToken,acceptableRedirect} from './connector-object.js';

const TITLE='THTH AI のコネクタを許す';
const CIMD_MAX=16_384,CIMD_TIMEOUT_MS=8_000,STATE_MAX=512;
const CODE_CHALLENGE=/^[A-Za-z0-9_-]{43}$/;
export const connectorStub=env=>env.CONNECTOR.getByName('connectors');
export const resourceUrl=origin=>origin+'/mcp';
export const metadataUrl=origin=>origin+'/.well-known/oauth-protected-resource/mcp';
function metadata(origin){
  return {issuer:origin,authorization_endpoint:origin+'/oauth/authorize',token_endpoint:origin+'/oauth/token',
    registration_endpoint:origin+'/oauth/register',revocation_endpoint:origin+'/oauth/revoke',
    response_types_supported:['code'],response_modes_supported:['query'],grant_types_supported:['authorization_code','refresh_token'],
    code_challenge_methods_supported:['S256'],token_endpoint_auth_methods_supported:['none'],revocation_endpoint_auth_methods_supported:['none'],
    client_id_metadata_document_supported:true,scopes_supported:SCOPES,resource_indicators_supported:true};
}
function resourceMetadata(origin){
  return {resource:resourceUrl(origin),authorization_servers:[origin],scopes_supported:SCOPES,bearer_methods_supported:['header'],
    resource_name:'THTH',resource_documentation:origin+'/'};
}
const json=(status,body,headers={})=>new Response(JSON.stringify(body),{status,headers:{'content-type':'application/json','cache-control':'no-store','pragma':'no-cache','referrer-policy':'no-referrer',...headers}});
const oauthError=(status,error,description)=>json(status,description?{error,error_description:description}:{error});
// sign-in を始める 401（Anthropic: resource_metadata の付いた 401 が要る・200 に付けても見ない）。本文に中身を出さない。
export function unauthorized(origin){
  return json(401,{error:'unauthorized'},{'www-authenticate':`Bearer resource_metadata="${metadataUrl(origin)}"`});
}

export function wellKnownRequest(request,env,url){
  if(request.method!=='GET')return reply(405,{error:'method_not_allowed'});
  if(url.pathname==='/.well-known/oauth-protected-resource'||url.pathname==='/.well-known/oauth-protected-resource/mcp')return json(200,resourceMetadata(url.origin));
  if(url.pathname==='/.well-known/oauth-authorization-server'||url.pathname==='/.well-known/openid-configuration')return json(200,metadata(url.origin));
  return reply(404,{error:'not_found'});
}

// loopback の戻り先は port を見ずに一致（RFC 8252 §7.3。Claude Code は port 無しで名乗る）。
function redirectMatches(registered,given){
  if(registered===given)return true;
  try{
    const a=new URL(registered),b=new URL(given);
    const loop=h=>h==='127.0.0.1'||h==='localhost'||h==='[::1]';
    return a.protocol==='http:'&&b.protocol==='http:'&&loop(a.hostname)&&a.hostname===b.hostname&&a.pathname===b.pathname&&!b.search&&!b.hash;
  }catch{return false;}
}
async function boundedText(stream,max){
  const reader=stream.getReader(),chunks=[];let size=0;
  for(;;){const {value,done}=await reader.read();if(done)break;size+=value.length;if(size>max){await reader.cancel();return null;}chunks.push(value);}
  const all=new Uint8Array(size);let i=0;for(const c of chunks){all.set(c,i);i+=c.length;}
  return new TextDecoder().decode(all);
}
// client の身元。CIMD（https の URL が身元・ここで取りに行く）が第一、DCR で登録した client が第二。
export async function clientRecord(env,client_id){
  if(typeof client_id!=='string'||client_id.length>512)return null;
  if(/^https:\/\//.test(client_id)){
    let u;try{u=new URL(client_id);}catch{return null;}
    if(u.username||u.password||u.hash||u.pathname==='/'||u.hostname==='localhost'||u.hostname.endsWith('.localhost')||
       u.hostname.startsWith('[')||/^(\d+\.){3}\d+$/.test(u.hostname))return null;
    // 監査 (8): 確かめて通った文書は 10 分控える（同意の頁を開くたび・許すたびに外へ取りに行かない）。
    const stub=connectorStub(env),key=await digest(client_id);
    try{const kept=await stub.cimdGet(key);if(kept.status===200)return kept.body.failed?null:{...kept.body,kind:'cimd'};}catch{/* 控えが読めなければ取りに行く */}
    const found=await fetchCimd(client_id,u);
    try{if(found)await stub.cimdPut(key,found);else await stub.cimdFailed(key);}catch{/* 控えられなくても今回は通す */}
    return found?{...found,kind:'cimd'}:null;
  }
  const row=await connectorStub(env).client(client_id);
  return row.status===200?{...row.body,name:row.body.name??'AI client',kind:'dcr'}:null;
}
// CIMD の文書を取りに行き、形を確かめる（通らなければ null）。
async function fetchCimd(client_id,u){
    try{
      // workerd は redirect: 'error' を受けない（monban 10/5 の失敗）。'manual' で 3xx を受け、下で断る。
      const got=await fetch(client_id,{headers:{accept:'application/json'},redirect:'manual',signal:AbortSignal.timeout(CIMD_TIMEOUT_MS)});
      if(got.status!==200||!/^application\/json/i.test(got.headers.get('content-type')||''))return null;
      if(Number(got.headers.get('content-length')||0)>CIMD_MAX)return null;
      const text=await boundedText(got.body,CIMD_MAX);if(text===null)return null;
      const doc=JSON.parse(text);
      if(!doc||typeof doc!=='object'||doc.client_id!==client_id||!Array.isArray(doc.redirect_uris)||!doc.redirect_uris.length||doc.redirect_uris.length>8)return null;
      // 戻り先は DCR と同じ形（https か loopback の http だけ）の物だけを残す（監査 (11)）。アプリ用の scheme を一緒に載せる
      // AI（ChatGPT など）も、合う戻り先で頼めば通る。合う物が 1 つも無ければ client ごと断る。頼まれた戻り先はこの中から選ぶ。
      const redirect_uris=doc.redirect_uris.filter(acceptableRedirect);
      if(!redirect_uris.length)return null;
      if(doc.token_endpoint_auth_method!==undefined&&doc.token_endpoint_auth_method!=='none')return null;
      const name=typeof doc.client_name==='string'&&!/[\u0000-\u001f\u007f]/.test(doc.client_name)?doc.client_name.slice(0,128):u.hostname;
      return {redirect_uris,name,host:u.hostname};
    }catch{return null;}
}
// コネクタのレーンの名（鍵の記録の名乗り「<持ち主>（lane:connector-…・key:…）」になる）。CIMD はホスト名と client の id の
// hash の頭 6 桁（1 つのホストに CIMD の文書が 2 つあっても別のレーン・VM は同じ括りの同じレーンの前の鍵を取り消すため）、
// DCR は client の id の hash。頭の `connector-` は VM の通り道が位置の引数を `--` のあとにだけ受ける印（server_passthrough）。
export async function connectorLane(client,client_id){
  const hashed=await digest(client_id);
  const base=client.kind==='cimd'?client.host.toLowerCase().replace(/[^a-z0-9]+/g,'-').replace(/^-+|-+$/g,'').slice(0,40)+'-'+hashed.slice(0,6)
    :'dcr-'+hashed.slice(0,12);
  return 'connector-'+base.replace(/^-+/,'');
}
function parseAuthorize(params){
  const g=k=>params.get(k);
  const scope=(g('scope')||'read').split(' ').filter(Boolean);
  const problems=[];
  if(g('response_type')!=='code')problems.push('unsupported_response_type');
  if(!g('client_id'))problems.push('invalid_client');
  if(!g('redirect_uri')||g('redirect_uri').length>512)problems.push('invalid_request');
  // PKCE は S256 だけ（plain と、無いのは断る）。
  if(!CODE_CHALLENGE.test(g('code_challenge')||'')||g('code_challenge_method')!=='S256')problems.push('invalid_request');
  if(!scope.length||scope.length>3||!scope.every(x=>SCOPES.includes(x))||new Set(scope).size!==scope.length)problems.push('invalid_scope');
  if((g('state')||'').length>STATE_MAX)problems.push('invalid_request');
  return {client_id:g('client_id'),redirect_uri:g('redirect_uri'),code_challenge:g('code_challenge'),scope,state:g('state')||null,
    resource:g('resource')||null,problems};
}
const hidden=(name,value)=>value===null||value===undefined?'':`<input type="hidden" name="${escape(name)}" value="${escape(value)}">`;
function oauthFields(a){
  return hidden('response_type','code')+hidden('client_id',a.client_id)+hidden('redirect_uri',a.redirect_uri)+hidden('code_challenge',a.code_challenge)+
    hidden('code_challenge_method','S256')+hidden('scope',a.scope.join(' '))+hidden('state',a.state)+hidden('resource',a.resource);
}
// 同意の頁（括りを選ぶ login の頁 `laneForm` の延長）: 括り・レーン・範囲を見せ、持ち主の名前と secret で許す。
function consentPage(a,client,lane,status=200,note=''){
  const host=(()=>{try{return new URL(a.redirect_uri).host;}catch{return a.redirect_uri;}})();
  const back=(()=>{try{const o=new URL(a.redirect_uri).origin;return /^https?:\/\//.test(o)?o:null;}catch{return null;}})();
  const write=a.scope.includes('write');
  const reach=write
    ?t('<strong>読む・書く</strong>: 括りの一枚・毎朝の一枚などを読み、原稿の承認・承認の取り消し・閉じる（どれも 2 段で、1 段目の中身をあなたに見せてから）を行えます。','<strong>Read and write</strong>: read the workspace board, morning summary and similar, and approve, unapprove or withdraw drafts (each in two steps, showing you the first step before acting).')
    :t('<strong>読むだけ</strong>: 括りの一枚・毎朝の一枚などを読めます。書く道具は使えません。','<strong>Read only</strong>: read the workspace board, morning summary and similar. No write tools.');
  return page(status,`<h1>${t('AI のコネクタに許す','Allow an AI connector')}</h1>${note}
<p>${t(`<strong>${escape(client.name)}</strong> が、あなたの括りの THTH の道具を使おうとしています。自分の AI から始めたのでなければ、このページを閉じてください。`,`<strong>${escape(client.name)}</strong> asks to use THTH tools in your workspace. If you did not start this from your own AI, close this page.`)}</p>
<dl class="facts"><dt>${t('戻り先','Returns to')}</dt><dd>${escape(host)}</dd><dt>${t('レーン','Lane')}</dt><dd>${escape(lane)}</dd><dt>${t('範囲','Scope')}</dt><dd>${reach}</dd></dl>
<form method="post" action="/oauth/authorize">${oauthFields(a)}
<label>${t('持ち主の名前','Owner name')} <input name="owner" autocomplete="username" required maxlength="64"></label>
<label>${t('持ち主のシークレット','Owner secret')} <input type="password" name="secret" autocomplete="current-password" required maxlength="128"></label>
<label>${t('括り（1 つだけなら空のまま）','Workspace (leave empty if you have only one)')} <input name="workspace" maxlength="32" autocomplete="off" data-1p-ignore data-lpignore="true" data-bwignore="true" data-form-type="other"></label>
${write?`<label class="pick"><input type="checkbox" name="readonly" value="1"> ${t('読むだけにする（書く道具を許さない）','Read only (do not allow write tools)')}</label>`:''}
<button type="submit">${t('許可する','Allow')}</button></form>
<form method="post" action="/oauth/authorize">${hidden('client_id',a.client_id)}${hidden('redirect_uri',a.redirect_uri)}${hidden('state',a.state)}${hidden('deny','1')}<button type="submit" class="quiet">${t('許可しない','Deny')}</button></form>
<p class="note">${t('許すと、このコネクタ専用のレーンの鍵が 1 本できます（同じレーンの前の鍵は止まります）。鍵はこの画面にも AI にも出ません。サーバーが数十秒で括りに結び付けます。それまで AI には「数十秒後にもう一度」と出ます。取り消しは <a href="/connectors">/connectors</a> から。','Allowing creates one lane key for this connector (the previous key of the same lane stops). The key is shown neither here nor to the AI. The server binds it to your workspace within a few tens of seconds; until then the AI sees "try again in a few tens of seconds". Revoke it at <a href="/connectors">/connectors</a>.')}</p>`,
  TITLE,'',back?`'self' ${back}`:"'self'");
}
const errorPage=(status,ja,en)=>page(status,`<h1>${t('続けられません','Cannot continue')}</h1><p class="err">${t(ja,en)}</p>`,TITLE);
function redirectBack(redirect_uri,params){
  const u=new URL(redirect_uri);
  for(const [k,v] of Object.entries(params))if(v!==null&&v!==undefined)u.searchParams.set(k,v);
  return new Response(null,{status:303,headers:{location:u.toString(),'cache-control':'no-store','referrer-policy':'no-referrer'}});
}
const SECRET_FAILED=`<p class="err">${t('確かめられませんでした。持ち主の名前とシークレットを確かめてください。5 回続けて間違えると 15 分閉じます。','Could not confirm. Check the owner name and the secret. After five failures in a row it is closed for 15 minutes.')}</p>`;
const BAD_WORKSPACE=`<p class="err">${t('括りの名の形ではありません（英小文字で始まる 32 字まで）。','That is not a workspace name (lowercase, starts with a letter, up to 32 characters).')}</p>`;

export async function oauthRequest(request,env,url){
  try{
    if(url.search&&url.pathname!=='/oauth/authorize'||url.hash||url.pathname.includes('%'))return reply(400,{error:'invalid_request'});
    if(!env.CONNECTOR||!env.PERSON)return reply(503,{error:'oauth_unavailable'});
    if(!await publicQuota(request,env))return reply(429,{error:'rate_limited'});
    if(url.pathname==='/oauth/register')return register(request,env);
    if(url.pathname==='/oauth/authorize')return authorize(request,env,url);
    if(url.pathname==='/oauth/token')return token(request,env,url);
    if(url.pathname==='/oauth/revoke')return revokeToken(request,env);
    return reply(404,{error:'not_found'});
  }catch{return reply(503,{error:'oauth_unavailable'});}
}

// 登録を数える所: IPv4 はそのまま、IPv6 は /64（1 台が番地を好きに替えられる幅）に丸める。値は Connector の中で塩と混ぜて hash にし、
// そのまま保存しない。
export function peerAddress(ip){
  if(typeof ip!=='string'||!ip||ip.length>64)return 'unknown-peer';
  if(!ip.includes(':'))return ip;
  const [head,tail]=ip.toLowerCase().split('::');
  const left=head?head.split(':'):[],right=tail!==undefined&&tail?tail.split(':'):[];
  const groups=tail===undefined?left:[...left,...Array(Math.max(0,8-left.length-right.length)).fill('0'),...right];
  return groups.slice(0,4).map(g=>g.replace(/^0+(?=.)/,'')).join(':')+'::/64';
}
async function register(request,env){
  if(request.method!=='POST')return reply(405,{error:'method_not_allowed'});
  if(!/^application\/json(?:\s*;|$)/i.test(request.headers.get('content-type')||''))return oauthError(400,'invalid_client_metadata');
  let m;try{m=JSON.parse(await boundedBody(request,8192));}catch{return oauthError(400,'invalid_client_metadata');}
  if(m?.token_endpoint_auth_method!==undefined&&m.token_endpoint_auth_method!=='none')return oauthError(400,'invalid_client_metadata','public clients only');
  const made=await connectorStub(env).registerClient(m,peerAddress(request.headers.get('cf-connecting-ip')));
  if(made.status!==200)return oauthError(made.status===429?429:400,made.body.error);
  return json(201,{client_id:made.body.client_id,client_id_issued_at:Math.floor(Date.now()/1000),redirect_uris:m.redirect_uris,
    token_endpoint_auth_method:'none',grant_types:['authorization_code','refresh_token'],response_types:['code']});
}

async function authorize(request,env,url){
  if(!['GET','POST'].includes(request.method))return reply(405,{error:'method_not_allowed'});
  if(request.method==='POST'&&(url.search||!fromSameOrigin(request,url)))return reply(403,{error:'forbidden'});
  const params=request.method==='GET'?url.searchParams:new URLSearchParams(await boundedBody(request,4096));
  const a=parseAuthorize(params);
  const client=a.problems.includes('invalid_client')?null:await clientRecord(env,a.client_id);
  if(!client)return errorPage(400,'知らない AI のクライアントです。AI の側からコネクタの追加をやり直してください。','Unknown client. Add the connector again from your AI.');
  if(!a.redirect_uri||!client.redirect_uris.some(r=>redirectMatches(r,a.redirect_uri)))
    return errorPage(400,'戻り先が、このクライアントの登録と合いません。','The return address is not one this client registered.');
  // ここから先は戻り先を信じてよい: 断りは client に戻す（RFC 6749 §4.1.2.1）。
  if(request.method==='POST'&&params.get('deny')==='1')return redirectBack(a.redirect_uri,{error:'access_denied',state:a.state});
  // 形の違う頼みは頁で断る（登録の戻り先へ知らない人を自分から送らない・RFC 9700 §4.11）。
  if(a.problems.length)return errorPage(400,`頼みの形が違います（${escape(a.problems[0])}）。AI の側からコネクタの追加をやり直してください。`,`The request is malformed (${escape(a.problems[0])}). Add the connector again from your AI.`);
  if(a.resource!==null&&a.resource!==resourceUrl(url.origin))return errorPage(400,'求められた resource が THTH の MCP ではありません。','The requested resource is not THTH MCP.');
  const lane=await connectorLane(client,a.client_id);
  if(request.method==='GET')return consentPage(a,client,lane);
  const owner=params.get('owner')||'',secret=params.get('secret')||'',ws=(params.get('workspace')||'').trim()||null;
  if(!PERSON.test(owner))return consentPage(a,client,lane,403,SECRET_FAILED);
  if(ws!==null&&!WORKSPACE.test(ws))return consentPage(a,client,lane,400,BAD_WORKSPACE);
  if(!(await env.API_KEY_LIMIT.limit({key:'oauth:'+await digest(owner)})).success)return reply(429,{error:'rate_limited'});
  // 持ち主が読むだけに絞れる（RFC 6749 §3.3: token の答えは絞った scope を言う）。
  const scope=params.get('readonly')==='1'?a.scope.filter(s=>s!=='write'):a.scope;
  const writes=scope.includes('write');
  let issued;
  try{issued=await (await personStub(env,owner)).laneIssue(secret,ws,lane,writes);}catch{issued={status:503};}
  if(issued.status===403)return consentPage(a,client,lane,403,SECRET_FAILED);
  if(issued.status!==200)return redirectBack(a.redirect_uri,{error:'temporarily_unavailable',state:a.state});
  const made=await connectorStub(env).createCode({client_id:a.client_id,client_name:client.name,redirect_uri:a.redirect_uri,
    code_challenge:a.code_challenge,scope,writes,owner,lane,workspace:ws,action:issued.body.action,key:issued.body.bearer});
  if(made.status!==200){
    // 鍵は発行を置いてしまった: VM が作ったあとで止まるよう、取り消しも置く。
    try{await (await personStub(env,owner)).laneRevoke(ws,await digest(issued.body.bearer));}catch{/* 置けなければ、鍵は VM で発行の頼みが 1 時間で切れて作られない */}
    return redirectBack(a.redirect_uri,{error:'temporarily_unavailable',state:a.state});
  }
  return redirectBack(a.redirect_uri,{code:made.body.code,state:a.state});
}

async function token(request,env,url){
  if(request.method!=='POST')return reply(405,{error:'method_not_allowed'});
  if(!/^application\/x-www-form-urlencoded(?:\s*;|$)/i.test(request.headers.get('content-type')||''))return oauthError(400,'invalid_request','application/x-www-form-urlencoded');
  const form=new URLSearchParams(await boundedBody(request,8192));
  const stub=connectorStub(env),grant=form.get('grant_type');
  if(form.get('resource')&&form.get('resource')!==resourceUrl(url.origin))return oauthError(400,'invalid_target');
  let made;
  if(grant==='authorization_code'){
    if(!form.get('code')||!form.get('code_verifier')||!form.get('client_id'))return oauthError(400,'invalid_request','code, code_verifier and client_id are required');
    made=await stub.exchange({code:form.get('code'),code_verifier:form.get('code_verifier'),client_id:form.get('client_id'),redirect_uri:form.get('redirect_uri')});
  }else if(grant==='refresh_token'){
    if(!form.get('refresh_token'))return oauthError(400,'invalid_request','refresh_token is required');
    if(!splitToken(form.get('refresh_token')))return oauthError(400,'invalid_grant');
    made=await stub.refresh(form.get('refresh_token'));
  }else return oauthError(400,'unsupported_grant_type');
  if(made.status!==200)return oauthError(400,made.body?.error==='storage_unavailable'?'temporarily_unavailable':'invalid_grant');
  return json(200,{...made.body,expires_in:ACCESS_TTL/1000});
}

// RFC 7009: 知らない token にも 200（あるかないかを見せない）。
async function revokeToken(request,env){
  if(request.method!=='POST')return reply(405,{error:'method_not_allowed'});
  if(!/^application\/x-www-form-urlencoded(?:\s*;|$)/i.test(request.headers.get('content-type')||''))return oauthError(400,'invalid_request');
  const form=new URLSearchParams(await boundedBody(request,4096));
  const value=form.get('token');
  if(!value)return oauthError(400,'invalid_request');
  if(splitToken(value))await connectorStub(env).revokeToken(value);
  return new Response(null,{status:200,headers:{'cache-control':'no-store','referrer-policy':'no-referrer'}});
}
export {SECRET as TOKEN_SECRET};
