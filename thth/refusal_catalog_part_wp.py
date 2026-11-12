"""断りの目録の項目（WordPress の口・5.16.0）。thth/refusal_catalog.py が読む。

WordPress（自分で置く型）へ記事を出す口（`thth/adapters/wordpress.py`・`thth/wp_html.py`・`hatena_entry.target`・
`thth principal add|auth`）が出す符丁。REST が閉じている・Authorization が落とされる・https でない・
セキュリティの部品が止めている、を分けて返す（設計の直し 8）。

`since` はまとめる者が版（5.16.0）を上げるまで今の版（`thth/VERSION` を越えない柵）。
"""
from __future__ import annotations

_C = ("cli",)
_CM = ("cli", "mcp")
_SINCE = "5.14.5"
_AUTH = "thth principal auth <主体> --force --by <名前>"


def _entry(why, fix, command, better, doors=_C, kind="refusal"):
    return {"kind": kind, "why": why, "fix": fix, "command": command, "better": better, "doors": doors,
            "since": _SINCE}


ENTRIES = {
    # ------------------------------------------------------------------ 主体の台帳と鍵
    "principal_invalid": _entry(
        "主体の台帳か命令の欄の組み合わせが、その主体の種類（はてな・WordPress）に合いません。",
        "主体の種類に合う欄で打ち直してください（WordPress は --kind wordpress --wp-user・はてなは --hatena-id）。",
        "thth principal show <主体>",
        "thth principal show で台帳の kind を見てから打つと、種類の取り違えが起きなくなります。"),
    "wp_user_invalid": _entry(
        "wp_user（WordPress のログイン名）が空か、使えない字が入っています。",
        "WordPress の「ユーザー」の画面に出るログイン名を --wp-user に入れて打ち直してください。",
        None,
        "記事を書くだけの「編集者」の利用者を作り、そのログイン名にすると、鍵が漏れたときの害が小さくなります。"),
    "wp_user_mismatch": _entry(
        "鍵で読んだ利用者が、主体の台帳の wp_user と違います（別の利用者のアプリケーションパスワードを入れた）。",
        "主体の wp_user の利用者でアプリケーションパスワードを作り直し、入れ替えてください。",
        _AUTH,
        "利用者ごとに主体を分けておくと、どの鍵で出したかが台帳から辿れます。"),
    "site_url_invalid": _entry(
        "主体のブログの行の site_url が、https://<blog_id> の形（手元の試しの localhost だけ http）になっていません。",
        "site_url を https://<blog_id> の形に直すか、欄を消してください（無ければ https://<blog_id> を使います）。",
        "thth principal show <主体>",
        "サイトのアドレスを https にしておくと、鍵が平文で流れる道がそもそも無くなります。"),
    # ------------------------------------------------------------------ 原稿（出し先ごとの形）
    "send_as_invalid": _entry(
        "send_as: の値が publish・draft のどれでもありません。",
        "send_as: は publish（公開・省略時）か draft（下書き）にしてください。",
        None,
        "下書きで出して WordPress の画面で見てから公開すると、崩れに先に気づけます。", _CM),
    "send_as_unsupported": _entry(
        "この出し先は、その公開の状態をまだ受けません（はてなブログは公開だけ・WordPress の予約は後の版）。",
        "send_as: を外すか、その出し先が受ける状態（WordPress は publish か draft）にしてください。",
        None,
        "予約したい時刻は publish_at に書けば、timer がその時刻に出します。", _CM),
    "send_as_create_only": _entry(
        "send_as: は作るとき（op: create）だけの欄です。直すときは、出したときの状態を変えません。",
        "op: update・delete の原稿からは send_as: を外してください。",
        None,
        "状態を替えたいときは WordPress の画面で替えると、承認した本文と別に扱えます。", _CM),
    "op_unsupported_for_media": _entry(
        "この出し先は、その操作をまだ受けません（WordPress の消す・ごみ箱へは後の版）。",
        "WordPress の記事を消すときは、WordPress の画面からごみ箱へ移してください。",
        None,
        "消す前に下書きへ戻すと、URL を残したまま読者から隠せます。", _CM),
    "wp_markup_unsupported": _entry(
        "記事に、WordPress へ送る変換が受けない記法があります（受けるのは段落・見出し・強調・リンク・箇条書き・コード）。",
        "（）に書いた記法を、受ける記法に書き換えてから commit してください（引用は段落に・表は箇条書きに など）。",
        None,
        "見出しと箇条書きで組むと、WordPress のテーマが変わっても崩れにくい記事になります。", _CM),
    "wp_link_scheme_unsupported": _entry(
        "リンクの行き先が http・https・mailto・相対のどれでもありません（javascript: などは送りません）。",
        "リンクの行き先を https の URL か、記事の中の相対のパスに直してください。",
        None,
        "https のリンクにそろえると、読者の端末の警告が出なくなります。", _CM),
    "wp_image_unsupported": _entry(
        "記事が手元の画像を参照しています。WordPress への画像の上げ（メディア）は後の版です。",
        "画像を WordPress のメディアに手で上げ、記事から画像の参照を外してから commit してください。",
        None,
        "記事が通ったあと WordPress の画面で画像を足すと、承認した本文と別に扱えます。", _CM),
    # ------------------------------------------------------------------ WordPress の答え
    "wordpress_rest_unavailable": _entry(
        "サイトの REST の入口（/wp-json/）が開いていません（パーマリンクが「基本」・部品や設定で REST を閉じている）。",
        "WordPress の「設定」→「パーマリンク」を「投稿名」などにし、REST を閉じる部品を止めてから打ち直してください。",
        "thth doctor <口座>",
        "ブラウザで <サイト>/wp-json/ を開いて JSON が出れば、THTH からも届きます。"),
    "wordpress_auth_header_dropped": _entry(
        "鍵を送りましたが、WordPress に届く前に Authorization の見出しが落とされています（サーバの設定）。",
        "Apache なら .htaccess に「SetEnvIf Authorization \"(.*)\" HTTP_AUTHORIZATION=$1」を足し、"
        "ホスティングの管理画面で Authorization を通す設定にしてから打ち直してください。",
        "thth doctor <口座>",
        "通ったあと thth doctor <口座> で users/me が読めれば、記事の公開も同じ道で通ります。"),
    "wordpress_auth_rejected": _entry(
        "WordPress が鍵を受け付けませんでした（アプリケーションパスワードが違う・取り消された・利用者名が違う）。",
        "WordPress の「プロフィール」でアプリケーションパスワードを作り直し、入れ替えてください。",
        _AUTH,
        "鍵の名前に THTH と付けておくと、取り消すときにどれか分かります。"),
    "wordpress_app_passwords_disabled": _entry(
        "このサイトか利用者で、アプリケーションパスワードが無効になっています（部品や設定で止めている）。",
        "サイトの管理者に、この利用者のアプリケーションパスワードを有効にしてもらってから入れてください。",
        _AUTH,
        "有効にするのは記事を書く利用者だけにすると、ほかの利用者の鍵は増えません。"),
    "wordpress_permission_missing": _entry(
        "鍵の利用者に、記事を作る・直す・公開する権限がありません（役割が投稿者より下）。",
        "その利用者の役割を「編集者」にするか、編集者の利用者で鍵を作り直して入れ替えてください。",
        _AUTH,
        "管理者ではなく編集者の鍵にすると、記事のほかの設定は鍵で変えられなくなります。"),
    "wordpress_blocked_by_security": _entry(
        "サイトの前のセキュリティの部品か WAF が、REST の書き込みを止めています（WordPress の答えではない断り）。",
        "セキュリティの部品の設定で、この利用者の REST（/wp-json/wp/v2/posts）を許してから打ち直してください。",
        "thth doctor <口座>",
        "許すのは REST の記事の入口だけにすると、ほかの守りは今のまま保てます。"),
    "wordpress_https_required": _entry(
        "サイトの入口が http です。鍵を平文では送りません。",
        "サイトを https にし、主体の site_url を https://<blog_id> にしてから打ち直してください。",
        "thth principal show <主体>",
        "https にすると、鍵も記事も途中で読まれなくなります。"),
    "wordpress_term_unknown": _entry(
        "原稿のカテゴリかタグ（# で始まる名前）が WordPress にまだありません（勝手には作りません）。",
        "WordPress の「投稿」→「カテゴリー」「タグ」で同じ名前を作るか、原稿の categories: を今ある名前に直してください。",
        None,
        "カテゴリは今ある 2〜3 個に絞ると、サイトの中で記事が辿られやすくなります。"),
    "wordpress_term_lookup_failed": _entry(
        "カテゴリとタグの名前を WordPress に確かめられなかったので、記事を送っていません。",
        "少し置いてからもう一度出してください（timer が次の回に出し直します）。",
        "thth doctor <口座>",
        "thth doctor <口座> が通るなら、次の回には名前も確かめられます。"),
    "wordpress_status_unexpected": _entry(
        "記事は WordPress に届きましたが、頼んだ状態（公開・下書き）と違う状態になりました（権限が足りず「レビュー待ち」など）。",
        "WordPress の画面で記事の状態を確かめ、thth inflight <口座> resolve で出たかどうかを決めてください。",
        "thth inflight <口座> resolve",
        "鍵の利用者を編集者にすると、頼んだ状態のまま出ます。"),
    "wordpress_transport_failed": _entry(
        "WordPress まで届いたか分からないまま、つながりが切れました（出し直しはしません）。",
        "WordPress の画面で記事が出たか確かめ、thth inflight <口座> resolve で決めてください。",
        "thth inflight <口座> resolve",
        "サイトの応答が遅いときは、口座の timeout を延ばすと切れにくくなります。"),
}
