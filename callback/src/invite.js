// 招待のページ（設計 3.10.0）。作法は承認ページと同じ: script なし・CSP・no-store・
// no-referrer・同一 origin の POST は Sec-Fetch-Site で確かめる。
// 審査員が読むので、日本語の下に英語を 1 行ずつ添える。
import {digest,reply} from './relay.js';
import {boundedBody,inviteStub} from './approval.js';

const escape=s=>String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const en=text=>`<span class="en">${text}</span>`;

export function invitePage(status,body,refresh=null){
  const meta=refresh?`<meta http-equiv="refresh" content="${Number(refresh)}">`:'';
  return new Response('<!doctype html><html lang="ja"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex,nofollow,noarchive">'+meta+'<title>THTH 招待</title><style>body{overflow-wrap:anywhere;max-width:44rem;margin:2rem auto;padding:0 1rem;font:1rem/1.7 system-ui}.en{display:block;font-size:.88rem;opacity:.75}li{margin:.4rem 0}code,.secret{font-family:ui-monospace,Menlo,monospace}.secret{display:block;font-size:1.05rem;border:1px solid;padding:1rem;user-select:all;word-break:break-all}button{display:block;margin:1rem 0;padding:.6rem 1.4rem;font:inherit}a.go{display:inline-block;margin:1rem 0;padding:.6rem 1.4rem;border:1px solid;text-decoration:none}</style><body>'+body+'</body></html>',{status,headers:{
    'content-type':'text/html; charset=utf-8','cache-control':'no-store','referrer-policy':'no-referrer',
    'content-security-policy':"default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'",
    'x-content-type-options':'nosniff','x-frame-options':'DENY','x-robots-tag':'noindex, nofollow'}});
}

const REASON={
  auth_expired:['前回の認可は 10 分の期限を過ぎました。もう一度押してください。','The previous authorization expired after 10 minutes. Please try again.'],
  auth_failed:['前回の認可を完了できませんでした。もう一度押してください。続くときは運営者に連絡してください。','The previous authorization could not be completed. Please try again, or contact the operator.'],
  account_exists:['その Threads アカウントは既に THTH にあります。別のアカウントで認可するか、運営者に連絡してください。','That Threads account is already registered. Use another account or contact the operator.'],
  unavailable:['運営者のサーバが今は口座を用意できません。運営者に連絡してください。','The operator’s server cannot prepare the account right now. Please contact the operator.'],
};
const unusable=()=>invitePage(410,`<h1>この招待リンクは使えません</h1><p>使用済み・期限切れ・取り消しのいずれかです。運営者に新しい招待を頼んでください。${en('This invitation link is no longer valid (used, expired or revoked). Please ask the operator for a new one.')}</p>`);
const day=ms=>new Date(ms).toISOString().slice(0,10);

function openPage(d){
  const reason=REASON[d.reason]?`<p><strong>${REASON[d.reason][0]}</strong>${en(REASON[d.reason][1])}</p>`:'';
  const mode=d.production
    ?`<p>この招待で用意する口座は、承認したときに本当に Threads へ投稿します。${en('The account prepared by this invitation publishes to Threads for real, only when you approve.')}</p>`
    :`<p>この招待で用意する口座は試し撃ちです（承認しても Threads には出しません）。${en('The account prepared by this invitation is a dry run (nothing is published to Threads).')}</p>`;
  return invitePage(200,`<h1>THTH への招待${en('Invitation to THTH')}</h1>
<p>THTH は、LLM が下書きした SNS の投稿を、人が承認したときだけ公開する道具です。${en('THTH publishes social posts drafted by an LLM only when a person approves them.')}</p>${reason}
<h2>何が起きるか${en('What happens')}</h2><ol>
<li>下のボタンを押すと、運営者のサーバが Threads の認可ページを用意します（数十秒）。${en('After you press the button, the operator’s server prepares the Threads authorization page (a few seconds).')}</li>
<li>あなたの Threads アカウントで認可すると、そのアカウントの口座が運営者のサーバに用意されます。トークンはこのページを通りません。${en('When you authorize with your Threads account, an account is prepared on the operator’s server. The token never passes through this page.')}</li>
<li>完了のページで承認 secret が一度だけ表示されます。パスワード管理に保存してください。${en('The completion page shows your approval secret once. Save it in a password manager.')}</li>
<li>投稿・返信・削除は、承認ページであなたが secret を入れて押したときだけ行われます。${en('Posts, replies and deletions happen only when you enter the secret and approve on an approval page.')}</li></ol>
<h2>求める権限${en('Permissions requested')}</h2><ul>${d.scopes.map(s=>`<li><code>${escape(s)}</code></li>`).join('')}</ul>
${mode}<p>期限: ${escape(day(d.expires_at))}（UTC）まで。1 回だけ使えます。${en('Valid until '+escape(day(d.expires_at))+' (UTC). Can be used once.')}</p>
<p><a href="/privacy/">プライバシー / Privacy</a> · <a href="/terms/">利用規約 / Terms</a></p>
<form method="post"><input type="hidden" name="csrf" value="${escape(d.csrf)}"><input type="hidden" name="action" value="start"><button type="submit">Threads で認可する / Authorize with Threads</button></form>`);
}

function renderView(d){
  if(d.status==='open')return openPage(d);
  if(d.status==='clicked'){
    // 常駐が 2 分拾わなければ、待たせ続けずに運営者へ連絡を頼む（常駐が戻れば進む）。
    if(d.stalled)return invitePage(200,`<h1>準備が進んでいません${en('Preparation has not started')}</h1><p>運営者のサーバが止まっている可能性があります。運営者に連絡してください。このページは開いたままで構いません（戻れば自動で進みます）。${en('The operator’s server may be down. Please contact the operator. You may keep this page open; it continues automatically.')}</p>`,15);
    return invitePage(200,`<h1>認可の準備中です${en('Preparing authorization')}</h1><p>数十秒で次へ進みます。${en('This page continues in a few seconds.')}</p>`,3);
  }
  if(d.status==='authorizing'){
    if(!d.authorize_url)return invitePage(200,`<h1>認可の時間が過ぎました${en('Authorization window closed')}</h1><p>10 分の期限を過ぎました。まもなくもう一度押せるようになります。${en('The 10-minute window passed. You can start again shortly.')}</p>`,5);
    return invitePage(200,`<h1>Threads で認可してください${en('Authorize with Threads')}</h1>
<p><a class="go" href="${escape(d.authorize_url)}" target="_blank" rel="noopener noreferrer">Threads の認可ページを開く / Open Threads authorization</a></p>
<p>新しいタブで開きます。認可が済んだら、このタブに戻ってください。自動で進みます（10 分以内）。${en('It opens in a new tab. After authorizing, come back to this tab; it continues automatically (within 10 minutes).')}</p>`,5);
  }
  if(d.status==='ready'){
    return invitePage(200,`<h1>用意ができました${en('Your account is ready')}</h1>
<p>Threads: @${escape(d.handle)} ／ 口座 / account: <code>${escape(d.account)}</code></p>
<p>次のボタンで承認 secret を表示します。<strong>表示は一度だけです。</strong>その場でパスワード管理に保存してください。${en('The next button shows your approval secret. <strong>It is shown only once.</strong> Save it in a password manager right away.')}</p>
<form method="post"><input type="hidden" name="csrf" value="${escape(d.csrf)}"><input type="hidden" name="action" value="reveal"><button type="submit">承認 secret を表示する / Show approval secret</button></form>`);
  }
  if(d.status==='done')return invitePage(410,`<h1>この招待は使用済みです${en('This invitation has been used')}</h1><p>承認 secret はすでに表示しました（表示は一度だけです）。失くした場合は運営者に再発行を頼んでください。${en('The approval secret was already shown (only once). If you lost it, ask the operator to issue a new one.')}</p>`);
  return unusable();
}

export function secretPage(result){
  return invitePage(200,`<h1>承認 secret${en('Approval secret')}</h1>
<p><strong>この表示は一度だけです。</strong>いまパスワード管理に保存してください。${en('<strong>This is shown only once.</strong> Save it in a password manager now.')}</p>
<code class="secret">${escape(result.secret)}</code>
<ul><li>ユーザ名 / username: <code>${escape(result.person)}</code></li><li>Web サイト / website: <code>thth.me</code></li></ul>
<!-- 3.12.0 §6-4: 口座名と secret を同じ form に置き、パスワード管理が thth.me の正しい組として覚えるようにする。
     欄の名前は一覧の入口（/pending）と同じなので、押すとそのまま一覧に入る。 -->
<form method="post" action="/pending"><label>ユーザ名 / Username <input name="person" autocomplete="username" value="${escape(result.person)}" readonly></label><label>承認 secret / Approval secret <input type="password" name="secret" autocomplete="new-password" value="${escape(result.secret)}" readonly></label><button type="submit">保存して承認待ちの一覧を開く / Save and open pending approvals</button></form>
<p>ボタンを押すとブラウザやパスワード管理が「保存しますか」と尋ねます。ユーザ名が上の口座名になっているのを確かめて保存してください。${en('When you press the button, your browser or password manager offers to save. Check that the username is the account name above, then save.')}</p>
<p>承認待ちは <a href="/pending">https://thth.me/pending</a> で、このユーザ名と secret を入れると一覧で見られます。承認ページでこの secret を入れて押したときだけ、投稿・返信・削除が行われます。secret は LLM や原稿に書かないでください。${en('To see what is waiting for your approval, open <a href="/pending">https://thth.me/pending</a> and sign in with this username and secret. Posts, replies and deletions happen only when you enter this secret on an approval page. Never paste it into an LLM or a draft.')}</p>`);
}

export async function inviteRequest(request,env,url){
  try{
    if(url.search||url.hash||url.pathname.includes('%'))return reply(400,{error:'invalid_request'});
    const match=/^\/invite\/([A-Za-z0-9_-]{43})$/.exec(url.pathname);
    if(!match)return reply(404,{error:'not_found'});
    if(!env.INVITE_OBJECT)return reply(503,{error:'invite_unavailable'});
    if(!env.APPROVAL_PUBLIC_LIMIT||!(await env.APPROVAL_PUBLIC_LIMIT.limit({key:await digest(request.headers.get('cf-connecting-ip')||'unknown-peer')})).success)return reply(429,{error:'rate_limited'});
    if(!['GET','POST'].includes(request.method))return reply(405,{error:'method_not_allowed'});
    const stub=inviteStub(env,await digest(match[1]));
    if(request.method==='GET')return renderView(await stub.view());
    // 承認ページと同じ: no-referrer では同一 origin の form POST でも Origin が null か無し。
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
