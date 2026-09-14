#!/usr/bin/env python3
"""`thth.me` の紹介ページ（`callback/public/`）を **repo の正本から生成する**。

設計 v2 §3「ドメイン」: `thth.me` ＝製品の入口（README 相当・導入・`llms.txt`・
認可ページは残す）。その入口に置く 3 つのファイルをここで作る:

| 出力 | 正本 |
|---|---|
| `callback/public/index.html` | `README.en.md` の「何を保証し、何を拒み、何を絶対にしないか」＋ `skills/thth/SKILL.md` の同じ 3 節（日本語） |
| `callback/public/llms.txt` | repo の `llms.txt`（**1 バイトも変えずに写す**） |
| `callback/public/robots.txt` | このスクリプト（認可の受け口だけを索引から外す） |
| `callback/public/privacy/index.html` | このスクリプト（`build_privacy()`。Meta の App Review が要求するプライバシーポリシー。2026-09-15） |

**手で二重管理しない。** 正本を直したらこのスクリプトを走らせ直す。
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
    add("<p>THTH は自分の PC やサーバで動かします。原稿や記録の保管に、THTH 専用のクラウドサービスは"
        "使いません。</p>")

    add("<h2>AI エージェントから使う</h2>")
    add("<p>MCP サーバと Claude Code 用のスキルを同梱しています。コマンドラインから直接使うほか、"
        "AI エージェントとやり取りしながら下書きや投稿の記録を扱えます。</p>")

    add("<h2>インストールと設定</h2>")
    add("<pre><code>pip install thth\n"
        "thth account add my-threads --media threads --project my-project\n"
        "thth doctor my-threads</code></pre>")
    add("<p>利用する SNS のアカウントと API の設定が必要です。Threads では、自分の Meta アプリを"
        "登録して使います。詳しい手順は、それぞれの導入ガイドをご覧ください。</p>")
    add(f'<p><a href="{guide("導入_自分のMetaアプリで動かす.md")}">Threads の導入ガイド</a> ／ '
        f'<a href="{guide("導入_Bluesky_2026-09-13.md")}">Bluesky の導入ガイド</a> ／ '
        f'<a href="{guide("導入_Mastodon_2026-09-13.md")}">Mastodon の導入ガイド</a></p>')

    add("<h2>English</h2>")
    add('<p class="en">THTH is a command-line tool for drafting and publishing social media posts with AI. '
        "It supports Threads, Bluesky, and Mastodon.</p>")
    add('<p class="en">Review and approve a draft, then let THTH publish it at the scheduled time. '
        "Drafts, published post IDs, replies, and view counts are stored in your own Git repository. "
        "THTH runs on your computer or server and includes an MCP server and a skill for Claude Code.</p>")

    add(f'<p><a href="{GITHUB}">GitHub</a> ／ <a href="{PYPI}">PyPI</a> ／ '
        f'<a href="{esc(REGISTRY_SEARCH)}">MCP Registry</a> ／ <a href="/llms.txt">llms.txt</a></p>')

    add('<footer>Free and open source · MIT License<br>© 2026 gotoq · <a href="/privacy/">プライバシーポリシー / Privacy Policy</a></footer>')
    add("</main></body></html>")
    return "\n".join(parts) + "\n"


PRIVACY_EFFECTIVE = "2026-09-15"


def build_privacy() -> str:
    """プライバシーポリシー（`/privacy/`）。Meta の App Review が「プライバシーポリシー URL」を
    要求する（`docs/手順_AppReview_2026-09-14.md` §1.3・2026-09-15 に masaru の指示で作成）。

    **書いてあることは全部この repo で確かめられる事実だけ**にする（盛らない・約束しない）:
    THTH はサーバを持たない（`README`・設計 v2 §3）、受け口は code を表示するだけで保存も送信もしない
    （`callback/src/index.js`・`wrangler.jsonc` の observability は無効）、外部リソースを読まない
    （`tests/test_site.py`）、トークンは利用者の機械の 600 のファイル（`thth/secrets_fs.py`）。

    英語を先に置く——読むのは Meta の審査担当と海外の利用者。日本語は同じ内容を下に。
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
    add(f'<p class="note">Effective {esc(PRIVACY_EFFECTIVE)} · <a href="#ja">日本語はこの下</a></p>')

    add("<h2>What THTH is</h2>")
    add("<p>THTH is a free, open-source command-line tool for drafting, approving, and publishing "
        "social media posts (Threads, Bluesky, Mastodon). It runs on <strong>your own computer or "
        "server</strong>. There is no THTH cloud service, no THTH account, and no server operated by "
        "THTH that receives or stores your content.</p>")

    add("<h2>What this website (thth.me) does</h2>")
    add("<ul>")
    add("<li><strong>Static pages</strong> (this page and the product introduction). They load no "
        "third-party scripts, fonts, images, or analytics, and set no cookies.</li>")
    add("<li><strong>Authorization receiver</strong> (<code>/callback/</code>). When you authorize THTH "
        "with Threads, Meta redirects your browser here with a one-time authorization code. The page "
        "<strong>only displays that code so you can paste it into your terminal</strong>. It does not store, "
        "log, or transmit the code anywhere, and it removes the code from the address bar immediately. "
        "Server-side logging for this site is disabled. The hosting provider (Cloudflare) may keep "
        "standard aggregate traffic metrics, which THTH does not use.</li>")
    add("<li><strong>Meta callbacks</strong> (<code>/deauthorize</code>, <code>/data-deletion</code>). "
        "These acknowledge Meta's requests and store nothing (see “Deleting your data”).</li>")
    add("</ul>")

    add("<h2>Data THTH accesses on your behalf</h2>")
    add("<p>When <strong>you</strong> authorize THTH for a Threads account and run its commands, THTH "
        "calls the Threads API with your access token to:</p>")
    add("<ul>")
    add("<li>publish posts and replies that <strong>you wrote and explicitly approved</strong> "
        "(a two-step approval is required for every post);</li>")
    add("<li>read your profile (id, username), your posts, replies to them, view/like/follower counts, "
        "and mentions of your account;</li>")
    add("<li>search public posts and locations, look up public profiles, tag a location on a post, "
        "share a post to Instagram, and delete a post — <strong>only when you run that command</strong>.</li>")
    add("</ul>")
    add("<p>THTH does not access any data you did not authorize, and never acts on Threads without "
        "a command from you.</p>")

    add("<h2>Where the data is stored</h2>")
    add("<p>Everything THTH obtains is written to <strong>files on the machine where you run it</strong>: "
        "your own Git repository (drafts, post IDs, replies, counts) and a configuration directory "
        "for access tokens (created with owner-only permissions). Nothing is sent to the author of THTH "
        "or to any third party. The only network destinations are the social media platforms you "
        "configured (for Threads, Meta's <code>graph.threads.net</code>).</p>")

    add("<h2>Sharing</h2>")
    add("<p>THTH does not sell, share, or transfer your data. There is no server on our side that holds it.</p>")

    add("<h2>Retention</h2>")
    add("<p>Data stays on your machine for as long as you keep the files. You can delete them at any time.</p>")

    add('<h2 id="delete">Deleting your data / revoking access</h2>')
    add("<ul>")
    add("<li>To revoke THTH's access to your Threads account, remove it in the Threads app "
        "(Settings → Account → Website permissions) or delete the token file on your machine.</li>")
    add("<li>Because THTH stores nothing on its own servers, there is nothing for us to delete on our side. "
        "Meta's data-deletion callback is answered with a confirmation for that reason. "
        "Data on your own machine is yours to delete.</li>")
    add("</ul>")

    add("<h2>Who uses the Meta app “THTH”</h2>")
    add("<p>The Meta app named “THTH” is operated by the author for accounts that hold a role on that app. "
        "Other people who want to use THTH register <strong>their own</strong> Meta app; their data never "
        "passes through ours.</p>")

    add("<h2>Contact</h2>")
    add(f'<p>Questions about this policy: open an issue at <a href="{GITHUB}/issues">{esc(GITHUB)}/issues</a>. '
        "Operator: gotoq.</p>")

    add("<h2>Changes</h2>")
    add("<p>This page is generated from the THTH source repository; its history is public there.</p>")

    # ---- 日本語 ----
    add('<h1 id="ja" style="margin-top:56px">THTH — プライバシーポリシー</h1>')
    add(f'<p class="note">{esc(PRIVACY_EFFECTIVE)} 施行</p>')

    add("<h2>THTH とは</h2>")
    add("<p>THTH は、SNS（Threads・Bluesky・Mastodon）の投稿を下書きし、承認し、投稿するための無料の"
        "オープンソースのコマンドラインツールです。<strong>あなた自身の PC やサーバ</strong>で動きます。"
        "THTH のクラウドサービスや THTH のアカウントは存在せず、あなたの内容を受け取ったり保存したりする"
        "THTH 側のサーバはありません。</p>")

    add("<h2>このサイト（thth.me）がすること</h2>")
    add("<ul>")
    add("<li><strong>静的なページ</strong>（このページと製品紹介）。第三者のスクリプト・フォント・画像・"
        "解析ツールを読み込まず、Cookie も使いません。</li>")
    add("<li><strong>認可の受け口</strong>（<code>/callback/</code>）。Threads で THTH を認可すると、Meta が"
        "使い捨ての認可コードを付けてブラウザをここへ戻します。このページは<strong>そのコードをターミナルに"
        "貼れるよう表示するだけ</strong>です。どこにも保存・記録・送信せず、アドレスバーからも直ちに消します。"
        "サーバ側のログは無効にしています。ホスティング事業者（Cloudflare）が標準的な集計トラフィック指標を"
        "保持することはありますが、THTH はそれを使いません。</li>")
    add("<li><strong>Meta 向けの応答</strong>（<code>/deauthorize</code>・<code>/data-deletion</code>）。"
        "Meta からの通知に応答するだけで、何も保存しません（「データの削除」参照）。</li>")
    add("</ul>")

    add("<h2>THTH があなたの代わりに扱うデータ</h2>")
    add("<p><strong>あなた</strong>が Threads アカウントで THTH を認可し、コマンドを実行したとき、THTH は"
        "あなたのアクセストークンで Threads API を呼び、次のことをします。</p>")
    add("<ul>")
    add("<li><strong>あなたが書き、明示的に承認した</strong>投稿・返信を出す（すべての投稿に二段階の承認が要ります）</li>")
    add("<li>あなたのプロフィール（id・ユーザー名）、投稿、その返信、閲覧数・いいね数・フォロワー数、"
        "あなたへの言及を読む</li>")
    add("<li>公開投稿と場所の検索、公開プロフィールの参照、投稿への場所の付与、Instagram への共有、投稿の削除"
        "——<strong>あなたがそのコマンドを実行したときだけ</strong></li>")
    add("</ul>")
    add("<p>認可していないデータには触れず、あなたの指示なしに Threads 上で何かをすることはありません。</p>")

    add("<h2>データの置き場所</h2>")
    add("<p>THTH が取得したものはすべて、<strong>THTH を動かしている機械のファイル</strong>に書かれます。"
        "あなた自身の Git リポジトリ（下書き・投稿 ID・返信・数）と、アクセストークンの設定ディレクトリ"
        "（所有者だけが読める権限で作られます）です。THTH の作者にも第三者にも送られません。通信先は、"
        "あなたが設定した SNS だけです（Threads なら Meta の <code>graph.threads.net</code>）。</p>")

    add("<h2>第三者提供</h2>")
    add("<p>THTH はあなたのデータを販売・共有・譲渡しません。こちら側にデータを持つサーバがありません。</p>")

    add("<h2>保存期間</h2>")
    add("<p>あなたがファイルを残す限り、あなたの機械に残ります。いつでも削除できます。</p>")

    add("<h2>データの削除・認可の取り消し</h2>")
    add("<ul>")
    add("<li>Threads アカウントへの THTH のアクセスを取り消すには、Threads アプリ（設定 → アカウント → "
        "ウェブサイトの許可）で外すか、あなたの機械のトークンファイルを削除してください。</li>")
    add("<li>THTH は自分のサーバに何も保存していないので、こちら側で削除するものはありません。Meta からの"
        "データ削除要求には、その理由で確認応答を返します。あなたの機械のデータはあなたが削除します。</li>")
    add("</ul>")

    add("<h2>Meta アプリ「THTH」を使うのは誰か</h2>")
    add("<p>「THTH」という名前の Meta アプリは、作者が、そのアプリで役割を持つアカウントのために運用しています。"
        "THTH を使いたい他の方は<strong>ご自身の</strong> Meta アプリを登録して使うので、その方のデータが"
        "こちらのアプリを通ることはありません。</p>")

    add("<h2>連絡先</h2>")
    add(f'<p>このポリシーについての質問は <a href="{GITHUB}/issues">{esc(GITHUB)}/issues</a> へ。運用者: gotoq。</p>')

    add("<h2>変更</h2>")
    add("<p>このページは THTH のソースリポジトリから生成されており、変更の履歴はそこで公開されています。</p>")

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
