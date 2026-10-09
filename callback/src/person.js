// VM→Worker の署名つき relay（/relay/v/{person,account,deletion,invite,activity}/…）の受け口と、
// 招待・退会・添付・削除・/activity が使う共通の部品。3.13.0 で approval.js から改名した
// （承認ページ /approve/<token> と承認待ちの一覧 /pending は無い）。旧 path の /approval/… は
// 3.12.0 の VM が deploy の間に叩くので 1 版の間だけ受ける。
import {digest, STATE_PATTERN, HASH_PATTERN, reply} from './relay.js';
import {deletionStub} from './deletion.js';
export const PERSON = /^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,63}$/;
export const TTL = 600_000;       // /activity に入った session（cookie）は 10 分
export const LIST_LOCK_MS = 900_000; // /activity に入る secret を 5 回間違えたら 15 分閉じる（時間で戻る）
export const VIEW_MAX = 8;       // 1 人の /activity の session（ブラウザ）の上限
export const ITERATIONS = 100_000; // Workers WebCrypto caps PBKDF2 at 100,000 iterations (production NotSupportedError above it).
// 遠くの道（3.14.0）: 1 回の sync で VM に渡す依頼の数と大きさの上限。
export const API_HANDOFF_MAX = 16, API_HANDOFF_BYTES = 786_432;
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
// VM の署名を確かめる公開鍵。3.13.0 で RELAY_PUBLIC_KEY に改めた。運営者が置き替えるまでは
// 旧名の APPROVAL_PUBLIC_KEY（Dashboard／wrangler secret）で動く。
export const relayPublicKey=env=>env.RELAY_PUBLIC_KEY??env.APPROVAL_PUBLIC_KEY;
export async function personStub(env,person) {return env.PERSON.getByName(await digest(person));}
export async function accountStub(env,account) {return env.ACCOUNT.getByName(await digest(account));}
// 招待（3.10.0）の object は code の SHA-256 で引く。VM は hash しか持たないので、署名の
// subject も同じ hash。ブラウザの道（/invite/<code>）はここで code を hash にしてから引く。
export function inviteStub(env,hash) {return env.INVITE_OBJECT.getByName(hash);}
// Canonical wire binding: role/subject/operation are signed, not caller-selected authority.
// 'thth-approval-v1' は署名の文字列の版名（2.12 の名残）。VM と Worker の版ずれで署名が合わなく
// ならないよう、3.13.0 の改名でも変えない。
export function canonical(method,path,role,subject,operation,time,nonce,bodyHash) {
  return ['thth-approval-v1',method,path,role,subject,operation,time,nonce,bodyHash].join('\n');
}
async function authenticate(request,env,url,raw,role,subject,operation) {
  const time=request.headers.get('x-thth-time')||'', nonce=request.headers.get('x-thth-nonce')||'';
  if(!/^\d{13}$/.test(time)||Math.abs(Date.now()-Number(time))>60_000||!STATE_PATTERN.test(nonce))return null;
  const signature=request.headers.get('x-thth-signature')||'';
  const publicKey=relayPublicKey(env);
  if(signature.length!==512||!publicKey)return null; // RSA-3072 raw signature, unpadded base64url.
  const key=await crypto.subtle.importKey('spki',unb64(publicKey),{name:'RSA-PSS',hash:'SHA-256'},false,['verify']);
  if(key.algorithm.modulusLength!==3072)return null;
  const ok=await crypto.subtle.verify({name:'RSA-PSS',saltLength:32},key,unb64(signature),encoder.encode(
    canonical(request.method,url.pathname,role,subject,operation,time,nonce,await digest(raw))));
  return ok?{nonce,time:Number(time)}:null;
}
// 画面の言葉は日本語と英語を両方入れ、上の「日本語 / English」で切り替える（2026-10-10 masaru:
// 同じ行に並べるのをやめる）。script は使えない（CSP）ので、ラジオボタンと CSS の :has で切り替える。
// 最初にどちらを選んでおくかは worker.js が Accept-Language で決める（ja 以外なら English）。
// t(ja,en) は 1 つの言い回しの組。en は審査員が読むので、どの画面にも必ず添える。
export const t=(ja,english)=>`<span class="ja" lang="ja">${ja}</span><span class="en" lang="en">${english}</span>`;
export const escape=s=>String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
// 招待の流れの段（今いる段を太く）。1 認可 2 シークレットの保存 3 thth login。
export function steps(current){
  const names=[['Threads で認可','Authorize'],['シークレットを保存','Save the secret'],['thth login','thth login']];
  return `<ol class="steps">${names.map(([ja,english],i)=>`<li${i+1===current?' aria-current="step"':i+1<current?' class="done"':''}>${t(ja,english)}</li>`).join('')}</ol>`;
}
// 静かな、読み物の画面。書体はシステムの和文ゴシック（読み込み 0・ずれ 0）。色は明暗の両方。
export const PAGE_STYLE=`:root{color-scheme:light dark;--bg:#f6f5f1;--card:#fff;--ink:#1e1d1b;--muted:#5d5a54;--line:#e2dfd8;--accent:#1d5c4c;--accent-ink:#fff;--soft:#edf3f0;--warn:#8a3a10;--warn-soft:#fbefe6;--sans:"Hiragino Sans","Hiragino Kaku Gothic ProN","Yu Gothic Medium","Yu Gothic","Noto Sans JP",system-ui,sans-serif;--mono:ui-monospace,"SF Mono",Menlo,Consolas,monospace}
@media (prefers-color-scheme:dark){:root{--bg:#141513;--card:#1c1e1b;--ink:#ecebe6;--muted:#aaa69e;--line:#33362f;--accent:#7cc5ad;--accent-ink:#0e1915;--soft:#1e2a25;--warn:#f2a87c;--warn-soft:#2c2019}}
*{box-sizing:border-box}html{background:var(--bg)}
body{margin:0;min-width:0;background:var(--bg);color:var(--ink);font-family:var(--sans);font-size:16px;line-height:1.8;line-break:strict;overflow-wrap:anywhere;-webkit-text-size-adjust:100%}
.lang{position:absolute;opacity:0;width:1px;height:1px;pointer-events:none}
.en{display:none}body:has(#lang-en:checked) .en{display:revert}body:has(#lang-en:checked) .ja{display:none}
.top,footer{max-width:40rem;margin:0 auto;padding:0 16px}
.top{display:flex;justify-content:space-between;align-items:center;min-height:4rem}
.mark{color:var(--ink);font-weight:700;letter-spacing:.12em;text-decoration:none}
.langs{display:flex;gap:2px;padding:3px;border:1px solid var(--line);border-radius:999px;background:var(--card)}
.langs label{display:block;margin:0;padding:.35rem .85rem;min-height:2.25rem;border-radius:999px;color:var(--muted);font-size:14px;font-weight:400;line-height:1.6;cursor:pointer}
body:has(#lang-ja:checked) label[for=lang-ja],body:has(#lang-en:checked) label[for=lang-en]{background:var(--ink);color:var(--card)}
#lang-ja:focus-visible~.top label[for=lang-ja],#lang-en:focus-visible~.top label[for=lang-en]{outline:3px solid var(--accent);outline-offset:2px}
main{max-width:40rem;margin:0 auto 1.5rem;padding:2.25rem 2rem;background:var(--card);border:1px solid var(--line);border-radius:16px}
footer{padding-bottom:2.5rem;color:var(--muted);font-size:13px}footer a{color:inherit;margin-right:1rem}
h1{margin:0 0 1rem;font-size:1.6rem;line-height:1.4;letter-spacing:.02em;font-feature-settings:"palt";word-break:auto-phrase;text-wrap:balance}
h2{margin:2.2rem 0 .6rem;font-size:1.1rem;line-height:1.5;word-break:auto-phrase}
p{margin:.9rem 0}ul,ol{padding-left:1.4rem}li{margin:.45rem 0}
a{color:var(--accent);text-underline-offset:.2em}
.lead{font-size:17px}.sub,.note,figcaption{color:var(--muted);font-size:14px;line-height:1.7}
.status{padding:.85rem 1rem;border-radius:10px;background:var(--soft)}
.err{padding:.85rem 1rem;border-radius:10px;background:var(--warn-soft);color:var(--warn);font-weight:600}
code{padding:.1em .35em;border-radius:6px;background:var(--soft);font-family:var(--mono);font-size:.92em}
pre,.secret,.code{display:block;margin:.8rem 0;padding:.9rem 1rem;border:1px solid var(--line);border-radius:10px;background:var(--bg);font-family:var(--mono);font-size:15px;line-height:1.6;white-space:pre-wrap;overflow-wrap:anywhere}
.secret{user-select:all;font-size:16px;letter-spacing:.02em}.code{user-select:all}
strong.once{color:var(--warn)}
form{margin:1.2rem 0}label{display:block;margin:1rem 0;font-size:15px;font-weight:600}
input:not([type=hidden]):not([type=radio]),select{display:block;width:100%;margin-top:.35rem;padding:.7rem .85rem;border:1px solid var(--line);border-radius:10px;background:var(--card);color:var(--ink);font:inherit;font-size:16px;font-weight:400}
input[readonly]{background:var(--bg)}
input:not([type=radio]):focus-visible,select:focus-visible{outline:2px solid var(--accent);outline-offset:1px;border-color:var(--accent)}
button,a.go{display:inline-flex;align-items:center;justify-content:center;min-height:48px;margin:.4rem 0;padding:.65rem 1.4rem;border:1px solid var(--accent);border-radius:12px;background:var(--accent);color:var(--accent-ink);font:inherit;font-weight:600;text-decoration:none;cursor:pointer}
button:hover,a.go:hover{filter:brightness(1.1)}button:focus-visible,a.go:focus-visible,a:focus-visible{outline:3px solid var(--accent);outline-offset:3px}
fieldset{margin:1rem 0;padding:.4rem 1rem .6rem;border:1px solid var(--line);border-radius:10px}legend{padding:0 .3rem;font-size:15px;font-weight:600}label.pick{display:flex;gap:.6rem;align-items:center;margin:.3rem 0;font-weight:400;min-height:2.25rem}label.pick input{width:1.1rem;height:1.1rem;accent-color:var(--accent)}
button.quiet{background:transparent;color:var(--ink);border-color:var(--line)}
section{margin:2rem 0 0;padding-top:1.5rem;border-top:1px solid var(--line)}
figure{margin:1rem 0}img{max-width:100%;height:auto;border-radius:8px}
.steps{display:flex;gap:.5rem;margin:0 0 1.8rem;padding:0;list-style:none;color:var(--muted);font-size:13px;line-height:1.5}
.steps li{flex:1;margin:0;padding-top:.55rem;border-top:3px solid var(--line)}
.steps li.done{border-color:var(--accent)}.steps li[aria-current]{border-color:var(--accent);color:var(--ink);font-weight:600}
.facts{display:grid;grid-template-columns:auto 1fr;gap:.3rem 1rem;margin:1rem 0;font-size:15px}.facts dt{color:var(--muted)}.facts dd{margin:0}
@media (max-width:40rem){.top,footer{padding:0 16px}main{margin:0 12px 1.25rem;padding:1.6rem 18px;border-radius:14px}h1{font-size:1.4rem}button,a.go{display:flex;width:100%}}`;
// どの画面も同じ枠（上に THTH と言語の切り替え、下にプライバシーと規約）。
export function frame(body,title,head=''){
  return '<!doctype html><html lang="ja"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex,nofollow,noarchive">'+head+'<title>'+title+'</title><style>'+PAGE_STYLE+'</style></head><body>'+
    '<input type="radio" name="lang" id="lang-ja" class="lang" checked aria-label="日本語"><input type="radio" name="lang" id="lang-en" class="lang" aria-label="English">'+
    '<header class="top"><a class="mark" href="/">THTH</a><div class="langs"><label for="lang-ja" lang="ja">日本語</label><label for="lang-en" lang="en">English</label></div></header>'+
    '<main>'+body+'</main><footer><a href="/privacy/">'+t('プライバシー','Privacy')+'</a><a href="/terms/">'+t('利用規約','Terms')+'</a></footer></body></html>';
}
// `head` は <head> に足す固定の行だけ（例: 待つページの <meta http-equiv="refresh">）。利用者の値を入れない。
export function page(status,body,title='THTH',head='') {
  return new Response(frame(body,title,head),{status,headers:{
    'content-type':'text/html; charset=utf-8','cache-control':'no-store','referrer-policy':'no-referrer',
    'content-security-policy':"default-src 'none'; img-src 'self'; style-src 'unsafe-inline'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'",
    'x-content-type-options':'nosniff','x-frame-options':'DENY'}});
}
export async function publicQuota(request,env){
  return !!env.RELAY_PUBLIC_LIMIT&&(await env.RELAY_PUBLIC_LIMIT.limit({key:await digest(request.headers.get('cf-connecting-ip')||'unknown-peer')})).success;
}
// Referrer-Policy: no-referrer makes browsers send `Origin: null` (or omit it) even on a
// same-origin form POST (Fetch spec §4.9). Same-origin is then proven by Sec-Fetch-Site;
// the per-session csrf field and CSP form-action 'self' remain the CSRF gate.
// /activity の form POST はここを通る。
export function fromSameOrigin(request,url){
  const originHeader=request.headers.get('origin'),site=request.headers.get('sec-fetch-site');
  const sameOrigin=originHeader===url.origin||((originHeader===null||originHeader==='null')&&(site===null||site==='same-origin'));
  return sameOrigin&&/^application\/x-www-form-urlencoded(?:\s*;|$)/i.test(request.headers.get('content-type')||'');
}
export async function relayRequest(request,env,url) {
  try {
    if(url.search||url.hash||url.pathname.includes('%'))return reply(400,{error:'invalid_request'});
    if(!env.PERSON||!env.ACCOUNT)return reply(503);
    // 3.13.0: /relay/v/… に改めた。旧 path /approval/… も 1 版の間だけ受ける（署名は叩かれた path で
    // 確かめるので、どちらの path でも VM が署名した path と一致しなければ通らない）。
    // 承認ページの session（…/session/…）は無い。
    const route=/^\/(?:relay\/v|approval)\/(person|account|deletion|invite|activity)\/([A-Za-z0-9_.-]+)\/(set|revoke|unlock|status|create|list|read|verify|complete|discard|cleanup-retry|authorize|reset|sync)$/.exec(url.pathname);
    if(!route||request.method!=='POST')return reply(404,{error:'not_found'});
    const [,type,subject,operation]=route;
    // 3.12.0 §3.4 アクティビティ: VM がオーナー（person）ごとの要約を押し上げ、オーナーの操作を受け取る。
    if(type==='activity'?!PERSON.test(subject)||operation!=='sync':
       type==='invite'?!HASH_PATTERN.test(subject)||!['create','status','authorize','reset','complete','revoke'].includes(operation):type==='deletion'?!(subject==='inbox'&&operation==='list'||STATE_PATTERN.test(subject)&&['read','verify','complete','discard'].includes(operation)):type==='account'?!PERSON.test(subject)||!['revoke','status','cleanup-retry'].includes(operation):!PERSON.test(subject)||!['set','revoke','unlock','status'].includes(operation))return reply(400,{error:'invalid_request'});
    if(!/^application\/json(?:\s*;|$)/i.test(request.headers.get('content-type')||''))return reply(400,{error:'invalid_request'});
    if(!env.RELAY_VERIFY_LIMIT || !(await env.RELAY_VERIFY_LIMIT.limit({key:await digest(request.headers.get('cf-connecting-ip')||'unknown-peer')})).success)return reply(429,{error:'rate_limited'});
    // アクティビティの sync はアカウント 8 つ×30 行の要約（本文は先頭 60 字だけ）と、3.14.0 からは /api/v1 の
    // 結果（1 件 128 KiB まで）を運ぶ。他は 64 KiB。
    const cap=type==='activity'?1_048_576:65_536;
    const raw=await boundedBody(request,cap), ticket=await authenticate(request,env,url,raw,'operator',subject,operation);
    if(!ticket)return reply(401,{error:'unauthorized'});
    if(!env.RELAY_JOB_LIMIT || !(await env.RELAY_JOB_LIMIT.limit({key:await digest(type+'/'+subject)})).success)return reply(429,{error:'rate_limited'});
    const body=JSON.parse(raw);
    if(type==='activity')return activityRelay(env,subject,body,ticket);
    const stub=type==='invite'?inviteStub(env,subject):type==='deletion'?deletionStub(env):type==='account'?await accountStub(env,subject):await personStub(env,subject);
    const result=await stub.manage(operation,body,ticket,subject);
    return reply(result.status,result.body);
  }catch{return reply(503,{error:'relay_unavailable'});}
}

// アクティビティの sync（3.12.0 §3.4）と遠くの道の受け渡し（3.14.0 §3.1）。Person DO が鍵の表を置き替え、
// 表に載るアカウントの束ごとに「そのオーナーの鍵の表・結果」を渡して、待っている依頼を受け取る。
// 3.13.0 の VM（`keys` を載せない）には依頼を渡さない（応答の形も今のまま）。
async function activityRelay(env,person,body,ticket){
  const result=await (await personStub(env,person)).activitySync(body,ticket);
  if(result.status!==200||!result.exchange)return reply(result.status,result.body);
  const requests=[];
  for(const [account,keys] of Object.entries(result.exchange)){
    const results=body.results.filter(r=>r.account===account);
    try{
      const got=await (await accountStub(env,account)).apiExchange(person,keys,results);
      if(got.status===200)requests.push(...got.body.requests);
    }catch{/* アカウントの束が今は使えない: そのアカウントの依頼は次の sync で渡す（結果も VM がもう一度送る）。 */}
  }
  let size=0;
  const handed=requests.filter(item=>(size+=JSON.stringify(item).length)<=API_HANDOFF_BYTES).slice(0,API_HANDOFF_MAX);
  return reply(200,{...result.body,requests:handed});
}
