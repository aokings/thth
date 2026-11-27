// ブラウザ式の `thth login`（設計 3.14.2 §2）の 1 回分。code（`XXXX-XXXX`）の SHA-256 で引き、10 分で消える。
//
//   waiting（CLI が始めた）→ issuing（ブラウザで許可を押した・secret の照合中）→ ready（鍵を置いた）→ taken（CLI が受け取った）
//
// - 置くのは poll_token の hash・状態・期限と、ready の間だけ鍵とアカウント名。鍵は CLI が受け取った時点で消す。
// - レーンの鍵（`thth login --lane`・括りの段 2）は、始めに括りとレーンの名（秘密でない）を置き、許可のあとは持ち主の名と
//   発行の操作の id を残す（CLI が鍵を受け取ったあと、VM で鍵が生きたか・なぜ生きないかを引くため・10 分で消える）。
// - 観測ログには何も出さない（鍵も code も）。
import {DurableObject} from 'cloudflare:workers';
import {HASH_PATTERN} from './relay.js';
import {fail,equal,WORKSPACE,LANE} from './person.js';

export const LOGIN_TTL=600_000;     // 10 分
export const LOGIN_CLAIM_MS=60_000; // 許可を押してから鍵を置くまでの最長（照合が落ちたら waiting に戻す）
const encoder=new TextEncoder();

export class Login extends DurableObject {
  now(){return Date.now();}
  put(key,value){this.ctx.storage.kv.put(key,value);}
  atomic(fn){try{return this.ctx.storage.transactionSync(fn);}catch{return fail(503,'storage_unavailable');}}
  // 期限を過ぎた行は無いのと同じ（alarm が消すまでの間も）。
  live(now){const row=this.ctx.storage.kv.get('login');return row&&row.expires_at>now?row:null;}
  gone(now){return this.ctx.storage.kv.get('login')?fail(410,'expired'):fail(404,'not_found');}

  async start(pollHash,lane=null,workspace=null){
    if(typeof pollHash!=='string'||!HASH_PATTERN.test(pollHash))return fail();
    if(!(lane===null||typeof lane==='string'&&LANE.test(lane))||!(workspace===null||lane!==null&&typeof workspace==='string'&&WORKSPACE.test(workspace)))return fail();
    const result=this.atomic(()=>{
      const now=this.now();
      if(this.live(now))return fail(409,'code_in_use');
      const expires_at=now+LOGIN_TTL;
      this.put('login',{poll_token_hash:pollHash,status:'waiting',claimed_until:null,key:null,account:null,expires_at,
        ...(lane===null?{}:{lane,workspace})});
      return {status:200,body:{expires_at}};
    });
    if(result.status===200)await this.ctx.storage.setAlarm(result.body.expires_at);
    return result;
  }
  // ブラウザの form を出してよいか（鍵もアカウント名も返さない）。
  state(){
    const now=this.now(),row=this.live(now);
    if(!row)return this.gone(now);
    if(row.status==='waiting'||row.status==='issuing')return {status:200,body:{status:'waiting',pending:row.pending??null,
      lane:row.lane??null,workspace:row.workspace??null}};
    return fail(410,'used');
  }
  // 許可を押したが、VM がその人のアカウントの状態をまだ押し上げていなかった（3.14.3 穴 2）。アカウント名だけを覚え、
  // GET のページがそれが届くまで自分で読み直す（secret は覚えない）。
  expect(account){return this.atomic(()=>{
    if(typeof account!=='string'||!/^[A-Za-z0-9_.-]{1,64}$/.test(account))return fail();
    const row=this.live(this.now());
    if(row?.status==='waiting')this.put('login',{...row,pending:account});
    return {status:200,body:{}};
  });}
  // 許可を押した: 1 度に 1 つだけ照合に進める（同じ code に 2 つ鍵を発行しない）。
  claim(){return this.atomic(()=>{
    const now=this.now(),row=this.live(now);
    if(!row)return this.gone(now);
    if(row.status==='issuing'&&row.claimed_until>now)return fail(409,'busy');
    if(row.status!=='waiting'&&row.status!=='issuing')return fail(410,'used');
    this.put('login',{...row,status:'issuing',claimed_until:now+LOGIN_CLAIM_MS});
    return {status:200,body:{}};
  });}
  release(){return this.atomic(()=>{
    const row=this.live(this.now());
    if(row?.status==='issuing')this.put('login',{...row,status:'waiting',claimed_until:null});
    return {status:200,body:{}};
  });}
  deliver(key,account){return this.atomic(()=>{
    if(typeof key!=='string'||!/^[A-Za-z0-9_-]{43}$/.test(key)||typeof account!=='string')return fail();
    const now=this.now(),row=this.live(now);
    if(!row)return this.gone(now);
    if(row.status!=='issuing')return fail(410,'used');
    this.put('login',{...row,status:'ready',claimed_until:null,key,account,pending:null});
    return {status:200,body:{}};
  });}
  // レーンの鍵: 鍵と、持ち主の名・発行の操作の id を置く。
  deliverLane(key,person,action){return this.atomic(()=>{
    if(typeof key!=='string'||!/^[A-Za-z0-9_-]{43}$/.test(key)||typeof person!=='string'||typeof action!=='string')return fail();
    const now=this.now(),row=this.live(now);
    if(!row)return this.gone(now);
    if(row.status!=='issuing'||!row.lane)return fail(410,'used');
    this.put('login',{...row,status:'ready',claimed_until:null,key,person,action,pending:null});
    return {status:200,body:{}};
  });}
  // CLI の poll。鍵は 1 度だけ返し、返したら消す。
  // レーンの鍵は、返したあとも期限まで {taken, person, action} を返す（Worker が VM の答えを引いて CLI に渡す）。
  poll(pollHash){return this.atomic(()=>{
    if(typeof pollHash!=='string'||!HASH_PATTERN.test(pollHash))return fail(401,'unauthorized');
    const now=this.now(),row=this.live(now);
    if(!row)return this.gone(now);
    if(!equal(encoder.encode(pollHash),encoder.encode(row.poll_token_hash)))return fail(401,'unauthorized');
    if(row.status==='taken')return row.lane?{status:200,body:{taken:true,person:row.person,action:row.action}}:fail(410,'used');
    if(row.status!=='ready')return {status:202,body:{status:'waiting'}};
    this.put('login',{...row,status:'taken',key:null,account:null});
    if(row.lane)return {status:200,body:{key:row.key,owner:row.person,lane:row.lane,workspace:row.workspace??null}};
    return {status:200,body:{key:row.key,account:row.account}};
  });}
  async alarm(){
    const row=this.ctx.storage.kv.get('login');
    if(row&&row.expires_at>this.now()){await this.ctx.storage.setAlarm(row.expires_at);return;}
    await this.ctx.storage.deleteAll();
  }
}
