// Approval capabilities are never emitted outside their dedicated page/URL.
import {digest, STATE_PATTERN, HASH_PATTERN, reply} from './relay.js';
import {deletionStub} from './deletion.js';
export const PERSON = /^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,63}$/;
export const TTL = 600_000;
// 3.11.0 承認待ちの一覧: 一覧に出す承認 job は最大 24 時間まで待てる（既定の 10 分は変えない）。
// 承認ページの 10 分は、開いた時点から数え直す（ただし job の期限は越えない）。
export const LIST_TTL = 86_400_000;
export const HEAD = 60;          // 一覧に出す本文は先頭 60 字まで
export const LIST_MAX = 50;      // 1 人の一覧に並べる承認待ちの上限
export const LIST_LOCK_MS = 900_000; // 一覧に入る secret を 5 回間違えたら 15 分閉じる（時間で戻る）
export const VIEW_MAX = 8;       // 1 人の一覧の session（ブラウザ）の上限
export const ITERATIONS = 100_000; // Workers WebCrypto caps PBKDF2 at 100,000 iterations (production NotSupportedError above it).
const encoder = new TextEncoder();
export const fail = (status=400, error='invalid_request') => ({status, body:{error}});
export const opaque = () => b64(crypto.getRandomValues(new Uint8Array(32)));
export function b64(bytes) { return btoa(String.fromCharCode(...new Uint8Array(bytes))).replaceAll('+','-').replaceAll('/','_').replaceAll('=',''); }
export function unb64(text) {
  if (typeof text !== 'string' || !/^[A-Za-z0-9_-]+$/.test(text)) throw new Error('invalid_encoding');
  return Uint8Array.from(atob(text.replaceAll('-','+').replaceAll('_','/')+'='.repeat((4-text.length%4)%4)), c=>c.charCodeAt(0));
}
export function equal(a,b) { return a.length===b.length && crypto.subtle.timingSafeEqual(a,b); }
export async function verifier(secret,salt) {
  const key=await crypto.subtle.importKey('raw',encoder.encode(secret),'PBKDF2',false,['deriveBits']);
  return b64(await crypto.subtle.deriveBits({name:'PBKDF2',hash:'SHA-256',salt:unb64(salt),iterations:ITERATIONS},key,256));
}
export const fields = (body,keys) => body && typeof body==='object' && !Array.isArray(body) &&
  Object.keys(body).sort().join(',')===[...keys].sort().join(',');
export async function boundedBody(request,max=65_536) {
  const reader=request.body?.getReader(); if(!reader)return '';
  const chunks=[];let size=0;
  try { for(;;) { const {value,done}=await reader.read();if(done)break;
    size+=value.length;if(size>max){await reader.cancel();throw new Error('too_large');}chunks.push(value); }
    const all=new Uint8Array(size);let i=0;for(const part of chunks){all.set(part,i);i+=part.length;}
    return new TextDecoder('utf-8',{fatal:true}).decode(all);
  } finally {reader.releaseLock();}
}
export async function personStub(env,person) {return env.APPROVAL_PERSON.getByName(await digest(person));}
export async function accountStub(env,account) {return env.APPROVAL_ACCOUNT.getByName(await digest(account));}
export async function sessionStub(env,token) {return env.APPROVAL_SESSION.getByName(await digest(token));}
// 招待（3.10.0）の object は code の SHA-256 で引く。VM は hash しか持たないので、署名の
// subject も同じ hash。ブラウザの道（/invite/<code>）はここで code を hash にしてから引く。
export function inviteStub(env,hash) {return env.INVITE_OBJECT.getByName(hash);}
// Canonical wire binding: role/subject/operation are signed, not caller-selected authority.
export function canonical(method,path,role,subject,operation,time,nonce,bodyHash) {
  return ['thth-approval-v1',method,path,role,subject,operation,time,nonce,bodyHash].join('\n');
}
async function authenticate(request,env,url,raw,role,subject,operation) {
  const time=request.headers.get('x-thth-time')||'', nonce=request.headers.get('x-thth-nonce')||'';
  if(!/^\d{13}$/.test(time)||Math.abs(Date.now()-Number(time))>60_000||!STATE_PATTERN.test(nonce))return null;
  const signature=request.headers.get('x-thth-signature')||'';
  if(signature.length!==512||!env.APPROVAL_PUBLIC_KEY)return null; // RSA-3072 raw signature, unpadded base64url.
  const key=await crypto.subtle.importKey('spki',unb64(env.APPROVAL_PUBLIC_KEY),{name:'RSA-PSS',hash:'SHA-256'},false,['verify']);
  if(key.algorithm.modulusLength!==3072)return null;
  const ok=await crypto.subtle.verify({name:'RSA-PSS',saltLength:32},key,unb64(signature),encoder.encode(
    canonical(request.method,url.pathname,role,subject,operation,time,nonce,await digest(raw))));
  return ok?{nonce,time:Number(time)}:null;
}
// 審査員（Meta）も押すので、日本語の下に英語を 1 行ずつ添える（招待のページと同じ作り・3.10.0）。
// 投稿の原稿そのものは訳さない。
export const en=text=>`<span class="en">${text}</span>`;
const kindLabel={approve:'原稿を承認',send:'この内容を公開',retract:'この投稿を削除'};
const kindEnglish={approve:'Approve this draft',send:'Publish this content',retract:'Delete this post'};
const accepted={approve:'原稿の承認を受け付けました',send:'公開の承認を受け付けました',retract:'削除の承認を受け付けました'};
const acceptedEnglish={approve:'Draft approval received',send:'Publication approval received',retract:'Deletion approval received'};
// 補足の見出しも「日本語 / English」（3.11.0・アカウントの行と同じ形）。
const labels={media:'媒体 / Platform',reply_to:'返信先 / Reply to',publish_at:'公開予定 / Scheduled for',target:'削除する投稿 / Post to delete',reason:'削除理由 / Reason',topic:'話題 / Topic',options:'公開オプション / Post options'};
export const escape=s=>String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const kindWord={image:'画像',video:'動画',audio:'音声',caption:'字幕'};
const roleWord={media:'添付',thumbnail:'代表画像',caption:'字幕'};
const clock=value=>{const total=Math.max(0,Math.round(Number(value)));return String(Math.floor(total/60)).padStart(2,'0')+':'+String(total%60).padStart(2,'0');};
// Every field is escaped; the preview capability appears in this markup only.
function attachment(row){
  const head=escape(roleWord[row.role])+' '+escape(row.index);
  const sha='sha '+escape(row.public_sha256.slice(0,12));
  const alt=' · alt: '+escape(row.alt);
  if(row.kind==='caption')return '<p>'+escape(kindWord.caption)+' ('+escape(row.alt)+') '+sha+'</p>';
  if(row.kind==='image'){
    const shape=row.width===null||row.height===null?'寸法: 適用外':escape(row.width)+'×'+escape(row.height);
    // `loading="lazy"` keeps a reload from fetching every capability at once:
    // /m/ shares the 120/IP/min public quota (docs/運用_承認relay_2.12.md).
    const missing=row.preview===null||row.preview===undefined;
    const image=missing?'':'<img src="/m/'+escape(row.preview)+'" alt="'+escape(row.alt)+'" loading="lazy">';
    // The human is the last check: an image that did not load is not an approval.
    const warn=missing?'':' · 画像が表示されない場合は承認しないでください / Do not approve if an image does not appear';
    return '<figure>'+image+'<figcaption>'+head+': '+escape(row.format.toUpperCase())+' '+shape+' · '+sha+alt+warn+'</figcaption></figure>';
  }
  const length=row.duration===null||row.duration===undefined?'長さ: 未取得':clock(row.duration);
  return '<p>'+head+': '+escape(kindWord[row.kind])+' '+length+' · '+sha+alt+'</p>';
}
function attachments(rows,typed){
  const list=Array.isArray(rows)?rows.map(attachment).join(''):'';
  const structured=typeof typed==='string'&&typed?'<p>型付き添付／公開設定 / Typed attachments and post settings</p><pre>'+escape(typed)+'</pre>':'';
  return list+structured;
}
export function page(status,body,title='THTH 承認') {
  return new Response('<!doctype html><html lang="ja"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex,nofollow,noarchive"><title>'+title+'</title><style>body{overflow-wrap:anywhere;max-width:44rem;margin:2rem auto;padding:0 1rem;font:1rem/1.7 system-ui}pre{white-space:pre-wrap;overflow-wrap:anywhere;font:inherit;border:1px solid;padding:1rem}figure{margin:1rem 0}img{max-width:100%;height:auto;border:1px solid}figcaption{font-size:.9rem}.en{display:block;font-size:.88rem;opacity:.75}input{max-width:100%;font:inherit}label{display:block;margin:.6rem 0}button{display:block;margin:1rem 0;padding:.6rem 1.4rem;font:inherit}li{margin:1.4rem 0}a.go{display:inline-block;padding:.4rem 1.2rem;border:1px solid;text-decoration:none}</style><body>'+body+'</body></html>',{status,headers:{
    'content-type':'text/html; charset=utf-8','cache-control':'no-store','referrer-policy':'no-referrer',
    'content-security-policy':"default-src 'none'; img-src 'self'; style-src 'unsafe-inline'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'",
    'x-content-type-options':'nosniff','x-frame-options':'DENY'}});
}
export async function publicQuota(request,env){
  return !!env.APPROVAL_PUBLIC_LIMIT&&(await env.APPROVAL_PUBLIC_LIMIT.limit({key:await digest(request.headers.get('cf-connecting-ip')||'unknown-peer')})).success;
}
// Referrer-Policy: no-referrer makes browsers send `Origin: null` (or omit it) even on a
// same-origin form POST (Fetch spec §4.9). Same-origin is then proven by Sec-Fetch-Site;
// the per-session csrf field and CSP form-action 'self' remain the CSRF gate.
// 承認ページ・承認待ちの一覧（3.11.0）の form POST はどれもここを通る。
export function fromSameOrigin(request,url){
  const originHeader=request.headers.get('origin'),site=request.headers.get('sec-fetch-site');
  const sameOrigin=originHeader===url.origin||((originHeader===null||originHeader==='null')&&(site===null||site==='same-origin'));
  return sameOrigin&&/^application\/x-www-form-urlencoded(?:\s*;|$)/i.test(request.headers.get('content-type')||'');
}
const toList=`<p><a href="/pending">承認待ちの一覧 / Pending approvals</a></p>`;
// 承認ページ（/approve/<token> と、一覧から開いた /pending/<job_id> の両方）。
function approvalPage(result,fromList=false){
  if(result.status!==200)return page(result.status,`<h1>承認ページは無効です${en('This approval page is no longer valid')}</h1><p>サーバから新しく承認を求めてください。${en('Please request a new approval from the server.')}</p>${toList}`);
  const d=result.body;
  // 一覧に出した承認は、開いた時点から 10 分（job の期限を越えない）。
  const expiry=d.listed===true
    ?`<p>このページは開いてから 10 分で失効します（残り ${escape(d.page_minutes)} 分）。${en('This page expires 10 minutes after it was opened ('+escape(d.page_minutes)+' min left).')}</p>`
    :`<p>10 分で失効します。${en('This page expires in 10 minutes.')}</p>`;
  // No form when the page cannot show every image it lists: a human must
  // never be asked to approve attachments they could not look at.
  const act=d.live===true
    ?`${expiry}<form method="post"><input type="hidden" name="csrf" value="${escape(d.csrf)}"><label>承認 secret / Approval secret <input type="password" name="secret" autocomplete="current-password" required maxlength="128"></label><button type="submit">承認 / Approve</button></form>`
    :`<p>添付を表示できないため、この承認ページは使えません。サーバから新しく承認を求めてください。${en('Attachments cannot be shown, so this page cannot be used. Please request a new approval from the server.')}</p>`;
  return page(200,`<h1>${kindLabel[d.kind]}${en(kindEnglish[d.kind])}</h1><p>アカウント / Account: ${escape(d.account)}</p><pre>${escape(d.text)}</pre>${attachments(d.attachments,d.typed)}${Object.entries(d.context).filter(([,v])=>v!==null).map(([k,v])=>`<p>${labels[k]}: ${escape(v)}</p>`).join('')}<p>digest: ${escape(d.digest)}</p>${act}${fromList?toList:''}`);
}
function approvalOutcome(result,fromList=false){
  const back=fromList?toList:'';
  if(result.status===200)return page(200,`<h1>${accepted[result.body.kind]}${en(acceptedEnglish[result.body.kind])}</h1><p>サーバが内容を再確認します。操作の完了は元のセッションで確認してください。${en('The server re-checks the content. Confirm completion in the original session.')}</p>${back}`);
  if(result.body?.error==='page_expired')return page(410,`<h1>このページの期限が過ぎました${en('This page has expired')}</h1><p>開いてから 10 分が過ぎました。一覧から開き直してください。${en('More than 10 minutes have passed since it was opened. Open it again from the list.')}</p>${toList}`);
  return page(result.status,`<h1>承認できませんでした${en('Approval failed')}</h1><p>承認 secret または有効期限を確認してください。繰り返し失敗すると管理者による解除が必要です。${en('Check the approval secret and the expiry. Repeated failures lock you out until the operator unlocks you.')}</p>${back}`);
}
export async function approvalRequest(request,env,url) {
  try {
    if(url.search||url.hash||url.pathname.includes('%'))return reply(400,{error:'invalid_request'});
    if(!env.APPROVAL_PERSON||!env.APPROVAL_SESSION||!env.APPROVAL_ACCOUNT)return reply(503);
    const browser=/^\/approve\/([A-Za-z0-9_-]{43})$/.exec(url.pathname);
    if(browser){
      if(!await publicQuota(request,env))return reply(429,{error:'rate_limited'});
      if(!['GET','POST'].includes(request.method))return reply(405,{error:'method_not_allowed'});
      const stub=await sessionStub(env,browser[1]);
      if(request.method==='GET')return approvalPage(await stub.view());
      if(!fromSameOrigin(request,url))return reply(403,{error:'forbidden'});
      const form=new URLSearchParams(await boundedBody(request,2048));
      if([...form.keys()].sort().join(',')!=='csrf,secret')return reply(400,{error:'invalid_request'});
      return approvalOutcome(await stub.approve(form.get('secret'),form.get('csrf')));
    }
    const route=/^\/approval\/(person|session|account|deletion|invite|activity)\/([A-Za-z0-9_.-]+)\/(set|revoke|unlock|status|create|consume|cancel|list|read|verify|complete|discard|cleanup-retry|authorize|reset|sync)$/.exec(url.pathname);
    if(!route||request.method!=='POST')return reply(404,{error:'not_found'});
    const [,type,subject,operation]=route;
    // 3.12.0 §3.4 動きの一覧: VM が持ち主（person）ごとの要約を押し上げ、持ち主の操作を受け取る。
    if(type==='activity'?!PERSON.test(subject)||operation!=='sync':
       type==='invite'?!HASH_PATTERN.test(subject)||!['create','status','authorize','reset','complete','revoke'].includes(operation):type==='deletion'?!(subject==='inbox'&&operation==='list'||STATE_PATTERN.test(subject)&&['read','verify','complete','discard'].includes(operation)):type==='account'?!PERSON.test(subject)||!['revoke','status','cleanup-retry'].includes(operation):type==='person'?!PERSON.test(subject)||!['set','revoke','unlock','status'].includes(operation):!STATE_PATTERN.test(subject)||!['create','consume','status','cancel'].includes(operation))return reply(400,{error:'invalid_request'});
    if(!/^application\/json(?:\s*;|$)/i.test(request.headers.get('content-type')||''))return reply(400,{error:'invalid_request'});
    if(!env.APPROVAL_VERIFY_LIMIT || !(await env.APPROVAL_VERIFY_LIMIT.limit({key:await digest(request.headers.get('cf-connecting-ip')||'unknown-peer')})).success)return reply(429,{error:'rate_limited'});
    // Only the session `create` body carries attachments (第 8 段), so only it
    // gets the larger cap; every other JSON route keeps the 64 KiB default.
    // 動きの一覧の sync は口座 8 つ×30 行の要約を運ぶ（本文は先頭 60 字だけ）。
    const cap=type==='session'&&operation==='create'?98_304:type==='activity'?131_072:65_536;
    const raw=await boundedBody(request,cap), ticket=await authenticate(request,env,url,raw,type==='session'?'job':'operator',subject,operation);
    if(!ticket)return reply(401,{error:'unauthorized'});
    if(!env.APPROVAL_JOB_LIMIT || !(await env.APPROVAL_JOB_LIMIT.limit({key:await digest(type+'/'+subject)})).success)return reply(429,{error:'rate_limited'});
    const body=JSON.parse(raw);
    if(type==='activity'){const result=await (await personStub(env,subject)).activitySync(body,ticket);return reply(result.status,result.body);}
    const stub=type==='invite'?inviteStub(env,subject):type==='deletion'?deletionStub(env):type==='account'?await accountStub(env,subject):type==='person'?await personStub(env,subject):await sessionStub(env,subject);
    const result=await stub.manage(operation,body,ticket,subject);
    return reply(result.status,result.body);
  }catch{return reply(503,{error:'approval_unavailable'});}
}

// ---------------------------------------------------------------- 承認待ちの一覧（設計 3.11.0）
// 本人がユーザ名（承認者の名前・招待の口座では口座名と同じ）と承認 secret で入り、自分あての
// 承認待ちを一覧で見て、そこから承認ページを開く。作法は承認ページと同じ（script なし・CSP・
// no-store・no-referrer・同一 origin の POST は Sec-Fetch-Site で確かめる）。
// 一覧の索引と session はその人の person object にだけ置き、本文は承認ページの session にだけある
// （一覧は先頭 60 字だけを見せる）。Worker は閲覧を記録しない（流量の上限だけ）。
const COOKIE='thth_pending';
const COOKIE_VALUE=/^([A-Za-z0-9_-]{43})([a-zA-Z0-9][a-zA-Z0-9_.-]{0,63})$/;
function pendingCookie(request){
  for(const part of (request.headers.get('cookie')||'').split(';')){
    const [name,...rest]=part.trim().split('=');
    if(name!==COOKIE)continue;
    const m=COOKIE_VALUE.exec(rest.join('='));
    return m?{token:m[1],person:m[2]}:null;
  }
  return null;
}
const setCookie=(value,age)=>`${COOKIE}=${value}; Max-Age=${age}; Path=/pending; Secure; HttpOnly; SameSite=Strict`;
const listTitle='THTH 承認待ち';
// 3.12.0 §6-3: 入り直しの form はいつも /pending へ POST する（/pending/<job> へ POST すると
// 承認の form と見なされて戻り続けた）。元の job は hidden の next で運び、入ったあと 303 で戻す。
// next は job_id の形（43 字の base64url）だけ。場所は Worker が組み立てるので外へは飛ばない。
const JOB_ID=/^[A-Za-z0-9_-]{43}$/;
function signIn(status=200,note='',next=null){
  const back=next&&JOB_ID.test(next)?`<input type="hidden" name="next" value="${escape(next)}">`:'';
  return page(status,`<h1>承認待ちの一覧${en('Pending approvals')}</h1>${note}
<p>ユーザ名と承認 secret で入ると、あなたが承認する投稿・返信・削除の承認待ちが並びます。招待で用意した口座では、ユーザ名は口座名（完了のページに出た名前）です。${en('Sign in with your username and approval secret to see the posts, replies and deletions waiting for your approval. For an account prepared by an invitation, the username is the account name shown on the completion page.')}</p>
<form method="post" action="/pending">${back}<label>ユーザ名 / Username <input name="person" autocomplete="username" required maxlength="64"></label><label>承認 secret / Approval secret <input type="password" name="secret" autocomplete="current-password" required maxlength="128"></label><button type="submit">一覧を見る / Show the list</button></form>
<p>一覧は 10 分で閉じます。secret は LLM や原稿に書かないでください。${en('The list closes after 10 minutes. Never paste the secret into an LLM or a draft.')}</p>`,listTitle);
}
function listing(person,rows){
  const items=rows.map(r=>`<li><p><strong>${kindLabel[r.kind]}</strong>${en(kindEnglish[r.kind])}</p><p>アカウント / Account: ${escape(r.account)} · 添付 / Attachments: ${r.attachments?'あり / yes':'なし / none'} · 期限まで / Expires in: ${escape(r.remaining)} 分 / min</p><pre>${escape(r.head)}${r.more?'…':''}</pre><a class="go" href="/pending/${escape(r.job_id)}">開く / Open</a></li>`).join('');
  const body=rows.length?`<ul>${items}</ul>`:`<p>いま承認待ちはありません。${en('Nothing is waiting for your approval.')}</p>`;
  return page(200,`<h1>承認待ちの一覧${en('Pending approvals')}</h1><p>ユーザ名 / Username: ${escape(person)}</p>${body}
<p>「開く」で承認ページに進みます。承認ページは開いてから 10 分で失効し、承認すると一覧から消えます。${en('“Open” takes you to the approval page. It expires 10 minutes after it is opened; approved items leave the list.')}</p>
<form method="post"><input type="hidden" name="leave" value="1"><button type="submit">閉じる / Sign out</button></form>`,listTitle);
}
const sessionById=(env,id)=>env.APPROVAL_SESSION.get(env.APPROVAL_SESSION.idFromString(id));
export async function pendingRequest(request,env,url){
  try{
    if(url.search||url.hash||url.pathname.includes('%'))return reply(400,{error:'invalid_request'});
    if(!env.APPROVAL_PERSON||!env.APPROVAL_SESSION||!env.APPROVAL_ACCOUNT)return reply(503);
    // 3.12.0 §6-2: 末尾の / は落として 308（方法と本文を保ったまま /pending へ）。
    if(url.pathname==='/pending/')return new Response(null,{status:308,headers:{location:'/pending','cache-control':'no-store','referrer-policy':'no-referrer'}});
    const match=/^\/pending(?:\/([A-Za-z0-9_-]{43}))?$/.exec(url.pathname);
    if(!match)return reply(404,{error:'not_found'});
    if(!await publicQuota(request,env))return reply(429,{error:'rate_limited'});
    if(!['GET','POST'].includes(request.method))return reply(405,{error:'method_not_allowed'});
    if(request.method==='POST'&&!fromSameOrigin(request,url))return reply(403,{error:'forbidden'});
    const cookie=pendingCookie(request);
    const who=cookie?{stub:await personStub(env,cookie.person),hash:await digest(cookie.token)}:null;
    const expired=response=>{if(cookie)response.headers.append('set-cookie',setCookie('',0));return response;};
    if(!match[1]){
      if(request.method==='GET'){
        if(!who)return signIn();
        const listed=await who.stub.listView(who.hash);
        if(listed.status!==200)return expired(signIn());
        const rows=[];
        for(const row of listed.body.rows){
          try{const summary=await sessionById(env,row.session).summary(cookie.person);if(summary.status===200)rows.push(summary.body);}catch{}
        }
        return listing(cookie.person,rows);
      }
      const form=new URLSearchParams(await boundedBody(request,1024));
      const keys=[...form.keys()].sort().join(',');
      if(keys==='leave'){
        if(who)await who.stub.closeList(who.hash);
        return new Response(null,{status:303,headers:{location:'/pending','cache-control':'no-store','referrer-policy':'no-referrer','set-cookie':setCookie('',0)}});
      }
      if(keys!=='person,secret'&&keys!=='next,person,secret')return reply(400,{error:'invalid_request'});
      const next=form.has('next')?form.get('next'):null;
      if(next!==null&&!JOB_ID.test(next))return reply(400,{error:'invalid_request'});
      const person=form.get('person'),refused=()=>signIn(403,`<p><strong>入れませんでした。</strong>ユーザ名と承認 secret を確かめてください。5 回続けて間違えると一覧は 15 分閉じます。${en('Sign-in failed. Check the username and the approval secret. After five failures in a row the list is closed for 15 minutes.')}</p>`,next);
      if(!PERSON.test(person))return refused();
      const token=opaque(),opened=await (await personStub(env,person)).openList(form.get('secret'),await digest(token));
      if(opened.status!==200)return refused();
      return new Response(null,{status:303,headers:{location:next?'/pending/'+next:'/pending','cache-control':'no-store','referrer-policy':'no-referrer','set-cookie':setCookie(token+person,Math.floor(TTL/1000))}});
    }
    // 一覧から開く承認ページ。一覧に入っていて、その人の索引にある job だけ。
    if(!who)return signIn(401,'',match[1]);
    const found=await who.stub.listedSession(who.hash,match[1]);
    if(found.status===401)return expired(signIn(401,'',match[1]));
    if(found.status!==200)return approvalPage(found,true);
    const stub=sessionById(env,found.body.session);
    if(request.method==='GET')return approvalPage(await stub.view(cookie.person),true);
    const form=new URLSearchParams(await boundedBody(request,2048));
    if([...form.keys()].sort().join(',')!=='csrf,secret')return reply(400,{error:'invalid_request'});
    return approvalOutcome(await stub.approve(form.get('secret'),form.get('csrf'),cookie.person),true);
  }catch{return reply(503,{error:'approval_unavailable'});}
}
