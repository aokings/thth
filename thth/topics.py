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

# **トピックの型**（masaru 提案 2026-09-10「その辺をツールの語彙として持つ」）。
#
# 1 つ 1 つのトピックの当たり外れは、次に別の語を選ぶときには直接使えない。
# だが**型ごとの当たり外れ**なら使い回せる——「専門語は外れやすい」が自分の
# 実測で裏付けば、まだ試していない専門語も避けられる。**回すほど溜まるのは
# 個々の語ではなく型のほう。**
#
# 型は「見れば分かること」だけにする（良し悪しの判断を型に混ぜない）。
KINDS = {
    "行動": "人がいまやっている行動・場面の名前（中学受験・学校説明会）",
    "一般名詞": "日常の物やカテゴリ（チョコレート・コーヒー）",
    "抽象": "感覚や性質（苦味）。意味が拡散しやすい",
    "専門語": "業界の語（精製・六大茶類・アナエロビック）。別業界・別言語に取られがち",
    "固有名": "ブランド・製品・店の名前",
    "つながり型": "「〜と繋がりたい」などの交流タグ。X・Instagram の作法",
    "自作": "自分で作った語（コピチャ観測所）。誰も見ていない",
}


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
            note: str = "", kind: str | None = None, now=None) -> dict:
    """1 回の下調べを追記する。**前の記録は消さない。**"""
    if verdict not in VERDICTS:
        raise ValueError(f"verdict は {VERDICTS} のどれか: {verdict}")
    if kind is not None and kind not in KINDS:
        raise ValueError(f"kind は {tuple(KINDS)} のどれか: {kind}")
    now = now if now is not None else jst.now_jst()
    data = load()
    row = {"topic": topic, "verdict": verdict, "audience": audience, "kind": kind,
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
    kind = f"［{row['kind']}］" if row.get("kind") else ""
    return (f"トピック `{topic}`{kind}: {mark}{detail}"
            f"／{row['checked_at'][:10]} {row['by']} が確認")


def learned(measured_by_topic: dict) -> list:
    """**型ごとに何が起きたか**を集める（masaru 提案 2026-09-10）。

    `measured_by_topic` は `{トピック: [24 時間時点の views, ...]}`。下調べの記録
    （型と判定）と実測を突き合わせ、**型ごとに**「何語を試したか・何本出したか・
    実測の中央値・判定の内訳」を返す。

    **これが「回すほど溜まる」ものの正体。** 個々の語の当たり外れは次の語選びに
    そのままは使えないが、型ごとの傾向なら使い回せる。**6 件の下調べは推測でしか
    ないが、84 本の実測が付けば根拠になる。**
    """
    rows = latest()
    out: dict = {}
    for topic, row in rows.items():
        kind = row.get("kind") or "（型なし）"
        bucket = out.setdefault(kind, {"kind": kind, "topics": [], "views": [],
                                        "alive": 0, "mismatch": 0, "dead": 0, "unknown": 0})
        bucket["topics"].append(topic)
        bucket[row["verdict"]] += 1
        bucket["views"].extend(measured_by_topic.get(topic, []))

    # 実測がまだ無いトピックも型に数える（「試したが数はこれから」が分かる）
    result = []
    for kind, bucket in out.items():
        seen = sorted(bucket["views"])
        result.append({
            "kind": kind,
            "description": KINDS.get(kind, ""),
            "topics": len(bucket["topics"]),
            "posts_measured": len(seen),
            "views_median": seen[len(seen) // 2] if seen else None,
            "views_min": seen[0] if seen else None,
            "views_max": seen[-1] if seen else None,
            "alive": bucket["alive"], "mismatch": bucket["mismatch"],
            "dead": bucket["dead"], "unknown": bucket["unknown"],
            "examples": sorted(bucket["topics"])[:6],
        })
    result.sort(key=lambda r: (r["views_median"] is None, -(r["views_median"] or 0)))
    return result
