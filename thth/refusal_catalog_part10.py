"""断りの目録の項目（第 10 部: 台帳の機械・`thth/register/`・`thth register …`）。thth/refusal_catalog.py が読む。

符丁は `thth/register/errors.py` の `REASONS`。文は店（実店舗）にも自前のメディアにも読めるように書く。
原則 `docs/原則_前向きな断り.md`: なぜ・どうすれば通るか・どうすればもっと良くなるか。
since は版上げのときに直す（今の版を越えない柵があるので、今は今の版を置く）。
"""
from __future__ import annotations

_C = ("cli",)
_SINCE = "5.18.1"
_ADD = "thth register add <台帳> --path <フォルダ> --project <project> --kind media --by <名前>"
_CHECK = "thth register check <台帳>"
_SHOW = "thth register show <台帳>"
_CONFIRM = "thth register confirm <台帳> <行> --by <名前>"
_TRIGGERS = "thth register triggers <台帳>"
_DRAFT = "thth register draft <台帳> --to <口座> --row <行>"


def _entry(why, fix, command, better, kind="refusal", by_detail=None):
    entry = {"kind": kind, "why": why, "fix": fix, "command": command, "better": better, "doors": _C,
             "since": _SINCE}
    if by_detail:
        entry["by_detail"] = by_detail
    return entry


ENTRIES = {
    # ---------------------------------------------------------------- 段 1: 土台
    "register_args_invalid": _entry(
        "thth register に渡した値が決まりに合っていません（台帳の名前と project は英数字と _・.・-、行の番号は英小文字・数字・_・.・-、"
        "--by は誰が頼んだか）。",
        "1 行目の（）が指す引数を直して打ち直してください。",
        _ADD,
        "台帳の名前は <project>-media・<project>-store のように project と使い道をつなげると、名前だけでどの台帳か分かります。",
        by_detail={
            "register_name": {
                "fix": "台帳の名前を英数字と _・.・- だけで付け直して打ち直してください（空白・斜線・日本語は使えません）。",
                "command": _ADD,
                "better": "台帳の名前は <project>-media・<project>-store のように project と使い道をつなげると、名前だけでどの台帳か分かります。"},
            "project": {
                "fix": "--project に thth の project の名前（英数字と _・.・-）を入れて打ち直してください。",
                "command": _ADD,
                "better": "口座の台帳と同じ project の名前にそろえると、下書きをどの口座の queue に置くかで迷いません。"},
            "by": {
                "fix": "--by に、頼んだ人の名前を入れて打ち直してください（変更の記録に残ります）。",
                "command": _CONFIRM,
                "better": "代わりに回すときは、店主の名前でなく打った人の名前を --by に入れ、誰が認めたかは台帳の本文に書くと、あとで読んでも取り違えません。"},
            "row_or_trigger": {
                "fix": "thth register draft には --row <行> か --trigger <種類> のどちらか 1 つだけを付けて打ち直してください。",
                "command": _DRAFT,
                "better": "まず thth register triggers <台帳> で今出す頃のきっかけを見て、その行を --row で選ぶと、出しすぎずに済みます。"},
            "row": {
                "fix": "行の番号（頭の id の欄）を英小文字・数字・_・.・- のまま写して打ち直してください。番号は thth register show <台帳> の左の列にあります。",
                "command": _SHOW,
                "better": "行の番号は店の名前と種類をつなげた形（shop-hours・special-2026-12-29 など）にすると、番号だけで中身が分かります。"},
        }),
    "register_name_taken": _entry(
        "その名前の台帳の控えが既にあります。上書きすると、前の台帳の認めた記録と取り違えるので、書きませんでした。",
        "別の名前で thth register add を打ち直すか、今の控えのまま thth register check <台帳> で中身を確かめてください。",
        _CHECK,
        "同じ台帳のフォルダを動かしただけなら、thth register remove で控えを消してから add し直すと、認めた行だけを認め直せば済みます。"),
    "register_missing": _entry(
        "その名前の台帳の控えがありません。",
        "先に thth register add で台帳のフォルダを控えてください（名前の綴りも確かめてください）。",
        _ADD,
        "控えたら、続けて thth register check で形を点検すると、直す所が 1 行ずつ分かります。"),
    "register_ledger_invalid": _entry(
        "台帳の控え（registers/<台帳>.json）か認めた記録（register_state/<台帳>/confirmed.json）が読めないか、決まった欄と違います。"
        "認めた記録が読めないときは、どの行も認めたと読まない側に倒しています。",
        "控えは register・project・path・kind の 4 つの欄だけにしてください。壊れたときは thth register remove <台帳> --by <名前> で"
        "控えを消し、thth register add から作り直して、要る行を認め直します。",
        "thth register remove <台帳> --by <名前>",
        "控えは手で直さず add で作り直すと、欄の取り違えが起きません。"),
    "register_path_unusable": _entry(
        "--path の台帳のフォルダが無いか、フォルダでないか、symlink です（台帳の外を読まないため、symlink は辿りません）。",
        "台帳のフォルダの本当の道（絶対の道か ~ から）を --path に入れて、thth register add を打ち直してください。"
        "控えた台帳のフォルダを動かしたなら、thth register remove で控えを消してから add し直します。",
        _ADD,
        "台帳のフォルダは git の repo にしておくと、変えた跡が残り、下書きの頭に出所の commit が書けます。"),
    "register_row_missing": _entry(
        "その番号の行が台帳にありません（1 行目の（）が番号）。",
        "thth register show <台帳> で行の番号を確かめて、打ち直してください。新しい行なら、台帳のフォルダに .md を 1 枚足します。",
        _SHOW,
        "行を足したら thth register check で形を点検すると、頭の欄の欠けがその場で分かります。"),
    "register_row_invalid": _entry(
        "台帳の行の形が決まりに合っていません（頭の 7 つの欄・印の語・日付の形・種類ごとの形のどれか）。",
        "表示された行ごとの直し方のとおりに .md を直して、thth register check <台帳> で点検し直してください。",
        _CHECK,
        "台帳の repo の CI で thth register check を回すと、形の崩れた行が commit の時点で分かります。"),
    "register_row_secret": _entry(
        "台帳の行に、鍵やパスワードらしい値があります。台帳は git の repo で、写しが残りやすいので、材料にもしません。",
        "その値を行から消して（鍵は thth の token set の命令で THTH の鍵の置き場へ）、thth register check <台帳> で点検し直してください。",
        _CHECK,
        "もう commit した値なら、行から消すのと一緒に、その鍵を発行し直すと安心です。"),
    "register_row_guess": _entry(
        "その行は「推測」の印のままです。推測の行の事実は、投稿・頁・貼る文の材料にしません（間違った事実を出さないため）。",
        "持ち主に中身が合っているかを聞き、合っていれば thth register confirm <台帳> <行> --by <名前> で「本人が認めた」にしてください。",
        _CONFIRM,
        "聞くときは推しを添えて、はい・いいえで答えられる形にすると、持ち主が 1 言で答えられます。"),
    "register_row_denied": _entry(
        "その行は、持ち主が「違う」と言った行です。材料にしません（行は残してあり、同じことを二度聞かないために使います）。",
        "正しい中身を持ち主に聞いて新しい行を書き（source: asked）、thth register confirm <台帳> <新しい行> --by <名前> で認めてもらってください。",
        _CONFIRM,
        "新しい行の本文に、違うと言われた行の番号を 1 行書いておくと、なぜ書き直したかが台帳に残ります。"),
    "register_row_unconfirmed": _entry(
        "その行は頭が「本人が認めた」でも、THTH の認めた記録が無いか、認めたあとに中身が変わっています。"
        "頭の印は手でも書き換えられるので、THTH は記録の指紋で見ています。",
        "今の中身を持ち主に見せて、合っていれば thth register confirm <台帳> <行> --by <名前> で認め直してください。",
        _CONFIRM,
        "中身を直すたびに認め直すのが手間なら、直す前に持ち主と一緒に中身を決めてから 1 回で認めると、往復が減ります。"),
    "register_row_review_passed": _entry(
        "その行は見直し日（review_by）を過ぎています。古いかもしれない事実は材料にしません。",
        "持ち主に今も合っているかを聞き、合っていれば review_by を先の日に直してから thth register confirm <台帳> <行> --by <名前> で認め直してください。",
        _CONFIRM,
        "営業の時間のように変わりにくい行は 3 か月先、催しのように日の決まった行はその日の翌日、と見直し日を中身に合わせると、聞く数が減ります。"),
    "register_row_not_public": _entry(
        "その行は外に出してよい印（public: true）がありません。",
        "持ち主が外に出してよいと言ったら、頭の public を true に直し、thth register confirm <台帳> <行> --by <名前> で認め直してください。"
        "出したくない欄だけがあるなら、行は public: true にして、その欄の名前を private_fields に並べます。",
        _CONFIRM,
        "住所や電話のような欄は、行ごと隠さず private_fields で欄ごとに隠すと、残りの事実は投稿に使えます。"),
    "register_row_retired": _entry(
        "その行は退役（status: 退役）しています。終わった事実は材料にしません。",
        "今の事実なら新しい行として書き、thth register confirm <台帳> <新しい行> --by <名前> で認めてもらってください。",
        _CONFIRM,
        "退役した行は消さずに残すと、前にいつ何があったかを次の年の投稿の材料に使えます。"),
    "register_confirm_mismatch": _entry(
        "--confirm の digest が、今の行の digest と違います。1 段目で見せたあとに行の中身が変わったか、別の行の digest です。",
        "--confirm を付けずにもう一度打って今の全文と digest を持ち主に見せ、合っていればその digest で確定してください。",
        _CONFIRM,
        "1 段目の全文を持ち主にそのまま見せてから確定すると、見せた物と認めた物が食い違いません。",
        by_detail={
            "confirm": {
                "fix": "--confirm を付けずにもう一度打って今の全文と digest を持ち主に見せ、合っていればその digest で確定してください。",
                "command": _CONFIRM,
                "better": "1 段目の全文を持ち主にそのまま見せてから確定すると、見せた物と認めた物が食い違いません。"},
        }),
    "register_row_unwritable": _entry(
        "台帳の行のファイルに書けませんでした（symlink・普通のファイルでない・書く権限が無い・置き場がいっぱい、のどれか）。何も変えていません。",
        "台帳のフォルダの持ち主と権限を確かめ、行を普通のファイルにしてから、同じ命令を打ち直してください。",
        _CHECK,
        "台帳のフォルダを THTH を動かす人の持ち物にしておくと、認める操作が頭の欄を書けます。"),
    # ---------------------------------------------------------------- 段 2: 実店舗の形・きっかけ・下書き
    "register_draft_account": _entry(
        "--to の口座の台帳が読めないか、その口座の queue の置き場（repo_dir と queue_dir）が使えません（1 行目の（）が欄）。",
        "thth list で口座の名前を確かめ、口座の台帳の repo_dir と queue_dir（repo の中の、. で始まらない道）を直してから打ち直してください。",
        "thth list",
        "台帳の project と同じ project の口座を --to に選ぶと、下書きが出す先の queue にそのまま入ります。"),
    "register_draft_nothing": _entry(
        "今は下書きにするきっかけがありません（行を選んだなら、その行のきっかけがまだ早いか、きっかけになる欄が無い）。",
        "thth register triggers <台帳> で今出す頃のきっかけを確かめ、出ている行を --row で選んで打ち直してください。",
        _TRIGGERS,
        "臨時の休みは 1 週間前、催しは 2 週間前からきっかけになります。行を早めに台帳に書いておくと、告知が間に合います。"),
    "register_draft_exists": _entry(
        "同じ行・同じきっかけ・同じ中身の下書きが、もう queue にあります。2 本目は作りませんでした。",
        "queue の下書きを整えて thth approve に通すか、要らなければその下書きを閉じてから、打ち直してください。",
        "thth approve <file>",
        "行の中身を直したなら、直した行を認め直してから作ると、別の下書きとして作れます。"),
    "register_draft_rejected": _entry(
        "作った下書きが queue の形の点検（thth lint）に通りませんでした（1 行目の（）が理由）。書いていません。",
        "行の名前や本文の長さを直して（媒体の字数に収まるように）、打ち直してください。",
        _DRAFT,
        "品や催しの名前は短くし、詳しい説明は思いの行に分けると、どの媒体の字数にも収まります。"),
    "register_draft_expired": _entry(
        "出さない期限（not_after）を過ぎているか、期限が消されています。終わった催しや過ぎた休みを告げないため、下書きにも承認にもしません。",
        "まだ出す意味があるなら、台帳の行の日を今の事実に直して認め直し、thth register draft で作り直してください。過ぎた物なら、下書きを閉じてください。",
        _DRAFT,
        "催しの行は、終わったら status を退役にすると、次の年に同じ催しを書くときの見本として残ります。"),
    "register_row_changed": _entry(
        "下書きを作ったあとに、台帳の行の中身が変わっています（1 行目の（）が行の番号）。古い事実のまま出さないため、承認しません。",
        "thth register use <台帳> <行> で今の中身を確かめ、下書きを今の中身で作り直して（thth register draft）、承認し直してください。",
        "thth register use <台帳> <行>",
        "行を直したら、前の下書きは閉じてから作り直すと、queue に古い事実の下書きが残りません。"),
    "register_recheck_unavailable": _entry(
        "この下書きの元の台帳の控えが、この機械にありません（1 行目の（）が台帳の名前）。店の事実は間違えたときの害が大きいので、読み直せないまま承認しません。",
        "台帳の控えがある機械（thth register add をした機械）で、同じ thth approve を打ってください。",
        _SHOW,
        "台帳を置く機械と承認する機械を同じにしておくと、承認のたびに読み直しが自動で済みます。"),
    "register_log_unavailable": _entry(
        "変更の記録（admin_log）に書けない置き場なので、何も変えませんでした。",
        "THTH の置き場（$THTH_ROOT）の権限と空きを確かめて、同じ命令を打ち直してください。",
        "thth doctor",
        "thth doctor で置き場を一度点検しておくと、ほかの命令も同じ理由で止まらずに済みます。"),
}
