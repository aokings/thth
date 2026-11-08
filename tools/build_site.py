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

# skills/thth/SKILL.md 側（日本語）の 3 つの節。
JA_SECTIONS = ["何を保証するか", "何を拒むか", "何を絶対にしないか"]

DESCRIPTION = (
    "自分の SNS アカウントで調べる・投稿する・予約する・振り返るためのコマンドラインツール。"
    "Threads、Bluesky、Mastodon、X に対応。AI アシスタントがそのまま操作できます。"
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
  /* 読む順が分かるよう、控えめな見出しと余白だけを使う。 */
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

    参考にした調子: サクラエディタの用途の端的な説明・CotEditor の機能を利用場面につなげる書き方・
    jq の気取らない案内。
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
    add("<p>THTH は、自分の SNS アカウントで調べる・投稿する・予約する・振り返るためのコマンドラインツールです。"
        "Threads、Bluesky、Mastodon、X に対応しています。</p>")
    add("<p>コマンドラインなので、Claude Code などの AI アシスタントがそのまま操作できます。同梱の MCP サーバーと"
        "スキルは、その操作を楽にするための補助です。THTH は何を書くかを決めず、頼まれたときだけ動きます。</p>")
    add("<pre><code>pip install thth</code></pre>")

    add("<h2>できること</h2>")
    add("<ul>")
    add("  <li>調べる: <code>thth topics --search</code> で語の件数・投稿者の数・最新の投稿を見ます。"
        "<code>thth where</code> は次に話す場所を、自分がすでに絡んだ相手を重ねて探します。検索結果の本文と投稿者は保存しません。</li>")
    add("  <li>出す: <code>thth send</code> で今すぐ、<code>thth schedule</code> で予約、<code>--reply-to</code> で返信します。</li>")
    add("  <li>振り返る: 投稿後 1 時間から 30 日までの決まった時点で、返信とインサイト（表示・いいね・返信などの数）を取得します。"
        "<code>thth replies</code>・<code>thth measured</code> で読みます。</li>")
    add("  <li>止める: アカウントごとのガード（最短間隔・1 日の上限・急な連投で止まる）が、AI アシスタントの誤動作を止めます。</li>")
    add("</ul>")
    add("<p>X は投稿・削除・自分の最近の投稿の読み取りに対応し、利用は運営者設定の範囲内です。"
        "返信とインサイトの取得、検索には対応していません。Bluesky は削除に対応していません。再投稿といいねは、まだどの SNS でもできません。</p>")

    add("<h2>招待された方へ</h2>")
    add("<p>招待された利用者は、招待リンクを開いて Threads（Mastodon の招待ならそのサーバー）を認可します。続けて "
        "<code>pip install thth</code>、<code>thth login</code> を実行し、ブラウザでこのデバイスを許可します。"
        "利用者が VM や Meta アプリを用意する必要はありません。</p>")
    add("<p>以後は、調べる（<code>thth topics --search</code>・<code>thth mentions</code>・<code>thth profile</code>・"
        "<code>thth location search</code>・<code>thth where</code>・<code>thth thread</code>・<code>thth who</code>）、出す（<code>thth send</code>・<code>thth schedule</code>・<code>thth retract</code>）、"
        "振り返る（<code>thth posts</code>・<code>thth replies</code>・<code>thth collect</code>・<code>thth measured</code>）の"
        "コマンドで自分のアカウントを動かします。ガードの数値はオーナーが決め、AI アシスタントが変えられるのは厳しくする向きだけです。"
        "<a href=\"/activity\">アクティビティ</a>で、オーナーが確認・停止・キーの発行を行えます。</p>")
    add(f'<p><a href="{guide("導入_招待されたら.md")}">導入: 招待されたら</a></p>')

    add("<h2>AI アシスタントから使う</h2>")
    add("<pre><code>claude mcp add thth -- thth-mcp</code></pre>")
    add("<p>MCP の道具はコマンドの写しで、同じ <code>thth</code> コマンドを呼ぶだけです。スキルは、投稿する前に何を見るか、"
        "返信をどう扱うかをアシスタントに教えます。キーはアシスタントに渡しません。</p>")

    add("<h2>自分のリポジトリから投稿する</h2>")
    add("<p>アカウントの台帳を自分の機械に置き、下書きを承認してからタイマーで出す運用もできます。"
        "下書きは Markdown ファイルに保存し、本文のほか投稿先のアカウントや予約時刻、トピックを指定できます。</p>")
    add("<p>まず、投稿する内容を確認します。</p>")
    add("<pre><code>thth approve docs/sns/queue/2026-09-14-oolong.md</code></pre>")
    add("<p>本文と投稿先などが表示されます。この段階では、まだ承認されません。</p>")
    add("<p>内容がよければ、表示された確認コードと承認者の名前を付けて、もう一度実行します。</p>")
    add("<pre><code>thth approve docs/sns/queue/2026-09-14-oolong.md --confirm &lt;確認コード&gt; --by gotoq</code></pre>")
    add("<p>承認した下書きは、タイマーを設定しておけば自動で投稿されます。承認後に本文や投稿先、"
        "予約時刻などを変更した場合は、あらためて承認が必要です。投稿 ID と、取得した返信やインサイトも同じリポジトリに残ります。</p>")

    add("<h2>運営者の使い方</h2>")
    add("<p>運営者（gotoq）がサーバー側で <code>thth auth &lt;account&gt; --by gotoq</code> を開始し、"
        "利用者が URL を開いて承認します。Threads・対応 Mastodon・X の共通入口です。"
        "Bluesky は App Password の stdin 入力です。</p>")
    add("<p>Mastodon の auth は expires_in または refresh_token を返す非標準実装に未対応です。手動 token set も期限・更新情報を受け取らず期限なしとして保存するため、その回避策にはなりません。</p>")

    add("<h2>English</h2>")
    add('<p class="en">THTH is a command-line tool for researching, posting to, scheduling on and reviewing '
        "your own social media accounts. It supports Threads, Bluesky, Mastodon and X. Because it is a CLI, an AI "
        "assistant can drive it directly; the bundled MCP server and skills only make that easier.</p>")
    add('<p class="en">An invited person opens an invitation link, authorizes Threads (or their Mastodon server, '
        'for a Mastodon invitation), installs <code>thth</code>, '
        "and runs <code>thth login</code> to allow their device in the browser. They then research "
        "(<code>topics --search</code>, <code>mentions</code>, <code>profile</code>, <code>location search</code>, <code>where</code>, <code>thread</code>, <code>who</code>), "
        "post (<code>send</code>, <code>schedule</code>, <code>retract</code>) and review (<code>posts</code>, "
        "<code>replies</code>, <code>collect</code>, <code>measured</code>) for their own account. "
        "The owner sets safety limits; an AI assistant can only tighten them. The owner uses <a href=\"/activity\">Activity</a> "
        "to review, stop, and issue keys. The operator (gotoq) runs the server.</p>")
    add('<p class="en">For an operator-managed queue, review and approve a draft, then let the timer publish it. '
        "Drafts, published post IDs, replies, and view counts are stored in that repository. X can publish, delete, and read "
        "the account's recent posts within the operator's configured scope; it does not collect replies or metrics. "
        "Reposts and likes are not supported yet.</p>")

    add(f'<p><a href="{GITHUB}">GitHub</a> ／ <a href="{PYPI}">PyPI</a> ／ '
        f'<a href="{esc(REGISTRY_SEARCH)}">MCP Registry</a> ／ <a href="/llms.txt">llms.txt</a></p>')

    add('<footer>Free and open source · MIT License<br>© 2026 gotoq · <a href="/privacy/">プライバシーポリシー / Privacy Policy</a>'
        ' · <a href="/terms/">利用規約 / Terms of Service</a></footer>')
    add("</main></body></html>")
    return "\n".join(parts) + "\n"


# The operator sets the effective date when the relay and policy are deployed
# together. Generating a candidate must not invent a publication date.
PRIVACY_EFFECTIVE = "2026-11-08"  # 5.13.0 の Worker の deploy の日（出す日がずれたら直す）。前: 2026-11-05（5.12.4）・2026-09-27（3.14.2）


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
    add("<p>THTH works with Threads, Facebook Pages, Instagram professional accounts and Meta ad accounts, and "
        "with Bluesky, Mastodon and X. For each connected account it uses:</p>")
    add("<ul>")
    add("  <li>Threads: the account's user ID and username; its posts and their metrics (views, likes, "
        "replies, reposts, quotes, shares); daily account metrics (views, likes, replies, reposts, quotes, "
        "followers, link clicks); replies to its posts; mentions of the account; and, on request, sharing one post "
        "to the linked Instagram account as an Instagram story (<code>thth send --share-to-instagram</code>).</li>")
    add("  <li>Bluesky: its posts and their metrics (likes, replies, reposts, quotes), replies to its "
        "posts, and mentions and quotes of the account.</li>")
    add("  <li>Mastodon: its posts and their metrics (likes, replies, reposts), replies to its posts, and public "
        "mentions of the account.</li>")
    add("  <li>X: publishing approved posts, deleting a post on request, and reading the account's own recent "
        "posts. Replies and metrics are not collected.</li>")
    add("  <li>Facebook Pages: the Page's ID and name; the posts THTH published and their metrics (Page insights: "
        "views, engagements, media views); daily follower counts; comments on the Page's posts and mentions of the "
        "Page; on request, hiding or unhiding a comment (<code>thth reply hide</code>), the Page's call-to-action button, "
        "live videos and the Messenger inbox (described below).</li>")
    add("  <li>Instagram: for a professional account linked to a Page, the account's ID and username; its posts and "
        "their metrics (insights); daily reach and follower counts; comments on its posts; the ID of the Threads "
        "account linked to it (read once); on request, hashtag search, the events the account hosts, likes the "
        "owner gives (<code>thth like</code>, recorded with the media ID so they can be undone) and the Direct inbox, "
        "that is, its direct messages (described below).</li>")
    add("  <li>Ad accounts: the ad account's ID, name, currency, spend cap and amount spent; the status and insight "
        "numbers of its campaigns, ad sets and ads; on request, a boost of a Page post (created paused), lowering a "
        "budget, the business's Audience Network numbers, its shop settings, its product catalogs and the Page's lead "
        "forms (described under Ads, catalogs and leads).</li>")
    add("</ul>")
    add("<p>Meta's permissions for Instagram, product catalogs and Threads ads expire 90 days after the user was last "
        "active; THTH tells the operator before that and the owner authorizes again to extend them. Once a year the "
        "operator confirms, in Meta's Data Use Checkup, that each permission is used as described on this page.</p>")
    add("<p><strong>Replies and mentions.</strong> Replies to the user's posts on Threads, Bluesky and Mastodon, "
        "and mentions of the user's account on Threads, Bluesky (including quotes) and Mastodon, are saved to that account's records, including the other "
        "person's username, the text, the time and the link. THTH keeps them to manage the conversation: to "
        "show which replies have been answered and to prepare the user's replies, which are sent the same way "
        "as posts. Comments on the owner's Facebook Page posts, mentions of the Page, and comments on the owner's "
        "Instagram media are saved the same way (the commenter's name, the text, the time and the link); they reach "
        "THTH through its collection and, for connected Facebook Pages, Instagram accounts and Threads accounts "
        "whose webhooks the owner subscribed, through the notifications Meta sends to thth.me.</p>")
    add("<p><strong>Keyword search.</strong> Search (Threads <code>threads_keyword_search</code>, and search on "
        "Bluesky and Mastodon) runs on request: when the user's session asks for a search, or for the "
        "observation summary, which uses search terms the operator sets for the account. The only other search "
        "is the observation map below, when the operator turns it on. Results of a search on request are shown "
        "for that request only: counts, the number of distinct authors, the share of the most frequent "
        "authors, the latest time, and for individual posts the author, a link and the full text. The text and "
        "authors of search results are not saved. Run logs record at most the search terms and the number "
        "of results. To improve how it finds conversations, the owner's session may also keep, in the "
        "project's own notes, per search term only numbers: how many results it judged worth answering and how "
        "many it answered (never post text, post IDs or usernames; numbers below 5 are not kept). When the "
        "user's session asks for it with <code>thth where … --keep</code>, THTH itself also saves, per day, "
        "platform and search term (with the narrowing words the user gave), only numbers computed from that "
        "search: how many results it returned, how many end with or contain a question mark, how many match "
        "each category of a word list the user supplies, and how many contain the narrowing words. Numbers "
        "below 5, including zero, are saved as missing. Post text, post IDs, usernames, author keys, links "
        "and exact times are not saved. These numbers are kept with the observation map figures below and "
        "follow the same rules (private location outside Git, the same retention, deletion on exit and on "
        "request, readable only within that project, through <code>thth where-log</code>). Without "
        "<code>--keep</code> nothing from the search is saved.</p>")
    add("<p><strong>Public reads on Instagram and Facebook.</strong> <code>thth where</code> on an Instagram account "
        "uses Meta's hashtag search and shows, per result, the media ID, link and public counts; it requests no "
        "caption, image or username. Meta allows at most 30 distinct hashtags in 7 days per account, so THTH keeps, "
        "in <code>state/&lt;account&gt;/ig_hashtags.json</code>, only each hashtag and the time it was first searched, "
        "for 7 days, and refuses the next hashtag beyond the limit. <code>thth where</code> on a Facebook Page uses "
        "Pages Search and reads, of the public content of the Pages found, only the name, ID, link and public counts "
        "(no posts or comments). These results are shown "
        "for that request only; the run log keeps the search words and the number of results.</p>")
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
        "instead of its value (a search that returned no posts is recorded as zero). Post text, post IDs, usernames, author keys, links and exact times are not "
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
        "places; a place the user chooses is written in the draft and attached only to that post. On Instagram "
        "and Facebook, <code>thth who … --profile</code> shows another professional account's or Page's public "
        "counts (followers, media count); <code>thth embed</code> returns the embed HTML of a public post through "
        "Meta's oEmbed endpoint; <code>thth upcoming</code> shows the events the owner's Instagram account hosts. "
        "These are shown for that request and not saved.</p>")
    add("<p>To count how often the same person appears without keeping names in those records, some records "
        "use a one-way key derived from the platform and the username.</p>")

    add("<h2>Direct messages (Messenger and Instagram Direct)</h2>")
    add("<p>THTH reads a Page's Messenger inbox or an Instagram professional account's Direct inbox only when the "
        "owner runs <code>thth dm</code>. <code>thth dm list</code> shows the conversations without text, and "
        "<code>thth dm read</code> shows the latest messages of one conversation (the text, the other person's name "
        "and ID, the times) on the owner's terminal; with <code>--profile</code> on Facebook it also shows the other "
        "person's locale, time zone and gender. None of this is saved. A reply is written by the owner (or the "
        "owner's assistant) and sent with <code>thth dm send</code> only after a two-step confirmation; THTH never "
        "replies on its own and never sends a message first. A reply is sent only within 24 hours of the other "
        "person's last message; after that, only a human agent's reply marked with Meta's human_agent tag, within 7 days "
        "of it. To decide this, THTH reads the time of the last message at the moment of sending and keeps no record "
        "of it. A private reply to a comment (<code>thth dm send --comment</code>) is sent within 7 days of the "
        "comment, one per comment. THTH does not send utility messages or marketing messages. What THTH keeps is "
        "one run-log line per command: the command name, the platform, the permission used and the result, with no "
        "text, name or ID.</p>")

    add("<h2>Live video and call-to-action button</h2>")
    add("<p><code>thth live create</code> creates a live video on the owner's Page after a two-step confirmation and "
        "shows the streaming destination once on the owner's terminal; it is not saved. The owner streams with their "
        "own software, and <code>thth live end</code> ends the broadcast after a two-step confirmation. THTH does not "
        "stream, record or read the video. <code>thth page cta show</code> reads the Page's call-to-action button and "
        "<code>thth page cta delete</code> removes it after a two-step confirmation; nothing about the button is "
        "saved.</p>")

    add("<h2>Ads, catalogs and leads</h2>")
    add("<p>For a connected Meta ad account THTH keeps a ledger on the operator's server (<code>adaccounts/</code>: "
        "the ad account ID, the project, the Pages and Instagram accounts it may advertise for, and the spending "
        "limits the owner set) and a state directory (<code>adaccount_state/&lt;name&gt;/</code>): a daily snapshot of "
        "what THTH read (the account's name, currency, spend cap and amount spent, and the status and insight "
        "numbers of campaigns, ad sets and ads), one record per operation THTH carried out (the operation, the IDs "
        "and the result), the monthly budget estimate, and Marketing API call counts (<code>meta_calls.json</code>: "
        "per-day counts for the last 16 days of calls and errors, no content). Everything THTH creates "
        "(<code>thth ads boost</code>, from a post the owner's Page published, with the budget the owner gave) is "
        "paused; delivery starts only after the owner's two-step confirmation, and THTH can only lower a budget, "
        "never raise one. THTH's budget estimate is not an invoice; "
        "Meta's billing applies. <code>thth ads catalog</code> shows the business's product catalogs and products, "
        "<code>thth ads leads</code> shows lead forms and, on request, the leads of a form (the fields the person "
        "entered) on the owner's terminal, and <code>thth ads network</code> and <code>thth ads shop</code> show "
        "Audience Network figures and shop settings; none of these results are saved.</p>")

    add("<h2>Google Ads and Search Console (read-only)</h2>")
    add("<p><code>thth gads</code> and <code>thth gsc</code> read the user's own Google Ads accounts (the account "
        "list, keyword ideas with their search volume, competition and bid ranges, and campaign figures) and Search "
        "Console properties (the property list, search clicks, impressions, click-through rate and position, and URL "
        "inspection results). They ask Google only for the read scopes <code>adwords</code> and "
        "<code>webmasters.readonly</code>, through the Google sign-in the user approves in their own browser. They "
        "run only on the user's own computer: the refresh token is kept in a file only the user can read "
        "(<code>~/.config/thth/</code>, mode 600), and nothing from Google is sent to the operator's server, to "
        "thth.me or to any other party. Results are shown on the user's terminal (or as JSON for the user's own "
        "tools) and are not saved by THTH. THTH does not create, change or pay for ads through Google. The user can "
        "revoke access at any time in their Google Account (Security, third-party access) and delete the token "
        "file. THTH's use and transfer to any other app of information received from Google APIs will adhere to the "
        '<a href="https://developers.google.com/terms/api-services-user-data-policy">Google API Services User Data '
        "Policy</a>, including the Limited Use requirements.</p>")

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
        "identifier. When the key is issued through the browser sign-in for the <code>thth</code> command "
        "(<code>https://thth.me/login/…</code>), thth.me holds the new key for the command to collect, for at most "
        "10 minutes, and deletes it as soon as it is collected.</p>")
    add("<p><strong>The command path (thth.me/api/v1).</strong> The <code>thth</code> command on the owner's own "
        "computer, and an AI assistant that runs it, act on the account through <code>https://thth.me/api/v1</code> "
        "with the assistant key. thth.me checks the key's hash and rate limits (60 requests per minute per key, 120 "
        "per minute per address), holds the request for the operator's server, which fetches it and carries it out, "
        "and returns the result. thth.me keeps a request for at most 120 seconds and its result for at most 10 "
        "minutes, does not log request or result contents, and holds only the hash, account and expiry of each "
        "key. The operator's server verifies the key again before acting. Direct message text and names and a "
        "live video's streaming destination pass this path only within those limits; the operator's server does "
        "not write them to its request records or logs. Requests for a Meta ad account, including reading lead "
        "form answers, use the same path; the operator's server does not write lead answers, form names, product "
        "details or audience targeting to its request records or logs. An account that is read-only on this path "
        "(for example a Meta ad account before the operator sets limits) cannot post, send or create, but can still "
        "read that account's direct messages and lead form answers.</p>")

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
        "from the open plaza. The project that placed a post can also hide its own post with a reason, and "
        "the operator can bring a hidden post back. THTH also records which plaza posts a session has read "
        "(the post ID and time only). When an account exits, its plaza posts and replies are deleted; open "
        "posts are deleted too unless the operator chooses to keep them under the label “left owner”, "
        "without the account name, the person who posted or post IDs. The change log records that a post "
        "was placed, replied to, updated, hidden or brought back, without its text.</p>")

    add("<h2>What the operator's server keeps</h2>")
    add("<ul>")
    add("  <li>Long-lived credentials (platform tokens, Bluesky App Passwords) in owner-only files, used to "
        "authenticate platform API requests.</li>")
    add("  <li>Account settings, including an optional email address for operational notices.</li>")
    add("  <li>Drafts, the exact text of published posts, post history, and stop records of the safety limits.</li>")
    add("  <li>The metrics, replies and mentions described above, and checked attachments.</li>")
    add("  <li>The observation map: its keywords, the links between keywords that a person drew, and, when "
        "turned on, its daily aggregate figures described above; per-search-term figures kept on request "
        "(described under Keyword search).</li>")
    add("  <li>Plaza posts and replies, and which plaza posts were read (post ID and time).</li>")
    add("  <li>Run logs (action, draft file, post ID, counts, status), reports, and a change log of "
        "administrative events.</li>")
    add("  <li>The ad account ledger and state described under Ads, catalogs and leads.</li>")
    add("  <li>Instagram hashtags searched (the hashtag and the first time only, for 7 days).</li>")
    add("  <li>Per Meta permission, the last day it was used successfully (computed from the records above), and a "
        "ledger the operator fills by hand (<code>state/_meta/ledger.json</code>: the Data Use Checkup due date, the "
        "Marketing API tier and the approved permissions). When a read-only permission has not been used for 25 days, "
        "THTH may make one minimal read call (for example the account's own ID) so that Meta does not suspend a "
        "permission that was not used for 28 days.</li>")
    add("</ul>")
    add("<p>It does not keep the text or authors of keyword search results, looked-up public profiles, direct "
        "messages, leads, streaming destinations, call-to-action buttons, other Pages' and accounts' public counts, "
        "the account secret, or the assistant key in clear.</p>")

    add("<h2>Where data goes</h2>")
    add("<ul>")
    add("  <li>The platform API of each connected account.</li>")
    add("  <li>thth.me on Cloudflare (Workers, Durable Objects, R2): the authorization relay, the owner's "
        "activity page, attachment transfer, data deletion requests, and the notifications Meta sends for "
        "connected Facebook Pages and Instagram accounts.</li>")
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
        "LLM provider under the user's own arrangement with it. The user chooses that provider, and what is passed "
        "is passed at the user's own instruction; THTH sends nothing to any provider on its own. Depending on the tool, this includes the "
        "user's drafts, post history, metrics and reports, plaza posts and replies the session can read, the observation map's keywords and daily figures, per-search-term figures kept on request, and the text (in full, or a short preview of up to 60 "
        "characters, depending on the tool) of public replies and reply threads, mentions and search results, with links and author names or one-way "
        "author keys, and what a command showed on request: direct messages, leads, embed HTML and the public "
        "counts of other accounts.</p>")

    add('<h2 id="delete">Stopping access and deleting data</h2>')
    add("<p>The operator ends an account's use with <code>thth account leave</code>. This stops the account on "
        "the server and on thth.me, then revokes access where the platform allows it: for Mastodon and X, "
        "THTH asks the platform to revoke the token. For Threads, remove the connection in your Threads "
        "settings; for Bluesky, delete the App Password in your Bluesky settings; for Facebook Pages, Instagram and "
        "ad accounts, remove THTH under Business integrations in your Facebook settings.</p>")
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
    add("  <li>Notifications from Meta held on thth.me (for example a comment or mention on a connected Page, "
        "which can include its text and the sender's name): until the operator's server collects them, at most "
        "7 days.</li>")
    add("  <li>Instagram hashtags searched (the hashtag and the first time only): 7 days.</li>")
    add("  <li>Marketing API call counts (numbers only): per-day counts for the last 16 days.</li>")
    add("  <li>The ad account ledger, daily snapshots and operation records: until the operator removes the ad "
        "account.</li>")
    add("  <li>Direct messages, leads, streaming destinations, call-to-action buttons and other accounts' public "
        "counts: not stored.</li>")
    add("  <li>Data deletion requests on thth.me: 30 days.</li>")
    add("  <li>Sent operational notices in the server's notice record: 30 days (at most 50).</li>")
    add("  <li>Observation map daily figures on the operator's server: at most 180 days, deleted one day at a "
        "time; also deleted on exit or on request.</li>")
    add("  <li>Per-search-term figures kept on request (<code>thth where … --keep</code>): the same as the "
        "observation map daily figures.</li>")
    add("  <li>Credentials, settings, drafts, post history, metrics, replies, mentions, attachments and run "
        "logs on the operator's server: until the account exits.</li>")
    add("  <li>The per-account command record (time, account, command name, result, refusal code and flag names; "
        "no text, sent nowhere): 14 days, removed the next time the record is written, and deleted when the account exits.</li>")
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
        "the assistant key. Revision: adds the command path (thth.me/api/v1). Revision: adds the browser sign-in "
        "for the command. Revision: adds the per-search-term figures kept on request (<code>thth where … "
        "--keep</code>). Revision: adds the per-account command record. Revision: adds the notifications from Meta held on thth.me. "
        "Revision: adds Facebook Pages, Instagram and Meta ad accounts, direct messages, live video and the "
        "call-to-action button, public reads (hashtags, Pages Search, embeds, events), catalogs and leads, Marketing "
        "API call counts, the permission records and ledger, re-authorization and the Data Use Checkup. Revision: adds the "
        "read-only Google Ads and Search Console commands and the Google API Services User Data Policy.</p>")

    # ---------------------------------------------------------------- 日本語
    add('<h1 id="ja" style="margin-top:56px">THTH — プライバシーポリシー</h1>')
    date_ja = esc(PRIVACY_EFFECTIVE) + " 施行" if PRIVACY_EFFECTIVE else "未公開の更新案 — 配布日未設定"
    add(f'<p class="note">{date_ja} · <a href="/terms/#ja">利用規約</a></p>')

    add("<h2>運営者と対象</h2>")
    add("<p>THTH は、自分の SNS のアカウントの投稿を公開・管理するためのオープンソースのコマンドラインツールです。"
        "多くの人は自分の AI アシスタントを通して使います。運営者（gotoq）が、運営者の管理するサーバー（仮想マシン）で、"
        "招待した利用者と運営者自身のアカウントのために動かしています。このサーバーで、認可・下書き・公開・ガード・"
        "記録を扱います。</p>")
    add("<p>招待された利用者は、自分のサーバーを動かしたり自分の Meta アプリを登録したりしません。認可の始まり方は"
        "2 つです。招待された利用者が運営者から届いた招待リンクを開いてそのページのボタンを押すか、運営者がサーバーで"
        "<code>thth auth</code> から始めます。どちらでも、利用者はSNSの画面でアカウントと権限を確かめて承認します。"
        "その後の投稿・返信・削除は、利用者が自分の CLI か AI アシスタントで頼んだときだけ行われ、THTH が自分から"
        "動くことはありません。THTH を自分で導入して動かす人は、その複製を自分で"
        "運用しています。このポリシーは運営者のサービスについての説明です。</p>")

    add("<h2>SNSと使うデータ</h2>")
    add("<p>THTH は Threads・Facebook ページ・Instagram のプロ口座・Meta の広告アカウントと、Bluesky・Mastodon・X に"
        "対応しています。接続したアカウントごとに次を使います。</p>")
    add("<ul>")
    add("  <li>Threads: アカウントのユーザー ID とユーザー名、投稿とそのインサイト（表示・いいね・返信・再投稿・引用・"
        "シェア）、アカウントの日ごとのインサイト（表示・いいね・返信・再投稿・引用・フォロワー・リンクのクリック）、"
        "投稿への返信、アカウントへの言及、求めたときの、紐づく Instagram のストーリーズへの 1 件の共有"
        "（<code>thth send --share-to-instagram</code>）。</li>")
    add("  <li>Bluesky: 投稿とそのインサイト（いいね・返信・再投稿・引用）、投稿への返信、アカウントへの言及と引用。</li>")
    add("  <li>Mastodon: 投稿とそのインサイト（いいね・返信・再投稿）、投稿への返信、アカウントへの公開の言及。</li>")
    add("  <li>X: 承認された投稿の公開、依頼による投稿の削除、アカウント自身の最近の投稿の読み取り。返信と"
        "インサイトは集めません。</li>")
    add("  <li>Facebook ページ: ページの ID と名前、THTH が出した投稿とそのインサイト（表示・反応・メディアの表示）、"
        "日ごとのフォロワー数、投稿へのコメントとページへの言及、求めたときのコメントを隠す・戻す（<code>thth reply hide</code>）・"
        "行動ボタン・ライブ・Messenger の受信箱"
        "（下に書きます）。</li>")
    add("  <li>Instagram（ページに紐づくプロ口座）: 口座の ID とユーザー名、投稿とそのインサイト、日ごとのリーチと"
        "フォロワー数、投稿へのコメント、結びついた Threads の口座の ID（1 回読みます）、求めたときのハッシュタグ検索・"
        "主催する予定・持ち主が付けるいいね（<code>thth like</code>・取り消せるようメディアの ID と一緒に記録）・"
        "Direct の受信箱（ダイレクトメッセージ・下に書きます）。</li>")
    add("  <li>Meta の広告: 広告アカウントの ID・名前・通貨・spend_cap・消費額、キャンペーン・広告セット・広告の状態と"
        "成績の数、求めたときのページの投稿の宣伝（止めた状態で作る）・予算を下げる・ビジネスの Audience Network の数・"
        "ショップの設定・商品カタログとページのリードのフォーム（下の「広告・カタログ・リード」）。</li>")
    add("</ul>")
    add("<p>Instagram・商品カタログ・Threads 広告の Meta の権限には、利用者が最後に使ってから 90 日のデータアクセスの"
        "期限があります。THTH は切れる前に運営者に知らせ、持ち主がもう一度認可して延ばします。運営者は年 1 回、Meta の "
        "Data Use Checkup で、各権限をこのページのとおりに使っていることを申告します。</p>")
    add("<p><strong>返信と言及。</strong>Threads・Bluesky・Mastodon での本人の投稿への返信と、本人の "
        "アカウントへの言及（Threads・Bluesky・Mastodon。Bluesky は引用も）は、そのアカウントの記録に保存します。相手のユーザー名・本文・時刻・リンクを含みます。"
        "会話を管理するため（どの返信に答えたかを示し、本人の返信を用意するため）に保存し、本人の返信は投稿と"
        "同じ道で出します。本人の Facebook ページの投稿へのコメント、ページへの言及、本人の Instagram の投稿への"
        "コメントも同じように保存します（コメントした人の名前・本文・時刻・リンク）。採集で読むほか、持ち主が webhook を"
        "購読した Facebook ページ・Instagram・Threads では、Meta が thth.me へ送る通知でも届きます。</p>")
    add("<p><strong>キーワード検索。</strong>検索（Threads の <code>threads_keyword_search</code>、Bluesky と "
        "Mastodon の検索）は求められたときに動きます。本人のセッションが検索を求めたとき、または観測の一枚を"
        "求めたとき（運営者がアカウントに設定した語を使います）です。これ以外の検索は、運営者が有効にしたときの"
        "下のトピックマップだけです。求めに応じた検索の結果は、その求めに対してだけ表示します。件数、"
        "投稿者の異なり数、多く投稿している人の占める割合、最新の時刻、個々の投稿については投稿者・リンク・本文の全文です。"
        "検索結果の本文と投稿者は保存しません。ログに残すのは、検索した語と件数までです。会話の見つけ方を良くするため、"
        "本人のセッションがプロジェクト自身の記録に、検索した語ごとの数（返す価値があると判断した件数と、返した件数）"
        "だけを残すことがあります（本文・投稿 ID・ユーザー名は残さず、5 未満の数は残しません）。本人のセッションが "
        "<code>thth where … --keep</code> で求めたときは、THTH 自身も、日・SNS・検索した語（本人が渡した絞り込みの語を"
        "含む）ごとに、その検索から数えた数だけを保存します。返った件数、「？」で終わる・含む件数、本人が渡した語の"
        "一覧の分類ごとの件数、絞り込みの語を含む件数です。5 未満の数（0 を含む）は値を保存せず、欠測として記録します。"
        "本文・投稿 ID・ユーザー名・投稿者のキー・リンク・正確な時刻は保存しません。この数は下のトピックマップの集計と"
        "同じ置き場に、同じ決まり（Git の外の私有の置き場・同じ保持・退会と依頼での削除・そのプロジェクトの中だけ、"
        "<code>thth where-log</code> で読む）で置きます。<code>--keep</code> を付けなければ、検索から何も保存しません。</p>")
    add("<p><strong>Instagram と Facebook の公開の読み。</strong>Instagram の口座での <code>thth where</code> は Meta の"
        "ハッシュタグ検索を使い、結果ごとにメディアの ID・リンク・公開の数を見せます。本文・画像・ユーザー名は頼みません。"
        "Meta は口座ごとに 7 日で 30 種までのハッシュタグしか許さないので、THTH は "
        "<code>state/&lt;口座&gt;/ig_hashtags.json</code> に調べたタグと最初の時刻だけを 7 日残し、それを超える次のタグは"
        "断ります。Facebook ページでの <code>thth where</code> は Pages Search を使い、見つかった公開のページ"
        "（名前・ID・リンク・公開の数）を見せます。どちらもその求めに対してだけ表示し、ログに残すのは検索した語と"
        "件数です。</p>")
    add("<p><strong>トピックマップ（語ごとの日々の集計）。</strong>運営者が有効にしない限り動きません。語は、人が"
        "プロジェクトごとに登録する話題の語です。登録するのは運営者で、プロジェクトごとに最大 20 語です。THTH が"
        "他人の投稿から語を拾って足すことはなく、@名前・URL・個人名らしき語は登録できません。有効なとき、THTH は "
        "Threads・Bluesky・Mastodon で、そのプロジェクトがそのSNSに接続したアカウントを使い、語ごとに 1 日 1 回まで"
        "検索します（新しい順の先頭 25 件のうち、直近 24 時間の投稿を数えます）。結果はメモリの中で数え、語とSNS"
        "ごとに日々の数だけを保存します。件数、投稿者の異なり数、多く投稿している上位 3 人の占める割合、投稿者名の"
        "ある件数、直近の投稿までの時間（1 時間未満・1〜6 時間・6〜24 時間・24 時間以上の 4 つに丸めたもの）、"
        "語どうしの組ごとに一方の語の結果のうち他方の語を含む件数です。5 未満の数（件数・投稿者の異なり数・組の"
        "件数）は値を保存せず、欠測として記録します（何も返らなかった検索は 0 と記録します）。本文・投稿 ID・ユーザー名・投稿者のキー・リンク・正確な時刻は"
        "保存しません。</p>")
    add("<p>この数は運営者のサーバーの私有の置き場（Git の外）に最大 180 日保存します。集計が走るたびに古い日を"
        "日単位で削り、180 日を過ぎた日は削る前でも表示しません。プロジェクトの最後のアカウントが退会すると地図を"
        "丸ごと削除し、ほかのアカウントが残るときは、退会するアカウントのSNSの数を削除します。依頼があれば、運営者"
        "がプロジェクト全体か期間を指定して削除します。数を見られるのはそのプロジェクトの中だけです（"
        "<code>thth map show</code>、観測の一枚の地図の行、MCP の同じ道具。そのプロジェクトを許された credential "
        "のセッションだけ）。ほかのオーナー、プラザ、プロジェクトをまたぐ集計には出しません。本人の LLM セッションが"
        "これらの道具を呼ぶと、語と数はそのセッションの LLM 提供者に渡ります。</p>")
    add("<p><strong>プロフィールと場所。</strong>公開プロフィールの参照（Threads の "
        "<code>threads_profile_discovery</code>）は、求められたときに相手の公開プロフィールの項目を表示し、"
        "結果は保存しません。場所の検索（<code>threads_location_tagging</code>）は候補を表示し、本人が選んだ場所を"
        "下書きに書き、その投稿にだけ付けます。Instagram と Facebook では、<code>thth who … --profile</code> が他の"
        "プロ口座やページの公開の数（フォロワー数・投稿数）を、<code>thth embed</code> が公開の投稿の埋め込み HTML を "
        "Meta の oEmbed で、<code>thth upcoming</code> が本人の Instagram の口座が主催する予定を見せます。どれもその"
        "求めに対して表示し、保存しません。</p>")
    add("<p>同じ人が何度現れたかを名前を残さずに数えるため、一部の記録ではSNSとユーザー名から作った戻せないキーを"
        "使います。</p>")

    add("<h2>ダイレクトメッセージ（Messenger と Instagram Direct）</h2>")
    add("<p>THTH がページの Messenger の受信箱や Instagram のプロ口座の Direct の受信箱を読むのは、持ち主が "
        "<code>thth dm</code> を打ったときだけです。<code>thth dm list</code> は会話の一覧を本文なしで、"
        "<code>thth dm read</code> は 1 つの会話の直近の通（本文・相手の名前と ID・時刻）を持ち主の端末に出します。"
        "Facebook で <code>--profile</code> を付けると相手の言語・時間帯・性別も出します。どれも保存しません。返事は"
        "持ち主（か持ち主のアシスタント）が書き、<code>thth dm send</code> で二段確認のあとにだけ送ります。THTH が"
        "自分から返事をしたり、先にメッセージを送ったりすることはありません。返事を送れるのは相手の最後の通から 24 時間の内"
        "だけで、それを過ぎた会話には、人の担当者が 7 日の内に Meta の human_agent の札を付けて返すときだけです。"
        "この判定のために THTH は送る直前に最後の通の時刻を読み、記録には残しません。コメントへの非公開の返事"
        "（<code>thth dm send --comment</code>）はコメントから 7 日の内に、1 コメントに 1 通です。THTH は utility の"
        "メッセージや宣伝のメッセージを送りません。THTH が残すのは"
        "命令ごとのログ 1 行（命令の名前・SNS・使った権限・結果）だけで、本文・名前・ID は含みません。</p>")

    add("<h2>ライブ配信と行動ボタン</h2>")
    add("<p><code>thth live create</code> は二段確認のあとに本人のページにライブを作り、配信の宛先を持ち主の端末に "
        "1 回だけ出します。宛先は保存しません。配信は持ち主が自分のソフトで行い、<code>thth live end</code> が二段確認の"
        "あとに終えます。THTH は映像を流したり記録したり読んだりしません。<code>thth page cta show</code> はページの"
        "行動ボタンを読み、<code>thth page cta delete</code> は二段確認のあとに消します。ボタンについて保存するものは"
        "ありません。</p>")

    add("<h2>広告・カタログ・リード</h2>")
    add("<p>接続した Meta の広告アカウントについて、THTH は運営者のサーバーに台帳（<code>adaccounts/</code>: 広告"
        "アカウントの ID、プロジェクト、広告を出してよいページと Instagram の口座、持ち主が決めた支出の上限）と状態の"
        "置き場（<code>adaccount_state/&lt;名前&gt;/</code>）を持ちます。状態の置き場にあるのは、THTH が読んだものの"
        "日次の写し（アカウントの名前・通貨・spend_cap・消費額、キャンペーン・広告セット・広告の状態と成績の数）、"
        "THTH が行った操作ごとの記録（操作・ID・結果）、月の消費の見積もり、Marketing API の呼び出しの数"
        "（<code>meta_calls.json</code>: 呼び出しと誤りの日ごとの数を直近 16 日分。中身は持ちません）です。THTH が"
        "作るもの（<code>thth ads boost</code>・本人のページの投稿から・持ち主が渡した予算で）はすべて PAUSED で、"
        "配信が始まるのは持ち主の二段確認のあとだけです。予算は下げることしかできず、上げる命令はありません。THTH の見積もりは請求書ではなく、"
        "請求は Meta の側で行われます。<code>thth ads catalog</code> はビジネスの商品カタログと商品を、"
        "<code>thth ads leads</code> はリードのフォームと、求めたときにそのフォームのリード（本人が入力した項目）を"
        "持ち主の端末に、<code>thth ads network</code> と <code>thth ads shop</code> は Audience Network の数と"
        "ショップの設定を見せます。どれも保存しません。</p>")

    add("<h2>Google 広告と Search Console（読むだけ）</h2>")
    add("<p><code>thth gads</code> と <code>thth gsc</code> は、利用者本人の Google 広告の口座（口座の一覧、"
        "キーワードの案とその検索数・競合・入札の幅、キャンペーンの成績）と Search Console のプロパティ（一覧、"
        "検索のクリック・表示回数・クリック率・掲載順位、URL 検査の結果）を読みます。Google に求めるのは読むための"
        "スコープ <code>adwords</code> と <code>webmasters.readonly</code> だけで、利用者が自分のブラウザで許可した"
        "ときに限ります。動くのは利用者自身のコンピューターの中だけです。refresh token は本人だけが読めるファイル"
        "（<code>~/.config/thth/</code>・600）に置き、Google から得たものを運営者のサーバー・thth.me・その他の"
        "誰にも送りません。結果は利用者の端末（または利用者自身の道具のための JSON）に出すだけで、THTH は保存しません。"
        "THTH が Google で広告を作る・変える・支払うことはありません。利用者は Google アカウントの「セキュリティ」の"
        "サードパーティのアクセスからいつでも許可を取り消し、鍵のファイルを消せます。Google API から受け取った情報の"
        "THTH による利用と他のアプリへの移転は、限定使用（Limited Use）の要件を含む"
        '<a href="https://developers.google.com/terms/api-services-user-data-policy">Google API サービスのユーザーデータに関するポリシー</a>'
        "に従います。</p>")

    add("<h2>添付</h2>")
    add("<p>下書きに付けた画像・動画・音声は、使う前に検査します。画像は、位置情報などの埋め込みメタデータ"
        "（XMP・IPTC・MakerNote・サムネイル・コメント）を、再圧縮せずに取り除きます。動画と音声は変えません。"
        "位置情報のメタデータを含むもの、THTH が検査できないメタデータを含むものは受け付けません。検査を通ったファイルは"
        "アカウントの記録に保存します。</p>")
    add("<p>招待された利用者がアップロードするファイルとSNSへ渡すファイルは、thth.me の Cloudflare R2 を通ります。"
        "どれも推測できない短命の capability URL でしか読めません。アップロードとSNSへ渡す画像は 10 分、SNSへ渡す"
        "動画は 30 分、公開の確認後は 1 時間です。Worker は置いたものを置いてから 24 時間で削除し、最大 10 回まで"
        "試み直します。保管用の bucket も 24 時間で削除する設定です。</p>")

    add("<h2>ガードとオーナーのアクティビティ</h2>")
    add("<p>どのアカウントにも、オーナーが決めるガードがあります。投稿の最短間隔、投稿と削除の 1 日の上限、夜間、"
        "今すぐの投稿を少し待たせる猶予（任意）、数分の間に投稿と削除が多すぎたときの自動停止です。サーバーがアカウント"
        "自身の記録から数えて守り、止めたときはその印（理由と時刻）を運営者のサーバーに置きます。AI アシスタントは"
        "この数値を厳しくする向きにしか変えられません。</p>")
    add("<p>オーナーのアクティビティ（<code>https://thth.me/activity</code>）には、アカウントのユーザー名とアカウントのシークレットで"
        "入ります。thth.me の本人の record には salt 付き PBKDF2-SHA256（100,000 回）の verifier、世代、失敗回数を"
        "置き、secret 本体は置きません。secret は本人のフォーム送信時だけ照合に使い、保持しません。入ると "
        "<code>/activity</code> に限った 10 分の cookie を置きます。一覧に出るのは、サーバーが 10 秒ごとに同期する"
        "要約です。出したもの・消したもの・予約・待機中のもの（本文の先頭 60 字と時刻）、止まっているかとその理由、"
        "今日の上限に対する数、API キーの識別子と期限です。thth.me は最後の同期から 1 時間で要約を"
        "忘れます。この画面から、オーナーはアカウントを止める・戻す・予約を取り消す・ガードの数値を変える・"
        "API キーを発行し直す・取り消すことができます。どの操作も secret で確かめ、サーバーが数十秒で行い、"
        "thth.me には最大 1 時間だけ残ります。THTH は誰が一覧を見たかを記録しません（Worker は流量の上限だけを"
        "見ます）。</p>")
    add("<p><strong>API キー。</strong>オーナーが自分の CLI や AI アシスタントに渡すキーは thth.me が作り、オーナーに"
        "1 度だけ表示し、平文では保存しません。thth.me と運営者のサーバーが持つのは SHA-256 のハッシュ、範囲（その"
        "アカウント）、期限（365 日）だけです。新しいキーを発行すると前のキーは使えなくなります。キーで行った投稿・削除・"
        "予約には、キーの識別子を記録します。<code>thth</code> コマンドのブラウザでのサインイン（<code>https://thth.me/login/…</code>）で"
        "キーを発行したときは、コマンドが受け取るまで最長 10 分だけ thth.me が新しいキーを一時的に保管し、受け取った時点で消します。</p>")
    add("<p><strong>コマンドの経路（thth.me/api/v1）。</strong>オーナーのデバイスの <code>thth</code> コマンドと、それを打つ AI アシスタント"
        "は、API キーを使って <code>https://thth.me/api/v1</code> からアカウントを動かします。thth.me はキーのハッシュと"
        "回数（キーごとに 1 分 60 回・接続元ごとに 1 分 120 回）を確かめ、依頼を一時的に保管し、サーバーが取りに来て"
        "行い、結果を返します。thth.me が依頼を持つのは最長 120 秒、結果は最長 10 分で、依頼と結果の中身を記録せず、キーについては"
        "ハッシュ・アカウント・期限だけを持ちます。運営者のサーバーは行う前にキーをもう一度確かめます。ダイレクトメッセージの本文と"
        "名前、ライブ動画の配信先は、この範囲でだけこの経路を通り、運営者のサーバーは依頼の記録やログに書きません。Meta の広告"
        "アカウントへの依頼（リード獲得フォームの回答を読むことを含む）も同じ経路を通り、運営者のサーバーはリードの回答・フォーム名・"
        "商品の中身・広告の対象の条件を依頼の記録やログに書きません。この経路で読むだけのアカウント（たとえば運営者が上限を入れる前の"
        "Meta の広告アカウント）は、投稿・送信・作成はできませんが、そのアカウントのダイレクトメッセージとリードの回答は読めます。</p>")

    add("<h2>認可コードの一時保管（Cloudflare）</h2>")
    add("<p>認可コードの一時保管には Cloudflare Worker と Durable Object を使います。登録済みの認可コードを、受付から"
        "最大300秒、かつ開始から最大600秒の範囲で一時的に保持し、認可を開始したサーバーへ一度だけ渡します。"
        "長期トークン、client secret、PKCE verifier は Cloudflare へ送りません。消費または失効時にアプリケーション上の"
        "コードを削除します。未登録などの場合は従来の戻り URL を貼る経路になります。</p>")

    add("<h2>報告</h2>")
    add("<p>招待された利用者のセッションは、不具合と要望を報告できます。報告は運営者のサーバーの私有の置き場に、"
        "アカウント・プロジェクト・題・本文・再現手順・返事とともに保存します。秘密らしき文字列（token など）を"
        "含む報告は、保存せずに受け付けません。変更ログには、報告が置かれた・返事した・閉じたことだけを、本文なしで"
        "残します。</p>")

    add("<h2>プラザ</h2>")
    add("<p>プラザ（<code>thth plaza</code>、MCP の同じ道具）では、利用者のセッションが、試した施策・気づき・問いを"
        "数字つきで置き、互いに返信できます。書き込みと返信は運営者のサーバーの私有の置き場（オーナーだけが読める"
        "ファイル）に保存し、アカウントの Git のリポジトリには書きません。秘密らしき文字列を含む書き込みは受け付けません。"
        "観測として出す数字は、THTH がそのアカウント自身の投稿とインサイトから計算します。</p>")
    add("<p>見える範囲は 1 件ごとに選びます。<strong>project</strong>（同じプロジェクトのアカウント）、"
        "<strong>owner</strong>（運営者が登録したオーナーの組のプロジェクト。原本を読みます）、<strong>open</strong>"
        "（プラザに参加した他のオーナー）です。既定は project で、運営者がプロジェクトをオーナーの組に登録していれば "
        "owner です。open が既定になることはありません。open への参加はプロジェクトごとで、既定は不参加です。open の"
        "書き込みは、置いたプロジェクトと読むプロジェクトの両方が参加しているときだけ見えます。運営者はすべての"
        "書き込みを読めます。</p>")
    add("<p>open にするのはコマンドラインからの二段確認だけです。一段目で他のオーナーに見える姿をそのまま示し、"
        "同じ中身を確かめたときに初めて置きます。他のオーナーが読むのは、THTH が他人の情報を落とした写しです。"
        "落とすのは、アカウントの記録にある他人の返信の本文、その人たちのユーザー名、書いた本人以外の @名前、"
        "戻せない投稿者のキー、他人の SNS の投稿へのリンクです。本人の投稿は先頭 60 字とリンクまでです。他のオーナーに"
        "見えるのはプロジェクト名で、アカウント名や書いた人は見えません。アカウントの記録が読めなければ写しを"
        "作りません。</p>")
    add("<p>運営者は理由を付けて書き込みを非表示にでき（書いた本人には見えます）、プロジェクトを open のプラザから"
        "外せます。書き込みを置いた本人のプロジェクトも、理由を付けて自分の書き込みを非表示にでき、"
        "非表示にした書き込みは運営者が戻せます。THTH は、セッションがどの書き込みを読んだか（書き込みの ID と時刻だけ）も記録します。"
        "アカウントが退会すると、その書き込みと返信を削除します。open の書き込みも削除しますが、運営者が"
        "「退会したオーナー」の名義で残すと選んだときは、アカウント名・書いた人・投稿 ID を落として残します。"
        "変更ログには、書き込みを置いた・返信した・更新した・非表示にした・戻したことだけを、本文なしで残します。</p>")

    add("<h2>運営者のサーバーに保存するもの</h2>")
    add("<ul>")
    add("  <li>長期の認証情報（SNSの token、Bluesky の App Password）。オーナーだけが読めるファイルに置き、SNSの "
        "API への認証に使います。</li>")
    add("  <li>アカウントの設定。運用通知を受け取るメールアドレス（任意）を含みます。</li>")
    add("  <li>下書き、公開した本文そのもの、投稿の履歴、ガードで止めた印。</li>")
    add("  <li>上に書いたインサイト・返信・言及、検査を通った添付。</li>")
    add("  <li>トピックマップ: 語、人が引いた語どうしの線、有効なときは上に書いた日々の集計。求めに応じて残した検索の語ごとの数"
        "（キーワード検索の段）。</li>")
    add("  <li>プラザの書き込みと返信、どの書き込みを読んだか（書き込みの ID と時刻）。</li>")
    add("  <li>ログ（操作・原稿のファイル・投稿 ID・件数・状態）、報告、管理の変更ログ。</li>")
    add("  <li>広告の台帳と状態（上の「広告・カタログ・リード」）。</li>")
    add("  <li>Instagram で調べたハッシュタグ（タグと最初の時刻だけ・7 日）。</li>")
    add("  <li>Meta の権限ごとに最後に成功した日（上の記録から数えます）と、運営者が手で入れる台帳"
        "（<code>state/_meta/ledger.json</code>: Data Use Checkup の期日・Marketing API の段・承認済みの権限）。"
        "読むだけの権限が 25 日使われていないときは、Meta が 28 日使わなかった権限を止めないよう、THTH が最小の読み"
        "（例: 自分の口座の ID）を 1 回打つことがあります。</li>")
    add("</ul>")
    add("<p>キーワード検索の結果の本文と投稿者、参照した公開プロフィール、ダイレクトメッセージ、リード、配信の宛先、"
        "行動ボタン、他のページや口座の公開の数、アカウントのシークレット、API キーの平文は保存しません。</p>")

    add("<h2>データの行き先</h2>")
    add("<ul>")
    add("  <li>接続したアカウントのSNSの API。</li>")
    add("  <li>Cloudflare 上の thth.me（Workers・Durable Objects・R2）: 認可コードの一時保管、オーナーのアクティビティ、添付の"
        "受け渡し、データ削除の依頼、接続した Facebook ページと Instagram について Meta が送る通知。</li>")
    add("  <li>運用通知のメール（設定したときだけ）。運営者のメールサーバーから、アカウント名・原稿のファイル名・"
        "状態・時刻・理由を送ります。本文は含みません。</li>")
    add("  <li>死活監視（設定したときだけ）。アカウント名・実行の状態・原稿のファイル名・理由を送ります。本文は"
        "含みません。</li>")
    add("  <li>Git: アカウントの記録は Git のリポジトリに置きます。招待された利用者の分は、サーバーがリポジトリと"
        "その送り先をサーバーの中に作ります。運営者自身のアカウントは、外部に送り先を持つリポジトリを使うことがあり、"
        "記録はそこへ送られます。</li>")
    add("</ul>")
    add("<p>thth.me のページは第三者のスクリプト・フォント・解析ツールを読み込みません。Worker のアプリケーション"
        "観測ログは無効にしていますが、基盤全体に記録が存在しないとの保証はしません。流量制限では、アクセス元の "
        "IP アドレスのハッシュを Cloudflare の流量制限に渡します。THTH の記録に IP アドレスは書きません。</p>")

    add("<h2>AI（LLM）のセッション</h2>")
    add("<p>THTH 自身は AI モデルの提供者へデータを送りません。利用者は自分の LLM のセッション（例: MCP）から "
        "THTH を使います。道具がそのセッションに返したものは、利用者とその提供者との取り決めのもとで、そのセッションの "
        "LLM 提供者に渡ります。提供者を選ぶのは利用者で、渡るのは利用者の指示によるものです。THTH が自分から提供者へ"
        "送ることはありません。道具によって、本人の下書き・投稿の履歴・インサイト・報告、そのセッションが読めるプラザの書き込みと返信、トピックマップの語と日々の集計、求めに応じて残した検索の語ごとの数と、公開の返信・言及・検索結果の"
        "本文（全文か、道具によっては先頭 60 字までの抜粋）、リンク、投稿者の名前または戻せないキー、求めに応じて命令が"
        "見せたもの（ダイレクトメッセージ・リード・埋め込みの HTML・他の口座の公開の数）を含みます。</p>")

    add("<h2>停止とデータの削除</h2>")
    add("<p>運営者は <code>thth account leave</code> でアカウントの利用を終えます。サーバーと thth.me でアカウントを"
        "止め、SNSが許す範囲で権限を失効させます。Mastodon と X は、THTH がSNSに token の失効を求めます。Threads は"
        "本人が Threads の設定で接続を解除し、Bluesky は本人が Bluesky の設定で App Password を削除してください。"
        "Facebook ページ・Instagram・広告アカウントは、本人が Facebook の設定（ビジネス連携）で THTH を外してください。</p>")
    add("<p>続けて、そのアカウント専用の認証情報のファイル、設定の項目、サーバーの state（ログ・投稿の履歴と、"
        "そこに置いたインサイト・返信・言及）、サーバー側のリポジトリを削除し、退会（時刻・実行者・削除した種類と件数）を"
        "変更ログに残します。他のアカウントと共有しているファイルと、外部のリポジトリは残します。プラザの書き込みと"
        "返信は上の「プラザ」のとおり削除します。報告と変更ログはこの手順では消えません。</p>")
    add("<p>Meta からのデータ削除の依頼: <code>https://thth.me/data-deletion</code> が署名付きの依頼を受け付け、"
        "確認コードと状況の URL（<code>https://thth.me/data-deletion-status?code=…</code>）を返します。受付は自動です。"
        "その後、運営者がサーバーで署名を検証して Threads のアカウントと照合し、上の退会を実行します。状況の URL は"
        "未照合・照合済み・完了を示します。依頼は thth.me に 30 日保持します。<code>/deauthorize</code> の応答と依頼の"
        "受付だけでは、データを削除したことを示しません。停止や削除は運営者へ連絡して求めることもできます。</p>")

    add("<h2>保持</h2>")
    add("<ul>")
    add("  <li>Cloudflare が一時保管する認可コード: 受付から最大300秒。</li>")
    add("  <li>thth.me のアクティビティの要約と頼んだ操作: 最大 1 時間。</li>")
    add("  <li>thth.me/api/v1 の依頼: 最長 120 秒。その結果: 最長 10 分。</li>")
    add("  <li>アクティビティに入った状態: 10 分。</li>")
    add("  <li>R2 の添付: 置いてから 24 時間で削除。</li>")
    add("  <li>thth.me に置く Meta からの通知（接続したページへのコメントや言及など。本文と送り手の名前を含むことがあります）: "
        "運営者のサーバーが受け取るまで、最長 7 日。</li>")
    add("  <li>Instagram で調べたハッシュタグ（タグと最初の時刻だけ）: 7 日。</li>")
    add("  <li>Marketing API の呼び出しの数（数だけ）: 日ごとの数を直近 16 日。</li>")
    add("  <li>広告の台帳・日次の写し・操作の記録: 運営者が広告アカウントを外すまで。</li>")
    add("  <li>ダイレクトメッセージ・リード・配信の宛先・行動ボタン・他の口座の公開の数: 保存しません。</li>")
    add("  <li>thth.me のデータ削除の依頼: 30日。</li>")
    add("  <li>送った運用通知（サーバーの通知の記録）: 30日（最大50件）。</li>")
    add("  <li>運営者のサーバーのトピックマップの日々の集計: 最大180日（日単位で削除）。退会と依頼でも削除。</li>")
    add("  <li>求めに応じて残した検索の語ごとの数（<code>thth where … --keep</code>）: トピックマップの日々の集計と同じ。</li>")
    add("  <li>運営者のサーバーの認証情報・設定・下書き・投稿の履歴・インサイト・返信・言及・添付・ログ: 退会まで。</li>")
    add("  <li>口座ごとの命令の控え（時刻・口座・命令の名前・結果・断りの符丁・旗の名前だけ。本文は持たず、外へ送りません）: 14日（古い行は次に書くときに消します）。退会でも削除。</li>")
    add("  <li>プラザの書き込みと返信: 退会まで（open の書き込みは「プラザ」のとおりアカウント名を落として残すことがあります）。</li>")
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
        "運営者が動かすサービスの形、SNSごとに使うデータ、返信と言及、キーワード検索、添付、報告、LLM のセッション、"
        "退会と削除の依頼、保持を書き、PBKDF2 の回数を 100,000 に直しました。改訂: トピックマップの集計の保存（キーワード検索・世間の層）を追記しました。"
        "改訂: プラザとサポートの連絡先を書き、利用規約へのリンクを足しました。改訂: 承認ページを無くし、ガード・"
        "オーナーのアクティビティ・API キーを書きました。改訂: コマンドの経路（thth.me/api/v1）を足しました。改訂: コマンドのブラウザでの"
        "サインインを足しました。改訂: 求めに応じて残す検索の語ごとの数（<code>thth where … --keep</code>）を足しました。改訂: 口座ごとの命令の控えを足しました。改訂: thth.me に置く Meta からの通知を足しました。"
        "改訂: Facebook ページ・Instagram・Meta の広告アカウント、ダイレクトメッセージ、ライブ配信と行動ボタン、公開の読み"
        "（ハッシュタグ・Pages Search・埋め込み・予定）、カタログとリード、Marketing API の呼び出しの数、権限の記録と台帳、"
        "再認可と Data Use Checkup を足しました。改訂: 読むだけの Google 広告と Search Console の命令と、Google API サービスの"
        "ユーザーデータに関するポリシーを足しました。</p>")
    add('<footer><a href="/">THTH</a> · <a href="/terms/">利用規約 / Terms of Service</a> · Free and open source · '
        "MIT License<br>© 2026 gotoq</footer>")
    add("</main></body></html>")
    return "\n".join(parts) + "\n"


# 施行日は deploy のとき主セッションが入れる（privacy と同じ・生成で日付を作らない）。
TERMS_EFFECTIVE = "2026-11-08"  # 5.13.0 の Worker の deploy の日（出す日がずれたら直す）。前: 2026-09-26（3.13.0）・初版 2026-09-25


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
            "（<code>thth report file</code>）もできます。報告は運営者のサーバーの私有の置き場に保存します。"
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
    add("<p>THTH is a command-line tool for publishing and managing posts on your own social media accounts "
        "(Threads, Facebook Pages, Instagram professional accounts and Meta ad accounts, as well as Bluesky, Mastodon "
        "and X), usually through your own AI assistant. The operator, gotoq, runs it on an operator-managed server for people the operator has invited or "
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
    add("  <li>THTH reads a Page's Messenger inbox or an Instagram Direct inbox only when you run <code>thth dm</code>; "
        "what it shows is printed for you and not saved, a reply is sent only after your two-step confirmation and "
        "inside the platform's messaging window, and THTH never replies or messages anyone on its own.</li>")
    add("  <li>Ads run on your own Meta ad account. THTH creates everything paused and starts delivery only after "
        "your two-step confirmation (and, where the operator requires it, an approval number). What is spent, and "
        "its consequences, are yours; THTH's budget estimate is not an invoice. A platform may stop or limit ads, "
        "permissions or the account at its own judgment, and THTH cannot override that.</li>")
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
        "delete the App Password in your Bluesky settings; for Facebook Pages, Instagram and ad accounts, remove "
        "THTH under Business integrations in your Facebook settings.</p>")

    add("<h2>Contact</h2>")
    add(contact_html("en"))
    add("<h2>Changes</h2>")
    add("<p>This page is generated from the THTH source repository; its history is public there. Revision: adds "
        "Facebook Pages, Instagram, Meta ad accounts and direct messages.</p>")
    add("<h2>Software license</h2>")
    add("<p>The THTH software is open source under the MIT License. That license covers the software; these "
        "terms cover the operator's service.</p>")

    # ---------------------------------------------------------------- 日本語
    add('<h1 id="ja" style="margin-top:56px">THTH — 利用規約</h1>')
    date_ja = esc(TERMS_EFFECTIVE) + " 施行" if TERMS_EFFECTIVE else "未公開の案 — 施行日未設定"
    add(f'<p class="note">{date_ja} · <a href="/privacy/">プライバシーポリシー</a></p>')

    add("<h2>THTH とは</h2>")
    add("<p>THTH は、自分の SNS のアカウント（Threads・Facebook ページ・Instagram のプロ口座・Meta の広告アカウント、"
        "ほかに Bluesky・Mastodon・X）の投稿を公開・管理するためのコマンドラインツールで、多くの人は自分の "
        "AI アシスタントを通して使います。運営者（gotoq）が、"
        "運営者の管理するサーバーで、運営者が招待した人または確かめた人のために動かしています。招待された利用者は"
        "自分のアカウントを接続し、自分のサーバーを動かしたり自分のアプリを登録したりしません。この規約はその"
        "サービスについてのものです。</p>")

    add("<h2>利用者がすること</h2>")
    add("<ul>")
    add("  <li>自分が管理するアカウントだけを、SNSの画面でアカウントと求められる権限を確かめてから認可する。</li>")
    add("  <li>自分のアカウントで公開・返信・削除されるものには、自分で頼んだときも、接続した AI アシスタントを通して"
        "頼んだときも、利用者が責任を持つ。THTH は内容の善し悪しを判断しない。</li>")
    add("  <li>投稿する各SNSの規約と決まりを守る。</li>")
    add("  <li>著作権・プライバシー・肖像権など、他人の権利を侵さない内容だけを公開する。</li>")
    add("  <li>アカウントのシークレット・API キー・SNSの認証情報は自分だけで持つ。チャット・原稿・issue に貼らない。"
        "キーが漏れたと思ったら https://thth.me/activity で発行し直す（前のキーは使えなくなる）。</li>")
    add("</ul>")

    add("<h2>運営者がすること・しないこと</h2>")
    add("<ul>")
    add("  <li>THTH は、利用者が自分の CLI か AI アシスタントで頼んだときだけ動き、自分から投稿・返信・削除を"
        "しません。どのアカウントにも利用者が決めるガードがあります。投稿の最短間隔、投稿と削除の 1 日の上限、"
        "短時間に操作が多すぎたときの自動停止です。アシスタントはこれを厳しくする向きにしか変えられません。緩める・"
        "止まったアカウントを戻す・API キーを発行する／取り消すのは、利用者が https://thth.me/activity で"
        "アカウントのシークレット を使って行います。</li>")
    add("  <li>認証情報（SNSの token と Bluesky の App Password）は、認可の流れか標準入力で受け取り、運営者の"
        "サーバーのオーナーだけが読めるファイルに置き、ログと画面の出力では伏せます。チャットやコマンドの引数を"
        "通さず、THTH の道具が LLM のセッションに返すこともありません。</li>")
    add("  <li>THTH がページの Messenger や Instagram の Direct の受信箱を読むのは、利用者が <code>thth dm</code> を"
        "打ったときだけです。見せたものは画面に出すだけで保存せず、返事は利用者の二段確認のあとに、SNS の決めた窓の内で"
        "だけ送ります。THTH が自分から返事やメッセージを送ることはありません。</li>")
    add("  <li>広告は利用者自身の Meta の広告アカウントで動きます。THTH はすべて止まった状態（PAUSED）で作り、配信を"
        "始めるのは利用者の二段確認（運営者が求めるときは承認の番号も）のあとだけです。使ったお金とその結果は利用者の"
        "ものです。THTH の予算の見積もりは請求書ではありません。SNS は自らの判断で広告・権限・アカウントを止めたり"
        "制限したりすることがあり、THTH はそれを覆せません。</li>")
    add("  <li>運営者は、いつでもアカウントの利用を止められ、プラザの書き込みを非表示にできます。利用者が求めたときは"
        "止めます。この規約やSNSの規約が守られないとき、SNSが求めたとき、サービスを終えるときにも止めることが"
        "あります。止めるときに前もって知らせないことがあります。</li>")
    add('  <li>データの使い方・保存・削除は、<a href="/privacy/#ja">プライバシーポリシー</a>に書いています。</li>')
    add("</ul>")

    add("<h2>提供の形</h2>")
    add("<p>サービスは無償で、現状のまま提供します。止まる・変わる・使えない時間があり、投稿が遅れたり公開"
        "されなかったりすることがあります。運営者は、サービスが使えること・誤りがないこと・特定の目的に合うことを"
        "保証しません。</p>")

    add("<h2>やめ方</h2>")
    add("<p>やめるときは運営者に連絡してください（連絡先を参照）。運営者は <code>thth account leave</code> で"
        "アカウントの利用を終えます。アカウントを止め、運営者のサーバーのデータを"
        '<a href="/privacy/#ja">プライバシーポリシー</a>のとおり削除します。Mastodon と X は THTH がSNSに token の'
        "失効を求めます。Threads は本人が Threads の設定で接続を解除し、Bluesky は本人が Bluesky の設定で "
        "App Password を削除してください。Facebook ページ・Instagram・広告アカウントは、本人が Facebook の設定"
        "（ビジネス連携）で THTH を外してください。</p>")

    add("<h2>連絡先</h2>")
    add(contact_html("ja"))
    add("<h2>変更</h2>")
    add("<p>このページは THTH のソースリポジトリから生成されており、変更の履歴はそこで公開されています。改訂: "
        "Facebook ページ・Instagram・Meta の広告アカウント・ダイレクトメッセージを足しました。</p>")
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
    en: dict[str, list[str]] = {}  # README.en は 2026-10-01 に書き直し、紹介ページはこの節を使わない
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
