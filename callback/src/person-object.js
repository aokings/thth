import {DurableObject} from 'cloudflare:workers';
import {TTL,VIEW_MAX,LIST_LOCK_MS,ITERATIONS,PERSON,fail,fields,opaque,verifier,equal,unb64} from './person.js';
import {HASH_PATTERN,STATE_PATTERN,digest} from './relay.js';
// 3.13.0: 承認ページの session（ApprovalSession）と承認待ちの一覧は無い。残るのは持ち主（person）の
// secret の照合・/activity・口座ごとの束（退出・添付の後始末）。名前は段 2 で改める。

// ---- 動きの一覧（設計 3.12.0 §3.4）の形。VM から来る要約も、ページから来る操作も、閉じた形だけ受ける。
export const ACTIVITY_TTL=3_600_000, ACTION_TTL=3_600_000, ACTION_PENDING_MAX=16;
const ACTIVITY_ACCOUNTS=8, ACTIVITY_ROWS=30, ACTIVITY_HEAD=60;
export const ACTION_KINDS=['stop','resume','cancel','settings','revoke','rotate'];
const ACTION_EXTRA={stop:[],resume:[],revoke:[],rotate:['sha256'],cancel:['draft_id'],settings:['key','value']};
export const SETTING_KEYS=['daily_max_posts','daily_max_retracts','burst_count','burst_minutes','hold_minutes','min_interval_hours'];
const SETTING_VALUE=/^[0-9]{1,6}(?:\.[0-9]{1,2})?$/;
const ROW_KINDS=['published','retracted','scheduled','held','stopped'];
const STOP_REASONS=['burst','owner','guard_state_unreadable'];
const ISO=/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:[+-]\d{2}:\d{2}|Z)$/;
const LIMIT_KEYS=['daily_max_posts','daily_max_retracts','burst_count','burst_minutes','hold_minutes','min_interval_hours'];
const same=(value,keys)=>value!==null&&typeof value==='object'&&!Array.isArray(value)&&Object.keys(value).sort().join(',')===[...keys].sort().join(',');
const plain=(v,max=300)=>typeof v==='string'&&v.length<=max&&!/[\u0000-\u001f\u007f]/.test(v);
const count=v=>Number.isSafeInteger(v)&&v>=0&&v<=1_000_000;
function validRow(r){
  return same(r,['kind','at','head','post_id','draft_id','reason','reply'])&&ROW_KINDS.includes(r.kind)&&typeof r.at==='string'&&ISO.test(r.at)&&
    plain(r.head,ACTIVITY_HEAD*2)&&Array.from(r.head).length<=ACTIVITY_HEAD&&(r.post_id===null||plain(r.post_id)&&r.post_id.length>0)&&
    (r.draft_id===null||typeof r.draft_id==='string'&&HASH_PATTERN.test(r.draft_id))&&(r.reason===null||STOP_REASONS.includes(r.reason))&&typeof r.reply==='boolean';
}
const SUMMARY_KEYS=['account','generated_at','scheduled','limits','stopped','today','credential','rows'];
// 3.12.0 の VM は要約に `approval` を足してくる。deploy は Worker が先・VM があと（設計 3.13.0 §3）なので、
// その間は `approval` が付いていても受ける（値は見ない・置かない）。
export function validSummary(s){
  return (same(s,SUMMARY_KEYS)||same(s,[...SUMMARY_KEYS,'approval'])&&typeof s.approval==='string'&&s.approval.length<=16)&&
    typeof s.account==='string'&&PERSON.test(s.account)&&typeof s.generated_at==='string'&&ISO.test(s.generated_at)&&
    typeof s.scheduled==='boolean'&&
    same(s.limits,LIMIT_KEYS)&&LIMIT_KEYS.every(k=>typeof s.limits[k]==='number'&&Number.isFinite(s.limits[k])&&s.limits[k]>=0&&s.limits[k]<=1_000_000)&&
    (s.stopped===null||same(s.stopped,['reason','at'])&&STOP_REASONS.includes(s.stopped.reason)&&(s.stopped.at===null||typeof s.stopped.at==='string'&&ISO.test(s.stopped.at)))&&
    same(s.today,['posts','retracts'])&&count(s.today.posts)&&count(s.today.retracts)&&
    (s.credential===null||same(s.credential,['id','expires_at','revoked'])&&typeof s.credential.id==='string'&&/^[0-9a-f]{12}$/.test(s.credential.id)&&
      plain(s.credential.expires_at,40)&&typeof s.credential.revoked==='boolean')&&
    Array.isArray(s.rows)&&s.rows.length<=ACTIVITY_ROWS&&s.rows.every(validRow);
}
function validSync(body){
  if(!same(body,['accounts','completed'])||!Array.isArray(body.accounts)||body.accounts.length>ACTIVITY_ACCOUNTS||
     !Array.isArray(body.completed)||body.completed.length>ACTION_PENDING_MAX)return false;
  if(!body.accounts.every(validSummary)||new Set(body.accounts.map(s=>s.account)).size!==body.accounts.length)return false;
  return body.completed.every(c=>same(c,['id','outcome','reason'])&&typeof c.id==='string'&&STATE_PATTERN.test(c.id)&&
    ['done','failed'].includes(c.outcome)&&(c.reason===null||typeof c.reason==='string'&&/^[a-z_]{1,64}$/.test(c.reason)));
}
export function validRequest(a){
  if(a===null||typeof a!=='object'||!ACTION_KINDS.includes(a.kind)||a.kind==='rotate'&&'sha256' in a)return false;
  const extra=a.kind==='rotate'?[]:ACTION_EXTRA[a.kind];
  if(!same(a,['kind','account',...extra])||typeof a.account!=='string'||!PERSON.test(a.account))return false;
  if(a.kind==='cancel')return typeof a.draft_id==='string'&&HASH_PATTERN.test(a.draft_id);
  if(a.kind==='settings')return typeof a.key==='string'&&SETTING_KEYS.includes(a.key)&&typeof a.value==='string'&&SETTING_VALUE.test(a.value);
  return true;
}
export class AtomicObject extends DurableObject {
  now(){return Date.now();}
  put(key,value){this.ctx.storage.kv.put(key,value);}
  atomic(fn){try{return this.ctx.storage.transactionSync(fn);}catch{return fail(503,'storage_unavailable');}}
  // Signature validated at the sole public HTTP entry; RPC bindings are private.
  replay(ticket){
    const now=this.now();
    if(!ticket||!STATE_PATTERN.test(ticket.nonce)||!Number.isSafeInteger(ticket.time)||Math.abs(now-ticket.time)>60_000)return false;
    const seen=this.ctx.storage.kv.get('nonces')||{};
    for(const [key,expiry] of Object.entries(seen))if(expiry<now)delete seen[key];
    if(seen[ticket.nonce]||Object.keys(seen).length>=1024)return false;
    seen[ticket.nonce]=ticket.time+60_001;this.put('nonces',seen);return true;
  }
}
export class Person extends AtomicObject {
  manage(operation,body,ticket){return this.atomic(()=>{
    if(!['set','revoke','unlock','status'].includes(operation))return fail();
    if(operation==='set'?!fields(body,['salt','verifier','iterations'])||typeof body.salt!=='string'||typeof body.verifier!=='string'||!STATE_PATTERN.test(body.salt)||!STATE_PATTERN.test(body.verifier)||body.iterations!==ITERATIONS:!fields(body,[]))return fail();
    if(!this.replay(ticket))return fail(409,'replayed_request');
    const old=this.ctx.storage.kv.get('person');
    if(operation==='status')return {status:200,body:old?{active:old.active,locked:old.failures>=5,generation:old.generation}:{active:false,locked:false,generation:null}};
    if(operation==='set')this.put('person',{...body,active:true,generation:opaque(),failures:0});
    else if(!old)return fail(404,'not_found');
    else if(operation==='revoke')this.put('person',{active:false,generation:opaque(),failures:0});
    else this.put('person',{...old,failures:0,list_failures:0,list_locked_until:null});
    return {status:200,body:{status:operation==='set'?'configured':operation==='revoke'?'revoked':'unlocked'}};
  });}
  // 招待（3.10.0）の完了ページが本人の口座の secret を 1 回だけ作る。既存の持ち主は
  // 上書きしない（運営者の admin secret set・失効・別の招待の人）。同じ招待のやり直しだけ通す。
  provision(salt,verifier,origin){return this.atomic(()=>{
    if(typeof salt!=='string'||!STATE_PATTERN.test(salt)||typeof verifier!=='string'||!STATE_PATTERN.test(verifier)||
       typeof origin!=='string'||!HASH_PATTERN.test(origin))return fail();
    const old=this.ctx.storage.kv.get('person');
    if(old&&old.invite!==origin)return fail(409,'person_exists');
    this.put('person',{salt,verifier,iterations:ITERATIONS,active:true,generation:opaque(),failures:0,invite:origin});
    return {status:200,body:{status:'configured'}};
  });}
  current(generation=null){
    const row=this.ctx.storage.kv.get('person');
    return row?.active&&row.failures<5&&(!generation||generation===row.generation)?row.generation:null;
  }
  async alarm(){
    const now=this.now();let next=null;
    // grant:・listed: は 3.12.0 までの承認の残り（期限で消える）。
    this.ctx.storage.transactionSync(()=>{for(const prefix of ['grant:','listed:','view:','activity:','action:'])for(const [key,row] of this.ctx.storage.kv.list({prefix})){
      if(row.expires_at<=now)this.ctx.storage.kv.delete(key);else next=Math.min(next??Infinity,row.expires_at);
    }});
    if(next!==null)await this.ctx.storage.setAlarm(next);
  }
  async wake(at){const alarm=await this.ctx.storage.getAlarm();if(alarm===null||at<alarm)await this.ctx.storage.setAlarm(at);}
  prune(now){for(const prefix of ['listed:','view:','activity:','action:'])for(const [key,row] of this.ctx.storage.kv.list({prefix}))if(row.expires_at<=now)this.ctx.storage.kv.delete(key);}
  // ---- 動きの一覧（設計 3.12.0 §3.4）。`activity:<口座>` は VM が押し上げた要約（本文は先頭 60 字・
  // 1 時間で忘れる）。`action:<id>` は持ち主が secret で確かめて頼んだ操作（VM が拾って結果を返す・
  // 1 時間）。どちらもこの人の object にだけある。鍵の発行し直しの bearer は**置かない**（hash だけ）。
  async activitySync(body,ticket){
    const result=this.atomic(()=>{
      if(!validSync(body))return fail();
      if(!this.replay(ticket))return fail(409,'replayed_request');
      const row=this.ctx.storage.kv.get('person');
      if(!row||!this.current(row.generation))return fail(409,'person_unavailable');
      const now=this.now();this.prune(now);
      for(const [key] of this.ctx.storage.kv.list({prefix:'activity:'}))this.ctx.storage.kv.delete(key);
      for(const {approval,...summary} of body.accounts)this.put('activity:'+summary.account,{...summary,expires_at:now+ACTIVITY_TTL});
      for(const done of body.completed){
        const key='action:'+done.id,old=this.ctx.storage.kv.get(key);
        if(!old||old.status!=='pending')continue;
        const {sha256,...kept}=old;
        this.put(key,{...kept,status:done.outcome,reason:done.reason,finished_at:now,expires_at:now+ACTION_TTL});
      }
      const actions=[...this.ctx.storage.kv.list({prefix:'action:'})].map(([,a])=>a)
        .filter(a=>a.status==='pending'&&a.expires_at>now).sort((a,b)=>a.created_at-b.created_at)
        .map(a=>{const out={id:a.id,account:a.account,kind:a.kind};for(const k of ACTION_EXTRA[a.kind])out[k]=a[k];return out;});
      return {status:200,body:{status:'synced',actions}};
    });
    if(result.status===200)await this.wake(this.now()+Math.min(ACTIVITY_TTL,ACTION_TTL));
    return result;
  }
  activityView(tokenHash){
    const view=this.opened(tokenHash);if(!view)return fail(401,'unauthorized');
    const now=this.now();
    const accounts=[...this.ctx.storage.kv.list({prefix:'activity:'})].map(([,s])=>s).filter(s=>s.expires_at>now)
      .sort((a,b)=>a.account<b.account?-1:1).map(({expires_at,...s})=>s);
    const actions=[...this.ctx.storage.kv.list({prefix:'action:'})].map(([,a])=>a).filter(a=>a.expires_at>now)
      .sort((a,b)=>b.created_at-a.created_at).map(({sha256,expires_at,...a})=>a);
    return {status:200,body:{accounts,actions}};
  }
  // 操作は secret をもう一度入れて確かめる（一覧に入るのと同じ照合・同じ失敗の数え方: 5 回で 15 分閉じる）。
  async activityAct(tokenHash,secret,action){
    const view=this.opened(tokenHash);if(!view)return fail(401,'unauthorized');
    if(!validRequest(action))return fail(400,'invalid_request');
    const before=this.ctx.storage.kv.get('person');
    const valid=typeof secret==='string'&&secret.length>=16&&secret.length<=128;
    const computed=valid?await verifier(secret,typeof before?.salt==='string'?before.salt:'A'.repeat(43)):null;
    // 鍵の発行し直し: bearer はここで作り、hash だけを置く。値は呼んだページに 1 度だけ返す。
    const bearer=action.kind==='rotate'?opaque():null,sha256=bearer?await digest(bearer):null;
    const result=this.atomic(()=>{
      const row=this.ctx.storage.kv.get('person');
      const now=this.now(),counted=row?.list_failures??0;
      const failures=counted>=5&&now>=(row.list_locked_until??0)?0:counted;
      if(!before?.verifier||!row?.verifier||row.generation!==before.generation||row.generation!==view.generation||
         !this.current(row.generation)||failures>=5)return fail(403,'secret_failed');
      const ok=valid&&equal(unb64(computed),unb64(row.verifier));
      const next=ok?0:failures+1;
      this.put('person',{...row,list_failures:next,list_locked_until:next>=5?now+LIST_LOCK_MS:null});
      if(!ok)return fail(403,'secret_failed');
      this.prune(now);
      // 他人の口座は触れない: VM がこの人に押し上げた口座だけ。
      const summary=this.ctx.storage.kv.get('activity:'+action.account);
      if(!summary||summary.expires_at<=now)return fail(404,'not_found');
      if(action.kind==='cancel'&&!summary.rows.some(r=>(r.kind==='held'||r.kind==='scheduled')&&r.draft_id===action.draft_id))return fail(404,'not_found');
      const pending=[...this.ctx.storage.kv.list({prefix:'action:'})].filter(([,a])=>a.status==='pending').length;
      if(pending>=ACTION_PENDING_MAX)return fail(409,'too_many_actions');
      const id=opaque(),stored={id,...action,status:'pending',reason:null,created_at:now,expires_at:now+ACTION_TTL};
      if(sha256)stored.sha256=sha256;
      this.put('action:'+id,stored);
      return {status:200,body:{id,kind:action.kind,account:action.account}};
    });
    if(result.status!==200)return result;
    await this.wake(this.now()+ACTION_TTL);
    return bearer?{status:200,body:{...result.body,bearer}}:result;
  }
  // ---- /activity の入口（設計 3.11.0 の一覧の入口を 3.12.0 §3.4 が使い続けている）。
  // session `view:<token の SHA-256>` は 10 分。この人の object にだけある。
  // /activity に入る。照合は PBKDF2 100,000。失敗は入口だけの回数で数え、5 回で 15 分閉じる。
  // 15 分たてば回数ごと自然に戻り、管理者の unlock でもすぐ戻る。名前の無い人にも同じだけ
  // 計算する（有無を時間で見せない）。
  async openList(secret,tokenHash){
    if(typeof tokenHash!=='string'||!/^[a-f0-9]{64}$/.test(tokenHash))return fail();
    const before=this.ctx.storage.kv.get('person');
    const valid=typeof secret==='string'&&secret.length>=16&&secret.length<=128;
    const computed=valid?await verifier(secret,typeof before?.salt==='string'?before.salt:'A'.repeat(43)):null;
    const result=this.atomic(()=>{
      const row=this.ctx.storage.kv.get('person');
      const now=this.now(),counted=row?.list_failures??0;
      // 閉じてから 15 分たてば、失敗の数も 0 に戻る。
      const failures=counted>=5&&now>=(row.list_locked_until??0)?0:counted;
      if(!before?.verifier||!row?.verifier||row.generation!==before.generation||!this.current(row.generation)||failures>=5)return null;
      const ok=valid&&equal(unb64(computed),unb64(row.verifier));
      const next=ok?0:failures+1;
      this.put('person',{...row,list_failures:next,list_locked_until:next>=5?now+LIST_LOCK_MS:null});
      if(!ok)return null;
      this.prune(now);
      const views=[...this.ctx.storage.kv.list({prefix:'view:'})].sort(([,a],[,b])=>a.expires_at-b.expires_at);
      for(const [key] of views.slice(0,Math.max(0,views.length-VIEW_MAX+1)))this.ctx.storage.kv.delete(key);
      const expires_at=now+TTL;
      this.put('view:'+tokenHash,{generation:row.generation,expires_at});
      return expires_at;
    });
    if(typeof result!=='number')return fail(403,'secret_failed');
    await this.wake(result);
    return {status:200,body:{expires_at:result}};
  }
  opened(tokenHash){
    const view=typeof tokenHash==='string'&&/^[a-f0-9]{64}$/.test(tokenHash)?this.ctx.storage.kv.get('view:'+tokenHash):null;
    return view&&this.now()<view.expires_at&&this.current(view.generation)?view:null;
  }
  closeList(tokenHash){return this.atomic(()=>{
    if(typeof tokenHash==='string'&&/^[a-f0-9]{64}$/.test(tokenHash))this.ctx.storage.kv.delete('view:'+tokenHash);
    return {status:200};
  });}
}
export class Account extends AtomicObject {
  active(){return this.ctx.storage.kv.get('revoked')!==true;}
  cleanupRegister(subject,account,due_at){return this.atomic(()=>{
    if(typeof subject!=='string'||! /^[a-f0-9]{64}$/.test(subject)||!PERSON.test(account)||!Number.isSafeInteger(due_at))return fail();
    const key='media_cleanup:'+subject,old=this.ctx.storage.kv.get(key);
    if(old&&(old.account!==account||old.due_at!==due_at))return fail(409,'cleanup_binding_mismatch');
    if(!old)this.put(key,{account,due_at,failed:false});
    return {status:200};
  });}
  cleanupFailed(subject){return this.atomic(()=>{
    const key='media_cleanup:'+subject,old=this.ctx.storage.kv.get(key);
    // A delayed notification cannot recreate a successfully removed obligation.
    if(old)this.put(key,{...old,failed:true});
    return {status:200};
  });}
  cleanupRemove(subject){return this.atomic(()=>{this.ctx.storage.kv.delete('media_cleanup:'+subject);if(![...this.ctx.storage.kv.list({prefix:'media_cleanup:'})].length)this.ctx.storage.kv.delete('media_cleanup_cursor');return {status:200};});}
  cleanupStatus(){
    let pending_count=0,failed_count=0;
    for(const [,row] of this.ctx.storage.kv.list({prefix:'media_cleanup:'})){
      if(row.due_at<=this.now()){pending_count++;if(row.failed)failed_count++;}
    }
    return {pending_count,failed_count,reason:failed_count?'cleanup_failed':pending_count?'cleanup_unconfirmed':null};
  }
  async cleanupRetry(account){
    let scheduled_count=0,unavailable_count=0;
    // The collection is private; only bounded counts leave this Worker.
    const selected=this.atomic(()=>{
      const due=[...this.ctx.storage.kv.list({prefix:'media_cleanup:'})].filter(([,row])=>row.due_at<=this.now());
      const cursor=this.ctx.storage.kv.get('media_cleanup_cursor')||'';
      const next=due.findIndex(([key])=>key>cursor),start=next<0?0:next;
      const batch=[...due.slice(start),...due.slice(0,start)].slice(0,64);
      // Advance before RPC: failures and concurrent retries cannot pin a prefix.
      if(batch.length)this.put('media_cleanup_cursor',batch.at(-1)[0]);
      return {batch,remaining:Math.max(0,due.length-batch.length)};
    });
    if(!Array.isArray(selected?.batch))return fail(503,'cleanup_retry_unavailable');
    for(const [key,row] of selected.batch){
      if(row.account!==account){unavailable_count++;continue;}
      try{
        const stub=this.env.MEDIA_OBJECT.get(this.env.MEDIA_OBJECT.idFromString(key.slice('media_cleanup:'.length)));
        const result=await stub.cleanupRetry(account);
        if(result.status!==200){unavailable_count++;continue;}
        scheduled_count++;
      }catch{unavailable_count++;}
    }
    return {status:200,body:{scheduled_count,unavailable_count,remaining_count:selected.remaining,reason:unavailable_count?'cleanup_retry_unavailable':null}};
  }

  async manage(operation,body,ticket,account){
    const result=this.atomic(()=>{
      if(!['status','revoke','cleanup-retry'].includes(operation)||!fields(body,[]))return fail();
      if(!this.replay(ticket))return fail(409,'replayed_request');
      if(operation==='status')return {status:200,body:{active:this.active(),cleanup:this.cleanupStatus()}};
      if(operation==='cleanup-retry')return {status:200};
      // This commit is the account revocation linearization point.
      this.put('revoked',true);return {status:200};
    });
    if(result.status!==200||operation==='status')return result;
    if(operation==='cleanup-retry')return this.cleanupRetry(account);
    return {status:200,body:{status:'revoked'}};
  }
  // `session:` は 3.12.0 までの承認ページの束（新しくは作らない・期限で消える）。
  async alarm(){
    let next=null;const now=this.now();
    this.ctx.storage.transactionSync(()=>{for(const [key,row] of this.ctx.storage.kv.list({prefix:'session:'})){
      if(row.expires_at<=now)this.ctx.storage.kv.delete(key);else next=Math.min(next??Infinity,row.expires_at);
    }});
    if(next!==null)await this.ctx.storage.setAlarm(next);
  }
}
