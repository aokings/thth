// ---------------------------------------------------------------- 動きの一覧（設計 3.12.0 §3.4・§3.5）
// 持ち主がユーザ名と承認 secret で入り（/pending と同じ入口・同じ照合・5 回失敗で 15 分閉じる）、
// 自分の口座で出たもの・消したもの・予約・猶予中・止まった理由を新しい順に見る。
// ここでできるのは: 口座を止める・止めたのを戻す・予約（猶予中を含む）を取り消す・設定を変える・
// LLM の鍵を発行し直す・鍵を取り消す。どれも secret をもう一度入れて確かめ、VM が数十秒で行う。
// 一覧の中身は VM が押し上げた要約（本文は先頭 60 字まで・1 時間で忘れる）。Worker は閲覧を記録しない。
// 作法は承認ページと同じ（script なし・CSP・no-store・no-referrer・同一 origin の POST）。
import {PERSON,TTL,opaque,boundedBody,personStub,fromSameOrigin,page,en,escape,publicQuota} from './approval.js';
import {digest,reply} from './relay.js';
import {SETTING_KEYS} from './approval-object.js';

const COOKIE='thth_activity';
const COOKIE_VALUE=/^([A-Za-z0-9_-]{43})([a-zA-Z0-9][a-zA-Z0-9_.-]{0,63})$/;
const TITLE='THTH 動きの一覧';
function readCookie(request){
  for(const part of (request.headers.get('cookie')||'').split(';')){
    const [name,...rest]=part.trim().split('=');
    if(name!==COOKIE)continue;
    const m=COOKIE_VALUE.exec(rest.join('='));
    return m?{token:m[1],person:m[2]}:null;
  }
  return null;
}
const setCookie=(value,age)=>`${COOKIE}=${value}; Max-Age=${age}; Path=/activity; Secure; HttpOnly; SameSite=Strict`;
const see=(location,cookie)=>new Response(null,{status:303,headers:{location,'cache-control':'no-store','referrer-policy':'no-referrer',...(cookie?{'set-cookie':cookie}:{})}});
const toPending=`<p><a href="/pending">承認待ちの一覧へ / Pending approvals</a></p>`;
const back=`<p><a href="/activity">動きの一覧に戻る / Back to activity</a></p>`;
const secretField=`<label>承認 secret / Approval secret <input type="password" name="secret" autocomplete="current-password" required maxlength="128"></label>`;

export const KIND_WORDS={published:['出た','Published'],retracted:['消した','Deleted'],scheduled:['予約','Scheduled'],
  held:['猶予中','On hold'],stopped:['止めた','Stopped']};
export const STOP_WORDS={burst:['急な連投で安全装置が止めました','Stopped by the safety limit (burst of posts)'],
  owner:['あなたが止めました','You stopped it'],guard_state_unreadable:['止めた印が読めないため止まっています','Stopped (the stop record cannot be read)']};
export const ACTION_WORDS={stop:['口座を止める','Stop the account'],resume:['止めたのを戻す','Resume the account'],
  cancel:['予約を取り消す','Cancel the scheduled post'],settings:['設定を変える','Change a setting'],
  revoke:['LLM の鍵を取り消す','Revoke the key for your LLM'],rotate:['LLM の鍵を発行する','Issue a key for your LLM']};
const STATUS_WORDS={pending:['反映待ち','Waiting for the server'],done:['済み','Done'],failed:['できませんでした','Failed']};
export const SETTING_WORDS={approval:'承認（none・publish・all）/ Approval',daily_max_posts:'1 日の公開の上限 / Daily post limit',
  daily_max_retracts:'1 日の削除の上限 / Daily delete limit',burst_count:'急な連投: 件数 / Burst: posts',
  burst_minutes:'急な連投: 分 / Burst: minutes',hold_minutes:'取り消しの猶予（分）/ Hold (minutes)',
  min_interval_hours:'最短間隔（時間・返信には掛けない）/ Minimum interval (hours, not for replies)'};

function signIn(status=200,note=''){
  return page(status,`<h1>動きの一覧${en('Activity')}</h1>${note}
<p>ユーザ名と承認 secret で入ると、あなたの口座で出たもの・消したもの・予約・猶予中のもの・止まった理由が新しい順に並びます。招待で用意した口座では、ユーザ名は口座名です。${en('Sign in with your username and approval secret to see what was published, deleted, scheduled or held on your accounts, newest first, and why an account was stopped. For an account prepared by an invitation, the username is the account name.')}</p>
<form method="post" action="/activity"><label>ユーザ名 / Username <input name="person" autocomplete="username" required maxlength="64"></label>${secretField}<button type="submit">一覧を見る / Show activity</button></form>
${toPending}<p>一覧は 10 分で閉じます。secret は LLM や原稿に書かないでください。${en('The page closes after 10 minutes. Never paste the secret into an LLM or a draft.')}</p>`,TITLE);
}
function form(act,account,fields,label,extra=''){
  return `<form method="post" action="/activity"><input type="hidden" name="act" value="${escape(act)}"><input type="hidden" name="account" value="${escape(account)}">${fields}${extra}${secretField}<button type="submit">${label}</button></form>`;
}
function row(account,r){
  const [ja,enWord]=KIND_WORDS[r.kind];
  const what=r.kind==='stopped'?escape((STOP_WORDS[r.reason]??[r.reason])[0]):escape(r.head);
  const reply=r.reply?' · 返信 / Reply':'';
  const id=r.post_id?` · post_id ${escape(r.post_id)}`:'';
  const cancel=(r.kind==='held'||r.kind==='scheduled')&&r.draft_id
    ?form('cancel',account,`<input type="hidden" name="draft_id" value="${escape(r.draft_id)}">`,`この予約を取り消す / Cancel this post`):'';
  return `<li><p><strong>${ja}</strong> / ${enWord} · ${escape(r.at)}${reply}${id}</p><pre>${what}</pre>${cancel}</li>`;
}
function section(s){
  const l=s.limits;
  const stop=s.stopped
    ?form('resume',s.account,'','止めたのを戻す / Resume')
    :form('stop',s.account,'','この口座を止める / Stop this account',`<p>止めると、戻すまで公開・削除・予約をすべて断ります。${en('While stopped, every publish, delete and schedule is refused until you resume.')}</p>`);
  const options=SETTING_KEYS.map(k=>`<option value="${k}">${escape(SETTING_WORDS[k])}</option>`).join('');
  const settings=form('settings',s.account,`<label>項目 / Setting <select name="key">${options}</select></label><label>値 / Value <input name="value" required maxlength="9" autocomplete="off"></label>`,'設定を変える / Change');
  const key=s.credential
    ?`<p>LLM の鍵 / Key for your LLM: ${escape(s.credential.id)}… · 期限 / Expires ${escape(s.credential.expires_at)}${s.credential.revoked?' · 取り消し済み / Revoked':''}</p>`
    :`<p>LLM の鍵 / Key for your LLM: なし / None</p>`;
  const keys=form('rotate',s.account,'','LLM の鍵を発行する / Issue a key for your LLM',`<p>新しい鍵をこの画面に 1 度だけ出します。前の鍵は使えなくなります。${en('The new key is shown once on this screen. The previous key stops working.')}</p>`)+
    (s.credential&&!s.credential.revoked?form('revoke',s.account,'','LLM の鍵を取り消す / Revoke the key'):'');
  const rows=s.rows.length?`<ul>${s.rows.map(r=>row(s.account,r)).join('')}</ul>`:`<p>まだ何もありません。${en('Nothing yet.')}</p>`;
  return `<section><h2>${escape(s.account)}</h2>
<p>承認 / Approval: ${escape(s.approval)} · 今日の公開 / Posts today: ${escape(s.today.posts)} / ${escape(l.daily_max_posts)} · 今日の削除 / Deletions today: ${escape(s.today.retracts)} / ${escape(l.daily_max_retracts)}</p>
<p>急な連投で止める / Burst stop: ${escape(l.burst_minutes)} 分に ${escape(l.burst_count)} 件 · 猶予 / Hold: ${escape(l.hold_minutes)} 分 · 最短間隔 / Interval: ${escape(l.min_interval_hours)} 時間${s.scheduled?'':' · 予約の timer なし（予約と猶予は使えません）/ No scheduler (no scheduled or held posts)'}</p>
<p>最終更新 / Updated: ${escape(s.generated_at)}</p>${key}${rows}${stop}${settings}${keys}</section>`;
}
function activityPage(person,data){
  const stopped=data.accounts.filter(s=>s.stopped);
  const banner=stopped.map(s=>{const [ja,english]=STOP_WORDS[s.stopped.reason]??[s.stopped.reason,s.stopped.reason];
    return `<p><strong>${escape(s.account)} は止まっています。${escape(ja)}（${escape(s.stopped.at??'')}）</strong>${en(escape(s.account)+' is stopped. '+escape(english)+'.')}</p>`;}).join('');
  const actions=data.actions.length?`<h2>頼んだ操作${en('Requested operations')}</h2><ul>${data.actions.map(a=>{const [ja,english]=ACTION_WORDS[a.kind],[sj,se]=STATUS_WORDS[a.status]??[a.status,a.status];
    return `<li>${escape(a.account)}: ${ja} / ${english} — ${sj} / ${se}${a.reason?' ('+escape(a.reason)+')':''}</li>`;}).join('')}</ul>`:'';
  const body=data.accounts.length?data.accounts.map(section).join(''):`<p>サーバからの様子がまだ届いていません。数十秒後に開き直してください。${en('Nothing has arrived from the server yet. Reload in a few tens of seconds.')}</p>`;
  return page(200,`<h1>動きの一覧${en('Activity')}</h1><p>ユーザ名 / Username: ${escape(person)}</p>${banner}${toPending}${actions}${body}
<p>操作は承認 secret をもう一度入れて確かめ、サーバが数十秒で行います。結果はこの一覧に出ます。${en('Each operation asks for the approval secret again; the server carries it out within a few tens of seconds and the result appears here.')}</p>
<form method="post" action="/activity"><input type="hidden" name="leave" value="1"><button type="submit">閉じる / Sign out</button></form>`,TITLE);
}
// MCP の登録の形（Claude Code）。mcp/server.py は THTH_REPORT_TOKEN（と運営者が置く THTH_REPORT_CREDENTIALS）
// を環境変数で読む。起動の命令は運営者が Worker の変数 THTH_MCP_COMMAND に置く（無ければ運営者に聞いてもらう）。
function keyPage(env,account,bearer){
  const command=typeof env.THTH_MCP_COMMAND==='string'&&env.THTH_MCP_COMMAND.trim()?env.THTH_MCP_COMMAND.trim():null;
  const line=command?`claude mcp add thth -e THTH_REPORT_TOKEN=${bearer} -- ${command}`:`THTH_REPORT_TOKEN=${bearer}`;
  return page(200,`<h1>LLM の鍵${en('Key for your LLM')}</h1><p>アカウント / Account: ${escape(account)}</p>
<p><strong>この表示は 1 度だけです。</strong>パスワード管理に入れてから閉じてください。${en('This is shown only once. Store it in your password manager before closing.')}</p>
<p>鍵 / Key</p><pre>${escape(bearer)}</pre>
<p>${command?'Claude Code に登録する 1 行 / One line to register it in Claude Code':'MCP の起動に渡す環境変数（起動の命令は運営者に聞いてください）/ Environment variable for the MCP server (ask the operator for the launch command)'}</p><pre>${escape(line)}</pre>
<p>サーバが切り替えた時点（数十秒後）から、この鍵が使え、前の鍵は使えなくなります。鍵は原稿や公開の場に貼らないでください。${en('Within a few tens of seconds the server switches to this key and the previous key stops working. Never paste the key into a draft or anywhere public.')}</p>${back}`,TITLE);
}
function accepted(kind){
  const [ja,english]=ACTION_WORDS[kind];
  return page(200,`<h1>受け付けました${en('Received')}</h1><p>${ja} / ${english}</p><p>サーバが数十秒で行い、結果を動きの一覧に出します。${en('The server carries it out within a few tens of seconds and shows the result in the activity list.')}</p>${back}`,TITLE);
}
const refusedSecret=()=>page(403,`<h1>確かめられませんでした${en('Could not confirm')}</h1><p>承認 secret を確かめてください。5 回続けて間違えると 15 分閉じます。${en('Check the approval secret. After five failures in a row the page is closed for 15 minutes.')}</p>${back}`,TITLE);
const ACT_KEYS={stop:'account,act,secret',resume:'account,act,secret',revoke:'account,act,secret',rotate:'account,act,secret',
  cancel:'account,act,draft_id,secret',settings:'account,act,key,secret,value'};

export async function activityRequest(request,env,url){
  try{
    if(url.search||url.hash||url.pathname.includes('%'))return reply(400,{error:'invalid_request'});
    if(!env.APPROVAL_PERSON)return reply(503);
    if(url.pathname!=='/activity')return reply(404,{error:'not_found'});
    if(!await publicQuota(request,env))return reply(429,{error:'rate_limited'});
    if(!['GET','POST'].includes(request.method))return reply(405,{error:'method_not_allowed'});
    if(request.method==='POST'&&!fromSameOrigin(request,url))return reply(403,{error:'forbidden'});
    const cookie=readCookie(request);
    const who=cookie?{stub:await personStub(env,cookie.person),hash:await digest(cookie.token)}:null;
    const expired=response=>{if(cookie)response.headers.append('set-cookie',setCookie('',0));return response;};
    if(request.method==='GET'){
      if(!who)return signIn();
      const viewed=await who.stub.activityView(who.hash);
      if(viewed.status!==200)return expired(signIn());
      return activityPage(cookie.person,viewed.body);
    }
    const form=new URLSearchParams(await boundedBody(request,2048));
    const keys=[...form.keys()].sort().join(',');
    if(keys==='leave'){
      if(who)await who.stub.closeList(who.hash);
      return see('/activity',setCookie('',0));
    }
    if(keys==='person,secret'){
      const person=form.get('person'),refused=()=>signIn(403,`<p><strong>入れませんでした。</strong>ユーザ名と承認 secret を確かめてください。5 回続けて間違えると 15 分閉じます。${en('Sign-in failed. Check the username and the approval secret. After five failures in a row it is closed for 15 minutes.')}</p>`);
      if(!PERSON.test(person))return refused();
      const token=opaque(),opened=await (await personStub(env,person)).openList(form.get('secret'),await digest(token));
      if(opened.status!==200)return refused();
      return see('/activity',setCookie(token+person,Math.floor(TTL/1000)));
    }
    const act=form.get('act');
    if(!Object.hasOwn(ACT_KEYS,act??'')||keys!==ACT_KEYS[act])return reply(400,{error:'invalid_request'});
    if(!who)return expired(signIn(401));
    const action={kind:act,account:form.get('account')};
    if(act==='cancel')action.draft_id=form.get('draft_id');
    if(act==='settings'){action.key=form.get('key');action.value=form.get('value').trim();}
    const done=await who.stub.activityAct(who.hash,form.get('secret'),action);
    if(done.status===401)return expired(signIn(401));
    if(done.status===403)return refusedSecret();
    if(done.status!==200)return page(done.status===409?409:400,`<h1>受け付けられませんでした${en('Not accepted')}</h1><p>一覧を開き直してからもう一度試してください。${en('Reload the activity list and try again.')}</p>${back}`,TITLE);
    return act==='rotate'?keyPage(env,done.body.account,done.body.bearer):accepted(act);
  }catch{return reply(503,{error:'approval_unavailable'});}
}
