import worker from '../src/worker.js';
import {ApprovalPerson,ApprovalAccount} from '../src/approval-object.js';
import {personStub,accountStub} from '../src/approval.js';
import {MediaObject} from '../src/media-object.js';
import {mediaStub} from '../src/media.js';
export {AuthRelay} from '../src/relay-object.js';
// The real media object with a clock, exactly like media-harness (3.13.0: no approval page).
export class TestMedia extends MediaObject {
  now(){return this.clock??Date.now();}
  configure(body){this.clock=body.clock;}
  inspect(){return [...this.ctx.storage.kv.list()];}
}
export class TestPerson extends ApprovalPerson {
  now(){return this.clock??Date.now();}
  async configure(body){this.clock=body.clock;this.fault=body.fault;}
  put(key,value){super.put(key,value);if(this.fault)throw new Error('synthetic_storage_failure');}
  inspect(){return [...this.ctx.storage.kv.list()];}
}
export default {async fetch(request,env){
  const match=/^\/__approval\/(person|account|deletion|media)\/(.+)$/.exec(new URL(request.url).pathname);
  if(match){const stub=await(match[1]==='deletion'?env.DELETION_INBOX.getByName('pending-receipts-v1'):match[1]==='account'?accountStub(env,match[2]):match[1]==='media'?mediaStub(env,match[2]):personStub(env,match[2]));
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
