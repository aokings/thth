#!/usr/bin/env python3
"""`thth.me` の紹介ページ（`callback/public/`）を **repo の正本から生成する**。

設計 v2 §3「ドメイン」: `thth.me` ＝製品の入口（README 相当・導入・`llms.txt`・
認可ページは残す）。その入口に置く 3 つのファイルをここで作る:

| 出力 | 正本 |
|---|---|
| `callback/public/index.html` | `README.en.md` の「何を保証し、何を拒み、何を絶対にしないか」＋ `skills/thth/SKILL.md` の同じ 3 節（日本語） |
| `callback/public/llms.txt` | repo の `llms.txt`（**1 バイトも変えずに写す**） |
| `callback/public/robots.txt` | このスクリプト（認可の受け口だけを索引から外す） |

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
    "AI が書いた下書きを、人が読んで承認してから投稿する道具。"
    "Threads・Bluesky・Mastodon。画面はなく、コマンドと MCP で動く。記録はあなたの git に残る。"
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
        background: var(--soft); border-radius: 10px; padding: 16px; overflow-x: auto; margin: 0 0 12px; }
  pre code { background: none; padding: 0; }
  footer { margin-top: 44px; font-size:.85rem; opacity:.7; }
"""


def build_index(en: dict[str, list[str]], ja: dict[str, list[str]]) -> str:
    """紹介ページ。**訪問者向けの文で、短く**（masaru 2026-09-14「文言が気持ち悪い」）。

    以前は README.en.md と SKILL.md の 3 節を要約せずに写し、日本語と英語を交互に並べ、
    「このページは正本から生成される」「泉はまだ無い」「LLM に選ばせる試験 5/5」のような
    作り手の作業メモまで載せていた。**正本を写す**ことと**読める**ことは別で、訪問者には
    後者だけが要る。内部の語（digest・approval_stale・loud reject・泉）はここでは使わない。
    保証の中身は README.en.md / SKILL.md と食い違わないよう、**同じ事実を平たい言葉で**
    書く（引数 `en`・`ja` は互換のため受けるが、写さない）。
    """
    del en, ja
    esc = lambda s: html_mod.escape(s, quote=True)  # noqa: E731
    parts: list[str] = []
    add = parts.append

    add("<!doctype html>")
    add('<html lang="ja"><head>')
    add('<meta charset="utf-8">')
    add('<meta name="viewport" content="width=device-width, initial-scale=1">')
    add('<meta name="referrer" content="no-referrer">')
    add("<title>THTH — 人が承認してから投稿する道具</title>")
    add(f'<meta name="description" content="{esc(DESCRIPTION)}">')
    add('<meta property="og:title" content="THTH — 人が承認してから投稿する道具">')
    add(f'<meta property="og:description" content="{esc(DESCRIPTION)}">')
    add(f"<style>\n{STYLE}</style>")
    add("</head><body><main>")

    add("<h1>THTH</h1>")
    add('<p class="lead">AI が書いた下書きを、人が読んで承認してから投稿する道具です。'
        "Threads・Bluesky・Mastodon に対応しています。画面はなく、コマンドと MCP で動きます。</p>")

    add("<h2>しくみ</h2>")
    add("<ol>")
    add("  <li>エージェントが下書きを書きます。置き場はあなたの repo の中です。</li>")
    add("  <li>あなたが本文を読んで承認します。承認していない本文は出ません。</li>")
    add("  <li>THTH が投稿し、投稿の ID・返信・閲覧数を同じ repo に記録します。</li>")
    add("</ol>")

    add("<h2>約束</h2>")
    add("<ul>")
    add("  <li>承認していない本文は 1 文字も出ません。承認は、本文を見せてから、その本文の"
        "指紋を渡す 2 段です。承認のあとに本文が変わると、出しません。</li>")
    add("  <li>出したものは全部あなたの git に残ります。THTH のサーバはありません。</li>")
    add("  <li>何も外に送りません。共有の機能は既定で切れていて、入れても送り先はまだありません。</li>")
    add("  <li>DM は読みません。定型文で自動返信もしません。返信の下書きも同じ承認を通ります。</li>")
    add("  <li>数字には件数と期間が付きます。分からないことは分からないと言います。</li>")
    add("  <li>1 回の実行で出すのは 1 件。静かな時間帯と最短間隔を守ります。</li>")
    add("</ul>")

    add("<h2>入れ方</h2>")
    add("<pre><code>pip install thth\n"
        "thth account add my-threads --media threads --project my-project\n"
        "thth doctor my-threads</code></pre>")
    add("<p>Threads は自分の Meta アプリで使います。初回の設定は "
        f'<a href="{GITHUB_BLOB}docs/導入_自分のMetaアプリで動かす.md">導入の手引き</a>'
        "を見てください。Bluesky と Mastodon は "
        f'<a href="{GITHUB_BLOB}docs/導入_Bluesky_2026-09-13.md">Bluesky</a>・'
        f'<a href="{GITHUB_BLOB}docs/導入_Mastodon_2026-09-13.md">Mastodon</a> の手引きがあります。</p>')
    add("<p>MCP サーバと Claude Code 用の skill は <code>pip install thth</code> に同梱です。"
        f'MCP registry では <a href="{esc(REGISTRY_SEARCH)}">io.github.aokings/thth</a> です。</p>')

    add("<h2>English</h2>")
    add('<p class="en">THTH publishes an LLM-drafted post only after a person has read and '
        "approved it. It works with Threads, Bluesky and Mastodon, has no dashboard, and runs "
        "from the command line or as an MCP server. Everything it posts and collects is recorded "
        f'in your own git repository. Nothing leaves your machine. See <a href="{GITHUB_BLOB}README.en.md">README.en.md</a>.</p>')

    add("<h2>リンク</h2>")
    add("<ul>")
    add(f'  <li><a href="{GITHUB}">GitHub</a></li>')
    add(f'  <li><a href="{PYPI}">PyPI</a></li>')
    add(f'  <li><a href="{esc(REGISTRY_SEARCH)}">MCP registry</a></li>')
    add('  <li><a href="/llms.txt">llms.txt</a>（LLM 向けの案内）</li>')
    add("</ul>")

    add("<footer>MIT License (c) 2026 gotoq。このページは外部の CSS・JS・フォント・画像を読み込みません。"
        "<code>thth.me/callback/</code> は Threads の認可の受け口です。</footer>")
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
        (PUBLIC / name).write_text(content, encoding="utf-8")
        print(f"書いた: callback/public/{name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
