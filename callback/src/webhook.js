// Meta の Webhook の受け口（設計 MetaAPI 計画 段 6・`/webhook/meta`）。
//
// - GET（購読の確認）: `hub.mode=subscribe` と `hub.verify_token` を Worker の secret
//   `META_WEBHOOK_VERIFY_TOKEN` と照合し、合えば `hub.challenge` をそのまま返す。違えば 403。
// - POST（通知）: `X-Hub-Signature-256`（`sha256=` ＋ app secret で取った本文の HMAC-SHA256）を
//   Worker の secret `META_APP_SECRET` で照合する。**本文の生のバイト列**で取り、比べるのは
//   `crypto.subtle.timingSafeEqual`（定数時間）。違えば 403 で、Durable Object には触らない。
//   合えば変化（entry × changes）を 1 行ずつにほどいて `WebhookInbox` に積み、すぐ 200 を返す。
//   重複（Meta は失敗のあと 36 時間まで送り直す・同じ通知が 2 度来る）は
//   `object|entry.id|entry.time|field|変化の id|verb` の SHA-256 で弾く。
// - 受ける欄は FB ページの `feed`・`mention` と、IG の `mentions`（試す・G-8）。IG の `comments`・
//   `live_comments` は Advanced Access が要るので受けない（数だけ `dropped` に残す）。
// - VM の `thth worker` が署名つき relay（`/relay/v/webhook/meta/{pull,ack}`）で取りに来る
//   （`person.js` の `relayRequest`）。
//
// 秘密の値はどこにも書かない・log に出さない。名前だけ: META_APP_SECRET・META_WEBHOOK_VERIFY_TOKEN。
import {digest, reply} from './relay.js';

// 1 回の通知の上限。Meta は最大 1000 件を束ねて送る（保証なし）。1 件 1〜2 KB で 2 MiB に収まる。
export const WEBHOOK_BODY_MAX = 2_097_152;
// 1 回の通知でほどく変化の上限（1000 件の束 × 数個の変化に余裕を持たせる）。
export const CHANGES_MAX = 10_000;
// 1 つの変化の中身（JSON）の上限。越えたら中身を落として `oversize: true` だけ積む（id は残る）。
export const VALUE_MAX = 32_768;
// object → 受ける欄。ここに無い欄は積まずに数える。
export const OBJECTS = Object.freeze({page: Object.freeze(['feed', 'mention']), instagram: Object.freeze(['mentions'])});
export const webhookStub = env => env.WEBHOOK_INBOX.getByName('meta-webhook-v1');
const ENTRY_ID = /^[0-9]{1,32}$/;
const encoder = new TextEncoder();
const HEADERS = {'cache-control': 'no-store', 'referrer-policy': 'no-referrer'};

function same(a, b) { return a.length === b.length && crypto.subtle.timingSafeEqual(a, b); }
async function sha256(bytes) { return new Uint8Array(await crypto.subtle.digest('SHA-256', bytes)); }

// verify_token の照合。長さの違いも漏らさないよう、両方を SHA-256 にしてから定数時間で比べる。
export async function sameSecret(given, expected) {
  if (typeof given !== 'string' || typeof expected !== 'string' || !expected) return false;
  return same(await sha256(encoder.encode(given)), await sha256(encoder.encode(expected)));
}

// `X-Hub-Signature-256: sha256=<hex>` の照合（本文の生のバイト列・定数時間）。
export async function signatureValid(raw, header, secret) {
  const match = /^sha256=([0-9a-fA-F]{64})$/.exec(header || '');
  if (!match || typeof secret !== 'string' || !secret) return false;
  const key = await crypto.subtle.importKey('raw', encoder.encode(secret), {name: 'HMAC', hash: 'SHA-256'}, false, ['sign']);
  const mac = new Uint8Array(await crypto.subtle.sign('HMAC', key, raw));
  const given = Uint8Array.from(match[1].toLowerCase().match(/../g), pair => parseInt(pair, 16));
  return same(mac, given);
}

async function rawBody(request, max) {
  const reader = request.body?.getReader();
  if (!reader) return new Uint8Array(0);
  const chunks = []; let size = 0;
  try {
    for (;;) {
      const {value, done} = await reader.read(); if (done) break;
      size += value.length;
      if (size > max) { await reader.cancel(); throw new Error('too_large'); }
      chunks.push(value);
    }
  } finally { reader.releaseLock(); }
  const all = new Uint8Array(size); let offset = 0;
  for (const part of chunks) { all.set(part, offset); offset += part.length; }
  return all;
}

function changeId(value) {
  for (const name of ['comment_id', 'post_id', 'media_id', 'id']) {
    const v = value[name];
    if ((typeof v === 'string' && v) || Number.isSafeInteger(v)) return String(v);
  }
  return null;
}

// 署名の合った通知を 1 変化 1 行にほどく。形の分からないものは積まずに数える（`dropped`）。
// 戻り値 {rows, dropped}。rows の各行: key・object・entry_id・time・field・value（JSON の文字列か null）・oversize。
export async function changes(payload) {
  const rows = []; let dropped = 0;
  if (!payload || typeof payload !== 'object' || Array.isArray(payload) || !Array.isArray(payload.entry)) return {rows, dropped: 1};
  const fields = Object.hasOwn(OBJECTS, payload.object) ? OBJECTS[payload.object] : null;
  for (const entry of payload.entry) {
    const list = entry && typeof entry === 'object' && Array.isArray(entry.changes) ? entry.changes : null;
    if (!fields || !list || typeof entry.id !== 'string' || !ENTRY_ID.test(entry.id) || !Number.isSafeInteger(entry.time)) {
      dropped += list ? Math.max(list.length, 1) : 1; continue;
    }
    for (const change of list) {
      const value = change && typeof change === 'object' ? change.value : null;
      if (!change || !fields.includes(change.field) || !value || typeof value !== 'object' || Array.isArray(value)) { dropped++; continue; }
      if (rows.length >= CHANGES_MAX) { dropped++; continue; }
      const text = JSON.stringify(value);
      const id = changeId(value) ?? 'h' + await digest(text);
      const verb = typeof value.verb === 'string' ? value.verb : '';
      const key = await digest([payload.object, entry.id, String(entry.time), change.field, id, verb].join('|'));
      const oversize = encoder.encode(text).length > VALUE_MAX;
      rows.push({key, object: payload.object, entry_id: entry.id, time: entry.time, field: change.field,
        value: oversize ? null : text, oversize});
    }
  }
  return {rows, dropped};
}

export async function webhookRequest(request, env, url) {
  try {
    if (url.pathname !== '/webhook/meta') return reply(404, {error: 'not_found'});
    if (request.method === 'GET') {
      if (!env.META_WEBHOOK_VERIFY_TOKEN) return reply(503, {error: 'webhook_unavailable'});
      const params = url.searchParams;
      // Meta の本物の確認は 3 つのほかの引数も付けてくる（10/30 本番で 400 を返していた）。要る 3 つだけを見る。
      if (params.get('hub.mode') !== 'subscribe' || !params.has('hub.verify_token'))
        return reply(400, {error: 'invalid_request'});
      const challenge = params.get('hub.challenge');
      // text/plain でそのまま返すので、制御文字・HTML の記号・長すぎるものだけを断る。
      if (!challenge || challenge.length > 1024 || /[\u0000-\u001f\u007f<>"'&]/.test(challenge)) return reply(400, {error: 'invalid_request'});
      if (!await sameSecret(params.get('hub.verify_token'), env.META_WEBHOOK_VERIFY_TOKEN)) return reply(403, {error: 'forbidden'});
      return new Response(challenge, {status: 200, headers: {...HEADERS, 'content-type': 'text/plain; charset=utf-8'}});
    }
    if (request.method !== 'POST') return reply(405, {error: 'method_not_allowed'});
    // secret を入れる前に deploy した間は 503（Meta は送り直す・36 時間まで）。
    if (!env.META_APP_SECRET || !env.WEBHOOK_INBOX) return reply(503, {error: 'webhook_unavailable'});
    if (url.search) return reply(400, {error: 'invalid_request'});
    let raw;
    try { raw = await rawBody(request, WEBHOOK_BODY_MAX); } catch { return reply(413, {error: 'too_large'}); }
    if (!await signatureValid(raw, request.headers.get('x-hub-signature-256'), env.META_APP_SECRET)) return reply(403, {error: 'forbidden'});
    let payload;
    try { payload = JSON.parse(new TextDecoder('utf-8', {fatal: true}).decode(raw)); } catch { return reply(400, {error: 'invalid_request'}); }
    const flat = await changes(payload);
    const result = await webhookStub(env).receive(flat.rows, flat.dropped);
    return reply(result.status, result.body);
  } catch { return reply(503, {error: 'webhook_unavailable'}); }
}
