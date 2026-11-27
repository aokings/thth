// リモート MCP のコネクタの預かり（段 K・設計 共通の層 §6.1）。形は monban の Device（monban docs 設計_遠隔MCP §4）に合わせた。
//
// 1 つのコネクタ＝1 本のレーンの鍵（括りの段 2 と同じ道で、持ち主が同意の頁で許すと Person DO が発行する）。
// Worker は鍵の平文を保存しない: `enc_key = AES(K, 鍵)`、K は access の秘密と refresh の秘密（どちらも token の中にだけある）
// から HKDF で導いた鍵でだけ包む（`wrap_a`・`wrap_r`）。code の間は code から導いた鍵で包む（`wrap_c`）。
// ここが丸ごと漏れても、token が無ければ鍵は開かない。実行中のメモリを取られる脅威は保証の外（monban と同じ）。
//
// 1 つの object（名前 `connectors`）に全部の持ち主の行を置く。行:
//   client:<id>    DCR の予備 {redirect_uris, name, expires_at}
//   code:<hash>    認可 code 1 つ {device_id, client_id, redirect_uri, code_challenge, wrap_c, expires_at}
//   device:<id>    {owner, client_id, client_name, scope, writes, lane, workspace_req, action, workspace, account, key_sha256,
//                   enc_key, status, a_hash, r_hash, wrap_a, wrap_r, a_expires_at, r_expires_at, created_at, expires_at, confirms}
//   revoke:<hash>  VM の鍵の取り消しを Person DO に置き損ねた分（alarm がやり直す）{owner, workspace, tries, expires_at}
// 何も記録に出さない。鍵と秘密は Worker への RPC の答えにだけ出る。
import {DurableObject} from 'cloudflare:workers';
import {PERSON,WORKSPACE,LANE,fail,equal,opaque,b64,unb64,personStub} from './person.js';
import {HASH_PATTERN,digest} from './relay.js';

export const DEVICE_ID=/^[A-Za-z0-9_-]{22}$/;          // 16 byte の乱数
export const SECRET=/^[A-Za-z0-9_-]{43}$/;             // 32 byte の乱数（code・access と refresh の秘密）
export const KEY=/^[A-Za-z0-9_-]{43}$/;                // レーンの鍵（Person DO の opaque()）
export const CODE_TTL=60_000;                          // code は 1 分の内に使う
export const ACCESS_TTL=3_600_000;                     // 1 時間
export const REFRESH_TTL=30*86_400_000;                // 30 日・refresh のたびに回す
export const CLIENT_TTL=30*86_400_000;
export const CONFIRM_TTL=600_000;                      // 書く道具の 1 段目から 2 段目まで 10 分
export const DEVICES_MAX=256,CLIENTS_MAX=256,CODES_MAX=64,CONFIRMS_MAX=16,REVOKES_MAX=256;
export const SCOPES=['read','write','offline_access'];
const REVOKE_TTL=86_400_000;                           // 取り消しの置き直しは 1 日まで
const encoder=new TextEncoder();
const INFO=encoder.encode('thth-connector-v1');

async function derive(secret,salt){
  const material=await crypto.subtle.importKey('raw',encoder.encode(secret),'HKDF',false,['deriveKey']);
  return crypto.subtle.deriveKey({name:'HKDF',hash:'SHA-256',salt,info:INFO},material,{name:'AES-GCM',length:256},false,['encrypt','decrypt']);
}
// wrap: base64url(salt16 | iv12 | 暗号文)。secret は token の秘密か code。
export async function wrap(secret,bytes){
  const salt=crypto.getRandomValues(new Uint8Array(16)),iv=crypto.getRandomValues(new Uint8Array(12));
  const key=await derive(secret,salt);
  const ct=new Uint8Array(await crypto.subtle.encrypt({name:'AES-GCM',iv},key,bytes));
  const out=new Uint8Array(28+ct.length);out.set(salt,0);out.set(iv,16);out.set(ct,28);
  return b64(out);
}
export async function unwrap(secret,text){
  try{
    const all=unb64(text),key=await derive(secret,all.subarray(0,16));
    return new Uint8Array(await crypto.subtle.decrypt({name:'AES-GCM',iv:all.subarray(16,28)},key,all.subarray(28)));
  }catch{return null;}
}
const kstr=K=>b64(K);
const scopeOk=scope=>Array.isArray(scope)&&scope.length>0&&scope.length<=3&&scope.every(s=>SCOPES.includes(s))&&new Set(scope).size===scope.length;
const sameHash=(a,b)=>typeof a==='string'&&typeof b==='string'&&equal(encoder.encode(a),encoder.encode(b));
// 戻り先の形: https で本当のホスト名か、loopback の http（RFC 8252）。
export function acceptableRedirect(value){
  if(typeof value!=='string'||value.length>512)return false;
  let u;try{u=new URL(value);}catch{return false;}
  if(u.username||u.password||u.hash)return false;
  const loop=u.hostname==='localhost'||u.hostname==='127.0.0.1'||u.hostname==='[::1]';
  if(u.protocol==='http:')return loop;
  return u.protocol==='https:'&&!loop&&!/^(\d+\.){3}\d+$/.test(u.hostname)&&!u.hostname.startsWith('[')&&!u.hostname.endsWith('.localhost');
}
export function splitToken(token){
  if(typeof token!=='string'||token.length!==66||token[22]!=='.')return null;
  const id=token.slice(0,22),secret=token.slice(23);
  return DEVICE_ID.test(id)&&SECRET.test(secret)?{id,secret}:null;
}
// 一覧と頁に出してよい欄だけ（秘密・暗号文・hash は出さない）。
function meta(id,d){
  return {device_id:id,client_name:d.client_name,lane:d.lane,workspace:d.workspace??d.workspace_req??null,
    scope:d.scope,writes:d.writes,created_at:d.created_at,expires_at:d.expires_at,last_used_at:d.last_used_at??null};
}

export class Connector extends DurableObject {
  now(){return Date.now();}
  put(key,value){this.ctx.storage.kv.put(key,value);}
  atomic(fn){try{return this.ctx.storage.transactionSync(fn);}catch{return fail(503,'storage_unavailable');}}
  count(prefix){return [...this.ctx.storage.kv.list({prefix})].length;}
  async wake(at){const alarm=await this.ctx.storage.getAlarm();if(alarm===null||at<alarm)await this.ctx.storage.setAlarm(at);}

  // DCR（client の身元の文書が無い client の予備）。
  async registerClient(m){
    if(!m||typeof m!=='object'||!Array.isArray(m.redirect_uris)||!m.redirect_uris.length||m.redirect_uris.length>8||
       !m.redirect_uris.every(acceptableRedirect))return fail(400,'invalid_client_metadata');
    const name=typeof m.client_name==='string'&&m.client_name.length<=128&&!/[\u0000-\u001f\u007f]/.test(m.client_name)?m.client_name:null;
    const result=this.atomic(()=>{
      const now=this.now();this.prune(now);
      const clients=[...this.ctx.storage.kv.list({prefix:'client:'})].sort(([,a],[,b])=>a.created_at-b.created_at);
      for(const [k] of clients.slice(0,Math.max(0,clients.length-CLIENTS_MAX+1)))this.ctx.storage.kv.delete(k);
      const id='c_'+opaque().slice(0,32),expires_at=now+CLIENT_TTL;
      this.put('client:'+id,{redirect_uris:m.redirect_uris,name,created_at:now,expires_at});
      return {status:200,body:{client_id:id,expires_at}};
    });
    if(result.status===200)await this.wake(result.body.expires_at);
    return result;
  }
  client(id){
    const row=typeof id==='string'&&/^c_[A-Za-z0-9_-]{32}$/.test(id)?this.ctx.storage.kv.get('client:'+id):null;
    return row&&row.expires_at>this.now()?{status:200,body:{redirect_uris:row.redirect_uris,name:row.name}}:fail(404,'unknown_client');
  }

  // 持ち主が許した: 発行したレーンの鍵を K で、K を code で包んで置く。code を 1 度だけ返す。
  async createCode({client_id,client_name,redirect_uri,code_challenge,scope,writes,owner,lane,workspace,action,key}){
    if(typeof client_id!=='string'||client_id.length>512||typeof redirect_uri!=='string'||redirect_uri.length>512||
       typeof client_name!=='string'||client_name.length>128||typeof code_challenge!=='string'||!SECRET.test(code_challenge)||
       !scopeOk(scope)||typeof writes!=='boolean'||typeof owner!=='string'||!PERSON.test(owner)||typeof lane!=='string'||!LANE.test(lane)||
       !(workspace===null||typeof workspace==='string'&&WORKSPACE.test(workspace))||typeof action!=='string'||!SECRET.test(action)||
       typeof key!=='string'||!KEY.test(key))return fail();
    const code=opaque(),K=crypto.getRandomValues(new Uint8Array(32));
    const enc_key=await wrap(kstr(K),encoder.encode(key)),wrap_c=await wrap(code,K);
    const code_hash=await digest(code),key_sha256=await digest(key),device_id=b64(crypto.getRandomValues(new Uint8Array(16)));
    const result=this.atomic(()=>{
      const now=this.now();this.prune(now);
      if(this.count('device:')>=DEVICES_MAX)return fail(429,'too_many_connectors');
      if(this.count('code:')>=CODES_MAX)return fail(429,'too_many_codes');
      const expires_at=now+CODE_TTL;
      this.put('device:'+device_id,{owner,client_id,client_name,scope,writes,lane,workspace_req:workspace,action,workspace:null,account:null,
        key_sha256,enc_key,status:'pending',created_at:now,expires_at,confirms:{}});
      this.put('code:'+code_hash,{device_id,client_id,redirect_uri,code_challenge,wrap_c,expires_at});
      return {status:200,body:{code,device_id,expires_at}};
    });
    if(result.status===200)await this.wake(result.body.expires_at);
    return result;
  }

  // code → token。PKCE S256 をここで確かめる。code はどうなっても 1 回で消える。
  async exchange({code,code_verifier,client_id,redirect_uri}){
    if(typeof code!=='string'||!SECRET.test(code)||typeof code_verifier!=='string'||code_verifier.length<43||code_verifier.length>128||
       !/^[A-Za-z0-9._~-]+$/.test(code_verifier))return fail(400,'invalid_grant');
    const code_hash=await digest(code);
    const challenge=b64(new Uint8Array(await crypto.subtle.digest('SHA-256',encoder.encode(code_verifier))));
    const taken=this.atomic(()=>{
      const row=this.ctx.storage.kv.get('code:'+code_hash);
      if(row)this.ctx.storage.kv.delete('code:'+code_hash);
      if(!row||row.expires_at<=this.now())return fail(400,'invalid_grant');
      if(row.client_id!==client_id||(redirect_uri!==undefined&&redirect_uri!==null&&row.redirect_uri!==redirect_uri)||!sameHash(row.code_challenge,challenge)){
        // 交換に失敗した code の預かりは捨てる（VM の鍵も取り消しを置く）。
        this.drop(row.device_id,this.now());
        return fail(400,'invalid_grant');
      }
      return {status:200,body:row};
    });
    if(taken.status!==200){await this.wake(this.now()+1000);return taken;}
    const K=await unwrap(code,taken.body.wrap_c);
    if(!K)return fail(400,'invalid_grant');
    return this.issue(taken.body.device_id,K);
  }
  // `expectRHash`: refresh は確かめたその秘密だけを使い切る（同時に 2 つ来たら 1 つだけ通る）。
  async issue(device_id,K,expectRHash=null){
    const sa=opaque(),sr=opaque();
    const wrap_a=await wrap(sa,K),wrap_r=await wrap(sr,K),a_hash=await digest(sa),r_hash=await digest(sr);
    const result=this.atomic(()=>{
      const now=this.now(),row=this.ctx.storage.kv.get('device:'+device_id);
      if(!row||(expectRHash!==null&&row.r_hash!==expectRHash))return fail(400,'invalid_grant');
      const a_expires_at=now+ACCESS_TTL,r_expires_at=now+REFRESH_TTL;
      this.put('device:'+device_id,{...row,status:'active',expires_at:r_expires_at,a_hash,r_hash,wrap_a,wrap_r,a_expires_at,r_expires_at,rotated_at:now});
      return {status:200,body:{access_token:device_id+'.'+sa,refresh_token:device_id+'.'+sr,expires_in:ACCESS_TTL/1000,
        token_type:'Bearer',scope:row.scope.join(' ')}};
    });
    if(result.status===200)await this.wake(this.now()+REFRESH_TTL);
    return result;
  }
  async refresh(token){
    const parsed=splitToken(token);if(!parsed)return fail(400,'invalid_grant');
    const row=this.ctx.storage.kv.get('device:'+parsed.id);
    if(!row||row.status!=='active'||row.r_expires_at<=this.now()||!sameHash(row.r_hash,await digest(parsed.secret)))return fail(400,'invalid_grant');
    const K=await unwrap(parsed.secret,row.wrap_r);
    if(!K)return fail(400,'invalid_grant');
    return this.issue(parsed.id,K,row.r_hash);
  }
  // 1 回の呼び出し: access の秘密で K を、K で鍵を開く。鍵は Worker にだけ返る（ここには書かない）。
  async resolve(token){
    const parsed=splitToken(token);if(!parsed)return fail(401,'invalid_token');
    const row=this.ctx.storage.kv.get('device:'+parsed.id);
    if(!row||row.status!=='active')return fail(401,'invalid_token');
    if(row.a_expires_at<=this.now()||!sameHash(row.a_hash,await digest(parsed.secret)))return fail(401,'invalid_token');
    const K=await unwrap(parsed.secret,row.wrap_a);if(!K)return fail(401,'invalid_token');
    const key=await unwrap(kstr(K),row.enc_key);if(!key)return fail(401,'invalid_token');
    return {status:200,body:{device_id:parsed.id,owner:row.owner,scope:row.scope,writes:row.writes,lane:row.lane,
      workspace:row.workspace,account:row.account,action:row.action,created_at:row.created_at,key:new TextDecoder().decode(key)}};
  }
  // RFC 7009 の取り消しの口: token の秘密（access か refresh）が合うときだけ。
  async revokeToken(token){
    const parsed=splitToken(token);if(!parsed)return fail(404,'not_found');
    const row=this.ctx.storage.kv.get('device:'+parsed.id);
    if(!row||row.status!=='active')return fail(404,'not_found');
    const hashed=await digest(parsed.secret);
    if(!sameHash(row.a_hash,hashed)&&!sameHash(row.r_hash,hashed))return fail(404,'not_found');
    return this.revoke(parsed.id);
  }
  // VM がレーンの鍵を作り、括りと頼みの宛先の口座を返した（Person DO の laneOutcome から）。秘密ではない。
  // 同じ持ち主・同じ client・同じレーンで、同じ括りに結んだ前の接続は、ここで外す（VM は同じ括りの同じレーンの前の鍵を
  // 新しい鍵を作るときに取り消している）。外すのは drop() を通し、VM の取り消しも置く（段 K の監査 3: 括りを見ずに
  // 黙って消すと、ほかの括りの接続が消え VM の鍵が残った）。ほかの括りの接続はそのまま並ぶ。
  async bind(device_id,workspace,account){
    const result=this.atomic(()=>{
      if(typeof device_id!=='string'||!DEVICE_ID.test(device_id)||typeof workspace!=='string'||!WORKSPACE.test(workspace)||
         typeof account!=='string'||!PERSON.test(account))return fail();
      const row=this.ctx.storage.kv.get('device:'+device_id);
      if(!row||row.status!=='active')return fail(404,'not_found');
      if(row.workspace!==null&&row.workspace!==workspace)return fail(409,'workspace_mismatch');
      const now=this.now();
      this.put('device:'+device_id,{...row,workspace,account});
      let dropped=0;
      if(row.workspace===null)
        for(const [k,d] of [...this.ctx.storage.kv.list({prefix:'device:'})])
          if(k!=='device:'+device_id&&d.owner===row.owner&&d.client_id===row.client_id&&d.lane===row.lane&&
             d.created_at<row.created_at&&(d.workspace??d.workspace_req)===workspace){this.drop(k.slice(7),now);dropped++;}
      return {status:200,body:{dropped}};
    });
    if(result.status===200&&result.body.dropped)await this.wake(this.now()+1000);
    return result;
  }
  // 書く道具の二段の確認（設計 精読 GenerativeUI §1: 人が押した証明はどの仕様にも無い → 1 段目を見せたことをサーバに残す）。
  // `hash` は接続・道具・引数（confirm を除く）の SHA-256。`value` は Worker が出した確認の値（approve は VM の digest なので null）。
  noteConfirm(device_id,hash,value){return this.atomic(()=>{
    if(typeof device_id!=='string'||!DEVICE_ID.test(device_id)||typeof hash!=='string'||!HASH_PATTERN.test(hash)||
       !(value===null||typeof value==='string'&&SECRET.test(value)))return fail();
    const row=this.ctx.storage.kv.get('device:'+device_id);
    if(!row||row.status!=='active')return fail(404,'not_found');
    const now=this.now();
    const kept=Object.entries(row.confirms??{}).filter(([,c])=>c.expires_at>now).sort(([,a],[,b])=>b.expires_at-a.expires_at).slice(0,CONFIRMS_MAX-1);
    this.put('device:'+device_id,{...row,confirms:{...Object.fromEntries(kept),[hash]:{value,expires_at:now+CONFIRM_TTL}},last_used_at:now});
    return {status:200,body:{}};
  });}
  // 2 段目: 1 段目の控えを 1 度だけ使う。合わなければ控えは残す（打ち間違いで 1 段目からやり直させない）。
  takeConfirm(device_id,hash,value){return this.atomic(()=>{
    if(typeof device_id!=='string'||!DEVICE_ID.test(device_id)||typeof hash!=='string'||!HASH_PATTERN.test(hash))return fail();
    const row=this.ctx.storage.kv.get('device:'+device_id);
    if(!row||row.status!=='active')return fail(404,'not_found');
    const found=(row.confirms??{})[hash];
    if(!found||found.expires_at<=this.now())return fail(409,'confirm_required');
    if(found.value!==null&&!(typeof value==='string'&&sameHash(found.value,value)))return fail(409,'confirm_mismatch');
    const {[hash]:_,...rest}=row.confirms;
    this.put('device:'+device_id,{...row,confirms:rest,last_used_at:this.now()});
    return {status:200,body:{}};
  });}
  // 取り消し: 預かりを消し、VM の鍵の取り消しを Person DO に置く（置けなければ alarm がやり直す）。
  async revoke(device_id){
    const dropped=this.atomic(()=>{
      if(typeof device_id!=='string'||!DEVICE_ID.test(device_id))return fail();
      const row=this.ctx.storage.kv.get('device:'+device_id);
      if(!row)return fail(404,'not_found');
      this.drop(device_id,this.now());
      return {status:200,body:{owner:row.owner,workspace:row.workspace??row.workspace_req??null,key_sha256:row.key_sha256}};
    });
    if(dropped.status!==200)return dropped;
    const placed=await this.placeRevoke(dropped.body.key_sha256);
    return {status:200,body:{revoked:true,vm_revoke:placed?'placed':'queued'}};
  }
  // 持ち主の頁から: その持ち主の行だけ取り消せる（ほかの持ち主の行は無いのと同じ）。
  async revokeOwned(owner,device_id){
    const row=typeof device_id==='string'&&DEVICE_ID.test(device_id)?this.ctx.storage.kv.get('device:'+device_id):null;
    if(!row||row.owner!==owner)return fail(404,'not_found');
    return this.revoke(device_id);
  }
  list(owner){
    const now=this.now();
    return {status:200,body:[...this.ctx.storage.kv.list({prefix:'device:'})]
      .filter(([,d])=>d.owner===owner&&d.status==='active'&&d.expires_at>now).map(([k,d])=>meta(k.slice(7),d))
      .sort((a,b)=>b.created_at-a.created_at)};
  }
  // 行を消し、VM の鍵の取り消しの控えを置く（transaction の中から呼ぶ）。
  drop(device_id,now){
    const row=this.ctx.storage.kv.get('device:'+device_id);
    if(!row)return;
    this.ctx.storage.kv.delete('device:'+device_id);
    if(typeof row.key_sha256==='string'&&HASH_PATTERN.test(row.key_sha256)&&this.count('revoke:')<REVOKES_MAX)
      this.put('revoke:'+row.key_sha256,{owner:row.owner,workspace:row.workspace??row.workspace_req??null,tries:0,expires_at:now+REVOKE_TTL});
  }
  async placeRevoke(sha256){
    const row=this.ctx.storage.kv.get('revoke:'+sha256);
    if(!row)return true;
    let placed;
    try{placed=await (await personStub(this.env,row.owner)).laneRevoke(row.workspace,sha256);}catch{placed={status:503};}
    // 持ち主がもういない（404）なら VM はその人の sync をしないので、置き直しても届かない: 控えを外す。
    if(placed.status===200||placed.status===404||placed.status===400){this.atomic(()=>{this.ctx.storage.kv.delete('revoke:'+sha256);return {status:200};});return placed.status===200;}
    this.atomic(()=>{const r=this.ctx.storage.kv.get('revoke:'+sha256);if(r)this.put('revoke:'+sha256,{...r,tries:r.tries+1});return {status:200};});
    await this.wake(this.now()+60_000);
    return false;
  }
  prune(now){
    for(const prefix of ['client:','code:','revoke:'])
      for(const [key,row] of this.ctx.storage.kv.list({prefix}))if(row.expires_at<=now)this.ctx.storage.kv.delete(key);
    // 期限の切れた接続（code を交換しなかった・refresh が 30 日切れた）は、VM の鍵も取り消す。
    for(const [key,row] of this.ctx.storage.kv.list({prefix:'device:'}))if(row.expires_at<=now)this.drop(key.slice(7),now);
  }
  async alarm(){
    const now=this.now();
    this.atomic(()=>{this.prune(now);return {status:200};});
    for(const [key] of [...this.ctx.storage.kv.list({prefix:'revoke:'})])await this.placeRevoke(key.slice(7));
    const next=Math.min(...[...this.ctx.storage.kv.list()].map(([,row])=>row?.expires_at??Infinity));
    const pending=this.count('revoke:')>0?now+60_000:Infinity;
    const at=Math.min(next,pending);
    if(Number.isFinite(at))await this.ctx.storage.setAlarm(Math.max(at,now+1000));
  }
}
