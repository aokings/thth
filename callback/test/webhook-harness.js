// Test-only: the real WebhookInbox with a clock, a capacity knob and a manual alarm (Meta 計画 段 6).
import worker from '../src/worker.js';
import {WebhookInbox} from '../src/webhook-object.js';
import {webhookStub} from '../src/webhook.js';
export {Person,Account} from '../src/person-object.js';
export {AuthRelay} from '../src/relay-object.js';
export class TestWebhook extends WebhookInbox {
  now(){return this.clock??Date.now();}
  capacity(){return this.limit??super.capacity();}
  async configure(body){this.clock=body.clock;this.limit=body.limit;if(body.alarm)await this.alarm();}
  inspect(){return [...this.ctx.storage.kv.list()].filter(([key])=>key!=='nonces');}
}
export default {async fetch(request,env){
  if(new URL(request.url).pathname==='/__webhook'){const stub=webhookStub(env);
    if(request.method==='POST')await stub.configure(await request.json());return Response.json(await stub.inspect());}
  return worker.fetch(request,env);
}};
