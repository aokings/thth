// 段 K の試験の器: person-harness の型に、時計を差し替えられる Connector と、発行したレーンの鍵の値を試験だけに
// 見せる Person（柵 6: その値が答えにも保存にも出ないことを照らすため）を足す。
import base,{TestPerson} from './person-harness.js';
export {TestAccount,TestLogin,TestDeletion,TestMedia,AuthRelay} from './person-harness.js';
import {Connector} from '../src/connector-object.js';

const issued=new Map();   // 持ち主 → 発行した鍵の値（この isolate の中だけ・試験だけ）
export class TestPersonK extends TestPerson {
  async laneIssue(...args){
    const got=await super.laneIssue(...args);
    if(got.status===200)issued.set(this.ctx.id.toString(),got.body.bearer);
    return got;
  }
  issuedBearer(){return issued.get(this.ctx.id.toString())??null;}
}
export class TestConnector extends Connector {
  now(){return this.clock??Date.now();}
  async configure(body){this.clock=body.clock;if(body.alarm)await this.alarm();}
  inspect(){return [...this.ctx.storage.kv.list()];}
}
import {personStub} from '../src/person.js';
import {buildArgv,TOOLS} from '../src/mcp.js';
export default {async fetch(request,env){
  const url=new URL(request.url);
  if(url.pathname==='/__connector'){
    const stub=env.CONNECTOR.getByName('connectors');
    if(request.method==='POST')await stub.configure(await request.json());
    return Response.json(await stub.inspect());
  }
  if(url.pathname==='/__argv'){
    const items=await request.json();
    return Response.json(items.map(([name,args,withConfirm])=>buildArgv(TOOLS.find(t=>t.name===name),args,{withConfirm})));
  }
  const bearer=/^\/__bearer\/(.+)$/.exec(url.pathname);
  if(bearer)return Response.json({bearer:await (await personStub(env,bearer[1])).issuedBearer()});
  return base.fetch(request,env);
}};
