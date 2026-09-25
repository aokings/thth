import {DurableObject} from 'cloudflare:workers';
import {TTL,LIST_TTL,HEAD,LIST_MAX,VIEW_MAX,LIST_LOCK_MS,ITERATIONS,PERSON,fail,fields,opaque,verifier,equal,unb64,personStub,accountStub} from './approval.js';
import {HASH_PATTERN,STATE_PATTERN,digest} from './relay.js';
import {mediaStub} from './media.js';

// ---- 動きの一覧（設計 3.12.0 §3.4）の形。VM から来る要約も、ページから来る操作も、閉じた形だけ受ける。
export const ACTIVITY_TTL=3_600_000, ACTION_TTL=3_600_000, ACTION_PENDING_MAX=16;
const ACTIVITY_ACCOUNTS=8, ACTIVITY_ROWS=30, ACTIVITY_HEAD=60;
export const ACTION_KINDS=['stop','resume','cancel','settings','revoke','rotate'];
const ACTION_EXTRA={stop:[],resume:[],revoke:[],rotate:['sha256'],cancel:['draft_id'],settings:['key','value']};
export const SETTING_KEYS=['approval','daily_max_posts','daily_max_retracts','burst_count','burst_minutes','hold_minutes','min_interval_hours'];
const SETTING_VALUE=/^(?:none|publish|all|[0-9]{1,6}(?:\.[0-9]{1,2})?)$/;
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
export function validSummary(s){
  return same(s,['account','generated_at','approval','scheduled','limits','stopped','today','credential','rows'])&&
    typeof s.account==='string'&&PERSON.test(s.account)&&typeof s.generated_at==='string'&&ISO.test(s.generated_at)&&
    ['none','publish','all'].includes(s.approval)&&typeof s.scheduled==='boolean'&&
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
  atomic(fn){try{return this.ctx.storage.transactionSync(fn);}catch{return fail(503,'approval_unavailable');}}
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
export class ApprovalPerson extends AtomicObject {
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
  // 招待（3.10.0）の完了ページが本人の承認 secret を 1 回だけ作る。既存の承認者は
  // 上書きしない（運営者の approver set・失効・別の招待の人）。同じ招待のやり直しだけ通す。
  provision(salt,verifier,origin){return this.atomic(()=>{
    if(typeof salt!=='string'||!STATE_PATTERN.test(salt)||typeof verifier!=='string'||!STATE_PATTERN.test(verifier)||
       typeof origin!=='string'||!HASH_PATTERN.test(origin))return fail();
    const old=this.ctx.storage.kv.get('person');
    if(old&&old.invite!==origin)return fail(409,'approver_exists');
    this.put('person',{salt,verifier,iterations:ITERATIONS,active:true,generation:opaque(),failures:0,invite:origin});
    return {status:200,body:{status:'configured'}};
  });}
  current(generation=null){
    const row=this.ctx.storage.kv.get('person');
    return row?.active&&row.failures<5&&(!generation||generation===row.generation)?row.generation:null;
  }
  async check(secret,generation,binding){
    const before=this.ctx.storage.kv.get('person');
    if(!this.current(generation))return null;
    const valid=typeof secret==='string' && secret.length>=16 && secret.length<=128;
    const computed=valid?await verifier(secret,before.salt):null;
    const result=this.atomic(()=>{
      const row=this.ctx.storage.kv.get('person');
      if(!row?.active||row.generation!==generation||row.failures>=5||this.now()>=binding.expires_at)return null;
      if(this.ctx.storage.kv.get('grant:'+binding.job_id))return null;
      const ok=valid && equal(unb64(computed),unb64(row.verifier));
      this.put('person',{...row,failures:ok?0:row.failures+1});
      if(!ok)return null;
      const approved_at=this.now();
      this.put('grant:'+binding.job_id,{...binding,generation,approved_at,status:'approved'});return approved_at;
    });
    if(typeof result!=='number')return null;
    const alarm=await this.ctx.storage.getAlarm();
    if(alarm===null||binding.expires_at<alarm)await this.ctx.storage.setAlarm(binding.expires_at);
    return result;
  }
  consume(generation,binding){return this.atomic(()=>{
    if(!this.current(generation))return fail(410,'approver_changed');
    const key='grant:'+binding.job_id, grant=this.ctx.storage.kv.get(key);
    if(!grant||grant.status!=='approved'||grant.generation!==generation||this.now()>=grant.expires_at||
       ['job_id','digest','account','kind','expires_at'].some(k=>grant[k]!==binding[k]))return fail(404,'not_ready');
    this.put(key,{...grant,status:'consumed'});return {status:200,body:{approved_at:grant.approved_at}};
  });}
  async alarm(){
    const now=this.now();let next=null;
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
      if(!row||!this.current(row.generation))return fail(409,'approver_unavailable');
      const now=this.now();this.prune(now);
      for(const [key] of this.ctx.storage.kv.list({prefix:'activity:'}))this.ctx.storage.kv.delete(key);
      for(const summary of body.accounts)this.put('activity:'+summary.account,{...summary,expires_at:now+ACTIVITY_TTL});
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
         !this.current(row.generation)||failures>=5)return fail(403,'approval_failed');
      const ok=valid&&equal(unb64(computed),unb64(row.verifier));
      const next=ok?0:failures+1;
      this.put('person',{...row,list_failures:next,list_locked_until:next>=5?now+LIST_LOCK_MS:null});
      if(!ok)return fail(403,'approval_failed');
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
  // ---- 承認待ちの一覧（設計 3.11.0）。索引 `listed:<job_id>` は承認ページの session の id と期限だけ
  // （本文は置かない）。一覧の session `view:<token の SHA-256>` は 10 分。どちらもこの人の object にだけある。
  async listJob(binding,generation){
    const result=this.atomic(()=>{
      if(!binding||typeof binding.job_id!=='string'||!STATE_PATTERN.test(binding.job_id)||typeof binding.session!=='string'||
         !/^[a-f0-9]{64}$/.test(binding.session)||!Number.isSafeInteger(binding.expires_at))return fail();
      if(!this.current(generation))return fail(409,'approver_unavailable');
      const now=this.now();this.prune(now);
      if([...this.ctx.storage.kv.list({prefix:'listed:'})].length>=LIST_MAX)return fail(409,'list_full');
      this.put('listed:'+binding.job_id,{session:binding.session,expires_at:binding.expires_at,generation});
      return {status:200};
    });
    if(result.status===200)await this.wake(binding.expires_at);
    return result;
  }
  // 一覧に入る。照合は承認ページと同じ（PBKDF2 100,000）。失敗は一覧だけの回数で数え、5 回で
  // 一覧を 15 分閉じる。15 分たてば回数ごと自然に戻り、管理者の unlock でもすぐ戻る。名前を知るだけの
  // 人が承認そのものを止められないよう、承認ページの失敗回数とは分ける。名前の無い人にも同じだけ
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
    if(typeof result!=='number')return fail(403,'approval_failed');
    await this.wake(result);
    return {status:200,body:{expires_at:result}};
  }
  opened(tokenHash){
    const view=typeof tokenHash==='string'&&/^[a-f0-9]{64}$/.test(tokenHash)?this.ctx.storage.kv.get('view:'+tokenHash):null;
    return view&&this.now()<view.expires_at&&this.current(view.generation)?view:null;
  }
  listView(tokenHash){
    const view=this.opened(tokenHash);if(!view)return fail(401,'unauthorized');
    const now=this.now();
    const rows=[...this.ctx.storage.kv.list({prefix:'listed:'})]
      .filter(([,row])=>row.expires_at>now&&row.generation===view.generation)
      .sort(([,a],[,b])=>a.expires_at-b.expires_at)
      .map(([key,row])=>({job_id:key.slice('listed:'.length),session:row.session}));
    return {status:200,body:{rows,expires_at:view.expires_at}};
  }
  listedSession(tokenHash,job_id){
    const view=this.opened(tokenHash);if(!view)return fail(401,'unauthorized');
    const row=typeof job_id==='string'&&STATE_PATTERN.test(job_id)?this.ctx.storage.kv.get('listed:'+job_id):null;
    if(!row||row.expires_at<=this.now()||row.generation!==view.generation)return fail(404,'not_found');
    return {status:200,body:{session:row.session}};
  }
  closeList(tokenHash){return this.atomic(()=>{
    if(typeof tokenHash==='string'&&/^[a-f0-9]{64}$/.test(tokenHash))this.ctx.storage.kv.delete('view:'+tokenHash);
    return {status:200};
  });}
}
export class ApprovalAccount extends AtomicObject {
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
    // The stop already holds if an invalidation RPC fails; a retry completes it.
    for(const [,row] of this.ctx.storage.kv.list({prefix:'session:'})){
      if(row.expires_at>this.now())await this.env.APPROVAL_SESSION.get(this.env.APPROVAL_SESSION.idFromString(row.session)).invalidate();
    }
    return {status:200,body:{status:'revoked'}};
  }
  async register(binding,generation,session){
    const result=this.atomic(()=>{
      if(!this.active())return fail(410,'account_revoked');
      if(this.now()>=binding.expires_at)return fail(410,'expired');
      const key='session:'+binding.job_id;
      if(this.ctx.storage.kv.get(key))return fail(409,'session_exists');
      this.put(key,{...binding,generation,session,status:'pending'});return {status:200};
    });
    if(result.status===200){const alarm=await this.ctx.storage.getAlarm();if(alarm===null||binding.expires_at<alarm)await this.ctx.storage.setAlarm(binding.expires_at);}
    return result;
  }
  grant(binding,generation,approved_at){return this.atomic(()=>{
    const key='session:'+binding.job_id,row=this.ctx.storage.kv.get(key);
    if(!this.active())return fail(410,'account_revoked');
    if(!this.matches(row,binding,generation)||row.status!=='pending')return fail(404,'not_ready');
    this.put(key,{...row,status:'approved',approved_at});return {status:200};
  });}
  matches(row,binding,generation){return row&&row.generation===generation&&this.now()<row.expires_at&&
    ['job_id','digest','account','kind','expires_at'].every(k=>row[k]===binding[k]);}
  consume(binding,generation,approved_at){return this.atomic(()=>{
    const key='session:'+binding.job_id,row=this.ctx.storage.kv.get(key);
    // No awaited status result can authorize this atomic final gate.
    if(!this.active())return fail(410,'account_revoked');
    if(!this.matches(row,binding,generation)||row.status!=='approved'||row.approved_at!==approved_at)return fail(404,'not_ready');
    this.put(key,{...row,status:'consumed'});return {status:200};
  });}
  async alarm(){
    let next=null;const now=this.now();
    this.ctx.storage.transactionSync(()=>{for(const [key,row] of this.ctx.storage.kv.list({prefix:'session:'})){
      if(row.expires_at<=now)this.ctx.storage.kv.delete(key);else next=Math.min(next??Infinity,row.expires_at);
    }});
    if(next!==null)await this.ctx.storage.setAlarm(next);
  }
}
// Attachment rows are display-only: they never bind a receipt and never leave
// the approval page. The key set is closed so an unknown field cannot ride in.
const ATTACHMENT_KEYS=['index','role','kind','format','public_sha256','public_size','width','height','duration','alt','preview'];
const FORMAT_PATTERN=/^[a-z0-9]{2,16}$/;
export function validAttachments(list){
  if(!Array.isArray(list)||list.length>20)return false;
  return list.every(a=>a!==null&&typeof a==='object'&&!Array.isArray(a)&&
    Object.keys(a).sort().join(',')===[...ATTACHMENT_KEYS].sort().join(',')&&
    Number.isSafeInteger(a.index)&&a.index>=1&&a.index<=1000&&
    ['media','thumbnail','caption'].includes(a.role)&&
    ['image','video','audio','caption'].includes(a.kind)&&
    typeof a.format==='string'&&FORMAT_PATTERN.test(a.format)&&
    typeof a.public_sha256==='string'&&HASH_PATTERN.test(a.public_sha256)&&
    Number.isSafeInteger(a.public_size)&&a.public_size>=0&&
    [a.width,a.height].every(v=>v===null||(Number.isSafeInteger(v)&&v>=0&&v<=1_000_000))&&
    (a.duration===null||(typeof a.duration==='number'&&Number.isFinite(a.duration)&&a.duration>=0&&a.duration<=86_400))&&
    typeof a.alt==='string'&&a.alt.length<=4096&&
    (a.preview===null||(a.kind==='image'&&typeof a.preview==='string'&&STATE_PATTERN.test(a.preview))));
}
export function validTyped(value){return typeof value==='string'&&new TextEncoder().encode(value).length<=16_384;}

export class ApprovalSession extends AtomicObject {
  row(){const row=this.ctx.storage.kv.get('session');
    if(row&&this.now()>=row.expires_at){this.clear(row,'expired');return null;}return row;}
  clear(row,status){
    // A resolved page must stop showing its images: note every preview this row
    // still carries before the attachments are dropped, then `settle()` revokes
    // them outside the storage transaction this runs inside.
    this.collect(row);
    // Keep only receipt binding and expiry; never display text/metadata/CSRF after resolution.
    const {person,generation,job_id,digest,account,kind,expires_at,read_key_hash,approved_at}=row;
    this.put('session',{person,generation,job_id,digest,account,kind,expires_at,read_key_hash,
      ...(approved_at?{approved_at}:{}),status});
  }
  collect(row){
    if(!this.env.MEDIA_OBJECT||!Array.isArray(row?.attachments))return;
    for(const a of row.attachments)if(a&&typeof a.preview==='string')(this.revoking??=[]).push(a.preview);
  }
  async settle(){
    // Previews are display-only: a capability that is already gone (404/410) or
    // a media object that cannot answer must never change an approval outcome.
    const list=this.revoking;if(!list?.length)return;this.revoking=[];
    for(const capability of list){try{await (await mediaStub(this.env,capability)).invalidate();}catch{}}
  }
  async invalidate(){const row=this.ctx.storage.kv.get('session');if(row)this.clear(row,'expired');await this.settle();}
  async manage(operation,body,ticket){try{return await this.handle(operation,body,ticket);}finally{await this.settle();}}
  async handle(operation,body,ticket){
    if(operation==='create'){
      const required=['person','job_id','digest','account','kind','text','context','read_key_hash'];
      if(!body||typeof body!=='object'||Array.isArray(body))return fail();
      const present=Object.keys(body);
      if(required.some(k=>!present.includes(k))||present.some(k=>!required.includes(k)&&!['attachments','typed','listed'].includes(k)))return fail();
      if(('attachments' in body&&!validAttachments(body.attachments))||('typed' in body&&!validTyped(body.typed))||('listed' in body&&typeof body.listed!=='boolean'))return fail();
      if(['person','account','job_id','digest','read_key_hash'].some(k=>typeof body[k]!=='string')||!PERSON.test(body.person)||!PERSON.test(body.account)||!STATE_PATTERN.test(body.job_id)||!HASH_PATTERN.test(body.digest)||!HASH_PATTERN.test(body.read_key_hash)||!['approve','send','retract'].includes(body.kind)||typeof body.text!=='string'||!body.text||new TextEncoder().encode(body.text).length>48_000||!fields(body.context,['media','reply_to','publish_at','target','reason','topic','options'])||!['threads','mastodon','bluesky','x'].includes(body.context.media)||Object.values(body.context).some(v=>v!==null&&(typeof v!=='string'||v.length>4096)))return fail();
      const authority=await accountStub(this.env,body.account);
      if(!await authority.active())return fail(410,'account_revoked');
      const generation=await (await personStub(this.env,body.person)).current();
      if(!generation)return fail(409,'approver_unavailable');
      // 一覧に出す承認（3.11.0）は最大 24 時間まで待てる。画像の表示（capability）は 10 分より
      // 延ばせないので、画像を見せる承認は延ばさない（一覧には出すが期限は 10 分のまま）。
      const listed=body.listed===true;
      const extended=listed&&!(body.attachments??[]).some(a=>typeof a.preview==='string');
      const deadline=this.now()+(extended?LIST_TTL:TTL);
      try{const created=await this.ctx.storage.transaction(async()=>{
        const result=this.atomic(()=>{
          if(!this.replay(ticket))return fail(409,'replayed_request');
          if(this.ctx.storage.kv.get('session'))return fail(409,'session_exists');
          this.put('session',{...body,listed,generation,status:'pending',csrf:opaque(),failures:0,expires_at:deadline,page_until:0});
          return {status:201,body:{status:'pending',expires_at:deadline}};
        });
        if(result.status===201)await this.ctx.storage.setAlarm(deadline);return result;
      });
        if(created.status!==201)return created;
        // A preview must not outlive the page that shows it: lower each grant to
        // this session's deadline. A grant that is already gone stays gone.
        const shown=this.ctx.storage.kv.get('session');
        if(this.env.MEDIA_OBJECT&&Array.isArray(shown?.attachments))
          for(const a of shown.attachments)if(a&&typeof a.preview==='string'){
            try{await (await mediaStub(this.env,a.preview)).boundExpiry(deadline);}catch{}}
        const registered=await authority.register(this.binding(this.row()),generation,this.ctx.id.toString());
        if(registered.status!==200){await this.invalidate();return registered;}
        if(listed){
          const indexed=await (await personStub(this.env,body.person)).listJob({job_id:body.job_id,session:this.ctx.id.toString(),expires_at:deadline},generation);
          if(indexed.status!==200){await this.invalidate();return indexed;}
        }
        return created;
      }catch{return fail(503,'approval_unavailable');}
    }
    if(!['consume','status','cancel'].includes(operation)||!fields(body,['read_key'])||typeof body.read_key!=='string'||!STATE_PATTERN.test(body.read_key))return fail();
    const hash=await digest(body.read_key), before=this.row();
    if(!before)return fail(404,'not_found');
    if(!equal(new TextEncoder().encode(hash),new TextEncoder().encode(before.read_key_hash)))return fail(401,'unauthorized');
    const admission=this.atomic(()=>{
      if(!this.replay(ticket))return fail(409,'replayed_request');
      const row=this.row();if(!row)return fail(404,'not_found');
      if(operation==='cancel'){this.clear(row,'expired');return {status:200,body:{status:'expired'}};}
      return {status:200};
    });
    if(admission.status!==200||operation==='cancel')return admission;
    const authority=await accountStub(this.env,before.account);
    if(!await authority.active()){await this.invalidate();return fail(410,'account_revoked');}
    const person=await personStub(this.env,before.person);
    if(operation==='status'){
      const current=await person.current(before.generation), row=this.row();
      if(!row)return fail(404,'not_found');
      if(!current){this.clear(row,'expired');return fail(410,'approver_changed');}
      return {status:200,body:{status:row.status,expires_at:row.expires_at}};
    }
    const row=this.row();if(!row||row.status!=='approved')return fail(404,'not_ready');
    const result=await person.consume(row.generation,this.binding(row));
    if(result.status!==200)return result;
    const permission=await authority.consume(this.binding(row),row.generation,result.body.approved_at);
    if(permission.status!==200)return permission;
    // Person.consume is the authoritative one-shot generation gate. A subsequent
    // local storage failure must never redeliver its receipt: VM reports unknown.
    return this.atomic(()=>{
      const latest=this.row();if(!latest||latest.status!=='approved')return fail(409,'outcome_unknown');
      this.clear(latest,'consumed');
      const {job_id,digest,account,kind,person:approver,generation,expires_at}=row;
      return {status:200,body:{job_id,digest,account,kind,approver,generation,approved_at:result.body.approved_at,expires_at}};
    });
  }
  binding(row){const {job_id,digest,account,kind,expires_at}=row;return {job_id,digest,account,kind,expires_at};}

  // `person` は一覧（/pending/<job_id>）から開いたときだけ渡る: その人あての承認でなければ見せない。
  async view(person=null){try{return await this.render(person);}finally{await this.settle();}}
  async render(person){
    const before=this.row();if(!before||before.status!=='pending')return fail(410,'expired');
    if(person!==null&&before.person!==person)return fail(404,'not_found');
    if(!await (await accountStub(this.env,before.account)).active()||!await (await personStub(this.env,before.person)).current(before.generation)){
      this.clear(before,'expired');return fail(410,'approver_unavailable');}
    const row=this.row();if(!row||row.status!=='pending')return fail(410,'expired');
    const {text,account,kind,digest,csrf,context,attachments,typed}=row;
    const live=await this.livePreviews(attachments);
    let page_minutes=null;
    if(row.listed===true){
      // 一覧に出した承認: 承認ページの 10 分は開いた時点から数え直す。job の期限（最大 24 時間）は越えない。
      const now=this.now(),until=Math.min(now+TTL,row.expires_at);
      const saved=this.atomic(()=>{const current=this.row();if(!current||current.status!=='pending')return false;
        this.put('session',{...current,page_until:until});return true;});
      if(saved!==true)return fail(410,'expired');
      page_minutes=Math.max(0,Math.ceil((until-now)/60_000));
    }
    // Only the page sees attachments. `clear()` drops them before any receipt.
    return {status:200,body:{text,account,kind,digest,csrf,context,attachments:attachments??[],typed:typed??null,
      live,listed:row.listed===true,page_minutes}};
  }
  // 一覧の 1 行（3.11.0）。その人あての・一覧に出した・まだ待っている承認だけ。本文は先頭 60 字まで。
  async summary(person){
    const before=this.row();
    if(!before||before.status!=='pending'||before.listed!==true||before.person!==person)return fail(404,'not_found');
    if(!await (await accountStub(this.env,before.account)).active()||!await (await personStub(this.env,before.person)).current(before.generation))return fail(404,'not_found');
    const row=this.row();if(!row||row.status!=='pending')return fail(404,'not_found');
    const chars=Array.from(row.text);
    return {status:200,body:{job_id:row.job_id,kind:row.kind,account:row.account,head:chars.slice(0,HEAD).join(''),more:chars.length>HEAD,
      attachments:(Array.isArray(row.attachments)&&row.attachments.length>0)||(typeof row.typed==='string'&&row.typed!==''),
      remaining:Math.max(0,Math.ceil((row.expires_at-this.now())/60_000))}};
  }
  // A page that cannot show one of its images must not offer the approve form:
  // ask each image's media object whether it would still serve those bytes.
  // Anything but a live answer — a retired grant, a revoked account, a missing
  // binding, an RPC that throws — counts as not showable.
  async livePreviews(rows){
    for(const row of Array.isArray(rows)?rows:[]){
      if(row?.kind!=='image'||typeof row.preview!=='string')continue;
      if(!this.env.MEDIA_OBJECT)return false;
      try{if(await (await mediaStub(this.env,row.preview)).live()!==true)return false;}catch{return false;}
    }
    return true;
  }
  async approve(secret,csrf,person=null){try{return await this.attempt(secret,csrf,person);}finally{await this.settle();}}
  async attempt(secret,csrf,person){
    const row=this.row();if(!row||row.status!=='pending')return fail(410,'expired');
    if(person!==null&&row.person!==person)return fail(404,'not_found');
    if(typeof csrf!=='string'||!STATE_PATTERN.test(csrf)||!equal(unb64(csrf),unb64(row.csrf)))return fail(403,'forbidden');
    // Reserve an attempt synchronously before the KDF so concurrent POSTs cannot exceed five.
    const allowed=this.atomic(()=>{
      const current=this.row();if(!current||current.status!=='pending'||current.failures>=5)return false;
      // 一覧に出した承認は、開いてから 10 分を過ぎたページでは押せない（開き直す）。
      if(current.listed===true&&this.now()>=(current.page_until??0))return 'page_expired';
      this.put('session',{...current,failures:current.failures+1});return true;
    });
    if(allowed==='page_expired')return fail(410,'page_expired');
    if(allowed!==true)return fail(410,'expired');
    const approved_at=await (await personStub(this.env,row.person)).check(secret,row.generation,this.binding(row));
    if(approved_at!==null){
      const grant=await (await accountStub(this.env,row.account)).grant(this.binding(row),row.generation,approved_at);
      if(grant.status!==200){await this.invalidate();return grant;}
    }
    return this.atomic(()=>{
      const current=this.row();if(!current||current.status!=='pending')return fail(410,'expired');
      if(approved_at===null){if(current.failures>=5)this.clear(current,'expired');return fail(403,'approval_failed');}
      this.clear({...current,approved_at},'approved');return {status:200,body:{status:'approved',kind:current.kind}};
    });
  }
  async alarm(){
    const row=this.ctx.storage.kv.get('session');
    if(!row||this.now()>=row.expires_at){this.collect(row);await this.ctx.storage.deleteAll();await this.settle();}
    else await this.ctx.storage.setAlarm(row.expires_at);
  }
}
