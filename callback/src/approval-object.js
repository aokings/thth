import {DurableObject} from 'cloudflare:workers';
import {TTL,ITERATIONS,PERSON,fail,fields,opaque,verifier,equal,unb64,personStub,accountStub} from './approval.js';
import {HASH_PATTERN,STATE_PATTERN,digest} from './relay.js';
import {mediaStub} from './media.js';

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
    else this.put('person',{...old,failures:0});
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
    this.ctx.storage.transactionSync(()=>{for(const [key,row] of this.ctx.storage.kv.list({prefix:'grant:'})){
      if(row.expires_at<=now)this.ctx.storage.kv.delete(key);else next=Math.min(next??Infinity,row.expires_at);
    }});
    if(next!==null)await this.ctx.storage.setAlarm(next);
  }
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
      if(required.some(k=>!present.includes(k))||present.some(k=>!required.includes(k)&&!['attachments','typed'].includes(k)))return fail();
      if(('attachments' in body&&!validAttachments(body.attachments))||('typed' in body&&!validTyped(body.typed)))return fail();
      if(['person','account','job_id','digest','read_key_hash'].some(k=>typeof body[k]!=='string')||!PERSON.test(body.person)||!PERSON.test(body.account)||!STATE_PATTERN.test(body.job_id)||!HASH_PATTERN.test(body.digest)||!HASH_PATTERN.test(body.read_key_hash)||!['approve','send','retract'].includes(body.kind)||typeof body.text!=='string'||!body.text||new TextEncoder().encode(body.text).length>48_000||!fields(body.context,['media','reply_to','publish_at','target','reason','topic','options'])||!['threads','mastodon','bluesky','x'].includes(body.context.media)||Object.values(body.context).some(v=>v!==null&&(typeof v!=='string'||v.length>4096)))return fail();
      const authority=await accountStub(this.env,body.account);
      if(!await authority.active())return fail(410,'account_revoked');
      const generation=await (await personStub(this.env,body.person)).current();
      if(!generation)return fail(409,'approver_unavailable');
      const deadline=this.now()+TTL;
      try{const created=await this.ctx.storage.transaction(async()=>{
        const result=this.atomic(()=>{
          if(!this.replay(ticket))return fail(409,'replayed_request');
          if(this.ctx.storage.kv.get('session'))return fail(409,'session_exists');
          this.put('session',{...body,generation,status:'pending',csrf:opaque(),failures:0,expires_at:deadline});
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

  async view(){try{return await this.render();}finally{await this.settle();}}
  async render(){
    const before=this.row();if(!before||before.status!=='pending')return fail(410,'expired');
    if(!await (await accountStub(this.env,before.account)).active()||!await (await personStub(this.env,before.person)).current(before.generation)){
      this.clear(before,'expired');return fail(410,'approver_unavailable');}
    const row=this.row();if(!row||row.status!=='pending')return fail(410,'expired');
    const {text,account,kind,digest,csrf,context,attachments,typed}=row;
    // Only the page sees attachments. `clear()` drops them before any receipt.
    return {status:200,body:{text,account,kind,digest,csrf,context,attachments:attachments??[],typed:typed??null,
      live:await this.livePreviews(attachments)}};
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
  async approve(secret,csrf){try{return await this.attempt(secret,csrf);}finally{await this.settle();}}
  async attempt(secret,csrf){
    const row=this.row();if(!row||row.status!=='pending')return fail(410,'expired');
    if(typeof csrf!=='string'||!STATE_PATTERN.test(csrf)||!equal(unb64(csrf),unb64(row.csrf)))return fail(403,'forbidden');
    // Reserve an attempt synchronously before the KDF so concurrent POSTs cannot exceed five.
    const allowed=this.atomic(()=>{
      const current=this.row();if(!current||current.status!=='pending'||current.failures>=5)return false;
      this.put('session',{...current,failures:current.failures+1});return true;
    });
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
