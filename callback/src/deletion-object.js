import {DurableObject} from 'cloudflare:workers';
import {fields,opaque,fail} from './person.js';
import {STATE_PATTERN,digest} from './relay.js';
import {RETENTION} from './deletion.js';
export class DeletionInbox extends DurableObject {
  now(){return Date.now();}
  atomic(fn){try{return this.ctx.storage.transactionSync(fn);}catch{return fail(503,'deletion_unavailable');}}
  row(code){const row=this.ctx.storage.kv.get('receipt:'+code);return row&&this.now()<row.expires_at?row:null;}
  async receive(blob){
    if(typeof blob!=='string'||blob.length>8192||!(/^[A-Za-z0-9_-]{43}\.[A-Za-z0-9_-]+$/).test(blob))return fail();
    const blob_sha256=await digest(blob),code=opaque(),now=this.now();
    const result=this.atomic(()=>{
      for(const [key,row] of this.ctx.storage.kv.list({prefix:'receipt:'}))if(row.expires_at<=now)this.ctx.storage.kv.delete(key);
      if([...this.ctx.storage.kv.list({prefix:'receipt:',limit:1000})].length>=1000)return fail(503,'deletion_capacity');
      this.ctx.storage.kv.put('receipt:'+code,{blob,blob_sha256,status:'unverified',received_at:now,expires_at:now+RETENTION});
      return {status:200,body:{confirmation_code:code}};
    });
    if(result.status===200){const alarm=await this.ctx.storage.getAlarm();if(alarm===null)await this.ctx.storage.setAlarm(now+RETENTION);}
    return result;
  }
  status(code){const row=this.row(code);return row?{status:200,body:{status:row.status,received_at:row.received_at,
    ...(row.verified_at?{verified_at:row.verified_at}:{}),...(row.completed_at?{completed_at:row.completed_at}:{})}}:fail(404,'not_found');}
  manage(operation,body,ticket,subject){return this.atomic(()=>{
    const now=this.now();
    if(!ticket||!STATE_PATTERN.test(ticket.nonce)||!Number.isSafeInteger(ticket.time)||Math.abs(now-ticket.time)>60000)return fail(401,'unauthorized');
    const nonces=this.ctx.storage.kv.get('nonces')||{};
    for(const [key,time] of Object.entries(nonces))if(time<now)delete nonces[key];
    if(nonces[ticket.nonce]||Object.keys(nonces).length>=1024)return fail(409,'replayed_request');
    nonces[ticket.nonce]=ticket.time+60001;this.ctx.storage.kv.put('nonces',nonces);
    if(operation==='list'){
      if(subject!=='inbox'||!fields(body,['after'])||body.after!==null&&(typeof body.after!=='string'||!STATE_PATTERN.test(body.after)))return fail();
      const entries=[...this.ctx.storage.kv.list({prefix:'receipt:'})].filter(([key,row])=>row.expires_at>now&&(!body.after||key>'receipt:'+body.after));
      const page=entries.slice(0,50);
      return {status:200,body:{receipts:page.map(([key,row])=>({receipt:key.slice(8),status:row.status,received_at:row.received_at})),next:entries.length>50?page.at(-1)[0].slice(8):null}};
    }
    if(!STATE_PATTERN.test(subject))return fail();
    if(operation==='discard'){
      if(!fields(body,['blob_sha256'])||typeof body.blob_sha256!=='string'||!(/^[0-9a-f]{64}$/).test(body.blob_sha256))return fail();
      const row=this.row(subject),previous=this.ctx.storage.kv.get('discarded:'+subject);
      if(!row)return previous&&previous.expires_at>now&&previous.blob_sha256===body.blob_sha256?{status:200,body:{status:'discarded'}}:fail(404,'not_found');
      if(row.status!=='unverified'||row.blob_sha256!==body.blob_sha256)return fail(409,'binding_changed');
      // Only the signed operator can attest a locally confirmed HMAC mismatch.
      // Keep no blob/account, only retry binding until the original expiry.
      this.ctx.storage.kv.put('discarded:'+subject,{blob_sha256:row.blob_sha256,expires_at:row.expires_at});
      this.ctx.storage.kv.delete('receipt:'+subject);
      return {status:200,body:{status:'discarded'}};
    }
    const row=this.row(subject);if(!row)return fail(404,'not_found');
    if(operation==='read'){
      if(!fields(body,[]))return fail();
      return {status:200,body:{status:row.status,received_at:row.received_at,expires_at:row.expires_at,...(row.blob?{signed_request:row.blob}:{}),...(row.account?{account:row.account}:{}),...(row.operation_id?{operation_id:row.operation_id}:{})}};
    }
    if(operation==='verify'){
      if(!fields(body,['account','blob_sha256'])||typeof body.account!=='string'||typeof body.blob_sha256!=='string'||!(/^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,63}$/).test(body.account)||!(/^[0-9a-f]{64}$/).test(body.blob_sha256))return fail();
      // Hash was captured at ingress. The trusted VM has checked the actual
      // provider signature and identity; this is not a public verification API.
      if(row.status!=='unverified')return row.account===body.account&&row.blob_sha256===body.blob_sha256?{status:200,body:{status:row.status}}:fail(409,'binding_changed');
      if(row.blob_sha256!==body.blob_sha256)return fail(409,'binding_changed');
      const {blob,...safe}=row;this.ctx.storage.kv.put('receipt:'+subject,{...safe,account:body.account,status:'verified',verified_at:now});
      return {status:200,body:{status:'verified'}};
    }
    if(operation==='complete'){
      if(!fields(body,['account','operation_id','completed_at'])||!Number.isSafeInteger(body.completed_at)||body.completed_at<0||body.completed_at>now||typeof body.operation_id!=='string'||!STATE_PATTERN.test(body.operation_id)||row.account!==body.account||!['verified','completed'].includes(row.status))return fail(409,'unverified');
      if(row.status==='completed')return row.operation_id===body.operation_id&&row.completed_at===body.completed_at?{status:200,body:{status:'completed'}}:fail(409,'binding_changed');
      this.ctx.storage.kv.put('receipt:'+subject,{...row,status:'completed',operation_id:body.operation_id,completed_at:body.completed_at});
      return {status:200,body:{status:'completed'}};
    }
    return fail();
  });}
  async alarm(){let next=null;const now=this.now();this.ctx.storage.transactionSync(()=>{
    for(const prefix of ['receipt:','discarded:'])for(const [key,row] of this.ctx.storage.kv.list({prefix})){
      if(row.expires_at<=now)this.ctx.storage.kv.delete(key);else next=Math.min(next??Infinity,row.expires_at);
    }
  });if(next!==null)await this.ctx.storage.setAlarm(next);}
}
