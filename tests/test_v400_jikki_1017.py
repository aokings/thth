"""本物のはてなで 4.0 S2 を確かめて見つけたことの直し（docs/記録_はてなの実機の確かめ_2026-10-17.md の 1・2・5・6）。

  - 1: はてなの口座の `thth run` は採取（collect）を黙って飛ばす（「media='hatenablog' を知りません」を出さない）。
  - 2: 原稿の `delivery:` の配信の台帳が合わない状態のとき、lint は `delivery_unknown` に丸めず状態を言う。
  - 5: はてなの記事の削除が安全装置に当たったときの言い出しは「削除しません」（SNS の「取り下げません」でない）。
  - 6: はてなの口座の `thth doctor` の観測が記録される（`doctor_observation_unavailable` を出さない）。

送信の口は FakeAdapter か偽物の HTTP（`httpsafe.urlopen` の差し替え）。**はてなには一切つながない。**
"""
from __future__ import annotations

import json

import pytest

from tests.hatena_s1_flow_helpers import (ACCOUNT, NOW, FakeAdapter, approve, call, factory_for,  # noqa: F401
                                          first_stage_digest, fm_of, lane, world)
from tests.hatena_s2_http import blog_handler, fake_http, put_secrets  # noqa: F401
from thth import cli, core, deliveries, doctor, guard, hatena_entry, jst, lint, runs

PLAIN_ARTICLE = "# アラビカ\n\n値動きのまとめ。\n\n終わり。\n"


@pytest.fixture
def plain(lane):  # noqa: F811
    lane.write("articles/a.md", PLAIN_ARTICLE)
    return lane


def _publish(lane, adapter=None):
    path = lane.queue()
    approve(path)
    result = core.throw_once(ACCOUNT, production_flag=True, adapter_factory=factory_for(adapter or FakeAdapter()),
                             now=NOW)
    assert (result.action, result.exit_code) == ("post", 0), result
    return path, fm_of(path)["delivery"]


def _lint_lines(path):
    return [line for line in lint.lint_file(path) if not lint.is_warning(line)]


# ---------------------------------------------------------------- 1: thth run は採取を飛ばす

def test_1_はてなの口座のrunは採取を呼ばず失敗の文も出さない(plain, fake_http, monkeypatch):
    """柵: `collect_applies` を外す（常に採取する）と赤（本物の run_collect が知らない媒体で落ちる）。"""
    put_secrets(plain.world)
    monkeypatch.delenv("HEALTHCHECK_URL", raising=False)
    monkeypatch.setattr(cli.selfupdate_mod, "pull_and_reexec", lambda *_a, **_k: None)
    monkeypatch.setattr(cli.core, "throw_once",
                        lambda *_a, **_k: core.ThrowResult(0, "production", "skip", "出すものが無い"))
    called = []
    real = cli.collect_mod.run_collect

    def spy(*a, **k):
        called.append(a)
        return real(*a, **k)

    monkeypatch.setattr(cli.collect_mod, "run_collect", spy)
    rc, out, err = call(["run", ACCOUNT])
    assert called == []
    assert "採取" not in out + err and "hatenablog' を知りません" not in out + err
    rows = runs.read_runs(plain.state)
    assert not any(r.get("action") == "collect" for r in rows)


def test_1_採取を走らせる口座の判定はmediaがhatenablogのときだけ外す():
    assert cli.collect_applies({"media": "hatenablog", "principal": "hatena-main"}) is False
    for media in ("threads", "bluesky", "mastodon", "x", "instagram"):
        assert cli.collect_applies({"media": media}) is True
    # SNS の口座に principal の欄があっても採取は止めない（主体を通すのは hatenablog だけ）
    assert cli.collect_applies({"media": "threads", "principal": "hatena-main"}) is True


# ---------------------------------------------------------------- 2: lint は状態を言う

@pytest.mark.parametrize("state, needle", [
    ("conflict", "はてなの側で手直しされたので止めています（conflict）。解く道は 4.0 S5"),
    ("unknown", "送った結果が分かりません（unknown）"),
])
def test_2_updateの配信が人待ちならlintは状態の符丁と文(plain, state, needle):
    """柵: lint の `delivery_unknown` への丸めを戻すと赤。"""
    _path, delivery = _publish(plain)
    deliveries.advance(ACCOUNT, delivery, state=state, at=jst.iso(NOW), row={"op": "update", "reason": "test"})
    path = plain.queue("u.md", op="update", delivery=delivery)
    lines = _lint_lines(path)
    assert lines == [f"delivery_state_{state}: {needle}" if state == "conflict"
                     else hatena_entry.message(f"delivery_state_{state}")]
    assert needle in lines[0] and "台帳にありません" not in lines[0]
    assert lint.reason_code(lint.lint_file(path)) == f"delivery_state_{state}"


def test_2_削除した配信をもう一度削除する原稿はdeleted(plain):
    _path, delivery = _publish(plain)
    deliveries.advance(ACCOUNT, delivery, state="deleted", at=jst.iso(NOW), row={"op": "delete"})
    path = plain.queue("d.md", op="delete", delivery=delivery)
    assert _lint_lines(path) == ["delivery_state_deleted: もう削除した配信です（deleted）"]
    # 承認の 1 段目（material の側）も同じ符丁と文で断る（delivery_unknown に言い換えない）
    rc, out, err = call(["approve", path])
    assert rc != 0 and "delivery_state_deleted: もう削除した配信です" in out + err
    assert "台帳にありません" not in out + err


def test_2_台帳に無い配信は今までどおりdelivery_unknown(plain):
    path = plain.queue("u.md", op="update", delivery="dl_000000000000")
    assert [line.split(":", 1)[0] for line in _lint_lines(path)] == ["delivery_unknown"]


def test_2_状態の符丁は符丁の表と文と理由の表にそろっている():
    """柵: 状態を 1 つ足して表に足し忘れると赤。"""
    for state in deliveries.STATES:
        code = "delivery_state_" + state
        assert code in hatena_entry.LINT_CODES
        assert hatena_entry.message(code) != f"{code}: {code}"
        assert code in lint.REASONS
        assert code.isascii() and code.islower()


# ---------------------------------------------------------------- 5: 削除の断りの言い出し

def test_5_はてなの記事の削除が安全装置に当たれば削除しません(plain, monkeypatch):
    """柵: `hatena_publish` の kind を retract に戻すと赤。"""
    _path, delivery = _publish(plain)
    path = plain.queue("d.md", op="delete", delivery=delivery)
    approve(path)

    def refuse(*_a, **_k):
        raise guard.GuardRefused("account_stopped", reason="burst")

    monkeypatch.setattr(guard, "check", refuse)
    logs = []
    result = core.throw_once(ACCOUNT, production_flag=True, adapter_factory=factory_for(FakeAdapter()),
                             now=NOW, log=logs.append)
    assert result.error == "account_stopped"
    text = "\n".join(str(x) for x in logs)
    assert "削除しません（安全装置）" in text and "取り下げ" not in text


@pytest.mark.parametrize("kind, head, can", [
    ("entry_create", "公開しません（安全装置）", "次に出せる"),
    ("entry_update", "更新しません（安全装置）", "次に出せる"),
    ("entry_delete", "削除しません（安全装置）", "次に消せる"),
    ("retract", "取り下げません（安全装置）", "次に消せる"),
    ("publish", "送信しません（安全装置）", "次に出せる"),
])
def test_5_安全装置の言い出しは操作ごと(kind, head, can):
    refused = guard.GuardRefused("daily_limit", next_at="2026-09-10T07:00:00+09:00")
    msg = core.guard_refusal_message(ACCOUNT, refused, kind=kind)
    assert msg.startswith(head) and can in msg


# ---------------------------------------------------------------- 6: doctor の観測

def test_6_はてなの口座のdoctorは観測を記録する(plain, fake_http, capsys):
    """柵: probe の HTTP の数を `status`（文字列の欄）に戻すと赤（記録できず stderr に出る）。"""
    put_secrets(plain.world)
    fake_http(blog_handler())
    rc, out, err = call(["doctor", ACCOUNT, "--json"])
    captured = capsys.readouterr()
    assert "doctor_observation_unavailable" not in err + captured.err
    (probe,) = json.loads(out)["probes"]
    assert probe["key"] == "atom_entries" and probe["http_status"] == 200
    observed = doctor.read_observation(ACCOUNT)
    assert observed is not None and observed["probes"][0]["key"] == "atom_entries"
    assert observed["probes"][0]["http_status"] == 200 and observed["probes"][0]["ok"] is True
