"""4.0 S1 §3.4・§10 T-9a: 配信の台帳 `state/<account>/deliveries/<delivery>.json` と `thth deliveries`。

- `seq` は 1 ずつ増える・`history[]` は追記だけ
- 他の process が先に書いていたら `server_file_changed` で断る（`replace_at(expected=…)`）
- `thth deliveries <project>` は読めない 1 本を `cannot_say` に入れて、他は出す

口座は SNS の形（`media: threads`）で作る——配信の台帳は主体の台帳を読まないので、
口座の loader の はてな の検査（別の段）に依存しない。
"""
from __future__ import annotations

import json
import os
import stat
import subprocess
import sys

import pytest

from tests.conftest import run_thth
from thth import accounts as accounts_mod
from thth import cli, deliveries, deliveries_cli

AT1 = "2026-10-20T08:00:12+09:00"
AT2 = "2026-10-20T08:00:41+09:00"
AT3 = "2026-10-21T09:00:00+09:00"


def _new(account, **kw):
    args = dict(principal="hatena-masaru", blog_id="Kopicha.hatenablog.com",
                article="kopicha/2026-10-arabica", at=AT1,
                fields={"article_source": "articles/2026-10-arabica.md", "form": "full",
                        "queue_file": "docs/sns/queue/2026-10-20-arabica-hatena.md"},
                row={"op": "create", "approved_sha": "a" * 64, "origin": {"via": "timer"}})
    args.update(kw)
    return deliveries.create(account, **args)


@pytest.fixture
def lane(isolated_account_factory):
    a = isolated_account_factory("kopicha-a", project="kopicha")
    b = isolated_account_factory("kopicha-b", project="kopicha")
    isolated_account_factory("other-c", project="other")
    return a["name"], b["name"]


def test_作ると_seq1_wanted_0600_schema(lane):
    account, _ = lane
    rec = _new(account)
    assert deliveries.is_delivery_id(rec["delivery"])
    assert rec["schema"] == "thth.delivery/1" and rec["seq"] == 1 and rec["state"] == "wanted"
    assert rec["blog_id"] == "kopicha.hatenablog.com"
    assert rec["history"] == [{"seq": 1, "state": "wanted", "at": AT1, "op": "create",
                               "approved_sha": "a" * 64, "origin": {"via": "timer"}}]
    path = deliveries.path_for(account, rec["delivery"])
    assert path == os.path.join(accounts_mod.thth_root(), "state", account, "deliveries",
                                rec["delivery"] + ".json")
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert deliveries.read(account, rec["delivery"]) == rec


def test_seqは1ずつ増え_historyは追記だけ(lane):
    account, _ = lane
    rec = _new(account)
    d = rec["delivery"]
    first = json.loads(json.dumps(rec["history"]))
    r2 = deliveries.advance(account, d, state="inflight", at=AT1, row={"op": "create"})
    r3 = deliveries.advance(account, d, state="published", at=AT2,
                            fields={"entry_id": "tag:x-1", "published_url": "https://kopicha.hatenablog.com/e/1",
                                    "checked_rev": "c" * 40, "readback_sha": "e" * 64},
                            row={"entry_id": "tag:x-1"})
    assert [r2["seq"], r3["seq"]] == [2, 3]
    assert [row["seq"] for row in r3["history"]] == [1, 2, 3]
    assert [row["state"] for row in r3["history"]] == ["wanted", "inflight", "published"]
    # 前の行は 1 バイトも変わらない（追記だけ）。
    assert r3["history"][:1] == first
    assert r3["history"][:2] == r2["history"]
    assert r3["published_url"] == "https://kopicha.hatenablog.com/e/1"
    assert deliveries.read(account, d) == r3


def test_決めたseqで書くとき_次の番号でなければ断る(lane):
    account, _ = lane
    d = _new(account)["delivery"]
    deliveries.advance(account, d, state="inflight", at=AT1)
    with pytest.raises(deliveries.DeliveryError, match="seq_mismatch"):
        deliveries.advance(account, d, state="published", at=AT2, seq=2)
    assert deliveries.advance(account, d, state="published", at=AT2, seq=3)["seq"] == 3


def test_他のprocessが先に書いていたらserver_file_changedで断る(lane):
    account, _ = lane
    d = _new(account)["delivery"]
    base = deliveries.load(account, d)
    # 別の process が先に 1 行足す。
    code = ("from thth import deliveries; "
            f"deliveries.advance({account!r}, {d!r}, state='inflight', at={AT1!r})")
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                          env=dict(os.environ), cwd=os.path.dirname(os.path.dirname(__file__)))
    assert done.returncode == 0, done.stderr
    before = open(deliveries.path_for(account, d), "rb").read()
    with pytest.raises(deliveries.DeliveryError, match="server_file_changed"):
        deliveries.advance(account, d, state="published", at=AT2, base=base)
    # 断ったときは何も書かない（相手の行が残る）。
    assert open(deliveries.path_for(account, d), "rb").read() == before
    assert deliveries.read(account, d)["seq"] == 2


def test_同じidで2度作れない(lane):
    account, _ = lane
    d = _new(account)["delivery"]
    with pytest.raises(deliveries.DeliveryError, match="server_file_changed"):
        _new(account, delivery=d)


def test_作ったあとに名乗りの欄は変えられない(lane):
    account, _ = lane
    d = _new(account)["delivery"]
    for key in ("blog_id", "principal", "article", "account", "delivery", "seq", "history"):
        with pytest.raises(deliveries.DeliveryError, match="field_not_allowed"):
            deliveries.advance(account, d, state="inflight", at=AT1, fields={key: "x"})
    with pytest.raises(deliveries.DeliveryError, match="history_key_reserved"):
        deliveries.advance(account, d, state="inflight", at=AT1, row={"seq": 9})
    with pytest.raises(deliveries.DeliveryError, match="invalid_state"):
        deliveries.advance(account, d, state="failed", at=AT1)
    with pytest.raises(deliveries.DeliveryError, match="invalid_at"):
        deliveries.advance(account, d, state="inflight", at="きのう")
    assert deliveries.read(account, d)["seq"] == 1


def _rewrite(account, d, mutate):
    path = deliveries.path_for(account, d)
    data = json.load(open(path, encoding="utf-8"))
    mutate(data)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f)


@pytest.mark.parametrize("mutate,why", [
    (lambda r: r["history"].pop(0), "history"),                         # 行を消した
    (lambda r: r["history"][0].update(seq=5), "history"),               # 番号を書き換えた
    (lambda r: r.update(seq=7), "history"),                             # 上の seq だけ進めた
    (lambda r: r.update(state="published"), "history_state"),           # 上の state だけ変えた
    (lambda r: r.update(schema="thth.delivery/2"), "schema"),
    (lambda r: r.update(account="kopicha-b"), "account_mismatch"),
])
def test_形の崩れた台帳は読めないと言う(lane, mutate, why):
    account, _ = lane
    d = _new(account)["delivery"]
    deliveries.advance(account, d, state="inflight", at=AT1)
    _rewrite(account, d, mutate)
    with pytest.raises(deliveries.DeliveryError, match=f"delivery_unreadable:{why}"):
        deliveries.read(account, d)


# ------------------------------------------------------------------ T-9a

def test_T9a_projectで2口座の台帳を並べ_読めない1本はcannot_say(lane):
    a, b = lane
    da = _new(a)["delivery"]
    deliveries.advance(a, da, state="published", at=AT2)
    db = _new(b, article="kopicha/2026-11-other", at=AT3)["delivery"]
    broken = _new(b, article="kopicha/2026-10-arabica")["delivery"]
    with open(deliveries.path_for(b, broken), "w", encoding="utf-8") as f:
        f.write("{壊れた")
    payload = deliveries_cli.build("kopicha")
    assert payload["target_kind"] == "project"
    assert payload["accounts"] == [a, b]
    assert [r["delivery"] for r in payload["deliveries"]] == [da, db]
    assert payload["n"] == 2
    assert payload["cannot_say"] == ["delivery_unreadable"]
    assert payload["unreadable"] == [{"account": b, "delivery": broken,
                                      "reason": "delivery_unreadable:json"}]
    # 記事で絞る。
    only = deliveries_cli.build("kopicha", article="kopicha/2026-11-other")
    assert [r["delivery"] for r in only["deliveries"]] == [db]
    # 口座の名指し。
    one = deliveries_cli.build(a)
    assert one["target_kind"] == "account" and [r["delivery"] for r in one["deliveries"]] == [da]
    assert one["cannot_say"] == []


def test_T9a_cliの口_jsonと1行表示(lane, capsys):
    a, b = lane
    da = _new(a)["delivery"]
    assert cli.main(["deliveries", "kopicha", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["schema"] == "thth.deliveries/1"
    assert [r["delivery"] for r in payload["deliveries"]] == [da]
    assert cli.main(["deliveries", "kopicha"]) == 0
    out = capsys.readouterr().out
    assert da in out and "wanted（1）" in out and "kopicha.hatenablog.com" in out
    assert cli.main(["deliveries", "no-such-target", "--json"]) == 2
    assert json.loads(capsys.readouterr().out)["cannot_say"] == ["target_unknown"]


def test_T9a_台帳が無い口座は0件で_読めないとは言わない(lane):
    payload = deliveries_cli.build("other")
    assert payload["n"] == 0 and payload["cannot_say"] == [] and payload["unreadable"] == []


def test_T9a_別のprocessのbin_ththからも読める(lane):
    a, _ = lane
    da = _new(a)["delivery"]
    done = run_thth(["deliveries", a, "--json"])
    assert done.returncode == 0, done.stderr
    assert [r["delivery"] for r in json.loads(done.stdout)["deliveries"]] == [da]
