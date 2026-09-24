import worker from '../src/worker.js';
import {ApprovalPerson,ApprovalSession,ApprovalAccount} from '../src/approval-object.js';
import {personStub,sessionStub,accountStub,opaque} from '../src/approval.js';
import {MediaObject} from '../src/media-object.js';
import {mediaStub} from '../src/media.js';
import {digest} from '../src/relay.js';
export {AuthRelay} from '../src/relay-object.js';
// The approval page now depends on live preview capabilities, so this harness
// runs the real media object and only adds a clock, exactly like media-harness.
export class TestMedia extends MediaObject {
  now(){return this.clock??Date.now();}
  configure(body){this.clock=body.clock;}
  inspect(){return [...this.ctx.storage.kv.list()];}
}
export class TestPerson extends ApprovalPerson {
  now(){return this.clock??Date.now();}
  async configure(body){this.clock=body.clock;this.fault=body.fault;this.revokeAfterCheck=body.revokeAfterCheck;this.revokeAccountAfterConsume=body.revokeAccountAfterConsume;
    // 3.11.0: put a foreign session into this person's list index, as a broken index would,
    // so the session-side check (whose approval is it?) is observed on its own.
    if(body.inject){const id=this.env.APPROVAL_SESSION.idFromName(await digest(body.inject.token)).toString(),row=this.ctx.storage.kv.get('person');
      this.ctx.storage.transactionSync(()=>this.ctx.storage.kv.put('listed:'+body.inject.job_id,{session:id,expires_at:body.inject.expires_at,generation:row.generation}));}}
  put(key,value){super.put(key,value);if(this.fault)throw new Error('synthetic_storage_failure');}
  async check(...args){const value=await super.check(...args);
    if(this.revokeAfterCheck)this.ctx.storage.transactionSync(()=>this.put('person',{active:false,generation:'revoked',failures:0}));return value;}
  async consume(...args){const result=super.consume(...args);
    if(result.status===200&&this.revokeAccountAfterConsume)await(await accountStub(this.env,this.revokeAccountAfterConsume)).manage('revoke',{}, {nonce:opaque(),time:Date.now()});
    return result;
  }
  inspect(){return [...this.ctx.storage.kv.list()];}
}
export class TestSession extends ApprovalSession {
  // A moved clock must not invalidate a freshly signed request's nonce window:
  // replay keeps real time, everything else sees the test clock.
  replay(ticket){const clock=this.clock;this.clock=undefined;try{return super.replay(ticket);}finally{this.clock=clock;}}
  now(){return this.clock??Date.now();}
  configure(body){this.clock=body.clock;this.fault=body.fault;}
  put(key,value){super.put(key,value);if(this.fault)throw new Error('synthetic_storage_failure');}
  inspect(){return [...this.ctx.storage.kv.list()];}
}
export default {async fetch(request,env){
  const match=/^\/__approval\/(person|session|account|deletion|media)\/(.+)$/.exec(new URL(request.url).pathname);
  if(match){const stub=await(match[1]==='deletion'?env.DELETION_INBOX.getByName('pending-receipts-v1'):match[1]==='account'?accountStub(env,match[2]):match[1]==='media'?mediaStub(env,match[2]):match[1]==='person'?personStub(env,match[2]):sessionStub(env,match[2]));
    if(request.method==='POST')await stub.configure(await request.json());return Response.json(await stub.inspect());}
  return worker.fetch(request,env);
}};

export class TestAccount extends ApprovalAccount {
  configure(body){this.fault=body.fault;}
  put(key,value){super.put(key,value);if(this.fault)throw new Error('synthetic_storage_failure');}
  inspect(){return [...this.ctx.storage.kv.list()];}
}

import {DeletionInbox} from '../src/deletion-object.js';
export class TestDeletion extends DeletionInbox {
  now(){return this.clock??Date.now();}
  async configure(body){this.clock=body.clock;if(body.alarm)await this.alarm();}
  inspect(){return [...this.ctx.storage.kv.list()];}
}
