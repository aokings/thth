"""投稿の形の語彙と、形ごとの実測（設計 §8・§9・工程 7〜8）。

**軸を 2 つに分ける**（Codex 指摘）。第 1 稿の語彙は「列挙」「問い→答え」が
**構成**、「誘導」が**目的**で混ざっていた。別々に記録すると比較できる
（「問い→答え × 記事へ」と「列挙 × 記事へ」を並べられる）。

**`form` / `outlet` は承認対象ではない**（設計 §8.3・Codex 最終条件 4）。
既にある本文を**分類するだけ**のラベルなので、変えても公開される内容は
変わらない。**`--advise` への影響だけでは承認対象にしない。**
ただし**実測に使った版を記録する**——ラベルの履歴は、時刻だけでなく
**当時の値と分類の版**を保存する。

**計測でやってはいけないこと**（設計 §9）は `OUTCOME_RULES` に書いてある。
コードのコメントではなく**出力に出す**——読む人が毎回見る場所に置く。
"""
from __future__ import annotations

VOCABULARY_VERSION = "2026-09-11.3"

# 構成（何段に分けて、どう並べるか）
FORMS = {
    "単発": "1 投稿で完結する。**分けなくて済むならこれ。**",
    "比較": "**A と B を軸で突き合わせる。** 1 段に詰めると両方ぼやける形"
             "（「2 校を並べる」「実倍率と合格者平均を並べる」）",
    "列挙": "数えられるものを並べる（「10 選」など）。先頭で何を並べるかを言う",
    "問い→答え": "先頭で問いを立て、後の段で答える。**答えを最後まで隠さない**",
    "手順": "順番に意味がある説明。段の番号が内容の番号と一致する",
}
# **`比較` は asmon 関東セッションの指摘で足した**（2026-09-11）。
# §0 の「分ける理由」の筆頭が比較なのに、**書いたあとに分類できる語が無かった。**
# > 分ける理由として挙げている型が、書いたあとに分類できないのは少し座りが悪い
# 同じセッションがトピックの型でも `カテゴリ` の欠落を見つけている——
# **分類の語彙は、使う人が書こうとして初めて穴が見える。**

# **まだ足していない軸**（nigamilab セッション 2026-09-11）:
#
# > 同じ「問い→答え」でも、**期待を上げる型と下げる型は別に数えたほうが、
# > 後で比較になる**と思いました。（「言われていることを、測られた範囲まで
# > 戻す」形は、読者の持っている前提を**下げる**方向に働く）
#
# **鋭い区別だが、いま足さない。** 実測がゼロなので、**軸を増やしても
# 比べる材料が無い。** 同じセッションもそう書いている——1 本目が出てから。
# **語彙は測れるようになってから増やす**（トピックの型で踏んだ順序の逆を
# やらない）。

# 段の番号を本文に書いたか（asmon 関東セッション要望 2026-09-11）。
#
# > 番号の有無を `form` と一緒に記録するなら、**原稿の front-matter に欄が
# > ほしい**です。…経緯のメモに書いても THTH は読みません。
#
# **記録する場所が無ければ、比べられない。** 2 セッションとも独立に
# 「書かない」を選んだが、**理由が違った**——
#   nigamilab: 1 段目で止まる人に「これは途中です」と思わせるのは損
#   asmon 関東: 番号が付くとその段が単独で読めなくなる
# **どちらが効くかは実測が無い。** だから欄を作って残す。
NUMBERING = {
    "あり": "本文に `1/3` のような番号を書いた",
    "なし": "書かなかった。**各段が単独で読める形**にしたとき",
}

# 最後の導線（読んだ人をどこへ渡すか）
OUTLETS = {
    "記事へ": "最終段で自分の記事に渡す",
    "別投稿へ": "最終段で自分の別の投稿を引用する",
    "無し": "その投稿の中で完結する。**渡さないのも選択。**",
}

# **実測ゼロでも渡せる判断材料**（設計 §8.2・Codex 指摘）。
# 「使いこなせる」を実測待ちにすると満たせない。
GUIDANCE = [
    "**単発で十分な内容を、無理に分割しない。**",
    "比較や段階的な説明には、連投を候補にする。",
    "**先頭だけでも、何の話で何が得られるか分かるようにする。**",
    "**続きを読ませるために、根拠や重要な留保を最後まで隠さない。**",
    "各段の役割と、最後の導線を説明する。",
    "**この形式で成果が出るかは未検証**——うちはまだ 1 本も出していない。",
]

# **計測でやってはいけないこと**（設計 §9・Codex 最終条件）。
OUTCOME_RULES = [
    "**全段の views を足して「到達人数」と呼ばない**（同じ人が複数段を見る）。",
    "**最終段 ÷ 先頭段を「読了率」と呼ばない**（views の定義がそれを支えない）。",
    "**取得できない記事クリックを views で代用しない**"
    "（`clicks` は Threads API から返ってこない）。",
    "投稿単位のまま保持し、束へ紐付ける。**束として言えるのは各段の数まで。**",
]


def label_error(form, outlet, numbering=None) -> str | None:
    """ラベルの値を検査する。**知らない語は推測で通さない。**"""
    if form is not None and form not in FORMS:
        return (f"form: 知らない語です（{form}）。"
                f"使えるのは: {'・'.join(FORMS)}")
    if outlet is not None and outlet not in OUTLETS:
        return (f"outlet: 知らない語です（{outlet}）。"
                f"使えるのは: {'・'.join(OUTLETS)}")
    if numbering is not None and numbering not in NUMBERING:
        return (f"numbering: 知らない語です（{numbering}）。"
                f"使えるのは: {'・'.join(NUMBERING)}")
    return None


_NUMBER_RE = __import__("re").compile(r"\d+\s*/\s*\d+")


def numbering_warning(numbering, segments: list) -> str | None:
    """名乗ったラベルと本文が食い違っていないか。**警告まで。**

    ラベルは承認の対象ではないので**止めない。** ただし食い違ったまま測ると、
    **番号ありと番号なしを取り違えて比べる**ことになる。
    """
    if numbering is None or not segments:
        return None
    found = any(_NUMBER_RE.search(seg or "") for seg in segments)
    if numbering == "あり" and not found:
        return ("warning: numbering: 「あり」と書いてありますが、本文に "
                "`1/3` のような番号が見つかりません")
    if numbering == "なし" and found:
        return ("warning: numbering: 「なし」と書いてありますが、本文に "
                "`N/M` の形が含まれています（本文の一部なら無視してください）")
    return None


def label_record(*, form, outlet, at: str, numbering=None) -> dict:
    """ラベルの履歴 1 件（Codex 最終条件 4）。

    **時刻だけでなく、当時の値と分類の版を保存する。**
    語彙が増えたあとに読み返しても「どの版の分類で測ったか」が分かる。
    """
    return {"at": at, "form": form, "outlet": outlet, "numbering": numbering,
            "vocabulary_version": VOCABULARY_VERSION}


def bundle_outcome(run: dict, measured_by_post: dict) -> dict:
    """束の実測（設計 §9）。**投稿単位のまま保持して、束へ紐付ける。**

    `measured_by_post` は `{post_id: {"views": n, ...}}`。

    **足さない。割らない。** 返すのは段ごとの数と、その並びだけ。
    どう読むかは人が決める——道具が「到達人数」や「読了率」という**名前を
    付けた時点で、その名前が事実として一人歩きする。**
    """
    rows = []
    for post in run.get("posts", []):
        post_id = post.get("post_id")
        rows.append({
            "index": post["index"],
            "post_id": post_id,
            "state": post["state"],
            "measured": measured_by_post.get(post_id) if post_id else None,
        })
    return {
        "run_id": run.get("run_id"),
        "account": run.get("account"),
        "rel_path": run.get("rel_path"),
        "root_post_id": run.get("root_post_id"),
        "posts": rows,
        "notice": "／".join(OUTCOME_RULES),
    }


def advise() -> dict:
    """形を選ぶ前に読むもの（設計 §8.2）。

    **実測がまだ無いことを隠さない。** 「この形式で成果が出るかは未検証」を
    毎回出す——出さないと、**根拠のない型が権威を持つ**（トピックで
    「一般名詞なら安全」と思い込んで 0 件を踏んだのと同じ罠）。
    """
    return {
        "vocabulary_version": VOCABULARY_VERSION,
        "forms": FORMS,
        "outlets": OUTLETS,
        "numbering": NUMBERING,
        "guidance": GUIDANCE,
        "measured": {},          # **空。まだ 1 本も出していない。**
        "notice": "形ごとの実測はまだありません。"
                   "**この形式で成果が出るかは未検証です。**"
                   "テンプレートは書くための補助であって、成果の保証ではありません。",
        "outcome_rules": OUTCOME_RULES,
    }
