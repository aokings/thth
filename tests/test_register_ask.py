"""聞いて埋める（計画 `docs/計画_実店舗_2026-11-13.md` 段 3）。見本の店を使い、時刻は 2026-12-08 10:00 JST に止める。

柵:
  - 上限を超えた問いは出ない（1 日の上限・同じ穴は日を空ける）。
  - 「違う」と言われた推測を二度聞かない。
  - 会話の一言（answer の yes）だけでは、営業の時間と臨時が「本人が認めた」にならない。
  - 問いの記録は持ち主の台帳のフォルダにあり、台帳の行としては読まない。
  - 問いの数・答えの数・答えから増えた行の数を数えられる。
網には出ない。THTH は持ち主に直接知らせを送らない。
"""
from __future__ import annotations

import datetime
import json
import pathlib

from tests.test_register_base import add, env, jrun, run  # noqa: F401
from tests.test_register_store import NOW, clock, confirm  # noqa: F401
from thth import jst
from thth.register import holes, questions


def ask(capsys, name="cafe-sample"):
    rc_, value, err = jrun(capsys, "register", "ask", name, "--by", "m")
    assert rc_ == 0, err
    return value["questions"]


def test_穴の一覧は推測と欠けと季節と品の思いを並べる(capsys, env, clock):
    add(capsys, env, "goods-sample", kind="store")
    rc_, value, _ = jrun(capsys, "register", "holes", "goods-sample")
    found = {hole["hole"] for hole in value["holes"]}
    assert "guess:goods-hours" in found and "season:year_end:2026" in found
    assert "voice:goods-item-tenugui" in found
    assert all(hole["hint"] for hole in value["holes"])
    # 年末年始の臨時の行がある店には、季節の穴を出さない。
    add(capsys, env, "cafe-sample", kind="store")
    rc_, value, _ = jrun(capsys, "register", "holes", "cafe-sample")
    assert not [h for h in value["holes"] if h["kind"] == "season"]


def test_店の行と営業の時間の行が無ければ穴にする(capsys, env, clock):
    path = add(capsys, env, "goods-sample", kind="store")
    (path / "店.md").rename(path / "店.txt")
    (path / "営業の時間.md").rename(path / "営業の時間.txt")
    found = {h["hole"] for h in jrun(capsys, "register", "holes", "goods-sample")[1]["holes"]}
    assert {"store:missing", "hours:missing"} <= found


def test_柵_上限を超えた問いは出ない(capsys, env, clock):
    add(capsys, env, "cafe-sample", kind="store")
    assert len(jrun(capsys, "register", "holes", "cafe-sample")[1]["holes"]) > holes.ASK_MAX_PER_DAY
    first = ask(capsys)
    assert len(first) == holes.ASK_MAX_PER_DAY
    assert ask(capsys) == []                                       # 同じ日はもう出ない
    clock["now"] = NOW + datetime.timedelta(days=1)
    second = ask(capsys)
    assert second and not ({q["hole"] for q in second} & {q["hole"] for q in first})   # 同じ穴は 7 日空ける
    clock["now"] = NOW + datetime.timedelta(days=holes.ASK_GAP_DAYS)
    again = {q["hole"] for q in ask(capsys)}
    assert again & {q["hole"] for q in first}                      # 7 日たてば同じ穴をまた聞ける


def test_柵_違うと言われた推測を二度聞かない(capsys, env, clock):
    path = add(capsys, env, "cafe-sample", kind="store")
    [question] = [q for q in ask(capsys) if q["hole"] == "guess:cafe-store"] or [None]
    assert question is not None
    rc_, value, err = jrun(capsys, "register", "answer", "cafe-sample", question["question"], "--result", "no",
                           "--by", "m", "--asked", "店の名前は 喫茶 みほん で合っていますか", "--said", "違う")
    assert rc_ == 0, err
    assert value["effect"] == "denied"
    assert "confirmed: 本人が違うと言った" in (path / "店.md").read_text(encoding="utf-8")
    for days in (1, holes.ASK_GAP_DAYS, 30):
        clock["now"] = NOW + datetime.timedelta(days=days)
        assert "guess:cafe-store" not in {q["hole"] for q in ask(capsys)}
        assert "guess:cafe-store" not in {h["hole"] for h in jrun(capsys, "register", "holes", "cafe-sample")[1]["holes"]}


def test_柵_答えの一言だけでは営業の時間と臨時は認めたにならない(capsys, env, clock):
    path = add(capsys, env, "cafe-sample", kind="store")
    [question] = [q for q in ask(capsys) if q["hole"] == "guess:cafe-hours"]
    rc_, value, err = jrun(capsys, "register", "answer", "cafe-sample", question["question"], "--result", "yes",
                           "--by", "m")
    assert rc_ == 0, err
    assert value["effect"] == "needs_confirm" and "confirm" in value["notes"][0]
    assert "confirmed: 推測" in (path / "営業の時間.md").read_text(encoding="utf-8")
    assert jrun(capsys, "register", "use", "cafe-sample", "cafe-hours")[1]["code"] == "register_row_guess"


def test_品や店の推測はyesの答えで認めたになる(capsys, env, clock):
    add(capsys, env, "cafe-sample", kind="store")
    [question] = [q for q in ask(capsys) if q["hole"] == "guess:cafe-store"]
    rc_, value, err = jrun(capsys, "register", "answer", "cafe-sample", question["question"], "--result", "yes",
                           "--by", "m")
    assert rc_ == 0, err
    assert value["effect"] == "confirmed"
    assert jrun(capsys, "register", "use", "cafe-sample", "cafe-store")[0] == 0


def test_柵_聞いたあとに中身が変わった行は答えのyesでは認めない(capsys, env, clock):
    """監査 5.19.0 直したほうがよい 2: 持ち主が「はい」と言ったのは聞かれたときの中身。"""
    path = add(capsys, env, "cafe-sample", kind="store")
    [question] = [q for q in ask(capsys) if q["hole"] == "guess:cafe-store"]
    store = path / "店.md"
    store.write_text(store.read_text(encoding="utf-8") + "見せていない 1 行\n", encoding="utf-8")
    rc_, value, err = jrun(capsys, "register", "answer", "cafe-sample", question["question"], "--result", "yes",
                           "--by", "m")
    assert rc_ == 0, err
    assert value["effect"] == "changed_since_asked"
    assert "聞いたときから変わっています" in value["notes"][0] and "confirm" in value["notes"][0]
    assert "confirmed: 推測" in store.read_text(encoding="utf-8")
    assert jrun(capsys, "register", "use", "cafe-sample", "cafe-store")[1]["code"] == "register_row_guess"


def test_柵_askを通さずに書いた問いへのyesでは認めない(capsys, env, clock):
    path = add(capsys, env, "cafe-sample", kind="store")
    log = path / questions.LOG_FILE
    log.write_text(json.dumps({"type": "asked", "id": "q-20261208-abcdef", "hole": "guess:cafe-store",
                               "row": "cafe-store", "at": jst.iso(NOW), "by": "m"}, ensure_ascii=False) + "\n",
                   encoding="utf-8")
    rc_, value, err = jrun(capsys, "register", "answer", "cafe-sample", "q-20261208-abcdef", "--result", "yes",
                           "--by", "m")
    assert rc_ == 0, err
    assert value["effect"] == "changed_since_asked"
    assert jrun(capsys, "register", "use", "cafe-sample", "cafe-store")[1]["code"] == "register_row_guess"


def test_聞いた問いの記録は行の中身の指紋を持ち中身は持たない(capsys, env, clock):
    from thth.register import form
    path = add(capsys, env, "cafe-sample", kind="store")
    [question] = [q for q in ask(capsys) if q["hole"] == "guess:cafe-store"]
    [entry] = [json.loads(line) for line in (path / questions.LOG_FILE).read_text(encoding="utf-8").splitlines()
               if json.loads(line)["id"] == question["question"]]
    assert entry["digest"] == form.fingerprint((path / "店.md").read_text(encoding="utf-8"))
    assert "喫茶" not in json.dumps(entry, ensure_ascii=False)


def test_問いの記録は持ち主の台帳にあり数を数えられる(capsys, env, clock):
    path = add(capsys, env, "cafe-sample", kind="store")
    asked = ask(capsys)
    voice = path / "品" / "冬のブレンドの思い.md"
    voice.write_text("---\nid: cafe-voice-winter-blend\nkind: voice\nstatus: 本採用\nsource: asked\nconfirmed: 推測\n"
                     "review_by: 2027-06-30\npublic: true\nabout: cafe-item-winter-blend\n---\n寒い朝に飲んでほしい。\n",
                     encoding="utf-8")
    jrun(capsys, "register", "answer", "cafe-sample", asked[0]["question"], "--result", "other", "--by", "m",
         "--row", "cafe-voice-winter-blend", "--said", "寒い朝に飲んでほしい")
    jrun(capsys, "register", "answer", "cafe-sample", asked[1]["question"], "--result", "skip", "--by", "m")
    log = path / questions.LOG_FILE
    assert log.exists() and len(log.read_text(encoding="utf-8").splitlines()) == len(asked) + 2
    rc_, value, _ = jrun(capsys, "register", "questions", "cafe-sample")
    assert value == {"register": "cafe-sample", "asked": 3, "answered": 1, "skipped": 1, "rows_from_answers": 1,
                     "open": 1}
    # 問いの記録は台帳の行として数えない（.md でない）。
    assert jrun(capsys, "register", "check", "cafe-sample")[1]["rows"] == 12
    # 思いの 1 枚を足した品は、思いの穴から外れる。
    found = {h["hole"] for h in jrun(capsys, "register", "holes", "cafe-sample")[1]["holes"]}
    assert "voice:cafe-item-winter-blend" not in found


def test_同じ問いに二度答えない_知らない問いは断る(capsys, env, clock):
    add(capsys, env, "cafe-sample", kind="store")
    [first, *_] = ask(capsys)
    assert jrun(capsys, "register", "answer", "cafe-sample", first["question"], "--result", "skip", "--by", "m")[0] == 0
    rc_, value, _ = jrun(capsys, "register", "answer", "cafe-sample", first["question"], "--result", "yes", "--by", "m")
    assert rc_ == 2 and value["code"] == "register_question_answered"
    rc_, value, _ = jrun(capsys, "register", "answer", "cafe-sample", "q-20261208-000000", "--result", "yes",
                         "--by", "m")
    assert rc_ == 2 and value["code"] == "register_question_missing"
    rc_, value, _ = jrun(capsys, "register", "answer", "cafe-sample", "x", "--result", "yes", "--by", "m")
    assert rc_ == 2 and value["code"] == "register_args_invalid"


def test_答えの文に秘密らしい値があれば断る(capsys, env, clock):
    path = add(capsys, env, "cafe-sample", kind="store")
    [first, *_] = ask(capsys)
    rc_, value, _ = jrun(capsys, "register", "answer", "cafe-sample", first["question"], "--result", "other",
                         "--by", "m", "--said", "password: hunter2hunter2abc123")
    assert rc_ == 2 and value["code"] == "register_args_invalid"
    assert "hunter2" not in (path / questions.LOG_FILE).read_text(encoding="utf-8")


def test_壊れた問いの記録では聞かない(capsys, env, clock):
    path = add(capsys, env, "cafe-sample", kind="store")
    (path / questions.LOG_FILE).write_text("壊れた行\n", encoding="utf-8")
    rc_, value, _ = jrun(capsys, "register", "ask", "cafe-sample", "--by", "m")
    assert rc_ == 2 and value["code"] == "register_question_log_invalid"


def test_問いの記録の行の形(capsys, env, clock):
    path = add(capsys, env, "cafe-sample", kind="store")
    ask(capsys)
    rows = [json.loads(line) for line in (path / questions.LOG_FILE).read_text(encoding="utf-8").splitlines()]
    # 行のある穴は聞いたときの中身の指紋を持つ（監査 5.19.0 の 2）。行の無い穴（欠け・季節）は持たない。
    assert {tuple(sorted(row)) for row in rows if row["row"]} == {("at", "by", "digest", "hole", "id", "row", "type")}
    assert {tuple(sorted(row)) for row in rows if not row["row"]} <= {("at", "by", "hole", "id", "row", "type")}
    assert all(jst.parse(row["at"]) for row in rows)
