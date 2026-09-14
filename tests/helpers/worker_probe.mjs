/**
 * callback Worker の handler を **Node で直接呼んで** 応答を JSON で吐く。
 *
 * `wrangler dev` を立てずに「認可の受け口の応答が 1 バイトも変わっていない」を
 * 固定するための道具（tests/test_site.py が使う）。assets は偽物を渡し、
 * Worker が自分で書く path と assets へ渡す path の区別も見えるようにする。
 *
 *   node tests/helpers/worker_probe.mjs <src/index.js への絶対パス>
 */
import { pathToFileURL } from "node:url";

const modulePath = process.argv[2];
if (!modulePath) {
  console.error("usage: node worker_probe.mjs <path to src/index.js>");
  process.exit(2);
}

// 決まった答えが返る path だけを並べる（/data-deletion は時刻を含むので別扱い）。
const URLS = [
  "https://thth.me/callback/",
  "https://thth.me/callback",
  "https://thth.me/callback/?code=FAKE-CODE-abc123&state=FAKE-STATE-xyz",
  "https://thth.me/callback/?code=FAKE-CODE-abc123",
  "https://thth.me/callback/?error=access_denied&error_description=The+user+denied+your+request",
  "https://thth.me/callback/?code=%3Cscript%3E&x=1",
  "https://thth.me/deauthorize",
  "https://thth.me/data-deletion-status",
  "https://thth.me/",
  "https://thth.me/llms.txt",
  "https://thth.me/robots.txt",
];

const worker = (await import(pathToFileURL(modulePath).href)).default;

// assets は本物を持たないので、渡されたことだけが分かる偽物を置く。
const env = {
  ASSETS: {
    fetch: async (request) =>
      new Response("[ASSETS] " + new URL(request.url).pathname, {
        status: 598,
        headers: { "content-type": "text/plain; charset=utf-8" },
      }),
  },
};

const out = [];
for (const url of URLS) {
  const res = await worker.fetch(new Request(url), env, {});
  out.push({
    url,
    status: res.status,
    headers: Object.fromEntries([...res.headers.entries()].sort()),
    body: await res.text(),
  });
}
process.stdout.write(JSON.stringify(out, null, 2) + "\n");
