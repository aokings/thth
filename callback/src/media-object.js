import {DurableObject} from 'cloudflare:workers';
import {opaque,accountStub} from './approval.js';
import {reply} from './relay.js';
import {MEDIA_TTL,RAW_RETENTION,MEDIA_NAME,MIME,MULTIPART_THRESHOLD,MIN_PART,fail,keys,validHash,validOpaque,mediaStub} from './media.js';

// One independently owned upload or public grant per object. No constructor
// writes: arbitrary unknown URL reads cannot allocate persistent records.
export class MediaObject extends DurableObject {
  now(){return Date.now();}
  row(){return this.ctx.storage.kv.get('media');}
  put(row){this.ctx.storage.kv.put('media',row);}
  atomic(fn){return this.ctx.storage.transactionSync(fn);}
  current(row){return row&&this.now()<row.expires_at&&!['retired','failed'].includes(row.status);}
  async active(account){return !!this.env.APPROVAL_ACCOUNT&&await(await accountStub(this.env,account)).active();}
  async cleanupAuthority(row){return accountStub(this.env,row.account);}
  async enroll(row){
    const result=await(await this.cleanupAuthority(row)).cleanupRegister(this.ctx.id.toString(),row.account,row.cleanup_at);
    if(result.status!==200)throw Error('cleanup_registration_unconfirmed');
    this.atomic(()=>{const current=this.row();if(current?.version===row.version)this.put({...current,cleanup_registered:true});});
  }
  async writing(row,operation){
    // A confirmed R2 write and its compensation finish before this marker clears.
    // Rejected write promises may still have remote effects: retain the marker.
    // If the isolate disappears while awaiting R2, the durable marker remains
    // unknown; an alarm/retry must not infer completion from an absent object.
    const token=opaque();let started=false,settled=false;
    this.atomic(()=>{const current=this.row();
      if(!this.current(current)||current.version!==row.version||current.io_ticket)throw Error('media_write_not_current');
      this.put({...current,io_ticket:token});
    });
    const write=async fn=>{started=true;const result=await fn();settled=true;return result;};
    try{return await operation(write);}
    finally{if(!started||settled)this.atomic(()=>{const current=this.row();if(current?.version===row.version&&current.io_ticket===token)this.put({...current,io_ticket:null});});}
  }
  async cleanupRetry(account){
    // A retry only restarts deletion; it cannot renew any public capability.
    return this.ctx.storage.transaction(async()=>{
      const changed=this.atomic(()=>{
        const row=this.row();if(!row||row.account!==account||this.now()<row.cleanup_at)return fail(409,'cleanup_not_due');
        this.put({...row,status:'retired',cleanup_attempts:0,cleanup_failed:false});return {status:200};
      });
      if(changed.status===200)await this.ctx.storage.setAlarm(this.now());
      return changed;
    });
  }
  replay(ticket){
    const now=this.now(),seen=this.ctx.storage.kv.get('nonces')||{};
    for(const [n,t] of Object.entries(seen))if(now-t>60_000)delete seen[n];
    if(seen[ticket.nonce]||Object.keys(seen).length>=512)return false;
    seen[ticket.nonce]=ticket.time;this.ctx.storage.kv.put('nonces',seen);return true;
  }
  bound(row,body){return row&&body.actor===row.actor&&body.account===row.account&&body.sha256===row.sha256;}
  async schedule(row){await this.ctx.storage.setAlarm(Math.min(row.expires_at,row.cleanup_at));}
  async manage(op,body,ticket){
    if(op==='create')return this.create(body,ticket);
    if(op==='preview'||op==='provider')return this.grant(op,body,ticket);
    if(op==='published'||op==='invalidate')return this.publicResult(op,body,ticket);
    if(!keys(body,['actor','account','sha256']))return fail();
    const before=this.row();if(!this.bound(before,body))return fail(404,'not_found');
    if(!await this.active(before.account))return fail(410,'account_revoked');
    const claim=this.atomic(()=>{
      const row=this.row();if(!this.bound(row,body)||!this.current(row))return fail(410,'media_expired');
      // Unknown I/O is checked before any claim: `complete` must not leave a
      // `completing` state (or consume a nonce) while a write is unresolved.
      if(row.io_ticket)return fail(409,'media_upload_unconfirmed_pending');
      if(!this.replay(ticket))return fail(409,'replayed_request');
      if(op==='status')return {status:200,body:{status:row.status,size:row.size,sha256:row.sha256,expires_at:row.expires_at}};
      if(op==='read')return row.status==='ready'&&['source','sanitized'].includes(row.kind)?{status:200,row}:fail(409,'media_not_ready');
      if(op==='ack'){
        if(row.status!=='ready'||row.kind!=='source')return fail(409,'media_not_ready');
        this.put({...row,status:'retired'});return {status:200,body:{status:'retired'}};
      }
      if(op==='complete'){
        if(row.status==='uploaded'&&!row.upload_id){this.put({...row,status:'ready'});return {status:200,body:{status:'ready',sha256:row.sha256}};}
        if(row.status!=='pending'||!row.upload_id||Object.keys(row.parts).length!==row.part_count)return fail(409,'media_not_ready');
        const ticket=opaque();this.put({...row,status:'completing',ticket});return {status:202,row:{...row,ticket}};
      }
      return fail();
    });
    if(claim.row&&op==='read')return this.read(claim.row);
    if(claim.status===202&&op==='complete')return this.complete(claim.row);
    return claim;
  }
  async create(body,ticket){
    if(!keys(body,['actor','account','sha256','size','mime','kind','part_size'])||typeof body.actor!=='string'||!MEDIA_NAME.test(body.actor)||typeof body.account!=='string'||!MEDIA_NAME.test(body.account)||!validHash(body.sha256)||!Number.isSafeInteger(body.size)||body.size<1||!MIME.has(body.mime)||!['source','sanitized'].includes(body.kind))return fail();
    const sizeLimit=body.kind==='source'?1_000_000_000:body.mime.startsWith('image/')?8_000_000:1_000_000_000;
    if(body.size>sizeLimit)return fail(413,'media_size_exceeded');
    const multi=body.size>MULTIPART_THRESHOLD;
    if(multi?(!Number.isSafeInteger(body.part_size)||body.part_size<MIN_PART||body.part_size>5*1024**3||Math.ceil(body.size/body.part_size)>10000):body.part_size!==null)return fail();
    if(!await this.active(body.account))return fail(410,'account_revoked');
    const result=await this.ctx.storage.transaction(async()=>{const claimed=this.atomic(()=>{
      if(this.row())return fail(409,'media_exists');
      if(!this.replay(ticket))return fail(409,'replayed_request');
      const now=this.now(),row={...body,version:opaque(),key:'media/'+opaque(),created_at:now,expires_at:now+MEDIA_TTL,cleanup_at:now+RAW_RETENTION,status:multi?'initializing':'pending',parts:{},part_count:multi?Math.ceil(body.size/body.part_size):1};
      this.put(row);return {status:201,row};
    });if(claimed.status===201)await this.schedule(claimed.row);return claimed;});
    if(result.status!==201)return result;
    const row=result.row;
    await this.enroll(row);
    if(!this.current(this.row())||this.row().version!==row.version)return fail(410,'media_expired');
    if(multi)return this.writing(row,async write=>{
      const upload=await write(()=>this.env.MEDIA_BUCKET.createMultipartUpload(row.key,{httpMetadata:{contentType:row.mime}}));
      const active=await this.active(row.account);
      const accepted=this.atomic(()=>{const current=this.row();if(!active||!this.current(current)||current.version!==row.version||current.status!=='initializing')return false;this.put({...current,status:'pending',upload_id:upload.uploadId});return true;});
      // Retain a returned upload ID even if expiry preceded the response.
      if(!accepted){this.atomic(()=>{const current=this.row();if(current?.version===row.version)this.put({...current,upload_id:upload.uploadId});});await upload.abort();this.atomic(()=>{const current=this.row();if(current?.version===row.version)this.put({...current,multipart_closed:'aborted'});});return fail(410,'media_expired');}
      return {status:201,body:{status:'pending',expires_at:row.expires_at,size:row.size,part_size:row.part_size}};
    });
    return {status:201,body:{status:'pending',expires_at:row.expires_at,size:row.size,part_size:row.part_size}};
  }
  async streamPut(row,request,part,write=fn=>fn()){
    const size=part===null?row.size:Math.min(row.part_size,row.size-(part-1)*row.part_size);
    if(request.headers.get('content-length')!==String(size)||!request.body)throw new Error('media_size_mismatch');
    const fixed=new FixedLengthStream(size),abort=new AbortController();
    const copying=request.body.pipeTo(fixed.writable,{signal:abort.signal});
    const storing=write(()=>part===null?this.env.MEDIA_BUCKET.put(row.key,fixed.readable,{httpMetadata:{contentType:row.mime},onlyIf:{etagDoesNotMatch:'*'}}):this.env.MEDIA_BUCKET.resumeMultipartUpload(row.key,row.upload_id).uploadPart(part,fixed.readable));
    try{const [,result]=await Promise.all([copying,storing]);if(!result)throw new Error('media_write_failed');return result;}
    catch(error){abort.abort();await Promise.allSettled([copying,storing]);throw error;}
  }
  async upload(request,part){
    const first=this.row();if(!this.current(first))return reply(404,{error:'not_found'});
    if(!first.cleanup_registered)return reply(503,{error:'cleanup_registration_unconfirmed'});
    if(!await this.active(first.account))return reply(410,{error:'account_revoked'});
    const claim=this.atomic(()=>{
      const row=this.row();if(!this.current(row)||row.version!==first.version)return null;
      if(row.io_ticket)return {unconfirmed:true};
      if(row.status!=='pending'||(row.upload_id?(!Number.isSafeInteger(part)||part<1||part>row.part_count):part!==null))return null;
      const ticket=opaque();this.put({...row,status:'uploading',ticket});return {...row,ticket};
    });
    if(claim?.unconfirmed)return reply(409,{error:'media_upload_unconfirmed_pending'});
    if(!claim)return reply(409,{error:'media_not_ready'});
    return this.writing(claim,async write=>{try{
      const result=await this.streamPut(claim,request,part,write),active=await this.active(claim.account);
      const accepted=this.atomic(()=>{const row=this.row();if(!this.current(row)||row.version!==claim.version||row.ticket!==claim.ticket||!active)return false;
        const parts={...row.parts};if(part!==null)parts[part]={partNumber:part,etag:result.etag};
        this.put({...row,parts,status:part===null?'uploaded':'pending',ticket:null});return true;});
      if(!accepted){await this.env.MEDIA_BUCKET.delete(claim.key);return reply(410,{error:'media_expired'});}
      return reply(200,{status:part===null?'uploaded':'part_uploaded'});
    }catch{
      // Only a settled write permits retry. Unknown I/O retains its marker and
      // the next claim returns409 without changing this row or accepting bytes.
      this.atomic(()=>{const row=this.row();if(row?.ticket===claim.ticket)this.put({...row,status:part===null?'failed':'pending',ticket:null});});
      return reply(503,{error:'media_upload_unconfirmed'});
    }});
  }
  async complete(claim){
    return this.writing(claim,async write=>{try{
      const object=await write(()=>this.env.MEDIA_BUCKET.resumeMultipartUpload(claim.key,claim.upload_id).complete(Object.values(claim.parts).sort((a,b)=>a.partNumber-b.partNumber)));
      this.atomic(()=>{const current=this.row();if(current?.version===claim.version)this.put({...current,multipart_closed:'completed'});});
      const active=await this.active(claim.account);
      const accepted=this.atomic(()=>{const row=this.row();if(!this.current(row)||row.ticket!==claim.ticket||row.version!==claim.version||!active||object.size!==row.size)return false;this.put({...row,status:'ready',ticket:null,multipart_closed:'completed'});return true;});
      return accepted?{status:200,body:{status:'ready',sha256:claim.sha256}}:fail(410,'media_expired');
    }catch{return fail(503,'media_completion_unconfirmed');}});
  }
  async read(row){
    const object=await this.env.MEDIA_BUCKET.get(row.key);
    const active=await this.active(row.account),current=this.row();
    if(!active||!object||!this.current(current)||current.version!==row.version||current.status!=='ready'){await object?.body?.cancel();return reply(410,{error:'media_expired'});}
    return new Response(object.body,{headers:{'content-type':'application/octet-stream','content-length':String(object.size),'cache-control':'no-store','x-content-type-options':'nosniff'}});
  }
  async previewSource(body){
    const row=this.row();if(!this.bound(row,body)||!this.current(row)||row.status!=='ready'||row.kind!=='sanitized')return null;
    return {key:row.key,version:row.version,mime:row.mime,size:row.size,expires_at:row.expires_at};
  }
  async grant(purpose,body,ticket){
    if(!keys(body,['actor','account','sha256','media_id','source','expires_at'])||!validHash(body.media_id)||!validOpaque(body.source)||!validHash(body.sha256)||typeof body.actor!=='string'||!MEDIA_NAME.test(body.actor)||typeof body.account!=='string'||!MEDIA_NAME.test(body.account)||!Number.isSafeInteger(body.expires_at))return fail();
    // Provider TTL is issued exclusively from this Worker's clock. The legacy
    // integer field cannot extend it or reject issuance due to a VM clock skew.
    if(purpose==='preview'&&(body.expires_at<=this.now()||body.expires_at>this.now()+MEDIA_TTL))return fail();
    if(!await this.active(body.account))return fail(410,'account_revoked');
    const source=await mediaStub(this.env,body.source),original=await source.previewSource(body);if(!original)return fail(404,'not_found');
    const expires_at=purpose==='preview'?Math.min(body.expires_at,original.expires_at):this.now()+(original.mime.startsWith('video/')?1_800_000:MEDIA_TTL);
    const claim=await this.ctx.storage.transaction(async()=>{const claimed=this.atomic(()=>{if(this.row())return null;if(!this.replay(ticket))return null;
      const row={actor:body.actor,account:body.account,sha256:body.sha256,media_id:body.media_id,kind:purpose,mime:original.mime,size:original.size,version:opaque(),key:'media/'+opaque(),status:'copying',expires_at,cleanup_at:purpose==='provider'?this.now()+RAW_RETENTION:expires_at};this.put(row);return row;});if(claimed)await this.schedule(claimed);return claimed;});
    if(!claim)return fail(409,'media_exists');
    await this.enroll(claim);
    if(!this.current(this.row())||this.row().version!==claim.version)return fail(410,'media_expired');
    return this.writing(claim,async write=>{
    const object=await this.env.MEDIA_BUCKET.get(original.key);if(!object)return fail(404,'not_found');
    await write(()=>this.env.MEDIA_BUCKET.put(claim.key,object.body,{httpMetadata:{contentType:claim.mime}}));
    const latest=await source.previewSource(body),active=await this.active(body.account);
    const accepted=this.atomic(()=>{const row=this.row();if(!this.current(row)||row.version!==claim.version||row.status!=='copying'||!latest||latest.version!==original.version||!active)return false;this.put({...row,status:'ready'});return true;});
    if(!accepted){await this.env.MEDIA_BUCKET.delete(claim.key);return fail(410,'media_expired');}
    return {status:201,body:{status:'ready',expires_at,generation:claim.version}};
    });
  }
  async publicResult(op,body,ticket){
    if(!keys(body,['actor','account','sha256','media_id','purpose','generation'])||body.purpose!=='provider')return fail();
    const before=this.row();if(!this.bound(before,body)||before.kind!=='provider'||before.media_id!==body.media_id||before.version!==body.generation)return fail(404,'not_found');
    if(!await this.active(before.account))return fail(410,'account_revoked');
    const result=await this.ctx.storage.transaction(async()=>{const changed=this.atomic(()=>{
      const row=this.row();if(!this.bound(row,body)||row.version!==body.generation||!this.current(row)||row.status!=='ready')return fail(410,'media_expired');
      if(!this.replay(ticket))return fail(409,'replayed_request');
      if(op==='published'){
        if(row.acked_at!==undefined)return fail(409,'media_already_acknowledged');
        const now=this.now();this.put({...row,acked_at:now,expires_at:now+3_600_000});
        return {status:200,body:{status:'acknowledged',expires_at:now+3_600_000}};
      }
      this.put({...row,status:'retired'});return {status:200,body:{status:'retired'}};
    });if(changed.status===200)await this.schedule(this.row());return changed;});
    return result;
  }
  async view(request){
    const row=this.row();if(!row)return reply(410,{error:'media_expired'});
    if(!this.current(row))return reply(410,{error:'media_expired'});
    if(!['preview','provider'].includes(row.kind)||row.status!=='ready')return reply(410,{error:'media_expired'});
    if(!await this.active(row.account))return reply(410,{error:'account_revoked'});
    const range=request.headers.get('range');
    const invalidRange=()=>{const response=reply(416,{error:'invalid_range'});response.headers.set('content-range',`bytes */${row.size}`);return response;};
    if(range&&!/^bytes=\d+-\d*$/.test(range))return invalidRange();
    if(range){
      const [start,end]=range.slice(6).split('-');
      if(!Number.isSafeInteger(Number(start))||Number(start)>=row.size||
         (end!==''&&(!Number.isSafeInteger(Number(end))||Number(end)<Number(start))))return invalidRange();
    }
    const object=await this.env.MEDIA_BUCKET.get(row.key,range?{range:request.headers}:{});
    const active=await this.active(row.account),current=this.row();if(!active||!object||!this.current(current)||current.version!==row.version||current.status!=='ready'){await object?.body?.cancel();return reply(410,{error:'media_expired'});}
    const headers={'content-type':row.mime,'cache-control':'no-store','x-content-type-options':'nosniff','referrer-policy':'no-referrer','content-security-policy':"default-src 'none'; sandbox"};
    if(range&&object.range){headers['content-range']=`bytes ${object.range.offset}-${object.range.offset+object.range.length-1}/${object.size}`;headers['content-length']=String(object.range.length);}
    else headers['content-length']=String(object.size);
    if(request.method==='HEAD')await object.body.cancel();
    return new Response(request.method==='HEAD'?null:object.body,{status:range&&object.range?206:200,headers});
  }
  async alarm(){
    let row=this.row();if(!row)return;
    if(this.now()>=row.expires_at&&row.status!=='retired')this.atomic(()=>this.put({...this.row(),status:'retired'}));
    if(this.now()<row.cleanup_at){await this.ctx.storage.setAlarm(row.cleanup_at);return;}
    // Persist each attempt before downstream work. Ten includes the first.
    if((row.cleanup_attempts||0)>=10){await this.ctx.storage.deleteAlarm();return;}
    const attempts=(row.cleanup_attempts||0)+1;
    await this.ctx.storage.transaction(async()=>{
      this.atomic(()=>this.put({...this.row(),status:'retired',cleanup_attempts:attempts}));
      if(attempts<10)await this.ctx.storage.setAlarm(this.now()+60_000);else await this.ctx.storage.deleteAlarm();
    });
    try{
      if(!row.cleanup_registered)await this.enroll(row);
      row=this.row();
      if(row.io_ticket)throw Error('cleanup_io_unconfirmed');
      if(row.upload_id&&!row.multipart_closed){
        await this.env.MEDIA_BUCKET.resumeMultipartUpload(row.key,row.upload_id).abort();
        this.atomic(()=>this.put({...this.row(),multipart_closed:'aborted'}));
      }
      await this.env.MEDIA_BUCKET.delete(row.key);
      const removed=await(await this.cleanupAuthority(row)).cleanupRemove(this.ctx.id.toString());
      if(removed.status!==200)throw Error('cleanup_remove_unconfirmed');
    }catch{
      if(attempts>=10){
        this.atomic(()=>this.put({...this.row(),cleanup_failed:true}));
        try{await(await this.cleanupAuthority(row)).cleanupFailed(this.ctx.id.toString());}catch{}
      }
      return;
    }
    await this.ctx.storage.deleteAll();await this.ctx.storage.deleteAlarm();
    // No permanent public marker or cleanup subject survives confirmed deletion.
  }
}
