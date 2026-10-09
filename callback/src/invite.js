// 招待のページ（設計 3.10.0）。作法は 3.12.0 までの承認ページと同じ: script なし・CSP・no-store・
// no-referrer・同一 origin の POST は Sec-Fetch-Site で確かめる。
// 言葉は日本語と英語の両方を入れ、画面の上で切り替える（2026-10-10・審査員は English で読める）。
// 2026-10-10 masaru: 完了のページで API キーを見せない。キーは `thth login` だけで受け取る
// （ブラウザが使えない機械は /activity で発行して `thth login --stdin`）。
import {digest,reply} from './relay.js';
import {boundedBody,inviteStub,frame,t,steps,escape} from './person.js';

export function invitePage(status,body,refresh=null){
  const meta=refresh?`<meta http-equiv="refresh" content="${Number(refresh)}">`:'';
  return new Response(frame(body,'THTH 招待',meta),{status,headers:{
    'content-type':'text/html; charset=utf-8','cache-control':'no-store','referrer-policy':'no-referrer',
    'content-security-policy':"default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'",
    'x-content-type-options':'nosniff','x-frame-options':'DENY','x-robots-tag':'noindex, nofollow'}});
}

const contact=t('<a href="/privacy/">プライバシーポリシーの Contact</a>から運営者に連絡してください。','Contact the operator through <a href="/privacy/">Contact in the Privacy Policy</a>.');
const REASON={
  auth_expired:t('前回の認可は 10 分の期限を過ぎました。もう一度押してください。','The previous authorization expired after 10 minutes. Please try again.'),
  auth_failed:t('前回の認可を完了できませんでした。もう一度押してください。続くときは','The previous authorization could not be completed. Please try again. If it continues: ')+contact,
  account_exists:t('その Threads アカウントは既に THTH にあります。別のアカウントで認可するか、','That Threads account is already registered. Use another account, or: ')+contact,
  unavailable:t('運営者のサーバーが今はアカウントを用意できません。','The operator’s server cannot prepare the account right now. ')+contact,
};
const unusable=()=>invitePage(410,`<h1>${t('この招待リンクは使えません','This invitation link is no longer valid')}</h1><p class="err" data-error="410">${t('使用済み・期限切れ・取り消しのいずれかです。<a href="/privacy/">プライバシーポリシーの Contact</a>から運営者に新しい招待を頼んでください。','It has been used, has expired, or was revoked. Ask the operator for a new one through <a href="/privacy/">Contact in the Privacy Policy</a>.')}</p>`);
const day=ms=>new Date(ms).toISOString().slice(0,10);

function openPage(d){
  const reason=REASON[d.reason]?`<p class="err">${REASON[d.reason]}</p>`:'';
  const mode=d.production
    ?t('このアカウントは、頼まれたときに本当に Threads へ投稿します。','This account publishes to Threads for real when asked.')
    :t('このアカウントはテスト用です（頼まれても Threads には出しません）。','This account is a dry run (nothing is published to Threads).');
  return invitePage(200,`${steps(1)}<h1>${t('THTH への招待','You’re invited to THTH')}</h1>
<p class="lead">${t('THTH は、SNS を調べ・投稿し・予約する CLI ツールです。あなたの CLI か AI アシスタントが代わりに打ちます。','THTH is a CLI tool to research, post and schedule on social media. Your CLI or AI assistant runs it for you.')}</p>${reason}
<h2>${t('流れ（3 段・数分）','How it works (3 steps, a few minutes)')}</h2><ol>
<li>${t('Threads で認可します。トークンはこのページを通りません。','Authorize with Threads. The token never passes through this page.')}</li>
<li>${t('アカウントのシークレットが 1 度だけ出ます。パスワード管理に保存します。','Your account secret is shown once. Save it in a password manager.')}</li>
<li>${t('ターミナルで <code>thth login</code> を打ち、ブラウザで許可します。','Run <code>thth login</code> in a terminal and allow it in the browser.')}</li></ol>
<p>${t('投稿・返信・削除は、あなたの CLI か AI アシスタントが頼んだときだけ行います。止める・上限を変えるのは /activity で。','Posts, replies and deletions happen only when your CLI or AI assistant asks. You can stop the account or change limits on /activity.')}</p>
<form method="post"><input type="hidden" name="csrf" value="${escape(d.csrf)}"><input type="hidden" name="action" value="start"><button type="submit">${t('Threads で認可する','Authorize with Threads')}</button></form>
<h2>${t('この招待について','About this invitation')}</h2>
<dl class="facts"><dt>${t('種類','Mode')}</dt><dd>${mode}</dd><dt>${t('期限','Valid until')}</dt><dd>${escape(day(d.expires_at))}（UTC）${t('・1 回だけ',' · single use')}</dd>
<dt>${t('求める権限','Permissions')}</dt><dd>${d.scopes.map(s=>`<code>${escape(s)}</code>`).join(' ')}</dd></dl>`);
}

function renderView(d){
  if(d.status==='open')return openPage(d);
  if(d.status==='clicked'){
    // 常駐が 2 分拾わなければ、待たせ続けずに運営者へ連絡を頼む（常駐が戻れば進む）。
    if(d.stalled)return invitePage(200,`${steps(1)}<h1>${t('準備が進んでいません','Preparation has not started')}</h1><p class="status">${t('運営者のサーバーが止まっている可能性があります。このページは開いたままで構いません。戻れば自動で進みます。','The operator’s server may be down. You may keep this page open; it continues automatically once the server is back.')}</p><p>${contact}</p>`,15);
    return invitePage(200,`${steps(1)}<h1>${t('認可の準備中です','Preparing authorization')}</h1><p class="status">${t('数十秒で次へ進みます。このままお待ちください。','This page continues in a few tens of seconds.')}</p>`,3);
  }
  if(d.status==='authorizing'){
    if(!d.authorize_url)return invitePage(200,`${steps(1)}<h1>${t('認可の時間が過ぎました','Authorization window closed')}</h1><p>${t('10 分の期限を過ぎました。まもなくもう一度押せるようになります。','The 10-minute window passed. You can start again shortly.')}</p>`,5);
    return invitePage(200,`${steps(1)}<h1>${t('Threads で認可してください','Authorize with Threads')}</h1>
<p>${t('ボタンで Threads の認可ページが<strong>新しいタブ</strong>で開きます。許可したら、<strong>このタブに戻ってください</strong>。自動で次へ進みます（10 分以内）。','The button opens Threads in a <strong>new tab</strong>. After you allow it, <strong>come back to this tab</strong>; it continues automatically (within 10 minutes).')}</p>
<p><a class="go" href="${escape(d.authorize_url)}" target="_blank" rel="noopener noreferrer">${t('Threads の認可ページを開く','Open Threads authorization')}</a></p>
<p class="status">${t('認可を待っています…','Waiting for your authorization…')}</p>`,5);
  }
  if(d.status==='ready'){
    return invitePage(200,`${steps(2)}<h1>${t('用意ができました','Your account is ready')}</h1>
<dl class="facts"><dt>Threads</dt><dd>@${escape(d.handle)}</dd><dt>${t('アカウント名','Account name')}</dt><dd><code>${escape(d.account)}</code></dd></dl>
<p>${t('次のボタンで<strong>アカウントのシークレット</strong>を出します。<strong class="once">出るのは 1 度だけです。</strong>パスワード管理を開いてから押してください。','The next button shows your <strong>account secret</strong>. <strong class="once">It is shown only once.</strong> Open your password manager before you press it.')}</p>
<form method="post"><input type="hidden" name="csrf" value="${escape(d.csrf)}"><input type="hidden" name="action" value="reveal"><button type="submit">${t('シークレットを表示する','Show the account secret')}</button></form>`);
  }
  if(d.status==='done')return invitePage(410,`<h1>${t('この招待は使用済みです','This invitation has been used')}</h1><p class="err" data-error="410">${t('アカウントのシークレットはすでに表示しました（1 度だけ）。失くした場合は<a href="/privacy/">プライバシーポリシーの Contact</a>から運営者に再発行を頼んでください。','The account secret was already shown (only once). If you lost it, ask the operator to issue a new one through <a href="/privacy/">Contact in the Privacy Policy</a>.')}</p>
<p>${t('保存してあれば、ターミナルで <code>thth login</code> から続けられます。','If you saved it, continue with <code>thth login</code> in a terminal.')}</p>`);
  return unusable();
}

export function secretPage(result){
  return invitePage(200,`${steps(2)}<h1>${t('シークレットを保存してください','Save your account secret')}</h1>
<p><strong class="once">${t('この表示は 1 度だけです。','This is shown only once.')}</strong>${t('いまパスワード管理に保存してください。','Save it in your password manager now.')}</p>
<dl class="facts"><dt>${t('アカウント名','Account name')}</dt><dd><code>${escape(result.person)}</code></dd><dt>${t('サイト','Website')}</dt><dd><code>thth.me</code></dd></dl>
<code class="secret">${escape(result.secret)}</code>
<!-- 3.12.0 §6-4: アカウント名と secret を同じ form に置き、パスワード管理が thth.me の正しい組として覚えるようにする。
     欄の名前はアクティビティの入口（/activity）と同じなので、押すとそのままアクティビティに入る（3.13.0）。 -->
<form method="post" action="/activity"><label>${t('アカウント名','Account name')} <input name="person" autocomplete="username" value="${escape(result.person)}" readonly></label><label>${t('アカウントのシークレット','Account secret')} <input type="password" name="secret" autocomplete="new-password" value="${escape(result.secret)}" readonly></label><button type="submit">${t('保存してアクティビティを開く','Save and open your activity')}</button></form>
<p class="note">${t('押すとブラウザかパスワード管理が「保存しますか」と尋ねます。ユーザー名がアカウント名になっているのを確かめて保存してください。シークレットは LLM や原稿に書かないでください。','Your browser or password manager will offer to save it. Check that the username is the account name, then save. Never paste the secret into an LLM or a draft.')}</p>
<h2>${t('最後: ターミナルで thth login','Last step: thth login in a terminal')}</h2>
<pre>pip install thth
thth login</pre>
<p>${t('ブラウザが開いたら、アカウント名とシークレットを入れて許可します。API キーはターミナルが受け取ります（画面には出ません）。','A browser page opens: enter the account name and secret and allow it. The terminal receives the API key; it is never shown on screen.')}</p>
<p class="note">${t('ブラウザが使えない機械では、<a href="/activity">アクティビティ</a>で API キーを発行し、<code>thth login --stdin</code> で入れます。','On a machine without a browser, issue an API key on <a href="/activity">your activity page</a> and enter it with <code>thth login --stdin</code>.')}</p>`);
}

export async function inviteRequest(request,env,url){
  try{
    if(url.search||url.hash||url.pathname.includes('%'))return reply(400,{error:'invalid_request'});
    const match=/^\/invite\/([A-Za-z0-9_-]{43})$/.exec(url.pathname);
    if(!match)return reply(404,{error:'not_found'});
    if(!env.INVITE_OBJECT)return reply(503,{error:'invite_unavailable'});
    if(!env.RELAY_PUBLIC_LIMIT||!(await env.RELAY_PUBLIC_LIMIT.limit({key:await digest(request.headers.get('cf-connecting-ip')||'unknown-peer')})).success)return reply(429,{error:'rate_limited'});
    if(!['GET','POST'].includes(request.method))return reply(405,{error:'method_not_allowed'});
    const stub=inviteStub(env,await digest(match[1]));
    if(request.method==='GET')return renderView(await stub.view());
    // 3.12.0 までの承認ページと同じ: no-referrer では同一 origin の form POST でも Origin が null か無し。
    // そのときは Sec-Fetch-Site で同一 origin を確かめる。csrf と form-action 'self' も効く。
    const originHeader=request.headers.get('origin'),site=request.headers.get('sec-fetch-site');
    const sameOrigin=originHeader===url.origin||((originHeader===null||originHeader==='null')&&(site===null||site==='same-origin'));
    if(!sameOrigin||!/^application\/x-www-form-urlencoded(?:\s*;|$)/i.test(request.headers.get('content-type')||''))return reply(403,{error:'forbidden'});
    const form=new URLSearchParams(await boundedBody(request,1024));
    if([...form.keys()].sort().join(',')!=='action,csrf')return reply(400,{error:'invalid_request'});
    if(form.get('action')==='start'){
      const result=await stub.start(form.get('csrf'));
      if(result.status===200)return new Response(null,{status:303,headers:{location:url.pathname,'cache-control':'no-store','referrer-policy':'no-referrer'}});
      return result.status===403?reply(403,{error:'forbidden'}):unusable();
    }
    if(form.get('action')==='reveal'){
      const result=await stub.reveal(form.get('csrf'));
      if(result.status===200)return secretPage(result.body);
      if(result.status===403)return reply(403,{error:'forbidden'});
      if(result.status===410)return renderView(await stub.view());
      return invitePage(503,`<h1>${t('いまは表示できません','Cannot show it right now')}</h1><p>${t('少し待ってからもう一度押してください。','Please wait a moment and try again.')}</p>`);
    }
    return reply(400,{error:'invalid_request'});
  }catch{return reply(503,{error:'invite_unavailable'});}
}
