// 3.13.0: 承認ページ（/approve/<token>）と承認待ちの一覧（/pending）は無い。ここに残るのは
// VM→Worker の署名つき relay（/approval/{person,account,deletion,invite,activity}/…）と、
// 招待・退出・添付・削除・/activity が使う共通の部品。名前（approval.js）は段 2 で改める。
import {digest, STATE_PATTERN, HASH_PATTERN, reply} from './relay.js';
import {deletionStub} from './deletion.js';
export const PERSON = /^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,63}$/;
export const TTL = 600_000;       // /activity に入った session（cookie）は 10 分
export const LIST_LOCK_MS = 900_000; // /activity に入る secret を 5 回間違えたら 15 分閉じる（時間で戻る）
export const VIEW_MAX = 8;       // 1 人の /activity の session（ブラウザ）の上限
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
// 日本語の下に英語を 1 行ずつ添える（招待のページ・/activity と同じ作り・3.10.0）。
export const en=text=>`<span class="en">${text}</span>`;
export const escape=s=>String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
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
// /activity の form POST はここを通る。
export function fromSameOrigin(request,url){
  const originHeader=request.headers.get('origin'),site=request.headers.get('sec-fetch-site');
  const sameOrigin=originHeader===url.origin||((originHeader===null||originHeader==='null')&&(site===null||site==='same-origin'));
  return sameOrigin&&/^application\/x-www-form-urlencoded(?:\s*;|$)/i.test(request.headers.get('content-type')||'');
}
export async function approvalRequest(request,env,url) {
  try {
    if(url.search||url.hash||url.pathname.includes('%'))return reply(400,{error:'invalid_request'});
    if(!env.APPROVAL_PERSON||!env.APPROVAL_ACCOUNT)return reply(503);
    // 3.13.0: 承認ページの session（/approval/session/…）は無い。
    const route=/^\/approval\/(person|account|deletion|invite|activity)\/([A-Za-z0-9_.-]+)\/(set|revoke|unlock|status|create|list|read|verify|complete|discard|cleanup-retry|authorize|reset|sync)$/.exec(url.pathname);
    if(!route||request.method!=='POST')return reply(404,{error:'not_found'});
    const [,type,subject,operation]=route;
    // 3.12.0 §3.4 動きの一覧: VM が持ち主（person）ごとの要約を押し上げ、持ち主の操作を受け取る。
    if(type==='activity'?!PERSON.test(subject)||operation!=='sync':
       type==='invite'?!HASH_PATTERN.test(subject)||!['create','status','authorize','reset','complete','revoke'].includes(operation):type==='deletion'?!(subject==='inbox'&&operation==='list'||STATE_PATTERN.test(subject)&&['read','verify','complete','discard'].includes(operation)):type==='account'?!PERSON.test(subject)||!['revoke','status','cleanup-retry'].includes(operation):!PERSON.test(subject)||!['set','revoke','unlock','status'].includes(operation))return reply(400,{error:'invalid_request'});
    if(!/^application\/json(?:\s*;|$)/i.test(request.headers.get('content-type')||''))return reply(400,{error:'invalid_request'});
    if(!env.APPROVAL_VERIFY_LIMIT || !(await env.APPROVAL_VERIFY_LIMIT.limit({key:await digest(request.headers.get('cf-connecting-ip')||'unknown-peer')})).success)return reply(429,{error:'rate_limited'});
    // 動きの一覧の sync は口座 8 つ×30 行の要約を運ぶ（本文は先頭 60 字だけ）。他は 64 KiB。
    const cap=type==='activity'?131_072:65_536;
    const raw=await boundedBody(request,cap), ticket=await authenticate(request,env,url,raw,'operator',subject,operation);
    if(!ticket)return reply(401,{error:'unauthorized'});
    if(!env.APPROVAL_JOB_LIMIT || !(await env.APPROVAL_JOB_LIMIT.limit({key:await digest(type+'/'+subject)})).success)return reply(429,{error:'rate_limited'});
    const body=JSON.parse(raw);
    if(type==='activity'){const result=await (await personStub(env,subject)).activitySync(body,ticket);return reply(result.status,result.body);}
    const stub=type==='invite'?inviteStub(env,subject):type==='deletion'?deletionStub(env):type==='account'?await accountStub(env,subject):await personStub(env,subject);
    const result=await stub.manage(operation,body,ticket,subject);
    return reply(result.status,result.body);
  }catch{return reply(503,{error:'approval_unavailable'});}
}
