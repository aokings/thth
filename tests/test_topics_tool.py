"""トピックの下調べを記録して使い回す（masaru 指摘 2026-09-10）。

> とにかくトピックが threads 新参者にとってはとても重要だと考えています
> これを thth のもつ強力なツールとしたいところです

実測: フォロワー 0 で `中学受験` は 202〜574 views、弱いトピックは 1 view。
**効き目が約 400 倍違う唯一の入口**。だが「誰がいる場所か」は THTH からは
分からない（検索の権限が降りてこない）。**見に行くのは人、覚えておくのは THTH。**
"""
from __future__ import annotations

from tests.conftest import approve_via_cli, run_thth, write_queue_file
from thth import topics as topics_mod


def test_下調べを残して読み出せる(thth_root):
    topics_mod.record("精製", verdict="mismatch", audience="レアアース・重加工",
                       by="claude（THTH セッション）")
    row = topics_mod.latest("精製")
    assert row["verdict"] == "mismatch"
    assert row["audience"] == "レアアース・重加工"


def test_後の確認が前を上書きせず履歴に残る(thth_root):
    topics_mod.record("苦味", verdict="dead", by="1 回目")
    topics_mod.record("苦味", verdict="alive", audience="味覚・体調の話", by="2 回目")

    assert topics_mod.latest("苦味")["verdict"] == "alive"   # 最後の確認が効く
    history = [r for r in topics_mod.load()["checks"] if r["topic"] == "苦味"]
    assert [r["by"] for r in history] == ["1 回目", "2 回目"], "履歴が消えた"


def test_未確認のトピックはそう言う(thth_root):
    line = topics_mod.verdict_line("まだ調べていない語")
    assert "未確認" in line


def test_承認の一段目にトピックの判定が出る(isolated_account):
    """**この 1 件がこの道具の理由。** 知識が会話の中にしか無ければ、
    明日ほかのセッションが同じ場所でつまずく。"""
    topics_mod.record("精製", verdict="mismatch", audience="レアアース・重加工",
                       by="claude（THTH セッション）")
    path = write_queue_file(isolated_account["queue_dir"], "a.md", fm_overrides={
        "status": "draft", "approved_sha": None, "topic": "精製"})

    first = run_thth(["approve", path])
    assert "不一致" in first.stdout, first.stdout
    assert "レアアース・重加工" in first.stdout, first.stdout


def test_確認済みのトピックもそう言う(isolated_account):
    topics_mod.record("中学受験", verdict="alive", audience="受験親",
                       by="claude（THTH セッション）")
    path = write_queue_file(isolated_account["queue_dir"], "a.md", fm_overrides={
        "status": "draft", "approved_sha": None, "topic": "中学受験"})
    first = run_thth(["approve", path])
    assert "合っています" in first.stdout, first.stdout


def test_planは未確認に何本賭かっているかを言う(isolated_account):
    topics_mod.record("中学受験", verdict="alive", by="テスト")
    for i, topic in enumerate(["中学受験", "精製", "精製", "六大茶類"]):
        write_queue_file(isolated_account["queue_dir"], f"{i}.md", fm_overrides={
            "status": "draft", "approved_sha": None, "topic": topic},
            body=f"## threads\n\n{i} 本目\n")

    result = run_thth(["topics", isolated_account["name"], "--plan"])
    assert result.returncode == 0, result.stderr
    # 未確認（精製 2 本 ＋ 六大茶類 1 本）が先に、本数付きで出る
    assert "未確認のトピックに 3 本が賭かっています" in result.stdout, result.stdout
    assert result.stdout.index("[精製]") < result.stdout.index("[中学受験]"), \
        "賭かっている本数の多い未確認が先頭に来ていない"


def test_noteはbyを省くと記録しない(thth_root):
    result = run_thth(["topics", "--note", "何か", "--verdict", "alive"])
    assert result.returncode == 1
    assert "--by" in result.stderr
