// 遠くの道（設計 3.14.0 §3.1）: CLI → thth.me/api/v1/<operation> → 口座の束（Account DO）→ VM が sync で拾う。
//
//   POST /api/v1/<operation>        Authorization: Bearer <鍵>・JSON（48 KB まで）・{account, ...引数}
//   GET  /api/v1/result/<request_id> 同じ鍵
//
// - 鍵は sha256 だけを見る。照合の表は VM が sync で押し上げる（Person DO が持ち、口座の束に配る）。
//   **Worker で先に断り、権威は VM**（VM が資格情報のファイルでもう一度確かめる）。
// - 応答は最長 20 秒待つ（口座の束を 0.5 秒ごとに見る）。結果が入れば 200 で VM の JSON をそのまま。
//   20 秒を超えたら 202 {status: pending, request_id}。CLI は /api/v1/result/<request_id> を見に行く。
// - Worker が自分で断るときは {error: <符丁>} だけ。
// - **request の中身も結果も記録しない**（/activity と同じ作法: no-store・no-referrer・観測ログ無し）。
//   本文は口座の束に最長 120 秒・結果は入ってから 10 分だけ。
import {PERSON,boundedBody,accountStub,publicQuota} from './person.js';
import {digest,reply} from './relay.js';

export const API_BODY_MAX=49_152;   // 48 KB（server_writes の本文の上限と同じ桁）
export const API_HOLD_MS=20_000;    // 応答を待つ最長
export const API_POLL_MS=500;       // 口座の束を見る間隔
const OPERATION=/^[a-z][a-z0-9_]{0,47}$/;
// request_id は `<43 字の乱数>.<口座>`（結果を引くときに口座の束が分かるように）。
export const REQUEST_ID=/^([A-Za-z0-9_-]{43})\.([a-zA-Z0-9][a-zA-Z0-9_.-]{0,63})$/;
const BEARER=/^Bearer ([\x21-\x7e]{16,512})$/;
const pause=ms=>new Promise(resolve=>setTimeout(resolve,ms));

// 試験だけが短くする（本番の binding には無い・あっても 20 秒を超えない）。
function holdMs(env){
  const value=Number(env.API_HOLD_MS);
  return Number.isFinite(value)&&value>=0&&value<=API_HOLD_MS?value:API_HOLD_MS;
}
async function keyHash(request){
  const match=BEARER.exec(request.headers.get('authorization')||'');
  return match?digest(match[1]):null;
}
async function wait(stub,id,sha256,request_id,hold){
  const deadline=Date.now()+hold;
  for(;;){
    const found=await stub.apiResult(id,sha256);
    if(found.status===200)return reply(200,found.body);
    if(found.status!==202)return reply(found.status,found.body);
    const left=deadline-Date.now();
    if(left<=0)return reply(202,{status:'pending',request_id});
    await pause(Math.min(API_POLL_MS,left));
  }
}

export async function apiRequest(request,env,url){
  try{
    if(url.search||url.hash||url.pathname.includes('%'))return reply(400,{error:'invalid_request'});
    if(!env.ACCOUNT||!env.API_KEY_LIMIT)return reply(503,{error:'api_unavailable'});
    const result=/^\/api\/v1\/result\/([^/]+)$/.exec(url.pathname);
    const operation=result?null:/^\/api\/v1\/([^/]+)$/.exec(url.pathname)?.[1];
    if(!result&&(!operation||!OPERATION.test(operation)))return reply(404,{error:'not_found'});
    if(request.method!==(result?'GET':'POST')||operation==='result')return reply(405,{error:'method_not_allowed'});
    // 回数制限: IP で 120 回／分（公開の口と同じ）、鍵ごとに 60 回／分。
    if(!await publicQuota(request,env))return reply(429,{error:'rate_limited'});
    const sha256=await keyHash(request);
    if(!sha256)return reply(401,{error:'invalid_key'});
    if(!(await env.API_KEY_LIMIT.limit({key:sha256})).success)return reply(429,{error:'rate_limited'});
    if(result){
      const id=REQUEST_ID.exec(result[1]);
      if(!id)return reply(404,{error:'not_found'});
      const stub=await accountStub(env,id[2]);
      const key=await stub.apiKey(sha256);
      if(key.status!==200)return reply(key.status,key.body);
      return wait(stub,id[1],sha256,result[1],0);
    }
    if(!/^application\/json(?:\s*;|$)/i.test(request.headers.get('content-type')||''))return reply(400,{error:'invalid_request'});
    let body;
    try{body=JSON.parse(await boundedBody(request,API_BODY_MAX));}
    catch(error){return error?.message==='too_large'?reply(413,{error:'too_large'}):reply(400,{error:'invalid_request'});}
    // operation は path から。body に operation を書いて別の口に化けさせない。
    if(body===null||typeof body!=='object'||Array.isArray(body)||Object.hasOwn(body,'operation')||
       typeof body.account!=='string'||!PERSON.test(body.account))return reply(400,{error:'invalid_request'});
    const stub=await accountStub(env,body.account);
    const placed=await stub.apiSubmit(sha256,operation,body);
    if(placed.status!==200)return reply(placed.status,placed.body);
    return wait(stub,placed.body.id,sha256,placed.body.id+'.'+body.account,holdMs(env));
  }catch{return reply(503,{error:'api_unavailable'});}
}
