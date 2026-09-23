"""`thth.me` の紹介ページ（`callback/public/`）を正本に縛る。

設計 v2 §3「ドメイン」: `thth.me` ＝製品の入口（README 相当・導入・`llms.txt`・
認可ページは残す）。入口に置くものは **`tools/build_site.py` が repo の正本から
生成する**——手で二重管理しない。ここで見張るのは 3 つ:

1. `public/llms.txt` が repo の `llms.txt` と **1 バイトも違わない**。
2. 外部 URL の `<script>`・`<link>`・`<img>` が **0**（認可の受け口と同じ礼儀。
   この入口を開いた人の browser から第三者へは何も飛ばない）。
3. **`/callback/` の応答が固定した形と byte 単位で一致する**
   （`tests/fixtures/callback/worker_responses.json`）。`wrangler dev` は
   使わず、handler を Node で直接呼ぶ。

   固定した応答は 2 度採り直している。1 度目は紹介ページ（assets）を足したとき
   ——受け口の振る舞いが 1 バイトも変わっていないことを示すため。2 度目は
   **2026-09-15**——受け口が `code` だけを見せていたのを、`state` を含む
   **戻り URL 全体**に変えたため。`thth auth` は 2026-09-14 のセキュリティ監査
   （P2-4）以降 `state` の照合が通らないと受け付けないので、`code` だけを
   見せる受け口は**必ず断られる形**を人に渡していた（masaru が実際に詰まった）。
   2.12 の削除受付は DO を必要とするため、未設定の status だけ 503 に更新。
   callback/paste の期待 bytes は維持。採り直しは意図した変更のときだけ行う。
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
# 2. 外部リソースを読み込まない
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
# 3. 認可の受け口の応答が変わっていない
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


@needs_node
def test_受け口が出す文字列を_thth_auth_が受け取れる():
    """**受け口が渡すものと、道具が要るものが同じ形か**（2026-09-15）。

    `thth auth` は `state` の照合が通らないと受け付けない（監査 P2-4）。
    受け口が `code` だけを見せていた間、貼っても必ず断られていた——**両側とも
    単体では正しいのに、繋ぐと通らない**。ここは繋ぎ目を実測で押さえる:
    受け口の画面に出る文字列を取り出し、`thth auth` の読み取りに通す。
    """
    from thth import oauth

    page = {r["url"]: r for r in _probe()}[
        "https://thth.me/callback/?code=FAKE-CODE-abc123&state=FAKE-STATE-xyz"]["body"]
    shown = re.search(r'<p class="code" id="c">([^<]*)</p>', page)
    assert shown, "貼るための文字列が画面に無い"
    pasted = html_mod.unescape(shown.group(1))

    assert oauth.extract_code(pasted) == "FAKE-CODE-abc123"
    assert oauth.extract_state(pasted) == "FAKE-STATE-xyz"


# --------------------------------------------------------------------------
# robots.txt
# --------------------------------------------------------------------------

def test_robots_は紹介ページを開き_認可の受け口を閉じる():
    robots = (PUBLIC / "robots.txt").read_text(encoding="utf-8")
    assert "Allow: /" in robots
    assert "Disallow: /callback/" in robots


def test_紹介ページは訪問者向けの言葉で書く():
    """masaru（2026-09-14）「製品紹介の中に開発側の申し送りが混ざっている」。内部の語・作業メモ・
    「まだ無い」の類・実行ログの抜粋をページに出さない。文案は Codex が書き masaru が採ったもの。"""
    page = (PUBLIC / "index.html").read_text(encoding="utf-8")
    for word in ("approval_stale", "loud reject", "正本から生成", "泉", "5/5", "cannot_say",
                 "What it guarantees", "送り先はまだ", "何も変えません", "投稿しました: post_id"):
        assert word not in page, f"訪問者向けのページに開発側の語が出ている: {word}"
    assert "AI と一緒に SNS の投稿を作成・管理するためのコマンドラインツール" in page
    assert "masaru がアプリ・サーバ・台帳を管理します" in page
    assert "利用者が VM や Meta アプリを用意する必要はありません" in page
    assert "X は認可だけ対応" in page
    assert page.count("<h2>English</h2>") == 1


# --------------------------------------------------------------------------
# プライバシーポリシー（/privacy/・2026-09-15・Meta の App Review が要求する）
# --------------------------------------------------------------------------

def test_プライバシーポリシーは正本から生成され_外へ出る口が無い():
    """`docs/手順_AppReview_2026-09-14.md` §1.3「プライバシーポリシー URL が無い」を埋めた。
    書いてあることは repo で確かめられる事実だけ。紹介ページと同じ礼儀（第三者リソース 0）。"""
    page = (PUBLIC / "privacy" / "index.html").read_text(encoding="utf-8")
    assert page == build_site.build_privacy()
    # 読み込む外部リソースは 0（リンク <a href> は可・script/link/img は不可）
    assert not re.search(r'<(script|link|img|iframe)\b[^>]*(src|href)\s*=\s*["\']https?:', page, re.I)
    assert "<script" not in page
    # 英語が先（審査担当が読む）・日本語も同じ内容
    assert page.index("Privacy Policy") < page.index("プライバシーポリシー")
    for must in ("operator-managed server", "at most 300 seconds", "600-second session lifetime",
                 "Long-lived tokens, client secrets and PKCE verifiers", "do not establish",
                 "削除したことを示しません",
                 "https://github.com/aokings/thth/issues"):
        assert must in page, must
    for obsolete in ("does not store, log, or transmit the code", "your own computer or server",
                     "どこにも保存・記録・送信せず", "2026-09-15 施行",
                     # 2026-09-23: 退出（thth account leave）と削除依頼の照合は実装済み。
                     # 「後の版で実装」の文は実態と合わない（Platform Terms §7(a)）。
                     "belong to a later implementation", "別の版で実装・検証する予定",
                     "600,000"):
        assert obsolete not in page
    # 2026-09-23 の書き直し（照合 論点 D）で書いた事実。どれもコードで裏を取った:
    # 返信・言及は相手の username と本文ごと保存（collect.py）・検索結果は保存しない
    # （where_cli.py・threads_read_cli.py）・退出（leave.py）・削除依頼（deletion.py）・
    # 添付（media-object.js）・LLM への受け渡し（mcp/server.py）。英語と日本語の両方。
    for must in ("the other person's username, the text", "相手のユーザー名・本文",
                 "The text and authors of search results are not saved", "検索結果の本文と投稿者は保存しません",
                 "thth account leave", "30 days", "24 hours after it was created",
                 "THTH itself does not send data to an AI model provider",
                 "THTH 自身は AI モデルの提供者へデータを送りません",
                 "unverified, verified or completed", "未照合・照合済み・完了"):
        assert must in page, must
    # 紹介ページから辿れる・robots は開いている
    index = (PUBLIC / "index.html").read_text(encoding="utf-8")
    assert 'href="/privacy/"' in index
    robots = (PUBLIC / "robots.txt").read_text(encoding="utf-8")
    assert "Disallow: /privacy" not in robots


@pytest.mark.parametrize("effective", [None, "2030-01-02"])
def test_privacy_date_is_explicit_not_invented(monkeypatch, effective):
    # Fixture date only: the checked-in default remains unset until deployment.
    monkeypatch.setattr(build_site, "PRIVACY_EFFECTIVE", effective)
    page = build_site.build_privacy()
    if effective is None:
        assert "Unpublished update — deployment date not set" in page
        assert "未公開の更新案 — 配布日未設定" in page
        assert "Effective " not in page and " 施行" not in page
    else:
        assert "Effective " + effective in page and effective + " 施行" in page
        assert "Unpublished update" not in page and "未公開の更新案" not in page
