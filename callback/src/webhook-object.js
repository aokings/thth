// Webhook の通知の置き場（設計 MetaAPI 計画 段 6）。1 つだけ（`webhookStub`・名前 meta-webhook-v1）。
//
// - `receive`（Worker の `/webhook/meta` から・署名を確かめたあと）: 変化を `row:<受けた時刻 13 桁>:<key>` に積む。
//   既に積んだ・VM が受け取り済み（`seen:<key>`・7 日）の key は重複として数えるだけ。積んだ行が
//   `PENDING_MAX` を越えるなら 503（Meta が送り直す）と数え（`overflow`）、何も積まない。
// - `manage`（VM の署名つき relay から）: `pull`（古い順に 1 頁）・`ack`（受け取った key を消して `seen` に移す）。
//   nonce の使い回しは断る（`DeletionInbox` と同じ型）。
// - `alarm`: VM が 7 日取りに来なかった行を消して `expired` に数える（過去の分は Meta から取り直せない・
//   VM はこの数を events に出す）。`seen` の期限切れも消す。
import {DurableObject} from 'cloudflare:workers';
import {fields, fail} from './person.js';
import {STATE_PATTERN} from './relay.js';
export const RETENTION = 7 * 24 * 60 * 60 * 1000;
export const SEEN_TTL = 7 * 24 * 60 * 60 * 1000;
export const PENDING_MAX = 20_000;
export const PAGE_ROWS = 100;
export const PAGE_BYTES = 786_432;
export const ACK_MAX = 200;
export const ALARM_EVERY = 60 * 60 * 1000;
const KEY = /^[0-9a-f]{64}$/;
const CURSOR = /^[0-9]{13}:[0-9a-f]{64}$/;
const EMPTY = {received: 0, duplicates: 0, dropped: 0, expired: 0, overflow: 0, pending: 0,
  first_received_at: null, last_received_at: null, last_delivery_at: null};
const pad = n => String(n).padStart(13, '0');

export class WebhookInbox extends DurableObject {
  now() { return Date.now(); }
  atomic(fn) { try { return this.ctx.storage.transactionSync(fn); } catch { return fail(503, 'webhook_unavailable'); } }
  capacity() { return PENDING_MAX; }
  stats() { return {...EMPTY, ...(this.ctx.storage.kv.get('stats') || {})}; }
  put(key, value) { this.ctx.storage.kv.put(key, value); }
  seen(key, now) { const until = this.ctx.storage.kv.get('seen:' + key); return typeof until === 'number' && until > now; }
  async receive(rows, dropped) {
    if (!Array.isArray(rows) || !Number.isSafeInteger(dropped) || dropped < 0) return fail();
    const now = this.now();
    const result = this.atomic(() => {
      const stats = this.stats(), batch = new Set(), fresh = [];
      for (const row of rows) {
        if (!row || !KEY.test(row.key)) return fail();
        if (batch.has(row.key) || this.ctx.storage.kv.get('idx:' + row.key) !== undefined || this.seen(row.key, now)) { stats.duplicates++; continue; }
        batch.add(row.key); fresh.push(row);
      }
      stats.last_delivery_at = now;
      if (stats.pending + fresh.length > this.capacity()) {
        stats.overflow += fresh.length; this.put('stats', stats);
        return {status: 503, body: {error: 'webhook_capacity'}};
      }
      for (const row of fresh) {
        const name = 'row:' + pad(now) + ':' + row.key;
        this.put(name, {key: row.key, object: row.object, entry_id: row.entry_id, time: row.time, field: row.field,
          value: row.value, oversize: row.oversize === true, received_at: now});
        this.put('idx:' + row.key, name);
      }
      stats.received += fresh.length; stats.dropped += dropped; stats.pending += fresh.length;
      if (fresh.length) { stats.last_received_at = now; stats.first_received_at ??= now; }
      this.put('stats', stats);
      return {status: 200, body: {ok: true}};
    });
    if (result.status !== 400) { const alarm = await this.ctx.storage.getAlarm(); if (alarm === null) await this.ctx.storage.setAlarm(now + ALARM_EVERY); }
    return result;
  }
  manage(operation, body, ticket, subject) {
    return this.atomic(() => {
      const now = this.now();
      if (!ticket || !STATE_PATTERN.test(ticket.nonce) || !Number.isSafeInteger(ticket.time) || Math.abs(now - ticket.time) > 60000) return fail(401, 'unauthorized');
      const nonces = this.ctx.storage.kv.get('nonces') || {};
      for (const [key, time] of Object.entries(nonces)) if (time < now) delete nonces[key];
      if (nonces[ticket.nonce] || Object.keys(nonces).length >= 1024) return fail(409, 'replayed_request');
      nonces[ticket.nonce] = ticket.time + 60001; this.put('nonces', nonces);
      if (subject !== 'meta') return fail();
      if (operation === 'pull') {
        if (!fields(body, ['after']) || (body.after !== null && (typeof body.after !== 'string' || !CURSOR.test(body.after)))) return fail();
        const start = body.after === null ? undefined : 'row:' + body.after;
        const rows = []; let size = 0, more = false;
        for (const [name, row] of this.ctx.storage.kv.list({prefix: 'row:', ...(start ? {start} : {})})) {
          if (start && name <= start) continue;
          const length = JSON.stringify(row).length;
          if (rows.length >= PAGE_ROWS || (rows.length && size + length > PAGE_BYTES)) { more = true; break; }
          rows.push(row); size += length;
        }
        const last = rows.at(-1);
        return {status: 200, body: {rows, next: more && last ? pad(last.received_at) + ':' + last.key : null, stats: this.stats(), now}};
      }
      if (operation === 'ack') {
        if (!fields(body, ['keys']) || !Array.isArray(body.keys) || !body.keys.length || body.keys.length > ACK_MAX ||
            new Set(body.keys).size !== body.keys.length || !body.keys.every(key => typeof key === 'string' && KEY.test(key))) return fail();
        const stats = this.stats(); let acked = 0;
        for (const key of body.keys) {
          const name = this.ctx.storage.kv.get('idx:' + key);
          if (typeof name === 'string') {
            this.ctx.storage.kv.delete(name); this.ctx.storage.kv.delete('idx:' + key);
            stats.pending = Math.max(0, stats.pending - 1); acked++;
          }
          this.put('seen:' + key, now + SEEN_TTL);
        }
        this.put('stats', stats);
        return {status: 200, body: {acked}};
      }
      return fail();
    });
  }
  async alarm() {
    let again = false; const now = this.now();
    this.ctx.storage.transactionSync(() => {
      const stats = this.stats();
      for (const [name, row] of this.ctx.storage.kv.list({prefix: 'row:'})) {
        if (row.received_at > now - RETENTION) { again = true; break; }
        this.ctx.storage.kv.delete(name); this.ctx.storage.kv.delete('idx:' + row.key);
        this.put('seen:' + row.key, now + SEEN_TTL);
        stats.expired++; stats.pending = Math.max(0, stats.pending - 1);
      }
      for (const [name, until] of this.ctx.storage.kv.list({prefix: 'seen:'})) {
        if (until <= now) this.ctx.storage.kv.delete(name); else again = true;
      }
      this.put('stats', stats);
    });
    if (again) await this.ctx.storage.setAlarm(now + ALARM_EVERY);
  }
}
