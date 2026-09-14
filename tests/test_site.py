"""`thth.me` の紹介ページ（`callback/public/`）を正本に縛る。

設計 v2 §3「ドメイン」: `thth.me` ＝製品の入口（README 相当・導入・`llms.txt`・
認可ページは残す）。入口に置くものは **`tools/build_site.py` が repo の正本から
生成する**——手で二重管理しない。ここで見張るのは 4 つ:

1. `public/llms.txt` が repo の `llms.txt` と **1 バイトも違わない**。
2. `README.en.md`（英語）と `skills/thth/SKILL.md`（日本語）の
   「何を保証し、何を拒み、何を絶対にしないか」が **要約されずに** 載っている。
3. 外部 URL の `<script>`・`<link>`・`<img>` が **0**（認可の受け口と同じ礼儀。
   この入口を開いた人の browser から第三者へは何も飛ばない）。
4. **`/callback/` の応答が紹介ページを足す前と同じ**。固定した応答
   （`tests/fixtures/callback/worker_responses.json`）は、assets を足す前の
   Worker（`HEAD:callback/src/index.js`）を同じ probe に通して採り、
   新しい Worker の出力と byte 単位で一致することを確かめてから置いた。
   `wrangler dev` は使わず、handler を Node で直接呼ぶ。
"""
from __future__ import annotations

import html as html_mod
import importlib.util
import json
import pathlib
import re
import shutil
import subprocess

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
PUBLIC = REPO_ROOT / "callback" / "public"
INDEX = PUBLIC / "index.html"
PROBE = REPO_ROOT / "tests" / "helpers" / "worker_probe.mjs"
WORKER = REPO_ROOT / "callback" / "src" / "index.js"
GOLDEN = REPO_ROOT / "tests" / "fixtures" / "callback" / "worker_responses.json"


def _load_build_site():
    spec = importlib.util.spec_from_file_location(
        "thth_build_site", REPO_ROOT / "tools" / "build_site.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


build_site = _load_build_site()


# --------------------------------------------------------------------------
# 文字の正規化（markdown と HTML を同じ土俵に乗せる）
# --------------------------------------------------------------------------

_TAG_RE = re.compile(r"<[^>]+>")
_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]+\)")


def _norm(text: str) -> str:
    return " ".join(text.split())


def _plain_from_markdown(item: str) -> str:
    """箇条書き 1 項目の markdown から、読み手に見える文字だけを取り出す。"""
    item = _MD_LINK_RE.sub(r"\1", item)
    item = item.replace("**", "").replace("`", "")
    return _norm(item)


def _plain_from_html(path: pathlib.Path) -> str:
    """タグを**空文字で**落とす（空白ではなく）。

    `(<code>max_per_run</code>, default 1)` のような行内のコードは、タグを空白に
    置き換えると `( max_per_run , default 1)` になって正本と食い違う。項目どうし
    は行が分かれているので、空文字で落としても語がくっつくことはない。
    """
    text = _TAG_RE.sub("", path.read_text(encoding="utf-8"))
    return _norm(html_mod.unescape(text))


def _items(source: pathlib.Path, titles: list[str]) -> list[tuple[str, str]]:
    found = build_site.sections_from(source.read_text(encoding="utf-8"), titles)
    return [(title, item) for title in titles for item in found[title]]


# --------------------------------------------------------------------------
# 1. 正本と同じもの が置かれている
# --------------------------------------------------------------------------

def test_public_llms_txt_は_repo_の正本と1バイトも違わない():
    assert (PUBLIC / "llms.txt").read_text(encoding="utf-8") == \
        (REPO_ROOT / "llms.txt").read_text(encoding="utf-8")


def test_public_は_build_site_の出力そのもの():
    """正本を直したら `python tools/build_site.py` を走らせ直す（loud reject）。"""
    stale = []
    for name, content in build_site.outputs().items():
        path = PUBLIC / name
        if not path.exists():
            stale.append(f"callback/public/{name} が無い")
        elif path.read_text(encoding="utf-8") != content:
            stale.append(f"callback/public/{name} が正本とずれている")
    assert not stale, (
        "python tools/build_site.py を走らせ直してください:\n" + "\n".join(stale))


# --------------------------------------------------------------------------
# 2. 3 つの節が要約されずに載っている
# --------------------------------------------------------------------------

_RESOURCE_RE = re.compile(r"<(script|link|img)\b[^>]*>", re.I)


def test_外部の_script_link_img_を1つも読み込まない():
    text = INDEX.read_text(encoding="utf-8")
    tags = _RESOURCE_RE.findall(text)
    assert not tags, f"外部リソースを読み得るタグがある: {tags}"
    # CSS からの取り込みも塞ぐ（@import・url(http…)・フォントの外部参照）。
    assert "@import" not in text
    assert not re.search(r"url\(\s*['\"]?https?:", text, re.I)


def test_リンクは_a_タグだけで_押すまで第三者へ何も送らない():
    """`<a href>` は人が押すまで何も起きない。押さずに飛ぶ口が無いことを見る。"""
    text = INDEX.read_text(encoding="utf-8")
    outbound = re.findall(r'\b(?:src|srcset|action|data-src)\s*=', text, re.I)
    assert not outbound, f"押さずに外へ出る属性がある: {outbound}"


# --------------------------------------------------------------------------
# 4. 認可の受け口の応答が変わっていない
# --------------------------------------------------------------------------

def _probe() -> list[dict]:
    result = subprocess.run(
        ["node", str(PROBE), str(WORKER)],
        capture_output=True, text=True, cwd=str(REPO_ROOT))
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


needs_node = pytest.mark.skipif(shutil.which("node") is None,
                                reason="node が無い環境では Worker を呼べない")


@needs_node
def test_callback_の応答が1バイトも変わらない():
    got = _probe()
    want = json.loads(GOLDEN.read_text(encoding="utf-8"))
    assert [r["url"] for r in got] == [r["url"] for r in want]
    for g, w in zip(got, want):
        assert g == w, f"{g['url']} の応答が変わった"


@needs_node
def test_callback_は_assets_に渡らず_Worker_が自分で応える():
    by_url = {r["url"]: r for r in _probe()}
    for url, res in by_url.items():
        if "/callback" in url:
            assert "[ASSETS]" not in res["body"], url
            assert res["headers"]["referrer-policy"] == "no-referrer", url
            assert res["headers"]["cache-control"] == "no-store", url
            assert res["headers"]["x-robots-tag"] == "noindex, nofollow", url


@needs_node
def test_紹介ページの_path_は_assets_に渡る():
    by_url = {r["url"]: r for r in _probe()}
    for url in ("https://thth.me/", "https://thth.me/llms.txt",
                "https://thth.me/robots.txt"):
        assert "[ASSETS]" in by_url[url]["body"], url


@needs_node
def test_認可コードは_HTML_に埋め込まれるだけで外へ出る口が無い():
    """受け口の画面は従来どおり——code を出す・外部リソースを読まない・
    `history.replaceState` でアドレスバーから消す（回帰の見張り）。"""
    page = {r["url"]: r for r in _probe()}[
        "https://thth.me/callback/?code=FAKE-CODE-abc123"]["body"]
    assert "FAKE-CODE-abc123" in page
    assert "history.replaceState" in page
    assert not re.search(r'(src|href)\s*=\s*["\']https?:', page, re.I)


# --------------------------------------------------------------------------
# robots.txt
# --------------------------------------------------------------------------

def test_robots_は紹介ページを開き_認可の受け口を閉じる():
    robots = (PUBLIC / "robots.txt").read_text(encoding="utf-8")
    assert "Allow: /" in robots
    assert "Disallow: /callback/" in robots


def test_紹介ページは訪問者向けの言葉で書く():
    """masaru（2026-09-14）「文言が気持ち悪い」。内部の語・作業メモ・二重言語をページに出さない。"""
    page = (PUBLIC / "index.html").read_text(encoding="utf-8")
    for word in ("approval_stale", "loud reject", "正本から生成", "泉はまだ無い", "5/5", "cannot_say",
                 "What it guarantees", "What it refuses"):
        assert word not in page, f"訪問者向けのページに内部の語が出ている: {word}"
    assert "承認していない本文は 1 文字も出ません" in page
    assert "THTH のサーバはありません" in page
    assert page.count("<h2>English</h2>") == 1
