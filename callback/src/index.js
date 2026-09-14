/**
 * THTH の認可の受け口（thth.me）と、製品の紹介ページの配り手。
 *
 * この Worker が自分で書くのは **`/callback/` の受け口と Meta 用の 3 本だけ**。
 * それ以外（`/`・`/llms.txt`・`/robots.txt`）は `public/` の静的ファイル
 * （`wrangler.jsonc` の `assets`）に任せる——中身は `tools/build_site.py` が
 * repo の正本（`README.en.md`・`skills/thth/SKILL.md`・`llms.txt`）から作る。
 * 設計 v2 §3「ドメイン」: `thth.me` ＝製品の入口（認可ページは残す）。
 *
 * Threads の OAuth は redirect_uri が HTTPS でなければならず localhost も使えない
 * ので、着地点が要る。研究所やアスモンの公開サイトに置くと (1) 意味が合わない
 * （THTH は 4 プロジェクトで使う基盤）(2) 認可コードが公開サイトのアクセスログに
 * 乗る、の 2 つが起きるため、THTH 専用のドメインを 1 つ持つことにした
 * （masaru 裁定 2026-09-09）。
 *
 * このページは受け取ったコードを画面に出すだけで、どこにも送らない。
 * 外部リソースも読み込まない（フォントも解析も無し）。
 */

const PAGE_HEAD = `<!doctype html>
<html lang="ja"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow,noarchive">
<title>THTH 認可の受け口</title>
<style>
  :root { color-scheme: light dark; }
  body { margin:0; min-height:100vh; display:flex; align-items:center; justify-content:center;
         font: 16px/1.7 -apple-system, BlinkMacSystemFont, "Hiragino Sans", "Noto Sans JP", sans-serif;
         background:#faf9f7; color:#1a1a1a; padding:24px; }
  @media (prefers-color-scheme: dark) { body { background:#16161a; color:#eee; } }
  main { max-width: 640px; width:100%; }
  h1 { font-size:1.15rem; font-weight:600; margin:0 0 4px; letter-spacing:.02em; }
  .sub { font-size:.85rem; opacity:.65; margin:0 0 24px; }
  .code { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size:.95rem;
          word-break:break-all; background:rgba(127,127,127,.12); border-radius:10px;
          padding:16px; margin:0 0 12px; user-select:all; }
  button { font:inherit; font-size:.9rem; padding:9px 18px; border-radius:8px; cursor:pointer;
           border:1px solid rgba(127,127,127,.4); background:transparent; color:inherit; }
  button:hover { background:rgba(127,127,127,.12); }
  .note { font-size:.85rem; opacity:.7; margin-top:20px; }
  .err { color:#b3261e; } @media (prefers-color-scheme: dark) { .err { color:#f2b8b5; } }
</style></head><body><main>`;
const PAGE_FOOT = `</main></body></html>`;

const esc = (s) => String(s).replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

function html(body, status = 200) {
  return new Response(PAGE_HEAD + body + PAGE_FOOT, {
    status,
    headers: {
      "content-type": "text/html; charset=utf-8",
      "cache-control": "no-store",
      // 認可コードがクエリに乗るので、外部へ referrer を出さない。
      "referrer-policy": "no-referrer",
      "x-robots-tag": "noindex, nofollow",
    },
  });
}

function callbackPage(url) {
  const code = url.searchParams.get("code");
  const error = url.searchParams.get("error");
  const desc = url.searchParams.get("error_description");

  if (error) {
    return html(
      `<h1>認可されませんでした</h1>
       <p class="sub">Threads 側から次の理由が返りました。</p>
       <p class="code err">${esc(error)}${desc ? " — " + esc(desc) : ""}</p>
       <p class="note">ターミナルに戻って <code>thth auth</code> をやり直してください。</p>`);
  }

  if (code) {
    // **貼るのは URL 全体**（`code` だけではない）。`thth auth` は 2026-09-14 の
    // セキュリティ監査（P2-4）以降、**`state` の照合が通らないと受け付けない**
    // ——「この道具が出した認可 URL の戻りかどうか」を確かめる術がないため。
    // 受け口が `code` だけを見せていた間、貼っても必ず「state がありません」で
    // 断られていた（2026-09-15 に masaru が詰まった）。**道具が要るものを、
    // 受け口が出す。**
    //
    // 出すのは `code` と `state` の 2 つだけに絞る（Meta が他の鍵を付けて
    // きても写さない——余計なものを画面に残さない）。
    const state = url.searchParams.get("state");
    const parts = ["code=" + encodeURIComponent(code)];
    if (state) parts.push("state=" + encodeURIComponent(state));
    const paste = url.origin + url.pathname + "?" + parts.join("&");

    const missing = state
      ? ""
      : `<p class="note err">state が付いていません。この戻りは <code>thth auth</code> に
         受け付けられません（もう一度 <code>thth auth</code> から始めてください）。</p>`;

    return html(
      `<h1>認可コードを受け取りました</h1>
       <p class="sub">下の <strong>URL 全体</strong>をターミナルに貼ってください
       （<code>code</code> だけでは足りません）。1 時間で切れる使い捨てです。</p>
       <p class="code" id="c">${esc(paste)}</p>
       <button id="b">コピー</button>
       ${missing}
       <p class="note">この画面は撮らないでください。貼り終えたら閉じて構いません。</p>
       <script>
         // アドレスバーからコードを消す（撮影・肩越しの覗き見への備え）。
         try { history.replaceState(null, "", location.pathname); } catch (e) {}
         document.getElementById("b").onclick = async () => {
           try {
             await navigator.clipboard.writeText(document.getElementById("c").textContent);
             document.getElementById("b").textContent = "コピーしました";
           } catch (e) { document.getElementById("b").textContent = "手で選んでコピーしてください"; }
         };
       <\/script>`);
  }

  return html(
    `<h1>THTH</h1>
     <p class="sub">Threads の認可の受け口です。ここを直接開いても何もありません。</p>
     <p class="note">ターミナルで <code>thth auth &lt;account&gt;</code> を実行すると、
     認可 URL が表示されます。承認するとこのページに戻ってくるので、
     表示された <strong>URL 全体</strong>をターミナルに貼ります。</p>`);
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    // 認可の受け口。**ここの振る舞いは紹介ページを足す前と 1 バイトも変えない**
    // （path・本文・ヘッダ・referrer-policy・history.replaceState・秘密を残さない）。
    // 登録してある redirect_uri は https://thth.me/callback/ （docs/導入_…§4）。
    if (url.pathname === "/callback" || url.pathname.startsWith("/callback/")) {
      return callbackPage(url);
    }

    // Meta が要求する 2 本。開発モードで masaru 自身のアカウントしか使わないが、
    // 欄を埋めないとアプリの設定が保存できないので、素直に応答だけ返す。
    if (url.pathname === "/deauthorize") {
      return new Response(null, { status: 200 });
    }
    if (url.pathname === "/data-deletion") {
      return Response.json({
        url: "https://thth.me/data-deletion-status",
        confirmation_code: "thth-" + Date.now().toString(36),
      });
    }
    if (url.pathname === "/data-deletion-status") {
      return html(
        `<h1>データ削除について</h1>
         <p class="sub">THTH は masaru 個人の道具で、Threads から取得した内容は
         本人の repository にのみ保存されます。</p>
         <p class="note">削除の依頼は repository の所有者へ直接どうぞ。</p>`);
    }

    // 残りは静的な紹介ページ（public/）。`assets` を先に見る設定なので通常は
    // ここへ来ないが、来たときも同じものを返す（どちらの経路でも同じ画面）。
    if (env && env.ASSETS) {
      return env.ASSETS.fetch(request);
    }
    return new Response("Not Found\n", {
      status: 404,
      headers: { "content-type": "text/plain; charset=utf-8" },
    });
  },
};
