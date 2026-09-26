import worker from '../src/worker.js';
import {InviteObject} from '../src/invite-object.js';
import {ApprovalPerson} from '../src/approval-object.js';
import {inviteStub,personStub} from '../src/approval.js';
export {ApprovalAccount} from '../src/approval-object.js';
export {AuthRelay} from '../src/relay-object.js';
export {MediaObject} from '../src/media-object.js';
// 招待の object に時計だけを足す（approval-harness と同じ作法）。署名の nonce 窓は実時間のまま。
export class TestInvite extends InviteObject {
  replay(ticket){const clock=this.clock;this.clock=undefined;try{return super.replay(ticket);}finally{this.clock=clock;}}
  now(){return this.clock??Date.now();}
  async configure(body){this.clock=body.clock;if(body.alarm)await this.alarm();}
  inspect(){return [...this.ctx.storage.kv.list()];}
}
export class TestPerson extends ApprovalPerson {
  configure(){}
  inspect(){return [...this.ctx.storage.kv.list()];}
}
export default {async fetch(request,env){
  const match=/^\/__invite\/(invite|person)\/(.+)$/.exec(new URL(request.url).pathname);
  if(match){const stub=match[1]==='invite'?inviteStub(env,match[2]):await personStub(env,match[2]);
    if(request.method==='POST')await stub.configure(await request.json());return Response.json(await stub.inspect());}
  return worker.fetch(request,env);
}};
