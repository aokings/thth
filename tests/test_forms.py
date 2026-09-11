"""形の語彙と計測の紐付け（設計 §8・§9・工程 7〜8）。

**「使いこなせる」は実測待ちにすると満たせない**（Codex 最終条件 6）。
実測がゼロでも、判断材料・語彙・検査・計測の口は初版から揃っている。
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys

from thth import bundle, forms, threadrun

BIN = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "bin", "thth")


def test_軸が構成と導線に分かれている():
    """**第 1 稿の語彙は軸が混ざっていた**（Codex 指摘）。

    「列挙」「問い→答え」は構成、「誘導」は目的。別々に記録すると
    「問い→答え × 記事へ」と「列挙 × 記事へ」を並べられる。
    """
    assert set(forms.FORMS) == {"単発", "比較", "列挙", "問い→答え", "手順"}
    assert set(forms.OUTLETS) == {"記事へ", "別投稿へ", "無し"}
    # **目的の語が構成に混ざっていない。**
    assert "誘導" not in forms.FORMS


def test_分ける理由として挙げた型は分類できる():
    """**§0 の「分ける理由」と `form` の語彙が食い違わない**（関東セッション指摘）。

    > 分ける理由として挙げている型が、書いたあとに分類できないのは少し座りが悪い
    """
    import os
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    doc = open(os.path.join(root, "docs", "手順_LLM_スレッド連投.md"),
                encoding="utf-8").read()
    reasons = doc.split("分ける理由になるもの:")[1].split("分ける理由にならない")[0]
    for word in ("比較", "段階的"):
        assert word in reasons, reasons
    # 「比較」で書いたものを「比較」と分類できる。
    assert forms.label_error("比較", "記事へ") is None


def test_知らない語は通さない():
    assert forms.label_error("問い→答え", "記事へ") is None
    assert "知らない語" in forms.label_error("問いと答え", "記事へ")
    assert "知らない語" in forms.label_error("列挙", "きじへ")
    # 書かなくてよい（承認対象ではない）。
    assert forms.label_error(None, None) is None


def test_ラベルの履歴は当時の値と分類の版を持つ():
    """**時刻だけでなく**（Codex 最終条件 4）。"""
    row = forms.label_record(form="列挙", outlet="記事へ",
                              at="2026-09-15T18:00:00+09:00")
    assert row["form"] == "列挙" and row["outlet"] == "記事へ"
    assert row["vocabulary_version"] == forms.VOCABULARY_VERSION


def test_実測がまだ無いことを隠さない():
    """**出さないと、根拠のない型が権威を持つ。**"""
    data = forms.advise()
    assert data["measured"] == {}
    assert "未検証" in data["notice"]
    assert any("未検証" in line for line in data["guidance"])


def test_判断の基準が先頭にある():
    """**分けるかどうかを決める問いは 1 つ**（masaru 2026-09-11）。

    以前は「無理に分割しない」を先頭に置いていたが、**それは編集の話だけ**
    だった。動機は信号・演出・編集の 3 つあり、**どれで分けても同じ問いに
    答える必要がある。**
    """
    assert "1 段目で止まった人にとっても有益か" in forms.GUIDANCE[0]
    assert "演じているだけ" in forms.GUIDANCE[0]


def test_引っぱってよいものと駄目なものを分ける():
    """**続きが気になるのは本物の効果**（masaru 2026-09-11）。

    禁じるのは引っぱること自体ではなく、**留保を引っぱること**。
    """
    rule = next(g for g in forms.GUIDANCE if "引っぱって" in g)
    assert "引っぱってよいのは答え" in rule
    assert "本物の効果" in rule


def test_信号は動機だが未検証と書く():
    rule = next(g for g in forms.GUIDANCE if "信号" in g)
    assert "未検証" in rule
    assert "伸びるらしい" in rule


def test_計測は足さない割らない(thth_root):
    """**「到達人数」や「読了率」という名前を付けない**（設計 §9）。"""
    run = threadrun.start(account="a", rel_path="q/x.md", bundle_sha="s",
                           segment_count=2, segment_shas=["h1", "h2"],
                           continue_until="2026-09-15T20:00:00+09:00", by="t")
    threadrun.mark(run, 1, threadrun.PUBLISHED, post_id="P1")
    threadrun.mark(run, 2, threadrun.PUBLISHED, post_id="P2")

    out = forms.bundle_outcome(run, {"P1": {"views": 100}, "P2": {"views": 40}})
    assert [r["measured"]["views"] for r in out["posts"]] == [100, 40]
    blob = json.dumps(out, ensure_ascii=False)
    assert "到達人数" not in blob.replace("到達人数」と呼ばない", "")
    assert "読了率" not in blob.replace("読了率」と呼ばない", "")
    # **合計も割り算も返していない。**
    assert "total" not in out and "rate" not in out
    assert "足して" in out["notice"]


def test_未公開の段は測りようがないと分かる(thth_root):
    run = threadrun.start(account="a", rel_path="q/y.md", bundle_sha="s",
                           segment_count=2, segment_shas=["h1", "h2"],
                           continue_until="2026-09-15T20:00:00+09:00", by="t")
    threadrun.mark(run, 1, threadrun.PUBLISHED, post_id="P1")
    out = forms.bundle_outcome(run, {"P1": {"views": 5}})
    assert out["posts"][1]["measured"] is None
    assert out["posts"][1]["state"] == threadrun.PENDING


def test_ラベルの綴り違いをlintが断る():
    from tests.test_bundle import FM, BODY, make, account_cfg
    b = bundle.parse_text(make(FM.replace("form: 問い→答え", "form: 問いと答え")),
                           "b.md")
    errors = bundle.check(b, account_cfg=account_cfg())
    assert any("form: 知らない語" in e for e in errors), errors


# --- 手順書と CLI ------------------------------------------------------------

def test_formsコマンドが動く():
    proc = subprocess.run([sys.executable, BIN, "forms"], capture_output=True,
                           text=True, env=dict(os.environ))
    assert proc.returncode == 0, proc.stderr
    assert "問い→答え" in proc.stdout
    assert "未検証" in proc.stdout
    assert "到達人数" in proc.stdout        # やってはいけないことも出る


def test_手順書のコマンドが実際に存在する():
    """**動かないコマンドを配らない**（2026-09-11 に 3 回やった）。"""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    doc = open(os.path.join(root, "docs", "手順_LLM_スレッド連投.md"),
                encoding="utf-8").read()
    used = set(re.findall(r"^thth ([a-z][a-z-]*)", doc, re.M))
    assert used, "手順書にコマンドが出てこない"
    proc = subprocess.run([sys.executable, BIN, "--help"], capture_output=True,
                           text=True)
    for name in used:
        assert name in proc.stdout, f"{name} が thth --help に無い"


def test_手順書の原稿の例が実際に通る():
    """**例が lint を通ることを機械で見張る。**"""
    from tests.test_bundle import account_cfg
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    doc = open(os.path.join(root, "docs", "手順_LLM_スレッド連投.md"),
                encoding="utf-8").read()
    block = doc.split("```markdown\n")[1].split("```")[0]
    b = bundle.parse_text(block, "例.md")
    assert b.malformed is False, "手順書の例が読めない"
    errors = bundle.check(b, account_cfg=account_cfg())
    assert errors == [], errors


def test_語彙が増えたら版が上がる():
    """**どの版の分類で測ったか**が要る（Codex 最終条件 4）。

    語彙を足したのに版が据え置きだと、**前後の実測が同じ分類で測られたように
    見える。** `比較` を足して .2、`numbering` を足して .3。
    """
    assert forms.VOCABULARY_VERSION == "2026-09-11.3"
    row = forms.label_record(form="比較", outlet="記事へ", numbering="なし",
                              at="2026-09-11T12:00:00+09:00")
    assert row["vocabulary_version"] == "2026-09-11.3"
    assert row["numbering"] == "なし"


def test_番号の有無を記録できる():
    """**記録する場所が無ければ、比べられない**（asmon 関東セッション要望）。

    2 セッションとも独立に「書かない」を選んだが**理由が違った**。
    どちらが効くかは実測が無いので、**欄を作って残す。**
    """
    assert set(forms.NUMBERING) == {"あり", "なし"}
    assert forms.label_error("問い→答え", "記事へ", "なし") is None
    assert "知らない語" in forms.label_error("問い→答え", "記事へ", "無し")


def test_名乗ったラベルと本文の食い違いは警告まで():
    """**ラベルは承認の対象ではないので止めない。** ただし黙って測らない。"""
    assert forms.numbering_warning("なし", ["問い。", "答え。"]) is None
    assert forms.numbering_warning("あり", ["1/2 問い。", "2/2 答え。"]) is None

    warn = forms.numbering_warning("あり", ["問い。", "答え。"])
    assert warn.startswith("warning:") and "見つかりません" in warn
    warn = forms.numbering_warning("なし", ["1/2 問い。", "答え。"])
    assert warn.startswith("warning:") and "含まれています" in warn
