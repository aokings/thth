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

import pytest

from thth import bundle, forms, threadrun

BIN = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "bin", "thth")


def test_軸が構成と導線に分かれている():
    """**第 1 稿の語彙は軸が混ざっていた**（Codex 指摘）。

    「列挙」「問い→答え」は構成、「誘導」は目的。別々に記録すると
    「問い→答え × 記事へ」と「列挙 × 記事へ」を並べられる。
    """
    assert set(forms.FORMS) == {
        "単発", "概要→実用詳細", "体験→再現方法", "困り事→理由→行動",
        "具体例→解釈→応用", "比較→条件→選択", "列挙"}
    assert set(forms.OUTLETS) == {"記事へ", "別投稿へ", "無し"}
    # **目的の語が構成に混ざっていない。**
    assert "誘導" not in forms.FORMS
    # **各段が何を渡すか**が名前に入っている（文章の形ではなく）。
    assert all("→" in name or name in ("単発", "列挙") for name in forms.FORMS)


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
    # 「比較」で書いたものを分類できる。
    assert forms.label_error("比較→条件→選択", "記事へ") is None


def test_旧語彙は新語彙を名指しで教える():
    """**3 セッションが 3 本とも `問い→答え` を選んだ**（2026-09-11）。

    中身も狙いも違う 3 本が同じ箱に落ちた——**分類が何も分けていなかった。**
    語彙を入れ替えたので、**旧語彙で書いた人に新しい語を名指しで渡す。**
    """
    err = forms.label_error("問い→答え", "記事へ")
    assert "旧語彙です" in err
    assert "困り事→理由→行動" in err
    assert forms.FORM_MIGRATION["手順"] == "概要→実用詳細"
    assert forms.FORM_MIGRATION["比較"] == "比較→条件→選択"


def test_ラベルの検査はprofileを読まない():
    """**再検収 F1**。`label_error()` は公開経路から呼ばれる `bundle.check()` の
    中にいるので、**profile を読まない**（読むと編集方針が公開の可否を決める）。
    `avoid_forms` の照合は `bundle.editorial_notes()` へ移した。
    """
    import inspect
    assert "avoid_forms" not in inspect.signature(forms.label_error).parameters
    assert forms.label_error("体験→再現方法", "記事へ") is None
    assert "知らない語" in forms.label_error("体験型", "記事へ")


def test_連投にする前の5つの問いがある():
    """**「記事 URL があるから連投」にしないための関門**（Codex §8）。"""
    assert len(forms.BEFORE_YOU_SPLIT) == 5
    assert "単発に収まらず" in forms.BEFORE_YOU_SPLIT[1]
    assert "各段で何を一つずつ渡すか" in forms.BEFORE_YOU_SPLIT[2]
    assert "何が不明か" in forms.BEFORE_YOU_SPLIT[3]
    assert "結論と対象読者" in forms.BASE_SHAPE


def test_知らない語は通さない():
    assert forms.label_error("困り事→理由→行動", "記事へ") is None
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
    b = bundle.parse_text(
        make(FM.replace("form: 困り事→理由→行動", "form: 問いと答え")), "b.md")
    errors = bundle.check(b, account_cfg=account_cfg())
    assert any("form: 知らない語" in e for e in errors), errors


# --- 手順書と CLI ------------------------------------------------------------

def test_formsコマンドが動く():
    proc = subprocess.run([sys.executable, BIN, "forms"], capture_output=True,
                           text=True, env=dict(os.environ))
    assert proc.returncode == 0, proc.stderr
    assert "困り事→理由→行動" in proc.stdout
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
    assert forms.VOCABULARY_VERSION == "2026-09-11.4"
    row = forms.label_record(form="比較→条件→選択", outlet="記事へ",
                              numbering="なし", at="2026-09-11T12:00:00+09:00")
    assert row["vocabulary_version"] == "2026-09-11.4"
    assert row["numbering"] == "なし"


def test_番号の有無を記録できる():
    """**記録する場所が無ければ、比べられない**（asmon 関東セッション要望）。

    2 セッションとも独立に「書かない」を選んだが**理由が違った**。
    どちらが効くかは実測が無いので、**欄を作って残す。**
    """
    assert set(forms.NUMBERING) == {"あり", "なし"}
    assert forms.label_error("困り事→理由→行動", "記事へ", "なし") is None
    assert "知らない語" in forms.label_error("困り事→理由→行動", "記事へ", "無し")


def test_名乗ったラベルと本文の食い違いは警告まで():
    """**ラベルは承認の対象ではないので止めない。** ただし黙って測らない。"""
    assert forms.numbering_warning("なし", ["問い。", "答え。"]) is None
    assert forms.numbering_warning("あり", ["1/2 問い。", "2/2 答え。"]) is None

    warn = forms.numbering_warning("あり", ["問い。", "答え。"])
    assert warn.startswith("warning:") and "見つかりません" in warn
    warn = forms.numbering_warning("なし", ["1/2 問い。", "答え。"])
    assert warn.startswith("warning:") and "含まれています" in warn


def test_profileのavoid_formsを検査する(thth_root):
    """**知らない型を宣言させない**（綴り違いで制限が効かなくなる）。"""
    from thth import topic_models as models

    base = {"account": "a", "language": "ja", "primary_goal": "article_visits",
            "editorial_scope": "x", "intended_interests": ["x"],
            "avoid_misrepresentation": ["x"], "status": "provisional",
            "confirmed_by": "t", "basis": []}
    ok = models.build_profile(dict(base, avoid_forms=["体験→再現方法"]))
    assert ok["avoid_forms"] == ["体験→再現方法"]

    with pytest.raises(models.SchemaError) as e:
        models.build_profile(dict(base, avoid_forms=["体験型"]))
    assert "知らない型" in str(e.value)

    with pytest.raises(models.SchemaError):
        models.build_profile(dict(base, avoid_forms="体験→再現方法"))

    # **書かなくてよい**（既存の profile を壊さない）。
    assert "avoid_forms" not in models.build_profile(dict(base))


def test_宣言した型は警告するが公開は止めない(tmp_path, isolated_account, thth_root):
    """**再検収 F1**（2026-09-11 Codex）。以前はここで**公開が止まっていた。**

    `bundle.check()` は**公開経路（`threadthrow`）からも呼ばれる。**
    そこで `avoid_forms` を実エラーにしていたので、**編集方針が公開の可否を
    決めていた**（構想書 §11 が別レビューを求めている変更）。照合は
    `editorial_notes()`（lint からだけ呼ぶ）へ移し、**警告までにした。**
    """
    from tests.test_bundle import FM, BODY, account_cfg
    from thth import bundle as bundle_mod
    from thth import lint as lint_mod
    from thth import topic_models as models
    from thth import topic_store

    account = isolated_account["name"]
    profile = models.build_profile({
        "account": account, "language": "ja", "primary_goal": "article_visits",
        "editorial_scope": "x", "intended_interests": ["x"],
        "avoid_misrepresentation": ["体験を創作しない"], "status": "confirmed",
        "confirmed_by": "t", "basis": ["docs/方針.md"],
        "avoid_forms": ["体験→再現方法"]})
    topic_store.set_profile(profile)

    text = (FM.replace("nigamilab-threads", account)
            .replace("form: 困り事→理由→行動", "form: 体験→再現方法"))
    b = bundle_mod.parse_text(f"---\n{text}---\n{BODY}", "b.md")

    # **公開経路の検査は profile を読まない。**
    assert bundle_mod.check(b, account_cfg=account_cfg()) == []

    notes = bundle_mod.editorial_notes(b)
    assert any("使わないと profile に宣言" in n for n in notes), notes
    assert all(lint_mod.is_warning(n) for n in notes), "承認を止める側に入っている"

    # 宣言していない型には何も言わない。
    ok = bundle_mod.parse_text(
        f"---\n{FM.replace('nigamilab-threads', account)}---\n{BODY}", "b.md")
    assert bundle_mod.editorial_notes(ok) == []


def test_profileが読めないことを制限なしにしない(tmp_path, isolated_account, thth_root):
    """**再検収 F1 の核心。** 以前は例外を握って空配列（＝制限なし）にしていた。

    そのため **profile を壊れた JSON に置き換えるだけで、原稿も承認も変えずに
    3 段すべてが公開できた。** いまは公開の可否が profile を見ていないうえに、
    診断の側も「読めなかった」と言う（**黙って制限なしにしない**）。
    """
    from tests.test_bundle import FM, BODY, account_cfg
    from thth import bundle as bundle_mod
    from thth import lint as lint_mod
    from thth import topic_models as models
    from thth import topic_store

    account = isolated_account["name"]
    topic_store.set_profile(models.build_profile({
        "account": account, "language": "ja", "primary_goal": "article_visits",
        "editorial_scope": "x", "intended_interests": ["x"],
        "avoid_misrepresentation": [], "status": "confirmed",
        "confirmed_by": "t", "basis": ["docs/方針.md"],
        "avoid_forms": ["体験→再現方法"]}))
    with open(topic_store.profile_path(account), "w", encoding="utf-8") as f:
        f.write("{壊れた")

    text = (FM.replace("nigamilab-threads", account)
            .replace("form: 困り事→理由→行動", "form: 体験→再現方法"))
    b = bundle_mod.parse_text(f"---\n{text}---\n{BODY}", "b.md")

    assert bundle_mod.check(b, account_cfg=account_cfg()) == []
    notes = bundle_mod.editorial_notes(b)
    assert any("読めません" in n for n in notes), notes
    assert all(lint_mod.is_warning(n) for n in notes)


def test_profileが無ければ何も言わない(tmp_path, isolated_account, thth_root):
    from tests.test_bundle import FM, BODY, account_cfg
    from thth import bundle as bundle_mod

    text = (FM.replace("nigamilab-threads", isolated_account["name"])
            .replace("form: 困り事→理由→行動", "form: 体験→再現方法"))
    b = bundle_mod.parse_text(f"---\n{text}---\n{BODY}", "b.md")
    assert bundle_mod.check(b, account_cfg=account_cfg()) == []
    assert bundle_mod.editorial_notes(b) == []
