"""台帳の器 段 3 の検収（Fable・11/14）の重い穴と、本線が決めた線引き。

答え: `thth-work/audit-s3/答え_段3の検収_Fable_2026-11-14.md`。反例は検収の台本 `probes.py` の 4〜7 を写した物。

1. 封の中の判断と同じ番号を書いた「止めない」を足すと、止めるが外れる
   → 「封の中か」は番号でなく記録の中の位置で決める。番号の重なり・中身と食い違う番号は読む入口で断る。
2. `on_narrow: drop` の出し口が、項目の集まりが増えなければ、封のときと違う中身を緑で通す
   → 外して配るときも、残った項目と表の行が封のときの物と同じかを、封の指紋と照らす。
3. 期限の切れた「止めない」が、止めるを外し続ける → 止めないにも期限を見る。
4. 封の外の狭める判断が、`at` の字の比べで封の中の広げる判断に負ける
   → 封の外の狭める判断は、`at` によらず封の中の広げる判断に勝つ。日時は時刻として比べる。
5. `parse_judgments` が判断の 1 行の形（`J.shape_problem`）を見ていない。
"""
from __future__ import annotations

import json
import pathlib
import shutil

import pytest

from thth.register import judgment as J
from thth.register import route as R
from thth.register import vessel as V
from thth.register.errors import RegisterRefused
from tests.test_register_route import (  # noqa: F401
    BRAZIL, MILK, SEALED_AT, accept, baseline, env, jrun, judge, ledger, use, verify, words,
)
from tests.test_register_route_holes import NOW, _outlets, publish

CLAIMS = "data/knowledge/claims.csv"


def _jpath(led) -> pathlib.Path:
    return pathlib.Path(J.path_of(str(led)))


def _lines(led) -> list:
    return [json.loads(x) for x in _jpath(led).read_text().splitlines() if x.strip()]


def _add_raw(led, obj):
    with open(_jpath(led), "a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False, sort_keys=True) + "\n")


def _has(site, word: str) -> bool:
    return word.encode() in (site / CLAIMS).read_bytes()


def _stopline(led, word, conclusion, at, *, question="stop", until=None):
    rec = V.open_vessel(str(led)).get(BRAZIL)
    body = {"schema": J.SCHEMA, "target": {"record": BRAZIL}, "question": question,
            "answer": {"word": word, "conclusion": conclusion, "scope": None},
            "reason": {"kind": None, "text": None}, "by": "m", "how": None, "at": at, "until": until,
            "path": "acceptance", "carries": None, "seen": {"version": rec.version, "depend": None}}
    body["id"] = J.new_id(body)
    return body


# ------------------------------------------------------------------ 重い 1: 番号を借りた「止めない」

@pytest.mark.parametrize("mode", ["stop", "drop"])
def test_重い1_封の中の番号を借りた止めないを足しても止めるは外れない(env, capsys, mode):
    led, site = ledger(env, "kopicha"), env / "site"
    if mode == "drop":
        _outlets(led, lambda o: o["facts"].update(on_narrow="drop"))
    baseline(capsys, led, "facts")
    accept(capsys, led, "facts", site)
    pub1 = env / "public/1.json"
    publish(capsys, led, "facts", site, pub1)
    old_csv = (site / CLAIMS).read_bytes()
    sealed_id = _lines(led)[0]["id"]
    judge(capsys, led, BRAZIL, "--stop", "--outlet", "facts")
    prev = pub1
    if mode == "drop":
        rc, v, err = jrun(capsys, "register", "export", led, "--outlet", "facts", "--to", site, "--as-of", NOW)
        assert rc == 0, err
        prev = env / "public/2.json"
        publish(capsys, led, "facts", site, prev, "--previous", pub1)
        (site / CLAIMS).write_bytes(old_csv)          # 止めた記録を材料に戻す
    rc, v = verify(capsys, led, "facts", site, NOW, "--previous", prev)
    assert rc != 0 and "narrowed_after_seal" in words(v), v
    forged = dict(_lines(led)[-1], id=sealed_id, at="2027-03-21T09:00:00+09:00", by="someone",
                  answer={"word": "resume", "conclusion": "good", "scope": None})
    _add_raw(led, forged)                               # 足すだけ。受け入れはしない
    rc, v = verify(capsys, led, "facts", site, NOW, "--previous", prev)
    assert rc != 0 and not (v or {}).get("ok"), v
    assert v["code"] == "register_judgment_invalid" and "id_same_as_line 1" in v["detail"], v
    rc, u = use(capsys, led, BRAZIL, "facts", "fact")
    assert rc != 0 and u["code"] == "register_judgment_invalid", u


def test_重い1_番号を中身から作り直して合わない行は断る(env, capsys):
    led, site = ledger(env, "kopicha"), env / "site"
    baseline(capsys, led, "facts")
    line = _stopline(led, "stop", "bad", NOW)
    line["id"] = "j-0000000000000000"
    _add_raw(led, line)
    with pytest.raises(RegisterRefused) as e:
        R.parse_judgments(R.judgments_raw(str(led)))
    assert e.value.code == "register_judgment_invalid" and "id_not_from_content" in e.value.detail


def test_重い1_封の中かは記録の中の位置で決める(env, capsys):
    """入口を通らずに作った判断の集まりでも、封の外の行は、封の中の番号を名乗っても広げない。"""
    led = ledger(env, "kopicha")
    vessel = V.open_vessel(str(led))
    sealed_lift = _stopline(led, "resume", "good", "2027-03-01T10:00:00+09:00")
    stop = _stopline(led, "stop", "bad", "2027-03-21T09:00:00+09:00")
    forged = dict(_stopline(led, "resume", "good", "2027-03-21T10:00:00+09:00"), id=sealed_lift["id"])
    ctx = R.Ctx(str(led), vessel, [sealed_lift], [sealed_lift, stop, forged], R.parse_time(NOW))
    got = R._stop_state(ctx.every, ctx.n_sealed, vessel, BRAZIL, None, "facts", ctx.as_of)
    assert got is not None and got["answer"]["conclusion"] == "bad"


# ------------------------------------------------------------------ 重い 2: drop の出し口で中身を変える

def _drop(env, outlet, tag):
    led, site = ledger(env, "kopicha"), env / ("site-" + tag)
    _outlets(led, lambda o: o[outlet].update(on_narrow="drop"))
    return led, site


@pytest.mark.parametrize("on_narrow", ["drop", "stop"])
def test_重い2_dropでも直す前の版へ戻すと赤(env, capsys, on_narrow):
    led, site = ledger(env, "kopicha"), env / "site"
    _outlets(led, lambda o: o["facts"].update(on_narrow=on_narrow))
    baseline(capsys, led, "facts")
    accept(capsys, led, "facts", site)
    claims = led / CLAIMS
    v1 = claims.read_bytes()
    claims.write_bytes(v1.replace("17.3%".encode(), "17.1%".encode()))
    judge(capsys, led, MILK, "--question", "may_publish@facts", "--conclusion", "good")
    v = accept(capsys, led, "facts", site)
    assert v["generation"] == 2 and _has(site, "17.1%")
    pub = env / "public/2.json"
    publish(capsys, led, "facts", site, pub)
    claims.write_bytes(v1)
    shutil.copy(claims, site / CLAIMS)                  # 直す前へ戻す。受け入れない
    rc, v = verify(capsys, led, "facts", site, NOW, "--previous", pub)
    assert rc != 0 and "narrowed_after_seal" in words(v), v


@pytest.mark.parametrize("on_narrow", ["drop", "stop"])
def test_重い2_dropでも表の見出しを入れ替えると赤(env, capsys, on_narrow):
    led, site = ledger(env, "kopicha"), env / "site"
    _outlets(led, lambda o: o["facts"].update(on_narrow=on_narrow))
    baseline(capsys, led, "facts")
    accept(capsys, led, "facts", site)
    pub = env / "public/1.json"
    publish(capsys, led, "facts", site, pub)
    claims = led / CLAIMS
    head, rest = claims.read_bytes().split(b"\n", 1)
    head2 = head.replace(b"object_text_ja,statement_ja", b"statement_ja,object_text_ja").replace(
        b"as_of,valid_until", b"valid_until,as_of")
    assert head2 != head
    claims.write_bytes(head2 + b"\n" + rest)
    shutil.copy(claims, site / CLAIMS)
    rc, v = verify(capsys, led, "facts", site, NOW, "--previous", pub)
    assert rc != 0 and "narrowed_after_seal" in words(v), v


@pytest.mark.parametrize("on_narrow", ["drop", "stop"])
def test_重い2_dropでも問いが依らない欄を書き換えると赤(env, capsys, on_narrow):
    led, site = ledger(env, "kopicha"), env / "site"
    _outlets(led, lambda o: o["strict"].update(on_narrow=on_narrow))
    baseline(capsys, led, "strict")
    accept(capsys, led, "strict", site)
    pub = env / "public/1.json"
    publish(capsys, led, "strict", site, pub)
    claims = led / CLAIMS
    b = claims.read_bytes()
    b2 = b.replace(b"c-term-tea-grade-ctc-res-milktea1-24,term-ctc,", b"c-term-tea-grade-ctc-res-milktea1-24,origin-brazil,")
    assert b2 != b
    claims.write_bytes(b2)
    rc, v, err = jrun(capsys, "register", "export", led, "--outlet", "strict", "--to", site, "--as-of", NOW)
    rc, v = verify(capsys, led, "strict", site, NOW, "--previous", pub)
    assert rc != 0 and "narrowed_after_seal" in words(v), v


def test_重い2_dropで止めた行だけを外して配るのは緑のまま(env, capsys):
    """直しのあとも、drop の本来の道（止めるで外して、封をし直さずに配る）は緑。"""
    led, site = _drop(env, "facts", "ok")
    baseline(capsys, led, "facts")
    accept(capsys, led, "facts", site)
    pub1 = env / "public/1.json"
    publish(capsys, led, "facts", site, pub1)
    judge(capsys, led, BRAZIL, "--stop", "--outlet", "facts")
    rc, v, err = jrun(capsys, "register", "export", led, "--outlet", "facts", "--to", site, "--as-of", NOW)
    assert rc == 0, err
    v = publish(capsys, led, "facts", site, env / "public/2.json", "--previous", pub1)
    assert v["warnings"][-1]["word"] == "dropped" and v["warnings"][-1]["items"], v
    # 外したうえで、残った行の 1 つを書き換えると赤
    claims = site / CLAIMS
    claims.write_bytes(claims.read_bytes().replace("17.3%".encode(), "17.9%".encode()))
    rc, v = verify(capsys, led, "facts", site, NOW, "--previous", env / "public/2.json")
    assert rc != 0, v


# ------------------------------------------------------------------ 重い 3: 期限の切れた「止めない」

def test_重い3_期限の切れた止めないは止めるを外さない(env, capsys):
    led, site = ledger(env, "kopicha"), env / "site"
    baseline(capsys, led, "facts")
    judge(capsys, led, BRAZIL, "--stop", "--outlet", "facts")
    judge(capsys, led, BRAZIL, "--resume", "--outlet", "facts", "--until", "2027-03-31")
    accept(capsys, led, "facts", site)
    assert _has(site, "c-origin-brazil-024")
    rc, v = verify(capsys, led, "facts", site, "2027-03-25", "--no-previous")
    assert rc == 0 and v["ok"], v
    rc, v = verify(capsys, led, "facts", site, "2027-04-02", "--no-previous")
    assert rc != 0 and "narrowed_after_seal" in words(v), v
    rc, u, err = jrun(capsys, "register", "use", led, BRAZIL, "--outlet", "facts", "--as", "fact", "--as-of",
                      "2027-04-02")
    assert rc != 0 and u["why"] == "stopped", u


# ------------------------------------------------------------------ 4: 封の外の狭める判断は日時の字で負けない

def _sealed_lift(env, capsys):
    led, site = ledger(env, "kopicha"), env / "site"
    baseline(capsys, led, "facts")
    _add_raw(led, _stopline(led, "stop", "bad", "2027-03-10T10:00:00+09:00"))
    _add_raw(led, _stopline(led, "resume", "good", "2027-03-15T10:00:00+09:00"))
    accept(capsys, led, "facts", site)
    assert _has(site, "c-origin-brazil-024")
    return led, site


@pytest.mark.parametrize("at", ["2027-03-15T03:00:00+00:00", "2027-03-14T10:00:00+09:00",
                                "2027-03-16T10:00:00+09:00"])
def test_4_封の後に足した止めるはatによらず効く(env, capsys, at):
    led, site = _sealed_lift(env, capsys)
    _add_raw(led, _stopline(led, "stop", "bad", at))
    rc, v = verify(capsys, led, "facts", site, NOW, "--no-previous")
    assert rc != 0 and "narrowed_after_seal" in words(v), v


def test_4_封の後に届いただめは前の日時でも封の中のよいに勝つ(env, capsys):
    led, site = _sealed_lift(env, capsys)
    J.append(str(led), [J.make(V.open_vessel(str(led)), {"record": MILK}, "may_publish@facts", by="other",
                               at="2020-01-01T00:00:00+09:00", conclusion="bad")])
    rc, v = verify(capsys, led, "facts", site, NOW, "--no-previous")
    assert rc != 0 and "narrowed_after_seal" in words(v), v


def test_4_日時は字でなく時刻として比べる_封の中のUTCの止めない(env, capsys):
    """封の中で、止める（日本の時刻 10:00）のあとに、UTC の書き方で 12:00 の止めない → 止めないが新しい。"""
    led, site = ledger(env, "kopicha"), env / "site"
    baseline(capsys, led, "facts")
    _add_raw(led, _stopline(led, "stop", "bad", "2027-03-15T10:00:00+09:00"))
    _add_raw(led, _stopline(led, "resume", "good", "2027-03-15T03:00:00+00:00"))
    accept(capsys, led, "facts", site)
    assert _has(site, "c-origin-brazil-024")
    rc, v = verify(capsys, led, "facts", site, NOW, "--no-previous")
    assert rc == 0 and v["ok"], v


def test_4_読めない日時の行は入口で断る(env, capsys):
    led = ledger(env, "kopicha")
    baseline(capsys, led, "facts")
    _add_raw(led, _stopline(led, "stop", "bad", "きのうの夜"))
    with pytest.raises(RegisterRefused) as e:
        R.parse_judgments(R.judgments_raw(str(led)))
    assert e.value.code == "register_judgment_invalid" and e.value.detail.endswith(" at"), e.value.detail


# ------------------------------------------------------------------ 5: parse_judgments の形の点検

def test_5_parse_judgmentsは判断の1行の形を見る(env, capsys):
    led = ledger(env, "kopicha")
    baseline(capsys, led, "facts")
    line = _stopline(led, "stop", "bad", NOW)
    line["question"] = ""
    line["id"] = J.new_id(line)
    _add_raw(led, line)
    with pytest.raises(RegisterRefused) as e:
        R.parse_judgments(R.judgments_raw(str(led)))
    assert e.value.code == "register_judgment_invalid" and e.value.detail.endswith("question"), e.value.detail
