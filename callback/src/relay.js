// No request-scoped secrets are kept in module state or diagnostic messages.
import {REFUSALS} from "./refusal-catalog.js";
export const STATE_PATTERN = /^[A-Za-z0-9_-]{43}$/;
export const HASH_PATTERN = /^[a-f0-9]{64}$/;
export const MAX_CODE_BYTES = 4096;
const HEADERS = {"cache-control": "no-store", "referrer-policy": "no-referrer"};

// 前向きな断り（設計「監査と審査の機械化と前向きな断り」§3）: 断りの答え（4xx・5xx の {error: 符丁}）に、目録の
// 3 つ（why・fix・better）を足す。今ある鍵は消さず名も変えず、同じ名の鍵があれば上書きしない。目録に無い符丁と、
// 断らない答え（2xx・VM の JSON をそのまま渡すもの）には何も足さない。文は静的（穴を埋めない）。
export const REFUSAL_KEYS = ["why", "fix", "better"];
export function explain(status, body) {
  if (!(status >= 400) || body === null || typeof body !== "object" || Array.isArray(body) ||
      typeof body.error !== "string" || !Object.hasOwn(REFUSALS, body.error)) return body;
  const found = REFUSALS[body.error], out = {...body};
  for (const key of REFUSAL_KEYS) if (!Object.hasOwn(out, key)) out[key] = found[key];
  return out;
}
export function reply(status, body = {error: "relay_unavailable"}) {
  return Response.json(explain(status, body), {status, headers: {...HEADERS,
    ...(status === 429 ? {"retry-after": "2"} : {})}});
}
export async function digest(value) {
  return [...new Uint8Array(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(value)))]
    .map(b => b.toString(16).padStart(2, "0")).join("");
}
export async function relayStub(env, state) {
  return env.AUTH_RELAY.getByName(await digest(state));
}
export async function rateAllowed(request, env) {
  if (!env.AUTH_RATE_LIMIT) return false;
  const ip = request.headers.get("cf-connecting-ip");
  // Missing peer identity must not turn into an unlimited bucket.
  const key = await digest(ip || "unknown-peer");
  return (await env.AUTH_RATE_LIMIT.limit({key})).success;
}
async function readRegistration(request) {
  if (!/^application\/json(?:\s*;|$)/i.test(request.headers.get("content-type") || "")) return null;
  const reader = request.body?.getReader();
  if (!reader) return null;
  let size = 0;
  const chunks = [];
  try {
    while (true) {
      const {done, value} = await reader.read();
      if (done) break;
      size += value.byteLength;
      if (size > 256) { await reader.cancel(); return null; }
      chunks.push(value);
    }
    const bytes = new Uint8Array(size);
    let offset = 0;
    for (const value of chunks) { bytes.set(value, offset); offset += value.length; }
    const text = new TextDecoder("utf-8", {fatal: true}).decode(bytes);
    // The exact one-field wire shape also rejects duplicate JSON keys.
    if (!/^\s*\{\s*"read_key_hash"\s*:\s*"[a-f0-9]{64}"\s*\}\s*$/.test(text)) return null;
    return JSON.parse(text);
  } catch { return null; }
  finally { reader.releaseLock(); }
}
export async function relayRequest(request, env, url) {
  try {
    if (url.search || url.pathname.length > 64) return reply(400, {error: "invalid_request"});
    const state = url.pathname.slice("/relay/".length);
    if (!STATE_PATTERN.test(state)) return reply(400, {error: "invalid_request"});
    if (!env.AUTH_RELAY) return reply(503);
    if (!await rateAllowed(request, env)) return reply(429, {error: "rate_limited"});
    if (request.method !== "POST" && request.method !== "GET") return reply(405, {error: "method_not_allowed"});
    const stub = await relayStub(env, state);
    let result;
    if (request.method === "POST") {
      const body = await readRegistration(request);
      if (!body) return reply(400, {error: "invalid_request"});
      result = await stub.register(body.read_key_hash);
    } else {
      const auth = request.headers.get("authorization") || "";
      const match = /^Bearer ([A-Za-z0-9_-]{43})$/.exec(auth);
      result = await stub.consume(match ? await digest(match[1]) : null);
    }
    return reply(result.status, result.body);
  } catch { return reply(503); }
}
export async function receiveCallback(request, env, url) {
  if (!env?.AUTH_RELAY) return null; // Existing paste-only deployment / Node golden.
  try {
    if (request.method !== "GET" || url.href.length > 8192) return reply(400, {error: "invalid_request"});
    if (url.searchParams.getAll("state").length > 1 || url.searchParams.getAll("code").length > 1) return reply(400, {error: "invalid_request"});
    const state = url.searchParams.get("state"), code = url.searchParams.get("code");
    if ((state && state.length > 128) || (code && (/[\x00-\x1f\x7f]/.test(code) ||
        new TextEncoder().encode(code).length > MAX_CODE_BYTES))) return reply(400, {error: "invalid_request"});
    if (!STATE_PATTERN.test(state) || !code || url.searchParams.has("error")) return null;
    if (!await rateAllowed(request, env)) return reply(429, {error: "rate_limited"});
    const result = await (await relayStub(env, state)).receive(code);
    if (result.status === 200) return result.body?.invite === true ? "ready-invite" : "ready";
    return result.status === 409 ? result.body?.status : null;
  } catch { return null; } // Storage outage preserves the manual URL path, never logs it.
}
