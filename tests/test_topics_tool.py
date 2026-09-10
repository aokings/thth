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


# --- 型ごとの学習（masaru 提案 2026-09-10「回していくうちに成功が蓄積されていく」）

def test_型ごとに実測がまとまる(thth_root):
    """**個々の語の当たり外れは次の語選びに使えないが、型ごとの傾向なら使える。**"""
    topics_mod.record("中学受験", verdict="alive", kind="行動", by="テスト")
    topics_mod.record("学校説明会", verdict="alive", kind="行動", by="テスト")
    topics_mod.record("精製", verdict="mismatch", kind="専門語", by="テスト")
    topics_mod.record("六大茶類", verdict="mismatch", kind="専門語", by="テスト")

    rows = topics_mod.learned({"中学受験": [574, 202, 368],
                               "学校説明会": [400],
                               "精製": [3],
                               "六大茶類": [5]})
    by_kind = {r["kind"]: r for r in rows}

    assert by_kind["行動"]["topics"] == 2
    assert by_kind["行動"]["posts_measured"] == 4
    # 4 本のときは上側の中央値（実際に観測した値をそのまま出す・補間しない）
    assert by_kind["行動"]["views_median"] == 400
    assert by_kind["専門語"]["views_median"] == 5
    assert by_kind["専門語"]["mismatch"] == 2
    # 効いている型が先頭に来る
    assert rows[0]["kind"] == "行動", [r["kind"] for r in rows]


def test_実測がまだ無い型も数える(thth_root):
    """「試したが数はこれから」が分かる（0 と混ぜない）。"""
    topics_mod.record("チョコレート", verdict="alive", kind="一般名詞", by="テスト")
    rows = topics_mod.learned({})
    assert rows[0]["topics"] == 1
    assert rows[0]["views_median"] is None
    assert rows[0]["posts_measured"] == 0


def test_知らない型は受け付けない(thth_root):
    import pytest
    with pytest.raises(ValueError):
        topics_mod.record("何か", verdict="alive", kind="でたらめ", by="テスト")


def test_承認の一段目に型も出る(isolated_account):
    topics_mod.record("精製", verdict="mismatch", kind="専門語",
                       audience="レアアース・重加工", by="テスト")
    path = write_queue_file(isolated_account["queue_dir"], "a.md", fm_overrides={
        "status": "draft", "approved_sha": None, "topic": "精製"})
    first = run_thth(["approve", path])
    assert "［専門語］" in first.stdout, first.stdout
