"""**落ちないもの**（設計 v2 §2）を機械で守る。

§2 の表の下半分:

> 原稿本文・返信の本文・返信者の `username`・自分の判断（`verdict`）・台帳
> （accounts）・トークン・利用者 repo のパス。post_id は**ハッシュ**にして落とす。

**約束を文章で書くだけでは守れない。** 上流の台帳に鍵が 1 つ増えただけで静かに
混ざるので、**偽の台帳に `SECRET-…` を仕込んで、outbox を grep する**。
仕込むのは「落ちてはいけない場所」だけ——`text`・`username`・`verdict`・
`repo_dir`・トークン。1 バイトでも出たら落とす。

§9「機械 lint に」・§9「門が泉に依存しないこともテスト」。
"""
from __future__ import annotations

import json
import os

import pytest

from thth import share as share_mod
from thth import topics as topics_mod
from tests.conftest import run_thth, write_queue_file
from tests.helpers.share_ledger import ROOT, build_reply_ledger

SECRET = "SECRET-8f3a21d0c4b7"

# **outbox のどの行にも出てはいけない鍵**（依頼の受け入れ条件そのまま）。
禁止の鍵 = ("text", "username", "verdict", "access_token", "repo_dir")


def _outbox_text() -> str:
    """積んだものを**生のまま**読む（json に直す前。整形で消える可能性を断つ）。"""
    d = share_mod.outbox_dir()
    if not os.path.isdir(d):
        return ""
    out = []
    for name in sorted(os.listdir(d)):
        with open(os.path.join(d, name), encoding="utf-8") as f:
            out.append(f.read())
    return "".join(out)


def _all_keys(node, into: set) -> set:
    if isinstance(node, dict):
        for k, v in node.items():
            into.add(k)
            _all_keys(v, into)
    elif isinstance(node, (list, tuple)):
        for v in node:
            _all_keys(v, into)
    return into


@pytest.fixture
def 仕込んだ台帳(thth_root, isolated_account):
    """**落ちてはいけない場所に全部 `SECRET-…` を入れた**偽の台帳で on にする。"""
    build_reply_ledger(isolated_account, secret=SECRET)
    topics_mod.record(f"中学受験2027", verdict="alive",
                       audience=f"受験する家庭", kind="年度付き", status="ok",
                       by=f"masaru{SECRET}", account=f"acct{SECRET}",
                       note=f"画面で見た{SECRET}")
    # トークンらしきものも置く（`accounts` の token パスは台帳の中）。
    share_mod.set_enabled(True, by=f"masaru{SECRET}")
    share_mod.sync()
    return isolated_account


# ------------------------------------------------------- 仕込んだ目印が出ない

def test_仕込んだSECRETがoutboxに1バイトも出ない(仕込んだ台帳):
    """`text`・`username`・`by`・`account`・`note`・ファイル名に仕込んだ目印。

    **`--by` と `account` にも仕込んである**——観測者の仮名に置き換わる約束
    （§2）が効いていなければ、ここで出る。
    """
    積んだ = _outbox_text()
    assert 積んだ.strip(), "何も積まれていないと、この検査は何も守っていない"
    assert SECRET not in 積んだ, (
        f"落ちてはいけないものが outbox に出ています:\n"
        + "\n".join(ln for ln in 積んだ.splitlines() if SECRET in ln))


def test_outboxのどの行にも禁止の鍵が無い(仕込んだ台帳):
    """鍵の名前で検査する（値が偶然空でも見逃さない・入れ子の奥も見る）。"""
    rows, broken = share_mod.log_rows()
    assert rows and broken == []
    for row in rows:
        keys = _all_keys(row, set())
        混入 = keys & set(禁止の鍵)
        assert not 混入, f"{混入} が行に入っています: {row}"
        # §2「落ちないもの」の残り。
        for k in ("body", "content", "reason", "token", "handle", "user_id",
                   "queue_dir", "replies_dir", "permalink", "url", "by",
                   "account", "note", "post_id"):
            assert k not in keys, f"`{k}` が行に入っています: {row}"


def test_生のpost_idがoutboxに出ない(仕込んだ台帳):
    """§2「post_id は**ハッシュ**にして落とす（permalink に戻せない）」。"""
    積んだ = _outbox_text()
    assert ROOT not in 積んだ, "生の post_id（数字）が出ています"
    assert "at://" not in 積んだ, "生の post_id（AT URI）が出ています"
    # ハッシュのほうは入っている（＝「出さない」で済ませていない）。
    assert share_mod.post_hash(ROOT) in 積んだ


def test_利用者repoのパスがoutboxに出ない(仕込んだ台帳):
    積んだ = _outbox_text()
    assert 仕込んだ台帳["repo_dir"] not in 積んだ
    assert 仕込んだ台帳["name"] not in 積んだ, "アカウント名も台帳のうち（§2）"


def test_自分の判断_verdict_がoutboxに出ない(仕込んだ台帳):
    """「誰がいるか」は共有できるが「合っているか」はプロジェクトのもの（§2）。"""
    積んだ = _outbox_text()
    for v in ("alive", "mismatch", "dead", "unknown"):
        assert f'"{v}"' not in 積んだ, f"verdict（{v}）が出ています"


# ------------------------------------------------- 書く直前の検査が効いている

def test_禁止の鍵を混ぜようとしたら例外で落ちる(thth_root):
    """**loud reject。** 黙って落とさず、黙って通さない。"""
    share_mod.set_enabled(True)
    for 鍵 in 禁止の鍵:
        with pytest.raises(share_mod.ShareError):
            share_mod._append({"schema": "x", 鍵: "なにか"})
    # 入れ子の奥でも見つける。
    with pytest.raises(share_mod.ShareError):
        share_mod._append({"schema": "x", "growth": {"1": {"username": "よそ子"}}})
    # **落としたぶんは 1 バイトも書かれていない。**
    assert share_mod.bytes_on_disk() == 0


def test_生のpost_idを直に渡しても落ちる(thth_root):
    share_mod.set_enabled(True)
    with pytest.raises(share_mod.ShareError):
        share_mod._append({"schema": "x", "なにか": "17912345678901234"})
    with pytest.raises(share_mod.ShareError):
        share_mod._append({"schema": "x", "なにか":
                            "at://did:plc:abc/app.bsky.feed.post/xyz"})


def test_原稿本文の長さのものは通さない(thth_root):
    """audience は自由文だが、**原稿本文が回ってくる経路は残さない**。"""
    share_mod.set_enabled(True)
    with pytest.raises(share_mod.ShareError):
        share_mod._append({"schema": "x", "audience": "あ" * 500})


# ------------------------------------------------------------- off で 0 バイト

def test_offなら仕込んだ台帳があっても0バイト(thth_root, isolated_account):
    """**on にしない限り、台帳が何であっても 1 バイトも積まない**（裁定 §7-3）。"""
    build_reply_ledger(isolated_account, secret=SECRET)
    topics_mod.record("中学受験2027", verdict="alive", audience="受験する家庭",
                       kind="年度付き", status="ok", by="masaru")
    share_mod.sync()
    share_mod.enqueue_observation(topic="中学受験2027", audience="受験する家庭")
    share_mod.enqueue_thread_shape({"post_id": ROOT, "medium": "threads"})

    assert share_mod.bytes_on_disk() == 0
    assert _outbox_text() == ""
    assert not os.path.exists(share_mod.root()), \
        "off なのに `state/share/` が出来ています（仮名も塩も作らない）"


# --------------------------------------------- 門は泉に依存しない（§5・§9）

def test_採集と観測の経路がshareをimportしていない():
    """**積む口は `collect.py` と `topics.py` の外側**（依頼の境界・§5）。

    ここが破れると「share を止めれば採集も止まる」になり、**門が泉に依存する**。

    **import 文だけを見る**（素の `"share" in src` は使えない——Threads の実測に
    `shares` という指標があり、`collect.py` も `measured.py` もその語を持っている。
    文字列一致にすると、無関係な語で赤くなるか、逆に `.replace()` の抜け道で
    黙る）。
    """
    import ast
    門の経路 = ("collect.py", "topics.py", "core.py", "threadthrow.py",
                "select.py", "approval.py", "writeback.py", "sent.py",
                "threadrun.py", "runs.py")
    for name in 門の経路:
        path = os.path.join(os.path.dirname(share_mod.__file__), name)
        if not os.path.exists(path):
            continue
        tree = ast.parse(open(path, encoding="utf-8").read(), filename=path)
        for node in ast.walk(tree):
            もらった = []
            if isinstance(node, ast.Import):
                もらった = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                もらった = [a.name for a in node.names] + [node.module or ""]
            for mod in もらった:
                assert mod.rsplit(".", 1)[-1] not in ("share", "share_cli"), (
                    f"{name} が share を import しています"
                    f"（門が泉に依存しています・§5）")


@pytest.mark.parametrize("壊し方", ["設定がJSONでない", "outboxがファイル",
                                     "仮名が読めない", "置き場が書けない"])
def test_shareの置き場をどう壊してもthrowが通る(thth_root, isolated_account, 壊し方):
    """**share の状態をあらゆる形で壊して、実プロセスの `thth throw` を通す。**

    「壊す」の現実的な形は、モジュールを消すことではなく**置き場が壊れること**
    （手で編集した・ディスクが埋まった・権限が変わった）。どれも投稿の経路には
    1 ミリも関係しないはず——それを**実際に走らせて**確かめる（§5・§9）。
    """
    os.makedirs(share_mod.root(), exist_ok=True)
    if 壊し方 == "設定がJSONでない":
        with open(share_mod.config_path(), "w", encoding="utf-8") as f:
            f.write("{これは JSON ではない")
        # **この試験が本当に何かを壊していること**を先に見せる（壊れていない
        # ものを壊したつもりで緑になるのがいちばん無意味）。
        with pytest.raises(share_mod.ShareError):
            share_mod.is_on()
    elif 壊し方 == "outboxがファイル":
        with open(share_mod.outbox_dir(), "w", encoding="utf-8") as f:
            f.write("ディレクトリのはずの場所にファイルがある")
    elif 壊し方 == "仮名が読めない":
        share_mod.set_enabled(True)
        os.chmod(share_mod.observer_id_path(), 0o000)
    else:
        os.chmod(share_mod.root(), 0o500)

    try:
        write_queue_file(isolated_account["queue_dir"], "a.md")
        r = run_thth(["throw", isolated_account["name"]])
        assert r.returncode == 0, (
            f"share が壊れている（{壊し方}）と throw が落ちます:\n"
            f"{r.stdout}{r.stderr}")
    finally:
        # あと片付け（読めない・書けないまま tmp を残さない）。
        os.chmod(share_mod.root(), 0o700)
        if os.path.exists(share_mod.observer_id_path()):
            os.chmod(share_mod.observer_id_path(), 0o600)
