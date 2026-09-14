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
    "THTH は「LLM が下書きし、人が承認してから」出すための門。"
    "Threads・Bluesky・Mastodon。ヘッドレス（CLI・MCP・skill）。MIT。"
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
  :root { color-scheme: light dark; --bg:#faf9f7; --fg:#1a1a1a; --dim:#5b5b5b;
          --line:rgba(127,127,127,.28); --soft:rgba(127,127,127,.12); --link:#1a4f8a; }
  @media (prefers-color-scheme: dark) {
    :root { --bg:#16161a; --fg:#e9e9ea; --dim:#a3a3a8; --link:#8ab4f8; }
  }
  * { box-sizing: border-box; }
  body { margin:0; background:var(--bg); color:var(--fg);
         font: 16px/1.75 -apple-system, BlinkMacSystemFont, "Hiragino Sans",
               "Noto Sans JP", "Segoe UI", sans-serif; }
  main { max-width: 680px; margin: 0 auto; padding: 48px 20px 72px; }
  h1 { font-size: 1.6rem; letter-spacing:.04em; margin: 0 0 6px; }
  h2 { font-size: 1.05rem; margin: 40px 0 8px; padding-top: 14px;
       border-top: 1px solid var(--line); }
  h3 { font-size: .95rem; margin: 22px 0 6px; }
  p { margin: 0 0 12px; }
  ul { margin: 0 0 12px; padding-left: 1.25em; }
  li { margin: 0 0 8px; }
  a { color: var(--link); }
  .lead { font-size: 1.02rem; margin-bottom: 4px; }
  .en { color: var(--dim); }
  .note { color: var(--dim); font-size: .9rem; }
  code { font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
         font-size: .9em; background: var(--soft); border-radius: 4px; padding: 1px 4px; }
  pre { font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
        font-size: .86rem; line-height:1.6; background: var(--soft);
        border-radius: 8px; padding: 14px 16px; overflow-x: auto; }
  pre code { background: none; padding: 0; }
  footer { margin-top: 44px; padding-top: 14px; border-top: 1px solid var(--line);
           color: var(--dim); font-size: .88rem; }
"""


def build_index(en: dict[str, list[str]], ja: dict[str, list[str]]) -> str:
    esc = lambda s: html_mod.escape(s, quote=True)  # noqa: E731
    parts: list[str] = []
    add = parts.append

    add("<!doctype html>")
    add('<html lang="ja"><head>')
    add('<meta charset="utf-8">')
    add('<meta name="viewport" content="width=device-width,initial-scale=1">')
    add("<title>THTH — 人が承認してから出す門</title>")
    add(f'<meta name="description" content="{esc(DESCRIPTION)}">')
    add('<meta property="og:title" content="THTH — 人が承認してから出す門">')
    add(f'<meta property="og:description" content="{esc(DESCRIPTION)}">')
    add(f"<style>\n{STYLE}</style>")
    add("</head><body><main>")

    add("<h1>THTH</h1>")
    add('<p class="lead">THTH は「LLM が下書きし、<strong>人が承認してから</strong>出す」'
        "ための門。Threads・Bluesky・Mastodon。ヘッドレス（CLI・MCP・skill）。</p>")
    add('<p class="lead en">THTH is the gate that publishes an LLM-drafted post '
        "<strong>only after a person approved it</strong> — Threads, Bluesky, Mastodon; "
        "headless (CLI, MCP, skill).</p>")

    add("<h2>入れ方 / Install</h2>")
    add("<pre><code>pip install thth\n"
        "thth account add my-threads --media threads --project my-project \\\n"
        "  --redirect-uri https://thth.me/callback/\n"
        "thth doctor my-threads</code></pre>")
    add("<p>台帳は repo の外（<code>$THTH_ROOT/accounts/</code>）に置かれ、"
        "<code>production: false</code> で始まる。"
        "Threads の初回設定（自分の Meta アプリを作る）は "
        f'<a href="{GITHUB_BLOB}docs/導入_自分のMetaアプリで動かす.md">'
        "docs/導入_自分のMetaアプリで動かす.md</a>。</p>")
    add("<p><strong>MCP</strong>: registry の <code>io.github.aokings/thth</code>"
        f'（<a href="{esc(REGISTRY_SEARCH)}">registry で引く</a>）。'
        "<code>pip install thth</code> に同梱で、stdio の <code>thth-mcp</code> として動く。"
        "<strong>skill</strong>: <code>skills/thth/SKILL.md</code>（wheel にも入る）。</p>")

    add("<h2>何を保証し、何を拒み、何を絶対にしないか</h2>")
    add('<p class="note">README.en.md と skills/thth/SKILL.md からそのまま写している'
        "（要約していない・このページは正本から生成される）。</p>")
    for title in EN_SECTIONS:
        add(f"<h3>{esc(title)}</h3>")
        add(_ul(en[title]))
    for title in JA_SECTIONS:
        add(f"<h3>{esc(title)}</h3>")
        add(_ul(ja[title]))

    add("<h2>投稿する前に聞く / <code>before_you_post</code></h2>")
    add("<p><code>thth ask before-you-post &lt;account&gt; --topic &lt;語&gt;</code>"
        "（MCP からは <code>before_you_post</code>）は、この語・この型・この時刻帯で"
        "スレッドがどう伸びたかを、<strong>件数と期間つきで</strong>返す。答えるのは"
        "<strong>あなたの手元の台帳だけ</strong>——ネットワークへは出ないし、原稿本文は"
        "問いにも答えにも入らない。件数が閾値（既定 20）に満たない群は中央値を返さず、"
        "<strong>ほとんどが <code>cannot_say</code> になる</strong>。それが正しい答えで、"
        "終了コードは 0。<strong>泉（共有の水）はまだ無い</strong>ので、"
        "<code>provenance.source</code> は <code>local</code> と言う。</p>")

    add("<h2>泉はまだ無い / The spring does not exist yet</h2>")
    add("<p><code>api.thth.me</code>（泉）は<strong>まだ無い</strong>。"
        "<code>thth share</code> は<strong>既定 off</strong>で、on にしても"
        "<strong>送り先が無い</strong>——手元の "
        "<code>$THTH_ROOT/state/share/outbox/</code> に積むだけで、"
        "<code>thth share log</code> で積んだ全部が読める"
        f'（設計 <a href="{GITHUB_BLOB}docs/設計_v2_泉と門_2026-09-13.md">'
        "docs/設計_v2_泉と門_2026-09-13.md</a> §3・§7-3）。</p>")

    add("<h2>LLM に選ばせる試験 / Picked by an LLM</h2>")
    add("<p>名前を伏せた 4 本の候補から、まっさらな Claude が"
        "「人の承認を通して Threads に出す」道具として THTH を選んだのは "
        "<strong>5/5</strong>、<code>pip install</code> 済みの箱で禁じ手ゼロのまま"
        "乾式試験まで着いたのが <strong>3/3</strong>。ただし<strong>承認の条件を外した"
        "対照の問いでも 5/5 だったので、「承認の機能ゆえに選ばれた」とはまだ言えない"
        "</strong>（記録あり・thth-notes）。</p>")

    add("<h2>リンク / Links</h2>")
    add("<ul>")
    add(f'  <li><a href="{GITHUB}">GitHub — aokings/thth</a>'
        "（README・docs・テスト）</li>")
    add(f'  <li><a href="{PYPI}">PyPI — thth</a>（<code>pip install thth</code>・依存 0）</li>')
    add(f'  <li><a href="{esc(REGISTRY_SEARCH)}">MCP registry — io.github.aokings/thth</a></li>')
    add('  <li><a href="/llms.txt">/llms.txt</a>（LLM 向けの入口。repo の <code>llms.txt</code> と同じ）</li>')
    add(f'  <li><a href="{GITHUB_BLOB}LICENSE">LICENSE</a> — MIT (c) 2026 gotoq</li>')
    add("</ul>")

    add("<footer>MIT License (c) 2026 gotoq ・ "
        "<code>thth.me</code> は Threads の認可の受け口（<code>/callback/</code>）も"
        "兼ねている。このページは外部の CSS・JS・フォント・画像を 1 つも読み込まない。"
        "</footer>")
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
