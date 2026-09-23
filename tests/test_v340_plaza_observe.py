"""施策の広場 第 3 段——観測の付与と媒体をまたぐ比較（設計 3.4.0 §2・§5・§6）。

見るのは:
  - measure の観測は**道具が付ける**: 宣言ごとに `study_report.answer()` と同じ計算
    （両群の分母・`observational_difference`・欠測は null と理由）。
  - 媒体をまたぐ比較の表: 列 = 媒体、行 = 指標。取れない列は null と理由。
  - 観測は置いた時点と更新時点の値を持つ（履歴・置いた時点は残る）。
  - **道具が付けた数字だけを「観測」と呼ぶ**。人や LLM が本文に書いた数字は「本文」の欄。
  - 宣言は同じ持ち主の account だけ。
  - 自分の投稿は先頭 60 字まで（送った記録から）。
"""
from __future__ import annotations

import datetime
import json

import pytest

from thth import accounts, cli, jst, plaza, sent, study_report
from tests.test_analytics_comparison import NOW, seed
from tests.test_v340_plaza_store import owners, post, viewer  # noqa: F401  (fixture)

DECIDED = NOW - datetime.timedelta(days=7)


def declaration(account, *, baseline, changed, status="adopted", decl_id=None):
    decision = ({"status": "adopted", "by": "kopicha", "at": jst.iso(DECIDED)}
                if status == "adopted" else {"status": "proposed"})
    return {"schema_version": 1, "id": decl_id or f"question-opening-{account}",
            "account": account, "hypothesis": "冒頭を問いにすると返信が増える",
            "change": "1 行目を問いにした", "decision": decision,
            "baseline_post_ids": list(baseline), "changed_post_ids": list(changed)}


def seed_medium(owners, account, *, before, after, prefix):
    """前後 2 本ずつ（views の値を変える）。"""
    a = owners[account]
    for i, value in enumerate(before):
        seed(a, f"{prefix}-b{i}", DECIDED - datetime.timedelta(days=3, hours=i), value=value)
    for i, value in enumerate(after):
        seed(a, f"{prefix}-a{i}", DECIDED + datetime.timedelta(days=1, hours=i), value=value)
    return ([f"{prefix}-b{i}" for i in range(len(before))],
            [f"{prefix}-a{i}" for i in range(len(after))])


@pytest.fixture
def measured(owners):
    threads = seed_medium(owners, "kopicha-threads", before=[10, 12], after=[20, 24], prefix="th")
    bsky = seed_medium(owners, "kopicha-bsky", before=[5, 5], after=[5, 5], prefix="bs")
    return {"kopicha-threads": threads, "kopicha-bsky": bsky}


def post_measure(measured, *, extra=(), now=NOW, body="3 媒体で 1 週間試した", **kwargs):
    declarations = [declaration(name, baseline=ids[0], changed=ids[1])
                    for name, ids in measured.items()] + list(extra)
    return post(kind="measure", title=kwargs.pop("title", "冒頭を問いにする"), body=body,
                declarations=declarations, min_n=1, now=now, **kwargs)


def test_観測はstudy_reportと同じ計算(measured):
    result = post_measure(measured)
    assert result["observed"] and result["n_targets"] == 2
    assert result["evidence_level"] == "observed"
    shown = plaza.show(result["plaza_id"], viewer("kopicha-threads"))
    columns = {c["account"]: c for c in shown["observation"]["latest"]["columns"]}
    assert shown["observation"]["source"] == "tool"
    assert shown["observation"]["interpretation"] == "observational_difference"
    for name, ids in measured.items():
        expected = study_report.answer(None, min_n=1, now=NOW,
                                       verified_declaration=declaration(name, baseline=ids[0],
                                                                        changed=ids[1]))
        column = columns[name]
        assert column["observed"] is True and column["reason"] is None
        for metric, entry in expected["comparison"].items():
            assert column["metrics"][metric]["observational_difference"] == entry["absolute_median_change"]
            assert column["metrics"][metric]["reason"] == entry["reason"]
        base = expected["observations"]["baseline"]
        assert column["denominators"]["baseline"] == {
            "requested": base["n_requested"], "total": base["n_total"], "eligible": base["n_eligible"],
            "missing": base["n_missing"], "excluded": base["n_excluded"]}
    assert columns["kopicha-threads"]["metrics"]["views"]["observational_difference"] == 11
    assert columns["kopicha-bsky"]["metrics"]["views"]["observational_difference"] == 0


def test_媒体をまたぐ比較の表_欠測はnullと理由(measured):
    proposed = declaration("kopicha-mstdn", baseline=[], changed=[], status="proposed")
    result = post_measure(measured, extra=[proposed])
    table = plaza.show(result["plaza_id"], viewer("kopicha-bsky"))["comparison"]
    assert [c["medium"] for c in table["columns"]] == ["threads", "bluesky", "mastodon"]
    assert table["source"] == "tool" and table["interpretation"] == "observational_difference"
    views = table["rows"]["views"]
    assert [cell["observational_difference"] for cell in views] == [11, 0, None]
    assert views[2]["reason"] == "proposed_not_adopted"
    assert views[0]["baseline_n_eligible"] == 2 and views[0]["changed_n_eligible"] == 2
    # 両群の分母が足りない指標は差を出さない（study-report と同じ）。
    reposts = table["rows"]["reposts"]
    assert reposts[0]["observational_difference"] is None
    assert reposts[0]["reason"] == "insufficient_samples_in_one_or_both_periods"


def test_人向けの比較の表(measured, capsys):
    result = post_measure(measured)
    assert cli.main(["plaza", "show", result["plaza_id"], "--as", "kopicha"]) == 0
    out = capsys.readouterr().out
    assert "[媒体をまたぐ比較] 観測（道具が付けた数字）・観測上の差（因果ではない）" in out
    assert "  指標 | threads | bluesky" in out
    assert "  views | +11（n=2/2） | 0（n=2/2）" in out
    assert "印: 観測あり（道具が付けた）" in out


def test_宣言は同じ持ち主のaccountだけ(measured, owners):
    other = declaration("other-threads", baseline=["x"], changed=["y"])
    with pytest.raises(plaza.PlazaError, match="^declaration_out_of_scope$"):
        post_measure(measured, extra=[other])
    with pytest.raises(plaza.PlazaError, match="^invalid_declaration$"):
        post_measure(measured, extra=[{"schema_version": 2}])


def test_持ち主でない投稿IDは除外の理由つきで残る(measured, owners):
    ids = measured["kopicha-threads"]
    decl = declaration("kopicha-threads", baseline=ids[0], changed=ids[1] + ["someone-else-post"])
    result = post(kind="measure", title="他人の ID 入り", body="b", declarations=[decl],
                  min_n=1, now=NOW)
    column = plaza.show(result["plaza_id"], viewer("kopicha-threads"))["observation"]["latest"]["columns"][0]
    assert {"group": "changed", "post_id": "someone-else-post",
            "reason": "unknown_or_unowned_id"} in column["excluded"]
    assert column["denominators"]["changed"]["requested"] == 3
    assert column["denominators"]["changed"]["excluded"] == 1


def test_観測は置いた時点と更新時点を持つ(measured, owners):
    result = post_measure(measured)
    plaza_id = result["plaza_id"]
    # 後から台帳の数字が変わる（Threads の後の群が伸びた）。
    seed(owners["kopicha-threads"], "th-a0", DECIDED + datetime.timedelta(days=1), value=40)
    updated = plaza.update(plaza_id, account="kopicha-threads", by="s", refresh=True,
                           viewer=viewer("kopicha-threads"), now=NOW + datetime.timedelta(hours=1))
    assert updated["refreshed"] and updated["n_observations"] == 2
    shown = plaza.show(plaza_id, viewer("kopicha-threads"))["observation"]
    assert shown["first"]["trigger"] == "posted" and shown["latest"]["trigger"] == "refresh"
    first = {c["account"]: c for c in shown["first"]["columns"]}
    latest = {c["account"]: c for c in shown["latest"]["columns"]}
    assert first["kopicha-threads"]["metrics"]["views"]["observational_difference"] == 11
    assert latest["kopicha-threads"]["metrics"]["views"]["observational_difference"] == 21
    assert shown["changed_since_first"] is True and shown["n_history"] == 2


def test_観測の履歴は置いた時点を残して詰める(measured, monkeypatch):
    monkeypatch.setattr(plaza, "OBSERVATION_HISTORY_MAX", 3)
    result = post_measure(measured)
    for hour in range(1, 6):
        plaza.update(result["plaza_id"], account="kopicha-threads", by="s", refresh=True,
                     viewer=viewer("kopicha-threads"), now=NOW + datetime.timedelta(hours=hour))
    record = json.loads((plaza.STORE.get(result["plaza_id"]) and
                         json.dumps(plaza.STORE.get(result["plaza_id"]))))
    triggers = [(o["trigger"], o["at"]) for o in record["observations"]]
    assert len(triggers) == 3 and triggers[0][0] == "posted"
    assert triggers[-1][1] == jst.iso(NOW + datetime.timedelta(hours=5))


def test_本文の数字は観測と呼ばない(owners, capsys):
    result = post(title="夜より朝", body="朝は返信が 3倍 になった。views は 120 件")
    shown = plaza.show(result["plaza_id"], viewer("kopicha-threads"))
    assert shown["observation"] is None and shown["comparison"] is None
    assert shown["observation_reason"] == "not_a_measure"
    assert shown["text_numbers"] == {"source": "text", "verified": False, "values": ["3倍", "120 件"]}
    assert shown["evidence_level"] == "stated"
    assert cli.main(["plaza", "show", result["plaza_id"], "--as", "kopicha-threads"]) == 0
    out = capsys.readouterr().out
    body_part, observed_part = out.split("[観測（道具が付けた数字）]")
    assert "本文に書かれた数字（観測ではない）: 3倍・120 件" in body_part
    assert "3倍" not in observed_part and "120" not in observed_part


def test_施策でも本文の数字は観測の欄に入らない(measured):
    result = post_measure(measured, body="うちの感覚では 5倍 伸びた")
    shown = plaza.show(result["plaza_id"], viewer("kopicha-threads"))
    assert shown["text_numbers"]["values"] == ["5倍"]
    observed = json.dumps(shown["observation"], ensure_ascii=False)
    assert "5倍" not in observed
    assert shown["observation"]["label"] == "観測（道具が付けた数字）"


def test_自分の投稿は先頭60字まで(measured, owners):
    text = "朝の一杯は問いから始めたい。" * 10
    sent.write(accounts.state_dir_for("kopicha-threads"), post_id="th-a0", text=text,
               body_hash="0" * 8, sent_at=jst.iso(DECIDED + datetime.timedelta(days=1)))
    result = post_measure(measured)
    column = next(c for c in plaza.show(result["plaza_id"], viewer("kopicha-threads"))
                  ["observation"]["latest"]["columns"] if c["account"] == "kopicha-threads")
    refs = {row["post_id"]: row for row in column["posts"]}
    # observe の本文の見せ方と同じ（先頭 60 字＋省略の印）。
    assert len(refs["th-a0"]["preview"].rstrip("…")) <= plaza.PREVIEW_CHARS
    assert refs["th-a0"]["preview"].startswith("朝の一杯は問いから")
    assert text not in json.dumps(column, ensure_ascii=False)
    assert refs["th-b0"]["preview"] is None and refs["th-b0"]["permalink"] is None


def test_CLIの宣言ファイル(measured, owners, tmp_path, capsys, frozen_now_jst):
    frozen_now_jst(NOW)
    paths = []
    for name, ids in measured.items():
        path = tmp_path / f"{name}.json"
        path.write_text(json.dumps(declaration(name, baseline=ids[0], changed=ids[1]),
                                   ensure_ascii=False), encoding="utf-8")
        paths += ["--declaration", str(path)]
    body = tmp_path / "b.txt"
    body.write_text("2 媒体で試した", encoding="utf-8")
    assert cli.main(["plaza", "post", "kopicha-threads", "--kind", "measure", "--title", "問い",
                     "--body-file", str(body), "--scope", "Threads と Bluesky の朝",
                     "--how", "thth study-report question.json", "--min-n", "1",
                     *paths, "--by", "s", "--json"]) == 0
    posted = json.loads(capsys.readouterr().out)
    assert posted["observed"] and posted["n_targets"] == 2
    broken = tmp_path / "broken.json"
    broken.write_text("{", encoding="utf-8")
    assert cli.main(["plaza", "post", "kopicha-threads", "--kind", "measure", "--title", "壊れ",
                     "--body-file", str(body), "--scope", "朝", "--how", "thth x",
                     "--declaration", str(broken), "--by", "s", "--json"]) == 2
    assert json.loads(capsys.readouterr().out)["cannot_say"] == ["invalid_declaration"]


def test_台帳が読めない媒体はnullと理由(measured, monkeypatch):
    real = study_report.answer

    def flaky(path, **kwargs):
        if kwargs["verified_declaration"]["account"] == "kopicha-bsky":
            raise study_report.StudyError("対象accountまたは比較に必要な台帳を読めません")
        return real(path, **kwargs)

    monkeypatch.setattr(study_report, "answer", flaky)
    result = post_measure(measured)
    columns = {c["account"]: c for c in plaza.show(result["plaza_id"], viewer("kopicha-threads"))
               ["observation"]["latest"]["columns"]}
    assert columns["kopicha-bsky"]["observed"] is False
    assert columns["kopicha-bsky"]["reason"] == "ledger_unavailable"
    assert columns["kopicha-bsky"]["metrics"] is None
    assert columns["kopicha-threads"]["observed"] is True
