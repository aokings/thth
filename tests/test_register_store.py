"""実店舗の形と、きっかけから下書き・承認の時の読み直し（計画 `docs/計画_実店舗_2026-11-13.md` 段 2）。

見本の店（`registers.example/cafe-sample`＝架空の飲食店「喫茶 みほん」・`goods-sample`＝架空の物販「雑貨 ためし」）を
一時の置き場に写して使う。時刻は 2026-12-08 10:00 JST に止める。

柵（計画 §2 の条件 3 の 5 つと、段 2 の柵）:
  - 推測の行からは下書きを作らない。
  - 見直し日を過ぎた行からは作らない。
  - 承認の確定の段で台帳を読み直し、行が変わっていれば（認め直していても）承認しない。
  - 終わった催し・過ぎた臨時の休みは、下書きにも承認にもしない（出さない期限 `not_after`）。
  - 出さない印の欄（private_fields）と、既定で出さない欄（住所・電話・休みの理由）は本文に入らない。
  - 営業の時間と臨時は二段で認める。
  - 新しいファイルは release の関門の危ない領域に入っている。
網には出ない。
"""
from __future__ import annotations

import datetime
import json
import pathlib

import pytest

from tests.conftest import commit_and_push_path
from tests.test_register_base import add, env, jrun, run  # noqa: F401
from thth import jst, queuefile
from thth.register import kinds

NOW = datetime.datetime(2026, 12, 8, 10, 0, tzinfo=jst.JST)


@pytest.fixture
def clock(monkeypatch):
    state = {"now": NOW}
    monkeypatch.setattr(jst, "now_jst", lambda: state["now"])
    return state


def confirm(capsys, name, row):
    """認める（二段の種類なら 1 段目の digest で確定する）。"""
    rc_, value, err = jrun(capsys, "register", "confirm", name, row, "--by", "m")
    if rc_ == 1:
        assert value["stage"] == 1
        rc_, value, err = jrun(capsys, "register", "confirm", name, row, "--by", "m", "--confirm", value["digest"])
    assert rc_ == 0, err
    return value


def drafts(account) -> list:
    return sorted(pathlib.Path(account["queue_dir"]).glob("*-register-*.md"))


def make_draft(capsys, account, name, row):
    rc_, value, err = jrun(capsys, "register", "draft", name, "--to", account["name"], "--row", row)
    assert rc_ == 0, err
    [one] = value["drafts"]
    return one["path"]


def approve_first(capsys, path):
    first = run(capsys, "approve", path)
    digests = [line.split(": ", 1)[1].strip() for line in first[1].splitlines() if line.startswith("digest: ")]
    assert first[0] == 1 and digests, first
    return digests[0]


def edit(path: pathlib.Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    assert old in text
    path.write_text(text.replace(old, new), encoding="utf-8")


# ------------------------------------------------------------------ 形

def test_見本の2店の台帳は形の点検を通る(capsys, env, clock):
    add(capsys, env, "cafe-sample", kind="store")
    add(capsys, env, "goods-sample", kind="store")
    for name, count in (("cafe-sample", 11), ("goods-sample", 7)):
        rc_, value, err = jrun(capsys, "register", "check", name)
        assert rc_ == 0, err
        assert value["rows"] == count and value["problems"] == []


@pytest.mark.parametrize("body, word", [
    ("月 10:00-18:00\n火 10:00-18:00", "hours_day_missing:wed"),
    ("月 18:00-10:00\n火 休み\n水 休み\n木 休み\n金 休み\n土 休み\n日 休み", "hours_time:mon"),
    ("月 10時から\n火 休み\n水 休み\n木 休み\n金 休み\n土 休み\n日 休み", "hours_time:mon"),
    ("定休日は水曜\n月 休み\n火 休み\n水 休み\n木 休み\n金 休み\n土 休み\n日 休み", "hours_line:1"),
    ("月 休み\n月 休み\n火 休み\n水 休み\n木 休み\n金 休み\n土 休み\n日 休み", "hours_day_twice:mon"),
])
def test_柵_営業の時間の本文の形(body, word):
    assert word in kinds.hours_lines(body)[2]


def test_日をまたぐ時間と2つの時間帯は通る():
    days, notes, problems = kinds.hours_lines("月 11:00-14:00, 17:00-翌01:00\n火 休み\n水 休み\n木 休み\n金 休み\n"
                                               "土 休み\n日 休み\n※ 注")
    assert problems == [] and days["月"] == [(660, 840), (1020, 1500)] and notes == ["※ 注"]


# ------------------------------------------------------------------ きっかけ

def test_きっかけは暦と台帳から出て終わった催しと早すぎる休みは出ない(capsys, env, clock):
    add(capsys, env, "cafe-sample", kind="store")
    rc_, value, _ = jrun(capsys, "register", "triggers", "cafe-sample")
    found = {(t["trigger"], t["row"]) for t in value["triggers"]}
    assert found == {("special_soon", "cafe-special-2026-12-12"), ("event_soon", "cafe-event-2026-12-14"),
                     ("hours_change", "cafe-hours-2026-12-15"), ("season_start", "cafe-item-winter-blend")}
    # どれもまだ推測なので、下書きにはできない印が付く。
    assert {t["code"] for t in value["triggers"]} == {"register_row_guess"}


def test_変わりの無い営業の時間は毎日のきっかけにしない(capsys, env, clock):
    add(capsys, env, "goods-sample", kind="store")
    rc_, value, _ = jrun(capsys, "register", "triggers", "goods-sample")
    assert "goods-hours" not in {t["row"] for t in value["triggers"]}
    assert {t["trigger"] for t in value["triggers"]} == {"special_soon", "event_soon", "item_new"}


# ------------------------------------------------------------------ 二段で認める

def test_柵_営業の時間と臨時は二段で認める(capsys, env, clock):
    path = add(capsys, env, "cafe-sample", kind="store")
    target = path / "臨時" / "2026-12-12_設備の点検.md"
    before = target.read_text(encoding="utf-8")
    rc_, first, _ = jrun(capsys, "register", "confirm", "cafe-sample", "cafe-special-2026-12-12", "--by", "m")
    assert rc_ == 1 and first["stage"] == 1 and "14:00" in first["text"]
    assert target.read_text(encoding="utf-8") == before                      # 1 段目は何も書かない
    rc_, wrong, _ = jrun(capsys, "register", "confirm", "cafe-sample", "cafe-special-2026-12-12", "--by", "m",
                         "--confirm", "000000000000")
    assert rc_ == 2 and wrong["code"] == "register_confirm_mismatch"
    edit(target, "close: 18:00", "close: 19:00")                              # 見せたあとに中身が変わった
    rc_, late, _ = jrun(capsys, "register", "confirm", "cafe-sample", "cafe-special-2026-12-12", "--by", "m",
                        "--confirm", first["digest"])
    assert rc_ == 2 and late["code"] == "register_confirm_mismatch"
    confirm(capsys, "cafe-sample", "cafe-special-2026-12-12")
    # 品や思いは一段で認める。
    rc_, value, _ = jrun(capsys, "register", "confirm", "cafe-sample", "cafe-item-winter-blend", "--by", "m")
    assert rc_ == 0 and value["confirmed"] == "本人が認めた"


def test_柵_営業の時間も二段で認める(capsys, env, clock):
    add(capsys, env, "cafe-sample", kind="store")
    rc_, first, _ = jrun(capsys, "register", "confirm", "cafe-sample", "cafe-hours", "--by", "m")
    assert rc_ == 1 and first["stage"] == 1 and "水 休み" in first["text"]
    assert jrun(capsys, "register", "use", "cafe-sample", "cafe-hours")[1]["code"] == "register_row_guess"


# ------------------------------------------------------------------ 下書き

def test_臨時の休みの下書きは出所と出さない期限を持ち理由を書かない(capsys, env, clock, isolated_account_factory):
    account = isolated_account_factory()
    add(capsys, env, "cafe-sample", kind="store")
    confirm(capsys, "cafe-sample", "cafe-special-2026-12-12")
    path = make_draft(capsys, account, "cafe-sample", "cafe-special-2026-12-12")
    fm = queuefile.parse(path).front_matter
    assert fm["status"] == "draft" and fm["from_register"] == "cafe-sample/cafe-special-2026-12-12"
    assert fm["from_register_trigger"] == "special_soon" and fm["from_register_commit"] == "none"
    assert str(fm["not_after"]).startswith("2026-12-12T23:59")
    text = pathlib.Path(path).read_text(encoding="utf-8")
    assert "12月12日（土）は 14:00〜18:00 の営業です" in text
    assert "設備の点検" not in text


def test_持ち主がpublic_fieldsに書けば休みの理由も出る(capsys, env, clock, isolated_account_factory):
    account = isolated_account_factory()
    path = add(capsys, env, "cafe-sample", kind="store")
    edit(path / "臨時" / "2026-12-12_設備の点検.md", "reason: 設備の点検", "reason: 設備の点検\npublic_fields: reason")
    confirm(capsys, "cafe-sample", "cafe-special-2026-12-12")
    text = pathlib.Path(make_draft(capsys, account, "cafe-sample", "cafe-special-2026-12-12")).read_text(encoding="utf-8")
    assert "（設備の点検）" in text


def test_柵_住所と電話は既定で外に出さない(capsys, env, clock, isolated_account_factory):
    account = isolated_account_factory()
    path = add(capsys, env, "cafe-sample", kind="store")
    confirm(capsys, "cafe-sample", "cafe-store")
    fields = jrun(capsys, "register", "use", "cafe-sample", "cafe-store")[1]["fields"]
    assert "address" not in fields and "phone" not in fields and fields["name"] == "喫茶 みほん"
    for row in ("cafe-special-2026-12-12", "cafe-event-2026-12-14", "cafe-hours-2026-12-15", "cafe-item-winter-blend"):
        confirm(capsys, "cafe-sample", row)
        make_draft(capsys, account, "cafe-sample", row)
    texts = "\n".join(p.read_text(encoding="utf-8") for p in drafts(account))
    assert len(drafts(account)) == 4
    assert "架空県" not in texts and "000-0000" not in texts and "帰省" not in texts
    # 持ち主が public_fields に書けば出る（欄ごとに選ぶ）。
    edit(path / "店.md", "phone: 000-0000-0000", "phone: 000-0000-0000\npublic_fields: address")
    confirm(capsys, "cafe-sample", "cafe-store")
    fields = jrun(capsys, "register", "use", "cafe-sample", "cafe-store")[1]["fields"]
    assert "address" in fields and "phone" not in fields


def test_柵_推測の行からは下書きを作らない(capsys, env, clock, isolated_account_factory):
    account = isolated_account_factory()
    add(capsys, env, "cafe-sample", kind="store")
    rc_, value, _ = jrun(capsys, "register", "draft", "cafe-sample", "--to", account["name"], "--row",
                         "cafe-event-2026-12-14")
    assert rc_ == 2 and value["code"] == "register_row_guess" and drafts(account) == []
    rc_, value, _ = jrun(capsys, "register", "draft", "cafe-sample", "--to", account["name"], "--trigger", "event_soon")
    assert rc_ == 2 and value["code"] == "register_draft_nothing" and drafts(account) == []


def test_柵_見直し日を過ぎた行からは下書きを作らない(capsys, env, clock, isolated_account_factory):
    account = isolated_account_factory()
    add(capsys, env, "cafe-sample", kind="store")
    confirm(capsys, "cafe-sample", "cafe-event-2026-12-14")
    clock["now"] = datetime.datetime(2026, 12, 14, 12, 0, tzinfo=jst.JST)   # 見直し日 12-15 の前は通る
    assert jrun(capsys, "register", "draft", "cafe-sample", "--to", account["name"], "--row",
                "cafe-event-2026-12-14")[0] == 0
    clock["now"] = datetime.datetime(2026, 12, 16, 9, 0, tzinfo=jst.JST)
    rc_, value, _ = jrun(capsys, "register", "draft", "cafe-sample", "--to", account["name"], "--row",
                         "cafe-event-2026-12-14")
    assert rc_ == 2 and value["code"] == "register_row_review_passed"


def test_柵_終わった催しは下書きにしない(capsys, env, clock, isolated_account_factory):
    account = isolated_account_factory()
    path = add(capsys, env, "cafe-sample", kind="store")
    edit(path / "催し" / "2026-11-20_秋の会.md", "review_by: 2026-11-21", "review_by: 2027-01-01")
    confirm(capsys, "cafe-sample", "cafe-event-2026-11-20")
    rc_, value, _ = jrun(capsys, "register", "draft", "cafe-sample", "--to", account["name"], "--row",
                         "cafe-event-2026-11-20")
    assert rc_ == 2 and value["code"] == "register_draft_expired" and drafts(account) == []


def test_柵_出さない印の欄は本文に入らない(capsys, env, clock, isolated_account_factory):
    account = isolated_account_factory()
    path = add(capsys, env, "cafe-sample", kind="store")
    edit(path / "催し" / "2026-12-14_試食会.md", "place: 店内", "place: 店内\nprivate_fields: place, fee")
    confirm(capsys, "cafe-sample", "cafe-event-2026-12-14")
    text = pathlib.Path(make_draft(capsys, account, "cafe-sample", "cafe-event-2026-12-14")).read_text(encoding="utf-8")
    assert "冬のブレンドの試食会: 12月14日（月） 13:00〜15:00" in text
    assert "店内" not in text and "無料" not in text and "予約: 要りません" in text


def test_同じ行の同じきっかけの下書きは2本目を作らない(capsys, env, clock, isolated_account_factory):
    account = isolated_account_factory()
    add(capsys, env, "goods-sample", kind="store")
    confirm(capsys, "goods-sample", "goods-item-tenugui")
    make_draft(capsys, account, "goods-sample", "goods-item-tenugui")
    rc_, value, _ = jrun(capsys, "register", "draft", "goods-sample", "--to", account["name"], "--row",
                         "goods-item-tenugui")
    assert rc_ == 2 and value["code"] == "register_draft_exists" and len(drafts(account)) == 1
    assert "冬柄の手ぬぐい（1,500 円）が店頭に並びました" in drafts(account)[0].read_text(encoding="utf-8")


def test_種類で選ぶと使える行だけを上限3本まで(capsys, env, clock, isolated_account_factory):
    account = isolated_account_factory()
    add(capsys, env, "goods-sample", kind="store")
    confirm(capsys, "goods-sample", "goods-event-2026-12-20")
    rc_, value, err = jrun(capsys, "register", "draft", "goods-sample", "--to", account["name"], "--trigger",
                           "event_soon")
    assert rc_ == 0, err
    assert [d["row"] for d in value["drafts"]] == ["goods-event-2026-12-20"]
    assert jrun(capsys, "register", "draft", "goods-sample", "--to", account["name"])[1]["code"] == \
        "register_args_invalid"


# ------------------------------------------------------------------ 承認の時の読み直し

def _approved_draft(capsys, env, clock, account, row="cafe-special-2026-12-12"):
    add(capsys, env, "cafe-sample", kind="store")
    confirm(capsys, "cafe-sample", row)
    path = make_draft(capsys, account, "cafe-sample", row)
    commit_and_push_path(path, message="test: register draft")
    return path, approve_first(capsys, path)


def test_変わっていなければ承認できる(capsys, env, clock, isolated_account_factory):
    account = isolated_account_factory()
    path, digest = _approved_draft(capsys, env, clock, account)
    rc_, out, err = run(capsys, "approve", path, "--confirm", digest, "--by", "m")
    assert rc_ == 0, err
    assert "承認しました" in out


def test_柵_承認の時に行が変わっていたら承認しない(capsys, env, clock, isolated_account_factory):
    account = isolated_account_factory()
    path, digest = _approved_draft(capsys, env, clock, account)
    folder = env["tmp"] / "cafe-sample"
    edit(folder / "臨時" / "2026-12-12_設備の点検.md", "close: 18:00", "close: 19:00")
    rc_, out, err = run(capsys, "approve", path, "--confirm", digest, "--by", "m")
    assert rc_ == 1 and "register_row_unconfirmed" in err and "承認しました" not in out
    # 認め直しても、下書きを作ったときの中身と違うので承認しない。
    confirm(capsys, "cafe-sample", "cafe-special-2026-12-12")
    rc_, out, err = run(capsys, "approve", path, "--confirm", digest, "--by", "m")
    assert rc_ == 1 and "register_row_changed" in err and "承認しました" not in out


def test_柵_出さない期限を過ぎた下書きは承認しない(capsys, env, clock, isolated_account_factory):
    account = isolated_account_factory()
    path, digest = _approved_draft(capsys, env, clock, account, row="cafe-event-2026-12-14")
    clock["now"] = datetime.datetime(2026, 12, 14, 15, 1, tzinfo=jst.JST)
    rc_, out, err = run(capsys, "approve", path, "--confirm", digest, "--by", "m")
    assert rc_ == 1 and "register_draft_expired" in err and "承認しました" not in out


def test_柵_台帳の控えの無い機械では承認しない(capsys, env, clock, isolated_account_factory):
    from thth.register import ledger
    account = isolated_account_factory()
    path, digest = _approved_draft(capsys, env, clock, account)
    pathlib.Path(ledger.path_for("cafe-sample")).unlink()
    rc_, out, err = run(capsys, "approve", path, "--confirm", digest, "--by", "m")
    assert rc_ == 1 and "register_recheck_unavailable" in err


def test_台帳の下書きでない原稿は台帳を読まない(capsys, env, clock, isolated_account_factory, monkeypatch):
    from tests.conftest import write_queue_file
    from thth.register import ledger
    account = isolated_account_factory()
    add(capsys, env, "cafe-sample", kind="store")
    path = write_queue_file(account["queue_dir"], "plain.md", fm_overrides={"status": "draft", "approved_sha": None})
    called = []
    real = ledger.load
    # 控えは見る（頭に出所が無くても台帳の下書きか知るため）が、台帳の控え（行を読む元）は開かない。
    monkeypatch.setattr(ledger, "load", lambda name: called.append(name) or real(name))
    digest = approve_first(capsys, path)
    assert run(capsys, "approve", path, "--confirm", digest, "--by", "m")[0] == 0
    assert called == []


# ------------------------------------------------------------------ 関門

def test_台帳の機械は危ない領域に入っている():
    from tests.test_release_gate import gate
    for path in ("thth/register/draft.py", "thth/register/recheck.py", "thth/register/confirm.py",
                 "thth/register/fence.py"):
        assert "register" in gate.areas_of(path), path
    # 下書きの出所の控え（承認の読み直しを掛けるかを決める・監査 5.19.0 の 1）も台帳と店の両方の領域。
    assert {"register", "store_read"} <= set(gate.areas_of("thth/draft_origin.py"))


def test_下書きの頭はJSONに出しても本文の値を持たない(capsys, env, clock, isolated_account_factory):
    account = isolated_account_factory()
    add(capsys, env, "cafe-sample", kind="store")
    confirm(capsys, "cafe-sample", "cafe-special-2026-12-12")
    rc_, value, _ = jrun(capsys, "register", "draft", "cafe-sample", "--to", account["name"], "--row",
                         "cafe-special-2026-12-12")
    assert "設備の点検" not in json.dumps(value, ensure_ascii=False)


# ------------------------------------------------------------------ 頭の出所の行が落とされても（監査 5.19.0 直したほうがよい 1）

def _strip_head(path, keys=("from_register",)) -> None:
    """頭の行のうち、名前が keys のどれかで始まる行を落とす（LLM が知らない欄を落として書き直した形）。"""
    p = pathlib.Path(path)
    lines = p.read_text(encoding="utf-8").split("\n")
    p.write_text("\n".join(line for line in lines if not line.startswith(keys)), encoding="utf-8")


def _restrip(capsys, path, keys=("from_register",)):
    _strip_head(path, keys)
    assert not any(k.startswith(keys) for k in queuefile.parse(path).front_matter)
    commit_and_push_path(path, message="test: strip head")
    return approve_first(capsys, path)


def test_柵_from_registerの行を消した台帳の下書きも承認の時に読み直す(capsys, env, clock, isolated_account_factory):
    account = isolated_account_factory()
    path, _digest = _approved_draft(capsys, env, clock, account, row="cafe-event-2026-12-14")
    digest = _restrip(capsys, path)
    # 監査の探りと同じ: 行を「違うと言った」にする。頭の行が無くても THTH の側の控えから読み直して断る。
    assert run(capsys, "register", "deny", "cafe-sample", "cafe-event-2026-12-14", "--by", "m")[0] == 0
    rc_, out, err = run(capsys, "approve", path, "--confirm", digest, "--by", "m")
    assert rc_ == 1 and "register_row_denied" in err and "承認しました" not in out


def test_from_registerの行を消しても行が変わっていなければ控えから読み直して承認できる(capsys, env, clock,
                                                                    isolated_account_factory):
    account = isolated_account_factory()
    path, _digest = _approved_draft(capsys, env, clock, account)
    digest = _restrip(capsys, path)
    rc_, out, err = run(capsys, "approve", path, "--confirm", digest, "--by", "m")
    assert rc_ == 0, err
    assert "承認しました" in out


def test_柵_from_registerの行を消したあとに行が変わったら承認しない(capsys, env, clock, isolated_account_factory):
    account = isolated_account_factory()
    path, _digest = _approved_draft(capsys, env, clock, account)
    digest = _restrip(capsys, path)
    edit(env["tmp"] / "cafe-sample" / "臨時" / "2026-12-12_設備の点検.md", "close: 18:00", "close: 19:00")
    confirm(capsys, "cafe-sample", "cafe-special-2026-12-12")
    rc_, out, err = run(capsys, "approve", path, "--confirm", digest, "--by", "m")
    assert rc_ == 1 and "register_row_changed" in err and "承認しました" not in out


def test_柵_頭の出所を別の行に書き換えた下書きは承認しない(capsys, env, clock, isolated_account_factory):
    account = isolated_account_factory()
    path, _digest = _approved_draft(capsys, env, clock, account)
    edit(pathlib.Path(path), "from_register: cafe-sample/cafe-special-2026-12-12",
         "from_register: cafe-sample/cafe-event-2026-12-14")
    commit_and_push_path(path, message="test: other origin")
    digest = approve_first(capsys, path)
    rc_, out, err = run(capsys, "approve", path, "--confirm", digest, "--by", "m")
    assert rc_ == 1 and "register_row_changed" in err and "from_register_mismatch" in err
    assert "頭の行はそのまま残して" in err


def test_柵_出さない期限を作ったときより後に動かした下書きは承認しない(capsys, env, clock, isolated_account_factory):
    account = isolated_account_factory()
    path, _digest = _approved_draft(capsys, env, clock, account, row="cafe-event-2026-12-14")
    before = queuefile.parse(path).front_matter["not_after"]
    text = pathlib.Path(path).read_text(encoding="utf-8")
    old = next(line for line in text.split("\n") if line.startswith("not_after:"))
    edit(pathlib.Path(path), old, "not_after: 2026-12-31T23:59:00+09:00")
    assert before
    commit_and_push_path(path, message="test: move not_after")
    digest = approve_first(capsys, path)
    rc_, out, err = run(capsys, "approve", path, "--confirm", digest, "--by", "m")
    assert rc_ == 1 and "register_draft_expired" in err and "not_after_moved" in err


def test_柵_控えにも頭にも出所の無い台帳の名前の下書きは承認しない(capsys, env, clock, isolated_account_factory):
    from thth import draft_origin
    from thth.register import ledger
    account = isolated_account_factory()
    path, _digest = _approved_draft(capsys, env, clock, account)
    pathlib.Path(ledger.state_dir_for("cafe-sample"), draft_origin.FILE).unlink()
    digest = _restrip(capsys, path)
    rc_, out, err = run(capsys, "approve", path, "--confirm", digest, "--by", "m")
    assert rc_ == 1 and "register_recheck_unavailable" in err and "from_register_missing" in err


def test_台帳の下書きの控えは道と頭の出所の行だけで本文を持たない(capsys, env, clock, isolated_account_factory):
    from thth import draft_origin
    from thth.register import ledger
    account = isolated_account_factory()
    path, _digest = _approved_draft(capsys, env, clock, account)
    state = ledger.state_dir_for("cafe-sample")
    found = draft_origin.read(state)
    [(key, head)] = found.items()
    assert key == draft_origin.key_of(path)
    assert set(head) == {"from_register", "from_register_digest", "from_register_commit", "from_register_trigger",
                         "not_after"}
    raw = pathlib.Path(state, draft_origin.FILE).read_text(encoding="utf-8")
    assert "お休み" not in raw and "設備の点検" not in raw
    assert oct(pathlib.Path(state, draft_origin.FILE).stat().st_mode & 0o777) == "0o600"


# ------------------------------------------------------------------ use の本文（監査 5.19.0 直したほうがよい 3）

def test_柵_見本の台帳の既定で出さない欄の値は同じ行の本文に現れない():
    """見本を写して使う持ち主が、理由や住所を本文に書く形を覚えないように。"""
    from thth.register import form
    root = pathlib.Path(__file__).resolve().parent.parent / "registers.example"
    checked = 0
    for folder in sorted(p for p in root.iterdir() if p.is_dir()):
        for row in form.scan(str(folder))["rows"]:
            for key in kinds.private_by_default(row.kind):
                value = (row.head.get(key) or "").strip()
                if value:
                    checked += 1
                    assert value not in row.body, f"{folder.name}/{row.path}: {key} の値が本文にある"
    assert checked >= 8


def test_柵_useは既定で出さない欄を持つ種類の本文を返さない(capsys, env, clock):
    path = add(capsys, env, "cafe-sample", kind="store")
    special = path / "臨時" / "2026-12-12_設備の点検.md"
    # 持ち主が本文に理由を書いてしまっても、use の材料には出ない。
    special.write_text(special.read_text(encoding="utf-8") + "設備の点検のため。\n", encoding="utf-8")
    confirm(capsys, "cafe-sample", "cafe-special-2026-12-12")
    rc_, value, err = jrun(capsys, "register", "use", "cafe-sample", "cafe-special-2026-12-12")
    assert rc_ == 0, err
    assert value["body"] is None and "reason" not in value["fields"]
    assert "設備の点検" not in json.dumps(value, ensure_ascii=False)
    assert any("public_fields に body" in n for n in value["notes"])
    assert any("本文は外に出る材料" in n for n in value["notes"])
    rc_, out, _ = run(capsys, "register", "use", "cafe-sample", "cafe-special-2026-12-12")
    assert rc_ == 0 and "設備の点検" not in out and "本文は外に出る材料" in out
    # 持ち主が public_fields に body と書けば本文を返す（店の行も同じ）。
    edit(special, "reason: 設備の点検", "reason: 設備の点検\npublic_fields: body")
    confirm(capsys, "cafe-sample", "cafe-special-2026-12-12")
    rc_, value, err = jrun(capsys, "register", "use", "cafe-sample", "cafe-special-2026-12-12")
    assert rc_ == 0, err
    assert value["body"] and "午後 2 時から" in value["body"] and "reason" not in value["fields"]


def test_既定で出さない欄の無い種類はuseが本文を返しprivate_fieldsのbodyで隠せる(capsys, env, clock):
    path = add(capsys, env, "cafe-sample", kind="store")
    confirm(capsys, "cafe-sample", "cafe-event-2026-12-14")
    rc_, value, _ = jrun(capsys, "register", "use", "cafe-sample", "cafe-event-2026-12-14")
    assert rc_ == 0 and value["body"]
    edit(path / "催し" / "2026-12-14_試食会.md", "public: true", "public: true\nprivate_fields: body")
    confirm(capsys, "cafe-sample", "cafe-event-2026-12-14")
    rc_, value, _ = jrun(capsys, "register", "use", "cafe-sample", "cafe-event-2026-12-14")
    assert rc_ == 0 and value["body"] is None


# ------------------------------------------------------------------ 例外で落ちない（監査 5.19.0 の気づき 5）

def test_組むのに要る欄をprivate_fieldsに入れた行は例外でなく前向きに断る(capsys, env, clock, isolated_account_factory):
    account = isolated_account_factory()
    path = add(capsys, env, "cafe-sample", kind="store")
    edit(path / "臨時" / "2026-12-12_設備の点検.md", "public: true", "public: true\nprivate_fields: date")
    confirm(capsys, "cafe-sample", "cafe-special-2026-12-12")
    for argv in (("draft", "cafe-sample", "--to", account["name"], "--row", "cafe-special-2026-12-12"),
                 ("paste", "cafe-sample", "cafe-special-2026-12-12")):
        rc_, value, _ = jrun(capsys, "register", *argv)
        assert rc_ == 2 and value["code"] == "register_row_invalid" and value["detail"] == "private_needed", argv
        rc_, out, err = run(capsys, "register", *argv)
        assert rc_ == 2 and "private_fields から外して" in err and "Traceback" not in err, argv
    assert drafts(account) == []


def test_draftのbyの形が違えばほかの命令と同じに断る(capsys, env, clock, isolated_account_factory):
    account = isolated_account_factory()
    add(capsys, env, "cafe-sample", kind="store")
    confirm(capsys, "cafe-sample", "cafe-special-2026-12-12")
    rc_, value, _ = jrun(capsys, "register", "draft", "cafe-sample", "--to", account["name"], "--row",
                         "cafe-special-2026-12-12", "--by", "a\tb")
    assert rc_ == 2 and value["code"] == "register_args_invalid" and value["detail"] == "by"
    assert drafts(account) == []


def test_柵_not_afterの行を消した台帳の下書きは承認しない(capsys, env, clock, isolated_account_factory):
    """監査 5.19.0 の「緑のまま残った」1 つ: 出さない期限を消した下書きを通す壊しで赤にする。"""
    account = isolated_account_factory()
    path, _digest = _approved_draft(capsys, env, clock, account, row="cafe-event-2026-12-14")
    digest = _restrip(capsys, path, keys=("not_after",))
    rc_, out, err = run(capsys, "approve", path, "--confirm", digest, "--by", "m")
    assert rc_ == 1 and "register_draft_expired" in err and "not_after_missing" in err and "承認しました" not in out
