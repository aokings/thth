import {deletionRequest} from './deletion.js';
import {relayRequest, receiveCallback} from "./relay.js";
import {frame,t} from './person.js';

/**
 * THTH の認可の受け口（thth.me）と、製品の紹介ページの配り手。
 *
 * この Worker は認可の受け口・短命relay・Meta 用の 3 本を持つ。
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
 * 登録済みflowは短命relayへ保存し、未登録なら貼付用の画面を出す。
 * 外部リソースも読み込まない（フォントも解析も無し）。
 */

const esc = (s) => String(s).replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

function html(body, status = 200) {
  return new Response(frame(body, "THTH 認可の受け口"), {
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
      `<h1>${t('認可されませんでした','Authorization was not completed')}</h1>
       <p class="sub">${t('Threads 側から次の理由が返りました。','Threads returned the following reason.')}</p>
       <p class="code err">${esc(error)}${desc ? " — " + esc(desc) : ""}</p>
       <p class="note">${t('ターミナルに戻って <code>thth auth</code> をやり直してください。','Return to your terminal and run <code>thth auth</code> again.')}</p>`);
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
      : `<p class="note err">${t('state が付いていません。この戻りは <code>thth auth</code> に受け付けられません。もう一度 <code>thth auth</code> から始めてください。','This return has no state, so <code>thth auth</code> cannot accept it. Start again with <code>thth auth</code>.')}</p>`;

    return html(
      `<h1>${t('認可コードを受け取りました','Authorization code received')}</h1>
       <p class="sub">${t('下の <strong>URL 全体</strong>をターミナルに貼ってください（<code>code</code> だけでは足りません）。1 時間で切れる使い捨てです。','Paste the complete URL below into your terminal. The <code>code</code> alone is not enough. It can be used once and expires in one hour.')}</p>
       <p class="code" id="c">${esc(paste)}</p>
       <button id="b">${t('コピー','Copy')}</button>
       ${missing}
       <p class="note">${t('この画面は撮らないでください。貼り終えたら閉じて構いません。','Do not record this page. Close it after pasting the URL.')}</p>
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
     <p class="sub">${t('Threads の認可の受け口です。ここを直接開いても何もありません。','This is the Threads authorization return page. Opening it directly does nothing.')}</p>
     <p class="note">${t('ターミナルで <code>thth auth &lt;account&gt;</code> を実行すると認可 URL が表示されます。承認するとこのページに戻ってくるので、表示された <strong>URL 全体</strong>をターミナルに貼ります。','Run <code>thth auth &lt;account&gt;</code> in a terminal to show an authorization URL. After approval, return here and paste the complete displayed URL into the terminal.')}</p>`);
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    if (url.pathname === "/oauth/client-metadata.json") return new Response("Not Found\n", {
      status: 404, headers: {"cache-control": "no-store", "referrer-policy": "no-referrer"}});
    if (url.pathname.startsWith("/relay/")) return relayRequest(request, env, url);

    // Existing paste fallback remains byte-compatible when relay is unavailable.
    // 貼付fallbackは紹介ページを足す前の振る舞いを保つ。
    // （path・本文・ヘッダ・referrer-policy・history.replaceState・秘密を残さない）。
    // 登録してある redirect_uri は https://thth.me/callback/ （docs/導入_…§4）。
    if (url.pathname === "/callback" || url.pathname.startsWith("/callback/")) {
      const received = (url.pathname === "/callback" || url.pathname === "/callback/")
        ? await receiveCallback(request, env, url) : null;
      if (received instanceof Response) return received;
      if (received === "consumed") return html(
        `<h1>${t('すでに受け取り済みです','Already received')}</h1><p>${t('完了しなかった場合は、ターミナルで認可をやり直してください。','If it did not finish, run authorization again in your terminal.')}</p>
         <script>try { history.replaceState(null, "", location.pathname); } catch (e) {}</script>`);
      if (received === "ready-invite") return html(
        `<h1>${t('認可を受け付けました','Authorization received')}</h1><p class="status">${t('このタブを閉じて、<strong>最初に開いた招待のタブ</strong>に戻ってください。数十秒で用意ができます。','Close this tab and go back to <strong>the invitation tab you opened first</strong>. Your account will be ready in a few tens of seconds.')}</p>
         <script>try { history.replaceState(null, "", location.pathname); } catch (e) {}</script>`);
      if (received === "ready") return html(
        `<h1>${t('認可を受け付けました','Authorization received')}</h1><p>${t('ターミナル（VM）が受け取ります。貼り付けは要りません。','The terminal receives it. You do not need to paste anything.')}</p>
         <script>try { history.replaceState(null, "", location.pathname); } catch (e) {}</script>`);
      return callbackPage(url);
    }

    // Existing acknowledgement endpoints only; these do not perform account/data deletion.
    if (url.pathname === "/deauthorize") {
      return new Response(null, { status: 200 });
    }
    if (url.pathname === "/data-deletion" || url.pathname === "/data-deletion-status") {
      return deletionRequest(request, env, url);
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
