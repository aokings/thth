// Approval capabilities are never emitted outside their dedicated page/URL.
import {digest, STATE_PATTERN, HASH_PATTERN, reply} from './relay.js';
import {deletionStub} from './deletion.js';
export const PERSON = /^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,63}$/;
export const TTL = 600_000;
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
const kindLabel={approve:'原稿を承認',send:'この内容を公開',retract:'この投稿を削除'};
const accepted={approve:'原稿の承認を受け付けました',send:'公開の承認を受け付けました',retract:'削除の承認を受け付けました'};
const labels={media:'媒体',reply_to:'返信先',publish_at:'公開予定',target:'削除する投稿',reason:'削除理由',topic:'話題',options:'公開オプション'};
const escape=s=>String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
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
    const image=row.preview===null||row.preview===undefined?'':'<img src="/m/'+escape(row.preview)+'" alt="'+escape(row.alt)+'">';
    return '<figure>'+image+'<figcaption>'+head+': '+escape(row.format.toUpperCase())+' '+shape+' · '+sha+alt+'</figcaption></figure>';
  }
  const length=row.duration===null||row.duration===undefined?'長さ: 未取得':clock(row.duration);
  return '<p>'+head+': '+escape(kindWord[row.kind])+' '+length+' · '+sha+alt+'</p>';
}
function attachments(rows,typed){
  const list=Array.isArray(rows)?rows.map(attachment).join(''):'';
  const structured=typeof typed==='string'&&typed?'<p>型付き添付／公開設定</p><pre>'+escape(typed)+'</pre>':'';
  return list+structured;
}
function page(status,body) {
  return new Response('<!doctype html><html lang="ja"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex,nofollow,noarchive"><title>THTH 承認</title><style>body{overflow-wrap:anywhere;max-width:44rem;margin:2rem auto;padding:0 1rem;font:1rem/1.7 system-ui}pre{white-space:pre-wrap;overflow-wrap:anywhere;font:inherit;border:1px solid;padding:1rem}figure{margin:1rem 0}img{max-width:100%;height:auto;border:1px solid}figcaption{font-size:.9rem}input{max-width:100%;font:inherit}button{display:block;margin:1rem 0;padding:.6rem 1.4rem;font:inherit}</style><body>'+body+'</body></html>',{status,headers:{
    'content-type':'text/html; charset=utf-8','cache-control':'no-store','referrer-policy':'no-referrer',
    'content-security-policy':"default-src 'none'; img-src 'self'; style-src 'unsafe-inline'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'",
    'x-content-type-options':'nosniff','x-frame-options':'DENY'}});
}
export async function approvalRequest(request,env,url) {
  try {
    if(url.search||url.hash||url.pathname.includes('%'))return reply(400,{error:'invalid_request'});
    if(!env.APPROVAL_PERSON||!env.APPROVAL_SESSION||!env.APPROVAL_ACCOUNT)return reply(503);
    const browser=/^\/approve\/([A-Za-z0-9_-]{43})$/.exec(url.pathname);
    if(browser){
      if(!env.APPROVAL_PUBLIC_LIMIT || !(await env.APPROVAL_PUBLIC_LIMIT.limit({key:await digest(request.headers.get('cf-connecting-ip')||'unknown-peer')})).success)return reply(429,{error:'rate_limited'});
      if(!['GET','POST'].includes(request.method))return reply(405,{error:'method_not_allowed'});
      const stub=await sessionStub(env,browser[1]);
      if(request.method==='GET'){
        const result=await stub.view();if(result.status!==200)return page(result.status,'<h1>承認ページは無効です</h1><p>サーバから新しく承認を求めてください。</p>');
        const d=result.body;
        return page(200,`<h1>${kindLabel[d.kind]}</h1><p>アカウント: ${escape(d.account)}</p><pre>${escape(d.text)}</pre>${attachments(d.attachments,d.typed)}${Object.entries(d.context).filter(([,v])=>v!==null).map(([k,v])=>`<p>${labels[k]}: ${escape(v)}</p>`).join('')}<p>digest: ${escape(d.digest)}</p><p>10 分で失効します。</p><form method="post"><input type="hidden" name="csrf" value="${escape(d.csrf)}"><label>承認 secret <input type="password" name="secret" autocomplete="current-password" required maxlength="128"></label><button type="submit">承認</button></form>`);
      }
      if(request.headers.get('origin')!==url.origin||!/^application\/x-www-form-urlencoded(?:\s*;|$)/i.test(request.headers.get('content-type')||''))return reply(403,{error:'forbidden'});
      const form=new URLSearchParams(await boundedBody(request,2048));
      if([...form.keys()].sort().join(',')!=='csrf,secret')return reply(400,{error:'invalid_request'});
      const result=await stub.approve(form.get('secret'),form.get('csrf'));
      return page(result.status,result.status===200?`<h1>${accepted[result.body.kind]}</h1><p>サーバが内容を再確認します。操作の完了は元のセッションで確認してください。</p>`:'<h1>承認できませんでした</h1><p>承認 secret または有効期限を確認してください。繰り返し失敗すると管理者による解除が必要です。</p>');
    }
    const route=/^\/approval\/(person|session|account|deletion)\/([A-Za-z0-9_.-]+)\/(set|revoke|unlock|status|create|consume|cancel|list|read|verify|complete|discard|cleanup-retry)$/.exec(url.pathname);
    if(!route||request.method!=='POST')return reply(404,{error:'not_found'});
    const [,type,subject,operation]=route;
    if(type==='deletion'?!(subject==='inbox'&&operation==='list'||STATE_PATTERN.test(subject)&&['read','verify','complete','discard'].includes(operation)):type==='account'?!PERSON.test(subject)||!['revoke','status','cleanup-retry'].includes(operation):type==='person'?!PERSON.test(subject)||!['set','revoke','unlock','status'].includes(operation):!STATE_PATTERN.test(subject)||!['create','consume','status','cancel'].includes(operation))return reply(400,{error:'invalid_request'});
    if(!/^application\/json(?:\s*;|$)/i.test(request.headers.get('content-type')||''))return reply(400,{error:'invalid_request'});
    if(!env.APPROVAL_VERIFY_LIMIT || !(await env.APPROVAL_VERIFY_LIMIT.limit({key:await digest(request.headers.get('cf-connecting-ip')||'unknown-peer')})).success)return reply(429,{error:'rate_limited'});
    // Only the session `create` body carries attachments (第 8 段), so only it
    // gets the larger cap; every other JSON route keeps the 64 KiB default.
    const cap=type==='session'&&operation==='create'?98_304:65_536;
    const raw=await boundedBody(request,cap), ticket=await authenticate(request,env,url,raw,type==='session'?'job':'operator',subject,operation);
    if(!ticket)return reply(401,{error:'unauthorized'});
    if(!env.APPROVAL_JOB_LIMIT || !(await env.APPROVAL_JOB_LIMIT.limit({key:await digest(type+'/'+subject)})).success)return reply(429,{error:'rate_limited'});
    const body=JSON.parse(raw);
    const stub=type==='deletion'?deletionStub(env):type==='account'?await accountStub(env,subject):type==='person'?await personStub(env,subject):await sessionStub(env,subject);
    const result=await stub.manage(operation,body,ticket,subject);
    return reply(result.status,result.body);
  }catch{return reply(503,{error:'approval_unavailable'});}
}
