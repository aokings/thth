#!/usr/bin/env python3
"""`thth.me` の紹介ページ（`callback/public/`）を **repo の正本から生成する**。

設計 v2 §3「ドメイン」: `thth.me` ＝製品の入口（README 相当・導入・`llms.txt`・
認可ページは残す）。その入口に置く 5 つのファイルをここで作る:

| 出力 | 正本 |
|---|---|
| `callback/public/index.html` | このスクリプトの `build_index()` に置く固定の製品紹介文 |
| `callback/public/llms.txt` | repo の `llms.txt`（**1 バイトも変えずに写す**） |
| `callback/public/robots.txt` | このスクリプト（認可の受け口だけを索引から外す） |
| `callback/public/privacy/index.html` | このスクリプト（`build_privacy()`。Meta の App Review が要求するプライバシーポリシー。2026-09-15） |
| `callback/public/terms/index.html` | このスクリプト（`build_terms()`。外の利用者向けの利用規約。2026-09-25） |

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

    add('<footer>Free and open source · MIT License<br>© 2026 gotoq · <a href="/privacy/">プライバシーポリシー / Privacy Policy</a>'
        ' · <a href="/terms/">利用規約 / Terms of Service</a></footer>')
    add("</main></body></html>")
    return "\n".join(parts) + "\n"


# The operator sets the effective date when the relay and policy are deployed
# together. Generating a candidate must not invent a publication date.
PRIVACY_EFFECTIVE = None  # 3.14.0（命令の道 /api/v1）の施行日は deploy のとき主セッションが入れる。前: 2026-09-26（3.13.0）


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
    add(f'<p class="note">{date_en} · <a href="#ja">日本語はこの下</a> · '
        '<a href="/terms/">Terms of Service</a></p>')

    add("<h2>Who runs THTH</h2>")
    add("<p>THTH is an open-source command-line tool for publishing and managing posts on your own social "
        "media accounts; people usually drive it through their own AI assistant. The operator, gotoq, runs it "
        "on an operator-managed server (a virtual machine) for invited users and for the operator's own "
        "accounts. On that server THTH handles authorization, drafts, publishing, safety limits and records.</p>")
    add("<p>Invited users do not run their own server or register their own Meta app. Authorization starts "
        "in one of two ways: an invited user opens the invitation link the operator sent and presses the button "
        "on that page, or the operator starts it from the server with <code>thth auth</code>. Either way, the "
        "user reviews the account and permissions on the platform and approves. Afterwards, posts, replies and "
        "deletions happen only when the user asks for them, through their own CLI or AI assistant; THTH does "
        "nothing on its own. People who install the THTH software and run it themselves operate their own "
        "copy; this policy describes the operator's service.</p>")

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
        "show which replies have been answered and to prepare the user's replies, which are sent the same way "
        "as posts. Mentions on Bluesky and Mastodon are shown on request and not saved.</p>")
    add("<p><strong>Keyword search.</strong> Search (Threads <code>threads_keyword_search</code>, and search on "
        "Bluesky and Mastodon) runs on request: when the user's session asks for a search, or for the "
        "observation summary, which uses search terms the operator sets for the account. The only other search "
        "is the observation map below, when the operator turns it on. Results of a search on request are shown "
        "for that request only: counts, the number of distinct authors, the share of the most frequent "
        "authors, the latest time, and for individual posts the author, a link and the first 60 characters. The text and "
        "authors of search results are not saved. Run logs record at most the search terms and the number "
        "of results.</p>")
    add("<p><strong>Observation map (daily keyword aggregates).</strong> It is off unless the operator turns it "
        "on. Its keywords are topic words that a person registers for a project: the operator registers them, "
        "at most 20 per project. THTH never adds keywords taken from other people's posts, and refuses @handles, "
        "URLs and words that look like personal names. When it is on, THTH searches each keyword at most once a "
        "day on Threads, Bluesky and Mastodon, using the project's own connected account on that platform (the "
        "25 most recent results, of which posts from the last 24 hours are counted). It counts the results in "
        "memory and saves only daily figures per keyword and platform: the number of posts, the number of "
        "distinct authors, the share of the three most frequent authors, the number of results with an author "
        "name, the time since the latest post rounded to one of four ranges (under 1 hour, 1–6 hours, 6–24 "
        "hours, 24 hours or more), and, for each pair of keywords, how many of one keyword's results also "
        "contain the other. A number below 5 (posts, distinct authors or a pair count) is saved as missing "
        "instead of its value. Post text, post IDs, usernames, author keys, links and exact times are not "
        "saved.</p>")
    add("<p>These figures are kept in a private location on the operator's server, outside Git, for at most "
        "180 days: each collection run deletes older days one day at a time, and days past 180 days are not "
        "shown even before they are deleted. When the project's last account exits, its whole map is deleted; "
        "when other accounts in the project remain, the figures for the leaving account's platform are deleted. "
        "On request, the operator deletes the figures for the whole project or for a date range. The figures "
        "are shown only within that project: through <code>thth map show</code>, the map line of the "
        "observation summary, and the same tools over MCP, to sessions whose credential covers that project. "
        "They are not shown to other owners, on the shared plaza, or in totals across projects. When the "
        "user's LLM session calls these tools, the keywords and figures are passed to that session's LLM "
        "provider.</p>")
    add("<p><strong>Profiles and places.</strong> Public profile lookup (Threads "
        "<code>threads_profile_discovery</code>) shows another account's public profile fields on request; "
        "the result is not saved. Location search (<code>threads_location_tagging</code>) shows candidate "
        "places; a place the user chooses is written in the draft and attached only to that post.</p>")
    add("<p>To count how often the same person appears without keeping names in those records, some records "
        "use a one-way key derived from the platform and the username.</p>")

    add("<h2>Attachments</h2>")
    add("<p>Images, video and audio attached to a draft are checked before use. For images, THTH removes "
        "location data and other embedded metadata (XMP, IPTC, MakerNote, thumbnails, comments) without "
        "re-encoding. Video and audio are not altered; a file that carries location metadata, or metadata "
        "THTH cannot inspect, is refused. The checked file is stored in the account's records.</p>")
    add("<p>Files uploaded by invited users and files handed to a platform pass through Cloudflare R2 on "
        "thth.me. Each is reachable only through an unguessable, short-lived capability URL: 10 minutes for "
        "uploads and for images handed to a platform, 30 minutes for video handed to a platform, and one hour "
        "after publication is confirmed. The Worker deletes each stored object 24 hours after it was created, "
        "retrying up to 10 times; the storage bucket is also set to delete these objects after 24 hours.</p>")

    add("<h2>Safety limits and the owner's activity page</h2>")
    add("<p>Every account has safety limits that its owner controls: a minimum interval between posts, daily "
        "caps on posts and on deletions, quiet hours, an optional hold before an immediate post goes out, and an "
        "automatic stop when too many posts and deletions happen within a few minutes. The server enforces them "
        "from the account's own records and keeps a stop record (reason and time) on the operator's server. An "
        "AI assistant can only tighten these limits.</p>")
    add("<p>The owner's activity page (<code>https://thth.me/activity</code>) is opened with the account's "
        "username and account secret. A person record on thth.me holds a salted PBKDF2-SHA256 verifier with "
        "100,000 iterations, generation and failure count, not the secret itself; the secret is transmitted only "
        "when the person submits a form and is not retained. Signing in sets a cookie limited to "
        "<code>/activity</code> that lasts 10 minutes. The page shows a summary that the server pushes every 10 "
        "seconds: what was published, deleted, scheduled or held (the first 60 characters of each text and the "
        "time), whether the account is stopped and why, today's counts against the limits, and the identifier "
        "and expiry of the assistant key. thth.me forgets the summary one hour after the last push. From the "
        "page the owner can stop the account, resume it, cancel a scheduled post, change the safety limits, and "
        "issue or revoke the assistant key; each of these actions is confirmed with the secret, carried out by "
        "the server within seconds, and kept on thth.me for at most one hour. THTH does not record who views "
        "the page; the Worker only applies a rate limit.</p>")
    add("<p><strong>Assistant key.</strong> The key an owner gives to their own CLI or AI assistant is generated "
        "on thth.me, shown once to the owner, and never stored in clear: thth.me and the operator's server keep "
        "only its SHA-256 hash, its scope (that account) and its expiry (365 days). Issuing a new key revokes "
        "the previous one. Every post, deletion and schedule made with a key is recorded with the key's "
        "identifier.</p>")
    add("<p><strong>The command path (thth.me/api/v1).</strong> The <code>thth</code> command on the owner's own "
        "computer, and an AI assistant that runs it, act on the account through <code>https://thth.me/api/v1</code> "
        "with the assistant key. thth.me checks the key's hash and rate limits (60 requests per minute per key, 120 "
        "per minute per address), holds the request for the operator's server, which fetches it and carries it out, "
        "and returns the result. thth.me keeps a request for at most 120 seconds and its result for at most 10 "
        "minutes, does not log request or result contents, and holds only the hash, account and expiry of each "
        "key. The operator's server verifies the key again before acting.</p>")

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

    add('<h2 id="plaza">Plaza</h2>')
    add("<p>The plaza (<code>thth plaza</code>, and the same tools over MCP) lets a user's sessions post "
        "measures they tried, findings and questions, with numbers, and reply to each other. Posts and replies "
        "are stored in a private location on the operator's server (owner-only files), not in the account's "
        "Git repository. A post that looks like it contains a secret is refused. Numbers shown as observations "
        "are computed by THTH from the account's own posts and metrics.</p>")
    add("<p>Who can read a post is chosen per post: <strong>project</strong> (the accounts of the same "
        "project), <strong>owner</strong> (the projects of an owner group that the operator registers; they "
        "read the original) or <strong>open</strong> (other owners who have joined the plaza). The default is "
        "project, or owner when the operator has registered the project in an owner group; open is never the "
        "default. Joining the open plaza is per project and off by default, and an open post is visible only "
        "when both the posting and the reading project have joined. The operator can read every post.</p>")
    add("<p>Making a post open is a two-step confirmation from the command line only: the first step shows "
        "exactly what other owners will see, and the post is placed only when the same content is confirmed. "
        "Other owners read a copy from which THTH has removed other people's information: text of other "
        "people's replies found in the account's records, their usernames, @handles other than the author's "
        "own, one-way author keys, and links to other people's social media posts. For the author's own posts "
        "the copy shows the first 60 characters and the link. Other owners see the project name, not the "
        "account name or the person who posted. If THTH cannot read the account's records, it does not make "
        "the copy.</p>")
    add("<p>The operator can hide a post with a reason (its author still sees it) and can remove a project "
        "from the open plaza. THTH also records which plaza posts a session has read (the post ID and time "
        "only). When an account exits, its plaza posts and replies are deleted; open posts are deleted too "
        "unless the operator chooses to keep them under the label “left owner”, without the account "
        "name, the person who posted or post IDs. The change log records that a post was placed, replied to, "
        "updated or hidden, without its text.</p>")

    add("<h2>What the operator's server keeps</h2>")
    add("<ul>")
    add("  <li>Long-lived credentials (platform tokens, Bluesky App Passwords) in owner-only files, used to "
        "authenticate platform API requests.</li>")
    add("  <li>Account settings, including an optional email address for operational notices.</li>")
    add("  <li>Drafts, the exact text of published posts, post history, and stop records of the safety limits.</li>")
    add("  <li>The metrics, replies and mentions described above, and checked attachments.</li>")
    add("  <li>The observation map: its keywords, the links between keywords that a person drew, and, when "
        "turned on, its daily aggregate figures described above.</li>")
    add("  <li>Plaza posts and replies, and which plaza posts were read (post ID and time).</li>")
    add("  <li>Run logs (action, draft file, post ID, counts, status), reports, and a change log of "
        "administrative events.</li>")
    add("</ul>")
    add("<p>It does not keep the text or authors of keyword search results, looked-up public profiles, the "
        "account secret, or the assistant key in clear.</p>")

    add("<h2>Where data goes</h2>")
    add("<ul>")
    add("  <li>The platform API of each connected account.</li>")
    add("  <li>thth.me on Cloudflare (Workers, Durable Objects, R2): the authorization relay, the owner's "
        "activity page, attachment transfer and data deletion requests.</li>")
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
        "user's drafts, post history, metrics and reports, plaza posts and replies the session can read, the observation map's keywords and daily figures, and the text or short previews (up to 60 "
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
        "with another account and external repositories are kept. Its plaza posts and replies are deleted as "
        'described under <a href="#plaza">Plaza</a>. Reports and the change log are not removed by this step.</p>')
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
    add("  <li>Activity summaries and requested actions on thth.me: at most one hour.</li>")
    add("  <li>Command requests on thth.me/api/v1: at most 120 seconds; their results: at most 10 minutes.</li>")
    add("  <li>Sign-in to the activity page: 10 minutes.</li>")
    add("  <li>Attachment objects on R2: deleted 24 hours after creation.</li>")
    add("  <li>Data deletion requests on thth.me: 30 days.</li>")
    add("  <li>Sent operational notices in the server's notice record: 30 days (at most 50).</li>")
    add("  <li>Observation map daily figures on the operator's server: at most 180 days, deleted one day at a "
        "time; also deleted on exit or on request.</li>")
    add("  <li>Credentials, settings, drafts, post history, metrics, replies, mentions, attachments and run "
        "logs on the operator's server: until the account exits.</li>")
    add("  <li>Plaza posts and replies: until the account exits (open posts may be kept without the account "
        "name, as described under Plaza).</li>")
    add("  <li>Reports and the change log: no set period.</li>")
    add("</ul>")
    add("<p>Cloudflare SQLite Durable Object point-in-time recovery (PITR) retains recovery history for 30 days: "
        "logical deletion is not a promise of immediate physical erasure from backups. Removing application "
        "records does not establish physical erasure from the hosting provider's infrastructure or backups.</p>")

    add("<h2>Contact</h2>")
    add(contact_html("en"))
    add('<p>The <a href="/terms/">Terms of Service</a> give the same contact.</p>')
    add("<h2>Changes</h2>")
    add("<p>This page is generated from the THTH source repository; its history is public there. This revision "
        "describes the operator-run service, the data used per platform, replies and mentions, keyword "
        "search, attachments, reports, LLM sessions, exit and deletion requests, and retention, and corrects "
        "the PBKDF2 iteration count to 100,000. Revision: adds the saved daily aggregates of the observation "
        "map (keyword search). Revision: adds the plaza and the support contact, and links to the Terms of "
        "Service. Revision: removes the approval pages; describes the safety limits, the owner's activity page and "
        "the assistant key. Revision: adds the command path (thth.me/api/v1).</p>")

    # ---------------------------------------------------------------- 日本語
    add('<h1 id="ja" style="margin-top:56px">THTH — プライバシーポリシー</h1>')
    date_ja = esc(PRIVACY_EFFECTIVE) + " 施行" if PRIVACY_EFFECTIVE else "未公開の更新案 — 配布日未設定"
    add(f'<p class="note">{date_ja} · <a href="/terms/#ja">利用規約</a></p>')

    add("<h2>運営者と対象</h2>")
    add("<p>THTH は、自分の SNS のアカウントの投稿を公開・管理するためのオープンソースのコマンドラインツールです。"
        "多くの人は自分の AI アシスタントを通して使います。運営者 gotoq が、運営者の管理するサーバ（仮想マシン）で、"
        "招待した利用者と運営者自身のアカウントのために動かしています。このサーバで、認可・下書き・公開・安全装置・"
        "記録を扱います。</p>")
    add("<p>招待された利用者は、自分のサーバを動かしたり自分の Meta アプリを登録したりしません。認可の始まり方は"
        "2 つです。招待された利用者が運営者から届いた招待リンクを開いてそのページのボタンを押すか、運営者がサーバで"
        "<code>thth auth</code> から始めます。どちらでも、利用者は媒体の画面でアカウントと権限を確かめて承認します。"
        "その後の投稿・返信・削除は、利用者が自分の CLI か AI アシスタントで頼んだときだけ行われ、THTH が自分から"
        "動くことはありません。THTH を自分で導入して動かす人は、その複製を自分で"
        "運用しています。このポリシーは運営者のサービスについての説明です。</p>")

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
        "同じ道で出します。Bluesky と Mastodon の言及は求められたときに表示するだけで、保存しません。</p>")
    add("<p><strong>キーワード検索。</strong>検索（Threads の <code>threads_keyword_search</code>、Bluesky と "
        "Mastodon の検索）は求められたときに動きます。本人のセッションが検索を求めたとき、または観測の一枚を"
        "求めたとき（運営者がアカウントに設定した語を使います）です。これ以外の検索は、運営者が有効にしたときの"
        "下の観測の地図だけです。求めに応じた検索の結果は、その求めに対してだけ表示します。件数、"
        "投稿者の異なり数、多く投稿している人の占める割合、最新の時刻、個々の投稿については投稿者・リンク・先頭 60 字です。"
        "検索結果の本文と投稿者は保存しません。実行記録に残すのは、検索した語と件数までです。</p>")
    add("<p><strong>観測の地図（語ごとの日々の集計）。</strong>運営者が有効にしない限り動きません。語は、人が"
        "プロジェクトごとに登録する話題の語です。登録するのは運営者で、プロジェクトごとに最大 20 語です。THTH が"
        "他人の投稿から語を拾って足すことはなく、@名前・URL・個人名らしき語は登録できません。有効なとき、THTH は "
        "Threads・Bluesky・Mastodon で、そのプロジェクトがその媒体に接続したアカウントを使い、語ごとに 1 日 1 回まで"
        "検索します（新しい順の先頭 25 件のうち、直近 24 時間の投稿を数えます）。結果はメモリの中で数え、語と媒体"
        "ごとに日々の数だけを保存します。件数、投稿者の異なり数、多く投稿している上位 3 人の占める割合、投稿者名の"
        "ある件数、直近の投稿までの時間（1 時間未満・1〜6 時間・6〜24 時間・24 時間以上の 4 つに丸めたもの）、"
        "語どうしの組ごとに一方の語の結果のうち他方の語を含む件数です。5 未満の数（件数・投稿者の異なり数・組の"
        "件数）は値を保存せず、欠測として記録します。本文・投稿 ID・ユーザー名・投稿者の鍵・リンク・正確な時刻は"
        "保存しません。</p>")
    add("<p>この数は運営者のサーバの私有の置き場（Git の外）に最大 180 日保存します。集計が走るたびに古い日を"
        "日単位で削り、180 日を過ぎた日は削る前でも表示しません。プロジェクトの最後のアカウントが退出すると地図を"
        "丸ごと削除し、ほかのアカウントが残るときは、退出するアカウントの媒体の数を削除します。依頼があれば、運営者"
        "がプロジェクト全体か期間を指定して削除します。数を見られるのはそのプロジェクトの中だけです（"
        "<code>thth map show</code>、観測の一枚の地図の行、MCP の同じ道具。そのプロジェクトを許された credential "
        "のセッションだけ）。ほかの持ち主、広場、プロジェクトをまたぐ集計には出しません。本人の LLM セッションが"
        "これらの道具を呼ぶと、語と数はそのセッションの LLM 提供者に渡ります。</p>")
    add("<p><strong>プロフィールと場所。</strong>公開プロフィールの参照（Threads の "
        "<code>threads_profile_discovery</code>）は、求められたときに相手の公開プロフィールの項目を表示し、"
        "結果は保存しません。場所の検索（<code>threads_location_tagging</code>）は候補を表示し、本人が選んだ場所を"
        "下書きに書き、その投稿にだけ付けます。</p>")
    add("<p>同じ人が何度現れたかを名前を残さずに数えるため、一部の記録では媒体とユーザー名から作った戻せない鍵を"
        "使います。</p>")

    add("<h2>添付</h2>")
    add("<p>下書きに付けた画像・動画・音声は、使う前に検査します。画像は、位置情報などの埋め込みメタデータ"
        "（XMP・IPTC・MakerNote・サムネイル・コメント）を、再圧縮せずに取り除きます。動画と音声は変えません。"
        "位置情報のメタデータを含むもの、THTH が検査できないメタデータを含むものは断ります。検査を通ったファイルは"
        "アカウントの記録に保存します。</p>")
    add("<p>招待された利用者がアップロードするファイルと媒体へ渡すファイルは、thth.me の Cloudflare R2 を通ります。"
        "どれも推測できない短命の capability URL でしか読めません。アップロードと媒体へ渡す画像は 10 分、媒体へ渡す"
        "動画は 30 分、公開の確認後は 1 時間です。Worker は置いたものを置いてから 24 時間で削除し、最大 10 回まで"
        "試み直します。保管用の bucket も 24 時間で削除する設定です。</p>")

    add("<h2>安全装置と持ち主の動きの一覧</h2>")
    add("<p>どのアカウントにも、持ち主が決める安全装置があります。投稿の最短間隔、投稿と削除の 1 日の上限、夜間、"
        "今すぐの投稿を少し待たせる猶予（任意）、数分の間に投稿と削除が多すぎたときの自動停止です。サーバがアカウント"
        "自身の記録から数えて守り、止めたときはその印（理由と時刻）を運営者のサーバに置きます。AI アシスタントは"
        "この数値を締める向きにしか変えられません。</p>")
    add("<p>持ち主の動きの一覧（<code>https://thth.me/activity</code>）には、アカウントのユーザ名と口座の secret で"
        "入ります。thth.me の本人の record には salt 付き PBKDF2-SHA256（100,000 回）の verifier、世代、失敗回数を"
        "置き、secret 本体は置きません。secret は本人のフォーム送信時だけ照合に使い、保持しません。入ると "
        "<code>/activity</code> に限った 10 分の cookie を置きます。一覧に出るのは、サーバが 10 秒ごとに押し上げる"
        "要約です。出したもの・消したもの・予約・猶予中のもの（本文の先頭 60 字と時刻）、止まっているかとその理由、"
        "今日の上限に対する数、アシスタントの鍵の識別子と期限です。thth.me は最後の押し上げから 1 時間で要約を"
        "忘れます。この画面から、持ち主はアカウントを止める・戻す・予約を取り消す・安全装置の数値を変える・"
        "アシスタントの鍵を発行し直す・取り消すことができます。どの操作も secret で確かめ、サーバが数十秒で行い、"
        "thth.me には最大 1 時間だけ残ります。THTH は誰が一覧を見たかを記録しません（Worker は流量の上限だけを"
        "見ます）。</p>")
    add("<p><strong>アシスタントの鍵。</strong>持ち主が自分の CLI や AI アシスタントに渡す鍵は thth.me が作り、持ち主に"
        "1 度だけ表示し、平文では保存しません。thth.me と運営者のサーバが持つのは SHA-256 のハッシュ、範囲（その"
        "アカウント）、期限（365 日）だけです。新しい鍵を発行すると前の鍵は使えなくなります。鍵で行った投稿・削除・"
        "予約には、鍵の識別子を記録します。</p>")
    add("<p><strong>命令の道（thth.me/api/v1）。</strong>持ち主の機械の <code>thth</code> 命令と、それを打つ AI アシスタント"
        "は、アシスタントの鍵を使って <code>https://thth.me/api/v1</code> からアカウントを動かします。thth.me は鍵のハッシュと"
        "回数（鍵ごとに 1 分 60 回・接続元ごとに 1 分 120 回）を確かめ、依頼を運営者のサーバのために預かり、サーバが取りに来て"
        "行い、結果を返します。thth.me が依頼を持つのは最長 120 秒、結果は最長 10 分で、依頼と結果の中身を記録せず、鍵については"
        "ハッシュ・アカウント・期限だけを持ちます。運営者のサーバは行う前に鍵をもう一度確かめます。</p>")

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

    add("<h2>広場</h2>")
    add("<p>広場（<code>thth plaza</code>、MCP の同じ道具）では、利用者のセッションが、試した施策・気づき・問いを"
        "数字つきで置き、互いに返信できます。書き込みと返信は運営者のサーバの私有の置き場（所有者だけが読める"
        "ファイル）に保存し、アカウントの Git のリポジトリには書きません。秘密らしき文字列を含む書き込みは断ります。"
        "観測として出す数字は、THTH がそのアカウント自身の投稿と実測から計算します。</p>")
    add("<p>見える範囲は 1 件ごとに選びます。<strong>project</strong>（同じプロジェクトのアカウント）、"
        "<strong>owner</strong>（運営者が登録した持ち主の組のプロジェクト。原本を読みます）、<strong>open</strong>"
        "（広場に参加した他の持ち主）です。既定は project で、運営者がプロジェクトを持ち主の組に登録していれば "
        "owner です。open が既定になることはありません。open への参加はプロジェクトごとで、既定は不参加です。open の"
        "書き込みは、置いたプロジェクトと読むプロジェクトの両方が参加しているときだけ見えます。運営者はすべての"
        "書き込みを読めます。</p>")
    add("<p>open にするのはコマンドラインからの二段確認だけです。一段目で他の持ち主に見える姿をそのまま示し、"
        "同じ中身を確かめたときに初めて置きます。他の持ち主が読むのは、THTH が他人の情報を落とした写しです。"
        "落とすのは、アカウントの記録にある他人の返信の本文、その人たちのユーザー名、書いた本人以外の @名前、"
        "戻せない投稿者の鍵、他人の SNS の投稿へのリンクです。本人の投稿は先頭 60 字とリンクまでです。他の持ち主に"
        "見えるのはプロジェクト名で、アカウント名や書いた人は見えません。アカウントの記録が読めなければ写しを"
        "作りません。</p>")
    add("<p>運営者は理由を付けて書き込みを非表示にでき（書いた本人には見えます）、プロジェクトを open の広場から"
        "外せます。THTH は、セッションがどの書き込みを読んだか（書き込みの ID と時刻だけ）も記録します。"
        "アカウントが退出すると、その書き込みと返信を削除します。open の書き込みも削除しますが、運営者が"
        "「退出した持ち主」の名義で残すと選んだときは、アカウント名・書いた人・投稿 ID を落として残します。"
        "変更ログには、書き込みを置いた・返信した・更新した・非表示にしたことだけを、本文なしで残します。</p>")

    add("<h2>運営者のサーバに保存するもの</h2>")
    add("<ul>")
    add("  <li>長期の認証情報（媒体の token、Bluesky の App Password）。所有者だけが読めるファイルに置き、媒体の "
        "API への認証に使います。</li>")
    add("  <li>アカウントの設定。運用通知を受け取るメールアドレス（任意）を含みます。</li>")
    add("  <li>下書き、公開した本文そのもの、投稿の履歴、安全装置で止めた印。</li>")
    add("  <li>上に書いた実測・返信・言及、検査を通った添付。</li>")
    add("  <li>観測の地図: 語、人が引いた語どうしの線、有効なときは上に書いた日々の集計。</li>")
    add("  <li>広場の書き込みと返信、どの書き込みを読んだか（書き込みの ID と時刻）。</li>")
    add("  <li>実行記録（操作・原稿のファイル・投稿 ID・件数・状態）、報告、管理の変更ログ。</li>")
    add("</ul>")
    add("<p>キーワード検索の結果の本文と投稿者、参照した公開プロフィール、口座の secret、アシスタントの鍵の平文は"
        "保存しません。</p>")

    add("<h2>データの行き先</h2>")
    add("<ul>")
    add("  <li>接続したアカウントの媒体の API。</li>")
    add("  <li>Cloudflare 上の thth.me（Workers・Durable Objects・R2）: 認可の預かり所、持ち主の動きの一覧、添付の"
        "受け渡し、データ削除の依頼。</li>")
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
        "LLM 提供者に渡ります。道具によって、本人の下書き・投稿の履歴・実測・報告、そのセッションが読める広場の書き込みと返信、観測の地図の語と日々の集計と、公開の返信・言及・検索結果の"
        "本文または短い抜粋（先頭 60 字まで）、リンク、投稿者の名前または戻せない鍵を含みます。</p>")

    add("<h2>停止とデータの削除</h2>")
    add("<p>運営者は <code>thth account leave</code> でアカウントの利用を終えます。サーバと thth.me でアカウントを"
        "止め、媒体が許す範囲で権限を失効させます。Mastodon と X は、THTH が媒体に token の失効を求めます。Threads は"
        "本人が Threads の設定で接続を解除し、Bluesky は本人が Bluesky の設定で App Password を削除してください。</p>")
    add("<p>続けて、そのアカウント専用の認証情報のファイル、設定の項目、サーバの state（実行記録・投稿の履歴と、"
        "そこに置いた実測・返信・言及）、サーバ側のリポジトリを削除し、退出（時刻・実行者・削除した種類と件数）を"
        "変更ログに残します。他のアカウントと共有しているファイルと、外部のリポジトリは残します。広場の書き込みと"
        "返信は上の「広場」のとおり削除します。報告と変更ログはこの手順では消えません。</p>")
    add("<p>Meta からのデータ削除の依頼: <code>https://thth.me/data-deletion</code> が署名付きの依頼を受け付け、"
        "確認コードと状況の URL（<code>https://thth.me/data-deletion-status?code=…</code>）を返します。受付は自動です。"
        "その後、運営者がサーバで署名を検証して Threads のアカウントと照合し、上の退出を実行します。状況の URL は"
        "未照合・照合済み・完了を示します。依頼は thth.me に 30 日保持します。<code>/deauthorize</code> の応答と依頼の"
        "受付だけでは、データを削除したことを示しません。停止や削除は運営者へ連絡して求めることもできます。</p>")

    add("<h2>保持</h2>")
    add("<ul>")
    add("  <li>預かり所の認可コード: 受付から最大300秒。</li>")
    add("  <li>thth.me の動きの一覧の要約と頼んだ操作: 最大 1 時間。</li>")
    add("  <li>thth.me/api/v1 の依頼: 最長 120 秒。その結果: 最長 10 分。</li>")
    add("  <li>動きの一覧に入った状態: 10 分。</li>")
    add("  <li>R2 の添付: 置いてから 24 時間で削除。</li>")
    add("  <li>thth.me のデータ削除の依頼: 30日。</li>")
    add("  <li>送った運用通知（サーバの通知の記録）: 30日（最大50件）。</li>")
    add("  <li>運営者のサーバの観測の地図の日々の集計: 最大180日（日単位で削除）。退出と依頼でも削除。</li>")
    add("  <li>運営者のサーバの認証情報・設定・下書き・投稿の履歴・実測・返信・言及・添付・実行記録: 退出まで。</li>")
    add("  <li>広場の書き込みと返信: 退出まで（open の書き込みは「広場」のとおりアカウント名を落として残すことがあります）。</li>")
    add("  <li>報告と変更ログ: 期限を定めていません。</li>")
    add("</ul>")
    add("<p>Cloudflare SQLite Durable Object の PITR は30日間の復元履歴を持つため、論理削除をバックアップからの"
        "即時物理消去とは約束しません。アプリケーション上の記録の削除は、Cloudflare 内部のバックアップや物理消去まで"
        "保証する説明ではありません。</p>")

    add("<h2>連絡先</h2>")
    add(contact_html("ja"))
    add('<p><a href="/terms/#ja">利用規約</a>にも同じ連絡先を書いています。</p>')
    add("<h2>変更</h2>")
    add("<p>このページは THTH のソースリポジトリから生成されており、変更の履歴はそこで公開されています。今回の改訂では、"
        "運営者が動かすサービスの形、媒体ごとに使うデータ、返信と言及、キーワード検索、添付、報告、LLM のセッション、"
        "退出と削除の依頼、保持を書き、PBKDF2 の回数を 100,000 に直しました。改訂: 観測の地図の集計の保存（キーワード検索・世間の層）を追記しました。"
        "改訂: 広場とサポートの連絡先を書き、利用規約へのリンクを足しました。改訂: 承認ページを無くし、安全装置・"
        "持ち主の動きの一覧・アシスタントの鍵を書きました。改訂: 命令の道（thth.me/api/v1）を足しました。</p>")
    add('<footer><a href="/">THTH</a> · <a href="/terms/">利用規約 / Terms of Service</a> · Free and open source · '
        "MIT License<br>© 2026 gotoq</footer>")
    add("</main></body></html>")
    return "\n".join(parts) + "\n"


# 施行日は deploy のとき主セッションが入れる（privacy と同じ・生成で日付を作らない）。
TERMS_EFFECTIVE = "2026-09-26"  # 3.13.0 の改訂。初版: 2026-09-25


def contact_html(lang: str) -> str:
    """サポートの連絡先。privacy と利用規約の**両方に同じ文**を出す（片方だけ直さない）。

    メールアドレスは repo に無いので書かない。正は GitHub の issues。招待された利用者は
    自分のセッションから報告の口（`thth report file`・`thth/report_inbox.py`）も使える。
    """
    esc = lambda s: html_mod.escape(s, quote=True)  # noqa: E731
    issues = f'<a href="{GITHUB}/issues">{esc(GITHUB)}/issues</a>'
    if lang == "en":
        return (f"<p>Open an issue at {issues}. Issues are public: do not include tokens, App Passwords "
                "or personal information. Invited users can also file a report from their THTH session "
                "(<code>thth report file</code>); reports are kept in a private location on the "
                "operator's server. Operator: gotoq.</p>")
    return (f"<p>{issues} に issue を立ててください。issue は公開されます。token・App Password・"
            "個人情報は書かないでください。招待された利用者は、自分の THTH のセッションから報告"
            "（<code>thth report file</code>）もできます。報告は運営者のサーバの私有の置き場に保存します。"
            "運営者: gotoq。</p>")


def build_terms() -> str:
    """利用規約（2026-09-25・Meta の申請の段取り #3）。

    書くのは事実と、運営者がしていること・しないことだけ。法的な保証を約束しない。
    準拠法・管轄など運営者が決めていないことは入れない。英語が先（審査担当が読む）、
    日本語も同じ内容。データの扱いは privacy に任せ、ここでは繰り返さない。
    """
    esc = lambda s: html_mod.escape(s, quote=True)  # noqa: E731
    parts: list[str] = []
    add = parts.append
    add("<!doctype html>")
    add('<html lang="en"><head>')
    add('<meta charset="utf-8">')
    add('<meta name="viewport" content="width=device-width, initial-scale=1">')
    add('<meta name="referrer" content="no-referrer">')
    add("<title>THTH — Terms of Service / 利用規約</title>")
    add(f"<style>\n{STYLE}</style>")
    add("</head><body><main>")
    add("<h1>THTH — Terms of Service</h1>")
    date_en = "Effective " + esc(TERMS_EFFECTIVE) if TERMS_EFFECTIVE else "Unpublished draft — effective date not set"
    add(f'<p class="note">{date_en} · <a href="#ja">日本語はこの下</a> · '
        '<a href="/privacy/">Privacy Policy</a></p>')

    add("<h2>What THTH is</h2>")
    add("<p>THTH is a command-line tool for publishing and managing posts on your own social media accounts, "
        "usually through your own AI assistant. The operator, gotoq, runs it on an operator-managed server for people the operator has invited or "
        "otherwise verified. Invited users connect their own accounts and do not run their own server or "
        "register their own app. These terms cover that service.</p>")

    add("<h2>What you do</h2>")
    add("<ul>")
    add("  <li>Authorize THTH only for accounts you control, on the platform's own screen, after checking the "
        "account and the permissions it asks for.</li>")
    add("  <li>You are responsible for what is published, replied to and deleted on your accounts, whether you "
        "ask for it yourself or through an AI assistant you connect. THTH does not judge content for you.</li>")
    add("  <li>Follow the terms and rules of each platform you post to.</li>")
    add("  <li>Publish only content that does not infringe other people's rights, such as copyright, privacy "
        "or portrait rights.</li>")
    add("  <li>Keep your account secret, your assistant key and your platform credentials to yourself. Do not "
        "paste them into chats, drafts or issues. If you think a key has leaked, issue a new one on "
        "https://thth.me/activity; the previous key stops working.</li>")
    add("</ul>")

    add("<h2>What the operator does and does not do</h2>")
    add("<ul>")
    add("  <li>THTH acts only when you ask for it, through your CLI or your assistant; it does not post, reply "
        "or delete on its own. Every account has safety limits you control: a minimum interval between posts, "
        "daily caps on posts and deletions, and an automatic stop on bursts of actions. An assistant can only "
        "tighten them; loosening them, resuming a stopped account and issuing or revoking an assistant key "
        "are done by you on https://thth.me/activity with your account secret.</li>")
    add("  <li>Credentials (platform tokens and Bluesky App Passwords) are received through the authorization "
        "flow or from standard input, kept in owner-only files on the operator's server, and redacted from "
        "logs and printed output. They are not passed through chats or command arguments, and THTH's tools "
        "do not return them to LLM sessions.</li>")
    add("  <li>THTH does not read direct messages.</li>")
    add("  <li>The operator can stop an account's use of the service at any time and can hide a post on the "
        "plaza. The operator stops an account when you ask, and may stop it when these terms or a platform's "
        "terms are not followed, when a platform requires it, or when the service ends. Stopping may happen "
        "without advance notice.</li>")
    add('  <li>How data is used, kept and deleted is described in the <a href="/privacy/">Privacy Policy</a>.</li>')
    add("</ul>")

    add("<h2>How the service is provided</h2>")
    add("<p>The service is free of charge and provided as is. It may stop, change or be unavailable at times, "
        "and a post may be delayed or not published. The operator does not guarantee that the service is "
        "available, free of errors or suitable for any purpose.</p>")

    add("<h2>Leaving</h2>")
    add("<p>To leave, contact the operator (see Contact). The operator ends the account with "
        "<code>thth account leave</code>, which stops the account and deletes its data on the operator's "
        'server as described in the <a href="/privacy/#delete">Privacy Policy</a>. THTH asks Mastodon and X '
        "to revoke the token. For Threads, remove the connection in your Threads settings; for Bluesky, "
        "delete the App Password in your Bluesky settings.</p>")

    add("<h2>Contact</h2>")
    add(contact_html("en"))
    add("<h2>Changes</h2>")
    add("<p>This page is generated from the THTH source repository; its history is public there.</p>")
    add("<h2>Software license</h2>")
    add("<p>The THTH software is open source under the MIT License. That license covers the software; these "
        "terms cover the operator's service.</p>")

    # ---------------------------------------------------------------- 日本語
    add('<h1 id="ja" style="margin-top:56px">THTH — 利用規約</h1>')
    date_ja = esc(TERMS_EFFECTIVE) + " 施行" if TERMS_EFFECTIVE else "未公開の案 — 施行日未設定"
    add(f'<p class="note">{date_ja} · <a href="/privacy/">プライバシーポリシー</a></p>')

    add("<h2>THTH とは</h2>")
    add("<p>THTH は、自分の SNS のアカウントの投稿を公開・管理するためのコマンドラインツールで、多くの人は自分の "
        "AI アシスタントを通して使います。運営者 gotoq が、"
        "運営者の管理するサーバで、運営者が招待した人または確かめた人のために動かしています。招待された利用者は"
        "自分のアカウントを接続し、自分のサーバを動かしたり自分のアプリを登録したりしません。この規約はその"
        "サービスについてのものです。</p>")

    add("<h2>利用者がすること</h2>")
    add("<ul>")
    add("  <li>自分が管理するアカウントだけを、媒体の画面でアカウントと求められる権限を確かめてから認可する。</li>")
    add("  <li>自分のアカウントで公開・返信・削除されるものには、自分で頼んだときも、接続した AI アシスタントを通して"
        "頼んだときも、利用者が責任を持つ。THTH は内容の善し悪しを判断しない。</li>")
    add("  <li>投稿する各媒体の規約と決まりを守る。</li>")
    add("  <li>著作権・プライバシー・肖像権など、他人の権利を侵さない内容だけを公開する。</li>")
    add("  <li>口座の secret・アシスタントの鍵・媒体の認証情報は自分だけで持つ。チャット・原稿・issue に貼らない。"
        "鍵が漏れたと思ったら https://thth.me/activity で発行し直す（前の鍵は使えなくなる）。</li>")
    add("</ul>")

    add("<h2>運営者がすること・しないこと</h2>")
    add("<ul>")
    add("  <li>THTH は、利用者が自分の CLI か AI アシスタントで頼んだときだけ動き、自分から投稿・返信・削除を"
        "しません。どのアカウントにも利用者が決める安全装置があります。投稿の最短間隔、投稿と削除の 1 日の上限、"
        "短時間に操作が多すぎたときの自動停止です。アシスタントはこれを締める向きにしか変えられません。緩める・"
        "止まったアカウントを戻す・アシスタントの鍵を発行する／取り消すのは、利用者が https://thth.me/activity で"
        "口座の secret を使って行います。</li>")
    add("  <li>認証情報（媒体の token と Bluesky の App Password）は、認可の流れか標準入力で受け取り、運営者の"
        "サーバの所有者だけが読めるファイルに置き、ログと画面の出力では伏せます。チャットやコマンドの引数を"
        "通さず、THTH の道具が LLM のセッションに返すこともありません。</li>")
    add("  <li>THTH はダイレクトメッセージを読みません。</li>")
    add("  <li>運営者は、いつでもアカウントの利用を止められ、広場の書き込みを非表示にできます。利用者が求めたときは"
        "止めます。この規約や媒体の規約が守られないとき、媒体が求めたとき、サービスを終えるときにも止めることが"
        "あります。止めるときに前もって知らせないことがあります。</li>")
    add('  <li>データの使い方・保存・削除は<a href="/privacy/#ja">プライバシーポリシー</a>に書いています。</li>')
    add("</ul>")

    add("<h2>提供の形</h2>")
    add("<p>サービスは無償で、現状のまま提供します。止まる・変わる・使えない時間があり、投稿が遅れたり公開"
        "されなかったりすることがあります。運営者は、サービスが使えること・誤りがないこと・特定の目的に合うことを"
        "保証しません。</p>")

    add("<h2>やめ方</h2>")
    add("<p>やめるときは運営者に連絡してください（連絡先を参照）。運営者は <code>thth account leave</code> で"
        "アカウントの利用を終えます。アカウントを止め、運営者のサーバのデータを"
        '<a href="/privacy/#ja">プライバシーポリシー</a>のとおり削除します。Mastodon と X は THTH が媒体に token の'
        "失効を求めます。Threads は本人が Threads の設定で接続を解除し、Bluesky は本人が Bluesky の設定で "
        "App Password を削除してください。</p>")

    add("<h2>連絡先</h2>")
    add(contact_html("ja"))
    add("<h2>変更</h2>")
    add("<p>このページは THTH のソースリポジトリから生成されており、変更の履歴はそこで公開されています。</p>")
    add("<h2>ソフトウェアのライセンス</h2>")
    add("<p>THTH のソフトウェアは MIT License のオープンソースです。このライセンスはソフトウェアについてのもので、"
        "運営者のサービスについてはこの規約が定めます。</p>")
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
        "terms/index.html": build_terms(),
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
