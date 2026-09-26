// ブラウザ式の `thth login`（設計 3.14.2 §2）。貼る作業を無くす（`gh auth login` と同じ形）。
//
//   CLI:  POST /api/v1/login/start {code_hash, poll_token_hash}         → 202 {url, expires_at}
//         （url は `<origin>/login/`。CLI が手元の code を足して開く。Worker は code を受け取らない）
//         GET  /api/v1/login/poll/<code>  Authorization: Bearer <poll_token>
//              → 202 {status: waiting} / 200 {key, account}（1 度だけ） / 410 / 404 / 401
//   人:   GET  /login/<code>  → 口座名と口座の secret の form（「この機械に鍵を渡す / Allow this device」）
//         POST /login/<code>  → Person DO で secret を照合 → rotate と同じ鍵の発行 → Login DO に 1 度だけ置く
//
// - 鍵は画面に出さない・記録しない。Login DO に最長 10 分、CLI が受け取った時点で消す。
// - 作法は /activity と同じ（script なし・CSP・no-store・no-referrer・同一 origin の POST）。
// - 回数制限: /login/* と start は RELAY_PUBLIC_LIMIT（IP）。poll は IP に加えて API_KEY_LIMIT を code ごと（60 回／分）。
import {PERSON,boundedBody,personStub,fromSameOrigin,page,en,escape,publicQuota} from './person.js';
import {HASH_PATTERN,digest,reply} from './relay.js';

// 大文字と数字から紛らわしい字（0 O 1 I L）を除いた 31 字。表示は XXXX-XXXX。
export const CODE_ALPHABET='ABCDEFGHJKMNPQRSTUVWXYZ23456789';
export const LOGIN_CODE=/^[A-HJKMNP-Z2-9]{4}-[A-HJKMNP-Z2-9]{4}$/;
const POLL_BEARER=/^Bearer ([A-Za-z0-9_-]{43})$/;
const TITLE='THTH この機械に鍵を渡す';

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
      if(body===null||typeof body!=='object'||Array.isArray(body)||Object.keys(body).sort().join(',')!=='code_hash,poll_token_hash'||
         typeof body.code_hash!=='string'||!HASH_PATTERN.test(body.code_hash)||
         typeof body.poll_token_hash!=='string'||!HASH_PATTERN.test(body.poll_token_hash))return reply(400,{error:'invalid_request'});
      const started=await loginStub(env,body.code_hash).start(body.poll_token_hash);
      if(started.status!==200)return reply(started.status,started.body);
      return reply(202,{url:url.origin+'/login/',expires_at:new Date(started.body.expires_at).toISOString()});
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
    return reply(got.status,got.body);
  }catch{return reply(503,{error:'login_unavailable'});}
}

function allowForm(code,status=200,note=''){
  return page(status,`<h1>この機械に鍵を渡す${en('Allow this device')}</h1>${note}
<p>ターミナルで <code>thth login</code> を打ったのがあなた自身なら、口座名と口座の secret を入れて許可してください。許可すると、その機械に LLM の鍵が渡ります（前の鍵は使えなくなります）。自分で打っていないなら、このページを閉じてください。${en('If you ran <code>thth login</code> in your own terminal, enter your account name and account secret to allow it. The device receives a key for your LLM (the previous key stops working). If you did not run it yourself, close this page.')}</p>
<p>コード / Code: <strong>${escape(code)}</strong>（ターミナルに出たものと同じか確かめてください${en('Check that it matches the code in your terminal.')}）</p>
<form method="post" action="/login/${escape(code)}"><label>口座名 / Account name <input name="account" autocomplete="username" required maxlength="64"></label><label>口座の secret / Account secret <input type="password" name="secret" autocomplete="current-password" required maxlength="128"></label><button type="submit">この機械に鍵を渡す / Allow this device</button></form>
<p>このページは 10 分で使えなくなります。鍵はこの画面には出ません。${en('This page stops working after 10 minutes. The key is never shown on this screen.')}</p>`,TITLE);
}
const unusable=(status=410)=>page(status,`<h1>このページは使えません${en('This page cannot be used')}</h1><p>期限（10 分）が切れたか、もう使われました。ターミナルで <code>thth login</code> をやり直してください。${en('It has expired (10 minutes) or has already been used. Run <code>thth login</code> again in your terminal.')}</p>`,TITLE);
const busy=()=>page(409,`<h1>確かめている途中です${en('Still checking')}</h1><p>少し待ってから、このページを開き直してください。${en('Wait a moment and reload this page.')}</p>`,TITLE);
const refused=code=>allowForm(code,403,`<p><strong>確かめられませんでした。</strong>口座名と口座の secret を確かめてください。5 回続けて間違えると 15 分閉じます。${en('Could not confirm. Check the account name and the account secret. After five failures in a row it is closed for 15 minutes.')}</p>`);
const notReady=code=>allowForm(code,409,`<p><strong>口座の様子がまだサーバから届いていないか、口座が 1 つに決められません。</strong>口座名を確かめ、数十秒後にもう一度押してください。${en('The server has not reported this account yet, or the account could not be determined. Check the account name and try again in a few tens of seconds.')}</p>`);
const done=()=>page(200,`<h1>渡しました。ターミナルに戻ってください${en('Done. Go back to your terminal')}</h1><p>鍵はターミナルが受け取り、この画面には出ません。鍵はサーバが切り替えた時点（数十秒後）から使え、前の鍵は使えなくなります。このページは閉じて構いません。${en('The terminal receives the key; it is never shown here. It works once the server switches to it (in a few tens of seconds), and the previous key stops working. You can close this page.')}</p>`,TITLE);

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
      return state.status===200?allowForm(code):unusable(state.status===404?404:410);
    }
    if(!fromSameOrigin(request,url))return reply(403,{error:'forbidden'});
    const form=new URLSearchParams(await boundedBody(request,2048));
    if([...form.keys()].sort().join(',')!=='account,secret')return reply(400,{error:'invalid_request'});
    const name=form.get('account');
    // 口座名の形が違えば照合に進まない（Login の状態も変えない）。
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
      if(issued.status===404||issued.status===409)return notReady(code);
      return reply(503,{error:'login_unavailable'});
    }
    const placed=await stub.deliver(issued.body.bearer,issued.body.account);
    return placed.status===200?done():unusable(410);
  }catch{return reply(503,{error:'login_unavailable'});}
}
