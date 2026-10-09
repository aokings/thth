// ---------------------------------------------------------------- アクティビティ（設計 3.12.0 §3.4・§3.5）
// オーナーがユーザー名とアカウントのシークレットで入り（照合は PBKDF2・5 回失敗で 15 分閉じる）、
// 自分のアカウントで出たもの・消したもの・予約・待機中・止まった理由を新しい順に見る。
// ここでできるのは: アカウントを止める・止めたのを戻す・予約（待機中を含む）を取り消す・設定を変える・
// LLM のキーを発行し直す・キーを取り消す。どれも secret をもう一度入れて確かめ、VM が数十秒で行う。
// 一覧の中身は VM が押し上げた要約（本文は先頭 60 字まで・1 時間で忘れる）。Worker は閲覧を記録しない。
// 作法は 3.12.0 までの承認ページと同じ（script なし・CSP・no-store・no-referrer・同一 origin の POST）。
import {PERSON,TTL,opaque,boundedBody,personStub,fromSameOrigin,page,t,escape,publicQuota} from './person.js';
import {digest,reply} from './relay.js';
import {SETTING_KEYS} from './person-object.js';

const COOKIE='thth_activity';
const COOKIE_VALUE=/^([A-Za-z0-9_-]{43})([a-zA-Z0-9][a-zA-Z0-9_.-]{0,63})$/;
const TITLE='THTH アクティビティ';
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
const back=`<p><a href="/activity">${t('アクティビティに戻る','Back to activity')}</a></p>`;
const secretField=`<label>${t('アカウントのシークレット','Account secret')} <input type="password" name="secret" autocomplete="current-password" required maxlength="128"></label>`;
const pair=([ja,english])=>t(escape(ja),escape(english));

export const KIND_WORDS={published:['出た','Published'],retracted:['消した','Deleted'],scheduled:['予約','Scheduled'],
  held:['待機中','On hold'],stopped:['止めた','Stopped']};
export const STOP_WORDS={burst:['急な連投でガードが止めました','Stopped by the safety limit (burst of posts)'],
  owner:['あなたが止めました','You stopped it'],guard_state_unreadable:['止めた印が読めないため止まっています','Stopped (the stop record cannot be read)']};
// 2026-10-10: キーの呼び名を「API キー」に揃えた（前は「LLM のキー」「アシスタントキー」が混じっていた）。
export const ACTION_WORDS={stop:['アカウントを止める','Stop the account'],resume:['止めたのを戻す','Resume the account'],
  cancel:['予約を取り消す','Cancel the scheduled post'],settings:['設定を変える','Change a setting'],
  revoke:['API キーを取り消す','Revoke the API key'],rotate:['API キーを発行する','Issue an API key']};
const STATUS_WORDS={pending:['反映待ち','Waiting for the server'],done:['済み','Done'],failed:['できませんでした','Failed']};
export const SETTING_WORDS={daily_max_posts:['1 日の公開の上限','Daily post limit'],
  daily_max_retracts:['1 日の削除の上限','Daily delete limit'],burst_count:['急な連投: 件数','Burst: posts'],
  burst_minutes:['急な連投: 分','Burst: minutes'],hold_minutes:['取り消しの猶予（分）','Hold (minutes)'],
  min_interval_hours:['最短間隔（時間・返信には掛けない）','Minimum interval (hours, not for replies)']};

function signIn(status=200,note=''){
  return page(status,`<h1>${t('アクティビティ','Activity')}</h1>${note}
<p>${t('あなたのアカウントで出たもの・消したもの・予約・待機中のもの・止まった理由が、新しい順に並びます。','See what happened on your accounts, newest first: published, deleted, scheduled or held items, and why an account was stopped.')}</p>
<form method="post" action="/activity"><label>${t('アカウント名','Account name')} <input name="person" autocomplete="username" required maxlength="64"></label>${secretField}<button type="submit">${t('入る','Sign in')}</button></form>
<p class="note">${t('招待で用意したアカウントでは、ユーザー名はアカウント名です。10 分で閉じます。シークレットは LLM や原稿に書かないでください。','For an account prepared by an invitation, the username is the account name. The page closes after 10 minutes. Never paste the secret into an LLM or a draft.')}</p>`,TITLE);
}
function form(act,account,fields,label,extra='',secretFirst=false,quiet=false){
  const body=secretFirst?`${secretField}${fields}${extra}`:`${fields}${extra}${secretField}`;
  return `<form method="post" action="/activity"><input type="hidden" name="act" value="${escape(act)}"><input type="hidden" name="account" value="${escape(account)}">${body}<button type="submit"${quiet?' class="quiet"':''}>${label}</button></form>`;
}
function row(account,r){
  const what=r.kind==='stopped'?(STOP_WORDS[r.reason]?pair(STOP_WORDS[r.reason]):escape(r.reason)):escape(r.head);
  const reply=r.reply?' · '+t('返信','Reply'):'';
  const id=r.post_id?` · post_id ${escape(r.post_id)}`:'';
  const cancel=(r.kind==='held'||r.kind==='scheduled')&&r.draft_id
    ?form('cancel',account,`<input type="hidden" name="draft_id" value="${escape(r.draft_id)}">`,t('この予約を取り消す','Cancel this post'),'',false,true):'';
  return `<li><p><strong>${pair(KIND_WORDS[r.kind])}</strong> · ${escape(r.at)}${reply}${id}</p><pre>${what}</pre>${cancel}</li>`;
}
function section(s){
  const l=s.limits;
  const stop=s.stopped
    ?form('resume',s.account,'',t('止めたのを戻す','Resume'))
    :form('stop',s.account,'',t('このアカウントを止める','Stop this account'),`<p class="note">${t('止めると、戻すまで公開・削除・予約をすべて受け付けません。','While stopped, every publish, delete and schedule is refused until you resume.')}</p>`);
  // 項目は select でなくラジオにした（option の中では日本語と English を切り替えられないため・2026-10-10）。
  const options=SETTING_KEYS.map((k,i)=>`<label class="pick"><input type="radio" name="key" value="${k}"${i===0?' required':''}> ${pair(SETTING_WORDS[k])}</label>`).join('');
  // 3.14.2: パスワード管理は password の欄の手前の文字の欄をユーザー名と見なし、値の欄にユーザー名を入れていた
  // （09-26 のスマホの画面・autocomplete="off" は 3.12.0 からあったが効かない）。secret の欄を先に置き、
  // 値の欄には主なパスワード管理の「入れない」印を添える。
  const settings=form('settings',s.account,`<fieldset><legend>${t('項目','Setting')}</legend>${options}</fieldset><label>${t('値','Value')} <input name="value" required maxlength="9" autocomplete="off" inputmode="decimal" data-1p-ignore data-lpignore="true" data-bwignore="true" data-form-type="other"></label>`,t('設定を変える','Change'),'',true,true);
  const key=s.credential
    ?`<dt>${t('API キー','API key')}</dt><dd><code>${escape(s.credential.id)}…</code> · ${t('期限','expires')} ${escape(s.credential.expires_at)}${s.credential.revoked?' · '+t('取り消し済み','revoked'):''}</dd>`
    :`<dt>${t('API キー','API key')}</dt><dd>${t('なし','None')}</dd>`;
  const keys=form('rotate',s.account,'',t('API キーを発行する','Issue an API key'),`<p class="note">${t('ふつうはターミナルの <code>thth login</code> で受け取ります。ここではブラウザが使えない機械のために、新しいキーをこの画面に 1 度だけ出します。前のキーは使えなくなります。','Normally the terminal receives it with <code>thth login</code>. This is for machines without a browser: the new key is shown once on this screen, and the previous key stops working.')}</p>`,false,true)+
    (s.credential&&!s.credential.revoked?form('revoke',s.account,'',t('API キーを取り消す','Revoke the API key'),'',false,true):'');
  const rows=s.rows.length?`<ul>${s.rows.map(r=>row(s.account,r)).join('')}</ul>`:`<p class="note">${t('まだ何もありません。','Nothing yet.')}</p>`;
  const min=t(' 分',' min'),hours=t(' 時間',' h');
  return `<section><h2>${escape(s.account)}</h2>
<dl class="facts"><dt>${t('今日の公開','Posts today')}</dt><dd>${escape(s.today.posts)} / ${escape(l.daily_max_posts)}</dd><dt>${t('今日の削除','Deletions today')}</dt><dd>${escape(s.today.retracts)} / ${escape(l.daily_max_retracts)}</dd>
<dt>${t('急な連投で止める','Burst stop')}</dt><dd>${escape(l.burst_minutes)}${min}${t('に',' /')} ${escape(l.burst_count)}${t(' 件',' posts')}</dd><dt>${t('猶予','Hold')}</dt><dd>${escape(l.hold_minutes)}${min}</dd><dt>${t('最短間隔','Interval')}</dt><dd>${escape(l.min_interval_hours)}${hours}</dd>
${s.scheduled?'':`<dt>${t('予約','Scheduler')}</dt><dd>${t('timer なし（予約と猶予は使えません）','none (no scheduled or held posts)')}</dd>`}${key}<dt>${t('最終更新','Updated')}</dt><dd>${escape(s.generated_at)}</dd></dl>
${rows}<h2>${t('操作','Controls')}</h2>${stop}${settings}${keys}</section>`;
}
function activityPage(person,data){
  const stopped=data.accounts.filter(s=>s.stopped);
  const banner=stopped.map(s=>{const words=STOP_WORDS[s.stopped.reason]??[s.stopped.reason,s.stopped.reason];
    return `<p class="err">${t(escape(s.account)+' は止まっています。'+escape(words[0])+'（'+escape(s.stopped.at??'')+'）',escape(s.account)+' is stopped. '+escape(words[1])+'.')}</p>`;}).join('');
  const actions=data.actions.length?`<h2>${t('頼んだ操作','Requested operations')}</h2><ul>${data.actions.map(a=>{
    return `<li>${escape(a.account)}: ${pair(ACTION_WORDS[a.kind])} — ${pair(STATUS_WORDS[a.status]??[a.status,a.status])}${a.reason?' ('+escape(a.reason)+')':''}</li>`;}).join('')}</ul>`:'';
  const body=data.accounts.length?data.accounts.map(section).join(''):`<p class="status">${t('サーバーからの様子がまだ届いていません。数十秒後に開き直してください。','Nothing has arrived from the server yet. Reload in a few tens of seconds.')}</p>`;
  return page(200,`<h1>${t('アクティビティ','Activity')}</h1><p class="sub">${t('オーナー','Owner')}: ${escape(person)}</p>${banner}${actions}${body}
<p class="note">${t('操作はシークレットをもう一度入れて確かめ、サーバーが数十秒で行います。結果はこの一覧に出ます。','Each operation asks for the secret again; the server carries it out within a few tens of seconds and the result appears here.')}</p>
<form method="post" action="/activity"><input type="hidden" name="leave" value="1"><button type="submit" class="quiet">${t('閉じる','Sign out')}</button></form>`,TITLE);
}
// MCP の登録の形（Claude Code）。mcp/server.py は THTH_REPORT_TOKEN（と運営者が置く THTH_REPORT_CREDENTIALS）
// を環境変数で読む。起動のコマンドは運営者が Worker の変数 THTH_MCP_COMMAND に置く（無ければ運営者に聞いてもらう）。
function keyPage(env,account,bearer){
  const command=typeof env.THTH_MCP_COMMAND==='string'&&env.THTH_MCP_COMMAND.trim()?env.THTH_MCP_COMMAND.trim():null;
  const line=command?`claude mcp add thth -e THTH_REPORT_TOKEN=${bearer} -- ${command}`:`THTH_REPORT_TOKEN=${bearer}`;
  return page(200,`<h1>${t('API キー','API key')}</h1><p class="sub">${t('アカウント','Account')}: ${escape(account)}</p>
<p><strong class="once">${t('この表示は 1 度だけです。','This is shown only once.')}</strong>${t('いま使うか、パスワード管理に入れてから閉じてください。','Use it now or store it in your password manager before closing.')}</p>
<code class="secret">${escape(bearer)}</code>
<p>${t('ブラウザが使えない機械では: <code>thth login --stdin</code> を打ち、このキーを貼ります。','On a machine without a browser: run <code>thth login --stdin</code> and paste this key.')}</p>
<p>${command?t('Claude Code に登録する 1 行','One line to register it in Claude Code'):t('MCP の起動に渡す環境変数です。起動のコマンドは<a href="/privacy/">プライバシーポリシーの Contact</a>から運営者に確認してください。','Environment variable for the MCP server. Ask the operator for the launch command through <a href="/privacy/">Contact in the Privacy Policy</a>.')}</p><pre>${escape(line)}</pre>
<p class="note">${t('数十秒でこのキーに切り替わり、前のキーは使えなくなります。キーは原稿や公開の場に貼らないでください。','Within a few tens of seconds the server switches to this key and the previous key stops working. Never paste the key into a draft or anywhere public.')}</p>${back}`,TITLE);
}
function accepted(kind){
  return page(200,`<h1>${t('受け付けました','Received')}</h1><p class="status">${pair(ACTION_WORDS[kind])}</p><p>${t('サーバーが数十秒で行い、結果をアクティビティに出します。','The server carries it out within a few tens of seconds and shows the result in the activity list.')}</p>${back}`,TITLE);
}
const refusedSecret=()=>page(403,`<h1>${t('確かめられませんでした','Could not confirm')}</h1><p class="err">${t('シークレットを確かめてください。5 回続けて間違えると 15 分閉じます。','Check the account secret. After five failures in a row the page is closed for 15 minutes.')}</p>${back}`,TITLE);
const ACT_KEYS={stop:'account,act,secret',resume:'account,act,secret',revoke:'account,act,secret',rotate:'account,act,secret',
  cancel:'account,act,draft_id,secret',settings:'account,act,key,secret,value'};

export async function activityRequest(request,env,url){
  try{
    if(url.search||url.hash||url.pathname.includes('%'))return reply(400,{error:'invalid_request'});
    if(!env.PERSON)return reply(503);
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
      const person=form.get('person'),refused=()=>signIn(403,`<p class="err">${t('入れませんでした。アカウント名とシークレットを確かめてください。5 回続けて間違えると 15 分閉じます。','Sign-in failed. Check the account name and the secret. After five failures in a row it is closed for 15 minutes.')}</p>`);
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
    if(done.status!==200)return page(done.status===409?409:400,`<h1>${t('受け付けられませんでした','Not accepted')}</h1><p>${t('一覧を開き直してからもう一度試してください。','Reload the activity list and try again.')}</p>${back}`,TITLE);
    return act==='rotate'?keyPage(env,done.body.account,done.body.bearer):accepted(act);
  }catch{return reply(503,{error:'activity_unavailable'});}
}
