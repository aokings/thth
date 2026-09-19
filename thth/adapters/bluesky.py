"""BlueskyAdapter（AT Protocol・設計 v2 §4.2「境界の拡張」・設計 v1 §3.4・§3.5）。

Threads と同じ境界（`Post` / `PublishResult` / 三分類の `failure`）に乗せる。媒体差は
**全部この中に閉じる**——core は「Bluesky にはコンテナが無い」ことも「リンクは facets を
付けないとリンクにならない」ことも知らない。

証拠段階（作法 §7）:

- **L2**（一次資料の lexicon を 2026-09-13 に読解）: `com.atproto.server.createSession`
  （入力 `identifier`/`password`、出力 `accessJwt`・`refreshJwt`・`handle`・`did`）、
  `com.atproto.repo.createRecord`（入力 `repo`・`collection`・`record`、出力 `uri`・`cid`）、
  `app.bsky.feed.post`（`text` は maxGraphemes 300・maxLength 3000、`createdAt` 必須、
  `facets`、`reply` は `#replyRef{root,parent}` でどちらも `com.atproto.repo.strongRef`）、
  `app.bsky.richtext.facet`（`index` は `#byteSlice{byteStart,byteEnd}`——
  **「UTF-8 で符号化した本文のバイトを数える。start は含み end は含まない」と明記**、
  `features` に `#link{uri}`）、`app.bsky.feed.getPostThread`（`uri`・`depth`・
  `parentHeight`、出力は `#threadViewPost` / `#notFoundPost` / `#blockedPost` の union）、
  `app.bsky.feed.getPosts`（`uris` は最大 25・出力 `posts` は `#postView`）、
  `app.bsky.actor.getProfile`（`actor`）。`#postView` は `likeCount`・`replyCount`・
  `repostCount`・`quoteCount` を持ち、**views に当たる項目は無い**。`#postView` には
  `bookmarkCount` もあるが、**このアダプタは取っていない**（監査 2・2026-09-13）。
- **L3**（記憶・慣行。一次資料で裏を取っていない）: XRPC の口が `<service>/xrpc/<nsid>` で
  あること、`bsky.social`（PDS）が `app.bsky.*` の問い合わせを AppView へ中継すること、
  投稿の人向け URL が `https://bsky.app/profile/<handle>/post/<rkey>` であること、
  `createSession` の呼び出し上限（30/5 分・300/日）、App Password の形
  （`xxxx-xxxx-xxxx-xxxx`）、本文から URL を拾う正規表現の切り出し方（末尾の句読点を
  どこまで落とすか）、`count()` の grapheme 近似（**厳密な grapheme 分割ではない**）、
  `refreshSession` は未実装（App Password で毎回 `createSession`）。

秘密（App Password・`accessJwt`）は**例外文にもログにも出さない**（`scrub()` を通す）。
"""
from __future__ import annotations

import collections
import datetime
import getpass
import json
import re
import unicodedata
import urllib.error
import urllib.parse
import urllib.request

from .. import httpsafe
from .. import jst
from .. import tags as tags_mod
from .. import redact as redact_mod
from . import base

MEDIUM = "bluesky"
DEFAULT_SERVICE = "https://bsky.social"
DEFAULT_TIMEOUT_SECONDS = 10.0
# `getPostThread` の `depth`（lexicon の既定は 6・最大 1000・**L2**）。既定の 6 では
# 深い枝が黙って切れる——**「取れて 0 件」と区別できない**ので広めに取る。
DEFAULT_THREAD_DEPTH = 100
# 本文の上限（**L2**: `app.bsky.feed.post.text` は maxGraphemes 300）。
CHAR_LIMIT = 300
POST_COLLECTION = "app.bsky.feed.post"
# この媒体が**持ちうる**指標（`insights()` の `available`）。**views は無い**（**L2**）。
AVAILABLE_METRICS = ("likes", "replies", "reposts", "quotes")
_METRIC_FIELDS = (("likes", "likeCount"), ("replies", "replyCount"),
                  ("reposts", "repostCount"), ("quotes", "quoteCount"))

ZWJ = "‍"


# --- 秘密の伏字 ------------------------------------------------------------
# `thth/redact.py` は Threads の秘密（`access_token=`・`client_secret=`・`code=`・
# `Authorization:`）を伏せる。Bluesky の秘密は名前が違うので、ここで足してから
# `redact()` に渡す。**値が判っているときは値そのものも置き換える**——JSON の
# 本文がそのまま例外文に載る経路が将来できても漏れないように、名前と値の両方から塞ぐ。
_BLUESKY_KEY_RE = re.compile(
    r'((?:app_?password|password|accessJwt|refreshJwt)"?\s*[:=]\s*"?)([^\s&"\',}]+)',
    re.IGNORECASE)
_BEARER_RE = re.compile(r'(Bearer\s+)(\S+)')


def scrub(text, *secrets) -> str | None:
    """秘密を伏せた文字列を返す。`secrets` に判っている値を渡すと値ごと置き換える。"""
    if text is None:
        return None
    out = str(text)
    for secret in secrets:
        if secret and isinstance(secret, str) and len(secret) >= 4:
            out = out.replace(secret, "***")
    out = _BLUESKY_KEY_RE.sub(lambda m: m.group(1) + "***", out)
    out = _BEARER_RE.sub(lambda m: m.group(1) + "***", out)
    return redact_mod.redact(out)


# --- facets（本文中の URL を UTF-8 のバイト位置で指す） --------------------
# **L2**: `app.bsky.richtext.facet#byteSlice` は「UTF-8 で符号化した本文のバイト
# 位置。start は含み end は含まない」。**文字数でも UTF-16 でもない**——日本語の
# 本文だと文字位置とバイト位置が必ずずれるので、ここを間違えるとリンクが本文の
# 別の場所に貼られる（黙って間違える形の典型）。
_URL_RE = re.compile("https?://[^\\s　]+")
# URL の末尾に食い込みがちな約物。**L3**（どこまで落とすかは慣行で、仕様ではない）。
_URL_TRAILING = "。、．，！？!?…‥.,;:'\"”’〉》」』】）)]}»>"
_CLOSERS = {")": "(", "）": "（", "]": "[", "}": "{", "】": "【",
            "」": "「", "』": "『", "》": "《", "〉": "〈"}


def _trim_url(url: str) -> str:
    """URL の末尾に付いてきた約物を落とす。**対応する開き括弧が本体にあれば残す**
    （`…/Foo_(bar)` のような URL の `)` を落とすと別の場所を指す）。"""
    while url and url[-1] in _URL_TRAILING:
        opener = _CLOSERS.get(url[-1])
        if opener and opener in url[:-1]:
            break
        url = url[:-1]
    return url


def build_facets(text: str, *, topic: str | None = None,
                 include_tags: bool = False) -> list:
    """本文から URL を拾い、`app.bsky.richtext.facet#link` の配列を作る。

    **付けなければリンクにならない**（Bluesky は本文の URL を自動でリンクにしない）。
    位置は**UTF-8 のバイト**（文字数ではない）。
    """
    facets = []
    for m in _URL_RE.finditer(text):
        url = _trim_url(m.group(0))
        if not url:
            continue
        byte_start = len(text[:m.start()].encode("utf-8"))
        byte_end = byte_start + len(url.encode("utf-8"))
        facets.append({
            "index": {"byteStart": byte_start, "byteEnd": byte_end},
            "features": [{"$type": "app.bsky.richtext.facet#link", "uri": url}],
        })
    # Facets cannot overlap. A #fragment inside a URL remains part of the link.
    occupied = {(f["index"]["byteStart"], f["index"]["byteEnd"])
                for f in facets}
    for span in tags_mod.spans(text, topic if topic else None):
        if not include_tags and span.tag != topic:
            continue
        start = len(text[:span.start].encode("utf-8"))
        end = len(text[:span.end].encode("utf-8"))
        if any(start < link_end and end > link_start
               for link_start, link_end in occupied):
            continue
        facets.append({"index": {"byteStart": start, "byteEnd": end},
                       "features": [{"$type": "app.bsky.richtext.facet#tag",
                                     "tag": span.tag}]})
    return sorted(facets, key=lambda f: f["index"]["byteStart"])


# --- 文字数（300 grapheme の近似） -----------------------------------------
def count(text: str) -> int:
    """本文の長さを数える（上限 `CHAR_LIMIT` = 300・**L2**）。

    **これは grapheme cluster の近似であって、厳密な分割ではない**（**L3**）。
    Unicode の grapheme cluster 分割（UAX #29）を正しく行うには外部ライブラリが
    要る。ここは標準ライブラリだけで、**実務上ずれやすいところ**だけを潰す:

      - 結合文字（`unicodedata.combining` が 0 でないもの）は数えない（`か`+`゛`＝1）
      - 異体字セレクタ（U+FE00〜FE0F・U+E0100〜E01EF）は数えない
      - 肌の色の修飾子（U+1F3FB〜1F3FF）は数えない
      - ZWJ（U+200D）と**その次の 1 文字**は数えない（`👨‍👩‍👧`＝1）
      - 地域指示記号（U+1F1E6〜1F1FF）は 2 個で 1（`🇯🇵`＝1）
      - タグ文字（U+E0020〜E007F。地域旗の続き）は数えない

    **ずれる向きは「実際より少なく数える」ことがある**。上限ぎりぎりの本文は
    実測で確かめること（**言い切らない**）。
    """
    total = 0
    skip_next = False
    prev_regional = False
    for ch in text:
        cp = ord(ch)
        if skip_next:
            # ZWJ の次の 1 文字。それがまた ZWJ なら、さらに次も飛ばす。
            skip_next = ch == ZWJ
            prev_regional = False
            continue
        if ch == ZWJ:
            skip_next = True
            prev_regional = False
            continue
        if unicodedata.combining(ch):
            continue
        if 0xFE00 <= cp <= 0xFE0F or 0xE0100 <= cp <= 0xE01EF:
            continue
        if 0x1F3FB <= cp <= 0x1F3FF:
            continue
        if 0xE0020 <= cp <= 0xE007F:
            continue
        if 0x1F1E6 <= cp <= 0x1F1FF:
            if prev_regional:
                prev_regional = False
                continue
            prev_regional = True
            total += 1
            continue
        prev_regional = False
        total += 1
    return total


def author_key(did: str | None) -> str | None:
    """投稿者の非可逆な識別子（設計 v2 §4.2 の `Message.author_key`）。

    **式は境界のもの**（`base.author_key(medium, identity)`・T3 の配線
    2026-09-13）。以前はここが `sha256("bluesky:" + did)` を自前で作っていて、
    Threads（`base.author_key`）と**同じ意味の欄に別の式が入っていた**——泉に
    出るのはこの欄だけなので、媒体ごとに式が違うと「同じ人か」を後から突き合わ
    せる根拠が媒体の実装の履歴に依存する。**式は 1 か所**にする。

    Bluesky の身元は **`did`**（handle は改名できるが did は変わらない）。
    did が無ければ `None`——**空文字を鍵にしない**（誰も彼もが同じ鍵になる）。
    """
    return base.author_key(MEDIUM, did)


def created_at(now: datetime.datetime | None = None) -> str:
    """`app.bsky.feed.post.createdAt`（UTC の ISO 8601・**L2** で必須）。

    台帳・記録の時刻は JST（`jst.iso()`）だが、**record に載るのは UTC**。
    `jst.now_jst()` を経由するので、テストの時刻固定（conftest）がそのまま効く。
    """
    dt = now or jst.now_jst()
    return (dt.astimezone(datetime.timezone.utc)
              .isoformat(timespec="milliseconds").replace("+00:00", "Z"))


def rkey_of(uri: str) -> str | None:
    """`at://did:plc:…/app.bsky.feed.post/<rkey>` から rkey を取り出す。"""
    if not uri or "/" not in uri:
        return None
    return uri.rsplit("/", 1)[-1] or None


def post_url(handle: str | None, uri: str) -> str | None:
    """人が開ける URL（**L3**: `https://bsky.app/profile/<handle>/post/<rkey>`）。"""
    rkey = rkey_of(uri)
    if not handle or not rkey:
        return None
    return f"https://bsky.app/profile/{handle}/post/{rkey}"


# --- XRPC ------------------------------------------------------------------
def _xrpc(service: str, method: str, nsid: str, *, params=None, payload=None,
          bearer: str | None = None, timeout: float = DEFAULT_TIMEOUT_SECONDS,
          secrets=()) -> dict:
    """XRPC を 1 回叩いて JSON の dict を返す。

    **L3**: 口は `<service>/xrpc/<nsid>`。query は GET のクエリ文字列、procedure は
    JSON の本文。配列の param（`getPosts` の `uris`）は同じ名前を繰り返す。

    失敗は `urllib` の例外のまま上げる（HTTP の状態番号の意味を三分類に写すのは
    **呼び手**・設計 v1 §3.4「core は HTTP を解釈しない」）。ただし
    **200 で返ってきた `error` を、取れたことにしない**（Threads 側の監査
    2026-09-11 と同じ穴。ここでも塞ぐ）。

    `secrets` は**いま判っている秘密の値**（App Password・`accessJwt`・
    `refreshJwt`）。**サーバの応答は伏字の対象**——鍵の名前（`password:` 等）で
    しか伏せていなかったので、`{"message": "rejected credential <値>"}` と
    鸚鵡返しにしてくるサーバ（饒舌な proxy・素朴な実装）が相手だと、値が
    そのまま例外文に載った。例外文は `collect` の `errors` → `runs` の ndjson →
    ログまで届く（独立監査 1・P1-2・2026-09-13）。**ここは秘密の値が通る唯一の
    関門**なので、名前でなく値で塞ぐ。
    """
    url = f"{service.rstrip('/')}/xrpc/{nsid}"
    if params:
        url += "?" + urllib.parse.urlencode(params, doseq=True)
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    if bearer:
        req.add_header("Authorization", f"Bearer {bearer}")
    with httpsafe.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
    body = json.loads(raw) if raw else {}
    if not isinstance(body, dict):
        raise RuntimeError(f"{nsid}: 応答が object ではありません（{type(body).__name__}）")
    if body.get("error"):
        raise RuntimeError(
            f"{nsid}: API が error を返しました（HTTP 200）: "
            f"{scrub(str(body.get('message') or body['error']), *secrets)[:200]}")
    return body


def create_session(service: str, identifier: str, app_password: str, *,
                   timeout: float = DEFAULT_TIMEOUT_SECONDS) -> dict:
    """`com.atproto.server.createSession`（**L2**）。

    **実行ごとに 1 回**（設計 v2 §4.2。呼び出し上限は 30/5 分・300/日と読んだ・**L3**。
    10 分刻みの timer なら足りる）。戻り値は `accessJwt`・`refreshJwt`・`handle`・`did`。
    """
    redact_mod.register_secret(app_password)
    body = _xrpc(service, "POST", "com.atproto.server.createSession",
                 payload={"identifier": identifier, "password": app_password},
                 timeout=timeout, secrets=(app_password,))
    # 応答の形を検査する前に登録する。必須の `did` / `handle` が欠けた異常応答でも、
    # すでに受け取った JWT はこの時点から秘密である。
    redact_mod.register_secret(body.get("accessJwt"))
    redact_mod.register_secret(body.get("refreshJwt"))
    for key in ("accessJwt", "did", "handle"):
        if not body.get(key):
            raise RuntimeError(f"createSession: 応答に {key} がありません")
    return body


class BlueskyAdapter(base.Adapter):
    """1 実行ぶんの Bluesky アダプタ（session をこの中に 1 本だけ持つ）。"""

    medium = MEDIUM
    char_limit = CHAR_LIMIT

    # --- 能力（**クラス属性**・設計 v2 §4.2「受け入れ 6」） -------------------
    # `select` はトークンを読まずに（実体を作らずに）ここを引く。語は
    # `base.KNOWN_CAPABILITIES` のものだけ。`topic` 無し（Threads の topic_tag に
    # 当たるものが無い）・`views` 無し（**L2**: `#postView` に views は無い）・
    # `quota` 無し・`inbox` 無し・`refresh` 無し（App Password に期限が無いので
    # 延長という概念が無い）・`account_insights` 無し（アカウント単位の日次は無い）。
    # `thread_read`（T1-1）: `fetch_post()` が `getPosts` で根を 1 件引ける。
    # `keyword_search`（T2-1・設計「自分の泉」§2.3・§3）: `searchPosts` で
    # 語による公開投稿の検索ができる（審査の壁が無い媒体）。
    CAPABILITIES: frozenset = frozenset({"link_preview", "recent_posts", "thread_read",
                                         "keyword_search"})

    # `.token` の鍵（`thth auth <account>` が書く形・設計 v2 §4.2「認可とトークン」）。
    # **`access_token` ではない**——doctor が `access_token` だけを見ていたので、
    # 正しく認可した Bluesky が「トークンが無い」と言われていた。
    TOKEN_KEYS = ("identifier", "app_password")
    # 次の一手は `thth token set` ではない（App Password は対話で受ける）。
    TOKEN_SETUP_HINT = "thth auth"
    POST_ID_FORM_HINT = (
        "Bluesky の post_id は `at://did:…/app.bsky.feed.post/…` の形です。")
    # App Password に期限は無い（`maintain` は `token_state: ok`・
    # `remaining_days: None`。「判らない」ではなく「期限を持たない」）。
    TOKEN_NO_EXPIRY = True

    @classmethod
    def is_post_id(cls, value) -> bool:
        """Bluesky の `post_id` は AT URI（`at://did:…/app.bsky.feed.post/…`）だけ
        （T6-2）。rkey だけの短い id（例: `hot1`）を渡す取り違いは、ここで
        adapter の生のエラーに落とす前に断る。
        """
        return isinstance(value, str) and value.startswith("at://")

    def __init__(self, *, service: str = DEFAULT_SERVICE, identifier: str = "",
                 app_password: str = "", timeout: float = DEFAULT_TIMEOUT_SECONDS,
                 thread_depth: int = DEFAULT_THREAD_DEPTH):
        self.service = (service or DEFAULT_SERVICE).rstrip("/")
        self.identifier = identifier
        self.app_password = app_password
        # adapter の局所 `scrub()` だけに頼らず、core・ログ・runs が共通で通す
        # `redact()` にも値を知らせる（監査 D12・2026-09-17）。
        redact_mod.register_secret(app_password)
        self.timeout = timeout
        self.thread_depth = thread_depth
        self._session: dict | None = None

    @classmethod
    def from_account(cls, account_cfg: dict, token: dict):
        """台帳と `.token` から組み立てる（`make_adapter()` が呼ぶ・設計 v2 §4.2）。

        読むのは台帳の `service`（省略時 `https://bsky.social`）と `.token` の
        `identifier`・`app_password`（`thth auth <account>` が書く形）。
        **`service` は既定を持つ**——Mastodon と違い、Bluesky は
        `bsky.social` が事実上の既定 PDS なので、書き忘れが「知らないサーバに
        投げる」事故にならない（Mastodon 側が `instance` 必須なのはその逆）。

        **トークンが無くてもここでは断らない**——`thth board` や `thth account`
        のような読むだけの口が、トークンを入れる前のアカウントで落ちる。
        実際に叩く段（`session()`）で「`thth auth` を先に」と loud に断る。
        """
        cfg = account_cfg or {}
        return cls(
            service=cfg.get("service") or DEFAULT_SERVICE,
            identifier=((token or {}).get("identifier")
                        or cfg.get("handle") or ""),
            app_password=(token or {}).get("app_password", ""),
        )

    # --- 秘密を通さない ----------------------------------------------------
    def _secrets(self) -> tuple:
        """いま判っている秘密の**値**（`scrub()` に渡して値ごと置き換える）。"""
        session = self._session or {}
        return (self.app_password, session.get("accessJwt"),
                session.get("refreshJwt"))

    def _scrub(self, text) -> str | None:
        return scrub(text, *self._secrets())

    # --- session -----------------------------------------------------------
    def session(self) -> dict:
        """この実行ぶんの session（初回だけ `createSession` を叩く）。"""
        if self._session is None:
            if not self.identifier or not self.app_password:
                raise RuntimeError(
                    "Bluesky の identifier と App Password がありません"
                    "（`thth auth <account>` で入れてください）")
            try:
                self._session = create_session(self.service, self.identifier,
                                                self.app_password, timeout=self.timeout)
                redact_mod.register_secret(self._session.get("accessJwt"))
                redact_mod.register_secret(self._session.get("refreshJwt"))
            except RuntimeError as e:
                raise RuntimeError(self._scrub(str(e))) from None
        return self._session

    def _request(self, method: str, nsid: str, *, params=None, payload=None) -> dict:
        """**媒体を叩く経路はここ 1 本**——秘密の伏字もここで済ませる（P1-2）。

        以前は `publish` と `probe` だけが `self._scrub()` を通していたので、
        `conversation`・`insights`・`whoami` の例外文は素通しだった。その文字列は
        `collect` の `errors` に積まれ、**`runs` の ndjson とログに残る**。
        `_xrpc()` に値を渡して塞ぐのが本体で、ここは「値で伏せそこねた文言が
        あっても、この関門をもう一度通る」ための二重の網（`RuntimeError` は
        200 応答の `error` と応答の形の異常だけ——`urllib` の例外は種類を保つ。
        状態番号を三分類に写すのは呼び手なので、型を変えてはいけない）。
        """
        try:
            return _xrpc(self.service, method, nsid, params=params, payload=payload,
                         bearer=self.session()["accessJwt"], timeout=self.timeout,
                         secrets=self._secrets())
        except RuntimeError as e:
            hidden = self._scrub(str(e))
            if hidden == str(e):
                raise
            raise RuntimeError(hidden) from None

    def quota(self):
        return None

    def refresh_token(self, token):
        raise NotImplementedError(
            "Bluesky の App Password に期限はありません（延長は要りません・設計 v2 §4.2）")

    @classmethod
    def count_text(cls, text: str) -> int:
        """媒体ごとの数え方（`queuefile.char_count` は Threads の数え方）。

        **実体を作らずに引ける**——`send`・`select`・`lint` はここを通る
        （`queuefile.count_for()`）。
        """
        return count(text)

    def count(self, text: str) -> int:
        """`count_text()` と同じ（実体を持っている呼び手のための別名）。"""
        return self.count_text(text)

    # --- 投稿 ---------------------------------------------------------------
    def publish(self, post: base.Post, *, dry_run: bool, on_container_created=None,
                before_publish=None) -> base.PublishResult:
        """`com.atproto.repo.createRecord` で `app.bsky.feed.post` を 1 本作る。

        **Bluesky に「コンテナ作成」は無い**ので `on_container_created` は呼ばない
        （引数は境界を揃えるために受け取るだけ）。30 秒の待機も無い。

        失敗の三分類は Threads と同じ規則（設計 v1 §3.5 の表）:
          - createRecord を**呼ぶ前**の失敗（session・返信先の解決）→ `publish_definite`
            ——**出ていない**（要求そのものを出していない）
          - createRecord が HTTP 4xx → `publish_definite`
          - createRecord が timeout・接続断・5xx・200 だが `uri` 無し → `publish_ambiguous`
          - `before_publish` が文字列を返した → `publish_vetoed`（**出ていない**）
        """
        ts = jst.iso()
        if dry_run:
            return base.PublishResult(post_id=None, url=None, ts=ts, error=None,
                                       failure="none")

        text = tags_mod.prepared(MEDIUM, post.text, post.topic,
                                 hashtags=post.hashtags_allowed)
        record = {
            "$type": POST_COLLECTION,
            "text": text,
            "createdAt": created_at(),
        }
        facets = build_facets(text, topic=post.topic if post.hashtags_allowed else None,
                              include_tags=post.hashtags_allowed)
        if facets:
            record["facets"] = facets
        # `post.topic` は Threads だけのもの。**黙って無視する**（設計 v2 §4.2:
        # 1 つの queue ファイルを Threads と Bluesky の 2 account が拾う形を壊さない）。
        # `post.link` も同じ——本文中の URL が facets でリンクになるので使わない。

        # **createRecord を呼ぶ前**の準備。ここで落ちたら**出ていない**。
        try:
            session = self.session()
            if post.reply_to:
                record["reply"] = self._reply_ref(post.reply_to)
        except urllib.error.HTTPError as e:
            return base.PublishResult(
                None, None, ts,
                error=self._scrub(f"公開前の準備に失敗: {e.code} {e.reason}"),
                failure="publish_definite")
        except (urllib.error.URLError, TimeoutError, OSError, RuntimeError) as e:
            return base.PublishResult(
                None, None, ts, error=self._scrub(f"公開前の準備に失敗: {e}"),
                failure="publish_definite")

        # **実際の公開要求の直前に、もう一度確かめる**（設計 v1・独立検収 P1-3 と同じ）。
        if before_publish is not None:
            veto = before_publish()
            if veto:
                return base.PublishResult(None, None, ts, error=str(veto),
                                           failure="publish_vetoed")

        try:
            body = self._request("POST", "com.atproto.repo.createRecord", payload={
                "repo": session["did"],
                "collection": POST_COLLECTION,
                "record": record,
            })
        except urllib.error.HTTPError as e:
            failure = "publish_definite" if 400 <= e.code < 500 else "publish_ambiguous"
            return base.PublishResult(
                None, None, ts, error=self._scrub(f"公開失敗: {e.code} {e.reason}"),
                failure=failure)
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            return base.PublishResult(None, None, ts,
                                       error=self._scrub(f"公開失敗: {e}"),
                                       failure="publish_ambiguous")
        except RuntimeError as e:
            # 200 のまま `error` が入っていた。**成立した可能性を排除できない。**
            return base.PublishResult(None, None, ts,
                                       error=self._scrub(f"公開失敗: {e}"),
                                       failure="publish_ambiguous")

        uri = body.get("uri")
        if not uri:
            return base.PublishResult(None, None, ts, error="公開失敗: uri無し",
                                       failure="publish_ambiguous")
        return base.PublishResult(post_id=uri, url=post_url(session.get("handle"), uri),
                                   ts=ts, error=None, failure="none")

    def _reply_ref(self, parent_uri: str) -> dict:
        """`app.bsky.feed.post#replyRef`（**L2**: `root` と `parent` の両方が要る）。

        **親が返信なら、根は親の根**（親を根にすると同じ話の続きが別スレッドに生える）。
        親が根そのものなら `root` は `parent` と同じ。
        """
        view = self._post_view(parent_uri)
        parent = {"uri": view["uri"], "cid": view["cid"]}
        record = view.get("record")
        reply = record.get("reply") if isinstance(record, dict) else None
        if isinstance(reply, dict):
            root = reply.get("root")
            if isinstance(root, dict) and root.get("uri") and root.get("cid"):
                return {"root": {"uri": root["uri"], "cid": root["cid"]}, "parent": parent}
        return {"root": dict(parent), "parent": parent}

    def _post_view(self, uri: str) -> dict:
        """`app.bsky.feed.getPosts` で 1 本ぶんの `#postView` を引く（**L2**）。

        **取れなかったことを、取れて 0 件にしない**（Threads 側 `_rows()` と同じ規律）。
        """
        body = self._request("GET", "app.bsky.feed.getPosts", params={"uris": [uri]})
        posts = body.get("posts")
        if posts is None:
            raise RuntimeError(f"getPosts: 応答に posts がありません（{uri}）")
        if not isinstance(posts, list):
            raise RuntimeError(
                f"getPosts: posts が配列ではありません（{type(posts).__name__}）")
        if not posts:
            raise RuntimeError(f"getPosts: 投稿が見つかりません（{uri}）")
        view = posts[0]
        if not isinstance(view, dict) or not view.get("uri") or not view.get("cid"):
            raise RuntimeError(f"getPosts: 応答に uri か cid がありません（{uri}）")
        return view

    # --- 会話 ---------------------------------------------------------------
    def conversation(self, post_id: str, *, since: str | None = None) -> list:
        """`app.bsky.feed.getPostThread` を辿って**全階層**を `Message` の形にする。

        返すのは**その投稿にぶら下がる返信ぜんぶ**（投稿そのものは入れない。Threads の
        `/conversation` と同じ形）。`parentHeight=0` で親側は取らない——根の uri は
        各返信の `record.reply.root.uri` に入っている（**L2**）ので、親を辿らなくても
        `root_post` が埋まる。

        `since` を渡すと `timestamp` がそれ以降のものだけを返す（ISO 8601 の文字列
        比較。同じ形・同じ tz の値どうしでしか正しく比べられない）。
        """
        body = self._request("GET", "app.bsky.feed.getPostThread", params={
            "uri": post_id, "depth": self.thread_depth, "parentHeight": 0})
        thread = body.get("thread")
        if not isinstance(thread, dict):
            raise RuntimeError(
                "会話: 応答に thread がありません。**取れて 0 件とは区別できない**"
                "ので、失敗として扱います")
        ntype = str(thread.get("$type") or "")
        if ntype.endswith("#notFoundPost"):
            raise RuntimeError(f"会話: 投稿が見つかりません（{post_id}）")
        if ntype.endswith("#blockedPost"):
            raise RuntimeError(f"会話: 投稿が遮断されています（{post_id}）")
        anchor = thread.get("post")
        if not isinstance(anchor, dict) or not anchor.get("uri"):
            raise RuntimeError("会話: thread に post がありません")

        out: list = []
        self._walk(thread, parent_uri=None, fallback_root=anchor["uri"], out=out,
                    include_self=False)
        if since:
            out = [m for m in out if m.get("timestamp") and m["timestamp"] >= since]
        return out

    def _walk(self, node, *, parent_uri, fallback_root, out: list, include_self: bool):
        if not isinstance(node, dict):
            raise RuntimeError(f"会話: 枝が object ではありません（{type(node).__name__}）")
        ntype = str(node.get("$type") or "")
        if ntype.endswith("#notFoundPost") or ntype.endswith("#blockedPost"):
            # **中身も枝も返らない**（**L2**: `#notFoundPost` は uri と notFound だけ）。
            # 数えようがないので飛ばす。**枝の途中で例外にはしない**——ほかの枝は取れる。
            return
        view = node.get("post")
        if not isinstance(view, dict) or not view.get("uri"):
            raise RuntimeError("会話: 枝に post がありません")
        if include_self:
            out.append(self._message(view, parent_uri=parent_uri,
                                      fallback_root=fallback_root))
        replies = node.get("replies")
        if replies is None:
            return
        if not isinstance(replies, list):
            raise RuntimeError(
                f"会話: replies が配列ではありません（{type(replies).__name__}）")
        for child in replies:
            self._walk(child, parent_uri=view["uri"], fallback_root=fallback_root,
                        out=out, include_self=True)

    @staticmethod
    def _message(view: dict, *, parent_uri, fallback_root) -> dict:
        """`#postView` 1 件を `Message`（設計 v2 §4.2）の形の dict に写す。

        **dict で返す**——`base.Message` の定義は T0（境界）の担当なので触らない。
        """
        record = view.get("record") if isinstance(view.get("record"), dict) else {}
        author = view.get("author") if isinstance(view.get("author"), dict) else {}
        reply = record.get("reply") if isinstance(record.get("reply"), dict) else {}
        root = reply.get("root") if isinstance(reply.get("root"), dict) else {}
        declared_parent = (reply.get("parent")
                           if isinstance(reply.get("parent"), dict) else {})
        return {
            "message_id": view.get("uri"),
            "username": author.get("handle"),
            "text": record.get("text"),
            "timestamp": record.get("createdAt") or view.get("indexedAt"),
            "replied_to": parent_uri or declared_parent.get("uri"),
            "root_post": root.get("uri") or fallback_root,
            "medium": MEDIUM,
            "author_key": author_key(author.get("did") or ""),
            # SNS に会話の窓の期限は無い（**WhatsApp の芽**・設計 v2 §4.2）。
            "reply_deadline": None,
        }

    # --- 根を 1 件 -----------------------------------------------------------
    def fetch_post(self, post_id: str) -> dict:
        """`app.bsky.feed.getPosts` で根を 1 件引く（T1-1・設計「自分の泉」§2.1）。

        **`_post_view()` をそのまま使う**（`_reply_ref()` と同じ口）。この口で
        返す行は**枝の根**として使うので、`replied_to` は常に `None`・
        `root_post` は自分自身の id にする（この投稿自身が誰かへの返信で
        あっても、`thread_read` が組む枝の根はこの投稿）。
        """
        view = self._post_view(post_id)
        record = view.get("record") if isinstance(view.get("record"), dict) else {}
        author = view.get("author") if isinstance(view.get("author"), dict) else {}
        handle = author.get("handle")
        uri = view.get("uri")
        out = {
            "message_id": uri,
            "username": handle,
            "text": record.get("text"),
            "timestamp": record.get("createdAt") or view.get("indexedAt"),
            "replied_to": None,
            "root_post": uri,
            "medium": MEDIUM,
            "author_key": author_key(author.get("did") or ""),
            "reply_deadline": None,
        }
        permalink = post_url(handle, uri) if handle and uri else None
        if permalink:
            out["permalink"] = permalink
        return out

    # --- 語で検索（T2-1・設計「自分の泉」§2.3・§3） -------------------------
    # **L2**（`docs.bsky.app/docs/api/app-bsky-feed-search-posts` →
    # `endpoints.bsky.app` へ 301・2026-09-16 に WebFetch で読解。lexicon の
    # 生 JSON も GitHub 経由で確認）: `app.bsky.feed.searchPosts` の param は
    # `q`（必須）・`sort`（`top`|`latest`・**既定 `latest`**）・`since`・
    # `until`・`mentions`・`author`・`lang`・`domain`・`url`・`tag`・
    # `limit`（1〜100・既定 25）・`cursor`。出力は `{"posts": [...], "cursor",
    # "hitsTotal"}` で、`posts` は `getPosts`/`getPostThread` と同じ
    # `#postView` の配列。
    SEARCH_TYPES = ("TOP", "RECENT")
    KEYWORD_SEARCH_MAX_LIMIT = 100
    # `search_type`（Threads の語に揃えた呼び名）→ lexicon の `sort` の値。
    _SORT_BY_SEARCH_TYPE = {"TOP": "top", "RECENT": "latest"}

    def _search_row(self, view: dict) -> dict:
        """`#postView` 1 件を、B-1 の一覧が使う形（`Message` の鍵＋`reply_count`・
        `has_replies`）に写す。**検索結果はどの枝の中かを返さない**——lexicon の
        `#postView` に `reply`（親）は乗らないので、`replied_to`・`root_post` は
        Threads の `keyword_search`（`_message_row()`）と同じく `None`。
        """
        record = view.get("record") if isinstance(view.get("record"), dict) else {}
        author = view.get("author") if isinstance(view.get("author"), dict) else {}
        handle = author.get("handle")
        uri = view.get("uri")
        out = {
            "message_id": uri,
            "username": handle,
            "text": record.get("text"),
            "timestamp": record.get("createdAt") or view.get("indexedAt"),
            "replied_to": None,
            "root_post": None,
            "medium": MEDIUM,
            "author_key": author_key(author.get("did") or ""),
            "reply_deadline": None,
        }
        permalink = post_url(handle, uri) if handle and uri else None
        if permalink:
            out["permalink"] = permalink
        # `#postView.replyCount`（**L2**）。**数と真偽を分ける**（`threads_read_cli.
        # REPLY_COUNT_KEYS`・設計 v2 §4.4）——無ければどちらも入れない（0 と混ぜない）。
        reply_count = view.get("replyCount")
        if isinstance(reply_count, int) and not isinstance(reply_count, bool):
            out["reply_count"] = reply_count
            out["has_replies"] = reply_count > 0
        return out

    def keyword_search(self, q: str, *, search_type: str = "TOP",
                       limit: int = 25) -> list:
        """`GET app.bsky.feed.searchPosts`（**L2**・上の表・T2-1）。**1 頁だけ。**

        `search_type` は Threads と同じ 2 値（`TOP`→`sort=top`・`RECENT`→
        `sort=latest`）。認証つき（`_request()` の流儀のまま——lexicon は
        「一部のサーバでは認証が要る場合がある」としか言っていないが、この
        アダプタは全部の呼び出しを認証つきにしている・T0 の境界どおり）。
        返るのは B-1 の形の行（`_search_row()`）。**本文はここで返すだけ**
        ——どこにも書かない（保存しないのは呼ぶ側の規律・設計「自分の泉」§5）。
        """
        if not isinstance(q, str) or not q.strip():
            raise base.AdapterError("検索の語が空です")
        if search_type not in self.SEARCH_TYPES:
            raise base.AdapterError(
                f"search_type は {' / '.join(self.SEARCH_TYPES)} のどちらかです"
                f"（{search_type!r}）")
        if (not isinstance(limit, int) or isinstance(limit, bool)
                or limit < 1 or limit > self.KEYWORD_SEARCH_MAX_LIMIT):
            raise base.AdapterError(
                f"limit は 1〜{self.KEYWORD_SEARCH_MAX_LIMIT} です（{limit!r}）")
        params = {"q": q.strip(), "sort": self._SORT_BY_SEARCH_TYPE[search_type],
                  "limit": limit}
        body = self._request("GET", "app.bsky.feed.searchPosts", params=params)
        posts = body.get("posts")
        if not isinstance(posts, list):
            raise base.AdapterError(
                "searchPosts: 応答に posts の配列がありません"
                f"（{type(posts).__name__}）。取れて 0 件とは区別できません")
        return [self._search_row(view) for view in posts
                if isinstance(view, dict) and view.get("uri")]

    # --- 直近の投稿 ---------------------------------------------------------
    @staticmethod
    def _observed_tags(view: dict) -> set[str]:
        """Read both facet tags and record.tags; neither text nor handles are kept."""
        record = view.get("record") if isinstance(view.get("record"), dict) else {}
        raw_tags = record.get("tags")
        found = {tag for tag in raw_tags if isinstance(tag, str) and tag} if isinstance(raw_tags, list) else set()
        facets = record.get("facets")
        for facet in facets if isinstance(facets, list) else []:
            if not isinstance(facet, dict):
                continue
            features = facet.get("features")
            for feature in features if isinstance(features, list) else []:
                if isinstance(feature, dict) and feature.get("$type") == "app.bsky.richtext.facet#tag":
                    tag = feature.get("tag")
                    if isinstance(tag, str) and tag:
                        found.add(tag)
        return found

    def tag_search(self, q: str, *, tags: list[str], sort: str = "latest",
                   since: str | None = None, until: str | None = None,
                   pages: int = 4, limit: int = 100) -> dict:
        """Bounded searchPosts pagination; return aggregates without post content."""
        if not isinstance(q, str) or not q.strip():
            raise base.AdapterError("tag search: q は空にできません")
        if not isinstance(tags, list) or not tags or any(
                not isinstance(tag, str) or not tag or tag.startswith("#") for tag in tags):
            raise base.AdapterError("tag search: tag は # なしの非空文字列の配列です")
        if sort not in ("top", "latest"):
            raise base.AdapterError("tag search: sort は top/latest です")
        if type(pages) is not int or not 1 <= pages <= 10:
            raise base.AdapterError("tag search: pages は 1〜10 です")
        if type(limit) is not int or not 1 <= limit <= 100:
            raise base.AdapterError("tag search: limit は 1〜100 です")
        params = {"q": q.strip(), "tag": tags, "sort": sort, "limit": limit}
        if since is not None:
            params["since"] = since
        if until is not None:
            params["until"] = until
        seen_ids, authors, co_tags = set(), set(), collections.Counter()
        n = tagged_n = 0
        latest_at = None
        cursor = None
        page_count = 0
        seen_cursors = set()
        for _ in range(pages):
            request = dict(params)
            if cursor:
                request["cursor"] = cursor
            body = self._request("GET", "app.bsky.feed.searchPosts", params=request)
            page_count += 1
            posts = body.get("posts")
            if not isinstance(posts, list):
                raise base.AdapterError("searchPosts: 応答に posts の配列がありません")
            for view in posts:
                if not isinstance(view, dict):
                    continue
                uri = view.get("uri")
                if not isinstance(uri, str) or not uri or uri in seen_ids:
                    continue
                seen_ids.add(uri)
                n += 1
                author = view.get("author") if isinstance(view.get("author"), dict) else {}
                if isinstance(author.get("did"), str) and author["did"]:
                    authors.add(author["did"])
                record = view.get("record") if isinstance(view.get("record"), dict) else {}
                stamp = record.get("createdAt") or view.get("indexedAt")
                if isinstance(stamp, str) and (latest_at is None or stamp > latest_at):
                    latest_at = stamp
                observed = self._observed_tags(view)
                if set(tags).issubset(observed):
                    tagged_n += 1
                for other in observed - set(tags):
                    co_tags[other] += 1
            next_cursor = body.get("cursor")
            if not isinstance(next_cursor, str) or not next_cursor or next_cursor in seen_cursors:
                cursor = None
                break
            seen_cursors.add(next_cursor)
            cursor = next_cursor
        return {"n": n, "distinct_authors": len(authors), "latest_at": latest_at,
                "tagged_n": tagged_n, "tagged_share": tagged_n / n if n else None,
                "co_tags": [{"tag": tag, "n": count} for tag, count in
                            sorted(co_tags.items(), key=lambda item: (-item[1], item[0]))[:5]],
                "observed_from": self.service, "window": {"since": since, "until": until},
                "pages_fetched": page_count, "cursor_available": bool(cursor),
                "search_tags": list(tags), "sort": sort}

    def recent_posts(self, *, limit: int = 25) -> list:
        """`app.bsky.feed.getAuthorFeed`（**L2**——lexicon を 2026-09-13 に読解）。

        `actor`（at-identifier・必須）・`limit`（1〜100・既定 50）・`cursor`・
        `filter`（既定 `posts_with_replies`）・`includePins`（既定 false）。出力は
        `cursor` と `feed`（`#feedViewPost` の配列）。`#feedViewPost` は `post`
        （`#postView`）が必須で、`reply`・`reason`（`#reasonRepost` か
        `#reasonPin`）が任意（**L2**・`app.bsky.feed.defs`）。

        **`reason` の付いた行は落とす**——それは「本人が再投稿したもの」であって
        本人が書いた投稿ではない（`thth posts` は「THTH を通していない**自分の
        投稿**」を数えるための口）。`includePins` は既定の false のままなので、
        ここで落ちるのは実質すべて再投稿。

        **返信も入る**（`filter` を既定のままにしている）。THTH が出した返信も
        `post_id` で突き合わせたいので、`posts_no_replies` にはしない。
        """
        actor = (self.session().get("did") or self.identifier or "").strip()
        if not actor:
            raise base.AdapterError("actor（did か handle）が判らないので直近の投稿を引けません")
        body = self._request("GET", "app.bsky.feed.getAuthorFeed", params={
            "actor": actor, "limit": max(1, min(int(limit), 100))})
        feed = body.get("feed")
        if not isinstance(feed, list):
            # **「取れなかった」を「取れて 0 件」にしない**（`_post_view` と同じ規律）。
            raise base.AdapterError(
                "getAuthorFeed: 応答に feed の配列がありません"
                f"（{type(feed).__name__}）。取れて 0 件とは区別できません")
        out = []
        for item in feed:
            if not isinstance(item, dict) or item.get("reason"):
                continue
            view = item.get("post")
            if not isinstance(view, dict) or not view.get("uri"):
                continue
            record = view.get("record")
            record = record if isinstance(record, dict) else {}
            author = view.get("author")
            handle = (author or {}).get("handle") if isinstance(author, dict) else None
            out.append({
                "post_id": view["uri"],
                # 書いた時刻（`record.createdAt`）を優先し、無ければ索引された時刻。
                "timestamp": record.get("createdAt") or view.get("indexedAt"),
                "url": post_url(handle, view["uri"]) if handle else None,
                "text": record.get("text"),
                # **語（Threads の topic_tag）に当たるものが無い**媒体。
                "topic": None,
            })
        return out

    # --- 実測 ---------------------------------------------------------------
    def insights(self, post_id: str) -> dict:
        """投稿 1 本の数（**読んだ時点の累計**）。

        **views は入れない**——**L2**: `app.bsky.feed.defs#postView` に views に
        当たる項目が無い。`available` は「この媒体が持ちうる指標」、`metrics` は
        「今回実際に取れた値」。取れなかった指標は `metrics` に入れない
        （**0 と混ぜない**・設計 v1 §3.2.2）。
        """
        view = self._post_view(post_id)
        metrics = {}
        for name, field in _METRIC_FIELDS:
            value = view.get(field)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                continue
            metrics[name] = value
        return {"metrics": metrics, "available": list(AVAILABLE_METRICS)}

    # --- 身元と検査 ----------------------------------------------------------
    def whoami(self) -> dict:
        """`thth token set` / `doctor` の検証に使う（Threads の `me` の一般化）。"""
        session = self.session()
        return {"user_id": session.get("did"), "username": session.get("handle")}

    def probe(self, *, get=None) -> list:
        """`doctor` の probe（媒体側に置く・設計 v2 §4.2）。

        `get` は doctor 側の「読み取りしか呼ばない取得口」（`base.Adapter.probe`
        参照）。**Bluesky 側は使わない**——XRPC は認可を `Authorization` ヘッダで
        載せるので、`access_token` をクエリに置く Threads 向けの口とは形が違う。
        **トークンをクエリに移してまで口を共有しない。**
        """
        # 1 行の形は T0 の決めた 6 項目（`name`・`label`・`permission`・`key`・
        # `ok`・`detail`）。**`doctor` の人向け画面は `label` と `permission` を
        # 直に読む**ので、欠けると画面のほうが落ちる（P2-3 と同じ筋）。
        def _row(name, label, permission, key, ok, detail):
            return {"name": name, "label": label, "permission": permission,
                    "key": key, "ok": ok, "detail": detail, "body": None}

        results = []
        try:
            session = self.session()
        except urllib.error.HTTPError as e:
            results.append(_row("createSession", "本人の確認", "App Password",
                                 "whoami", False, self._scrub(f"{e.code} {e.reason}")))
            return results
        except Exception as e:  # noqa: BLE001 - 理由を detail に残して先へ進まない
            results.append(_row("createSession", "本人の確認", "App Password",
                                 "whoami", False, self._scrub(str(e))[:220]))
            return results
        results.append(_row("createSession", "本人の確認", "App Password", "whoami",
                             True, f"handle={session.get('handle')}"))
        try:
            body = self._request("GET", "app.bsky.actor.getProfile",
                                  params={"actor": session["did"]})
        except urllib.error.HTTPError as e:
            results.append(_row("getProfile", "自分の素性", "（認可不要）", "profile",
                                 False, self._scrub(f"{e.code} {e.reason}")))
        except Exception as e:  # noqa: BLE001
            results.append(_row("getProfile", "自分の素性", "（認可不要）", "profile",
                                 False, self._scrub(str(e))[:220]))
        else:
            results.append(_row(
                "getProfile", "自分の素性", "（認可不要）", "profile", True,
                f"handle={body.get('handle')} posts={body.get('postsCount')}"))
        return results


# --- 認可（配線なしで呼べる） ----------------------------------------------
# App Password は `xxxx-xxxx-xxxx-xxxx`（**L3**: 記憶。管理画面が出す形）。
APP_PASSWORD_RE = re.compile(r"^[a-z0-9]{4}(?:-[a-z0-9]{4}){3}$")


def auth_interactive(identifier_input=None, password_input=None, *,
                     service: str = DEFAULT_SERVICE,
                     timeout: float = DEFAULT_TIMEOUT_SECONDS,
                     allow_any_password: bool = False) -> dict:
    """`thth auth <account>`（Bluesky）の中身。**書き込みは呼び手**。

    `identifier_input` / `password_input` は**値を返す呼び出し可能なもの**（既定は
    `input` と `getpass.getpass`）。この関数は**値をどこにも出さない**——戻り値を
    受け取った側が `~/.config/thth/<account>.token`（600）に書く。

    戻り値の鍵（設計 v2 §4.2「認可とトークン」）:
      `identifier`・`app_password`・`did`・`handle`・`no_expiry`（常に True）・`obtained_at`

    `no_expiry` は「**判らない**」ではなく「**期限を持たない**」の印。`maintain` は
    これを見て `token_state: ok` / `remaining_days: None` と言い分ける。
    """
    ask_id = identifier_input or (lambda: input("Bluesky の handle（例: name.bsky.social）: "))
    ask_pw = password_input or (lambda: getpass.getpass("App Password: "))

    identifier = (ask_id() or "").strip().lstrip("@")
    if not identifier:
        raise ValueError("handle が空です")
    app_password = (ask_pw() or "").strip()
    if not app_password:
        raise ValueError("App Password が空です")
    if not allow_any_password and not APP_PASSWORD_RE.match(app_password):
        # **アカウントのパスワードを受け取らない。** App Password は管理画面から
        # 取り消せるが、アカウントのパスワードは取り消せない——`.token` に置いて
        # よいものではない。**loud に断る**（作法 §5）。
        raise ValueError(
            "App Password の形（xxxx-xxxx-xxxx-xxxx）ではありません。"
            "アカウントのパスワードではなく、設定画面で発行した App Password を"
            "使ってください（どうしても通したいときは allow_any_password=True）")

    try:
        session = create_session(service, identifier, app_password, timeout=timeout)
    except urllib.error.HTTPError as e:
        raise RuntimeError(
            scrub(f"createSession に失敗しました: {e.code} {e.reason}",
                  app_password)) from None
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(
            scrub(f"createSession に失敗しました: {e}", app_password)) from None

    return {
        "identifier": identifier,
        "app_password": app_password,
        "did": session["did"],
        "handle": session["handle"],
        "no_expiry": True,
        "obtained_at": jst.iso(),
    }
