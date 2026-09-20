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
PRIVACY_EFFECTIVE = "2026-09-20"


def build_privacy() -> str:
    """Approved factual wording (Claude, 2026-09-20); no new retention promises."""
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
    add("<h2>Operator and authorization</h2>")
    add("<p>THTH runs authorization and posting management on an operator-managed server for invited accounts. "
        "Users do not need their own VM or Meta app. The operator starts authorization; users review the "
        "account and permissions on the platform and approve. This does not mean that a user connection "
        "page, posting interface, or account exit workflow has all been implemented.</p>")
    add("<h2>Authorization relay</h2>")
    add("<p>The relay uses a Cloudflare Worker and Durable Object. It temporarily holds a registered "
        "authorization code for at most 300 seconds after receipt and within the 600-second session lifetime, "
        "and releases it once to the initiating server. Long-lived tokens, client secrets and PKCE verifiers "
        "are not sent to the relay. The application deletes the code when consumed or expired. Removing "
        "application records does not establish physical erasure from the hosting provider's infrastructure "
        "or backups. Unregistered flows can use the existing return-URL paste fallback.</p>")
    add("<h2>Server records and communication</h2>")
    add("<p>Long-lived credentials reside in owner-only files on the operator's server and authenticate "
        "platform API requests. Drafts, post history and collected records also reside in configured "
        "server locations. Network destinations are not limited to platform APIs: authorization uses "
        "thth.me/Cloudflare, and configured repositories may also receive requests. Static pages include "
        "no third-party scripts or analytics. Worker application observability logs are disabled; this "
        "does not guarantee that no infrastructure records exist.</p>")
    add('<h2 id="delete">Stopping access and deleting data</h2>')
    add("<p>The current <code>/deauthorize</code> and <code>/data-deletion</code> responses do not establish "
        "that server-side accounts or data have been deleted. Contact the operator about stopping access "
        "or deleting data. Automated revocation, deletion and record keeping, including integration with "
        "these callbacks, belong to a later implementation and verification.</p>")
    add("<h2>Contact</h2>")
    add(f'<p>Questions about this policy: open an issue at <a href="{GITHUB}/issues">{esc(GITHUB)}/issues</a>. '
        "Operator: gotoq.</p>")
    add("<h2>Changes</h2>")
    add("<p>This page is generated from the THTH source repository; its history is public there.</p>")

    add('<h1 id="ja" style="margin-top:56px">THTH — プライバシーポリシー</h1>')
    date_ja = esc(PRIVACY_EFFECTIVE) + " 施行" if PRIVACY_EFFECTIVE else "未公開の更新案 — 配布日未設定"
    add(f'<p class="note">{date_ja}</p>')
    add("<h2>運営者と認可</h2>")
    add("<p>THTH は、運営者が管理するサーバで認可と投稿管理を行うソフトウェアです。今回の導入は招待された"
        "アカウントが対象で、利用者が自分の VM や Meta アプリを用意する手順ではありません。運営者が認可を開始し、"
        "利用者は媒体の画面で内容を確認して承認します。利用者向けの接続画面・投稿操作・退出の仕組みがすべて"
        "実装済みという意味ではありません。</p>")
    add("<h2>認可の預かり所</h2>")
    add("<p>認可の預かり所は Cloudflare Worker と Durable Object を使います。登録済みの認可コードを、受付から"
        "最大300秒、かつ開始から最大600秒の範囲で一時的に保持し、認可を開始したサーバへ一度だけ渡します。"
        "長期トークン、client secret、PKCE verifier は預かり所へ送りません。消費または失効時にアプリケーション上の"
        "コードを削除します。Cloudflare 内部のバックアップや物理消去まで保証する説明ではありません。未登録などの"
        "場合は従来の戻り URL を貼る経路になります。</p>")
    add("<h2>サーバの記録と通信</h2>")
    add("<p>長期の認証情報は運営者のサーバの所有者専用ファイルへ保存し、媒体の API への認証に使います。"
        "下書き・投稿履歴・採集した記録等も設定されたサーバ側の置き場にあります。通信先は媒体 API だけとは限らず、"
        "認可時の thth.me/Cloudflare や設定された repository 等があります。静的ページには第三者のスクリプトや解析"
        "ツールを組み込んでいません。Worker のアプリケーション観測ログは無効にしていますが、基盤全体に記録が存在"
        "しないとの保証はしません。</p>")
    add("<h2>停止とデータの削除</h2>")
    add("<p>現在の <code>/deauthorize</code> と <code>/data-deletion</code> の応答は、サーバ内のアカウントやデータを"
        "削除したことを示しません。停止・削除については運営者へ連絡してください。自動の失効・削除・記録とこれらの"
        "URL の接続は、別の版で実装・検証する予定です。</p>")
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
