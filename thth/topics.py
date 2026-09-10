"""トピックの下調べを記録して使い回す（masaru 指摘 2026-09-10）。

**なぜ要るか。** トピックは効き目が桁で違う変数（実測で約 400 倍）なのに、
「そのトピックに誰がいるか」は THTH からは分からない——検索の権限（上級アクセス）
が要る。いまはブラウザで人が見るしかない。

**だが、いちばん惜しいのは見に行く手間ではない。**2026-09-10 に統括がブラウザで
`精製` を見て「レアアース・重加工の場だ」と知ったが、**その知識は会話の中にしか
残らなかった**。明日ほかのセッションが同じ場所でつまずく。

そこでこの表を置く。**見に行くのは人（またはブラウザを持つ AI）、覚えておくのは
THTH。** 記録があれば、承認の一段目が「このトピックは不一致だと分かっています」と
言える。

**判定の値**（`verdict`）:
  - `alive`    … 人がいて、内容も合っている
  - `mismatch` … 人はいるが**別の業界・別の言語**（`精製` ＝鉱物精製、など）
  - `dead`     … 人がいない
  - `unknown`  … 未確認

**置き場は `$THTH_ROOT/state/topics.json`**（VM の状態。利用者 repo には置かない）。
全プロジェクトで共有する——`精製` が鉱物の場であることは、どのプロジェクトにとっても
同じ事実だから。**追記のみ**（後の確認が前の確認を上書きせず、履歴として残る）。
"""
from __future__ import annotations

import json
import os

from . import accounts as accounts_mod
from . import jst

VERDICTS = ("alive", "mismatch", "dead", "unknown")


def path() -> str:
    return os.path.join(accounts_mod.thth_root(), "state", "topics.json")


def load() -> dict:
    p = path()
    if not os.path.exists(p):
        return {"checks": []}
    try:
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {"checks": []}
    if not isinstance(data.get("checks"), list):
        return {"checks": []}
    return data


def record(topic: str, *, verdict: str, audience: str = "", by: str,
            note: str = "", now=None) -> dict:
    """1 回の下調べを追記する。**前の記録は消さない。**"""
    if verdict not in VERDICTS:
        raise ValueError(f"verdict は {VERDICTS} のどれか: {verdict}")
    now = now if now is not None else jst.now_jst()
    data = load()
    row = {"topic": topic, "verdict": verdict, "audience": audience,
           "note": note, "by": by, "checked_at": jst.iso(now)}
    data["checks"].append(row)
    p = path()
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, p)
    return row


def latest(topic: str | None = None) -> dict:
    """トピックごとの**最後の確認**（`topic` を渡せばその 1 件・無ければ空の dict）。"""
    out: dict = {}
    for row in load()["checks"]:
        out[row["topic"]] = row          # 後の行が勝つ（追記順＝時系列）
    if topic is None:
        return out
    return out.get(topic, {})


def verdict_line(topic: str | None) -> str | None:
    """承認の一段目に添える 1 行（確認が無ければ「未確認」と言う）。"""
    if not topic:
        return None
    row = latest(topic)
    if not row:
        return (f"トピック `{topic}` は**未確認**です。"
                "誰がいる場所か確かめてから出すことを勧めます。")
    mark = {"alive": "確認済み・合っています", "mismatch": "**不一致**",
            "dead": "**人がいません**", "unknown": "未確認"}[row["verdict"]]
    detail = f"（{row['audience']}）" if row.get("audience") else ""
    return (f"トピック `{topic}`: {mark}{detail}"
            f"／{row['checked_at'][:10]} {row['by']} が確認")
