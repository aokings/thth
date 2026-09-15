"""アダプタの境界（差し替え可能な interface。設計 v1 §3.4・設計 v2 §4.2）。

礼儀（静かな時間帯・最短間隔・1 件だけ）は Adapter の外側の `select` が守る。
アダプタは「渡されたものを 1 回投げる」しかしない。

**境界の拡張**（設計 v2 §4.2・masaru 裁定 2026-09-13）。Threads の返信の形を
そのまま共通の形にする——親と根と時刻と誰が、は SNS のスレッドにもメッセンジャーの
会話にも当てはまる。足したのは 3 つ（`medium`・`author_key`・`reply_deadline`）と、
媒体差を core から締め出すための 5 つの口（`capabilities`・`inbox`・`whoami`・
`probe`・`insights` の `available`）だけ。**core は媒体名を知らない。**
"""
from __future__ import annotations

import dataclasses
import hashlib


@dataclasses.dataclass
class Post:
    text: str                       # 媒体節の本文（検査済み）
    reply_to: str | None = None     # post_id | None
    link: str | None = None
    # Threads の topic_tag（設計 §2.2・§4.1・masaru 裁定 2026-09-09。全アカウントで
    # 使う）。1〜50 字・`.`・`&` 不可・1 投稿に 1 つだけ（検査済みのものだけを渡す
    # こと・queuefile.normalize_topic()/topic_error() で検査する）。
    #
    # **topic を持たない媒体は黙って無視する**（設計 v2 §4.2）。1 つの queue
    # ファイルを Threads と Bluesky の 2 account が拾う形（設計 v1 §8-16）を
    # 壊さないため——ここで loud に断ると、媒体を足した瞬間に既存の queue が
    # 全部止まる。**使わない媒体が受け取っても何も起きない**が正しい。
    topic: str | None = None
    # --- 承認を通る書き込みの口（設計 v2 §4.3・v2.1-B・2026-09-14） ---
    # 場所（Threads の `location_id`）。queue の front-matter `location:`（人が
    # 書く語）と `location_id:`（`thth location search` が引いた id を人が書く）
    # から `core._throw_chosen()` が渡す。**承認の指紋に入る**（`approval.
    # compute_approved_sha()`）——承認後に変えれば `approval_stale`。
    # 持たない媒体は黙って無視する（`topic` と同じ理由）。
    location_id: str | None = None
    # Instagram のストーリーズにも出す（Threads の `crossreshare_to_ig`・**L2**
    # create-posts/share-to-ig-stories）。front-matter `share_to_instagram: true`。
    # 台帳に `instagram_linked: true` が無ければ lint が断る。指紋に入る。
    share_to_instagram: bool = False


@dataclasses.dataclass
class PublishResult:
    post_id: str | None
    url: str | None
    ts: str                         # JST ISO
    error: str | None = None        # 伏字済み
    # 失敗の種類（設計 §3.5・T1 検収 2026-09-09 で追加）。core はこれだけを見て
    # inflight を消すか残すかを決める（HTTP の状態番号は core が解釈しない・§3.4）。
    #   "none"              成功
    #   "container"         コンテナ作成の失敗（全部）→ 出ていない
    #   "publish_definite"  公開が HTTP 4xx → 出ていない
    #   "publish_ambiguous" 公開が timeout・接続断・5xx・200 だが id 無し → 分からない
    #   "publish_vetoed"    公開要求の直前の関門で止めた → **出ていない**
    #   "permission"        この投稿の任意項目（場所・Instagram 共有）に要る権限が
    #                       トークンに乗っていない → **出ていない**（コンテナ作成の
    #                       手前、または作成が権限不足で断られた）。core は inflight
    #                       を消し、`thth auth` のやり直しを促して rc=2（v2.1-B）
    failure: str = "none"


class AdapterError(RuntimeError):
    """媒体の呼び出しが成立しなかった（メッセージは `redact()` 済みであること）。

    **`RuntimeError` を継承する**——採取側は「例外なら記録を書かない」で成功と
    失敗を分けているので、既存の `except Exception` の網にそのまま入る。
    """


class UnknownMedium(AdapterError):
    """台帳の `media` を知らない（`thth/adapters/__init__.py` が投げる・T-B0）。"""


class PermissionMissing(AdapterError):
    """**その口に要る権限がトークンに乗っていない**（設計 v2 §4.3・受け入れ (c)・v2.1-A/B）。

    `thth doctor` と同じ判定（`threads._is_permission_error()`——Meta の
    `error.message` に `permission`、または権限系 code）で上げる。**黙って 0 件
    にしない・500 を黙って返さない**——CLI は「`<permission>` がトークンに
    乗っていません。`thth auth <account>` をやり直してください」で rc=2、
    `collect` は **`errors` に積まず** `inbox_state: permission_missing` として
    残して続行する（投稿は止めない・board に `inbox=権限なし`・本番 P1
    2026-09-14。以前は `errors` に 1 行積んでいたが、5 権限のトークンでは
    毎 run 「採取は完全ではありません」になり本物の失敗が埋もれた）。

    `permission` は権限の綴り（`threads_keyword_search` 等）。**媒体の語**だが、
    読む側（CLI・collect）は文言に流すだけで分岐はしない。

    判定は `.token` の `scopes`（一覧があるとき）→ `/debug_token`（Threads・
    実体で 1 回だけ）→ 媒体の応答の権限系エラー（4xx・または 500 で権限が無いと
    分かっているとき）の順。**不明なら叩く**（判らないことを「無い」にしない）。
    """

    def __init__(self, permission: str, detail: str = ""):
        self.permission = permission
        self.detail = detail
        super().__init__(not_granted_message(permission, detail))


def not_granted_message(permission: str, detail: str = "") -> str:
    """「乗っていません」の 1 行（doctor の `NOT_GRANTED` と同じ意味・文言は口向け）。"""
    tail = f"（{detail}）" if detail else ""
    return (f"`{permission}` がトークンに乗っていません{tail}。"
            "`thth auth <account>` をやり直してください")


# `Adapter.capabilities()` が返しうる語（設計 v2 §4.2）。**ここに無い語を返さない**
# ——読み手（`select`・`collect`・`core`）はこの一覧だけを見て分岐する。
KNOWN_CAPABILITIES = frozenset({
    "topic",          # 投稿に語（Threads の topic_tag）を付けられる
    "link_preview",   # 本文の URL がリンクとして展開される
    "views",          # 投稿ごとの表示回数が取れる
    "quota",          # 残量を問える
    "inbox",          # 利用者から始まった会話が取れる（**WhatsApp の芽**）
    "refresh",        # トークンを更新できる
    # そのアカウントが**実際に出している**直近の投稿を引ける（`Adapter.recent_posts()`）。
    # **THTH を通していない投稿を含む**のがこの口の目的（`thth posts`・`thth account`
    # の「外で出したもの」）。持たない媒体には**聞きに行かない**——
    # `account_report.fetch_posts()` がこの語で塞ぐ（F2・2026-09-13）。
    "recent_posts",
    # アカウント単位の日次（`account_insights()`）が取れる。**Threads だけ**
    # （T0 の残件・2026-09-13）。以前は `collect._collect_account_daily()` が
    # 媒体を問わず呼んでいたので、持たない媒体では毎回 `errors` に
    # `account_insights: …` が積まれ、**採取が「1 本でも失敗したか」で
    # 非ゼロ終了し続けた**——「無い」を「失敗」と呼ばないための語。
    "account_insights",
    # --- Threads の 11 権限を使う読み取りの口（設計 v2 §4.3・v2.1-A・2026-09-14）
    # 語で公開投稿を検索できる（`keyword_search()`・`threads_keyword_search`）。
    "keyword_search",
    # 自分への言及を引ける（`mentions()`・`threads_manage_mentions`）。Threads は
    # これを `inbox` にも流す（言及＝利用者から始まった会話）。
    "mentions",
    # 公開プロフィールを引ける（`profile_lookup()`・`threads_profile_discovery`）。
    "profile_lookup",
})


def author_key(medium: str, username: str | None) -> str | None:
    """媒体と投稿者から決まる**非可逆**の識別子（設計 v2 §4.2・§2）。

    **泉に出るのはこれで、`username` は出ない**（§2「落ちないもの」）。偏りを
    数える（同じ人が何度も返しているか）ためだけに要るので、**戻せない形**で
    十分。媒体名を混ぜるのは、別媒体の同名アカウントを同一人物として数えない
    ため（§2.1「媒体をまたいで比較しない」と同じ筋）。

    `username` が無ければ `None`——**空文字を鍵にしない**（誰も彼もが同じ鍵に
    なる）。
    """
    if not username:
        return None
    digest = hashlib.sha256(f"{medium}\n{username}".encode("utf-8")).hexdigest()
    return digest[:16]


@dataclasses.dataclass
class Message:
    """会話の 1 行（設計 v2 §4.2。旧 `Reply` の改名）。

    **改名した理由**: 同じ形が「返信」でも「利用者から届いた問い合わせ」でも
    使える（親と根と時刻と誰が、は両方にある）。`Reply` は別名として残すが、
    新しく書くものは `Message` を使うこと。
    """
    message_id: str
    username: str
    text: str
    timestamp: str
    replied_to: str | None
    root_post: str | None
    # --- 設計 v2 §4.2 で足した 3 つ ---
    medium: str | None = None
    # 媒体と投稿者から決まる非可逆の識別子（`author_key()` で作る）。
    author_key: str | None = None
    # **WhatsApp の芽**（設計 v2 §4.1・§4.2）。利用者の最後のメッセージから
    # 24 時間の会話窓を過ぎると自由文で返せない。SNS では `None`。
    reply_deadline: str | None = None


# 旧名（設計 v2 §4.2「`Reply = Message` の別名を残す」）。
Reply = Message


def metrics_of(result) -> tuple:
    """`insights()` の戻り `{"metrics": {...}, "available": [...]}` を開く（設計 v2 §4.2）。

    **`available` は「その媒体が持っている指標の名前」**で、`metrics` に無い
    ものは「持っているのに取れなかった」——`available` に無いものは
    「**そもそも媒体に無い**」。この 2 つを混ぜないためだけに在る欄。

    **旧い形（指標の辞書そのもの）は受けない**（F3・2026-09-13・規約 5）。以前は
    鍵が揃っていなければ辞書全体を指標とみなし、`available` を `None`（＝判らない）
    にしていた。**その `None` が黙って通る**のが穴で、

    - `collect` は `available is not None` のときだけ「媒体に無い指標」を `null`
      で埋める。旧い形の戻りは埋めないので、**「そもそも媒体に無い」と「今回
      取れなかった」が同じ「欄が無い」になった**（`comparable_views()` が理由を
      言い分けられない）。
    - 鍵を綴り間違えた新しいアダプタ（`metric` / `availables`）は、**戻り全体が
      指標として通り**、`{"metrics": {...}}` という名前の指標が 1 つある行が
      台帳に入る。

    どちらも**黙って間違える**形なので、ここで断る。`AdapterError`（`RuntimeError`
    の子）なので、採取側の「例外なら記録を書かない」にそのまま乗る——1 本の
    アダプタの不備で**投稿は止まらず**、`errors` に理由が残る。
    """
    if (isinstance(result, dict) and isinstance(result.get("metrics"), dict)
            and isinstance(result.get("available"), (list, tuple, set, frozenset))):
        return dict(result["metrics"]), list(result["available"])
    見えたもの = (f"dict（鍵: {sorted(result)[:8]}）" if isinstance(result, dict)
                 else type(result).__name__)
    raise AdapterError(
        'insights() の戻りは {"metrics": {…}, "available": […]} です'
        f"（受け取ったのは {見えたもの}）。**旧い形（指標の辞書そのもの）は"
        "受けません**——`available` が無いと「媒体に無い指標」と「今回取れなかった"
        "指標」を言い分けられません（設計 v2 §4.2・F3）")


def missing_for_post(adapter, token, post: "Post") -> list:
    """`adapter` がこの `Post` に要求する権限のうち、`.token` に無いもの。

    **`Adapter` を継がない差し替え（テストの偽アダプタ）でも落ちない**——口を
    持たないものは「要求する権限が無い」として空を返す。
    """
    needed_fn = getattr(adapter, "permissions_for_post", None)
    missing_fn = getattr(adapter, "missing_permissions", None)
    if not callable(needed_fn) or not callable(missing_fn):
        return []
    return list(missing_fn(token, needed_fn(post)))


class Adapter:
    """媒体ごとの実装（`thth/adapters/__init__.py` の `REGISTRY` に登録する）。

    **`CAPABILITIES` はクラス属性**（設計 v2 §4.2・受け入れ 6）。台帳の `media`
    から `REGISTRY` を引くだけで判定できないと、`select` が「トピック検査を
    するかどうか」を決めるためにトークンを読んでアダプタを組み立てる羽目になる。
    """

    # 設計 v2 §4.2 の部分集合（`KNOWN_CAPABILITIES` の語だけを使う）。
    CAPABILITIES: frozenset = frozenset()

    # **`.token` に何が入っていればトークンが在ると言えるか**（設計 v2 §4.2
    # 「認可とトークン」・T1/T2 の配線 2026-09-13）。`doctor.diagnose()` は
    # 以前 `token.get("access_token")` で早期 return していたので、
    # **Bluesky（`identifier` と `app_password`）は正しく認可されていても
    # 「トークンが無い」と言われた。** 媒体ごとの鍵の名前は媒体の知識なので
    # ここに置く。**値は見ない**（在るかどうかだけ）。
    TOKEN_KEYS: tuple = ("access_token",)

    # トークンが無いときに人へ示す次の一手（`doctor`）。Bluesky は
    # `thth token set` ではなく `thth auth`（App Password を対話で受ける）。
    TOKEN_SETUP_HINT: str = "thth token set"

    # `thth auth` に Meta の `app.env`（app id と secret）が要るか。**Threads だけ**
    # （OAuth の往復をこちらが組み立てるので）。Bluesky は App Password を対話で
    # 受けるだけ、Mastodon はそもそも `thth auth` を使わない。`doctor` が
    # 「`thth auth` を使うなら先に `thth app set`」と**要らない一手を勧めない**
    # ようにするための印（T3・2026-09-13）。
    AUTH_NEEDS_APP_ENV: bool = False

    # **`Post` の任意項目に要る権限**（設計 v2 §4.3・v2.1-B）。`Post` の属性名 →
    # その媒体で要る権限名。core は `missing_permissions()` を通してだけ見る
    # （媒体名も権限名も core には書かない）。持たない媒体は空のまま。
    OPTION_PERMISSIONS: dict = {}
    # 投稿の取り下げ（`delete_post()`）に要る権限。`None` は「この媒体の取り下げは
    # 未対応」（`thth retract` が rc=2 で断る）。
    DELETE_PERMISSION: str | None = None

    # `.token` に `expires_in` を書かず `no_expiry: true` を立てる媒体
    # （Bluesky の App Password・Mastodon の access token・設計 v2 §4.2）。
    # `oauth.token_age_and_remaining()` がこの印を見て `remaining_days` を
    # `None`（＝**判らない、ではなく期限を持たない**）にする。
    TOKEN_NO_EXPIRY: bool = False

    @classmethod
    def has_token(cls, token) -> bool:
        """`.token` の中身が、この媒体にとって「在る」と言える形か。

        **値は読まない**——在るかどうかだけを見る（値を触る口を増やさない）。
        """
        token = token or {}
        return all(token.get(key) for key in cls.TOKEN_KEYS)

    @classmethod
    def capabilities(cls) -> set:
        """この媒体にできることの集合。**実体を作らずにも引ける**（classmethod）。"""
        return set(cls.CAPABILITIES)

    @classmethod
    def count_text(cls, text: str) -> int:
        """**この媒体の数え方**で本文の長さを数える（`queuefile.limit_for()` と対）。

        **実体を作らずに引ける**（classmethod）——`send`・`select`・`lint` は
        トークンを持たずに文字数を数えるため。既定は `queuefile.char_count()`
        （Threads の数え方: 絵文字は UTF-8 バイト数・設計 §2.2）で、**違う
        数え方を持つ媒体だけが上書きする**（Bluesky は grapheme の近似）。

        以前は 3 か所とも `queuefile.char_count()` を直に呼んでいたので、
        **Bluesky の 300 を Threads の数え方で測っていた**——絵文字の多い本文が
        「超えている」と断られていた（引継ぎ 2026-09-15 §3-D）。
        """
        from .. import queuefile
        return queuefile.char_count(text)

    @classmethod
    def permissions_for_post(cls, post: "Post") -> list:
        """この `Post` の任意項目が、この媒体で要求する権限の一覧（無ければ空）。"""
        out = []
        for attr, permission in cls.OPTION_PERMISSIONS.items():
            if getattr(post, attr, None):
                out.append(permission)
        return out

    @classmethod
    def missing_permissions(cls, token, needed) -> list:
        """`needed` のうち `.token` の `scopes` に**無い**もの（doctor と同じ物差し）。

        `.token` の `scopes` が一覧でなければ（`thth token set` の管理画面発行・
        `scopes_source: "unknown"`）**判らない**ので空を返す——止めない。その
        場合は媒体の応答の権限系エラーで判る（`PermissionMissing`・
        `PublishResult.failure == "permission"`）。**嘘の一覧で断らない**。
        """
        scopes = (token or {}).get("scopes")
        if not isinstance(scopes, list):
            return []
        return [p for p in needed if p not in scopes]

    def location_search(self, query: str, *, limit: int = 5) -> list:
        """場所を検索して `[{"id", "name", …}, …]`（読み取り・設計 v2 §4.3）。

        持たない媒体は `AdapterError`（`thth location search` が rc=2 で断る）。
        """
        raise AdapterError(
            f"{type(self).__name__}: この媒体に場所の検索はありません")

    def delete_post(self, post_id: str) -> dict:
        """公開済みの投稿 1 本を取り下げる（**承認の二段を通った後にだけ呼ばれる**）。

        `DELETE_PERMISSION` が `None` の媒体は未対応——`thth retract` は
        **このメソッドに来る前**に rc=2 で断る（Bluesky / Mastodon は v2.2）。
        """
        raise AdapterError(
            f"{type(self).__name__}: この媒体の取り下げは未対応です")

    @classmethod
    def from_account(cls, account_cfg: dict, token: dict):
        """台帳とトークンから実体を作る（`make_adapter()` が呼ぶ）。

        **媒体ごとの台帳項目・環境変数の読み方はアダプタの中に閉じる**——core は
        `media` の名前すら見ない（設計 v2 §4.2「台帳と登録」）。
        """
        raise NotImplementedError

    def publish(self, post: Post, *, dry_run: bool, on_container_created=None,
                before_publish=None) -> PublishResult:
        """`before_publish` は**実際の公開要求の直前**に呼ばれる最後の関門。

        文字列を返せば公開しない（`failure="publish_vetoed"`）。**container を
        作ったあと・待機のあとに呼ぶ**——待っているあいだに継続期限を越える
        ことがある（独立検収 2026-09-11・P1-3）。
        """
        raise NotImplementedError

    def conversation(self, post_id: str, *, since: str | None = None) -> list:
        """会話全体（**全階層**）。`replies`（上位 1 階層）から改名・2026-09-12。

        各行に `medium` と `author_key` を添えること（設計 v2 §4.2）。
        **返信の台帳（ndjson）の既存の鍵は変えない——足すだけ。**
        """
        raise NotImplementedError

    def inbox(self, *, since: str | None = None) -> list:
        """利用者から始まった会話（`root_post` 無し）。**WhatsApp の芽**。

        **既定は空**——持たない媒体は何も返さない（`capabilities()` に `inbox`
        が無ければ `collect` は呼ばない）。
        """
        return []

    def recent_posts(self, *, limit: int = 25) -> list:
        """そのアカウントが**実際に出している**直近の投稿（新しい順）。

        **THTH を通していない投稿も入る**のがこの口の目的（`thth posts`・
        `thth account` の「外で出したもの」）。読み取りだけ——投稿・返信・削除は
        呼ばない。

        1 行の形（**媒体差はここで吸収する**。core も `account_report` も媒体名を
        知らない）:

          `post_id`   台帳の `post_id` と突き合わせる鍵（`PublishResult.post_id`
                      と同じ綴り。Threads は数字・Bluesky は AT URI）
          `timestamp` 媒体が返す時刻の綴りそのまま（**揃えない**——揃えたふりを
                      すると、揃っていないことが見えなくなる）
          `url`       人が開ける URL（無ければ `None`）
          `text`      本文（**切り詰めない**）
          `topic`     語（Threads の `topic_tag`）。持たない媒体は `None`

        **「取れなかった」を「取れて 0 件」にしない**——引けなければ
        `AdapterError` を上げる（`_rows()`・`_get_json()` と同じ規律）。

        以前この口は `account_report.fetch_posts()` の中にあり、`graph.threads.net`
        の URL を直に組み立てていた（**設計 v2 §4.2 が数えた「Threads 固有になって
        いる 6 箇所」に入っていなかった 7 つめ**）。境界へ移したので、**媒体ごとの
        宛先は媒体が決める**——他媒体の `.token` が Meta のサーバへ行く筋が、
        分岐ではなく構造で無くなる。
        """
        raise AdapterError(
            f"{type(self).__name__}: 直近の投稿を引く口がありません"
            f"（capabilities に recent_posts がある媒体だけが答えます）")

    def insights(self, post_id: str) -> dict:
        """`{"metrics": {...}, "available": [...]}`（設計 v2 §4.2）。

        `available` には**その媒体が持っている指標の名前**を並べる。views が
        無い媒体は `views` を入れない——実測の行は `views: null` になり、
        `account_report.comparable_views()` が「媒体に views が無い」の理由で
        **除外して数える**（捨てない・設計 v1 §3.2.2）。
        """
        raise NotImplementedError

    def whoami(self) -> dict:
        """`{"user_id", "username"}`（`thth token set` と `thth doctor` の検証）。

        失敗したら `AdapterError`（メッセージは伏字済み）。
        """
        raise NotImplementedError

    def probe(self, *, get=None) -> list:
        """読み取りだけで能力を測る（`thth doctor`）。`[{"name", "ok", "detail"}, …]`。

        `ok` は 3 値（`True` ○ / `False` × / `None` －「試せていない」）。

        `get` は**取得口の差し替え**（省略可）。`thth/doctor.py` は自分の
        `_get(base_url, path, params, token)` を渡す——doctor は「読み取りしか
        呼ばない」ことを自分の source に対する検査で担保しており（`tests/
        test_doctor.py::test_doctor_は書き込みの口を持たない`）、その唯一の
        HTTP 呼び出しを doctor 側に残すため。**媒体側は使わなくてよい。**
        """
        return []

    def quota(self) -> dict | None:
        return None

    def refresh_token(self, token: dict) -> dict:
        raise NotImplementedError
