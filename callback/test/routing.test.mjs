import {test} from "node:test";
import assert from "node:assert/strict";
import {spawn} from "node:child_process";
import {once} from "node:events";
import {createServer} from "node:net";
import {randomBytes, createHash} from "node:crypto";
import {mkdir, mkdtemp, writeFile, rm} from "node:fs/promises";
import path from "node:path";
import {fileURLToPath} from "node:url";

const cwd=fileURLToPath(new URL("..",import.meta.url));
const opaque=()=>randomBytes(32).toString("base64url");
const pause=ms=>new Promise(resolve=>setTimeout(resolve,ms));

test("real Wrangler local routing, persistent SQLite and silent HTTP logs", {timeout:30000}, async () => {
  const base=path.join(cwd,".test-tmp");await mkdir(base,{recursive:true});
  const tmp=await mkdtemp(path.join(base,"routing-"));
  const state=opaque(), key=opaque(), code=opaque(), home=path.join(tmp,"home"), assets=path.join(tmp,"assets");
  await mkdir(home);await mkdir(path.join(assets,"relay"),{recursive:true});
  await mkdir(path.join(assets,"callback"));await mkdir(path.join(assets,"oauth"));
  await writeFile(path.join(assets,"relay",state),"shadow must not serve");
  await writeFile(path.join(assets,"callback","index.html"),"shadow must not serve");
  await writeFile(path.join(assets,"oauth","client-metadata.json"),"shadow must not serve");
  await writeFile(path.join(tmp,"empty.env"),"");
  const listener=createServer();listener.listen(0,"127.0.0.1");await once(listener,"listening");
  const port=listener.address().port;await new Promise(resolve=>listener.close(resolve));
  const logs=[];
  let child;
  const start=async()=> {
    child=spawn(process.execPath,["node_modules/wrangler/bin/wrangler.js","dev","--local","--ip","127.0.0.1",
      "--port",String(port),"--log-level","none","--persist-to",path.join(tmp,"state"),"--assets",assets,
      "--env-file",path.join(tmp,"empty.env")],{cwd,env:{PATH:process.env.PATH,HOME:home,XDG_CONFIG_HOME:home,
        WRANGLER_SEND_METRICS:"false",CLOUDFLARE_LOAD_DEV_VARS_FROM_DOT_ENV:"false"},stdio:["ignore","pipe","pipe"]});
    child.stdout.on("data",x=>logs.push(x.toString()));child.stderr.on("data",x=>logs.push(x.toString()));
    for(let i=0;i<150;i++) {
      if(child.exitCode!==null) throw new Error("local runtime exited before ready");
      try { await fetch(`http://127.0.0.1:${port}/health-check-local`,{signal:AbortSignal.timeout(100)});return; }
      catch { await pause(50); }
    }
    throw new Error("local runtime did not start");
  };
  const stop=async()=>{if(child&&child.exitCode===null){child.kill("SIGTERM");await once(child,"exit");}};
  const url=`http://127.0.0.1:${port}`;
  try {
    await start();
    const absent=await fetch(`${url}/relay/${state}`);assert.equal(absent.status,404);
    assert.ok(!(await absent.text()).includes("shadow"));
    assert.equal((await fetch(`${url}/oauth/client-metadata.json`)).status,404);
    const registration=await fetch(`${url}/relay/${state}`,{method:"POST",headers:{"content-type":"application/json"},
      body:JSON.stringify({read_key_hash:createHash("sha256").update(key).digest("hex")})});
    assert.equal(registration.status,201);
    const received=await fetch(`${url}/callback/?state=${state}&code=${code}`);
    const body=await received.text();assert.equal(received.status,200);
    assert.ok(!body.includes("shadow")&&!body.includes(code)&&body.includes("貼り付けは要りません"));
    // Restart the real runtime: ready/consumed must not be just in-memory state.
    await stop();await start();
    const responses=await Promise.all(Array.from({length:8},()=>fetch(`${url}/relay/${state}`,{headers:{authorization:`Bearer ${key}`}})));
    assert.equal(responses.filter(r=>r.status===200).length,1);
    const value=await responses.find(r=>r.status===200).json();assert.ok(value.code===code,"wrong returned code");
    await stop();await start();
    assert.equal((await fetch(`${url}/relay/${state}`,{headers:{authorization:`Bearer ${key}`}})).status,404);
  } finally {
    await stop();
    const text=logs.join("\n");
    await rm(tmp,{recursive:true,force:true});
    for(const secret of [state,key,code]) assert.ok(!text.includes(secret),"local stdout/stderr leaked a credential");
  }
});
