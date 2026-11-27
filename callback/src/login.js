// ブラウザ式の `thth login`（設計 3.14.2 §2）。貼る作業を無くす（`gh auth login` と同じ形）。
//
//   CLI:  POST /api/v1/login/start {code_hash, poll_token_hash}         → 202 {url, expires_at}
//         （url は `<origin>/login/`。CLI が手元の code を足して開く。Worker は code を受け取らない）
//         GET  /api/v1/login/poll/<code>  Authorization: Bearer <poll_token>
//              → 202 {status: waiting} / 200 {key, account}（1 度だけ） / 410 / 404 / 401
//   人:   GET  /login/<code>  → アカウント名とアカウントのシークレット の form（「このデバイスにキーを渡す / Allow this device」）
//         POST /login/<code>  → Person DO で secret を照合 → rotate と同じキーの発行 → Login DO に 1 度だけ置く
//
// - キーは画面に出さない・記録しない。Login DO に最長 10 分、CLI が受け取った時点で消す。
// - 作法は /activity と同じ（script なし・CSP・no-store・no-referrer・同一 origin の POST）。
// - 回数制限: /login/* と start は RELAY_PUBLIC_LIMIT（IP）。poll は IP に加えて API_KEY_LIMIT を code ごと（60 回／分）。
//
// レーンの鍵（`thth login --lane <レーン> [--workspace <括り>]`・括りの段 2）:
//   CLI:  start に {lane[, workspace]}（秘密でない名だけ）を足す
//   人:   /login/<code> は「括り・レーン」を見せ、**持ち主の名前と持ち主の secret** で許す → Person DO に lane_key を置く
//   CLI:  poll → 200 {key, owner, lane, workspace}（1 度だけ）。そのあとの poll は 200 {outcome, reason, workspace, account}
//         （VM が sync で鍵を作ったか・作らなかった理由。outcome は pending／done／failed）
//   括りの持ち主か・括りが 1 つに決まるかは VM が対応表で決める（Worker は括りと持ち主の対応を持たない）。
import {PERSON,WORKSPACE,LANE,boundedBody,personStub,workspaceStub,fromSameOrigin,page,t,steps,escape,publicQuota} from './person.js';
import {HASH_PATTERN,digest,reply} from './relay.js';

// 大文字と数字から紛らわしい字（0 O 1 I L）を除いた 31 字。表示は XXXX-XXXX。
export const CODE_ALPHABET='ABCDEFGHJKMNPQRSTUVWXYZ23456789';
export const LOGIN_CODE=/^[A-HJKMNP-Z2-9]{4}-[A-HJKMNP-Z2-9]{4}$/;
const POLL_BEARER=/^Bearer ([A-Za-z0-9_-]{43})$/;
const TITLE='THTH このデバイスにキーを渡す';

// code は手で打たれることもあるので大文字に揃えてから引く（hash も揃えた形で取る）。
const normalized=code=>typeof code==='string'?code.toUpperCase():'';
const loginStub=(env,codeHash)=>env.LOGIN.getByName(codeHash);

export async function loginApiRequest(request,env,url){
  try{
    if(url.search||url.hash||url.pathname.includes('%'))return reply(400,{error:'invalid_request'});
    if(!env.LOGIN||!env.API_KEY_LIMIT)return reply(503,{error:'login_unavailable'});
    if(url.pathname==='/api/v1/login/start'){
      if(request.method!=='POST')return reply(405,{error:'method_not_allowed'});
      if(!await publicQuota(request,env))return reply(429,{error:'rate_limited'});
      if(!/^application\/json(?:\s*;|$)/i.test(request.headers.get('content-type')||''))return reply(400,{error:'invalid_request'});
      let body;
      try{body=JSON.parse(await boundedBody(request,1024));}catch{return reply(400,{error:'invalid_request'});}
      const shape=body!==null&&typeof body==='object'&&!Array.isArray(body)?Object.keys(body).sort().join(','):'';
      if(!['code_hash,poll_token_hash','code_hash,lane,poll_token_hash','code_hash,lane,poll_token_hash,workspace'].includes(shape)||
         typeof body.code_hash!=='string'||!HASH_PATTERN.test(body.code_hash)||
         typeof body.poll_token_hash!=='string'||!HASH_PATTERN.test(body.poll_token_hash)||
         'lane' in body&&(typeof body.lane!=='string'||!LANE.test(body.lane))||
         'workspace' in body&&(typeof body.workspace!=='string'||!WORKSPACE.test(body.workspace)))return reply(400,{error:'invalid_request'});
      // `connector-` で始まる名は AI のコネクタの同意の頁だけが付ける（古い CLI からも作らせない・Mac の CLI と同じ断り）。
      if(typeof body.lane==='string'&&body.lane.startsWith('connector-'))return reply(400,{error:'lane_reserved'});
      const started=await loginStub(env,body.code_hash).start(body.poll_token_hash,body.lane??null,body.workspace??null);
      if(started.status!==200)return reply(started.status,started.body);
      return reply(202,{url:url.origin+'/login/',expires_at:new Date(started.body.expires_at).toISOString()});
    }
    // `thth logout --lane`（段 K）: レーンの鍵そのものを Bearer にして、自分の取り消しを頼む（持っていることが証し）。
    // 括りの束の鍵の表から押し上げた持ち主を引き、Person DO に取り消しを置く。VM が次の sync で括りの表の行を止める。
    if(url.pathname==='/api/v1/login/revoke'){
      if(request.method!=='POST')return reply(405,{error:'method_not_allowed'});
      if(!await publicQuota(request,env))return reply(429,{error:'rate_limited'});
      const bearer=/^Bearer ([A-Za-z0-9_-]{43})$/.exec(request.headers.get('authorization')||'');
      if(!bearer)return reply(401,{error:'invalid_key'});
      const sha256=await digest(bearer[1]);
      if(!(await env.API_KEY_LIMIT.limit({key:sha256})).success)return reply(429,{error:'rate_limited'});
      if(!/^application\/json(?:\s*;|$)/i.test(request.headers.get('content-type')||''))return reply(400,{error:'invalid_request'});
      let body;try{body=JSON.parse(await boundedBody(request,256));}catch{return reply(400,{error:'invalid_request'});}
      if(!body||typeof body!=='object'||Array.isArray(body)||Object.keys(body).join(',')!=='workspace'||
         typeof body.workspace!=='string'||!WORKSPACE.test(body.workspace))return reply(400,{error:'invalid_request'});
      const owner=await (await workspaceStub(env,body.workspace)).keyOwner(sha256);
      if(owner.status!==200)return reply(401,{error:'invalid_key'});
      const placed=await (await personStub(env,owner.body.person)).laneRevoke(body.workspace,sha256);
      if(placed.status!==200)return reply(placed.status===409?409:503,{error:placed.status===409?'too_many_actions':'login_unavailable'});
      return reply(202,{status:'accepted'});
    }
    const poll=/^\/api\/v1\/login\/poll\/([^/]+)$/.exec(url.pathname);
    if(!poll)return reply(404,{error:'not_found'});
    if(request.method!=='GET')return reply(405,{error:'method_not_allowed'});
    if(!await publicQuota(request,env))return reply(429,{error:'rate_limited'});
    const code=normalized(poll[1]);
    if(!LOGIN_CODE.test(code))return reply(404,{error:'not_found'});
    const codeHash=await digest(code);
    if(!(await env.API_KEY_LIMIT.limit({key:'login:'+codeHash})).success)return reply(429,{error:'rate_limited'});
    const token=POLL_BEARER.exec(request.headers.get('authorization')||'');
    if(!token)return reply(401,{error:'unauthorized'});
    const got=await loginStub(env,codeHash).poll(await digest(token[1]));
    if(got.status===200&&got.body.taken){
      // レーンの鍵を受け取ったあと: VM が鍵を作ったか（Person DO が sync の結果を持つ）。
      let outcome;
      try{outcome=await (await personStub(env,got.body.person)).laneOutcome(got.body.action);}catch{outcome={status:503};}
      if(outcome.status!==200)return reply(outcome.status===404?410:503,{error:outcome.status===404?'expired':'login_unavailable'});
      return reply(200,outcome.body);
    }
    return reply(got.status,got.body);
  }catch{return reply(503,{error:'login_unavailable'});}
}

function allowForm(code,status=200,note='',head=''){
  return page(status,`${steps(3)}<h1>${t('このデバイスにキーを渡す','Allow this device')}</h1>${note}
<p>${t('ターミナルで <code>thth login</code> を打ったのがあなた自身なら、アカウント名とシークレットを入れて許可してください。その機械に API キーが渡り、前のキーは使えなくなります。','If you ran <code>thth login</code> in your own terminal, enter your account name and secret to allow it. The device receives an API key, and the previous key stops working.')}</p>
<p class="status">${t('コード','Code')}: <strong>${escape(code)}</strong> — ${t('ターミナルに出たものと同じか確かめてください。','check that it matches your terminal.')}</p>
<form method="post" action="/login/${escape(code)}"><label>${t('アカウント名','Account name')} <input name="account" autocomplete="username" required maxlength="64"></label><label>${t('アカウントのシークレット','Account secret')} <input type="password" name="secret" autocomplete="current-password" required maxlength="128"></label><button type="submit">${t('許可する','Allow')}</button></form>
<p class="note">${t('自分で打っていないなら、このページを閉じてください。10 分で使えなくなります。API キーはこの画面には出ません。','If you did not run it yourself, close this page. It stops working after 10 minutes. The API key is never shown on this screen.')}</p>`,TITLE,head);
}
// レーンの鍵の form（括りの段 2）: 括りとレーンを見せ、持ち主の名前と持ち主の secret で許す。
function laneForm(code,lane,workspace,status=200,note=''){
  const where=workspace?escape(workspace):t('あなたの括り（1 つだけのとき）','your workspace (when you have only one)');
  return page(status,`<h1>${t('このデバイスにレーンの鍵を渡す','Allow this device for a lane')}</h1>${note}
<p>${t('ターミナルで <code>thth login --lane</code> を打ったのがあなた自身なら、括りの持ち主の名前と持ち主のシークレットを入れて許可してください。そのレーンの機械に鍵が渡り、同じレーンの前の鍵は使えなくなります。','If you ran <code>thth login --lane</code> in your own terminal, enter the workspace owner name and the owner secret to allow it. The lane receives a key, and the previous key of the same lane stops working.')}</p>
<dl class="facts"><dt>${t('括り','Workspace')}</dt><dd>${where}</dd><dt>${t('レーン','Lane')}</dt><dd>${escape(lane)}</dd><dt>${t('コード','Code')}</dt><dd><strong>${escape(code)}</strong></dd></dl>
<p class="status">${t('コードがターミナルに出たものと同じか確かめてください。','Check that the code matches your terminal.')}</p>
<form method="post" action="/login/${escape(code)}"><label>${t('持ち主の名前','Owner name')} <input name="owner" autocomplete="username" required maxlength="64"></label><label>${t('持ち主のシークレット','Owner secret')} <input type="password" name="secret" autocomplete="current-password" required maxlength="128"></label><button type="submit">${t('許可する','Allow')}</button></form>
<p class="note">${t('括りの持ち主でないとき・括りが 2 つ以上で選んでいないときは、サーバーが鍵を作らず、ターミナルに理由と直し方が出ます。自分で打っていないなら、このページを閉じてください。10 分で使えなくなります。','If you do not own the workspace, or you own two or more and did not choose one, the server does not create the key and your terminal shows why and how to fix it. If you did not run it yourself, close this page. It stops working after 10 minutes.')}</p>`,TITLE);
}
const laneRefused=(code,lane,workspace)=>laneForm(code,lane,workspace,403,`<p class="err">${t('確かめられませんでした。持ち主の名前とシークレットを確かめてください。5 回続けて間違えると 15 分閉じます。','Could not confirm. Check the owner name and the secret. After five failures in a row it is closed for 15 minutes.')}</p>`);
const laneDone=()=>page(200,`<h1>${t('できました。ターミナルに戻ってください','Done. Go back to your terminal')}</h1><p class="status">${t('鍵はターミナルが受け取りました。サーバーが数十秒で括りに結び付け、ターミナルが生きたかを確かめて括りの名を出します。このページは閉じて構いません。','The terminal has received the key. The server binds it to the workspace within a few tens of seconds, and the terminal checks it and shows the workspace name. You can close this page.')}</p>`,TITLE);
const unusable=(status=410)=>page(status,`<h1>${t('このページは使えません','This page cannot be used')}</h1><p class="err" data-error="${status}">${t('期限（10 分）が切れたか、もう使われました。ターミナルで <code>thth login</code> をやり直してください。','It has expired (10 minutes) or has already been used. Run <code>thth login</code> again in your terminal.')}</p>`,TITLE);
const busy=()=>page(409,`<h1>${t('確かめている途中です','Still checking')}</h1><p>${t('少し待ってから、このページを開き直してください。','Wait a moment and reload this page.')}</p>`,TITLE);
const refused=code=>allowForm(code,403,`<p class="err">${t('確かめられませんでした。アカウント名とシークレットを確かめてください。5 回続けて間違えると 15 分閉じます。','Could not confirm. Check the account name and the secret. After five failures in a row it is closed for 15 minutes.')}</p>`);
const notReady=(code,head='')=>allowForm(code,409,`<p class="err">${t('アカウントの状態がまだサーバーから届いていないか、アカウントが 1 つに決められません。アカウント名を確かめ、数十秒後にもう一度押してください。','The server has not reported this account yet, or the account could not be determined. Check the account name and try again in a few tens of seconds.')}</p>`,head);
// アカウントの状態がまだ届いていない（招待の完了の直後・3.14.3 穴 2）: script を使わず、5 秒ごとに自分で読み直す。
// 届けば同じ URL がアカウント名と secret の form に戻る。
const REFRESH='<meta http-equiv="refresh" content="5">';
const waiting=code=>page(200,`${steps(3)}<h1>${t('届くまで待っています（数十秒）','Waiting for the server (a few tens of seconds)')}</h1>
<p class="status">${t('アカウントの状態がまだサーバーから届いていません。このページは 5 秒ごとに読み直し、届いたら入力の画面に戻ります。閉じずにお待ちください。','The server has not reported your account yet. This page reloads every 5 seconds and shows the form once it arrives. Keep it open.')}</p>
<p>${t('コード','Code')}: <strong>${escape(code)}</strong>。${t('10 分を過ぎたらターミナルで <code>thth login</code> をやり直してください。','After 10 minutes, run <code>thth login</code> again in your terminal.')}</p>`,TITLE,REFRESH);
// POST の 409 は今のまま。まだ届いていないときだけ、5 秒後に同じ URL の GET（上の待つページ）へ移る。
const notReported=code=>notReady(code,`<meta http-equiv="refresh" content="5;url=/login/${escape(code)}">`);
const done=()=>page(200,`<h1>${t('できました。ターミナルに戻ってください','Done. Go back to your terminal')}</h1><p class="status">${t('API キーはターミナルが受け取りました。数十秒で使えるようになり、前のキーは使えなくなります。このページは閉じて構いません。','The terminal has received the API key. It works within a few tens of seconds, and the previous key stops working. You can close this page.')}</p>
<p>${t('確かめるには: <code>thth account status &lt;アカウント名&gt;</code>','To check: <code>thth account status &lt;account&gt;</code>')}</p>`,TITLE);

export async function loginRequest(request,env,url){
  try{
    if(url.search||url.hash||url.pathname.includes('%'))return reply(400,{error:'invalid_request'});
    if(!env.LOGIN||!env.PERSON)return reply(503,{error:'login_unavailable'});
    const match=/^\/login\/([^/]+)$/.exec(url.pathname);
    const code=normalized(match?.[1]);
    if(!match||!LOGIN_CODE.test(code))return unusable(404);
    if(!await publicQuota(request,env))return reply(429,{error:'rate_limited'});
    if(!['GET','POST'].includes(request.method))return reply(405,{error:'method_not_allowed'});
    const stub=loginStub(env,await digest(code));
    if(request.method==='GET'){
      const state=await stub.state();
      if(state.status!==200)return unusable(state.status===404?404:410);
      if(state.body.lane)return laneForm(code,state.body.lane,state.body.workspace);
      const pending=state.body.pending;
      if(typeof pending==='string'&&PERSON.test(pending)){
        let reported;
        try{reported=await (await personStub(env,pending)).loginReported();}catch{reported={status:503};}
        if(reported.status===200&&!reported.body.reported)return waiting(code);
      }
      return allowForm(code);
    }
    if(!fromSameOrigin(request,url))return reply(403,{error:'forbidden'});
    const form=new URLSearchParams(await boundedBody(request,2048));
    const state=await stub.state();
    if(state.status!==200)return unusable(state.status===404?404:410);
    if(state.body.lane)return laneAllow(stub,env,code,form,state.body.lane,state.body.workspace);
    if([...form.keys()].sort().join(',')!=='account,secret')return reply(400,{error:'invalid_request'});
    const name=form.get('account');
    // アカウント名の形が違えば照合に進まない（Login の状態も変えない）。
    if(!PERSON.test(name))return refused(code);
    const claimed=await stub.claim();
    if(claimed.status===409)return busy();
    if(claimed.status!==200)return unusable(claimed.status===404?404:410);
    let issued;
    try{issued=await (await personStub(env,name)).loginIssue(form.get('secret'),name);}
    catch{issued={status:503};}
    if(issued.status!==200){
      await stub.release();
      if(issued.status===403)return refused(code);
      if(issued.status===404&&issued.body?.error==='not_reported'){
        await stub.expect(name);
        return notReported(code);
      }
      if(issued.status===404||issued.status===409)return notReady(code);
      return reply(503,{error:'login_unavailable'});
    }
    const placed=await stub.deliver(issued.body.bearer,issued.body.account);
    return placed.status===200?done():unusable(410);
  }catch{return reply(503,{error:'login_unavailable'});}
}

// レーンの鍵の許可（括りの段 2）。form は持ち主の名前と secret だけ（口座の form の欄は受けない）。
async function laneAllow(stub,env,code,form,lane,workspace){
  if([...form.keys()].sort().join(',')!=='owner,secret')return reply(400,{error:'invalid_request'});
  const name=form.get('owner');
  if(!PERSON.test(name))return laneRefused(code,lane,workspace);
  const claimed=await stub.claim();
  if(claimed.status===409)return busy();
  if(claimed.status!==200)return unusable(claimed.status===404?404:410);
  let issued;
  try{issued=await (await personStub(env,name)).laneIssue(form.get('secret'),workspace,lane);}
  catch{issued={status:503};}
  if(issued.status!==200){
    await stub.release();
    if(issued.status===403)return laneRefused(code,lane,workspace);
    return reply(503,{error:'login_unavailable'});
  }
  const placed=await stub.deliverLane(issued.body.bearer,name,issued.body.action);
  return placed.status===200?laneDone():unusable(410);
}
