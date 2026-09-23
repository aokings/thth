"""3.7.0 §C2 where が 0 件のときの言い方（`zero_or_filtered`）。

0 件を「無い」と言わない——本当に無いか、媒体が語を受け付けなかったかは区別できない。
Threads の文書にはセンシティブと判断した語の検索に空の配列を返すとある。短い語・形容詞の
扱いは一次資料に記載が無い（未確認）。
"""
from __future__ import annotations

import json

from tests.test_v370_where_also import _setup
from thth import cli


def test_0件ならzero_or_filteredと区別できないと言う(isolated_account_factory, monkeypatch, capsys):
    _setup(isolated_account_factory, monkeypatch, [])
    assert cli.main(["where", "one", "語", "--json"]) == 0
    entry = json.loads(capsys.readouterr().out)["by_account"]["one"]["by_word"]["語"]
    zero = entry["zero_or_filtered"]
    assert zero["code"] == "zero_or_filtered"
    assert zero["message"] == ("0 件でした。本当に無いか、媒体が語を受け付けなかったかは区別できません"
                               "（Threads は、センシティブと判断した語の検索に空の配列を返すと文書にあります）")
    assert zero["unverified"] == "短い語・形容詞の扱いは一次資料に記載がありません（未確認）"
    assert cli.main(["where", "one", "語"]) == 0
    assert "0 件でした。本当に無いか" in capsys.readouterr().out


def test_threads以外は媒体の文書の一文を足さない(isolated_account_factory, monkeypatch, capsys):
    _setup(isolated_account_factory, monkeypatch, [], media="mastodon")
    assert cli.main(["where", "one", "語", "--json"]) == 0
    entry = json.loads(capsys.readouterr().out)["by_account"]["one"]["by_word"]["語"]
    assert entry["zero_or_filtered"]["message"].endswith("区別できません")


def test_件数があればzero_or_filteredを付けない_手元の絞りで0件になっても付けない(
        isolated_account_factory, monkeypatch, capsys):
    _setup(isolated_account_factory, monkeypatch, ["珈琲"])
    assert cli.main(["where", "one", "語", "--also", "紅茶", "--json"]) == 0
    entry = json.loads(capsys.readouterr().out)["by_account"]["one"]["by_word"]["語"]
    assert "zero_or_filtered" not in entry and entry["also"]["n_matched"] == 0
