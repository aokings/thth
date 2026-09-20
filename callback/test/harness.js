// Test-only RPC controls, never referenced from the production entry/config.
import worker from "../src/index.js";
import {AuthRelay} from "../src/relay-object.js";
import {relayStub} from "../src/relay.js";
export class TestRelay extends AuthRelay {
  now() { return this.clock ?? Date.now(); }
  setClock(value) { this.clock = value; }
  put(row) {
    super.put(row);
    if (this.fault) throw new Error(this.fault);
  }
  async scheduleAlarm(deadline) {
    await super.scheduleAlarm(deadline);
    if (this.alarmFault) throw new Error(this.alarmFault);
  }
  setAlarmFault(value) { this.alarmFault=value; }
  setFault(value) { this.fault = value; }
  async inspect() {
    return {row: this.ctx.storage.kv.get("session") ?? null,
      alarm: await this.ctx.storage.getAlarm(), rows: Array.from(this.ctx.storage.kv.list()).length};
  }
  async cleanup() { await this.alarm(); }
}
export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (url.pathname.startsWith("/__test/")) {
      const stub = await relayStub(env, url.pathname.slice(8));
      const body = await request.json();
      if (body.clock) await stub.setClock(body.clock);
      if (Object.hasOwn(body, "fault")) await stub.setFault(body.fault);
      if (Object.hasOwn(body, "alarmFault")) await stub.setAlarmFault(body.alarmFault);
      if (body.cleanup) await stub.cleanup();
      return Response.json(await stub.inspect());
    }
    return worker.fetch(request, env);
  }
};
