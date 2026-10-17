"""媒体の特性の台帳（設計「上限と連投の柵」2026-10-17 §4・段 0・T-M-1〜2）。

T-M-1 台帳の検査: 7 媒体と未対応の misskey が通り、根拠の欠け・確かさの縛り・知らない語・
未対応の媒体の既定・未知の媒体を断る。壊した台帳は tmp に写して作る（repo の台帳は触らない）。
T-M-2 導き: 台帳の `defaults` から柵の行（§6.2 の形）が集まり、行の `trait` が台帳に実在する。
段 0 では柵（guard）は台帳を読まないので、行の数は「今の既定の数」と同じであることだけを縛る。
"""
from __future__ import annotations

import copy
import json
import shutil

import pytest

from thth import accounts, hatena_fotolife, media_cli, media_traits as mt, principals
from thth import cli


# ------------------------------------------------------------------ 道具

@pytest.fixture
def ledger_dir(tmp_path):
    """repo の台帳の写し（壊してよい）。"""
    dest = tmp_path / "media_traits"
    shutil.copytree(mt.TRAITS_DIR, dest)
    return dest


def _edit(directory, media, fn):
    path = directory / f"{media}.json"
    doc = json.loads(path.read_text(encoding="utf-8"))
    fn(doc)
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")


def _trait(doc, name):
    return next(t for t in doc["traits"] if t["id"] == f"{doc['media']}.{name}")


def _codes(directory):
    return {p["code"] for p in mt.check(directory)["problems"]}


# ------------------------------------------------------------------ T-M-1 今の台帳が通る

def test_同梱の台帳は7媒体と未対応のmisskeyで検査を通る():
    report = mt.check()
    assert report["problems"] == [], report["problems"]
    assert report["ok"] is True
    assert set(report["media"]) == set(mt.SUPPORTED_MEDIA) | {"misskey"}
    for media in mt.SUPPORTED_MEDIA:
        assert mt.load(media)["supported"] is True
    misskey = mt.load("misskey")
    assert misskey["supported"] is False and misskey["relates_to"] == ["mastodon"]
    assert {t["kind"] for t in misskey["traits"]} == {"observation", "culture"}
    assert not any("defaults" in t for t in misskey["traits"])


def test_推測の項目はすべて確かさlow_公式の文書はURLと日付を持つ():
    for media, doc in mt.load_all().items():
        for t in doc["traits"]:
            ev = t["evidence"]
            if ev["type"] == "inferred":
                assert t["confidence"] == "low", t["id"]
            if ev["type"] == "official_doc":
                assert ev["url"].startswith("http") and ev["checked_at"], t["id"]


def test_設計の主な項目が入っている():
    """§4.2 の表の要の項目（はてなのスパムの指針・X の 403・Threads の 250）。"""
    ids = mt.trait_ids()
    for tid in ("hatenabookmark.same_site_bulk", "hatenabookmark.mechanical_high_frequency",
                "hatenabookmark.self_site_allowed", "hatenabookmark.normal_batch_use",
                "x.reply_requires_mention", "x.human_verification", "threads.api_posts_24h",
                "bluesky.pds_points", "mastodon.api_rate", "hatenablog.commercial_guideline",
                "fotolife.first_403", "misskey.registration_closed", "misskey.fediverse_reach"):
        assert tid in ids, tid
    batch = _trait(mt.load("hatenabookmark"), "normal_batch_use")
    assert batch["evidence"]["type"] == "inferred" and batch["confidence"] == "low"


# ------------------------------------------------------------------ T-M-1 断るもの（壊して赤）

def test_推測なのにhighは断る(ledger_dir):
    _edit(ledger_dir, "hatenabookmark", lambda d: _trait(d, "normal_batch_use").update(confidence="high"))
    assert "inferred_must_be_low" in _codes(ledger_dir)
    _edit(ledger_dir, "hatenabookmark", lambda d: _trait(d, "normal_batch_use").update(confidence="medium"))
    assert "inferred_must_be_low" in _codes(ledger_dir)


def test_公式の文書を推測に書き換えて確かさを残すと断る(ledger_dir):
    def edit(d):
        t = _trait(d, "same_site_bulk")
        t["evidence"] = {"type": "inferred", "by": "試験", "at": "2026-10-17"}
    _edit(ledger_dir, "hatenabookmark", edit)
    assert "inferred_must_be_low" in _codes(ledger_dir)


def test_未対応の媒体にdefaultsを書くと断る(ledger_dir):
    def edit(d):
        t = d["traits"][0]
        t["signals"] = ["post.daily"]
        t["defaults"] = {"post.daily": {"signal": "frequency", "actions": ["post"], "scope": "account",
                                        "window": {"day": "jst"}, "count": 8, "key": "op",
                                        "on_exceed": "refuse", "purpose": "preference"}}
    _edit(ledger_dir, "misskey", edit)
    assert "unsupported_media_has_defaults" in _codes(ledger_dir)
    with pytest.raises(mt.MediaTraitsError) as exc:
        mt.collect_defaults(ledger_dir)
    assert exc.value.code == mt.INVALID


@pytest.mark.parametrize("field", ["url", "checked_at"])
def test_公式の文書にURLか確かめた日が無いと断る(ledger_dir, field):
    _edit(ledger_dir, "threads", lambda d: _trait(d, "api_posts_24h")["evidence"].pop(field))
    problems = mt.check(ledger_dir)["problems"]
    assert {"code": "evidence_field_missing", "media": "threads", "trait": "threads.api_posts_24h",
            "detail": f"official_doc.{field}"} in problems


def test_確かめた日の形が違えば断る(ledger_dir):
    _edit(ledger_dir, "threads", lambda d: _trait(d, "api_posts_24h")["evidence"].update(checked_at="10/17"))
    assert "evidence_date_invalid" in _codes(ledger_dir)


@pytest.mark.parametrize("ev_type,field", [("observed", "record"), ("observed", "observed_at"),
                                           ("inferred", "by"), ("official_statement", "source")])
def test_根拠の種類ごとの欄が欠けると断る(ledger_dir, ev_type, field):
    target = {"observed": ("x", "duplicate_content"), "inferred": ("hatenabookmark", "normal_batch_use"),
              "official_statement": ("x", "reply_requires_mention")}[ev_type]
    _edit(ledger_dir, target[0], lambda d: _trait(d, target[1])["evidence"].pop(field))
    assert "evidence_field_missing" in _codes(ledger_dir)


def test_二次で読んだ公式の文書をhighにすると断る(ledger_dir):
    _edit(ledger_dir, "x", lambda d: _trait(d, "account_post_limit").update(confidence="high"))
    assert "secondhand_cannot_be_high" in _codes(ledger_dir)


def test_idの重複を断る(ledger_dir):
    _edit(ledger_dir, "x", lambda d: d["traits"].append(copy.deepcopy(_trait(d, "duplicate_content"))))
    assert "duplicate_id" in _codes(ledger_dir)


def test_idの頭が媒体名でなければ断る(ledger_dir):
    _edit(ledger_dir, "x", lambda d: _trait(d, "duplicate_content").update(id="threads.duplicate_content"))
    assert "trait_id_invalid" in _codes(ledger_dir)


@pytest.mark.parametrize("field,value,code", [
    ("kind", "rumor", "unknown_kind"),
    ("confidence", "certain", "unknown_confidence"),
    ("penalty", ["shadowban"], "penalty_invalid"),
    ("color", "red", "trait_field_unknown"),
])
def test_知らない語を断る(ledger_dir, field, value, code):
    _edit(ledger_dir, "x", lambda d: _trait(d, "duplicate_content").update({field: value}))
    assert code in _codes(ledger_dir)


def test_知らない根拠の種類を断る(ledger_dir):
    _edit(ledger_dir, "x", lambda d: _trait(d, "duplicate_content")["evidence"].update(type="rumor"))
    assert "unknown_evidence_type" in _codes(ledger_dir)


def test_知らない行の名を断る(ledger_dir):
    _edit(ledger_dir, "x", lambda d: _trait(d, "duplicate_content")["signals"].append("post.hourly"))
    assert "unknown_signal" in _codes(ledger_dir)


def test_defaultsの鍵がsignalsに無いと断る(ledger_dir):
    _edit(ledger_dir, "hatenabookmark", lambda d: _trait(d, "same_site_bulk").update(signals=[]))
    assert "default_not_in_signals" in _codes(ledger_dir)


def test_同じ行の既定を2つの項目が持つと断る(ledger_dir):
    def edit(d):
        t = _trait(d, "api_no_number")
        t["defaults"] = {"bookmark.burst": copy.deepcopy(_trait(d, "normal_batch_use")["defaults"]["bookmark.burst"])}
    _edit(ledger_dir, "hatenabookmark", edit)
    assert "duplicate_default" in _codes(ledger_dir)


def test_良い振る舞いと罰の項目は既定を持てない(ledger_dir):
    def edit(d):
        t = _trait(d, "self_site_allowed")
        t["defaults"] = {"bookmark.self_daily": copy.deepcopy(_trait(d, "promo_seo")["defaults"]["bookmark.self_daily"])}
        _trait(d, "promo_seo").pop("defaults")
    _edit(ledger_dir, "hatenabookmark", edit)
    assert _codes(ledger_dir) == {"kind_cannot_have_defaults"}


@pytest.mark.parametrize("field,value,code", [
    ("signal", "vibes", "unknown_signal"),
    ("actions", ["tweet"], "default_actions_invalid"),
    ("scope", "planet", "default_scope_invalid"),
    ("window", {"hours": 1}, "default_window_invalid"),
    ("count", -1, "default_count_invalid"),
    ("count", True, "default_count_invalid"),
    ("key", "user", "default_key_invalid"),
    ("on_exceed", "explode", "default_on_exceed_invalid"),
    ("purpose", "fun", "default_purpose_invalid"),
    ("applies_to", "post", "default_applies_to_invalid"),
    # 段 2（中-1）: `applies_to` は配列。1 つの文字列・数える動作に無い動作・空・重複は断る。
    ("applies_to", "bookmark_add", "default_applies_to_invalid"),
    ("applies_to", ["post"], "default_applies_to_invalid"),
    ("applies_to", [], "default_applies_to_invalid"),
    ("applies_to", ["bookmark_add", "bookmark_add"], "default_applies_to_invalid"),
    ("count_evidence", "vibes", "count_evidence_invalid"),
    ("extra", 1, "default_field_unknown"),
])
def test_既定の行の形が違えば断る(ledger_dir, field, value, code):
    _edit(ledger_dir, "hatenabookmark",
          lambda d: _trait(d, "normal_batch_use")["defaults"]["bookmark.burst"].update({field: value}))
    assert code in _codes(ledger_dir)


def test_止めるのは安全装置だけ(ledger_dir):
    _edit(ledger_dir, "hatenabookmark",
          lambda d: _trait(d, "normal_batch_use")["defaults"]["bookmark.burst"].update(on_exceed="stop"))
    assert "default_stop_not_safety" in _codes(ledger_dir)


def test_対応の媒体の台帳が欠けると断る(ledger_dir):
    (ledger_dir / "fotolife.json").unlink()
    assert {"code": "ledger_missing", "media": "fotolife", "trait": None, "detail": None} \
        in mt.check(ledger_dir)["problems"]


def test_知らない媒体が対応を名乗ると断る_未対応なら置ける(ledger_dir):
    doc = json.loads((ledger_dir / "misskey.json").read_text(encoding="utf-8"))
    doc["media"] = "pixelfed"
    doc["relates_to"] = []
    for t in doc["traits"]:
        t["id"] = t["id"].replace("misskey.", "pixelfed.")
    (ledger_dir / "pixelfed.json").write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    assert mt.check(ledger_dir)["ok"] is True
    doc["supported"] = True
    (ledger_dir / "pixelfed.json").write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    assert "supported_unknown_media" in _codes(ledger_dir)


def test_ファイル名と媒体名が食い違えば断る_関係の先が知らない媒体なら断る(ledger_dir):
    _edit(ledger_dir, "misskey", lambda d: d.update(relates_to=["myspace"]))
    assert "relates_to_unknown" in _codes(ledger_dir)
    _edit(ledger_dir, "misskey", lambda d: d.update(media="mastodon", relates_to=[]))
    assert "media_name_mismatch" in _codes(ledger_dir)


def test_JSONとして読めない台帳は理由を言う(ledger_dir):
    (ledger_dir / "x.json").write_text("{", encoding="utf-8")
    problems = mt.check(ledger_dir)["problems"]
    assert problems == [{"code": "ledger_unreadable", "media": "x", "trait": None, "detail": "JSONDecodeError"}]


def test_未知の媒体はunknown_media(ledger_dir):
    for name in ("myspace", "../x", "", "X"):
        with pytest.raises(mt.MediaTraitsError) as exc:
            mt.load(name, ledger_dir)
        assert exc.value.code == mt.UNKNOWN_MEDIA


def test_検査に落ちた台帳は読む口が断る_既定に倒さない(ledger_dir):
    _edit(ledger_dir, "x", lambda d: _trait(d, "duplicate_content").update(kind="rumor"))
    with pytest.raises(mt.MediaTraitsError) as exc:
        mt.load("x", ledger_dir)
    assert exc.value.code == mt.INVALID and exc.value.problems[0]["code"] == "unknown_kind"
    for fn in (mt.load_all, mt.collect_defaults, mt.trait_ids):
        with pytest.raises(mt.MediaTraitsError):
            fn(ledger_dir)
    # 壊れていない媒体は 1 つずつなら読める（check が全体の理由を言う）。
    assert mt.load("threads", ledger_dir)["media"] == "threads"


# ------------------------------------------------------------------ T-M-2 導き

def test_既定から柵の行が集まり_行のtraitが台帳に実在する():
    rows = mt.collect_defaults()
    ids = mt.trait_ids()
    assert rows
    seen = set()
    for row in rows:
        assert row["trait"] in ids, row
        assert row["id"] in mt.RULE_IDS, row
        assert row["media"] in mt.SUPPORTED_MEDIA, row
        assert row["trait"].startswith(row["media"] + "."), row
        trait = _trait(mt.load(row["media"]), row["trait"].split(".", 1)[1])
        assert row["id"] in trait["signals"]
        assert row["confidence"] == trait["confidence"]
        assert (row["media"], row["id"]) not in seen, row
        seen.add((row["media"], row["id"]))
    assert not any(row["media"] == "misskey" for row in rows)


def _row(media, rule_id, directory=None):
    return next(r for r in mt.collect_defaults(directory) if r["media"] == media and r["id"] == rule_id)


def test_既定の数は今の既定と同じ_振る舞いを変えない():
    """段 0 は柵を変えない。台帳の既定は 4.0.1 の数（accounts・principals・fotolife の既定）と同じ。"""
    for media in ("threads", "x", "bluesky", "mastodon"):
        assert _row(media, "post.daily")["count"] == accounts.GUARD_DEFAULTS["daily_max_posts"]
        assert _row(media, "retract.daily")["count"] == accounts.GUARD_DEFAULTS["daily_max_retracts"]
    for media in ("x", "bluesky", "mastodon"):
        assert _row(media, "reaction.daily")["count"] == accounts.GUARD_DEFAULTS["daily_max_reactions"]
    assert _row("hatenablog", "entry.daily")["count"] == accounts.GUARD_DEFAULTS["daily_max_posts"]
    assert _row("hatenablog", "entry_delete.daily")["count"] == accounts.GUARD_DEFAULTS["daily_max_retracts"]
    lim = principals.LIMIT_DEFAULTS
    burst = _row("hatenabookmark", "bookmark.burst")
    assert (burst["count"], burst["window"]) == (lim["bookmark_burst"]["count"],
                                                 {"minutes": lim["bookmark_burst"]["minutes"]})
    assert burst["on_exceed"] == "wait" and burst["scope"] == "principal"
    assert _row("hatenabookmark", "bookmark.daily")["count"] == lim["daily_max_bookmarks"]
    assert _row("hatenabookmark", "bookmark.self_daily")["count"] == lim["daily_max_self_bookmarks"]
    host = _row("hatenabookmark", "bookmark.same_host_daily")
    assert (host["count"], host["key"]) == (lim["bookmark_same_host_daily"], "host")
    assert _row("fotolife", "upload.daily")["count"] == hatena_fotolife.LIMIT_DEFAULTS["daily_max_uploads"]
    stop = _row("hatenabookmark", "bookmark.stop")
    assert (stop["count"], stop["window"], stop["on_exceed"], stop["purpose"]) == (
        30, {"minutes": 10}, "stop", "safety")


def test_台帳の既定を消すとその行が消える(ledger_dir):
    assert _row("hatenabookmark", "bookmark.burst", ledger_dir)
    def edit(d):
        t = _trait(d, "normal_batch_use")
        t["defaults"].pop("bookmark.burst")
    _edit(ledger_dir, "hatenabookmark", edit)
    assert not any(r["id"] == "bookmark.burst" for r in mt.collect_defaults(ledger_dir))


def test_行の形は段1が読める_設計の欄だけ():
    for row in mt.collect_defaults():
        assert set(row) - {"id", "media", "trait", "confidence", "evidence_type", "count_confidence"} <= (
            mt.DEFAULT_FIELDS_REQUIRED | mt.DEFAULT_FIELDS_OPTIONAL)
        assert mt.DEFAULT_FIELDS_REQUIRED <= set(row)


# ------------------------------------------------------------------ CLI

def test_CLI_traitsの一覧と1媒体と信号で絞る(capsys):
    assert cli.main(["media", "traits", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["schema"] == media_cli.TRAITS_SCHEMA
    by = {row["media"]: row for row in payload["media"]}
    assert by["misskey"]["supported"] is False and by["misskey"]["defaults"] == []
    assert "bookmark.burst" in by["hatenabookmark"]["defaults"]

    assert cli.main(["media", "traits", "hatenabookmark", "--signal", "bookmark.same_host_daily", "--json"]) == 0
    items = json.loads(capsys.readouterr().out)["items"]
    assert {t["id"] for t in items} == {"hatenabookmark.same_site_bulk", "hatenabookmark.skew_correction"}

    assert cli.main(["media", "traits", "misskey"]) == 0
    out = capsys.readouterr().out
    assert "未対応" in out and "misskey.registration_closed" in out and "推測" in out

    assert cli.main(["media", "traits", "x"]) == 0
    out = capsys.readouterr().out
    assert "x.reply_requires_mention" in out and "確かさ 高" in out


def test_CLI_未知の媒体はrc2(capsys):
    assert cli.main(["media", "traits", "myspace", "--json"]) == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False and payload["error"] == "unknown_media"
    assert cli.main(["media", "traits", "myspace"]) == 2
    assert "unknown_media" in capsys.readouterr().err


def test_CLI_checkは通ればrc0_壊れればrc1と理由(capsys, monkeypatch, ledger_dir):
    assert cli.main(["media", "check"]) == 0
    assert "検査を通りました" in capsys.readouterr().out
    _edit(ledger_dir, "hatenabookmark", lambda d: _trait(d, "normal_batch_use").update(confidence="high"))
    monkeypatch.setattr(mt, "TRAITS_DIR", ledger_dir)
    assert cli.main(["media", "check", "--json"]) == 1
    report = json.loads(capsys.readouterr().out)
    assert report["ok"] is False
    assert report["problems"][0]["code"] == "inferred_must_be_low"
    assert cli.main(["media", "check"]) == 1
    assert "inferred_must_be_low: hatenabookmark hatenabookmark.normal_batch_use" in capsys.readouterr().err
    # 壊れた台帳を traits で読むと rc1（既定に倒さない）。
    assert cli.main(["media", "traits", "hatenabookmark", "--json"]) == 1
    assert json.loads(capsys.readouterr().out)["error"] == "invalid_media_traits"


# ------------------------------------------------------------------ MCP（読むだけ）

def test_MCPのthth_media_traitsはCLIを呼ぶだけ(monkeypatch):
    from tests.test_mcp import _load_server_module
    server = _load_server_module()
    assert any(t["name"] == "thth_media_traits" for t in server.TOOLS)
    seen = []

    class Proc:
        returncode, stdout, stderr = 0, "{}", ""

    monkeypatch.setattr(server, "server_mode", lambda: False)
    monkeypatch.setattr(server, "remote_mode", lambda: False)
    monkeypatch.setattr(server, "run_cli", lambda args, *a, **k: seen.append(args) or Proc())
    server.call_tool("thth_media_traits", server.validate_arguments("thth_media_traits", {}))
    server.call_tool("thth_media_traits", server.validate_arguments(
        "thth_media_traits", {"media": "hatenabookmark", "signal": "bookmark.burst"}))
    assert seen == [["media", "traits", "--json"],
                    ["media", "traits", "hatenabookmark", "--signal", "bookmark.burst", "--json"]]
    with pytest.raises(server.ToolInputError):
        server.call_tool("thth_media_traits", {"signal": "bookmark.burst"})
    with pytest.raises(server.ToolInputError):
        server.validate_arguments("thth_media_traits", {"media": "-x"})
    with pytest.raises(server.ToolInputError):
        server.validate_arguments("thth_media_traits", {"media": "x", "write": True})


def test_MCPのthth_media_traitsは本物のCLIで台帳を返す_未知はisError(monkeypatch):
    from tests.test_mcp import _load_server_module
    server = _load_server_module()
    monkeypatch.setattr(server, "server_mode", lambda: False)
    monkeypatch.setattr(server, "remote_mode", lambda: False)
    got = server.call_tool("thth_media_traits", {"media": "x"})
    assert got["isError"] is False
    assert json.loads(got["content"][0]["text"])["media"] == "x"
    bad = server.call_tool("thth_media_traits", {"media": "myspace"})
    assert bad["isError"] is True


# ------------------------------------------------------------------ 段 2（監査 中-1・中-2）

def test_ブックマークの窓は付ける消すの両方に当てる_4_0_1と同じ():
    """中-1: 台帳の `bookmark.burst` の `applies_to` が付けるだけだと、消すの連投に窓が当たらない（4.0.1 と違う）。"""
    burst = _row("hatenabookmark", "bookmark.burst")
    assert burst["applies_to"] == ["bookmark_add", "bookmark_delete"]


def test_語彙は1か所_段1の表の行のidと語がすべて台帳の語彙にある():
    """中-1: 表（limits_policy）の語彙は media_traits を import する。行の id・単位・鍵・振る舞いが台帳の語彙の外なら赤。"""
    from thth import assess, limits_policy
    assert limits_policy.SCOPES is mt.SCOPES and limits_policy.KEYS is mt.KEYS
    assert limits_policy.ON_EXCEED is mt.ON_EXCEED and limits_policy.PURPOSES is mt.PURPOSES
    assert limits_policy.RULE_IDS is mt.RULE_IDS and assess.SIGNALS is mt.SIGNALS_V1
    assert "own" in mt.KEYS and "halted" in mt.ON_EXCEED and "app" in mt.SCOPES
    for row in limits_policy.POLICY:
        assert row["id"] in mt.RULE_IDS and row["scope"] in mt.SCOPES and row["key"] in mt.KEYS
        assert row["on_exceed"] in mt.ON_EXCEED and set(row["actions"]) <= set(mt.ACTIONS)


def test_台帳の信号と鍵は表と同じ():
    """中-1: 段 0 の台帳は `bookmark.self_daily` を frequency/op、`same_host_daily` を frequency で書いていた。"""
    own = _row("hatenabookmark", "bookmark.self_daily")
    host = _row("hatenabookmark", "bookmark.same_host_daily")
    assert (own["signal"], own["key"]) == ("self_reference", "own")
    assert (host["signal"], host["key"]) == ("same_host", "host")


def test_数のある既定は数の根拠が要る(ledger_dir):
    """中-2: 推測の数に項目の official_doc・high が付いて見えないように、数の根拠を必須にする。"""
    _edit(ledger_dir, "hatenabookmark",
          lambda d: _trait(d, "mechanical_high_frequency")["defaults"]["bookmark.stop"].pop("count_evidence"))
    assert _codes(ledger_dir) == {"count_evidence_missing"}


def test_数を公式と言うなら項目の根拠にその種類が要る(ledger_dir):
    _edit(ledger_dir, "hatenabookmark",
          lambda d: _trait(d, "mechanical_high_frequency")["defaults"]["bookmark.stop"].update(
              count_evidence="observed"))
    assert _codes(ledger_dir) == {"count_evidence_not_in_trait"}


def test_数の無い既定に数の根拠は置けない(ledger_dir):
    _edit(ledger_dir, "x", lambda d: _trait(d, "reply_requires_mention")["defaults"]["reply.counterpart"].update(
        count_evidence="inferred"))
    assert _codes(ledger_dir) == {"count_evidence_without_count"}


@pytest.mark.parametrize("rule_id", ["bookmark.stop", "bookmark.same_host_daily", "bookmark.self_daily",
                                     "bookmark.burst", "bookmark.daily"])
def test_推測の数は確かさlowで出る(rule_id):
    """中-2: 項目が official_doc・high でも、数が推測なら数の確かさは low。"""
    row = _row("hatenabookmark", rule_id)
    assert row["count_evidence"] == "inferred" and row["count_confidence"] == "low"


def test_THTHの線の数は確かさを問わない_公式の数は項目の確かさ():
    row = _row("threads", "post.daily")
    assert (row["count_evidence"], row["count_confidence"]) == ("thth_line", None)
    row = _row("hatenabookmark", "bookmark.once")
    assert (row["count_evidence"], row["count_confidence"]) == ("official_doc", "high")


def test_CLIは数の根拠を言う(capsys):
    assert cli.main(["media", "traits", "hatenabookmark"]) == 0
    out = capsys.readouterr().out
    assert "既定 bookmark.stop: principal・10 分・30（数は推測・確かさ low）・stop（safety）" in out
