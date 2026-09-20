import {test, before, after} from "node:test";
import assert from "node:assert/strict";
import {randomBytes, createHash} from "node:crypto";
import {Miniflare, Log, LogLevel, convertV4MiniflareOptions} from "miniflare";
import {fileURLToPath} from "node:url";
import {readFile} from "node:fs/promises";
import worker from "../src/index.js";

const opaque = () => randomBytes(32).toString("base64url");
const hash = s => createHash("sha256").update(s).digest("hex");
const logs = [], secrets = [];
class CaptureLog extends Log {
  constructor() { super(LogLevel.NONE); }
  log(message) { logs.push(String(message)); }
}
let mf;
before(async () => {
  const modules = await Promise.all(["test/harness.js", "src/index.js", "src/relay.js", "src/relay-object.js"].map(async name => {
    const path=fileURLToPath(new URL("../"+name,import.meta.url));
    return {type:"ESModule",path,contents:await readFile(path,"utf8")};
  }));
  mf = new Miniflare(convertV4MiniflareOptions({modules,
    modulesRoot: fileURLToPath(new URL("..", import.meta.url)),
    compatibilityDate: "2026-09-01", log: new CaptureLog(),
    handleStructuredLogs: item => { logs.push(JSON.stringify(item)); },
    durableObjects: {AUTH_RELAY: {className: "TestRelay", useSQLite: true}},
    ratelimits: {AUTH_RATE_LIMIT: {namespace_id: "21101", simple: {limit: 120, period: 60}}}}));
  await mf.ready;
});
after(async () => {
  await mf?.dispose();
  const text = logs.join("\n");
  const hits=logs.filter(line=>secrets.some(secret=>line.includes(secret)));
  for(const line of hits) { let safe=line; for(const secret of secrets) safe=safe.replaceAll(secret,"[dynamic-fake-redacted]"); console.error(safe); }
  assert.equal(hits.length,0,"runtime log contains a credential");
});
function flow() {
  const state = opaque(), key = opaque(), code = opaque();
  secrets.push(state, key, code);
  return {state, key, code, ip: opaque()};
}
async function req(f, method = "GET", body, key = f.key) {
  return mf.dispatchFetch(`https://relay.test/relay/${f.state}`, {method,
    headers: {"cf-connecting-ip": f.ip, ...(key ? {authorization: `Bearer ${key}`} : {}),
      ...(body !== undefined ? {"content-type": "application/json"} : {})},
    ...(body !== undefined ? {body: typeof body === "string" ? body : JSON.stringify(body)} : {})});
}
const register = f => req(f, "POST", {read_key_hash: hash(f.key)});
const callback = f => mf.dispatchFetch(`https://relay.test/callback/?code=${f.code}&state=${f.state}`, {headers: {"cf-connecting-ip": f.ip}});
async function control(f, data = {}) {
  return (await mf.dispatchFetch(`https://relay.test/__test/${f.state}`, {method:"POST",body:JSON.stringify(data)})).json();
}

test("unknown GET has no persistent record or alarm; missing/wrong key cannot consume", async () => {
  const f=flow(); assert.equal((await req(f,"GET",undefined,null)).status,404);
  assert.deepEqual(await control(f), {row:null,alarm:null,rows:0});
  assert.equal((await register(f)).status,201);
  assert.equal((await req(f,"GET",undefined,null)).status,401);
  assert.equal((await req(f,"GET",undefined,opaque())).status,401);
  assert.equal((await control(f)).row.polls,0);
  assert.equal((await register(f)).status,409);
  assert.equal((await req(f)).status,404);
  assert.equal((await control(f)).row.polls,1);
});
test("parallel consumes return code once; duplicate callback cannot overwrite or reinsert", async () => {
  const f=flow(); await register(f);
  const received=await callback(f); assert.equal(received.status,200);
  const page=await received.text();assert.ok(!page.includes(f.code));
  assert.ok(page.includes("history.replaceState"));
  const first=(await control(f)).row;
  await callback({...f,code:opaque()}); assert.ok((await control(f)).row.code===f.code,"original callback code changed");
  const responses=await Promise.all(Array.from({length:16},()=>req(f)));
  assert.equal(responses.filter(r=>r.status===200).length,1);
  assert.equal(responses.filter(r=>r.status===404).length,15);
  const payload=await responses.find(r=>r.status===200).json();
  assert.ok(payload.code===f.code,"wrong returned code");assert.equal(payload.received_at,new Date(first.received_at).toISOString());
  assert.equal((await control(f)).row.status,"consumed");
  assert.ok(!Object.hasOwn((await control(f)).row,"code"));
  await callback(f); assert.equal((await req(f)).status,404);
  assert.equal((await register(f)).status,409);
});
test("code TTL300 and state TTL600 boundaries; cleanup removes payload and hash", async () => {
  for (const delay of [0, 500_000]) {
    const f=flow(), start=Date.now()+10_000;
    await control(f,{clock:start});await register(f);
    await control(f,{clock:start+delay});await callback(f);
    const row=(await control(f)).row, deadline=Math.min(start+600_000,start+delay+300_000);
    assert.equal(row.code_expires_at,deadline);
    await control(f,{clock:deadline,cleanup:true});
    const expired=await control(f);
    assert.ok(!JSON.stringify(expired).includes(f.code));assert.ok(!JSON.stringify(expired).includes(hash(f.key)));
    assert.equal((await req(f)).status,404);
    await callback(f);assert.equal((await req(f)).status,404);
    await control(f,{clock:start+600_000,cleanup:true});
    assert.deepEqual(await control(f),{row:null,alarm:null,rows:0});
    // New VM flows must use new random states; no permanent reuse blacklist.
    assert.equal((await register(f)).status,201);
  }
});
test("at 299999ms code is still retrievable", async () => {
  const f=flow(), now=Date.now()+10_000;await control(f,{clock:now});await register(f);await callback(f);
  await control(f,{clock:now+299_999});assert.equal((await req(f)).status,200);
});
test("400 authenticated polls allowed; wrong keys do not exhaust the state", async () => {
  const f=flow();await register(f);
  // Different peer buckets avoid testing the separate approximate IP limiter here.
  for(let i=0;i<400;i++) {f.ip=opaque();assert.equal((await req(f)).status,404);}
  f.ip=opaque();assert.equal((await req(f,"GET",undefined,opaque())).status,401);
  assert.equal((await control(f)).row.polls,400);
  assert.equal((await req(f)).status,404);assert.equal((await control(f)).row.status,"expired");
  assert.ok(!JSON.stringify(await control(f)).includes(hash(f.key)));
  await callback(f);assert.equal((await req(f)).status,404);
});
test("IP limit is 120/minute and produces safe 429", async () => {
  const f=flow();
  for(let i=0;i<120;i++) assert.equal((await req(f)).status,404);
  const response=await req(f);assert.equal(response.status,429);
  assert.equal(response.headers.get("cache-control"),"no-store");
  assert.equal(response.headers.get("referrer-policy"),"no-referrer");
  assert.equal(response.headers.get("retry-after"),"2");
});
test("bad input and unsupported methods never register", async () => {
  const f=flow();
  for(const body of [{read_key_hash:"bad"},{read_key_hash:hash(f.key),code:f.code},
    `{"read_key_hash":"${hash(f.key)}","read_key_hash":"${hash(f.key)}"}`,"x".repeat(300)]) {
    assert.equal((await req(f,"POST",body)).status,400);
  }
  assert.equal((await req(f,"PUT")).status,405);
  assert.equal((await req({...f,state:"bad"})).status,400);
  assert.deepEqual(await control(f),{row:null,alarm:null,rows:0});
});
test("storage failure rolls back and does not expose exception secrets", async () => {
  const f=flow();await register(f);await callback(f);
  await control(f,{fault:f.code+f.key});
  const response=await req(f);assert.equal(response.status,503);
  assert.ok(!(await response.text()).includes(f.code));
  assert.equal((await control(f)).row.status,"ready");
  await control(f,{fault:null});assert.equal((await req(f)).status,200);
});
test("unregistered and storage-outage callbacks retain manual fallback only", async () => {
  const f=flow();const response=await callback(f);
  assert.ok((await response.text()).includes(f.code));
  assert.deepEqual(await control(f),{row:null,alarm:null,rows:0});
  const env={AUTH_RELAY:{getByName(){throw new Error(f.key)}},AUTH_RATE_LIMIT:{limit:async()=>({success:true})}};
  const result=await worker.fetch(new Request(`https://relay.test/callback/?code=${f.code}&state=${f.state}`),env);
  const text=await result.text();assert.ok(text.includes(f.code));assert.ok(!text.includes(f.key));
  const unavailable=await worker.fetch(new Request(`https://relay.test/relay/${f.state}`),env);
  assert.equal(unavailable.status,503);assert.ok(!(await unavailable.text()).includes(f.key));
});
test("Bluesky metadata remains 404; config disables observation and selects SQLite", async () => {
  assert.equal((await mf.dispatchFetch("https://relay.test/oauth/client-metadata.json")).status,404);
  const config=await readFile(new URL("../wrangler.jsonc",import.meta.url),"utf8");
  assert.match(config,/"observability":\s*\{\s*"enabled": false/);
  assert.match(config,/"new_sqlite_classes": \["AuthRelay"\]/);
  assert.ok(!config.includes('"remote": true'));
});

test("HEAD cannot consume a ready code and does not count", async () => {
  const f=flow();await register(f);await callback(f);
  assert.equal((await req(f,"HEAD")).status,405);
  assert.equal((await control(f)).row.polls,0);
  assert.equal((await req(f)).status,200);
});
test("pending state expires at600 and callback does not register it", async () => {
  const f=flow(), now=Date.now()+10_000;await control(f,{clock:now});await register(f);
  await control(f,{clock:now+600_000,cleanup:true});
  const result=await callback(f);assert.ok((await result.text()).includes(f.code));
  assert.deepEqual(await control(f),{row:null,alarm:null,rows:0});
});
test("register and callback faults rollback SQLite writes and never claim success", async () => {
  const f=flow();await control(f,{fault:f.code});
  assert.equal((await register(f)).status,503);
  assert.deepEqual(await control(f),{row:null,alarm:null,rows:0});
  await control(f,{fault:null});await register(f);await control(f,{fault:f.key});
  const response=await callback(f);assert.ok((await response.text()).includes(f.code));
  assert.equal((await control(f)).row.status,"pending");
  await control(f,{fault:null});await callback(f);assert.equal((await req(f)).status,200);
});
test("bounded callback inputs are rejected without storage", async () => {
  const f=flow();
  for(const query of [`code=${"x".repeat(4097)}&state=${f.state}`,
    `code=${f.code}&state=${f.state}&state=${f.state}`,`code=a%00b&state=${f.state}`]) {
    const response=await mf.dispatchFetch(`https://relay.test/callback/?${query}`);
    assert.equal(response.status,400);assert.ok(!(await response.text()).includes(f.state));
  }
  assert.deepEqual(await control(f),{row:null,alarm:null,rows:0});
});

test("child callback paths are paste-only and never mutate a registered flow", async () => {
  const f=flow();await register(f);
  const response=await mf.dispatchFetch(`https://relay.test/callback/unexpected?code=${f.code}&state=${f.state}`);
  assert.ok((await response.text()).includes(f.code));assert.equal((await control(f)).row.status,"pending");
  assert.equal((await req(f)).status,404);
});
test("alarm failure rolls back registration/ready record with its alarm", async () => {
  const f=flow();await control(f,{alarmFault:f.code});
  assert.equal((await register(f)).status,503);
  assert.deepEqual(await control(f),{row:null,alarm:null,rows:0});
  await control(f,{alarmFault:null});await register(f);
  const before=await control(f);await control(f,{alarmFault:f.key});
  const response=await callback(f);assert.ok((await response.text()).includes(f.code));
  const after=await control(f);assert.equal(after.row.status,"pending");assert.equal(after.alarm,before.alarm);
  await control(f,{alarmFault:null});await callback(f);assert.equal((await req(f)).status,200);
});
