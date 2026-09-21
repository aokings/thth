// Media signatures deliberately cannot authorize approval/operator operations.
import {digest,STATE_PATTERN,HASH_PATTERN,reply} from './relay.js';
import {boundedBody,unb64} from './approval.js';
export const MEDIA_TTL=600_000, RAW_RETENTION=86_400_000;
export const MEDIA_NAME=/^[A-Za-z0-9_][A-Za-z0-9_.-]{0,63}$/;
export const MIME=new Set(['image/jpeg','image/png','image/webp','image/gif','video/mp4','video/quicktime']);
export const MULTIPART_THRESHOLD=100_000_000, MIN_PART=5*1024*1024;
export const fail=(status=400,error='invalid_media_request')=>({status,body:{error}});
export const validHash=v=>typeof v==='string'&&HASH_PATTERN.test(v);
export const validOpaque=v=>typeof v==='string'&&STATE_PATTERN.test(v);
export const keys=(b,fields)=>b&&typeof b==='object'&&!Array.isArray(b)&&Object.keys(b).sort().join(',')===[...fields].sort().join(',');
export const mediaStub=async(env,id)=>env.MEDIA_OBJECT.getByName(await digest(id));
async function allowed(binding,key){if(!binding)throw Error('media_unavailable');return (await binding.limit({key:await digest(key)})).success===true;}
export function canonical(method,path,subject,operation,time,nonce,bodyHash){
  return ['thth-media-v1',method,path,'media',subject,operation,time,nonce,bodyHash].join('\n');
}
async function authenticate(request,env,url,raw,id,op){
  const time=request.headers.get('x-thth-time')||'',nonce=request.headers.get('x-thth-nonce')||'',sig=request.headers.get('x-thth-signature')||'';
  if(!/^\d{13}$/.test(time)||Math.abs(Date.now()-Number(time))>60_000||!validOpaque(nonce)||sig.length!==512||!env.APPROVAL_PUBLIC_KEY)return null;
  const key=await crypto.subtle.importKey('spki',unb64(env.APPROVAL_PUBLIC_KEY),{name:'RSA-PSS',hash:'SHA-256'},false,['verify']);
  if(key.algorithm.modulusLength!==3072)return null;
  const ok=await crypto.subtle.verify({name:'RSA-PSS',saltLength:32},key,unb64(sig),new TextEncoder().encode(canonical(request.method,url.pathname,id,op,time,nonce,await digest(raw))));
  return ok?{nonce,time:Number(time)}:null;
}
export async function mediaRequest(request,env,url){
  try{
    if(url.search||url.hash||url.pathname.includes('%'))return url.pathname.startsWith('/m/')?reply(404,{error:'not_found'}):reply(400,{error:'invalid_media_request'});
    if(!env.MEDIA_OBJECT||!env.MEDIA_BUCKET)return reply(503,{error:'media_unavailable'});
    let match=/^\/media\/([A-Za-z0-9_-]{43})\/(create|complete|read|ack|preview|provider|published|invalidate|status)$/.exec(url.pathname);
    if(match){
      if(request.method!=='POST')return reply(405,{error:'method_not_allowed'});
      if(!await allowed(env.MEDIA_CONTROL_LIMIT,request.headers.get('cf-connecting-ip')||'unknown-peer'))return reply(429,{error:'rate_limited'});
      if(!/^application\/json(?:\s*;|$)/i.test(request.headers.get('content-type')||''))return reply(400,{error:'invalid_media_request'});
      const [,id,op]=match,raw=await boundedBody(request,16384),ticket=await authenticate(request,env,url,raw,id,op);
      if(!ticket)return reply(401,{error:'unauthorized'});
      const stub=await mediaStub(env,id),result=await stub.manage(op,JSON.parse(raw),ticket);
      return result instanceof Response?result:reply(result.status,result.body);
    }
    match=/^\/media-upload\/([A-Za-z0-9_-]{43})(?:\/(\d+))?$/.exec(url.pathname);
    if(match){
      if(request.method!=='PUT')return reply(405,{error:'method_not_allowed'});
      if(!await allowed(env.MEDIA_UPLOAD_LIMIT,match[1]))return reply(429,{error:'rate_limited'});
      return await(await mediaStub(env,match[1])).upload(request,match[2]===undefined?null:Number(match[2]));
    }
    match=/^\/m\/([A-Za-z0-9_-]{43})$/.exec(url.pathname);
    if(match){
      if(!['GET','HEAD'].includes(request.method))return reply(405,{error:'method_not_allowed'});
      if(!await allowed(env.MEDIA_PUBLIC_LIMIT,request.headers.get('cf-connecting-ip')||'unknown-peer'))return reply(429,{error:'rate_limited'});
      return await(await mediaStub(env,match[1])).view(request);
    }
    return reply(404,{error:'not_found'});
  }catch{return reply(503,{error:'media_unavailable'});}
}
