#!/usr/bin/env python3
"""`thth.me` の紹介ページ（`callback/public/`）を **repo の正本から生成する**。

設計 v2 §3「ドメイン」: `thth.me` ＝製品の入口（README 相当・導入・`llms.txt`・
認可ページは残す）。その入口に置く 4 つのファイルをここで作る:

| 出力 | 正本 |
|---|---|
| `callback/public/index.html` | このスクリプトの `build_index()` に置く固定の製品紹介文 |
| `callback/public/llms.txt` | repo の `llms.txt`（**1 バイトも変えずに写す**） |
| `callback/public/robots.txt` | このスクリプト（認可の受け口だけを索引から外す） |
| `callback/public/privacy/index.html` | このスクリプト（`build_privacy()`。Meta の App Review が要求するプライバシーポリシー。2026-09-15） |

生成物を手で直さない。紹介文を直したら `build_index()` を直して、このスクリプトを
走らせ直す。
`--check` は書かずに突き合わせるだけで、ずれていれば非ゼロで終わる
（`tests/test_site.py` がこれと同じ突き合わせをする・作法 5 loud reject）。

外部のリソース（CSS・JS・フォント・画像）は 1 つも読み込まない。認可の受け口と
同じ礼儀——この入口を開いた人の browser から第三者へは何も飛ばない。

使い方:

    python tools/build_site.py            # callback/public/ に書く
    python tools/build_site.py --check    # 書かずに一致だけ見る
"""
from __future__ import annotations

import argparse
import html as html_mod
import pathlib
import re
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
README_EN = REPO_ROOT / "README.en.md"
SKILL = REPO_ROOT / "skills" / "thth" / "SKILL.md"
LLMS_TXT = REPO_ROOT / "llms.txt"
PUBLIC = REPO_ROOT / "callback" / "public"

GITHUB_BLOB = "https://github.com/aokings/thth/blob/main/"
GITHUB = "https://github.com/aokings/thth"
PYPI = "https://pypi.org/project/thth/"
REGISTRY_SEARCH = (
    "https://registry.modelcontextprotocol.io/v0.1/servers"
    "?search=io.github.aokings/thth"
)

# README.en.md 側（英語）。**順番も README のまま。**
EN_SECTIONS = ["What it guarantees", "What it refuses", "What it never does"]
# skills/thth/SKILL.md 側（日本語）。同じ 3 つ。
JA_SECTIONS = ["何を保証するか", "何を拒むか", "何を絶対にしないか"]

DESCRIPTION = (
    "AI と一緒に SNS の投稿を作成・管理するためのコマンドラインツール。"
    "Threads、Bluesky、Mastodon に対応。原稿も投稿後の記録も、自分の Git リポジトリで管理できます。"
)


# --------------------------------------------------------------------------
# 正本から節を取り出す
# --------------------------------------------------------------------------

def _join(head: str, tail: str) -> str:
    """折り返された行を繋ぐ。**和文どうしのあいだには空白を入れない。**

    markdown の改行は英文では語の区切り（空白 1 つ）だが、和文で同じことを
    すると「渡す）。 digest」のように不自然な隙間が出る。正本の文字は
    1 つも足さず引かず、繋ぎ目だけを文字種で決める。
    """
    if not head:
        return tail
    if ord(head[-1]) >= 0x3000 and ord(tail[0]) >= 0x3000:
        return head + tail
    return head + " " + tail


def _collect_bullets(lines: list[str], start: int) -> list[str]:
    """`start` 行目から続く箇条書きを、1 項目 1 文字列にして返す。

    継続行（先頭が空白）は前の項目に繋ぐ（`_join`）。箇条書き以外の行
    （見出し・`---`・空行の次の地の文）が来たら終わり。
    """
    items: list[str] = []
    for raw in lines[start:]:
        line = raw.rstrip("\n")
        if line.startswith("- "):
            items.append(line[2:].strip())
        elif line.strip() == "":
            if items:
                break
            continue
        elif line.startswith(" ") and items:
            items[-1] = _join(items[-1], line.strip())
        else:
            break
    return items


def sections_from(text: str, titles: list[str]) -> dict[str, list[str]]:
    """`**題**` か `## 題` の直後に続く箇条書きを題ごとに集める。"""
    lines = text.splitlines()
    found: dict[str, list[str]] = {}
    for i, line in enumerate(lines):
        stripped = line.strip()
        for title in titles:
            if stripped in (f"**{title}**", f"## {title}"):
                found[title] = _collect_bullets(lines, i + 1)
    missing = [t for t in titles if not found.get(t)]
    if missing:
        raise SystemExit(f"正本に節が見つからない（綴りが変わった？）: {missing}")
    return found


# --------------------------------------------------------------------------
# markdown の行内記法 → HTML（最小限。正本にある記法だけ）
# --------------------------------------------------------------------------

_CODE_RE = re.compile(r"`([^`]+)`")
_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
_BOLD_RE = re.compile(r"\*\*([^*]+)\*\*")


def _href(target: str) -> str:
    """repo 相対のリンクは GitHub の blob へ向ける（public には無いので）。"""
    if target.startswith(("http://", "https://", "mailto:")):
        return target
    return GITHUB_BLOB + target.lstrip("./")


def md_inline(text: str) -> str:
    """`code`・**bold**・[label](target) だけを HTML にする。

    コードスパンを先に取り置くのは、中の `*` や `[` を装飾と読まないため。
    """
    holds: list[str] = []

    def hold(inner: str) -> str:
        holds.append(inner)
        return f"\x00{len(holds) - 1}\x00"

    text = _CODE_RE.sub(lambda m: hold(m.group(1)), text)
    text = html_mod.escape(text, quote=False)
    text = _LINK_RE.sub(
        lambda m: f'<a href="{html_mod.escape(_href(m.group(2)), quote=True)}">'
                  f"{m.group(1)}</a>",
        text)
    text = _BOLD_RE.sub(lambda m: f"<strong>{m.group(1)}</strong>", text)
    text = re.sub(r"\x00(\d+)\x00",
                  lambda m: "<code>"
                            + html_mod.escape(holds[int(m.group(1))], quote=False)
                            + "</code>",
                  text)
    return text


def _ul(items: list[str]) -> str:
    return "<ul>\n" + "\n".join(f"  <li>{md_inline(i)}</li>" for i in items) + "\n</ul>"


# --------------------------------------------------------------------------
# 出力
# --------------------------------------------------------------------------

STYLE = """\
  /* 見た目は認可の受け口（src/index.js）に揃える（masaru 2026-09-14「あのシンプルなテイスト」）:
     小さめの見出し・罫線なし・淡い角丸の背景・余白で区切る。装飾を足さない。 */
  :root { color-scheme: light dark; --bg:#faf9f7; --fg:#1a1a1a; --soft:rgba(127,127,127,.12); }
  @media (prefers-color-scheme: dark) { :root { --bg:#16161a; --fg:#eee; } }
  * { box-sizing: border-box; }
  body { margin:0; background:var(--bg); color:var(--fg);
         font: 16px/1.7 -apple-system, BlinkMacSystemFont, "Hiragino Sans", "Noto Sans JP", sans-serif; }
  main { max-width: 640px; margin: 0 auto; padding: 48px 24px 72px; }
  h1 { font-size:1.15rem; font-weight:600; margin:0 0 4px; letter-spacing:.02em; }
  h2 { font-size:1rem; font-weight:600; margin: 40px 0 8px; }
  h3 { font-size:.95rem; font-weight:600; margin: 20px 0 6px; }
  p { margin: 0 0 12px; }
  ul { margin: 0 0 12px; padding-left: 1.25em; }
  li { margin: 0 0 6px; }
  a { color: inherit; text-decoration: underline; text-underline-offset: 3px; opacity:.85; }
  .lead { margin-bottom: 4px; }
  .en { opacity:.65; }
  .note { font-size:.85rem; opacity:.7; }
  code { font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
         font-size:.92em; background: var(--soft); border-radius: 6px; padding: 1px 5px; }
  pre { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size:.9rem; line-height:1.6;
        background: var(--soft); border-radius: 10px; padding: 16px; margin: 0 0 12px;
        white-space: pre-wrap; overflow-wrap: anywhere; }
  pre code { background: none; padding: 0; }
  footer { margin-top: 44px; font-size:.85rem; opacity:.7; }
"""


def build_index(en: dict[str, list[str]], ja: dict[str, list[str]]) -> str:
    """紹介ページ。**読む人に向けた製品紹介**で、開発側の申し送りを混ぜない。

    文案は masaru が Codex に書かせたもの（2026-09-14）。参考にした調子: サクラエディタの
    用途の端的な説明・CotEditor の機能を利用場面につなげる書き方・jq の気取らない案内。
    実行ログは載せない（現行の出力には投稿先や予約時刻の行も出るので、抜粋を「本物の出力」と
    しない）。使用例はコマンドだけ。
    """
    del en, ja
    esc = lambda s: html_mod.escape(s, quote=True)  # noqa: E731
    parts: list[str] = []
    add = parts.append
    guide = lambda name: f"{GITHUB_BLOB}docs/{name}"  # noqa: E731

    add("<!doctype html>")
    add('<html lang="ja"><head>')
    add('<meta charset="utf-8">')
    add('<meta name="viewport" content="width=device-width, initial-scale=1">')
    add('<meta name="referrer" content="no-referrer">')
    add("<title>THTH — AI と一緒に SNS の投稿を作成・管理するコマンドラインツール</title>")
    add(f'<meta name="description" content="{esc(DESCRIPTION)}">')
    add('<meta property="og:title" content="THTH">')
    add(f'<meta property="og:description" content="{esc(DESCRIPTION)}">')
    add(f"<style>\n{STYLE}</style>")
    add("</head><body><main>")

    add("<h1>THTH</h1>")
    add("<p>THTH は、AI と一緒に SNS の投稿を作成・管理するためのコマンドラインツールです。"
        "Threads、Bluesky、Mastodon に対応しています。</p>")
    add("<p>いつものエディタや AI エージェントで下書きを作り、内容を確認して承認すると、"
        "予約した時刻に投稿されます。原稿も投稿後の記録も、自分の Git リポジトリで管理できます。</p>")
    add("<pre><code>pip install thth</code></pre>")

    add("<h2>使い方</h2>")
    add("<p>下書きは Markdown ファイルに保存します。本文のほか、投稿先のアカウントや予約時刻、"
        "トピックなどを指定できます。</p>")
    add("<p>まず、投稿する内容を確認します。</p>")
    add("<pre><code>thth approve docs/sns/queue/2026-09-14-oolong.md</code></pre>")
    add("<p>本文と投稿先などが表示されます。この段階では、まだ承認されません。</p>")
    add("<p>内容がよければ、表示された確認コードと承認者の名前を付けて、もう一度実行します。</p>")
    add("<pre><code>thth approve docs/sns/queue/2026-09-14-oolong.md --confirm &lt;確認コード&gt; --by masaru</code></pre>")
    add("<p>承認した下書きは、タイマーを設定しておけば自動で投稿されます。承認後に本文や投稿先、"
        "予約時刻などを変更した場合は、あらためて承認が必要です。</p>")

    add("<h2>投稿したあとの管理も</h2>")
    add("<p>投稿が完了すると、原稿に投稿 ID が記録されます。返信や閲覧数も取得して、同じリポジトリに"
        "保存できます。投稿の履歴をたどったり、次の原稿を考えるときの資料として使えます。</p>")
    add("<p>招待した利用者は認可 URL を開いて承認し、masaru がアプリ・サーバ・台帳を管理します。"
        "利用者が VM や Meta アプリを用意する必要はありません。接続ページは今後の実装です。</p>")

    add("<h2>AI エージェントから使う</h2>")
    add("<p>MCP サーバと Claude Code 用のスキルを同梱しています。コマンドラインから直接使うほか、"
        "AI エージェントとやり取りしながら下書きや投稿の記録を扱えます。</p>")

    add("<h2>認可と運営者の設定</h2>")
    add("<p>Mastodon の auth は expires_in または refresh_token を返す非標準実装に未対応です。手動 token set も期限・更新情報を受け取らず期限なしとして保存するため、その回避策にはなりません。</p>")
    add("<p>masaru がサーバ側で <code>thth auth &lt;account&gt; --by masaru</code> を開始し、"
        "利用者が URL を開いて承認します。Threads・対応 Mastodon・X の共通入口です。"
        "X は認可だけ対応し、投稿・採集は未対応。Bluesky は App Password の stdin 入力です。</p>")
    add(f'<p><a href="{guide("導入_承認を押すだけ.md")}">導入: 承認を押すだけ（利用者と masaru の手順）</a></p>')

    add("<h2>English</h2>")
    add('<p class="en">THTH is a command-line tool for drafting and publishing social media posts with AI. '
        "It supports Threads, Bluesky, and Mastodon.</p>")
    add('<p class="en">Review and approve a draft, then let THTH publish it at the scheduled time. '
        "Drafts, published post IDs, replies, and view counts are stored in your own Git repository. "
        "Masaru operates the server and apps; invited users approve an authorization URL. X is authorization-only. A connection page is not implemented yet.</p>")

    add(f'<p><a href="{GITHUB}">GitHub</a> ／ <a href="{PYPI}">PyPI</a> ／ '
        f'<a href="{esc(REGISTRY_SEARCH)}">MCP Registry</a> ／ <a href="/llms.txt">llms.txt</a></p>')

    add('<footer>Free and open source · MIT License<br>© 2026 gotoq · <a href="/privacy/">プライバシーポリシー / Privacy Policy</a></footer>')
    add("</main></body></html>")
    return "\n".join(parts) + "\n"


# The operator sets the effective date when the relay and policy are deployed
# together. Generating a candidate must not invent a publication date.
PRIVACY_EFFECTIVE = "2026-09-23"  # 3.4.0 の実態どおりの書き直し（masaru「プライバシーデプロイして下さい」2026-09-23）


def build_privacy() -> str:
    """Factual policy (2026-09-23 rewrite for Meta Platform Terms §4(b)・§7(a)).

    Every sentence is checked against the code; see the commit message for the
    source of each fact. Nothing unimplemented is described and no new retention
    promise is made. English first (App Review reads it), Japanese says the same.
    """
    esc = lambda s: html_mod.escape(s, quote=True)  # noqa: E731
    parts: list[str] = []
    add = parts.append
    add("<!doctype html>")
    add('<html lang="en"><head>')
    add('<meta charset="utf-8">')
    add('<meta name="viewport" content="width=device-width, initial-scale=1">')
    add('<meta name="referrer" content="no-referrer">')
    add("<title>THTH — Privacy Policy / プライバシーポリシー</title>")
    add(f"<style>\n{STYLE}</style>")
    add("</head><body><main>")
    add("<h1>THTH — Privacy Policy</h1>")
    date_en = "Effective " + esc(PRIVACY_EFFECTIVE) if PRIVACY_EFFECTIVE else "Unpublished update — deployment date not set"
    add(f'<p class="note">{date_en} · <a href="#ja">日本語はこの下</a></p>')

    add("<h2>Who runs THTH</h2>")
    add("<p>THTH is open-source software for drafting, approving and publishing social media posts. "
        "The operator, gotoq, runs it on an operator-managed server (a virtual machine) for invited users "
        "and for the operator's own accounts. On that server THTH handles authorization, drafts, human "
        "approval, publishing and records.</p>")
    add("<p>Invited users do not run their own server or register their own Meta app. The operator starts "
        "authorization; the user reviews the account and permissions on the platform and approves, and later "
        "approves each post on a thth.me approval page. People who install the THTH software and run it "
        "themselves operate their own copy; this policy describes the operator's service.</p>")

    add("<h2>Platforms and the data THTH uses</h2>")
    add("<p>THTH works with Threads, Bluesky, Mastodon and X. For each connected account it uses:</p>")
    add("<ul>")
    add("  <li>Threads: the account's user ID and username; its posts and their metrics (views, likes, "
        "replies, reposts, quotes, shares); daily account metrics (views, likes, replies, reposts, quotes, "
        "followers, link clicks); replies to its posts; and mentions of the account.</li>")
    add("  <li>Bluesky: its posts and their metrics (likes, replies, reposts, quotes), and replies to its "
        "posts.</li>")
    add("  <li>Mastodon: its posts and their metrics (likes, replies, reposts), and replies to its posts.</li>")
    add("  <li>X: publishing approved posts, deleting a post on request, and reading the account's own recent "
        "posts. Replies and metrics are not collected.</li>")
    add("</ul>")
    add("<p><strong>Replies and mentions.</strong> Replies to the user's posts on Threads, Bluesky and Mastodon, "
        "and mentions of the user's Threads account, are saved to that account's records, including the other "
        "person's username, the text, the time and the link. THTH keeps them to manage the conversation: to "
        "show which replies have been answered and to prepare the user's replies, which go through the same "
        "human approval as posts. Mentions on Bluesky and Mastodon are shown on request and not saved.</p>")
    add("<p><strong>Keyword search.</strong> Search (Threads <code>threads_keyword_search</code>, and search on "
        "Bluesky and Mastodon) runs only on request: when the user's session asks for a search, or for the "
        "observation summary, which uses search terms the operator sets for the account. Results are shown "
        "for that request only: counts, the number of distinct authors, the share of the most frequent "
        "authors, the latest time, and for individual posts the author, a link and the first 60 characters. The text and "
        "authors of search results are not saved. Run logs record at most the search terms and the number "
        "of results.</p>")
    add("<p><strong>Profiles and places.</strong> Public profile lookup (Threads "
        "<code>threads_profile_discovery</code>) shows another account's public profile fields on request; "
        "the result is not saved. Location search (<code>threads_location_tagging</code>) shows candidate "
        "places; a place the user chooses is written in the draft and attached only to an approved post.</p>")
    add("<p>To count how often the same person appears without keeping names in those records, some records "
        "use a one-way key derived from the platform and the username.</p>")

    add("<h2>Attachments</h2>")
    add("<p>Images, video and audio attached to a draft are checked before use. For images, THTH removes "
        "location data and other embedded metadata (XMP, IPTC, MakerNote, thumbnails, comments) without "
        "re-encoding. Video and audio are not altered; a file that carries location metadata, or metadata "
        "THTH cannot inspect, is refused. The checked file is stored in the account's records.</p>")
    add("<p>Files uploaded by invited users, image previews on approval pages, and files handed to a platform "
        "pass through Cloudflare R2 on thth.me. Each is reachable only through an unguessable, short-lived "
        "capability URL: 10 minutes for uploads and for images handed to a platform, up to the approval page's lifetime for previews, "
        "30 minutes for video handed to a platform, and one hour after publication is confirmed. The Worker "
        "deletes each stored object 24 hours after it was created (image previews when they expire), retrying "
        "up to 10 times; the storage bucket is also set to delete these objects after 24 hours.</p>")

    add("<h2>Human approval pages</h2>")
    add("<p>An approval page temporarily holds the exact proposed text and display context for a logical "
        "session lifetime of at most 600 seconds. Approval or expiry removes that text and display context "
        "from the active record; minimal receipt bindings remain until expiry. A separate person record "
        "holds a salted PBKDF2-SHA256 verifier with 100,000 iterations, generation and failure count, "
        "not the approval secret itself. The secret is transmitted only when the person submits the form "
        "and is not retained. The URL holder can read the proposed text but cannot approve without the "
        "secret.</p>")

    add("<h2>Authorization relay</h2>")
    add("<p>The relay uses a Cloudflare Worker and Durable Object. It temporarily holds a registered "
        "authorization code for at most 300 seconds after receipt and within the 600-second session lifetime, "
        "and releases it once to the initiating server. Long-lived tokens, client secrets and PKCE verifiers "
        "are not sent to the relay. The application deletes the code when consumed or expired. Unregistered "
        "flows can use the existing return-URL paste fallback.</p>")

    add("<h2>Reports</h2>")
    add("<p>An invited user's session can file bug reports and requests. A report is stored in a private "
        "location on the operator's server with the account, project, title, text, reproduction steps and "
        "replies. A report containing something that looks like a secret (for example a token) is refused "
        "rather than stored. The change log records that a report was filed, answered or closed, without its "
        "text.</p>")

    add("<h2>What the operator's server keeps</h2>")
    add("<ul>")
    add("  <li>Long-lived credentials (platform tokens, Bluesky App Passwords) in owner-only files, used to "
        "authenticate platform API requests.</li>")
    add("  <li>Account settings, including an optional email address for operational notices.</li>")
    add("  <li>Drafts, approval records, the exact text of published posts, and post history.</li>")
    add("  <li>The metrics, replies and mentions described above, and checked attachments.</li>")
    add("  <li>Run logs (action, draft file, post ID, counts, status), reports, and a change log of "
        "administrative events.</li>")
    add("</ul>")
    add("<p>It does not keep the text or authors of keyword search results, looked-up public profiles, or "
        "the approval secret.</p>")

    add("<h2>Where data goes</h2>")
    add("<ul>")
    add("  <li>The platform API of each connected account.</li>")
    add("  <li>thth.me on Cloudflare (Workers, Durable Objects, R2): the authorization relay, approval pages, "
        "attachment transfer and data deletion requests.</li>")
    add("  <li>Operational notices by email through the operator's mail server, when configured: account "
        "name, draft file name, state, time and a reason. No post text.</li>")
    add("  <li>A liveness monitor, when configured: account name, run state, draft file name and a reason. "
        "No post text.</li>")
    add("  <li>Git: each account's records are kept in a Git repository. For invited users the server creates "
        "that repository and its remote on the server itself. The operator's own accounts may use a "
        "repository with an external remote, to which records are pushed.</li>")
    add("</ul>")
    add("<p>Pages on thth.me load no third-party scripts, fonts or analytics. Worker application observability "
        "logs are disabled; this does not guarantee that no infrastructure records exist. Rate limiting "
        "passes a hash of the requester's IP address to Cloudflare's rate limiter; THTH does not write IP "
        "addresses to its records.</p>")

    add("<h2>AI (LLM) sessions</h2>")
    add("<p>THTH itself does not send data to an AI model provider. Users work with THTH through their own "
        "LLM session (for example over MCP). What a tool returns to that session is passed to that session's "
        "LLM provider under the user's own arrangement with it. Depending on the tool, this includes the "
        "user's drafts, post history, metrics and reports, and the text or short previews (up to 60 "
        "characters) of public replies, mentions and search results, with links and author names or one-way "
        "author keys.</p>")

    add('<h2 id="delete">Stopping access and deleting data</h2>')
    add("<p>The operator ends an account's use with <code>thth account leave</code>. This stops the account on "
        "the server and on thth.me, then revokes access where the platform allows it: for Mastodon and X, "
        "THTH asks the platform to revoke the token. For Threads, remove the connection in your Threads "
        "settings; for Bluesky, delete the App Password in your Bluesky settings.</p>")
    add("<p>It then deletes the account's own credential files, its settings entry, its server state (run logs, "
        "post history, and the metrics, replies and mentions kept there) and its server-side repository, and "
        "records the exit (time, operator, deleted categories and counts) in the change log. Files shared "
        "with another account and external repositories are kept. Reports and the change log are not removed "
        "by this step.</p>")
    add("<p>Meta data deletion requests: <code>https://thth.me/data-deletion</code> accepts a signed request "
        "and returns a confirmation code with a status URL "
        "(<code>https://thth.me/data-deletion-status?code=…</code>). Receipt is automatic. The operator then "
        "has the server verify the signature and match the request to a Threads account, and runs the exit "
        "above; the status URL shows unverified, verified or completed. Requests are kept on thth.me for 30 "
        "days. The <code>/deauthorize</code> callback and the receipt of a request do not establish that data "
        "has been deleted. You can also contact the operator to stop access or delete data.</p>")

    add("<h2>Retention</h2>")
    add("<ul>")
    add("  <li>Authorization codes on the relay: at most 300 seconds after receipt.</li>")
    add("  <li>Approval pages and their image previews: at most 600 seconds.</li>")
    add("  <li>Attachment objects on R2: deleted 24 hours after creation (image previews when they expire).</li>")
    add("  <li>Data deletion requests on thth.me: 30 days.</li>")
    add("  <li>Sent operational notices in the server's notice record: 30 days (at most 50).</li>")
    add("  <li>Credentials, settings, drafts, post history, metrics, replies, mentions, attachments and run "
        "logs on the operator's server: until the account exits.</li>")
    add("  <li>Reports and the change log: no set period.</li>")
    add("</ul>")
    add("<p>Cloudflare SQLite Durable Object point-in-time recovery (PITR) retains recovery history for 30 days: "
        "logical deletion is not a promise of immediate physical erasure from backups. Removing application "
        "records does not establish physical erasure from the hosting provider's infrastructure or backups.</p>")

    add("<h2>Contact</h2>")
    add(f'<p>Questions about this policy: open an issue at <a href="{GITHUB}/issues">{esc(GITHUB)}/issues</a>. '
        "Operator: gotoq.</p>")
    add("<h2>Changes</h2>")
    add("<p>This page is generated from the THTH source repository; its history is public there. This revision "
        "describes the operator-run service, the data used per platform, replies and mentions, keyword "
        "search, attachments, reports, LLM sessions, exit and deletion requests, and retention, and corrects "
        "the PBKDF2 iteration count to 100,000.</p>")

    # ---------------------------------------------------------------- 日本語
    add('<h1 id="ja" style="margin-top:56px">THTH — プライバシーポリシー</h1>')
    date_ja = esc(PRIVACY_EFFECTIVE) + " 施行" if PRIVACY_EFFECTIVE else "未公開の更新案 — 配布日未設定"
    add(f'<p class="note">{date_ja}</p>')

    add("<h2>運営者と対象</h2>")
    add("<p>THTH は、SNS の投稿を下書き・承認・公開するためのオープンソースのソフトウェアです。運営者 gotoq が、"
        "運営者の管理するサーバ（仮想マシン）で、招待した利用者と運営者自身のアカウントのために動かしています。"
        "このサーバで、認可・下書き・人の承認・公開・記録を扱います。</p>")
    add("<p>招待された利用者は、自分のサーバを動かしたり自分の Meta アプリを登録したりしません。運営者が認可を"
        "開始し、利用者は媒体の画面でアカウントと権限を確かめて承認し、その後は投稿ごとに thth.me の承認ページで"
        "承認します。THTH を自分で導入して動かす人は、その複製を自分で運用しています。このポリシーは運営者の"
        "サービスについての説明です。</p>")

    add("<h2>媒体と使うデータ</h2>")
    add("<p>THTH は Threads・Bluesky・Mastodon・X に対応しています。接続したアカウントごとに次を使います。</p>")
    add("<ul>")
    add("  <li>Threads: アカウントのユーザー ID とユーザー名、投稿とその実測（表示・いいね・返信・再投稿・引用・"
        "シェア）、アカウントの日ごとの実測（表示・いいね・返信・再投稿・引用・フォロワー・リンクのクリック）、"
        "投稿への返信、アカウントへの言及。</li>")
    add("  <li>Bluesky: 投稿とその実測（いいね・返信・再投稿・引用）、投稿への返信。</li>")
    add("  <li>Mastodon: 投稿とその実測（いいね・返信・再投稿）、投稿への返信。</li>")
    add("  <li>X: 承認された投稿の公開、依頼による投稿の削除、アカウント自身の最近の投稿の読み取り。返信と"
        "実測は集めません。</li>")
    add("</ul>")
    add("<p><strong>返信と言及。</strong>Threads・Bluesky・Mastodon での本人の投稿への返信と、本人の Threads "
        "アカウントへの言及は、そのアカウントの記録に保存します。相手のユーザー名・本文・時刻・リンクを含みます。"
        "会話を管理するため（どの返信に答えたかを示し、本人の返信を用意するため）に保存し、本人の返信は投稿と"
        "同じ人の承認を通ります。Bluesky と Mastodon の言及は求められたときに表示するだけで、保存しません。</p>")
    add("<p><strong>キーワード検索。</strong>検索（Threads の <code>threads_keyword_search</code>、Bluesky と "
        "Mastodon の検索）は求められたときだけ動きます。本人のセッションが検索を求めたとき、または観測の一枚を"
        "求めたとき（運営者がアカウントに設定した語を使います）です。結果はその求めに対してだけ表示します。件数、"
        "投稿者の異なり数、多く投稿している人の占める割合、最新の時刻、個々の投稿については投稿者・リンク・先頭 60 字です。"
        "検索結果の本文と投稿者は保存しません。実行記録に残すのは、検索した語と件数までです。</p>")
    add("<p><strong>プロフィールと場所。</strong>公開プロフィールの参照（Threads の "
        "<code>threads_profile_discovery</code>）は、求められたときに相手の公開プロフィールの項目を表示し、"
        "結果は保存しません。場所の検索（<code>threads_location_tagging</code>）は候補を表示し、本人が選んだ場所を"
        "下書きに書き、承認された投稿にだけ付けます。</p>")
    add("<p>同じ人が何度現れたかを名前を残さずに数えるため、一部の記録では媒体とユーザー名から作った戻せない鍵を"
        "使います。</p>")

    add("<h2>添付</h2>")
    add("<p>下書きに付けた画像・動画・音声は、使う前に検査します。画像は、位置情報などの埋め込みメタデータ"
        "（XMP・IPTC・MakerNote・サムネイル・コメント）を、再圧縮せずに取り除きます。動画と音声は変えません。"
        "位置情報のメタデータを含むもの、THTH が検査できないメタデータを含むものは断ります。検査を通ったファイルは"
        "アカウントの記録に保存します。</p>")
    add("<p>招待された利用者がアップロードするファイル、承認ページの画像の表示、媒体へ渡すファイルは、thth.me の "
        "Cloudflare R2 を通ります。どれも推測できない短命の capability URL でしか読めません。アップロードと媒体へ渡す画像は"
        "10 分、表示は承認ページの期限まで、媒体へ渡す動画は 30 分、公開の確認後は 1 時間です。Worker は置いた"
        "ものを置いてから 24 時間で削除し（画像の表示は期限が来たとき）、最大 10 回まで試み直します。保管用の bucket "
        "も 24 時間で削除する設定です。</p>")

    add("<h2>本人が押す承認ページ</h2>")
    add("<p>承認ページは公開予定の本文そのものと表示情報を、最大600秒の論理的な session 期限内で一時的に保持します。"
        "承認または失効で稼働中 record から本文と表示情報を削除し、最小の受領照合情報だけを期限まで残します。"
        "本人の別 record には salt 付き PBKDF2-SHA256（100,000回）の verifier、世代、失敗回数を保存し、"
        "承認 secret 本体は保存しません。secret は本人のフォーム送信時だけ照合に使います。URL の所持者は本文を"
        "読めますが、secret 無しで承認はできません。</p>")

    add("<h2>認可の預かり所</h2>")
    add("<p>認可の預かり所は Cloudflare Worker と Durable Object を使います。登録済みの認可コードを、受付から"
        "最大300秒、かつ開始から最大600秒の範囲で一時的に保持し、認可を開始したサーバへ一度だけ渡します。"
        "長期トークン、client secret、PKCE verifier は預かり所へ送りません。消費または失効時にアプリケーション上の"
        "コードを削除します。未登録などの場合は従来の戻り URL を貼る経路になります。</p>")

    add("<h2>報告</h2>")
    add("<p>招待された利用者のセッションは、不具合と要望を報告できます。報告は運営者のサーバの私有の置き場に、"
        "アカウント・プロジェクト・題・本文・再現手順・返事とともに保存します。秘密らしき文字列（token など）を"
        "含む報告は、保存せずに断ります。変更ログには、報告が置かれた・返事した・閉じたことだけを、本文なしで"
        "残します。</p>")

    add("<h2>運営者のサーバに保存するもの</h2>")
    add("<ul>")
    add("  <li>長期の認証情報（媒体の token、Bluesky の App Password）。所有者だけが読めるファイルに置き、媒体の "
        "API への認証に使います。</li>")
    add("  <li>アカウントの設定。運用通知を受け取るメールアドレス（任意）を含みます。</li>")
    add("  <li>下書き、承認の記録、公開した本文そのもの、投稿の履歴。</li>")
    add("  <li>上に書いた実測・返信・言及、検査を通った添付。</li>")
    add("  <li>実行記録（操作・原稿のファイル・投稿 ID・件数・状態）、報告、管理の変更ログ。</li>")
    add("</ul>")
    add("<p>キーワード検索の結果の本文と投稿者、参照した公開プロフィール、承認 secret は保存しません。</p>")

    add("<h2>データの行き先</h2>")
    add("<ul>")
    add("  <li>接続したアカウントの媒体の API。</li>")
    add("  <li>Cloudflare 上の thth.me（Workers・Durable Objects・R2）: 認可の預かり所、承認ページ、添付の受け渡し、"
        "データ削除の依頼。</li>")
    add("  <li>運用通知のメール（設定したときだけ）。運営者のメールサーバから、アカウント名・原稿のファイル名・"
        "状態・時刻・理由を送ります。本文は含みません。</li>")
    add("  <li>死活監視（設定したときだけ）。アカウント名・実行の状態・原稿のファイル名・理由を送ります。本文は"
        "含みません。</li>")
    add("  <li>Git: アカウントの記録は Git のリポジトリに置きます。招待された利用者の分は、サーバがリポジトリと"
        "その送り先をサーバの中に作ります。運営者自身のアカウントは、外部に送り先を持つリポジトリを使うことがあり、"
        "記録はそこへ送られます。</li>")
    add("</ul>")
    add("<p>thth.me のページは第三者のスクリプト・フォント・解析ツールを読み込みません。Worker のアプリケーション"
        "観測ログは無効にしていますが、基盤全体に記録が存在しないとの保証はしません。流量制限では、アクセス元の "
        "IP アドレスのハッシュを Cloudflare の流量制限に渡します。THTH の記録に IP アドレスは書きません。</p>")

    add("<h2>AI（LLM）のセッション</h2>")
    add("<p>THTH 自身は AI モデルの提供者へデータを送りません。利用者は自分の LLM のセッション（例: MCP）から "
        "THTH を使います。道具がそのセッションに返したものは、利用者とその提供者との取り決めのもとで、そのセッションの "
        "LLM 提供者に渡ります。道具によって、本人の下書き・投稿の履歴・実測・報告と、公開の返信・言及・検索結果の"
        "本文または短い抜粋（先頭 60 字まで）、リンク、投稿者の名前または戻せない鍵を含みます。</p>")

    add("<h2>停止とデータの削除</h2>")
    add("<p>運営者は <code>thth account leave</code> でアカウントの利用を終えます。サーバと thth.me でアカウントを"
        "止め、媒体が許す範囲で権限を失効させます。Mastodon と X は、THTH が媒体に token の失効を求めます。Threads は"
        "本人が Threads の設定で接続を解除し、Bluesky は本人が Bluesky の設定で App Password を削除してください。</p>")
    add("<p>続けて、そのアカウント専用の認証情報のファイル、設定の項目、サーバの state（実行記録・投稿の履歴と、"
        "そこに置いた実測・返信・言及）、サーバ側のリポジトリを削除し、退出（時刻・実行者・削除した種類と件数）を"
        "変更ログに残します。他のアカウントと共有しているファイルと、外部のリポジトリは残します。報告と変更ログは"
        "この手順では消えません。</p>")
    add("<p>Meta からのデータ削除の依頼: <code>https://thth.me/data-deletion</code> が署名付きの依頼を受け付け、"
        "確認コードと状況の URL（<code>https://thth.me/data-deletion-status?code=…</code>）を返します。受付は自動です。"
        "その後、運営者がサーバで署名を検証して Threads のアカウントと照合し、上の退出を実行します。状況の URL は"
        "未照合・照合済み・完了を示します。依頼は thth.me に 30 日保持します。<code>/deauthorize</code> の応答と依頼の"
        "受付だけでは、データを削除したことを示しません。停止や削除は運営者へ連絡して求めることもできます。</p>")

    add("<h2>保持</h2>")
    add("<ul>")
    add("  <li>預かり所の認可コード: 受付から最大300秒。</li>")
    add("  <li>承認ページとその画像の表示: 最大600秒。</li>")
    add("  <li>R2 の添付: 置いてから24時間で削除（画像の表示は期限が来たとき）。</li>")
    add("  <li>thth.me のデータ削除の依頼: 30日。</li>")
    add("  <li>送った運用通知（サーバの通知の記録）: 30日（最大50件）。</li>")
    add("  <li>運営者のサーバの認証情報・設定・下書き・投稿の履歴・実測・返信・言及・添付・実行記録: 退出まで。</li>")
    add("  <li>報告と変更ログ: 期限を定めていません。</li>")
    add("</ul>")
    add("<p>Cloudflare SQLite Durable Object の PITR は30日間の復元履歴を持つため、論理削除をバックアップからの"
        "即時物理消去とは約束しません。アプリケーション上の記録の削除は、Cloudflare 内部のバックアップや物理消去まで"
        "保証する説明ではありません。</p>")

    add("<h2>連絡先</h2>")
    add(f'<p>このポリシーについての質問は <a href="{GITHUB}/issues">{esc(GITHUB)}/issues</a> へ。運営者: gotoq。</p>')
    add("<h2>変更</h2>")
    add("<p>このページは THTH のソースリポジトリから生成されており、変更の履歴はそこで公開されています。今回の改訂では、"
        "運営者が動かすサービスの形、媒体ごとに使うデータ、返信と言及、キーワード検索、添付、報告、LLM のセッション、"
        "退出と削除の依頼、保持を書き、PBKDF2 の回数を 100,000 に直しました。</p>")
    add('<footer><a href="/">THTH</a> · Free and open source · MIT License<br>© 2026 gotoq</footer>')
    add("</main></body></html>")
    return "\n".join(parts) + "\n"


ROBOTS = """\
# thth.me は製品の入口（設計 v2 §3）。紹介ページは索引してよい。
# 認可の受け口と Meta 用の口は索引しない（認可コードがクエリに乗る）。
User-agent: *
Allow: /
Disallow: /callback/
Disallow: /deauthorize
Disallow: /data-deletion
Disallow: /data-deletion-status
"""


def outputs() -> dict[str, str]:
    """出力パス（`callback/public/` 相対）→ 中身。"""
    en = sections_from(README_EN.read_text(encoding="utf-8"), EN_SECTIONS)
    ja = sections_from(SKILL.read_text(encoding="utf-8"), JA_SECTIONS)
    return {
        "index.html": build_index(en, ja),
        # **1 バイトも変えずに写す。** 正本は repo の llms.txt（手で二重管理しない）。
        "llms.txt": LLMS_TXT.read_text(encoding="utf-8"),
        "robots.txt": ROBOTS,
        "privacy/index.html": build_privacy(),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true",
                    help="書かずに、既にある出力と一致するかだけ見る")
    args = ap.parse_args(argv)

    built = outputs()
    if args.check:
        stale = []
        for name, content in built.items():
            path = PUBLIC / name
            if not path.exists():
                stale.append(f"{name}: 無い")
            elif path.read_text(encoding="utf-8") != content:
                stale.append(f"{name}: 正本とずれている")
        if stale:
            print("callback/public/ が古い（python tools/build_site.py を走らせる）:",
                  file=sys.stderr)
            for line in stale:
                print("  " + line, file=sys.stderr)
            return 1
        print("callback/public/ は正本と一致している。")
        return 0

    PUBLIC.mkdir(parents=True, exist_ok=True)
    for name, content in built.items():
        (PUBLIC / name).parent.mkdir(parents=True, exist_ok=True)
        (PUBLIC / name).write_text(content, encoding="utf-8")
        print(f"書いた: callback/public/{name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
