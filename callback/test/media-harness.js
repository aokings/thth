import worker from '../src/worker.js';
import {MediaObject} from '../src/media-object.js';
import {mediaStub} from '../src/media.js';
import {ApprovalAccount,ApprovalPerson,ApprovalSession} from '../src/approval-object.js';
import {accountStub} from '../src/approval.js';
export class TestMedia extends MediaObject {
  constructor(ctx,env){
    let target;
    const bucket=new Proxy(env.MEDIA_BUCKET,{get(object,key){
      const value=object[key];if(typeof value!=='function')return value;
      if(key==='resumeMultipartUpload')return (...args)=>{
        const upload=value.apply(object,args);
        return new Proxy(upload,{get(u,method){const f=u[method];if(typeof f!=='function')return f;return async(...values)=>{
          if(target?.failR2===method){target.failR2=null;throw Error('synthetic_r2_before_failure');}
          return f.apply(u,values);
        };}});
      };
      return async(...args)=>{
        if(target?.failR2===key){target.failR2=null;throw Error('synthetic_r2_before_failure');}
        const result=await value.apply(object,args);
        if(target?.afterR2?.operation===key){
          const action=target.afterR2.action;target.afterR2=null;
          if(action==='fail')throw Error('synthetic_r2_response_loss');
          if(action==='expire')target.clock=target.row().expires_at;
          if(action==='rotate')target.atomic(()=>target.put({...target.row(),version:'changed'}));
          if(action==='revoke')await(await accountStub(env,target.row().account)).manage('revoke',{}, {nonce:'b'.repeat(43),time:Date.now()});
        }
        return result;
      };
    }});
    super(ctx,{...env,MEDIA_BUCKET:bucket});target=this;
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
  async control(data){this.clock=data.clock;this.afterPut=data.afterPut;this.afterR2=data.afterR2;this.failSave=data.failSave;this.failAlarm=data.failAlarm;this.failR2=data.failR2;if(data.alarm)await this.alarm();return data.inspect?{rows:[...this.ctx.storage.kv.list()],alarm:await this.ctx.storage.getAlarm()}:[...this.ctx.storage.kv.list()];}
}
export {ApprovalAccount,ApprovalPerson,ApprovalSession};
export default {async fetch(request,env){
  const url=new URL(request.url);if(url.pathname==='/__media-egress-canary')return fetch('https://egress-canary.invalid/blocked');
  const match=/^\/__media\/([A-Za-z0-9_-]{43})$/.exec(url.pathname);
  if(match)return Response.json(await(await mediaStub(env,match[1])).control(await request.json()));
  return worker.fetch(request,env);
}};
