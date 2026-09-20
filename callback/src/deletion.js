import {boundedBody,fields,opaque,fail} from './approval.js';
import {STATE_PATTERN,digest,reply} from './relay.js';
export const RETENTION=30*24*60*60*1000;
export const deletionStub=env=>env.DELETION_INBOX.getByName('pending-receipts-v1');
export async function deletionRequest(request,env,url){
  try{
    if(!env.DELETION_INBOX||!env.DELETION_PUBLIC_LIMIT)return reply(503,{error:'deletion_unavailable'});
    if(!(await env.DELETION_PUBLIC_LIMIT.limit({key:await digest(request.headers.get('cf-connecting-ip')||'unknown-peer')})).success)return reply(429,{error:'rate_limited'});
    if(url.pathname==='/data-deletion'){
      if(request.method!=='POST'||url.search||!/^application\/x-www-form-urlencoded(?:\s*;|$)/i.test(request.headers.get('content-type')||''))return reply(400,{error:'invalid_request'});
      const form=new URLSearchParams(await boundedBody(request,16384));
      if([...form.keys()].join(',')!=='signed_request')return reply(400,{error:'invalid_request'});
      const result=await deletionStub(env).receive(form.get('signed_request'));
      if(result.status!==200)return reply(result.status,result.body);
      return reply(200,{url:'https://thth.me/data-deletion-status?code='+result.body.confirmation_code,confirmation_code:result.body.confirmation_code});
    }
    const code=url.searchParams.get('code');
    if(request.method!=='GET'||[...url.searchParams.keys()].join(',')!=='code'||!code||!STATE_PATTERN.test(code))return reply(404,{error:'not_found'});
    const result=await deletionStub(env).status(code);
    return reply(result.status,result.body);
  }catch{return reply(503,{error:'deletion_unavailable'});}
}
