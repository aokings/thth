// リモート MCP の口 `POST https://thth.me/mcp`（段 K・設計 共通の層 §6.1）。形は monban edge/src/mcp.js に合わせた。
//
// Streamable HTTP（MCP 仕様 2025-11-25）・JSON-RPC 2.0・状態を持たない・SSE なし（POST だけ）。Bearer はコネクタの access token
// （oauth.js）。token がこの 1 回だけレーンの鍵を開き、頼みは括りの束（段 2・`/api/v1` と同じ `workspaceCall`）を通って
// VM の通り道（段 3 の `ws_command`）に届く。権威は VM（通すか・旗の検めは `server_passthrough`）。
//
// - 道具は `mcp-tools.js`（Python の `thth/mcp_remote_tools.py` が通り道の表から作る・ずれたら試験が赤）と `thth_result`。
// - 書く道具（approve・revoke・withdraw）は二段: confirm を省いた 1 段目の答えをこの接続に返したことを Connector に残し、
//   2 段目はその控えが無ければ VM に頼まない（人に見せる前に書かない）。読むだけの接続には書く道具を出さず、呼べば断る。
// - HTTP の誤りは運び方のためだけ: token が無い・効かないときの 401（client が sign-in を始める）、形の違う頼み、429・503。
//   THTH の断りは道具の結果（isError・符丁・次の一手）。鍵の反映待ちは 401 にしない（client が再ログインを始めてしまう）。
import {boundedBody,personStub,publicQuota,opaque} from './person.js';
import {digest,reply} from './relay.js';
import {workspaceCall,workspaceResult,WORKSPACE_REQUEST_ID} from './api.js';
import {connectorStub,unauthorized} from './oauth.js';
import {splitToken} from './connector-object.js';
import TABLE from './mcp-tools.js';

export const PROTOCOLS=['2025-11-25','2025-06-18','2025-03-26'];
const BODY_MAX=65_536;
const KEY_PENDING_MS=10*60_000;   // 発行したての鍵は VM の次の sync を待つ
export const TOOLS=TABLE.tools;
const BY_NAME=Object.fromEntries(TOOLS.map(tool=>[tool.name,tool]));
const NEXT={
  key_pending:'THTH のサーバーがまだこのコネクタの鍵を括りに結び付けていません。数十秒後にもう一度呼んでください / try again in a few tens of seconds',
  pending:'VM の答えがまだです。retry_after_seconds のあとに thth_result を同じ request_id で呼ぶ（書く道具を送り直さない） / call thth_result later, never resend',
  writes_not_allowed:'このコネクタは読むだけで許されています。書くには持ち主が同意の頁で「読むだけ」を外して許し直す / this connector is read only',
  confirm_required:'1 段目（confirm を省いた同じ引数）を先に呼び、その中身を持ち主に見せてから confirm を付ける / call the first step and show it to the person first',
  confirm_mismatch:'1 段目が返した confirm の値をそのまま付ける / pass the confirm value the first step returned',
  workspace_mismatch:'答えた括りがこのコネクタの括りと違うので中身を出していません。持ち主に知らせ、コネクタを許し直す / reconnect the connector',
  connector_unbound:'このコネクタの鍵の発行の記録が切れました。持ち主にコネクタの接続をやり直してもらう / reconnect the connector',
  connector_key_invalid:'このコネクタの鍵はもう使えません。持ち主にコネクタの接続をやり直してもらう / reconnect the connector',
  forbidden:'その request_id はこのコネクタの括りの物ではありません / that request belongs to another workspace',
  invalid_request:'引数を道具の schema と照らす / check the arguments against the tool schema',
  unsupported_operation:'tools/list の道具を使う / pick a tool from tools/list',
  rate_limited:'1 分待ってからもう一度 / wait a minute',
  workspace_not_owned:'その括りの持ち主ではありません（または、その名の括りはありません）。持ち主にコネクタの接続をやり直してもらう',
  workspace_required:'持ち主の括りが 2 つ以上あります。同意の頁で括りを入れて接続をやり直してもらう',
  workspace_has_no_accounts:'その括りにはまだ口座がありません',
};
const COACH='THTH の道具です。あなたは持ち主のために動きます。書く道具（approve・revoke・withdraw）は 2 段: confirm を省いて呼ぶと何をするか（approve は本文の全文）と確認の値だけが返ります。'+
  'その中身を省かず持ち主に見せ、そうすると言われたときだけ、同じ引数に confirm を付けて呼んでください。断りは {error, next} です。next に従ってください。'+
  'pending が返ったら、あとで thth_result を同じ request_id で呼び、書く道具を送り直さないでください。';
const RESULT_TOOL={name:'thth_result',title:'thth_result',description:'前に pending が返った道具の答えを request_id で取り出す（読むだけ）。まだなら少し置いてもう一度。書く道具を送り直さない',
  inputSchema:{type:'object',properties:{request_id:{type:'string',maxLength:96}},required:['request_id'],additionalProperties:false},
  annotations:{readOnlyHint:true,openWorldHint:false}};
const rpc=(id,result)=>({jsonrpc:'2.0',id,result});
const rpcError=(id,code,message)=>({jsonrpc:'2.0',id,error:{code,message}});
const json=(status,body)=>new Response(JSON.stringify(body),{status,headers:{'content-type':'application/json','cache-control':'no-store','referrer-policy':'no-referrer'}});
const toolText=value=>({content:[{type:'text',text:JSON.stringify(value)}],structuredContent:value});
const refusal=(error,extra={})=>({...toolText({error,...extra,next:extra.next??NEXT[error]??'THTH の断りです。reason と THTH の説明に従う'}),isError:true});
const describe=tool=>({name:tool.name,title:tool.name,description:tool.description,inputSchema:tool.inputSchema,annotations:tool.annotations});

// 道具の引数を argv に組む（形の検めはここでも 1 度。最後の検めは VM の `server_passthrough._parse`）。
export function buildArgv(tool,args,{withConfirm}={}){
  const props=tool.inputSchema.properties,required=tool.inputSchema.required??[];
  if(!args||typeof args!=='object'||Array.isArray(args))return null;
  for(const key of Object.keys(args))if(!Object.hasOwn(props,key))return null;
  for(const key of required)if(!Object.hasOwn(args,key))return null;
  const out=[tool.command];
  const map=tool.argv;
  for(const [name,value] of Object.entries(args)){
    const schema=props[name];
    if(schema.type==='boolean'){if(typeof value!=='boolean')return null;continue;}
    if(schema.type==='array'){
      if(!Array.isArray(value)||value.length<(schema.minItems??0)||value.length>(schema.maxItems??Infinity)||
         !value.every(v=>typeof v==='string'&&v.length>=1&&v.length<=(schema.items.maxLength??300)))return null;
      continue;
    }
    if(typeof value!=='string'||value.length>(schema.maxLength??200)||(schema.minLength&&value.length<schema.minLength))return null;
    if(schema.enum&&!schema.enum.includes(value))return null;
    if(schema.pattern&&!new RegExp(schema.pattern).test(value))return null;
  }
  for(const [name,flag] of Object.entries(map.values)){
    if(!Object.hasOwn(args,name))continue;
    if(name==='confirm'&&!withConfirm)continue;
    out.push(flag+'='+args[name]);
  }
  for(const [name,flag] of Object.entries(map.texts))if(Object.hasOwn(args,name))out.push(flag+'='+args[name]);
  for(const [name,flag] of Object.entries(map.flags))if(args[name]===true)out.push(flag);
  out.push(...map.always);
  if(map.positional==='name'&&Object.hasOwn(args,'name'))out.push(args.name);
  if(map.positional==='files')out.push(...args.files);
  return out;
}
// 1 段目と 2 段目を結ぶ hash（接続・道具・confirm を除いた引数）。
async function confirmHash(device_id,name,args){
  const {confirm:_,...rest}=args;
  const canonical=JSON.stringify(Object.keys(rest).sort().map(k=>[k,rest[k]]));
  return digest(device_id+'\n'+name+'\n'+canonical);
}

export async function mcpRequest(request,env,url){
  try{
    if(url.search||url.hash||url.pathname!=='/mcp')return reply(404,{error:'not_found'});
    if(!env.CONNECTOR||!env.ACCOUNT||!env.PERSON||!env.API_KEY_LIMIT)return reply(503,{error:'mcp_unavailable'});
    if(request.method!=='POST')return new Response(null,{status:405,headers:{allow:'POST','cache-control':'no-store'}});
    if(!await publicQuota(request,env))return reply(429,{error:'rate_limited'});
    // token が無い・形が違う・期限切れ・取り消し済みは、どれも同じ 401（中身を出さない）。
    const m=/^Bearer ([A-Za-z0-9_.-]{66})$/.exec(request.headers.get('authorization')||'');
    if(!m||!splitToken(m[1]))return unauthorized(url.origin);
    const device=await connectorStub(env).resolve(m[1]);
    if(device.status!==200)return unauthorized(url.origin);
    if(!/^application\/json(?:\s*;|$)/i.test(request.headers.get('content-type')||''))return json(400,rpcError(null,-32700,'application/json expected'));
    let message;try{message=JSON.parse(await boundedBody(request,BODY_MAX));}catch{return json(400,rpcError(null,-32700,'parse error'));}
    if(Array.isArray(message))return json(400,rpcError(null,-32600,'batches are not supported'));
    if(!message||typeof message!=='object'||message.jsonrpc!=='2.0'||typeof message.method!=='string')return json(400,rpcError(null,-32600,'invalid request'));
    const id=Object.hasOwn(message,'id')?message.id:null,params=message.params??{};
    if(message.method.startsWith('notifications/'))return new Response(null,{status:202,headers:{'cache-control':'no-store'}});
    const c={env,...device.body};
    if(message.method==='initialize'){
      const asked=typeof params.protocolVersion==='string'&&PROTOCOLS.includes(params.protocolVersion)?params.protocolVersion:PROTOCOLS[0];
      return json(200,rpc(id,{protocolVersion:asked,capabilities:{tools:{listChanged:false}},
        serverInfo:{name:'thth',title:'THTH',version:env.THTH_EDGE_VERSION||'0'},
        instructions:`${COACH}（範囲: ${c.writes?'読む・書く':'読むだけ'}）`}));
    }
    if(message.method==='ping')return json(200,rpc(id,{}));
    if(message.method==='tools/list'){
      // 読むだけの接続には書く道具を出さない（呼ばれても下で断る）。鍵の反映前も同じ一覧（表は Worker にある）。
      return json(200,rpc(id,{tools:[...TOOLS.filter(tool=>c.writes||!tool.writes).map(describe),RESULT_TOOL]}));
    }
    if(message.method!=='tools/call')return json(200,rpcError(id,-32601,'method not found'));
    if(!params||typeof params!=='object'||typeof params.name!=='string')return json(200,rpcError(id,-32602,'invalid params'));
    const args=params.arguments&&typeof params.arguments==='object'&&!Array.isArray(params.arguments)?params.arguments:{};
    return json(200,rpc(id,await call(params.name,args,c)));
  }catch{return reply(503,{error:'mcp_unavailable'});}
}

// 括りと頼みの宛先の口座を決める（VM がレーンの鍵を作ったあと Person DO にある・1 度決まれば Connector に控える）。
async function bound(c){
  if(c.workspace&&c.account)return null;
  let outcome;
  try{outcome=await (await personStub(c.env,c.owner)).laneOutcome(c.action);}catch{outcome={status:503};}
  if(outcome.status===404)return refusal('connector_unbound');
  if(outcome.status!==200||outcome.body.outcome==='pending')return refusal('key_pending');
  if(outcome.body.outcome==='failed')return refusal(outcome.body.reason||'connector_unbound');
  const ws=outcome.body.workspace,account=outcome.body.account;
  const kept=await connectorStub(c.env).bind(c.device_id,ws,account);
  if(kept.status===409)return refusal('workspace_mismatch');
  if(kept.status!==200)return refusal('key_pending');
  c.workspace=ws;c.account=account;
  return null;
}

async function call(name,args,c){
  const tool=BY_NAME[name];
  if(!tool&&name!=='thth_result')return refusal('unsupported_operation');
  // 読むだけの接続の書く道具は、VM に頼む前に断る（VM の鍵も writes: false なのでそこでも断る）。
  if(tool?.writes&&!c.writes)return refusal('writes_not_allowed');
  const unbound=await bound(c);if(unbound)return unbound;
  const sha256=await digest(c.key);
  if(name==='thth_result'){
    const rid=typeof args.request_id==='string'?WORKSPACE_REQUEST_ID.exec(args.request_id):null;
    if(!rid||Object.keys(args).length!==1)return refusal('invalid_request');
    // ほかの括りの束の結果は引かない（鍵もその束には無いが、問う前に断る）。
    if(rid[2]!==c.workspace)return refusal('forbidden');
    return finish(await workspaceResult(c.env,sha256,args.request_id),c,null);
  }
  const argv=buildArgv(tool,args,{withConfirm:tool.confirm==='vm'});
  if(!argv)return refusal('invalid_request');
  let hash=null,first=false;
  if(tool.writes){
    hash=await confirmHash(c.device_id,name,args);
    if(!Object.hasOwn(args,'confirm')){
      first=true;
      if(tool.confirm==='worker'){
        // revoke・withdraw の CLI に 2 段は無いので、1 段目は Worker が答える（VM には何も頼まない）。
        const value=opaque();
        const noted=await connectorStub(c.env).noteConfirm(c.device_id,hash,value);
        if(noted.status!==200)return refusal('mcp_unavailable',{next:'何もしていません。1 回だけやり直す'});
        const {account,files,...rest}=args;
        return toolText({status:'confirm_required',command:tool.command,account,files,...rest,workspace:c.workspace,confirm:value,
          next:'この中身（口座・原稿・理由）を持ち主に見せ、そうすると言われたら同じ引数に confirm を付けて呼ぶ / show this to the person, then call again with confirm'});
      }
    }else{
      const taken=await connectorStub(c.env).takeConfirm(c.device_id,hash,tool.confirm==='worker'?args.confirm:null);
      if(taken.status!==200)return refusal(taken.body?.error==='confirm_mismatch'?'confirm_mismatch':'confirm_required');
    }
  }
  const answered=await workspaceCall(c.env,sha256,c.workspace,TABLE.operation,{account:c.account,argv});
  // approve の 1 段目は VM が本文と digest を返す（何も書かない）。返せた（または pending）ときだけ 2 段目を開ける。
  if(first&&(answered.status===200&&!answered.body?.error&&answered.body?.rc===0||answered.status===202))
    await connectorStub(c.env).noteConfirm(c.device_id,hash,null);
  return finish(answered,c,tool);
}

function finish({status,body},c,tool){
  if(status===202)return {...toolText({status:'pending',request_id:body?.request_id,retry_after_seconds:20,next:NEXT.pending}),isError:false};
  if(status===401&&(body?.error==='invalid_key'||body?.error==='key_expired'))
    return Date.now()-c.created_at<KEY_PENDING_MS&&body.error==='invalid_key'?refusal('key_pending'):refusal('connector_key_invalid');
  if(status===429)return refusal('rate_limited');
  if(status!==200||!body||typeof body!=='object'||Array.isArray(body))return refusal(typeof body?.error==='string'?body.error:'mcp_unavailable');
  // VM の断り（HTTP 200 の {error}）。目録の文は Worker の reply が付けない形なので、次の一手をここで足す。
  if(typeof body.error==='string'&&body.error){const {error,...rest}=body;return refusal(error,rest);}
  // 答えの括りが接続の括りと違えば中身を出さない（取り違えた括りの中身を見せない・段 2 の workspace_mismatch と同じ）。
  if(body.workspace!==c.workspace)return refusal('workspace_mismatch');
  let output=body.stdout;
  if(typeof output==='string'){try{output=JSON.parse(output);}catch{/* JSON でない出力は文字のまま */}}
  const value={command:body.command,rc:body.rc,output,...(body.stderr?{stderr:body.stderr}:{}),...(body.truncated?{truncated:true}:{}),workspace:c.workspace};
  return body.rc===0?toolText(value):{...toolText(value),isError:true};
}
