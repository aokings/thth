"""独立レビュー（2026-09-11・Codex）で出た 6 件の修正。

`tests/test_independent_topic_review.py` はレビュアーが書いた再現テストを
**そのまま**置いてある。こちらは同じ穴を**別の角度から**押さえる。
「1 つの入力で直った」ことと「塞がった」ことは違う。
"""
from __future__ import annotations

import datetime
import json
import os
import subprocess
import sys

import pytest

from tests.conftest import run_thth, write_queue_file
from tests.test_topic_advice import (candidate, evaluate, ids, make_article,
                                      make_context, make_observation)
from thth import topic_advice as advice
from thth import topic_models as models
from thth import topic_store as store

NOW = datetime.datetime.fromisoformat("2026-09-11T12:00:00+09:00")
BODY = "## threads\n\n精製の話。https://example.test/coffee\n"
BIN = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "bin", "thth")


def _run(args, payload=None):
    return subprocess.run(
        [sys.executable, BIN, *args], capture_output=True, text=True,
        env=dict(os.environ),
        input=None if payload is None else json.dumps(payload, ensure_ascii=False))


# --- 指摘 1: 投稿の URL と記事証拠の対応 ------------------------------------

def _evaluate_with(article, *, main_url="https://example.test/coffee"):
    obs = {ids(1)[0]: make_observation("コーヒー")}
    context = make_context(article, obs)
    context["main_article_url"] = main_url
    context["urls_in_post"] = [main_url] if main_url else []
    return advice.evaluate(context, article=article, proposal=None,
                            observations=obs, now=NOW)


def test_別の記事の証拠は主対象と対応しないと言われる():
    article = make_article()
    article = dict(article)
    article["requested_url"] = "https://different.test/unrelated"
    article["final_url"] = "https://different.test/unrelated"
    result = _evaluate_with(article)
    assert result["status"] == "needs_article", result
    assert any("別の記事" in a["reason"] for a in result["required_actions"]), \
        result["required_actions"]


def test_転送は通すが黙って通さない():
    """`requested_url` が投稿の URL なら、`final_url` が動いていても記事は同じ。"""
    article = dict(make_article())
    article["final_url"] = "https://example.test/coffee-2026"
    result = _evaluate_with(article)
    assert result["status"] != "needs_article", result
    assert any("転送" in w for w in result["warnings"]), result["warnings"]


def test_最終URLが投稿のURLでも通す():
    """短縮 URL を取りに行って、投稿の URL に着いた場合。"""
    article = dict(make_article())
    article["requested_url"] = "https://exmpl.test/x"
    result = _evaluate_with(article)
    assert result["status"] != "needs_article", result


def test_フラグメントの違いは同じ記事():
    assert advice.same_article_url("https://a.test/x", "https://a.test/x#s2")
    assert advice.same_article_url("https://a.test/x/", "https://a.test/x")


def test_queryの違いは同じ記事にしない():
    """**query で別の記事を出すサイトがある。** 機械には区別できないので寛容に
    しない——寛容にすると「別の記事を主対象にできる」穴に戻る。
    """
    assert not advice.same_article_url("https://a.test/x",
                                        "https://a.test/x?utm_source=threads")


def test_主対象が決まらなければ先へ進ませない():
    article = make_article()
    result = _evaluate_with(article, main_url=None)
    assert result["status"] == "needs_article"
    assert any(a["type"] == "article_url" for a in result["required_actions"])


def test_投稿にないURLは主対象にできない(isolated_account):
    path = write_queue_file(isolated_account["queue_dir"], "u.md", body=BODY,
                             fm_overrides={"status": "draft"})
    proc = _run(["topics", "suggest", path,
                  "--article-url", "https://elsewhere.test/other"])
    assert proc.returncode == 2, proc.stdout
    out = json.loads(proc.stdout)
    assert out["error"]["code"] == "schema_error"
    assert "投稿本文にありません" in out["error"]["message"]


def test_主対象を変えれば別の判断になる(isolated_account):
    body = ("## threads\n\nA https://example.test/a と B https://example.test/b。\n")
    path = write_queue_file(isolated_account["queue_dir"], "two.md", body=body,
                             fm_overrides={"status": "draft"})
    a = json.loads(_run(["topics", "suggest", path,
                          "--article-url", "https://example.test/a"]).stdout)
    b = json.loads(_run(["topics", "suggest", path,
                          "--article-url", "https://example.test/b"]).stdout)
    assert a["context_id"] != b["context_id"], "主対象を変えても同じ判断のまま"


# --- 指摘 2: 根拠が LLM に届く ----------------------------------------------

def test_contextに根拠の本文が載る(isolated_account, tmp_path):
    """**ID だけでは、別のセッションは何も読めない。**"""
    path = write_queue_file(isolated_account["queue_dir"], "e.md", body=BODY,
                             fm_overrides={"status": "draft"})
    obs = make_observation("コーヒー", excerpt="MARKER_投稿例: 今日の一杯")
    obs["retrieved_at"] = datetime.datetime.now().astimezone().isoformat()
    saved = json.loads(_run(["topics", "observe", "--json-stdin", "--by", "t"],
                             obs).stdout)

    profile = {"language": "ja", "primary_goal": "article_visits",
               "editorial_scope": "MARKER_方針: 家庭で楽しむコーヒー",
               "intended_interests": ["コーヒー"],
               "avoid_misrepresentation": ["医療効能を主張しない"],
               "status": "confirmed", "basis": ["docs/編集方針.md"]}
    assert _run(["topics", "profile", isolated_account["name"],
                  "--json-stdin", "--by", "t"], profile).returncode == 0

    out = json.loads(_run(["topics", "suggest", path]).stdout)
    blob = json.dumps(out, ensure_ascii=False)
    assert "MARKER_投稿例" in blob, "保存した投稿例が返らない"
    assert "MARKER_方針" in blob, "保存した編集方針が返らない"
    assert out["evidence"]["post_text"], "投稿本文が返らない"
    assert saved["observation_id"] in {o["observation_id"]
                                        for o in out["evidence"]["observations"]}


def test_投稿例が多ければ削るが削ったと言う(isolated_account):
    path = write_queue_file(isolated_account["queue_dir"], "many.md", body=BODY,
                             fm_overrides={"status": "draft"})
    obs = make_observation("コーヒー", authors=20, samples=40)
    obs["retrieved_at"] = datetime.datetime.now().astimezone().isoformat()
    oid = json.loads(_run(["topics", "observe", "--json-stdin", "--by", "t"],
                           obs).stdout)["observation_id"]

    out = json.loads(_run(["topics", "suggest", path]).stdout)
    row = next(o for o in out["evidence"]["observations"]
               if o["observation_id"] == oid)
    assert len(row["samples"]) == 8
    # **黙って減らさない。** 減らしたら「その投稿例は無い」と読まれる。
    assert row["samples_truncated"]["total"] == 40
    assert oid in out["evidence"]["truncated_observation_ids"]

    full = json.loads(_run(["topics", "observation", oid]).stdout)
    assert len(full["observation"]["samples"]) == 40, "取りに来る口が無い"


def test_既存22語も根拠として返る(isolated_account, thth_root):
    from thth import topics as topics_mod
    topics_mod.record("コーヒー", verdict="alive", audience="焙煎士・喫茶店",
                       by="nigamilab セッション")
    path = write_queue_file(isolated_account["queue_dir"], "l.md", body=BODY,
                             fm_overrides={"status": "draft"})
    out = json.loads(_run(["topics", "suggest", path]).stdout)
    assert any(r["topic"] == "コーヒー"
               for r in out["evidence"]["legacy_notes"]), out["evidence"]


# --- 指摘 3: 空の引用 -------------------------------------------------------

def test_引用が空のsuitable候補は断る():
    article = make_article()
    with pytest.raises(models.SchemaError) as e:
        models.validate_proposal({
            "context_id": "x", "prompt_version": "t", "intended_reader": "x",
            "article_value": "x", "post_angle": "x",
            "candidates": [candidate("コーヒー", [], quote="精製")
                            | {"article_quotes": []}],
            "selected_topic": "コーヒー", "selection_reason": "x",
        }, article=article, known_observation_ids=set())
    assert "article_quotes が空" in str(e.value)


def test_不適合の候補は引用が無くてよい():
    """**見なかった候補にまで引用を求めない。**"""
    article = make_article()
    out = models.validate_proposal({
        "context_id": "x", "prompt_version": "t", "intended_reader": "x",
        "article_value": "x", "post_angle": "x",
        "candidates": [
            candidate("コーヒー", [], quote="精製"),
            candidate("お茶", [], fit="unsuitable") | {"article_quotes": []},
        ],
        "selected_topic": "コーヒー", "selection_reason": "x",
    }, article=article, known_observation_ids=set())
    assert out["proposal_id"].startswith("sha256:")


def test_引用が文字列でなければ断る():
    article = make_article()
    with pytest.raises(models.SchemaError):
        models.validate_proposal({
            "context_id": "x", "prompt_version": "t", "intended_reader": "x",
            "article_value": "x", "post_angle": "x",
            "candidates": [candidate("コーヒー", []) | {"article_quotes": [42]}],
            "selected_topic": "コーヒー", "selection_reason": "x",
        }, article=article, known_observation_ids=set())


# --- 指摘 4: 仮 profile -----------------------------------------------------

def _with_profile(status):
    article = make_article()
    obs = {ids(1)[0]: make_observation("コーヒー")}
    context = make_context(article, obs, profile_status=status)
    context["main_article_url"] = "https://example.test/coffee"
    proposal = models.validate_proposal({
        "context_id": context["context_id"], "prompt_version": "t",
        "intended_reader": "x", "article_value": "x", "post_angle": "x",
        "candidates": [candidate("コーヒー", ids(1))],
        "selected_topic": "コーヒー", "selection_reason": "x",
    }, article=article, known_observation_ids=set(obs))
    return advice.evaluate(context, article=article, proposal=proposal,
                            observations=obs, now=NOW)


def test_仮のprofileでは確定と言わない():
    result = _with_profile("provisional")
    assert result["status"] == "provisional", result
    assert any("profile が確定していません" in s for s in result["shortfalls"])


def test_確定したprofileなら推奨になる():
    assert _with_profile("confirmed")["status"] == "recommended"


def test_足りないものの種類が原因に合っている():
    """**profile が無いのに「観測を追加してください」と言わない。**"""
    result = _with_profile("provisional")
    kinds = {a["type"] for a in result["required_actions"]}
    assert kinds == {"profile"}, result["required_actions"]


def test_原因が複数なら別々の作業として返す():
    article = make_article()
    obs = {ids(1)[0]: make_observation("コーヒー", status="partial")}
    context = make_context(article, obs, profile_status="provisional")
    context["main_article_url"] = "https://example.test/coffee"
    proposal = models.validate_proposal({
        "context_id": context["context_id"], "prompt_version": "t",
        "intended_reader": "x", "article_value": "x", "post_angle": "x",
        "candidates": [candidate("コーヒー", ids(1), article_fit=1)],
        "selected_topic": "コーヒー", "selection_reason": "x",
    }, article=article, known_observation_ids=set(obs))
    result = advice.evaluate(context, article=article, proposal=proposal,
                              observations=obs, now=NOW)
    kinds = {a["type"] for a in result["required_actions"]}
    assert kinds == {"profile", "proposal", "observation"}, result["required_actions"]
    for action in result["required_actions"]:
        assert action["expected_schema"] != "TopicObservation" \
            or action["type"] == "observation"


# --- 指摘 5: 内容アドレスの再検証 -------------------------------------------

def test_同じIDで中身が変わっていたら壊れている扱い(thth_root):
    row = make_observation("コーヒー")
    row["schema_version"] = models.SCHEMA_VERSION
    row["observation_id"] = models.content_id(row, exclude=("observation_id",))
    store.put("observations", row, id_key="observation_id")

    path = os.path.join(store.root(), "observations",
                        row["observation_id"][7:] + ".json")
    tampered = dict(row)
    tampered["status"] = "ok"
    tampered["samples"] = make_observation("コーヒー", samples=9)["samples"]
    with open(path, "w", encoding="utf-8") as f:
        json.dump(tampered, f, ensure_ascii=False)

    rows, broken, _ = store.load_all("observations")
    assert rows == [], "書き換えられた記録を読めたことにした"
    assert broken == [row["observation_id"]]
    with pytest.raises(store.StoreError):
        store.get("observations", row["observation_id"])


def test_IDを名乗るだけの記録は保存できない(thth_root):
    row = make_observation("コーヒー")
    row["observation_id"] = "sha256:" + "a" * 64
    with pytest.raises(store.StoreError):
        store.put("observations", row, id_key="observation_id")


def test_壊れた根拠を参照する判断は保存しない(isolated_account, thth_root):
    path = write_queue_file(isolated_account["queue_dir"], "b.md", body=BODY,
                             fm_overrides={"status": "draft"})
    obs = make_observation("コーヒー")
    obs["retrieved_at"] = datetime.datetime.now().astimezone().isoformat()
    oid = json.loads(_run(["topics", "observe", "--json-stdin", "--by", "t"],
                           obs).stdout)["observation_id"]
    article = make_article()
    first = json.loads(_run(["topics", "suggest", path, "--input-json-stdin"],
                             {"article": article}).stdout)
    proposal = {
        "context_id": first["context_id"], "prompt_version": "t",
        "intended_reader": "x", "article_value": "x", "post_angle": "x",
        "candidates": [candidate("コーヒー", [oid])],
        "selected_topic": "コーヒー", "selection_reason": "x",
    }
    checked = json.loads(_run(["topics", "suggest", path, "--input-json-stdin"],
                               {"article": article, "proposal": proposal}).stdout)

    store_path = os.path.join(store.root(), "observations", oid[7:] + ".json")
    row = json.loads(open(store_path, encoding="utf-8").read())
    row["note"] = "あとから書き足した"
    open(store_path, "w", encoding="utf-8").write(json.dumps(row, ensure_ascii=False))

    proc = _run(["topics", "record-decision", "--json-stdin", "--by", "t"], checked)
    assert proc.returncode != 0, proc.stdout
    out = json.loads(proc.stdout)
    assert not out.get("stored")


# --- 指摘 6: suggest の出力をそのまま保存できる -----------------------------

def test_suggestの出力をそのまま保存できる(isolated_account, thth_root):
    path = write_queue_file(isolated_account["queue_dir"], "r.md", body=BODY,
                             fm_overrides={"status": "draft"})
    obs = make_observation("コーヒー")
    obs["retrieved_at"] = datetime.datetime.now().astimezone().isoformat()
    oid = json.loads(_run(["topics", "observe", "--json-stdin", "--by", "t"],
                           obs).stdout)["observation_id"]
    profile = {"language": "ja", "primary_goal": "article_visits",
               "editorial_scope": "コーヒー", "intended_interests": ["コーヒー"],
               "avoid_misrepresentation": ["医療効能を主張しない"],
               "status": "confirmed", "basis": ["docs/編集方針.md"]}
    _run(["topics", "profile", isolated_account["name"], "--json-stdin", "--by", "t"],
          profile)

    article = make_article()
    first = json.loads(_run(["topics", "suggest", path, "--input-json-stdin"],
                             {"article": article}).stdout)
    proposal = {
        "context_id": first["context_id"], "prompt_version": "t",
        "intended_reader": "コーヒーを淹れる人", "article_value": "精製の違い",
        "post_angle": "味の違い",
        "candidates": [candidate("コーヒー", [oid],
                                  counterevidence="重大な反証を確認できず")],
        "selected_topic": "コーヒー", "selection_reason": "本文と会話の適合",
    }
    checked = json.loads(_run(["topics", "suggest", path, "--input-json-stdin"],
                               {"article": article, "proposal": proposal}).stdout)
    assert checked["status"] == "recommended", checked["shortfalls"]

    # **手で組み直さずに保存できる。**
    proc = _run(["topics", "record-decision", "--json-stdin", "--by", "t"], checked)
    assert proc.returncode == 0, proc.stdout
    saved = json.loads(proc.stdout)
    assert saved["stored"] is True

    loaded = json.loads(_run(["topics", "decision", saved["decision_id"]]).stdout)
    assert loaded["selected_topic"] == "コーヒー"
    assert loaded["freshness"]["draft_unchanged"] is True


def test_保存に足りない入力はそう言う(isolated_account):
    proc = _run(["topics", "record-decision", "--json-stdin", "--by", "t"],
                 {"draft_path": "x.md"})
    assert proc.returncode == 2
    message = json.loads(proc.stdout)["error"]["message"]
    assert "record_payload" in message, message


# ===========================================================================
# 独立レビュー第 2 巡（2026-09-11）の 3 件
# ===========================================================================

def _make_profile(status="provisional", scope="家庭で楽しむコーヒー"):
    return {"language": "ja", "primary_goal": "article_visits",
            "editorial_scope": scope, "intended_interests": ["コーヒー"],
            "avoid_misrepresentation": ["医療効能を主張しない"],
            "status": status, "basis": ["docs/編集方針.md"]}


# --- P1-1: profile を例外にしない -------------------------------------------

def test_profileも読むたびに中身と版番号を照合する(thth_root, isolated_account):
    """**profile だけ `read_json` のままだった。**

    観測・記事・候補比較・判断には入れた照合が、profile には無かった。
    `profile_version` を据え置いて `status` を `confirmed` に、`basis` を
    空に書き換えると、**未確定の方針に基づく判断が確定扱いで保存できた。**
    """
    account = isolated_account["name"]
    assert _run(["topics", "profile", account, "--json-stdin", "--by", "t"],
                 _make_profile("provisional")).returncode == 0

    path = store.profile_path(account)
    row = json.loads(open(path, encoding="utf-8").read())
    row["status"] = "confirmed"
    row["basis"] = []
    row["editorial_scope"] = "書き換えた別の方針"
    open(path, "w", encoding="utf-8").write(json.dumps(row, ensure_ascii=False))

    with pytest.raises(store.StoreError) as e:
        store.get_profile(account)
    assert "profile_version と合いません" in str(e.value) \
        or "schema に合いません" in str(e.value), str(e.value)


def test_書き換えられたprofileでは判断も保存もできない(thth_root, isolated_account):
    path = write_queue_file(isolated_account["queue_dir"], "p.md", body=BODY,
                             fm_overrides={"status": "draft"})
    account = isolated_account["name"]
    _run(["topics", "profile", account, "--json-stdin", "--by", "t"],
          _make_profile("provisional"))

    ppath = store.profile_path(account)
    row = json.loads(open(ppath, encoding="utf-8").read())
    row["status"] = "confirmed"
    open(ppath, "w", encoding="utf-8").write(json.dumps(row, ensure_ascii=False))

    proc = _run(["topics", "suggest", path])
    assert proc.returncode == 2, proc.stdout
    assert json.loads(proc.stdout)["error"]["code"] == "store_busy" \
        or "profile" in json.loads(proc.stdout)["error"]["message"]


def test_検討用profileも検証関数を通る(isolated_account, tmp_path):
    """`--profile` を素通ししていたので、何を渡しても通っていた。"""
    path = write_queue_file(isolated_account["queue_dir"], "ov.md", body=BODY,
                             fm_overrides={"status": "draft"})
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps(
        _make_profile("confirmed") | {"basis": [], "confirmed_by": "t"},
        ensure_ascii=False))
    proc = _run(["topics", "suggest", path, "--profile", str(bad)])
    assert proc.returncode == 2, proc.stdout
    assert "basis" in json.loads(proc.stdout)["error"]["message"]


def test_検討用profileは版番号を名乗らせず中身から決める(isolated_account, tmp_path):
    path = write_queue_file(isolated_account["queue_dir"], "ov2.md", body=BODY,
                             fm_overrides={"status": "draft"})
    liar = tmp_path / "liar.json"
    liar.write_text(json.dumps(
        _make_profile("confirmed") | {"confirmed_by": "t",
                                       "profile_version": "sha256:" + "0" * 64},
        ensure_ascii=False))
    out = json.loads(_run(["topics", "suggest", path,
                            "--profile", str(liar)]).stdout)
    assert out["context"]["profile_version"] != "sha256:" + "0" * 64


# --- P2-3: 使った方針が読み手に届く -----------------------------------------

def test_差し替えたprofileの本文が出力に載る(isolated_account, tmp_path):
    """**「差し替えています」の 1 行だけでは、次の LLM は何も分からない。**"""
    path = write_queue_file(isolated_account["queue_dir"], "ov3.md", body=BODY,
                             fm_overrides={"status": "draft"})
    override = tmp_path / "o.json"
    override.write_text(json.dumps(
        _make_profile("confirmed", scope="MARKER_差し替えた方針") |
        {"confirmed_by": "t"}, ensure_ascii=False))
    proc = _run(["topics", "suggest", path, "--profile", str(override)])
    out = json.loads(proc.stdout)
    assert "evidence" in out, out
    assert out["evidence"]["profile"]["editorial_scope"] == "MARKER_差し替えた方針"
    assert out["evidence"]["profile_overridden"] is True
    assert out["context"]["profile_overridden"] is True


def test_使ったprofileと出力のprofileが同じもの(isolated_account):
    """**検証した snapshot をそのまま読み手へ渡す。**

    以前は `evidence` が active profile を**読み直して**いた。判断に使ったものと
    読み手が見るものが別の経路だと、片方だけ変わっても気づけない。
    """
    account = isolated_account["name"]
    path = write_queue_file(isolated_account["queue_dir"], "sn.md", body=BODY,
                             fm_overrides={"status": "draft"})
    _run(["topics", "profile", account, "--json-stdin", "--by", "t"],
          _make_profile("confirmed"))
    out = json.loads(_run(["topics", "suggest", path]).stdout)
    assert out["evidence"]["profile"]["profile_version"] == \
        out["context"]["profile_version"]
    assert out["evidence"]["profile_overridden"] is False


# --- P1-2: 共有観測と自 account の判断を混ぜない ----------------------------

def test_自分の不適合が他所の適合に置き換わらない(isolated_account, thth_root):
    """**`judgment()` は取っていたのに `bool()` しか使っていなかった。**

    最新の 1 行から `fit` も理由も取っていたので、その行が account を持たない
    記録だと、**自分が unsuitable と判断した事実が出力から消えた。**
    """
    from thth import topics as topics_mod
    account = isolated_account["name"]
    path = write_queue_file(isolated_account["queue_dir"], "j.md", body=BODY,
                             fm_overrides={"status": "draft"})

    topics_mod.record("お茶", verdict="mismatch", by="自分", account=account,
                       note="研究所は効能を扱わない")
    topics_mod.record("お茶", verdict="alive", by="別のセッション",
                       note="日本茶の場として活きている")   # account 無し

    out = json.loads(_run(["topics", "suggest", path]).stdout)
    row = next(r for r in out["evidence"]["legacy_notes"] if r["topic"] == "お茶")
    assert row["own_judgment"]["fit"] == "unsuitable"
    assert row["own_judgment"]["reason"] == "研究所は効能を扱わない"
    assert row["own_judgment"]["account"] == account
    # account 無しの記録は**常に参考**。
    assert row["legacy_judgment"]["fit"] == "suitable"
    assert row["legacy_judgment"]["account"] is None
    assert "採用しないでください" in row["notice"]


def test_他accountの判断も自分の判断を消さない(isolated_account, thth_root):
    from thth import topics as topics_mod
    account = isolated_account["name"]
    path = write_queue_file(isolated_account["queue_dir"], "j2.md", body=BODY,
                             fm_overrides={"status": "draft"})
    topics_mod.record("お茶", verdict="mismatch", by="自分", account=account)
    topics_mod.record("お茶", verdict="alive", by="kopicha", account="kopicha-threads")

    out = json.loads(_run(["topics", "suggest", path]).stdout)
    row = next(r for r in out["evidence"]["legacy_notes"] if r["topic"] == "お茶")
    assert row["own_judgment"]["fit"] == "unsuitable"
    assert [r["account"] for r in row["other_judgments"]] == ["kopicha-threads"]


def test_自分の判断が無ければNoneで返す(isolated_account, thth_root):
    """**「まだ判断していない」を「他所が判断した」で埋めない。**"""
    from thth import topics as topics_mod
    path = write_queue_file(isolated_account["queue_dir"], "j3.md", body=BODY,
                             fm_overrides={"status": "draft"})
    topics_mod.record("お茶", verdict="alive", by="別のセッション")

    out = json.loads(_run(["topics", "suggest", path]).stdout)
    row = next(r for r in out["evidence"]["legacy_notes"] if r["topic"] == "お茶")
    assert row["own_judgment"] is None
    assert row["legacy_judgment"]["fit"] == "suitable"


def test_観測と判断が別の物として返る(isolated_account, thth_root):
    """誰がいたか（共有できる事実）と、合うか（account ごと）を分ける。"""
    from thth import topics as topics_mod
    path = write_queue_file(isolated_account["queue_dir"], "j4.md", body=BODY,
                             fm_overrides={"status": "draft"})
    topics_mod.record("精製", verdict="mismatch", audience="レアアース・重加工",
                       by="THTH セッション", kind="専門語")

    out = json.loads(_run(["topics", "suggest", path]).stdout)
    row = next(r for r in out["evidence"]["legacy_notes"] if r["topic"] == "精製")
    assert row["observation"]["audience"] == "レアアース・重加工"
    assert row["observation"]["kind"] == "専門語"
    assert "fit" not in row["observation"], "観測に判定が混ざっている"


# ===========================================================================
# asmon 関東セッションが実記事で見つけたもの（2026-09-11）
# ===========================================================================

def test_既存22語が参照できるIDを持つ(isolated_account, thth_root):
    """**いちばん重かった指摘。**

    > `required_actions` が `TopicObservation` を求めますが、私が入れた 16 語の
    > 記録は `legacy_notes` に入っていて `observation_id` を持っていません。
    > **満たす手段が無い。**

    検出は正しかったが、**直せない指示を出していた**——第 2 巡の
    「profile が無いのに観測を足せと言う」と同じ形。
    """
    from thth import topics as topics_mod
    account = isolated_account["name"]
    topics_mod.record("中学受験", verdict="alive", audience="受験親のやりとり",
                       by="関東セッション", account=account, kind="行動")
    path = write_queue_file(isolated_account["queue_dir"], "k.md", body=BODY,
                             fm_overrides={"status": "draft"})

    out = json.loads(_run(["topics", "suggest", path]).stdout)
    row = next(r for r in out["evidence"]["legacy_notes"]
               if r["topic"] == "中学受験")
    oid = row["observation_id"]
    assert oid and oid.startswith("sha256:"), row
    # **context にも入っている**＝候補比較に書いてよい ID。
    assert oid in out["context"]["observation_ids"]
    # **引ける。**
    full = json.loads(_run(["topics", "observation", oid]).stdout)
    assert full["observation"]["topic"] == "中学受験"
    assert full["observation"]["provenance"] == "legacy"


def test_noteで記録したら観測IDが返る(isolated_account, thth_root):
    account = isolated_account["name"]
    proc = _run(["topics", account, "--note", "中受", "--verdict", "alive",
                  "--status", "ok", "--kind", "行動", "--audience", "受験親",
                  "--by", "関東", "--json"])
    assert proc.returncode == 0, proc.stderr
    row = json.loads(proc.stdout)
    assert row["observation_id"].startswith("sha256:"), row

    text = _run(["topics", account, "--note", "中受2", "--verdict", "alive",
                  "--status", "ok", "--kind", "行動", "--by", "関東"]).stdout
    assert "観測 ID:" in text, text
    assert "これだけでは推奨になりません" in text, text


def test_参考記録だけでは推奨にならないが理由が正しい(isolated_account, thth_root):
    """**旧記録を「keyword だから」と言わない。**

    引き方の記録が無いだけで、keyword で引いたわけではない。理由が違えば
    **次にすることも違う**（tag で引き直す、ではなく、投稿例を控える）。
    """
    from thth import topics as topics_mod
    account = isolated_account["name"]
    topics_mod.record("コーヒー", verdict="alive", audience="焙煎士",
                       by="関東", account=account)
    path = write_queue_file(isolated_account["queue_dir"], "lg.md", body=BODY,
                             fm_overrides={"status": "draft"})
    _run(["topics", "profile", account, "--json-stdin", "--by", "t"],
          _make_profile("confirmed"))

    first = json.loads(_run(["topics", "suggest", path, "--input-json-stdin"],
                             {"article": make_article()}).stdout)
    oid = next(r["observation_id"] for r in first["evidence"]["legacy_notes"]
               if r["topic"] == "コーヒー")
    proposal = {
        "context_id": first["context_id"], "prompt_version": "t",
        "intended_reader": "x", "article_value": "x", "post_angle": "x",
        "candidates": [candidate("コーヒー", [oid])],
        "selected_topic": "コーヒー", "selection_reason": "x",
    }
    out = json.loads(_run(["topics", "suggest", path, "--input-json-stdin"],
                           {"article": make_article(),
                            "proposal": proposal}).stdout)
    assert out["status"] == "provisional", out
    reason = [s for s in out["shortfalls"] if "観測" in s or "記録" in s]
    assert reason, out["shortfalls"]
    assert "keyword" not in reason[0], reason
    assert "投稿例を控えて" in reason[0], reason


def test_needs_proposalでも次にすることが出る(isolated_account):
    """**`needs_article` には出て `needs_proposal` には出ていなかった。**

    > ソースを読まないと次に進めませんでした。
    """
    path = write_queue_file(isolated_account["queue_dir"], "np.md", body=BODY,
                             fm_overrides={"status": "draft"})
    out = json.loads(_run(["topics", "suggest", path, "--input-json-stdin"],
                           {"article": make_article()}).stdout)
    assert out["status"] == "needs_proposal"
    assert out["required_actions"], "次にすることが空だった"
    assert out["required_actions"][0]["type"] == "proposal"
    shape = out["expected_schema"]["TopicProposal"]
    assert "candidates" in shape and "context_id" in shape
    assert "suitable" in shape["candidates"][0]["fit"]


def test_記事の必須項目と値域が出力に出る(isolated_account):
    """`topic_models.py` を読ませない。"""
    path = write_queue_file(isolated_account["queue_dir"], "sc.md", body=BODY,
                             fm_overrides={"status": "draft"})
    out = json.loads(_run(["topics", "suggest", path]).stdout)
    assert out["status"] == "needs_article"
    shape = out["expected_schema"]["ArticleEvidence"]
    assert set(shape) >= set(models.ARTICLE_KEYS), shape
    assert "full" in shape["coverage"] and "ok" in shape["retrieval_status"]


def test_記事だけを封筒なしで渡したらそう言う(isolated_account):
    """通知に**動かない例**を書いてしまった（`--account` と同じ形の事故）。"""
    path = write_queue_file(isolated_account["queue_dir"], "wr.md", body=BODY,
                             fm_overrides={"status": "draft"})
    proc = _run(["topics", "suggest", path, "--input-json-stdin"],
                 make_article())
    assert proc.returncode == 2, proc.stdout
    message = json.loads(proc.stdout)["error"]["message"]
    assert "--article-json-stdin" in message, message


def test_stale_contextは直し方を言う(isolated_account, thth_root):
    """**不足を埋めると 1 回必ず踏む。** 踏んだときに迷わせない。"""
    account = isolated_account["name"]
    path = write_queue_file(isolated_account["queue_dir"], "st.md", body=BODY,
                             fm_overrides={"status": "draft"})
    article = make_article()
    first = json.loads(_run(["topics", "suggest", path, "--input-json-stdin"],
                             {"article": article}).stdout)
    # profile を作る＝入力が変わる
    _run(["topics", "profile", account, "--json-stdin", "--by", "t"],
          _make_profile("confirmed"))

    proposal = {
        "context_id": first["context_id"], "prompt_version": "t",
        "intended_reader": "x", "article_value": "x", "post_angle": "x",
        "candidates": [candidate("コーヒー", [])],
        "selected_topic": "コーヒー", "selection_reason": "x",
    }
    out = json.loads(_run(["topics", "suggest", path, "--input-json-stdin"],
                           {"article": article, "proposal": proposal}).stdout)
    assert out["status"] == "stale_context"
    assert "context_id を" in out["warnings"][-1], out["warnings"]


def test_形が違うと断るときも正しい形を返す(isolated_account):
    """**断るときこそ、次にできることを渡す。**

    項目の名指しはしていたが、値域は `topic_models.py` を読むまで分からず、
    関東セッションはソースを読みに行った。
    """
    path = write_queue_file(isolated_account["queue_dir"], "sh.md", body=BODY,
                             fm_overrides={"status": "draft"})
    proc = _run(["topics", "suggest", path, "--input-json-stdin"],
                 {"article": {"url": "https://example.test/coffee",
                               "title": "t", "content_text": "本文",
                               "fetched_at": "2026-09-11T09:00:00+09:00"}})
    assert proc.returncode == 2, proc.stdout
    out = json.loads(proc.stdout)
    assert "requested_url" in out["error"]["message"]
    shape = out["expected_schema"]["ArticleEvidence"]
    assert set(shape) >= set(models.ARTICLE_KEYS), shape
    assert "full" in shape["coverage"]


# ===========================================================================
# 実運用 2 巡目: kopicha / nigamilab セッションの報告（2026-09-11）
# ===========================================================================

def _fresh_observation(topic="コーヒー", **over):
    obs = make_observation(topic, **over)
    obs["retrieved_at"] = datetime.datetime.now().astimezone().isoformat()
    return obs


def test_正直に書いても落ちない(isolated_account, thth_root):
    """**いちばん重い指摘**（kopicha セッション報告 2026-09-11）。

    > `uncertainties` を**空文字にしただけ**で、他は 1 文字も変えずに再実行したら
    > `recommended` になりました。**根拠は 1 つも増えていません。**

    書かれていたのは「このアカウントの実績がまだ 0 本なので、期待した反応が出るかは
    未検証」——**結果の不確かさ**であって、根拠の欠けではない。初投稿の
    アカウントは必ず書けるし、書けば落ちる。**正直に書くと落ちる道具は、全員に
    「空にすれば通る」を教える。**
    """
    honest = evaluate(
        [candidate("コーヒー", ids(1)) |
         {"evidence_gaps": [],
          "uncertainties": "このアカウントの実績がまだ 0 本なので、"
                            "観測から期待した反応が出るかは未検証"}],
        [make_observation("コーヒー")], selected="コーヒー")
    silent = evaluate(
        [candidate("コーヒー", ids(1)) | {"evidence_gaps": [],
                                           "uncertainties": ""}],
        [make_observation("コーヒー")], selected="コーヒー")

    assert honest["status"] == silent["status"] == "recommended", honest
    # **黙っても得をしない。正直に書いた分は残る。**
    assert any("残る不確かさ" in w for w in honest["warnings"]), honest["warnings"]
    assert not any("残る不確かさ" in w for w in silent["warnings"])


def test_根拠の欠けは止める():
    """結果の読めなさは止めないが、**根拠が欠けていれば止める。**"""
    result = evaluate(
        [candidate("コーヒー", ids(1)) |
         {"evidence_gaps": ["この語でコーヒーの話をしている人を確認できていない"],
          "uncertainties": ""}],
        [make_observation("コーヒー")], selected="コーヒー")
    assert result["status"] == "provisional", result
    assert any("根拠が欠けています" in s for s in result["shortfalls"])


def test_古い形の候補比較は今までどおり止める():
    """**黙って緩めない。**

    `evidence_gaps` を書いていない（キーが無い）候補比較は、`uncertainties` を
    今までどおり「足りないもの」として扱う——移行の途中で穴を開けない。
    """
    result = evaluate(
        [candidate("コーヒー", ids(1),
                    uncertainties="投稿例が鉱物の話ばかりで確認できていない")],
        [make_observation("コーヒー")], selected="コーヒー")
    assert result["status"] == "provisional", result
    assert any("未解決の不確かさ" in s for s in result["shortfalls"])
    assert any("evidence_gaps" in w for w in result["warnings"])


def test_空のJSONは観測として保存できない(isolated_account, thth_root):
    """両セッションが同じ穴を見つけた。

    > `echo "{}" | thth topics observe --json-stdin` が `stored: true` を返し、
    > `topic` も `samples` も `retrieved_at` も無い記録が**共有台帳に ID つきで
    > 残った。**

    `build_article` / `build_profile` には検査があったのに、**観測だけ素通し**
    ——また例外を 1 つ作っていた。
    """
    proc = _run(["topics", "observe", "--json-stdin", "--by", "t"], {})
    assert proc.returncode == 2, proc.stdout
    message = json.loads(proc.stdout)["error"]["message"]
    for key in ("topic", "search_mode", "retrieved_at", "status", "samples"):
        assert key in message, message


def test_投稿者が数えられない観測は断る(isolated_account, thth_root):
    """`author` だけ書くと投稿者 0 人と数えられていた（nigamilab セッション）。

    **保存のときに言う**のがいちばん親切。
    """
    obs = _fresh_observation()
    obs["samples"] = [{"post_id": "p", "excerpt": "x", "author": "だれか"}]
    proc = _run(["topics", "observe", "--json-stdin", "--by", "t"], obs)
    assert proc.returncode == 2, proc.stdout
    message = json.loads(proc.stdout)["error"]["message"]
    assert "author_key" in message and "author" in message, message


def test_okなのに投稿例が空の観測は断る(isolated_account, thth_root):
    """**0 件は「人がいない」ではない。** `empty` と区別させる。"""
    obs = _fresh_observation()
    obs["samples"] = []
    proc = _run(["topics", "observe", "--json-stdin", "--by", "t"], obs)
    assert proc.returncode == 2
    assert "empty" in json.loads(proc.stdout)["error"]["message"]


def test_legacy_notesのobservationの中にIDがある(isolated_account, thth_root):
    """**行の top-level にだけ置いていた。**

    > `legacy_notes[].observation` に `observation_id` が入っていないので
    > `observation_refs` に書けません。…LLM の席から見ると直っていないのと
    > 同じです。
    """
    from thth import topics as topics_mod
    topics_mod.record("日本茶", verdict="alive", audience="煎茶・玉露",
                       by="kopicha", account=isolated_account["name"])
    path = write_queue_file(isolated_account["queue_dir"], "lid.md", body=BODY,
                             fm_overrides={"status": "draft"})
    out = json.loads(_run(["topics", "suggest", path]).stdout)
    row = next(r for r in out["evidence"]["legacy_notes"] if r["topic"] == "日本茶")
    assert row["observation"]["observation_id"] == row["observation_id"]
    assert row["observation"]["observation_id"].startswith("sha256:")


def test_既存22語はevidenceのobservationsに重ねない(isolated_account, thth_root):
    """**実際に見てきた観測が埋もれる**（実運用報告 2026-09-11）。

    本番で 90 件並び、そのほとんどが既存 22 語の履歴だった。参照できる ID は
    `legacy_notes[].observation.observation_id` にある。
    """
    from thth import topics as topics_mod
    topics_mod.record("お茶", verdict="alive", by="1 回目")
    topics_mod.record("お茶", verdict="mismatch", by="2 回目")   # 同じ語の履歴
    path = write_queue_file(isolated_account["queue_dir"], "dup.md", body=BODY,
                             fm_overrides={"status": "draft"})
    _run(["topics", "observe", "--json-stdin", "--by", "t"],
          _fresh_observation("コーヒー"))

    out = json.loads(_run(["topics", "suggest", path]).stdout)
    topics = [o["topic"] for o in out["evidence"]["observations"]]
    assert topics == ["コーヒー"], topics
    assert [r["topic"] for r in out["evidence"]["legacy_notes"]] == ["お茶"]
    # **参照はできる**（context に入っている）。
    oid = out["evidence"]["legacy_notes"][0]["observation"]["observation_id"]
    assert oid in out["context"]["observation_ids"]


def test_原稿に関係する観測が先に出る(isolated_account, thth_root):
    """**ID の辞書順で切っていたので、関係のない語が予算を使い切っていた。**"""
    body = "## threads\n\n日本茶の話。https://example.test/coffee\n"
    path = write_queue_file(isolated_account["queue_dir"], "ord.md", body=body,
                             fm_overrides={"status": "draft"})
    for topic in ("コーヒー", "焙煎", "日本茶"):
        _run(["topics", "observe", "--json-stdin", "--by", "t"],
              _fresh_observation(topic))
    out = json.loads(_run(["topics", "suggest", path]).stdout)
    topics = [o["topic"] for o in out["evidence"]["observations"]]
    assert topics[0] == "日本茶", topics


def test_読んだファイルを言う(isolated_account, tmp_path):
    """`thth` は VM 側で走るので、**手元のつもりのパスが別のファイルを指す。**"""
    path = write_queue_file(isolated_account["queue_dir"], "rf.md", body=BODY,
                             fm_overrides={"status": "draft"})
    other = tmp_path / "article.json"
    other.write_text(json.dumps({"url": "https://elsewhere.test/other",
                                  "title": "別の記事", "content_text": "x"},
                                 ensure_ascii=False))
    proc = _run(["topics", "suggest", path, "--article", str(other)])
    assert proc.returncode == 2
    message = json.loads(proc.stdout)["error"]["message"]
    assert str(other) in message, message
    assert "elsewhere.test" in message, message


def test_IDの形の誤りはstore_busyではない(isolated_account):
    proc = _run(["topics", "observation", "sha256:xxx"])
    assert proc.returncode == 2
    assert json.loads(proc.stdout)["error"]["code"] == "invalid_id"


def test_accountで最近の判断を引ける(isolated_account, thth_root):
    """**decision_id を控えていないと読み返せなかった。**"""
    account = isolated_account["name"]
    proc = _run(["topics", "decision", account])
    assert proc.returncode == 0, proc.stdout
    out = json.loads(proc.stdout)
    assert out["account"] == account and out["decisions"] == []

    proc = _run(["topics", "decision", "そんなaccountはない"])
    assert proc.returncode == 2
    assert json.loads(proc.stdout)["error"]["code"] == "unknown_account"


def test_adviseも他accountの判断をそう書く(isolated_account, thth_root):
    """**同じ情報が入口によって扱いが変わっていた。**

    `--advise` は「最新の 1 行」の判断を「アカウント未指定」と書いていた——
    **その行が他 account のものでも。**
    """
    from thth import topics as topics_mod
    topics_mod.record("お茶", verdict="alive", by="kopicha",
                       account="kopicha-threads", audience="煎茶の話")
    out = run_thth(["topics", isolated_account["name"], "--advise"]).stdout
    line = next(l for l in out.splitlines() if "お茶" in l)
    assert "このアカウントの判断ではありません" in line, line
    assert "kopicha-threads が「適合」と判断" in line, line
    assert "アカウント未指定" not in line, line


def test_中身の無い観測は使えるものとして数えない(thth_root):
    """**検査を足す前に保存された記録**が `topic: null` として並んでいた。

    消さずに、使わない。**壊れている扱いにして名前を出す**——黙って消すと
    「無かった」ことになる。
    """
    junk = {"observation_id": "", "schema_version": models.SCHEMA_VERSION,
            "submitted_by": "だれか"}
    junk["observation_id"] = models.content_id(junk, exclude=("observation_id",))
    store.put("observations", junk, id_key="observation_id")

    rows, broken, _ = store.load_all("observations")
    assert rows == []
    assert broken == [junk["observation_id"]]


def test_投稿者を数えられないことを偏りと言わない():
    """**キー名が違うだけなのに「投稿者を増やせ」と読める**（関東セッション報告）。

    > 0 人はおかしいと思って `topic_advice.py` を読んだら、`_authors()` が
    > 見ているのは `author_key` でした。…次にすることが「投稿者を増やす」に
    > 見えて、実際には「キー名を直す」でした。
    """
    obs = make_observation("コーヒー", samples=1, authors=1)
    obs["samples"] = [{"post_id": "p", "excerpt": "x", "author": "だれか",
                        "posted_at": obs["retrieved_at"]}]
    result = evaluate([candidate("コーヒー", ids(1))], [obs], selected="コーヒー")
    reason = [s for s in result["shortfalls"] if "投稿者" in s]
    assert reason, result["shortfalls"]
    assert "author_key" in reason[0], reason
    assert "偏って" not in reason[0], reason


def test_取得手段の限界と見ていないことを区別する():
    """**プラットフォームがそれしか出さなかったのか、こちらが見なかったのか。**

    ログイン状態の実ブラウザでも `中学受験` のトピック頁に 1 件しか描画
    されなかった、という報告。**「3 件以上ほしい」とだけ言うと、取りに行けば
    あるように読める。**
    """
    obs = make_observation("コーヒー", samples=1, authors=1)
    obs["coverage"] = {"pages": 1, "fetched": 1, "has_more": False}
    result = evaluate([candidate("コーヒー", ids(1))], [obs], selected="コーヒー")
    assert any("これ以上出ていません" in s for s in result["shortfalls"]), \
        result["shortfalls"]

    more = make_observation("コーヒー", samples=1, authors=1)
    more["coverage"] = {"pages": 1, "fetched": 1, "has_more": True}
    result = evaluate([candidate("コーヒー", ids(1))], [more], selected="コーヒー")
    assert not any("これ以上出ていません" in s for s in result["shortfalls"])


def test_観測の形も断るときに返る(isolated_account, thth_root):
    proc = _run(["topics", "observe", "--json-stdin", "--by", "t"], {})
    assert proc.returncode == 2
    out = json.loads(proc.stdout)
    shape = out["expected_schema"]["TopicObservation"]
    assert "author_key" in shape["samples"][0]
    assert "topic_tag" in shape["search_mode"]


def test_評価セットの実行例に両方の環境変数が入る(tmp_path):
    """`accounts/` は `THTH_ROOT` ではなく `THTH_APP_DIR` 基準（nigamilab
    セッション報告 2026-09-11: ここで詰まった）。**動く例だけを配る。**
    """
    import subprocess
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    proc = subprocess.run(
        [sys.executable, os.path.join(root, "tests", "fixtures",
                                       "build_injection_eval.py"),
         str(tmp_path / "eval")], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    assert "THTH_APP_DIR=" in proc.stderr, proc.stderr
    assert "THTH_ROOT=" in proc.stderr, proc.stderr

    info = json.loads(proc.stdout)
    out = subprocess.run(
        [sys.executable, os.path.join(root, "bin", "thth"), "topics", "suggest",
         info["draft"], "--article", info["article"]],
        capture_output=True, text=True,
        env=dict(os.environ, THTH_ROOT=info["root"], THTH_APP_DIR=info["app_dir"]))
    assert out.returncode == 0, out.stdout + out.stderr
    assert json.loads(out.stdout)["status"] == "needs_proposal"


# ===========================================================================
# 規約 14: 層を足したら、検査も同時に足す
# ===========================================================================

# **形の一覧と必須項目の一覧は別々に育つ。** 片方だけ増えると、
# 「項目が足りません」と言いながら正しい形を教えない、という状態になる。
# 新しい schema を足したらここに 1 行足すこと（足し忘れたら下が落ちる）。
SHAPES = {
    "ArticleEvidence": (advice.ARTICLE_SHAPE, models.ARTICLE_KEYS),
    "TopicObservation": (advice.OBSERVATION_SHAPE, models.OBSERVATION_KEYS),
    "TopicProposal": (advice.PROPOSAL_SHAPE, models.PROPOSAL_KEYS),
}


@pytest.mark.parametrize("name", sorted(SHAPES))
def test_出力する形が必須項目を全部含む(name):
    shape, keys = SHAPES[name]
    missing = set(keys) - set(shape[name])
    assert not missing, f"{name} の expected_schema に無い必須項目: {missing}"


def test_候補の必須項目も形に出ている():
    shape = advice.PROPOSAL_SHAPE["TopicProposal"]["candidates"][0]
    missing = set(models.CANDIDATE_KEYS) - set(shape)
    assert not missing, f"候補の expected_schema に無い必須項目: {missing}"
    for optional in models.CANDIDATE_OPTIONAL:
        assert optional in shape, f"任意の項目も形に出す: {optional}"


def test_検査を持つ層がすべて空で断る():
    """**空・欠損で断ること**を、層ごとに 1 か所で確かめる。

    `profile` だけ照合が無い・`observation` だけ検査が無い、を 3 回やった
    （規約 14）。**層が増えたらここに 1 行足す。**
    """
    for build in (models.build_article, models.build_observation,
                   models.build_profile):
        with pytest.raises(models.SchemaError):
            build({})


def test_正規化語しか持たない古い観測を捨てない(thth_root):
    """**「使えない」と「形が古い」を混同しない**（2026-09-11）。

    検査を足す前に残された `中学受験` の観測が `topic` を持たず
    `normalized_topic` だけ持っていた。**ログイン状態のブラウザで実際に見てきた
    唯一のトピック観測**だったのに、掃除で丸ごと捨てていた。
    """
    row = {"normalized_topic": "中学受験", "query": "中学受験",
           "search_mode": "topic_tag", "provider": "browser",
           "retrieved_at": datetime.datetime.now().astimezone().isoformat(),
           "samples": [{"post_id": "p", "excerpt": "x",
                         "author_key": "manamanaty79"}],
           "schema_version": models.SCHEMA_VERSION, "submitted_by": "関東"}
    row["observation_id"] = models.content_id(row, exclude=("observation_id",))
    store.put("observations", row, id_key="observation_id")

    rows, broken, _ = store.load_all("observations")
    assert broken == [], "実際に見てきた観測を捨てた"
    assert rows[0]["normalized_topic"] == "中学受験"


# ===========================================================================
# kopicha セッション 2 巡目: 本番でのクラッシュ（2026-09-11）
# ===========================================================================

def test_coverageが文字列でも落ちない():
    """**本番でクラッシュした**（kopicha セッション報告 2026-09-11）。

    > `coverage` が文字列の観測を参照すると `suggest` が落ちます。
    > …JSON は返りません（stdout が空になるので、呼ぶ側は「出力が無い」としか
    > 分かりません）。

    `ArticleEvidence` の `coverage` は `full`/`partial`/`unknown` の**文字列**
    なので、同じ名前で観測にも文字列を入れるのは自然な取り違え。
    **保存された記録の形を信じない。**
    """
    obs = make_observation("コーヒー", samples=1, authors=1)
    obs["coverage"] = "partial"          # 記事証拠のほうの形
    result = evaluate([candidate("コーヒー", ids(1))], [obs], selected="コーヒー")
    assert result["status"] == "provisional", result
    # 分からないものを「これで全部」と言わない。
    assert not any("これ以上出ていません" in s for s in result["shortfalls"])


@pytest.mark.parametrize("broken", ["partial", [], 3, {"has_more": "yes"}])
def test_壊れた形の観測でも応答を返す(broken):
    obs = make_observation("コーヒー")
    obs["coverage"] = broken
    obs["samples"].append("投稿例のつもりの文字列")     # 形が違う sample
    result = evaluate([candidate("コーヒー", ids(1))], [obs], selected="コーヒー")
    assert result["status"] in ("recommended", "provisional"), result


def test_観測のcoverageに文字列を保存させない(isolated_account, thth_root):
    """**同じ名前で別の形**なので、保存のときに言う。"""
    obs = _fresh_observation()
    obs["coverage"] = "partial"
    proc = _run(["topics", "observe", "--json-stdin", "--by", "t"], obs)
    assert proc.returncode == 2, proc.stdout
    message = json.loads(proc.stdout)["error"]["message"]
    assert "記事証拠の coverage とは別物" in message, message


def test_使えない記録の件数と一覧が食い違わない(isolated_account, thth_root):
    """「4 件あります」なのに配列は 3 件、が起きていた。"""
    path = write_queue_file(isolated_account["queue_dir"], "bc.md", body=BODY,
                             fm_overrides={"status": "draft"})
    for i in range(7):
        junk = {"submitted_by": f"だれか {i}",
                "schema_version": models.SCHEMA_VERSION}
        junk["observation_id"] = models.content_id(junk,
                                                    exclude=("observation_id",))
        store.put("observations", junk, id_key="observation_id")

    out = json.loads(_run(["topics", "suggest", path]).stdout)
    warning = next(w for w in out["warnings"] if "使えない観測" in w)
    assert "7 件あります" in warning, warning
    assert "ほか 2 件" in warning, warning


def test_想定外の失敗でもJSONを返す(isolated_account, monkeypatch, tmp_path):
    """**stdout を空で終わらせない**（kopicha セッション報告 2026-09-11）。

    > JSON は返りません（stdout が空になるので、呼ぶ側は「出力が無い」としか
    > 分かりません）。

    個々の穴を塞ぐだけでなく、**落ち方そのもの**を直す。握りつぶさず、
    型と場所を応答に入れる——出さなければ、使う側は報告のしようがない。
    """
    from thth import topic_cli
    path = write_queue_file(isolated_account["queue_dir"], "boom.md", body=BODY,
                             fm_overrides={"status": "draft"})

    def explode(*a, **k):
        raise RuntimeError("わざと壊す")

    monkeypatch.setattr(topic_cli.advice, "build_context", explode)
    out = tmp_path / "out.json"
    import contextlib
    with open(out, "w", encoding="utf-8") as f, contextlib.redirect_stdout(f):
        rc = topic_cli.dispatch(["topics", "suggest", path])
    assert rc == 2
    payload = json.loads(out.read_text())
    assert payload["error"]["code"] == "internal_error"
    assert "RuntimeError" in payload["error"]["message"]
    assert "わざと壊す" in payload["error"]["message"]
    assert "統括に報告" in payload["error"]["message"]


def test_正規化語しか無い観測は表示だけ補う(isolated_account, thth_root):
    """`topic: null` と並ぶと読めない。**保存された記録は書き換えない。**"""
    row = {"normalized_topic": "中学受験", "query": "中学受験",
           "search_mode": "topic_tag", "provider": "browser",
           "retrieved_at": datetime.datetime.now().astimezone().isoformat(),
           "status": "ok",
           "samples": [{"post_id": "p", "excerpt": "x", "author_key": "a"}],
           "schema_version": models.SCHEMA_VERSION, "submitted_by": "関東"}
    row["observation_id"] = models.content_id(row, exclude=("observation_id",))
    store.put("observations", row, id_key="observation_id")

    path = write_queue_file(isolated_account["queue_dir"], "nt.md", body=BODY,
                             fm_overrides={"status": "draft"})
    out = json.loads(_run(["topics", "suggest", path]).stdout)
    shown = next(o for o in out["evidence"]["observations"]
                 if o["observation_id"] == row["observation_id"])
    assert shown["topic"] == "中学受験"
    assert shown["topic_filled_from"] == "normalized_topic"
    # **保存されたほうは変わっていない**（内容 ID が変わると別の記録になる）。
    assert store.get("observations", row["observation_id"]).get("topic") is None


# ===========================================================================
# 取り下げ（asmon 関東セッション提案 2026-09-11）
# ===========================================================================

def _save_observation(topic="コーヒー"):
    row = models.build_observation(_fresh_observation(topic) | {"submitted_by": "t"})
    store.put("observations", row, id_key="observation_id")
    return row["observation_id"]


def test_取り下げても記録は消えない(thth_root, isolated_account):
    """> 観測は内容アドレスで、**消すと参照していた候補比較が壊れます。**"""
    oid = _save_observation()
    proc = _run(["topics", "retract", oid, "--reason", "入れ間違い",
                  "--by", "関東"])
    assert proc.returncode == 0, proc.stdout

    # **ファイルは残っている。**
    assert store.get("observations", oid) is not None
    # **使える一覧からは外れる。壊れている扱いにはしない。**
    rows, broken, taken = store.load_all("observations")
    assert rows == [] and broken == []
    assert taken[0]["observation_id"] == oid
    assert taken[0]["reason"] == "入れ間違い"
    assert taken[0]["retracted_by"] == "関東"


def test_取り下げた観測は候補比較から参照できない(thth_root, isolated_account):
    oid = _save_observation()
    path = write_queue_file(isolated_account["queue_dir"], "rt.md", body=BODY,
                             fm_overrides={"status": "draft"})
    before = json.loads(_run(["topics", "suggest", path]).stdout)
    assert oid in before["context"]["observation_ids"]

    _run(["topics", "retract", oid, "--reason", "入れ間違い", "--by", "関東"])
    after = json.loads(_run(["topics", "suggest", path]).stdout)
    assert oid not in after["context"]["observation_ids"]
    assert any("取り下げられた観測" in w for w in after["warnings"]), after["warnings"]
    # **「壊れている」とは言わない。**
    assert not any("使えない観測" in w for w in after["warnings"])


def test_取り下げは戻せる(thth_root, isolated_account):
    oid = _save_observation()
    _run(["topics", "retract", oid, "--reason", "入れ間違い", "--by", "関東"])
    proc = _run(["topics", "unretract", oid])
    assert proc.returncode == 0, proc.stdout
    rows, _broken, taken = store.load_all("observations")
    assert [r["observation_id"] for r in rows] == [oid]
    assert taken == []


def test_取り下げには理由と名前が要る(thth_root, isolated_account):
    oid = _save_observation()
    assert _run(["topics", "retract", oid, "--by", "関東"]).returncode == 2
    proc = _run(["topics", "retract", oid, "--reason", "x"])
    assert proc.returncode == 2
    assert "--by" in json.loads(proc.stdout)["error"]["message"]


def test_無い観測は取り下げられない(thth_root, isolated_account):
    proc = _run(["topics", "retract", "sha256:" + "a" * 64,
                  "--reason", "x", "--by", "y"])
    assert proc.returncode == 2
    assert "保存されていません" in json.loads(proc.stdout)["error"]["message"]


def test_取り下げの表示も正規化語を拾う(thth_root, isolated_account):
    """**照合は直したのに表示だけ取り残していた**（asmon 関東セッション報告）。

    「（語なし）」では、どの語の観測を下げたのか読み手に分からない。
    取り下げの対象になるのは**まさに形の古い記録**なので、ここで拾えないと
    意味がない。
    """
    row = {"normalized_topic": "中学受験", "query": "中学受験",
           "search_mode": "topic_tag", "provider": "browser",
           "retrieved_at": datetime.datetime.now().astimezone().isoformat(),
           "samples": [{"post_id": "p", "excerpt": "x", "author_key": "a"}],
           "schema_version": models.SCHEMA_VERSION, "submitted_by": "関東"}
    row["observation_id"] = models.content_id(row, exclude=("observation_id",))
    store.put("observations", row, id_key="observation_id")
    _run(["topics", "retract", row["observation_id"],
           "--reason", "author_key でなく author を使用", "--by", "関東"])

    path = write_queue_file(isolated_account["queue_dir"], "rw.md", body=BODY,
                             fm_overrides={"status": "draft"})
    out = json.loads(_run(["topics", "suggest", path]).stdout)
    warning = next(w for w in out["warnings"] if "取り下げられた観測" in w)
    assert "中学受験" in warning, warning
    assert "（語なし）" not in warning, warning
