// 前向きな断り（設計「監査と審査の機械化と前向きな断り」§3）: Worker の reply が断りの答えに目録の 3 つを足す。
// 目録の写し（src/refusal-catalog.js）は tools/render_refusals.py の生成物で、ずれは Python の試験が縛る。
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import {reply,explain,REFUSAL_KEYS} from '../src/relay.js';
import {REFUSALS} from '../src/refusal-catalog.js';

test('every catalog row has a why and a fix, and better is text or null',()=>{
  const codes=Object.keys(REFUSALS);assert.ok(codes.length>=50,String(codes.length));
  for(const code of codes){
    assert.match(code,/^[a-z][a-z0-9]*(?:_[a-z0-9]+)+$/);
    const row=REFUSALS[code];assert.deepEqual(Object.keys(row),REFUSAL_KEYS,code);
    assert.ok(typeof row.why==='string'&&row.why,code);assert.ok(typeof row.fix==='string'&&row.fix,code);
    assert.ok(row.better===null||typeof row.better==='string'&&row.better,code);
    // Worker は穴を埋めない: 型の穴（<口座> など）を書かない。
    for(const text of [row.why,row.fix,row.better??''])assert.ok(!/<(口座|主体|名前|時刻)>/.test(text),code);
  }
});

test('a refusal keeps its keys and adds the three after them; nothing is overwritten',async()=>{
  const plain=await reply(410,{error:'media_expired'}).json();
  assert.deepEqual(plain,{error:'media_expired',...REFUSALS.media_expired});
  assert.deepEqual(Object.keys(plain),['error','why','fix','better']);
  const own=await reply(400,{error:'invalid_request',why:'own',detail:'x'}).json();
  assert.deepEqual(own,{error:'invalid_request',why:'own',detail:'x',fix:REFUSALS.invalid_request.fix,better:REFUSALS.invalid_request.better});
  const fallback=await reply(503).json();assert.deepEqual(fallback,{error:'relay_unavailable',...REFUSALS.relay_unavailable});
});

test('answers that do not refuse, unknown codes and odd bodies are left exactly as they were',async()=>{
  for(const [status,body] of [[200,{error:'invalid_key'}],[202,{status:'pending'}],[200,{status:'ok'}],[403,{error:'forbidden'}],
    [409,{error:'not_in_catalog_code'}],[400,{error:7}],[400,[{error:'invalid_request'}]],[400,null]]){
    assert.equal(await reply(status,body).text(),JSON.stringify(body),JSON.stringify([status,body]));
    assert.equal(explain(status,body),body);
  }
  const range=reply(416,{error:'invalid_range'});assert.equal(range.status,416);assert.equal((await range.json()).error,'invalid_range');
  assert.equal(reply(429,{error:'rate_limited'}).headers.get('retry-after'),'2');
});

test('the generated table names its generator (it is not edited by hand)',async()=>{
  const text=await readFile(fileURLToPath(new URL('../src/refusal-catalog.js',import.meta.url)),'utf8');
  assert.ok(text.startsWith('// 生成物: tools/render_refusals.py'));
});
