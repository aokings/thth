// 招待のページ（設計 3.10.0）。作法は 3.12.0 までの承認ページと同じ: script なし・CSP・no-store・
// no-referrer・同一 origin の POST は Sec-Fetch-Site で確かめる。
// 審査員が読むので、日本語の下に英語を 1 行ずつ添える。
import {digest,reply} from './relay.js';
import {boundedBody,inviteStub,PAGE_STYLE} from './person.js';

const escape=s=>String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const en=text=>`<span class="en">${text}</span>`;

export function invitePage(status,body,refresh=null){
  const meta=refresh?`<meta http-equiv="refresh" content="${Number(refresh)}">`:'';
  return new Response('<!doctype html><html lang="ja"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex,nofollow,noarchive">'+meta+'<title>THTH 招待</title><style>'+PAGE_STYLE+'</style><body>'+body+'</body></html>',{status,headers:{
    'content-type':'text/html; charset=utf-8','cache-control':'no-store','referrer-policy':'no-referrer',
    'content-security-policy':"default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'",
    'x-content-type-options':'nosniff','x-frame-options':'DENY','x-robots-tag':'noindex, nofollow'}});
}

const REASON={
  auth_expired:['前回の認可は 10 分の期限を過ぎました。もう一度押してください。','The previous authorization expired after 10 minutes. Please try again.'],
  auth_failed:['前回の認可を完了できませんでした。もう一度押してください。続くときは<a href="/privacy/">プライバシーポリシーの Contact</a>から運営者に連絡してください。','The previous authorization could not be completed. Please try again. If it continues, contact the operator through <a href="/privacy/">Contact in the Privacy Policy</a>.'],
  account_exists:['その Threads アカウントは既に THTH にあります。別のアカウントで認可するか、<a href="/privacy/">プライバシーポリシーの Contact</a>から運営者に連絡してください。','That Threads account is already registered. Use another account or contact the operator through <a href="/privacy/">Contact in the Privacy Policy</a>.'],
  unavailable:['運営者のサーバーが今はアカウントを用意できません。<a href="/privacy/">プライバシーポリシーの Contact</a>から運営者に連絡してください。','The operator’s server cannot prepare the account right now. Contact the operator through <a href="/privacy/">Contact in the Privacy Policy</a>.'],
};
const unusable=()=>invitePage(410,`<h1>この招待リンクは使えません</h1><p class="err" data-error="410">使用済み・期限切れ・取り消しのいずれかです。<a href="/privacy/">プライバシーポリシーの Contact</a>から運営者に新しい招待を頼んでください。${en('This invitation link is no longer valid (used, expired or revoked). Ask the operator for a new one through <a href="/privacy/">Contact in the Privacy Policy</a>.')}</p>`);
const day=ms=>new Date(ms).toISOString().slice(0,10);

function openPage(d){
  const reason=REASON[d.reason]?`<p><strong>${REASON[d.reason][0]}</strong>${en(REASON[d.reason][1])}</p>`:'';
  const mode=d.production
    ?`<p>この招待で用意するアカウントは、頼まれたときに本当に Threads へ投稿します。${en('The account prepared by this invitation publishes to Threads for real when asked.')}</p>`
    :`<p>この招待で用意するアカウントはテスト用です（頼まれても Threads には出しません）。${en('The account prepared by this invitation is a dry run (nothing is published to Threads).')}</p>`;
  return invitePage(200,`<h1>THTH への招待${en('Invitation to THTH')}</h1>
<p>THTH は、SNS を調べ・投稿し・予約する CLI ツールです。あなたの CLI か AI アシスタントが代わりに打ちます。${en('THTH is a CLI tool to research, post and schedule on social media. Your CLI or AI assistant runs it for you.')}</p>${reason}
<h2>何が起きるか${en('What happens')}</h2><ol>
<li>下のボタンを押すと、運営者のサーバーが Threads の認可ページを用意します（数十秒）。${en('After you press the button, the operator’s server prepares the Threads authorization page (a few tens of seconds).')}</li>
<li>あなたの Threads アカウントで認可すると、そのアカウントのアカウントが運営者のサーバーに用意されます。トークンはこのページを通りません。${en('When you authorize with your Threads account, an account is prepared on the operator’s server. The token never passes through this page.')}</li>
<li>完了のページでアカウントのシークレット とAPI キーが一度だけ表示されます。パスワード管理に別々に保存してください。${en('The completion page shows your account secret and assistant key once. Save them separately in a password manager.')}</li>
<li>投稿・返信・削除はあなたの CLI か AI アシスタントが頼んだときに行われます（ガードは /activity で）。${en('Posts, replies and deletions happen when your CLI or AI assistant asks for them (safety limits are on /activity).')}</li></ol>
<h2>求める権限${en('Permissions requested')}</h2><ul>${d.scopes.map(s=>`<li><code>${escape(s)}</code></li>`).join('')}</ul>
${mode}<p>期限: ${escape(day(d.expires_at))}（UTC）まで。1 回だけ使えます。${en('Valid until '+escape(day(d.expires_at))+' (UTC). Can be used once.')}</p>
<p><a href="/privacy/">プライバシー / Privacy</a> · <a href="/terms/">利用規約 / Terms</a></p>
<form method="post"><input type="hidden" name="csrf" value="${escape(d.csrf)}"><input type="hidden" name="action" value="start"><button type="submit">Threads で認可する / Authorize with Threads</button></form>`);
}

function renderView(d){
  if(d.status==='open')return openPage(d);
  if(d.status==='clicked'){
    // 常駐が 2 分拾わなければ、待たせ続けずに運営者へ連絡を頼む（常駐が戻れば進む）。
    if(d.stalled)return invitePage(200,`<h1>準備が進んでいません${en('Preparation has not started')}</h1><p class="status">運営者のサーバーが止まっている可能性があります。<a href="/privacy/">プライバシーポリシーの Contact</a>から運営者に連絡してください。このページは開いたままで構いません。戻れば自動で進みます。${en('The operator’s server may be down. Contact the operator through <a href="/privacy/">Contact in the Privacy Policy</a>. You may keep this page open; it continues automatically once it returns.')}</p>`,15);
    return invitePage(200,`<h1>認可の準備中です${en('Preparing authorization')}</h1><p class="status">数十秒で次へ進みます。${en('This page continues in a few tens of seconds.')}</p>`,3);
  }
  if(d.status==='authorizing'){
    if(!d.authorize_url)return invitePage(200,`<h1>認可の時間が過ぎました${en('Authorization window closed')}</h1><p>10 分の期限を過ぎました。まもなくもう一度押せるようになります。${en('The 10-minute window passed. You can start again shortly.')}</p>`,5);
    return invitePage(200,`<h1>Threads で認可してください${en('Authorize with Threads')}</h1>
<p><a class="go" href="${escape(d.authorize_url)}" target="_blank" rel="noopener noreferrer">Threads の認可ページを開く / Open Threads authorization</a></p>
<p class="status">新しいタブで開きます。認可が済んだら、このタブに戻ってください。自動で進みます（10 分以内）。${en('It opens in a new tab. After authorizing, come back to this tab; it continues automatically (within 10 minutes).')}</p>`,5);
  }
  if(d.status==='ready'){
    return invitePage(200,`<h1>用意ができました${en('Your account is ready')}</h1>
<p class="status">Threads: @${escape(d.handle)} ／ アカウント / account: <code>${escape(d.account)}</code></p>
<p>次のボタンでアカウントのシークレット とAPI キーを表示します。<strong class="once">表示は一度だけです。</strong>その場でパスワード管理に別々に保存してください。${en('The next button shows your account secret and assistant key. <strong class="once">They are shown only once.</strong> Save them separately in a password manager right away.')}</p>
<form method="post"><input type="hidden" name="csrf" value="${escape(d.csrf)}"><input type="hidden" name="action" value="reveal"><button type="submit">アカウントのシークレットを表示する / Show account secret</button></form>`);
  }
  if(d.status==='done')return invitePage(410,`<h1>この招待は使用済みです${en('This invitation has been used')}</h1><p class="err" data-error="410">アカウントのシークレット はすでに表示しました。<strong class="once">表示は一度だけです。</strong>失くした場合は<a href="/privacy/">プライバシーポリシーの Contact</a>から運営者に再発行を頼んでください。${en('The account secret was already shown. <strong class="once">It is shown only once.</strong> If you lost it, ask the operator to issue a new one through <a href="/privacy/">Contact in the Privacy Policy</a>.')}</p>`);
  return unusable();
}

export function secretPage(result){
  return invitePage(200,`<h1>アカウントのシークレット${en('Account secret')}</h1>
<p><strong class="once">この表示は一度だけです。</strong>いまパスワード管理に保存してください。${en('<strong class="once">This is shown only once.</strong> Save it in a password manager now.')}</p>
<code class="secret">${escape(result.secret)}</code>
<ul><li>ユーザー名 / username: <code>${escape(result.person)}</code></li><li>Web サイト / website: <code>thth.me</code></li></ul>
<!-- 3.12.0 §6-4: アカウント名と secret を同じ form に置き、パスワード管理が thth.me の正しい組として覚えるようにする。
     欄の名前はアクティビティの入口（/activity）と同じなので、押すとそのままアクティビティに入る（3.13.0）。 -->
<form method="post" action="/activity"><label>ユーザー名 / Username <input name="person" autocomplete="username" value="${escape(result.person)}" readonly></label><label>アカウントのシークレット / Account secret <input type="password" name="secret" autocomplete="new-password" value="${escape(result.secret)}" readonly></label><button type="submit">保存してアクティビティを開く / Save and open your activity</button></form>
<p>ボタンを押すとブラウザやパスワード管理が「保存しますか」と尋ねます。ユーザー名が上のアカウント名になっているのを確かめて保存してください。${en('When you press the button, your browser or password manager offers to save. Check that the username is the account name above, then save.')}</p>
<p>アクティビティは <a href="/activity">https://thth.me/activity</a> で、このユーザー名と secret を入れると見られます。ここでアカウントを止める・戻す、予約を取り消す、ガードの数値を変える、LLM のキーを発行・取り消しできます。secret は LLM や原稿に書かないでください。${en('Open <a href="/activity">https://thth.me/activity</a> and sign in with this username and secret. There you can stop or resume the account, cancel scheduled posts, change safety limits, and issue or revoke the key for your LLM. Never paste the secret into an LLM or a draft.')}</p>
${result.key?`<h2>API キー${en('Assistant key')}</h2>
<p><strong class="once">表示は一度だけです。</strong>通常はターミナルで <code>thth login</code> を実行し、ブラウザでアカウント名とアカウントのシークレットを入力してこのデバイスを許可します。ブラウザを使えない場合は、ここに表示したキーを <code>thth login --stdin</code> で入力できます。パスワード管理にはアカウントのシークレット と別の項目で保存してください。${en('<strong class="once">It is shown only once.</strong> Normally, run <code>thth login</code> in a terminal and enter the account name and account secret in the browser to allow this device. If a browser is not available, enter this key with <code>thth login --stdin</code>. Save it in your password manager as a separate item from the account secret.')}</p>
<code class="secret key">${escape(result.key)}</code>
<p>サーバーが受け取った時点（数十秒後）から使えます。キーは LLM・原稿・公開の場に貼らないでください。発行し直し・取り消しはアクティビティでできます。${en('It works within a few tens of seconds, once the server has taken it. Never paste the key into an LLM, a draft or anywhere public. You can re-issue or revoke it on your activity page.')}</p>`:''}`);
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
      return invitePage(503,`<h1>いまは表示できません${en('Cannot show it right now')}</h1><p>少し待ってからもう一度押してください。${en('Please wait a moment and try again.')}</p>`);
    }
    return reply(400,{error:'invalid_request'});
  }catch{return reply(503,{error:'invite_unavailable'});}
}
