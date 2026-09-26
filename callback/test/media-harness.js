import worker from '../src/worker.js';
import {MediaObject} from '../src/media-object.js';
import {mediaStub} from '../src/media.js';
import {Account as BaseAccount,Person} from '../src/person-object.js';
import {accountStub} from '../src/person.js';
export class TestMedia extends MediaObject {
  constructor(ctx,env){
    let target;
    const bucket=new Proxy(env.MEDIA_BUCKET,{get(object,key){
      const value=object[key];if(typeof value!=='function')return value;
      if(key==='resumeMultipartUpload')return (...args)=>{
        const upload=value.apply(object,args);
        return new Proxy(upload,{get(u,method){const f=u[method];if(typeof f!=='function')return f;return async(...values)=>{
          await target?.beforeOperation(method);
          if(target?.failR2===method){target.failR2=null;throw Error('synthetic_r2_before_failure');}
          const result=await f.apply(u,values);
          if(target.afterR2?.operation===method&&target.afterR2.action==='fail'){target.afterR2=null;throw Error('synthetic_r2_response_loss');}
          return result;
        };}});
      };
      return async(...args)=>{
        await target?.beforeOperation(key);
        if(target?.lateR2===key){
          target.lateR2=null;const [name,body,metadata]=args;
          const bytes=await new Response(body).arrayBuffer();
          target.lateWrite=()=>value.call(object,name,bytes,metadata);
          throw Error('synthetic_response_lost_before_remote_completion');
        }
        if(target?.failR2===key){target.failR2=null;throw Error('synthetic_r2_before_failure');}
        const result=await value.apply(object,args);
        if(target?.afterR2?.operation===key){
          const action=target.afterR2.action;target.afterR2=null;
          if(action==='fail')throw Error('synthetic_r2_response_loss');
          if(action==='expire')target.clock=target.row().expires_at;
          if(action==='rotate')target.atomic(()=>target.put({...target.row(),version:'changed'}));
          if(action==='revoke')await(await accountStub(env,target.row().account)).manage('revoke',{}, {nonce:'b'.repeat(43),time:Date.now()});
        }
        if(key==='createMultipartUpload')return new Proxy(result,{get(upload,method){
          const fn=upload[method];if(typeof fn!=='function')return fn;
          return async(...args)=>{if(target.failR2===method){target.failR2=null;throw Error('synthetic_r2_before_failure');}return fn.apply(upload,args);};
        }});
        return result;
      };
    }});
    super(ctx,{...env,MEDIA_BUCKET:bucket});target=this;
  }
  async cleanupRetry(account){this.retryCalls=(this.retryCalls||0)+1;return super.cleanupRetry(account);}
  async beforeOperation(operation){
    if(this.beforeR2?.operation!==operation)return;
    const action=this.beforeR2;this.beforeR2=null;this.clock=this.row().cleanup_at;
    await this.alarm();this.duringIO={row:this.row(),alarm:await this.ctx.storage.getAlarm()};
    this.failR2=action.compensationFailure;
  }
  async cleanupAuthority(row){
    const stub=await super.cleanupAuthority(row),target=this,result={};
    for(const key of ['cleanupRegister','cleanupRemove','cleanupFailed'])result[key]=async(...args)=>{
      if(target.cleanupFault===key){target.cleanupFault=null;throw Error('synthetic_cleanup_rpc_failure');}
      const value=await stub[key](...args);
      if(target.cleanupLoss===key){target.cleanupLoss=null;throw Error('synthetic_cleanup_rpc_response_loss');}
      return value;
    };
    return result;
  }
  async schedule(row){if(this.failAlarm){this.failAlarm=false;throw Error('synthetic_alarm_fault');}return super.schedule(row);}
  atomic(fn){return super.atomic(()=>{const value=fn();if(this.failSave){this.failSave=false;throw Error('synthetic_storage_fault');}return value;});}
  now(){return this.clock??Date.now();}
  async streamPut(...args){
    const result=await super.streamPut(...args);
    if(this.afterPut==='expire')this.clock=this.row().expires_at;
    if(this.afterPut==='rotate')this.atomic(()=>this.put({...this.row(),version:'changed'}));
    if(this.afterPut==='revoke')await(await accountStub(this.env,this.row().account)).manage('revoke',{}, {nonce:'a'.repeat(43),time:Date.now()});
    return result;
  }
  async control(data){this.clock=data.clock;this.afterPut=data.afterPut;this.afterR2=data.afterR2;this.beforeR2=data.beforeR2;this.lateR2=data.lateR2;this.failSave=data.failSave;this.failAlarm=data.failAlarm;this.failR2=data.failR2;this.cleanupFault=data.cleanupFault;this.cleanupLoss=data.cleanupLoss;if(data.finishLateR2&&this.lateWrite){await this.lateWrite();this.lateWrite=null;}if(data.unresolvedIO)this.atomic(()=>this.put({...this.row(),io_ticket:'synthetic-unresolved'}));if(data.alarm)await this.alarm();if(data.retryInspect)return {calls:this.retryCalls||0,row:this.row()||null,subject:this.ctx.id.toString()};if(data.ioInspect)return this.duringIO;return data.inspect?{rows:[...this.ctx.storage.kv.list()],alarm:await this.ctx.storage.getAlarm()}:[...this.ctx.storage.kv.list()];}
}
export class Account extends BaseAccount {
  replay(ticket){const clock=this.clock;this.clock=undefined;try{return super.replay(ticket);}finally{this.clock=clock;}}
  now(){return this.clock??Date.now();}
  async control(data){
    this.clock=data.clock;
    if(data.failed)await this.cleanupFailed(data.failed);
    const result={rows:[...this.ctx.storage.kv.list({prefix:'media_cleanup:'})],cleanup:this.cleanupStatus()};
    if(data.cursor)result.cursor=this.ctx.storage.kv.get('media_cleanup_cursor')||null;return result;
  }
}
export {Person};
export default {async fetch(request,env){
  const url=new URL(request.url);if(url.pathname==='/__media-egress-canary')return fetch('https://egress-canary.invalid/blocked');
  const account=/^\/__cleanup\/([A-Za-z0-9_.-]+)$/.exec(url.pathname);
  if(account)return Response.json(await(await accountStub(env,account[1])).control(await request.json()));
  const match=/^\/__media\/([A-Za-z0-9_-]{43})$/.exec(url.pathname);
  if(match)return Response.json(await(await mediaStub(env,match[1])).control(await request.json()));
  return worker.fetch(request,env);
}};
