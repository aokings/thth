"""断りの目録の項目（第 10 部: 台帳の機械・`thth/register/`・`thth register …`）。thth/refusal_catalog.py が読む。

符丁は `thth/register/errors.py` の `REASONS`。文は店（実店舗）にも自前のメディアにも読めるように書く。
原則 `docs/原則_前向きな断り.md`: なぜ・どうすれば通るか・どうすればもっと良くなるか。
since は版上げのときに直す（今の版を越えない柵があるので、今は今の版を置く）。
"""
from __future__ import annotations

_C = ("cli",)
_SINCE = "5.19.0"
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
            "question": {
                "fix": "問いの番号（q-YYYYMMDD- と 6 字の形）を、thth register ask の出力からそのまま写して打ち直してください。",
                "command": "thth register questions <台帳>",
                "better": "ask の --json の question の欄を写すと、番号の写し違いが起きません。"},
            "answer_text": {
                "fix": "--asked と --said は 1 行・200 字までにし、鍵やパスワードらしい値を入れずに打ち直してください。",
                "command": "thth register answer <台帳> <問い> --result yes --by <名前>",
                "better": "答えの中身は台帳の行に書き、--said には要約の 1 文だけを入れると、問いの記録が読みやすく残ります。"},
            "row": {
                "fix": "行の番号（頭の id の欄）を英小文字・数字・_・.・- のまま写して打ち直してください。番号は thth register show <台帳> の左の列にあります。",
                "command": _SHOW,
                "better": "行の番号は店の名前と種類をつなげた形（shop-hours・special-2026-12-29 など）にすると、番号だけで中身が分かります。"},
            "previous": {
                "fix": "thth register published には、前に配った控えの頭を --previous <置き場> で渡すか、初めて回すときだけ"
                       " --no-previous を付けて打ち直してください。頭が無いと、控えの行を消して頭も書き直した控えを見抜けません。",
                "command": "thth register published <台帳> --outlet <出し口> --list <一覧> --previous <前に配った控えの頭>",
                "better": "CI の手順で、緑のときの --publish の置き場を公開の場所に置き、次の回はそこから --previous に渡すと、"
                          "控えの書き直しが CI で分かります。"},
            "checked_by": {
                "fix": "--by と --checked-by には、人か道具の名前を字で入れて打ち直してください（空白・点・見えない字だけの"
                       "名前は、集めた者と検めた者を比べられません）。",
                "command": "thth register merge <台帳> <束> --by <集めた者> --checked-by <検めた者>",
                "better": "名前は大文字小文字・全角・空白・末尾の点をそろえて比べます。別の者には、字の違う名前を付けてください。"},
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
        "控えは手で直さず add で作り直すと、欄の取り違えが起きません。",
        by_detail={
            "drafts": {
                "fix": "THTH の側の下書きの控え（register_state/<台帳>/drafts.json）が読めないので、この下書きが台帳から作った物か"
                       "確かめられません。thth register draft で下書きを作り直すと控えも書き直されるので、新しい下書きを承認してください。",
                "command": _DRAFT,
                "better": "下書きの控えは THTH が書く物なので、手で開かずにおくと、承認のたびの読み直しが自動で済みます。"}}),
    "register_path_unusable": _entry(
        "--path の台帳のフォルダが無いか、フォルダでないか、symlink です（台帳の外を読まないため、symlink は辿りません）。",
        "台帳のフォルダの本当の道（絶対の道か ~ から）を --path に入れて、thth register add を打ち直してください。"
        "控えた台帳のフォルダを動かしたなら、thth register remove で控えを消してから add し直します。",
        _ADD,
        "台帳のフォルダは git の repo にしておくと、変えた跡が残り、下書きの頭に出所の commit が書けます。",
        by_detail={
            "inside_thth_state": {
                "fix": "台帳のフォルダが THTH の置き場（$THTH_ROOT の下のどこか・~/.config/thth の下）にあります。そこは "
                       "thth register remove と口座やメディアの leave が丸ごと消しうる所なので、台帳が消えてしまいます。"
                       "台帳のフォルダを THTH の置き場の外（持ち主の repo の中など）に移してから、--path にその道を入れて打ち直してください。",
                "command": _ADD,
                "better": "台帳のフォルダは git の repo にしておくと、変えた跡が残り、下書きの頭に出所の commit が書けます。"},
            "contains_thth_state": {
                "fix": "--path のフォルダが、THTH の置き場（$THTH_ROOT・~/.config/thth）を中に含む親のフォルダ（/ やホームなど）です。"
                       "そこを台帳と呼ぶと THTH の置き場が台帳の中に入り、歩くのも消さない囲いも広すぎます。台帳だけを入れた"
                       "フォルダ（持ち主の repo など）を --path に入れて打ち直してください。",
                "command": _ADD,
                "better": "台帳ごとに 1 つのフォルダ（git の repo）にしておくと、外す命令の見積りも健康も速く終わります。"}}),
    "register_row_missing": _entry(
        "その番号の行が台帳にありません（1 行目の（）が番号）。",
        "thth register show <台帳> で行の番号を確かめて、打ち直してください。新しい行なら、台帳のフォルダに .md を 1 枚足します。",
        _SHOW,
        "行を足したら thth register check で形を点検すると、頭の欄の欠けがその場で分かります。"),
    "register_row_invalid": _entry(
        "台帳の行の形が決まりに合っていません（頭の 7 つの欄・印の語・日付の形・種類ごとの形のどれか）。",
        "表示された行ごとの直し方のとおりに .md を直して、thth register check <台帳> で点検し直してください。",
        _CHECK,
        "台帳の repo の CI で thth register check を回すと、形の崩れた行が commit の時点で分かります。",
        by_detail={
            "private_needed": {
                "fix": "下書きや貼る文を組むのに要る欄（臨時は日と休みか・時刻、催しは名前と始まりと終わり、営業の時間の変わりは"
                       "変わる日、品は名前）が、その行の private_fields に入っています。その欄を private_fields から外して"
                       "認め直してから、打ち直してください。",
                "command": _CONFIRM,
                "better": "外に出したくないのが理由や住所なら、その欄だけを private_fields に入れると、告知は出しつつ中身を守れます。"}}),
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
        "出さない期限（not_after）を過ぎているか、期限が消されたか、下書きを作ったときより後に動かされています。"
        "終わった催しや過ぎた休みを告げないため、下書きにも承認にもしません。",
        "まだ出す意味があるなら、台帳の行の日を今の事実に直して認め直し、thth register draft で作り直してください。過ぎた物なら、下書きを閉じてください。",
        _DRAFT,
        "催しの行は、終わったら status を退役にすると、次の年に同じ催しを書くときの見本として残ります。",
        by_detail={
            "not_after_moved": {
                "fix": "下書きの頭の not_after を、作ったときの値（thth register draft の出力の「出さない期限」）に戻してから、"
                       "thth approve を打ち直してください。日が延びたなら、台帳の行の日を直して認め直し、作り直します。",
                "command": _DRAFT,
                "better": "LLM に本文を整えてもらうときは「頭の行はそのまま残して」と添えると、期限も出所も変わらずに済みます。"}}),
    "register_row_changed": _entry(
        "下書きを作ったあとに、台帳の行の中身が変わっています（1 行目の（）が行の番号）。古い事実のまま出さないため、承認しません。",
        "thth register use <台帳> <行> で今の中身を確かめ、下書きを今の中身で作り直して（thth register draft）、承認し直してください。",
        "thth register use <台帳> <行>",
        "行を直したら、前の下書きは閉じてから作り直すと、queue に古い事実の下書きが残りません。",
        by_detail={
            "from_register_mismatch": {
                "fix": "下書きの頭の from_register の行が、THTH がこの下書きを作ったときの控えと違います。頭の from_register の 4 行を"
                       "作ったときのまま戻すか、出したい行で thth register draft を打って作り直してください。",
                "command": _DRAFT,
                "better": "LLM に本文を整えてもらうときは「頭の行はそのまま残して」と添えると、出所が変わらずに済みます。"}}),
    "register_recheck_unavailable": _entry(
        "この下書きの元の台帳の控えが、この機械にありません（1 行目の（）が台帳の名前）。店の事実は間違えたときの害が大きいので、読み直せないまま承認しません。",
        "台帳の控えがある機械（thth register add をした機械）で、同じ thth approve を打ってください。",
        _SHOW,
        "台帳を置く機械と承認する機械を同じにしておくと、承認のたびに読み直しが自動で済みます。",
        by_detail={
            "from_register_missing": {
                "fix": "名前は thth register draft の下書きの形なのに、頭に from_register の行が無く、この機械の控えにもありません。"
                       "どの行から作ったか分からないので読み直せません。台帳の控えのある機械で thth register draft を打って作り直してください。"
                       "台帳から作った物でなければ、ファイルの名前から -register- を外してから打ち直します。",
                "command": _DRAFT,
                "better": "LLM に本文を整えてもらうときは「頭の行はそのまま残して」と添えると、承認のときの読み直しが自動で済みます。"}}),
    # ---------------------------------------------------------------- 段 3: 聞いて埋める
    "register_question_log_invalid": _entry(
        "台帳のフォルダの問いの記録（問いの記録.jsonl）が読めないか、形が違います（symlink・大きすぎる・JSON の行でない）。"
        "聞いた数を数えられないので、新しく聞くことも答えを足すこともしません。",
        "問いの記録.jsonl を git で前の版に戻すか、別の名前に移してから、同じ命令を打ち直してください（新しい記録から数え直します）。",
        "thth register questions <台帳>",
        "問いの記録は手で直さず、台帳の repo に commit しておくと、壊れたときに前の版へ戻せます。"),
    "register_question_missing": _entry(
        "その番号の問いが、問いの記録にありません（1 行目の（）が番号）。",
        "thth register ask <台帳> --by <名前> の出力の問いの番号を写して、打ち直してください。",
        "thth register questions <台帳>",
        "聞いたらすぐ answer で記録すると、どの問いの答えかを取り違えません。"),
    "register_question_answered": _entry(
        "その問いには、もう答えが記録されています（1 行目の（）が番号）。答えは足すだけで書き換えません。",
        "答えが変わったなら、台帳の行を直して認め直してください。もう一度聞くなら、次の ask で新しい問いの番号が出ます。",
        "thth register ask <台帳> --by <名前>",
        "答えを書き換えずに新しい問いとして残すと、いつ答えが変わったかが記録に残ります。"),
    "register_log_unavailable": _entry(
        "変更の記録（admin_log）に書けない置き場なので、何も変えませんでした。",
        "THTH の置き場（$THTH_ROOT）の権限と空きを確かめて、同じ命令を打ち直してください。",
        "thth doctor",
        "thth doctor で置き場を一度点検しておくと、ほかの命令も同じ理由で止まらずに済みます。"),
    # ---------------------------------------------------------------- 段 4: 外の場所に手で貼る束
    "register_paste_kind": _entry(
        "外の場所に貼る文を作れるのは、臨時（special）・営業の時間（hours）・催し（event）の行だけです（1 行目の（）が渡した行）。",
        "貼りたい中身を臨時・営業の時間・催しのどれかの行にして、thth register paste <台帳> <その行> を打ち直してください。",
        "thth register paste <台帳> <行>",
        "品や店の紹介は、自前のメディアの記事（thth register draft）から外の場所へリンクで届けると、文を二か所で直さずに済みます。"),
    "register_paste_nothing": _entry(
        "この台帳に外の場所の行（kind: place）が 1 枚も無いので、どこに貼るかが分かりません。",
        "店の情報が載っている場所（Google マップ・食べログなど）ごとに、kind: place・place: <場所の番号> の行を台帳に足して、"
        "thth register check で形を確かめてから打ち直してください。",
        _CHECK,
        "場所の行に admin_by（管理画面を誰が持つか）を書いておくと、貼る人を毎回聞かずに済みます。"),
    "register_place_not_place": _entry(
        "1 行目の（）の行は外の場所の行（kind: place）ではありません。",
        "thth register show <台帳> で kind が place の行の番号を見て、その番号で打ち直してください。",
        _SHOW,
        "場所の行の番号は place-tabelog・place-google のように場所の名前を入れると、取り違えにくくなります。"),
    "register_compare_empty": _entry(
        "比べる文が空か、長すぎます（5000 字まで）。",
        "外の場所の頁の、営業の時間やお知らせの欄の文だけを写して、標準入力に渡して打ち直してください。",
        "thth register compare <台帳> <場所の行> --for <行> < 写した文",
        "欄の文だけを写すと、ほかの欄の日付と取り違えずに比べられます。"),
    # ---------------------------------------------------------------- 台帳の器 段 1: 読んで、同じ対象を指す
    "register_kind_invalid": _entry(
        "台帳の .thth/ の宣言（種類の宣言・憲法・番号の表）が決まった形に合っていません（1 行目の（）が、どの宣言の"
        "どの語か）。THTH は宣言を語と道の型として読むだけで、宣言に書いた命令は実行しません。知らない欄も断ります。",
        "（）の語の欄を直してください。種類の宣言に書けるのは schema・kind・place・format・records・max_bytes・"
        "rows_at・id・questions・pointers・delimiter・units・inspects・checks・note だけで、値は欄の名前・道の型・語です"
        "（checks は 9 つの部品と決まった形の条件だけ。式・正規表現は書けません）。forbid_pair・count の via の欄に"
        "指す欄が 2 本以上あるときは、via_to に先の種類の名前を書いて 1 本を選んでください。直したら打ち直してください。",
        "thth register kinds <台帳>",
        "宣言は台帳の repo に commit しておくと、形を変えたこと（意味を変えたこと）が跡に残り、持ち主が確かめられます。"),
    "register_yaml_refused": _entry(
        "YAML の記録に、器が読む範囲の外の書き方があります（同じ段の同じ鍵・&と*の別名・2 つめの文書・!! の型の札・<<: "
        "のどれか。語と行は --json の problems）。黙って読み違えないように、中は読まずに断りました。版は取ってあります。",
        "その書き方を開いて書き直してください（別名は値を書き写す・型の札は外して字で書く・2 つめの文書は別の枚に分ける・"
        "重なる鍵は 1 つにまとめる）。直したら thth register check <台帳> で点検し直してください。",
        _CHECK,
        "日付や true・false も字のまま読み、型は種類の宣言で見るので、引用符を足す直しは要りません。"),
    "register_record_invalid": _entry(
        "宣言した種類の記録の読みに問題があるか（番号の形・置き場の番号のフォルダの名（id_folder）・同じ番号が 2 つ・"
        "表の列の数・大きさの上限・UTF-8・頭つきの .md の頭（md_head_missing・md_head_open・md_head_shape）のどれか。"
        "記録ごとの語は --json の problems）、種類の宣言の検査（checks）のうち赤（error）の検査に合わない記録があります"
        "（1 行目の（）の checks の数。検査の呼び名・注・記録と理由の語は --json の checks）。同じ番号の記録は、黙って 1 つにしません。",
        "記録ごとの語、または検査の注と理由の語のとおりに元のファイルを直すか、種類の宣言（番号の取り方・max_bytes・"
        "checks）を直して、thth register check <台帳> で点検し直してください。",
        _CHECK,
        "番号は一度付けたら変えないでください。番号で判断が付くので、付け直すと前の確かめが別の記録へ移りません。"),
    "register_record_missing": _entry(
        "その記録（または単位・値）が台帳にありません（1 行目の（）が、何を探したか）。記録は <種類>:<番号> で指します。",
        "thth register check <台帳> --json の catalog で番号を確かめて、打ち直してください。",
        _CHECK,
        "同じ番号でも種類が違えば別の記録です（枚と点の表など）。種類の名前から付けて指すと取り違えません。"),
    "register_unit_ambiguous": _entry(
        "渡した鍵（か番号）に当たる単位が 2 つ以上あります。器は鍵で決まらない単位を黙って 1 つにしません"
        "（並べ替えや挿入で、確かめが別の値に移らないように）。",
        "--json の candidates の指し先（kc: で始まる、鍵と中身の指紋）を 1 つ選んで、--unit <指し先> で打ち直してください。",
        "thth register links <台帳> <記録> --units",
        "元の表に、その 2 つを分ける欄（コース・内訳など）があれば鍵に足すと、鍵だけで指せるようになります。"),
    "register_link_invalid": _entry(
        "指す欄に、指す先の無い番号か、輪があります（輪を禁じるのは 含む・親・置き換える だけ。主張どうしの往復は止めません）。"
        "どれかは --json の dangling と cycles。",
        "指す先の番号を直すか、指す先の記録を足してください。輪は、どこかの 1 本を外してください。"
        "直したら thth register check <台帳> で点検し直してください。",
        _CHECK,
        "台帳の repo の CI で check を回すと、指す先の切れが commit の時点で分かります。"),
    "register_judgment_invalid": _entry(
        "台帳の判断の記録（.thth/judgments.jsonl）の 1 行が、決まった形に合いません（1 行目の（）が行の番号と、"
        "合わない欄の名前）。判断の 1 行には、id・question・target.record（どれも 1 字以上の字）が要り、"
        "target.unit と target.value は字、answer・reason・seen・content は辞書、at は字、siblings は字の並びです。"
        "段 3 の命令（verify・export・accept・use）は加えて、id が中身から作った番号と合わない行（id_not_from_content）・"
        "中身の違う前の行と同じ id の行（id_same_as_line と前の行の番号）・at が日時として読めない行（at）も断ります。"
        "番号を借りた行は、受け入れていない判断を封の中の判断に見せかけられるためです。"
        "形の合わない行が 1 つでもあると、どの判断が効いているかを数え違えるので、全体を読みません。",
        "その行を、git の前の版から戻してください（判断の記録は足すだけで、手で書き換えない物です）。"
        "前の版が無ければ、その行を外して、同じ判断を thth の命令で出し直してください（命令が番号と日時を正しく付けます）。",
        _CHECK,
        "判断の記録を台帳の repo に commit しておくと、壊れたときに前の版へ戻せます。"),
    "register_judgment_same_person": _entry(
        "中身を変えた者と、結び直す者が同じです。同じ者の判断は、別の者の確かめに数えません。",
        "中身を変えていない別の者に、新しい版を見てもらって結び直してください。",
        _CHECK,
        "直しと確かめを同じ便に入れると、頁から外れる間を作らずに済みます。"),
    "register_carry_unnamed": _entry(
        "中身を変えた者が分からないので、結び直す者が別の者かを確かめられません。別の者の確かめに数えられないため、"
        "結び直しませんでした。",
        "中身を変えた者（commit の書き手や、取り込んだ道具の名前）を名指しして、もう一度結び直してください。",
        _CHECK,
        "取り込みの道具が、直した者の名前を変更の記録に残すようにしておくと、結び直しのたびに調べずに済みます。"),
    # ---------------------------------------------------------------- 台帳の器 段 2
    "register_adopt_unusable": _entry(
        "取り込みか戻す出口か束の、フォルダの組み合わせが使えません（1 行目の（）の語: source は元が無い・"
        "inside は片方がもう片方の中・not_empty は書き先が空でない・other_source は台帳が別の元から取り込まれた・"
        "symlink は範囲の中か範囲の道の途中か、書き先とその .thth/ に symlink がある・thth_in_source は範囲が .thth/ を"
        "含む・plan は範囲の JSON の形・spelling は範囲の道の綴りが実在の綴りと大文字小文字か Unicode の正規化で違う・"
        "too_large は種類の宣言の上限を超える枚か、どの種類の宣言にも当たらない 64 KB を超える枚がある・unreadable は"
        "権限で読めないフォルダかファイルがある（読めない物を「消えた」と読まない）。元にも台帳にも何も書いていません。",
        "語に合わせて直してください: 書き先は新しい空のフォルダにする・元の repo の外に置く・symlink と .thth/ は"
        "範囲の exclude に足す（書き先の symlink は本物のフォルダにする）・spelling は --json の actual の綴りで範囲を"
        "書く・too_large は --json の files の種類の宣言に max_bytes を書く（kind が null の枚は、その道の種類を宣言して"
        " max_bytes を書くか、範囲の exclude に足す）・unreadable は --json の path を読めるようにするか、範囲の exclude に"
        "足す・範囲は {\"schema\": \"thth.adopt_plan/1\", "
        "\"include\": [...], \"exclude\": [...]} の形にする。",
        "thth register adopt <元> --to <新しいフォルダ> --plan <範囲.json> --by <名前>",
        "影の台帳は ~/Documents/THTH/shadow/<メディア>/ のように repo の外の決まった置き場に作ると、元と取り違えません。"),
    "register_batch_pending": _entry(
        "台帳に、途中で止まったまとめての入れ替えが残っています（1 行目の（）が何の入れ替えか）。混ざった姿を読まない"
        "ように、器の読み手（check・health・links・used など）は断り、ほかの書き込みも止めています。前の姿で読めるのは"
        "控えの読み・restore・resume だけです。",
        "続けるなら thth register resume <台帳> --continue --by <名前>（同じ命令を打ち直してもよい）、戻すなら"
        " thth register resume <台帳> --rollback --by <名前> を打ってください。別の者が打ってもかまいません。",
        "thth register resume <台帳> --continue --by <名前>",
        "--json の batch・what・by・at で、誰がいつ何を入れ替えていたかが分かります。打ち直す前に、その者に一言かけると"
        "二重に進めずに済みます。"),
    "register_batch_conflict": _entry(
        "入れ替えの途中か前に、台帳のファイルが器の知らない中身に変わっていました（1 行目の（）: moved は前でも後でも"
        "ない中身・base は束を作った時から台帳が動いた・symlink は書く先か読む先の途中の段（.thth/ の中も）が symlink で、"
        "台帳の外を指しうる・busy は読んでいる間に入れ替えが何度も始まった）。黙って上書きせず、台帳の外にも書かずに"
        "止めました。",
        "moved と base は、--json の path のファイルを誰が書いたかを確かめ、その書き込みを済ませてから、thth register keep "
        "で今を控え、束を今の台帳から作り直して打ち直してください。symlink は、--json の path の symlink を本物の"
        "フォルダかファイルに置き換えてから打ち直してください。busy は、入れ替えが済んでから打ち直してください。",
        "thth register diff <台帳> last..now",
        "元の形式の道具と器の入れ替えを同じ時間に走らせないと、この断りは出ません。"),
    "register_kept_missing": _entry(
        "器の控え（.thth/kept/）に、求めた前の版がありません（1 行目の（）の語）。取り出せる前の版は、器が取り込んだか"
        "控えた時点からだけで、それより前と、控えと控えの間の版は取り出せません。",
        "thth register keep <台帳> --list で控えた時点を並べ、その目録の指紋の頭（8 字以上）か first・last で打ち直して"
        "ください。控えが壊れている（object_broken・snapshot_broken）ときは、元からもう一度取り込んでください。",
        "thth register keep <台帳> --list",
        "元の形式の道具で直したあとに thth register keep を打つ習いにすると、git の外の台帳でも前の版を取り出せます。"),
    "register_version_unavailable": _entry(
        "求めた時点の版を取り出せません（1 行目の（）の語）。not_git は台帳のフォルダが git の作業木でない・"
        "unknown_commit はその名の commit が無い・no_places はその commit に宣言の置き場の枚が 1 つも無い・"
        "git_missing は決まった場所に git が無い・git_failed は git が読めなかった・no_judgments_kept は段 5 より前の"
        "控えで判断の記録を控えていない・judgments_erased は消す求めがその時点の判断の記録の控えを除いた、です。"
        "器は取り出せない版を推して埋めません。",
        "git の commit で求めたときは、控えの名（keep --list の目録の指紋の頭・first・last）で打ち直してください。"
        "no_judgments_kept は commit の名で、judgments_erased は消す求めより後の控えか commit の名で打ち直してください。",
        "thth register keep <台帳> --list",
        "台帳の repo に commit する習いにすると、控えが無い時点でも commit の名で前の版を取り出せます。"),
    "register_judgments_rewritten": _entry(
        "判断の記録（.thth/judgments.jsonl）が、前の時点の記録の続きになっていません（前の行が書き換わったか、減った。"
        "1 行目の（）の line が前の時点の行の番号）。判断の記録は足すだけなので、書き換えは跡を消すことになります。"
        "消す求めの結果に載った印の行は、骨が前の行と同じなら通します。",
        "その行を git の前の版から戻してください。判断を取り消したいときは、行を書き換えずに、止める判断を thth の命令で"
        "足してください。直したら thth register check <台帳> --against <前> で確かめ直してください。"
        "消す求めで書き換えた行は、受け入れ直して封に載ってから通ります（thth register accept。結果の記録だけでは通しません）。"
        "この台帳は封が無い（出し口が 1 つも無い）なら、消したあとの差は確かめられないので、消す前の控えの名ではなく、"
        "消したあとの控えの名か commit を基準に替えてください。",
        _CHECK,
        "CI で check --against <前の commit> を回すと、判断の記録の書き換えが commit の時点で分かります。"),
    "register_published_gate": _entry(
        "出した URL の関門が赤です（--json の problems に URL と語: missing は前に出した URL が一覧から消えて転送も"
        "消した印も無い・redirect_target_missing は転送の先が一覧に無い・redirect_chain は転送が 2 段・"
        "redirect_status は転送の番号が決まりの外・duplicate と redirect_with_page は決まりで赤にした物）。"
        "出した URL を黙って 404 にしないための関門で、控えには何も足していません（終わりの番号は 1）。",
        "消えた URL には、出し口の宣言の published.redirects に転送を足すか、published.gone に消した理由を書いて"
        "ください。転送の先は一覧にある URL にしてください。直したら同じ一覧で打ち直してください。",
        _CHECK,
        "build のあと、配る前に published を回し、緑のときだけ --record で控えに足すと、出した URL の跡が途切れません。"),
    "register_record_vanished": _entry(
        "前の時点にあった記録の番号（かファイルか、内側の行の明示の番号。--json の units_vanished）が、後の時点にありません。番号が消えると、その番号への判断と、"
        "指していた記録がたどれなくなります。",
        "消すつもりでなければ、thth register restore <台帳> --at <前の控え> --to <フォルダ> で前の版を書き出して戻して"
        "ください。退役させたいなら、記録を消さずに状態を退役にしてください。消す求めで除いた記録なら、受け入れ直して"
        "封に載ってから通ります（thth register accept。--json の erase_unsealed が受け入れ直す前の求め）。"
        "この台帳は封が無い（出し口が 1 つも無い・--json の erase_no_outlets）なら、消したあとの差は確かめられないので、"
        "消す前の控えの名ではなく、消したあとの控えの名か commit を基準に替えてください。",
        "thth register diff <台帳> <前>..<後> --json",
        "名前を変えただけなら、番号の表（.thth/ids.json）に新しい場所を書くと、番号を引き継げます。"),
    "register_write_unsafe": _entry(
        "表の 1 行を書くと、触っていない行のバイトが変わるか、書いた行が読み直せない形になります（1 行目の（）の語: "
        "other_rows・no_final_newline・unknown_field・id_field・id_exists・id_form・row_width・not_table・which_table・"
        "table_shape・not_utf8・row_split_mismatch・path）。row_split_mismatch は、器の行の分け方と csv の読み方が"
        "食い違う表（セルの途中に引用符が 1 つある行など）で、書くと器から見えない行を消しかねません。path は器が"
        "書かない道（.thth・.git。大文字小文字違いも）。表には何も書いていません。",
        "語に合わせて直してください: 最後の行に改行が無い表は、元の道具で改行を足してから・欄の名前は表の見出しのとおりに・"
        "番号の欄は直さずに新しい行として足す・番号が重なるなら別の番号で・row_split_mismatch は元の道具でセルの"
        "途中の引用符を \"\" にするかセルを引用符で包んでから。",
        "thth register links <台帳> <種類:番号>",
        "1 行ずつ直すより、束をまとめて入れる（thth register merge）ほうが、止まっても前か後のどちらかに決まります。"),
    "register_merge_refused": _entry(
        "この束は入れていません（1 行目の（）の語）。unchecked は検めた者が無い・unchecked_once は --unchecked を"
        "1 度だけ言った・same_person は集めた者と検めた者が同じ・verdict_refused は束の中に判定が断りの検収の行がある"
        "（--json の rows）・id_overlap は束を入れると同じ番号の記録が 2 つになる（--json の records）・"
        "inspection_unreadable は束の中の検収の表に、検収の記録として読めない行がある（--json の rows に表の道・"
        "行の番号・わけ）・verdict_unknown は検収の行の語が憲法に無い（大文字違い・前後の空白も無い語）・"
        "reserved_path は束が器の置き場（.thth・.git。大文字小文字違いも）の道を持つ。台帳には何も書いていません。",
        "語に合わせて直してください: 集めた者と別の者に束を検めてもらい --checked-by <名前> を付けて打ち直す。"
        "検めずに入れると決めたなら --unchecked を 2 度並べて言う（取り込みの跡に「検めていない」と残ります）。"
        "断りの判定の行は、その行と、断られた記録の直しを束から外すか直してから。重なる番号は、別の番号にしてから。"
        "読めない検収の行は、幅・指す先・番号を直すか束から外してから。憲法に無い語は、憲法の語に書き直すか、"
        "憲法に語を足してから。器の置き場の道は束から外してから。",
        "thth register merge <台帳> <束> --by <集めた者> --checked-by <検めた者>",
        "検めの結果の表を同じ束に入れると、版を示す検収の行は取り込みと同時に新しい版への判断として結ばれ、"
        "頁から外れる間を作らずに済みます。"),
    # ---------------------------------------------------------------- 台帳の器 段 3
    "register_outlet_unknown": _entry(
        "その出し口が、台帳の .thth/outlets.json に宣言されていないか、宣言の形が崩れています（1 行目の（）の語: "
        "outlet は名前が無い・outlets: に続く語は宣言の崩れた所）。材料は書いていません。",
        "thth register kinds <台帳> で種類の名前を確かめ、.thth/outlets.json の outlets に出し口を足すか直してから、"
        "打ち直してください（出し口ごとに audience・kinds・requires・fact・reservation を書きます）。kinds を"
        "種類ごとの設定（private_fields・inserts・hide）で書くのは読者の出し口だけ・欄の名前は英数字と _ だけ・"
        "dest は台帳の外の相対の道で、内の道具の出し口と読者の出し口で同じにしない（語の dest_shared）。"
        "private_fields に書いた欄の名前は、その種類の記録のどれかに在ること（語の private_missing。見出しの空白・"
        "大文字違いで黙って出さないように断ります）・dest は .thth・.git を含まず、内の出し口と読者の出し口で"
        "入れ子にもしない・"
        "reader_fields（読者に見える欄）も読者の出し口だけ・頭の internal_words は字のままの語の並び（512 語・"
        "1 語 64 字まで・空と改行は不可）・published（出した URL の関門）の redirects は from・to・status（数）、"
        "gone は url と reason_kind（英数字の語）、redirect_status は数の並び、duplicate と redirect_with_page は"
        " red か warn。",
        "thth register kinds <台帳>",
        "読者に出る出し口と内の道具が読む出し口を分けて宣言すると、出さない層が公開の材料へ混ざりません。"),
    "register_use_not_usable": _entry(
        "その記録は、この出し口に、頼んだ「何として」では使えません（1 行目の（）の語: stopped は止めている・"
        "not_accepted は判断はあるが受け入れていない・no_judgment は判断が無い・no_inspection_for_new_version は"
        "中身が変わって新しい版への検収が無い・against_changed は照らした根拠が変わった・expired は期限が切れた・"
        "not_permitted は出してよいの判断が無い・reason_kind_not_allowed は留保にしてよい理由の種類でない・"
        "only_reservation は留保としてだけ使える・no_seal はまだ封が無い）。手元でも CI でも同じ答えです。",
        "語に合わせて進めてください: 判断が無い・古いなら thth register judge で新しい版へ判断を足し（直した者と別の者なら"
        " --carry で結び直せます）、thth register accept で受け入れて封をし直す。留保としてだけ使えるなら --as reservation で。",
        "thth register judge <台帳> <種類:番号> --question <問い> --conclusion good --by <名前>",
        "直しと検収を同じ便に入れると、頁から外れる間を作らずに済みます。"),
    "register_material_stale": _entry(
        "書き出し先に、この出し口の一覧に無い旧い材料のファイルがあります（--json の stale。. で始まるフォルダの中も"
        "見ます）。読まれ続けると、外したはずの記録が頁に残るので、書くのをやめました。1 行目の（）の語が other_outlet なら、"
        "読む者の違う出し口（内の道具と読者）の材料が同じ書き出し先にあり、上書きすると出さない欄や内部の層が"
        "読者の材料へ混ざります。THTH はファイルを消しません。",
        "一覧に無いファイルを、持ち主の手で書き出し先から外して（git の repo なら git rm）、打ち直してください。"
        "other_outlet なら、出し口ごとに別の書き出し先を使います。build の道具など材料でない物は、出し口の宣言の"
        " ignore に道の型で書けば見ません。",
        "thth register export <台帳> --outlet <出し口> --to <先> --as-of <日時> --check",
        "書き出し先を出し口ごとのフォルダに分けると、旧い材料がほかのファイルと混ざりません。"),
    "register_seal_missing": _entry(
        "この出し口は、まだ受け入れて封をしていません。封の中の判断だけで材料を作るので、封が無いと材料を作れません。",
        "thth register accept で、今の判断を持ち主に見せて受け入れ、封をしてください（二段）。",
        "thth register accept <台帳> --outlet <出し口> --to <先> --as-of <日時> --by <名前>",
        "初めての出し口は、thth register judge --baseline で今の版を基準として受けてから accept すると、頁を変えずに始められます。"),
    "register_seal_unverified": _entry(
        "公開の直前の確かめが赤です（1 行目の（）の語が最初の赤。全部は --json の problems）。受け入れていない判断・"
        "封のあとの止める・期限・中身の変わり・世代の戻り・判断の記録の消え・別の鍵の封・別の出し口の封・宣言の変わりのどれかが"
        "あります。手元の use・export・accept では、封の署名が合わない（signature）・公開鍵が無い（public_key）・"
        "別の出し口の封（other_outlet）のとき、封なしとして扱って止まります。",
        "語に合わせて直してください: 新しい判断や止めるを足したなら thth register accept で封をし直す・世代を戻したなら"
        "最後の世代の封と材料に戻す・判断の記録の行を消したなら git の前の版から戻す・公開鍵は CI の変数"
        " THTH_REGISTER_SEAL_PUBLIC_KEY に持ち主の公開鍵を置く（repo の中の鍵のファイルは読みません）・"
        "前に配った物は台本（--publish）が置いた thth-seal.<出し口>.json を渡す（封だけの写しは読みません）・"
        "手元で署名が合わないなら、封のファイルを git の前の版から戻すか、持ち主が受け入れ直す。",
        "thth register verify <台帳> --outlet <出し口> --material <先> --previous <前に配った物>",
        "配る前に手元で同じ verify を回すと、CI で止まる前に気づけます。夜に期限が切れて朝の配信が止まるのを避けたい出し口は、"
        "出し口の宣言で on_narrow を drop にすると、CI が外して配ります。",
        by_detail={
            "salt_missing": {
                "fix": "台帳の塩（.thth/seals/salt）がありません。塩は、封の署名と公開の写しの要約に混ぜる秘密の値で、"
                       "これが無いと封を確かめられず、前に配った物とも比べられません。消した・書き換えたなら git の前の版から"
                       "戻してください。戻せないなら、一度ふつうに受け入れて（塩を作り直す）、初めての配りとして "
                       "--no-previous で配り直してください（前に配った物との続きは確かめられなくなります）。",
                "command": "thth register accept <台帳> --outlet <出し口> --to <先> --as-of <日時> --by <名前>",
                "better": "塩は台帳の中にだけ置く物です。台帳の repo を公開していると塩も公開になり、公開の写しから短い値を"
                          "当て直せるので、公開する repo には台帳を置かないでください。"},
            "salt_broken": {
                "fix": "台帳の塩（.thth/seals/salt）が 32 バイトでないか、symlink です。器は塩を作り直しません（替えると前に"
                       "配った物と比べられなくなるので）。git の前の版から戻すか、ファイルを除いてから受け入れ直し、"
                       "--no-previous で配り直してください。",
                "command": "thth register accept <台帳> --outlet <出し口> --to <先> --as-of <日時> --by <名前>",
                "better": "塩のファイルは手で開かずにおくと、壊れません。"},
            "previous_old_form": {
                "fix": "前に配った物が古い形（封の細目をそのまま持つ写し）です。今の写しは、記録の番号や版の指紋を出さない"
                       "塩を混ぜた形なので、古い写しとは比べられません。一度ふつうに受け入れて、初めての配りとして "
                       "--no-previous で配り直してください。次の回からは新しい写しと比べます。",
                "command": "thth register verify <台帳> --outlet <出し口> --material <先> --no-previous --publish <配る物>",
                "better": "配り直したら、古い写し（thth-seal.<出し口>.json）は公開の場所から下げると、番号と版の指紋が"
                          "残りません。"}}),
    "register_seal_key_missing": _entry(
        "封の鍵か、封に使う openssl（3 か 4）がありません（1 行目の（）の語: key は持ち主の秘密の鍵が無い・"
        "public_key は確かめに使う公開鍵が無い・openssl は決まった場所に OpenSSL 3・4 が無い・public_key_form は公開鍵の形が違う）。",
        "持ち主の機械で thth register key init を 1 回打ち、出た公開鍵を CI の変数 THTH_REGISTER_SEAL_PUBLIC_KEY に"
        "置いてください（置くのは持ち主の手か承認で）。openssl が無い機械では、Homebrew などで OpenSSL 3 以上を入れてください。",
        "thth register key init",
        "公開鍵の指紋（thth register key show）を控えておくと、CI に置いた鍵が持ち主の物かを見分けられます。"),
    "register_seal_key_unsafe": _entry(
        "持ち主の秘密の鍵の置き場が使えません（1 行目の（）の語: inside_register は台帳か台帳を含む repo の中にある・"
        "key_mode は持ち主のほかにも読める権限か symlink・key_exists はもう鍵がある）。鍵の値は出していません。",
        "秘密の鍵は ~/.config/thth/ に置き、権限は 0600 にしてください。台帳や repo の中に置かないでください。"
        "もう鍵があるなら、作り直さずにそのまま使ってください（替えると CI の公開鍵も替えることになります）。",
        "thth register key show",
        "XDG_CONFIG_HOME を台帳の repo の中に向けないでおくと、鍵が commit に紛れ込みません。"),
    "register_common_unusable": _entry(
        "共通の台帳が使えません（1 行目の（）の語: path はフォルダが無い・common は --common が無い・rows は表の行の"
        "記録で、版ごとに取り出せない）。採っていません。",
        "--common に共通の台帳のフォルダを渡してください。共通に置く記録は、1 つのファイル = 1 つの記録の種類にしてください。",
        "thth register take <台帳> --common <共通の台帳> --record <種類:番号> --by <名前>",
        "共通の台帳を git に置き、直すたびに thth register keep で控えると、前の版を採っているメディアも材料を作り直せます。"),
    # ---------------------------------------------------------------- 台帳の器 段 5: 消す求め
    "register_erase_invalid": _entry(
        "消す求めの形が合いません（1 行目の（）の語: why は理由が種類の語でない・field と unit は記録に無い欄か単位・"
        "id_field は番号の欄・format はこの形の記録は欄では消せない・request_missing は求めが無い・scope_missing は"
        "範囲の判断が無い・scope_wider は範囲が求めより広い・scope_exists は範囲の判断がもうある・versions は版の"
        "書き方・place と result は場所と結果の語・not_run はまだ実行していない）。何も消していません。",
        "理由は personal・rights・mistake・other のどれかの語だけで書き、中身は書かないでください。範囲は求めと同じ"
        "番号・欄か、それより狭く。版は all・current・控えの名のどれかで。範囲の判断を置いてから run を打ってください。"
        "YAML・頭つきの .md・バイトの枚は、記録ごと（--field なし）で求めてください。",
        "thth register erase status <台帳> <求め>",
        "出すのを今すぐ止めたいなら、消す求めとは別に thth register judge <台帳> <記録> --stop を足すと、封を待たずに"
        "次の材料から外れます。"),
    "register_erase_left": _entry(
        "消し終わりの確かめで、台帳のフォルダの中に、消した範囲の中身か指紋がまだあります（--json の found_in が場所）。"
        "求めの範囲の外（ほかの記録の文・ほかの判断の理由）に同じ中身があるときも、ここで止まります。",
        "found_in の場所を見て、範囲の外の物なら、その記録にも消す求めを置くか、持ち主が "
        "thth register erase done <台帳> <求め> --place <場所> --result kept で「消さない」と決めてください。"
        "器の記録に残っているなら、もう一度 thth register erase run で続きから消せます。",
        "thth register erase status <台帳> <求め>",
        "住所や電話のような値は、判断の理由の文や注に写さないでおくと、消すときに 1 つの求めで済みます。",
        by_detail={
            "list_rebuilt": {
                "fix": "前の版を除いたあとで止まって打ち直したので、消した値の一覧を作り直せず、消し終わりを確かめていません。"
                       "器は値の一覧に頼らない確かめ（印でない行を持つ控えの判断の記録・除いた版の番号・署名の残った封）は"
                       "済ませました。持ち主が erase done --place self_check --result erased を打つと、器がもう一度全部の"
                       "ファイルを読み、残りが無いときだけ受けます。",
                "command": "thth register erase done <台帳> <求め> --place self_check --result erased --by <名前>",
                "better": "消す求めの実行は止めずに終えると、値の一覧で確かめられます。"}}),
    # ---------------------------------------------------------------- 台帳の器 段 8: 共通の台帳と外す範囲
    "register_common_exists": _entry(
        "共通の台帳は持ち主に 1 つだけで、もう控えてあります（1 行目の（）の語が今の共通の台帳の名前）。2 つめは控えていません。",
        "共通に置く物は、今の共通の台帳に足してください。共通の台帳を替えるなら、先に今の控えを "
        "thth register remove <台帳> --by <名前> で外してから --common で控え直してください（台帳のフォルダは消えません）。",
        "thth register kinds <台帳>",
        "メディアごとの物はメディアの台帳に、メディアをまたぐ物（運営者・口座の一覧・対応の記録）だけを共通に置くと、"
        "1 つで足ります。"),
    "register_builtin_kind_exists": _entry(
        "共通の台帳に写す備え付けの種類（correspondence・operator・account）と同じ名前の宣言が、台帳の .thth/kinds/ に"
        "もうあります（1 行目の（）の語がその種類）。上書きせず、何も控えていません。",
        "今の宣言をそのまま使うなら、その種類の宣言のファイルの名前を変えてから（中身はそのまま）もう一度打つか、"
        "--common を外して控えてください。備え付けの形に合わせるなら、今の宣言を除いて打ち直すと写ります。",
        "thth register kinds <台帳>",
        "備え付けの種類の名前は共通の台帳のために取っておくと、メディアの台帳から共通を指すときに迷いません。"),
    "register_leave_confirm": _entry(
        "外す命令の --confirm が、今の見積りと合いません（1 行目の（）の語: changed は見積りのあとに口座や控えが"
        "増減した・所属が変わった・別の見積りを打った・in_progress は途中で止まった外し方があり、その digest で続ける）。"
        "digest は見積りの回ごとに変わります。何も消していません。",
        "--confirm を付けずに打って今の見積り（消える物と残る物）を見てから、出た digest で打ち直してください。"
        "途中で止まったときは --json の digest を付けて同じ命令を打つと、続きから進みます。",
        "thth project leave <メディア> --by <名前>",
        "見積りを見てすぐに打つと、間に口座が増えて digest が変わるのを避けられます。"),
    "register_leave_changed": _entry(
        "途中で止まった外し方を続けようとしたところ、これから消す口座か台帳の控えの所属（プロジェクト・道・共通か）が"
        "見積りのときと違っていました（1 行目の（）の語: account は口座・register は台帳の控え。--json の was と now が前と今）。"
        "その 1 つも残りも消さずに止め、この回の記録は「やめた」として日時つきで残しました。もう消した物はそのままです。",
        "--confirm を付けずにもう一度打って、今の状態で見積りからやり直してください。出た digest で打ち直すと、今の所属で"
        "消す物を決め直します。",
        "thth project leave <メディア> --by <名前>",
        "外し方が途中で止まっている間は、口座や台帳の控えを付け替えないでおくと、見積りのとおりに終わります。"),
    "register_folder_too_wide": _entry(
        "台帳のフォルダが広すぎて歩けません（1 行目の（）の語: root は控えの道が / そのもの・files はファイルが 200,000 を"
        "超えた）。控えの道が書き換えられているか、台帳でない大きなフォルダを控えています。歩くのをやめ、何も書いていません。",
        "thth register remove <台帳> --by <名前> で控えを外し、台帳だけを入れたフォルダを --path にして "
        "thth register add で控え直してください。健康の --all では、その台帳だけ読めないと出して次へ進みます。",
        "thth register check <台帳>",
        "台帳ごとに 1 つのフォルダ（git の repo）にしておくと、外す命令の見積りも健康も速く終わります。"),
    "register_leave_overlap": _entry(
        "台帳のフォルダが、外す命令の消す所（口座の state・管理する repo・THTH の側の台帳の控えと記録・主体の置き場）と"
        "重なっているか、消す所の中に台帳のフォルダらしい物（.thth/ を持つフォルダ）があります（1 行目の（）の語がその所）。消すと台帳のフォルダの中の物まで消えるので、何も消さずに止めました。"
        "控えの道が symlink や .. で THTH の置き場を指すように書き換えてあるときも、ここで止まります。",
        "台帳のフォルダを THTH の置き場（$THTH_ROOT と ~/.config/thth）の外へ移し、"
        "thth register remove <台帳> --by <名前> と thth register add <台帳> --path <新しいフォルダ> ... で控え直してから、"
        "もう一度見積りから打ってください。",
        "thth register check <台帳>",
        "台帳は持ち主の repo に置き、THTH の置き場の外にしておくと、口座やメディアの出入りに巻き込まれません。"),
    "register_leave_incomplete": _entry(
        "外す命令が途中で止まりました（1 行目の（）の語: account は口座の leave が止まった・journal は途中の記録が"
        "読めない）。済んだ所までは記録にあり、消した物はそのままです。",
        "口座の leave の理由（2 行目）を直してから、同じ命令を同じ --confirm でもう一度打つと、続きから進みます"
        "（済んだ口座と控えは二度消しません）。記録が読めないときは $THTH_ROOT/register_leave/ の記録を見てください。",
        "thth project leave <メディア> --by <名前>",
        "先に thth account leave <口座> --by <名前> で 1 本ずつ外しておくと、どこで止まったかが分かりやすくなります。"),
}
