import {DurableObject} from "cloudflare:workers";
import {HASH_PATTERN, MAX_CODE_BYTES} from "./relay.js";

const STATE_MS = 600_000;
const CODE_MS = 300_000;
const missing = () => ({status: 404, body: {error: "not_found"}});

// SQLite-backed synchronous KV, not Workers KV. One object per hashed state.
// Constructors do not create a schema/record: unknown GETs never persist data.
export class AuthRelay extends DurableObject {
  now() { return Date.now(); }
  put(row) { this.ctx.storage.kv.put("session", row); }
  transaction(action) {
    try { return this.ctx.storage.transactionSync(action); }
    catch { return {status: 503, body: {error: "relay_unavailable"}}; }
  }
  expired(row, now) {
    return now >= row.expires_at || (row.status === "ready" && now >= row.code_expires_at);
  }
  expire(row) {
    // A small marker prevents reinjection until the original state deadline.
    // After that deadline there is no permanent state-reuse blacklist.
    if (this.now() >= row.expires_at) this.ctx.storage.kv.delete("session");
    else this.put({schema_version: 1, status: "expired", expires_at: row.expires_at});
  }
  scheduleAlarm(deadline) { return this.ctx.storage.setAlarm(deadline); }
  async register(readHash) {
    if (!HASH_PATTERN.test(readHash)) return {status: 400, body: {error: "invalid_request"}};
    const now = this.now();
    try { return await this.ctx.storage.transaction(async () => {
      const result = this.transaction(() => {
        if (this.ctx.storage.kv.get("session")) return {status: 409, body: {error: "state_exists"}};
        this.put({schema_version: 1, status: "pending", read_key_hash: readHash,
          created_at: now, expires_at: now + STATE_MS, polls: 0});
        return {status: 201, body: {status: "pending"}};
      });
      if (result.status === 201) await this.scheduleAlarm(now + STATE_MS);
      return result;
    }); } catch { return {status: 503, body: {error: "relay_unavailable"}}; }
  }
  async receive(code) {
    if (typeof code !== "string" || !code || /[\x00-\x1f\x7f]/.test(code) ||
        new TextEncoder().encode(code).length > MAX_CODE_BYTES) return {status: 400};
    const now = this.now();
    let expiry;
    try { return await this.ctx.storage.transaction(async () => {
      const result = this.transaction(() => {
        const row = this.ctx.storage.kv.get("session");
        if (!row) return missing();
        if (row.status === "expired" || this.expired(row, now)) { this.expire(row); return missing(); }
        if (row.status !== "pending") return {status: 409};
        expiry = Math.min(row.expires_at, now + CODE_MS);
        this.put({...row, status: "ready", code, received_at: now, code_expires_at: expiry});
        return {status: 200};
      });
      if (result.status === 200) await this.scheduleAlarm(expiry);
      return result;
    }); } catch { return {status: 503}; }
  }
  consume(readHash) {
    return this.transaction(() => {
      const row = this.ctx.storage.kv.get("session"), now = this.now();
      if (!row) return missing();
      if (row.status === "expired" || this.expired(row, now)) { this.expire(row); return missing(); }
      if (!HASH_PATTERN.test(readHash || "") || !crypto.subtle.timingSafeEqual(
          new TextEncoder().encode(row.read_key_hash), new TextEncoder().encode(readHash))) {
        return {status: 401, body: {error: "unauthorized"}};
      }
      if (row.status === "consumed") return missing();
      const polls = row.polls + 1;
      if (polls > 400) { this.expire(row); return missing(); }
      if (row.status === "pending") { this.put({...row, polls}); return missing(); }
      const body = {code: row.code, received_at: new Date(row.received_at).toISOString()};
      const {code, code_expires_at, received_at, ...rest} = row;
      this.put({...rest, polls, status: "consumed"});
      return {status: 200, body};
    });
  }
  async alarm() {
    let deadline;
    const result = this.transaction(() => {
      const row = this.ctx.storage.kv.get("session");
      if (row && this.expired(row, this.now())) this.expire(row);
      if (row && this.now() < row.expires_at) deadline = row.expires_at;
      return {status: 200};
    });
    if (result.status !== 200) throw new Error("relay_cleanup_unavailable");
    if (deadline) await this.ctx.storage.setAlarm(deadline);
    else await this.ctx.storage.deleteAll();
  }
}
