"""X（api.x.com の v2）の投稿 adapter。設計 2.14.0 §2・一次資料は同 §7。

**この版で出せるもの**: 本文・返信・画像 4 枚・GIF 1・動画 1・投票・返信制限・alt。
**引用は出さない**——`quote_tweet_id` は自己サーブの tier では使えない（§7 の
"Quote-posting … requires an Enterprise plan"）。「API に無い」のではないので、
表には `unsupported: provider_tier_enterprise_only` と理由を付けて載せる
（`thth/media_capabilities.py`）。**この module は `quote_tweet_id` を組み立てない。**

**読む口**: `whoami`（`GET /2/users/me`）と `posts`（`GET /2/users/:id/tweets`）。
どちらも従量の読み取りなので **2.12 の読取予算（USD）の中でしか動かない**
（既定 0＝動かない）。

**他人の投稿を読む口**（設計 2026-10-08「X の検索と枝の読み取りと使用ルール」）:
`keyword_search`（`GET /2/tweets/search/recent`）・`fetch_post`（`GET /2/tweets/:id`）・
`conversation`（`fetch_post` で `conversation_id` を取り、`conversation_id:<id>` で検索）。
**返ってきた投稿 1 本ごとに 0.005 USD**（Posts: Read）なので、`budget_x.posts_read()` の
予約の中でだけ動く（月の上限は本人確認と合わせて・account ごとの JST の 1 日の本数）。
**人が選んで 1 本ずつ返す出向きのためだけ**——timer・見張り・自動の返信には使わない
（`PAID_READS`。`collect`・`morning` はこの印を見て叩かない）。`mentions`／`insights` は無い。

**自分の投稿の数字の口**（masaru 10/12 決定 1）: `owned_metrics`（`GET /2/tweets?ids=…` に
`public_metrics,created_at,author_id`・最大 100 本を 1 回で）。**呼ぶ側が渡すのは自分が THTH
から出した投稿の id だけ**（`collect` が `sent/` から拾う）。応答の `author_id` が接続中の
user_id と一致しない行は `x_metrics.normalize_owned_metrics` が落とす。料金表の Owned Reads
（1 本 0.001 USD）で、`budget_x.owned_read()` の予約の中でだけ動く——他人の投稿の読み取り
（`PAID_READS`）とは別の段なので、collect の timer から叩いてよいのはこの口だけ。
本文・username は取らない（`tweet.fields` に text を入れず、`expansions` も付けない）。

**コミュニティ**（3.19.0）: 出すのは `tweet_body` の `community_id`（返信とは同時に使わない）。
探すのは `community_search`（`GET /2/communities/search`・Community: Read 1 件 0.005 USD・
`budget_x.community_read()` の予約の中でだけ動く・1 日の本数には数えない）。参加の API と
「参加しているか」の欄は資料に無い——参加は X の画面で、断りは `publish_refusal` が残す。

**書く口の上限**: `thth admin budget x-posts --monthly <本数>`（`thth/budget_x_posts.py`）。
既定 0＝**投稿しない**。予約は要求の前・確定は応答の後で、**結果不明は自動で
投げ直さない**（数え上げでは「不明」として別に残る）。

**全期間の検索**（4.5.0・設計 `docs/設計_4.5.0_Xの全期間の検索_2026-10-21.md`）:
`archive_search`（`GET /2/tweets/search/all`・2006 年から・1 回 10〜100 本・`next_token` を
辿るのは総本数の上限まで）と `archive_counts`（`GET /2/tweets/counts/all`・1 回 0.010 USD・
31 日ごとに 1 ページ）。台帳は `budget_x.archive_read`／`request_read(COUNTS_ALL)`（材料集め・
1 日の本数は `x_archive_daily_reads` で別に縛る）。**人の居ない道（`x_archive.unattended()`）では
要求も予約もせずに断る。** どちらも `CAPABILITIES` には載せない（`where`・`map`・`morning` が
拾わない）。

**429**: `retry-after`／`x-rate-limit-reset` を静的理由 `provider_rate_limited` の
傍らに数字だけ持つ（待たない・次の run で）。
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request

from .. import httpsafe, jst, leave_gate
from .. import redact as redact_mod
from . import base, x_text

MEDIUM = 'x'
DEFAULT_BASE_URL = 'https://api.x.com'
BASE_URL_ENV = 'THTH_X_BASE_URL'
DEFAULT_TIMEOUT_SECONDS = 10.0

# **本文の数え方**（3.20.0）: X の公式の数え方＝twitter-text v3 の weighted length
# （`x_text.py`・CJK と全角は 2、URL は 23、絵文字の並びは 2）。上限は 280（config/v3.json
# の maxWeightedTweetLength）。2.14〜3.19 はコードポイントで数えていて、X のアプリで
# 284/280 になる原稿を 160/280 として通していた（kopicha-x・10/15）。
MAX_WEIGHTED_LENGTH = x_text.MAX_WEIGHTED_LENGTH

REPLY_SETTINGS = ('following', 'mentionedUsers', 'subscribers', 'verified')
POST_ID = re.compile(r'[0-9]{1,19}')
# コミュニティの id（**L1** docs.x.com create-post `community_id`: `^[0-9]{1,19}$`）。
COMMUNITY_ID = re.compile(r'[0-9]{1,19}')


class AdapterError(base.AdapterError):
    def __init__(self, message, *, failure=None, http_status=None):
        super().__init__(message)
        self.failure = failure
        self.http_status = http_status


def api_origin():
    """`https://api.x.com`、または試験の loopback。**それ以外は受けない。**

    `auth_x.api_origin()` と同じ判定を同じ環境変数で行う（認可と投稿で別々の
    宛先を向かないように、綴りも条件も 1 つにしておく）。
    """
    from .auth_x import api_origin as origin
    return origin()


class NoRedirect(httpsafe.SameOriginRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise httpsafe.RedirectBlocked(req.full_url, code, 'media_redirect_refused', headers, fp)


_transport = httpsafe.build_opener(NoRedirect())
MAX_RESPONSE_BYTES = 1024 * 1024


def rate_limit(error):
    """429 の応答が持っている**数字だけ**（provider の文面は取らない）。

    `x-rate-limit-reset` は epoch 秒、`retry-after` は相対秒。どちらも静的な形
    （非負の整数）でなければ落とす——理由コードに provider の文字列を混ぜない。
    """
    headers = getattr(error, 'headers', None)
    out = {}
    if headers is None:
        return out
    for name, key in (('x-rate-limit-reset', 'rate_limit_reset'),
                      ('retry-after', 'retry_after_seconds')):
        raw = headers.get(name)
        if raw is None:
            continue
        try:
            value = int(str(raw).strip())
        except (TypeError, ValueError):
            continue
        if 0 <= value <= 2 ** 31:
            out[key] = value
    return out


# 投稿の 4xx の本文から**静的な符丁だけ**を引く（provider の文面は理由に混ぜない・
# `rate_limit()` と同じ流儀）。10/13 関東の返信が `x_publish_http_403` だけを残し、
# 「返信先の投稿者から関わりが無い」制限か、アカウントの制限かを見分けられなかった。
# 先頭の 1 行は 10/13 に実機で取れた文面（L1）。残りはフォーラムで報告されている形（L3）。
PUBLISH_REFUSALS = (
    # 10/13 実機（関東・3.18.2 で取れた・L1）: "You can only reply to or quote posts where
    # you are mentioned or are the author."（自己サーブでは、自分がメンションされた投稿か
    # 自分の投稿にしか返信・引用できない）。
    ('you can only reply to or quote posts where you are mentioned', 'x_reply_not_engaged'),
    ('not been mentioned or otherwise engaged', 'x_reply_not_engaged'),
    ('reply to this conversation is not allowed', 'x_reply_not_engaged'),
    ('duplicate content', 'x_duplicate_content'),
    ('not permitted to perform this action', 'x_not_permitted'),
    ('not permitted to access this feature', 'x_not_permitted'),
)
MAX_ERROR_BYTES = 4096


def publish_refusal(error) -> str | None:
    """4xx の本文（`detail`・`title`・`type`・`errors[].message`）を 1 回だけ読む。

    知っている形なら静的な符丁。知らない形は **X の文面を短く残す**（3.18.2・10/13
    関東の返信の 403 が知らない形で、何が断られたのか読めなかった。SageSpring の依頼）。
    X の problem の文面は自分の要求への断りで、他人の投稿の本文は入らない。それでも
    `redact` を通し、200 字で切る。
    """
    try:
        raw = error.read(MAX_ERROR_BYTES + 1)
    except (OSError, ValueError, AttributeError):
        return None
    if not raw or len(raw) > MAX_ERROR_BYTES:
        return None
    try:
        body = json.loads(raw)
    except (ValueError, UnicodeError):
        return None
    if not isinstance(body, dict):
        return None
    texts = [body.get('detail'), body.get('title')]
    for item in body.get('errors') or []:
        if isinstance(item, dict):
            texts += [item.get('message'), item.get('detail')]
    blob = ' '.join(t for t in texts if isinstance(t, str)).lower()
    for needle, code in PUBLISH_REFUSALS:
        if needle in blob:
            return code
    parts = []
    kind = body.get('type')
    if isinstance(kind, str) and kind.strip():
        parts.append('type=' + kind.strip().rstrip('/').rsplit('/', 1)[-1][:60])
    for key in ('title', 'detail'):
        if isinstance(body.get(key), str) and body[key].strip():
            parts.append(key + '=' + body[key].strip())
    for item in body.get('errors') or []:
        if isinstance(item, dict) and isinstance(item.get('message'), str):
            parts.append('message=' + item['message'].strip())
            break
    if not parts:
        return None
    text = redact_mod.redact(' / '.join(parts).replace('\n', ' ')) or ''
    return ('x_unknown_refusal（' + text[:200] + '）') if text else None


def request(adapter, method, path, *, json_body=None, data=None, query=None,
            headers=None, timeout=None, socket_timeout=None):
    """1 回だけ投げて `(status, value)`。**urllib の例外はそのまま上げる**
    （4xx と 5xx の言い分けは呼び手が持つ・Mastodon と同じ流儀）。"""
    url = adapter.base_url + path
    if query:
        url += '?' + urllib.parse.urlencode(query)
    head = {'Accept': 'application/json'}
    if adapter.access_token:
        head['Authorization'] = 'Bearer ' + adapter.access_token
    body = data
    if json_body is not None:
        body = json.dumps(json_body, ensure_ascii=False, allow_nan=False).encode('utf-8')
        head['Content-Type'] = 'application/json'
    if headers:
        head.update(headers)
    req = urllib.request.Request(url, data=body, method=method, headers=head)
    budget = adapter.timeout if socket_timeout is None else socket_timeout
    with leave_gate.urlopen(_transport.open, req,
                            timeout=budget if timeout is None else min(budget, timeout)) as response:
        status = response.status
        raw = response.read(MAX_RESPONSE_BYTES + 1)
    if len(raw) > MAX_RESPONSE_BYTES:
        raise AdapterError('x_response_too_large')
    value = json.loads(raw) if raw.strip() else {}
    if not isinstance(value, dict):
        raise AdapterError('x_response_invalid')
    return status, value


def post_id_of(value):
    data = value.get('data') if isinstance(value, dict) else None
    identifier = data.get('id') if isinstance(data, dict) else None
    if not isinstance(identifier, str) or not POST_ID.fullmatch(identifier):
        return None
    return identifier


def full_text(row):
    """1 本の本文。長文（280 字超）は `note_tweet.text` の全文を、無ければ `text` を返す。

    応答の名前は資料の data dictionary が `note_tweet`、OpenAPI（2.171）の Post が `note_post`
    （`referenced_tweets`／`referenced_posts` と同じ改名）。要求の欄の名前は `note_tweet` のまま
    なので、応答はどちらでも読む。中の `text` が空・型違いなら `text` に戻る。
    """
    for key in ('note_tweet', 'note_post'):
        note = row.get(key)
        if isinstance(note, dict) and isinstance(note.get('text'), str) and note['text'].strip():
            return note['text']
    return row.get('text')


def post_url(username, post_id):
    if not username or not post_id:
        return None
    return 'https://x.com/' + urllib.parse.quote(str(username)) + '/status/' + str(post_id)


def tweet_body(post, manifest=None, media_ids=()):
    """`POST /2/tweets` の本体（**ここが `quote_tweet_id` を持たない唯一の場所**）。

    `text` は media があるときだけ省ける（§7 "Required unless media is
    provided"）。投票と添付は排他で、そのことは要求の前に `x_media.intent_error`
    が断る——ここは組み立てるだけ。
    """
    manifest = manifest or {'attachments': [], 'post_options': {}}
    body = {}
    text = post.text or ''
    if text or not media_ids:
        body['text'] = text
    if post.reply_to:
        body['reply'] = {'in_reply_to_tweet_id': str(post.reply_to)}
    community = getattr(post, 'community_id', None)
    if community:
        # 3.19.0: コミュニティへ出す。返信との同時指定は手前（lint・select・send）が断る。
        # ここでも形だけは確かめる（数字 19 桁まで以外は組み立てない）。
        if post.reply_to or not COMMUNITY_ID.fullmatch(str(community)):
            raise AdapterError('x_community_id_invalid')
        body['community_id'] = str(community)
    if media_ids:
        body['media'] = {'media_ids': list(media_ids)}
    poll = next((row for row in manifest['attachments'] if row['type'] == 'poll'), None)
    if poll is not None:
        body['poll'] = {'options': list(poll['options']),
                        'duration_minutes': poll['duration_minutes']}
    settings = manifest['post_options'].get('reply_settings')
    if settings:
        body['reply_settings'] = settings
    return body


class XAdapter(base.Adapter):
    """X v2。認可は `Authorization: Bearer <access_token>` の**ヘッダだけ**。

    トークンは 2.11 の `thth auth`（PKCE・預かり所）が置いたもの（`offline.access`
    の refresh は `thth refresh`／`thth maintain` が回す——`adapters.auth_x`）。
    """

    CAPABILITIES: frozenset = frozenset({'recent_posts', 'keyword_search', 'thread_read'})
    # 他人の投稿の読み取りが従量（設計 2026-10-08）。**自動の経路はこの印で叩かない。**
    PAID_READS = True
    KEYWORD_SEARCH_DEFAULT_LIMIT = 10
    KEYWORD_SEARCH_MAX_LIMIT = 25
    CONVERSATION_MAX = 50
    # `thth thread --limit N` の下限（recent search の `max_results` の下限と同じ）。
    CONVERSATION_MIN = 10
    # 自分の投稿の数字（Owned Reads）。`GET /2/tweets` の `ids` は 1 回 100 本まで。
    OWNED_BATCH = 100
    OWNED_FIELDS = 'public_metrics,created_at,author_id'
    OWNED_METRICS = ('views', 'likes', 'replies', 'reposts', 'quotes', 'bookmarks')
    # `max_results` の下限（一次資料: recent search は 10〜100）。
    SEARCH_MIN_RESULTS = 10
    SEARCH_WINDOW_DAYS = 7
    SEARCH_TYPES = ('TOP', 'RECENT')
    # `note_tweet`（10/15・点検 §B-3）: 280 字を超える長文の全文。既定の欄ではないので頼まないと
    # 来ない（一次資料 docs.x.com data dictionary「Contains the full text of a Post for long-form
    # Posts (>280 characters)」）。課金は返った本数なので、欄を足しても費用は変わらない。
    READ_FIELDS = {'tweet.fields': 'created_at,author_id,conversation_id,referenced_tweets,note_tweet',
                   'expansions': 'author_id', 'user.fields': 'username,public_metrics'}
    TOKEN_KEYS = ('access_token',)
    TOKEN_SETUP_HINT = 'thth auth'
    POST_ID_FORM_HINT = 'X の post_id は 19 桁までの数字です。'
    DELETE_PERMISSION = 'tweet.write'
    prepared_media_supported = True
    # 反応（設計 3.15.0・**L1** docs.x.com repost-post・like-post）: 再投稿は
    # `POST /2/users/:id/retweets`（`tweet.write`・いまのトークンにある）、取り消しは
    # `DELETE /2/users/:id/retweets/:source_tweet_id`。いいねは `POST /2/users/:id/likes`・
    # `DELETE /2/users/:id/likes/:tweet_id`（`like.write`・3.15.0 から認可で求める。
    # **それより前のトークンには無い**）。従量: 作る 0.015 USD・消す 0.010 USD（料金表・
    # 推定として出すだけで、新しい予算の台帳は持たない）。
    CAPABILITIES = CAPABILITIES | frozenset({'repost', 'like'})
    REACTION_PERMISSIONS = {'repost': 'tweet.write', 'like': 'like.write'}
    REACTION_PRICE_USD = {'create': '0.015', 'delete': '0.010'}

    def __init__(self, *, base_url: str = '', access_token: str = '', user_id: str = '',
                 username: str = '', timeout: float = DEFAULT_TIMEOUT_SECONDS):
        self.base_url = (base_url or os.environ.get(BASE_URL_ENV) or DEFAULT_BASE_URL).rstrip('/')
        self.access_token = access_token
        redact_mod.register_secret(access_token)
        self.user_id = str(user_id or '')
        self.username = str(username or '')
        self.timeout = timeout
        self.auth_account = '<account>'
        self.granted_scopes = None
        # この実体（＝1 回のコマンド）で読んだ他人の投稿（本数・推定 USD・打ち切り）。
        self._reads = {'posts': 0, 'usd': '0', 'calls': 0, 'truncated': False}
        self._post_cache = {}
        # 自分の投稿の数字（Owned Reads）は別に数える（料金も台帳も別の段）。
        self._owned = {'posts': 0, 'usd': '0', 'calls': 0}
        # コミュニティの検索（Community: Read・3.19.0）も別に数える（台帳も別）。
        self._communities = {'count': 0, 'usd': '0', 'calls': 0}
        self.community_more = False
        # トレンド・語ごとの投稿数（3.19.0）: 台帳の種類 → 確定した要求の回数（断りの応答は数えない）。
        self._requests = {}
        # 全期間の検索（4.5.0）: 別の台帳（`budget_x.ARCHIVE`）・別に数える。
        self._archive = {'posts': 0, 'usd': '0', 'calls': 0, 'truncated': False}

    @classmethod
    def from_account(cls, account_cfg: dict, token: dict):
        cfg = account_cfg or {}
        token = token or {}
        adapter = cls(base_url=api_origin(), access_token=token.get('access_token', ''),
                      user_id=token.get('user_id') or cfg.get('user_id') or '',
                      username=token.get('username') or cfg.get('handle') or '')
        recorded = token.get('scopes')
        if (token.get('scopes_source') == 'response' and type(recorded) is list
                and all(type(value) is str for value in recorded)):
            adapter.granted_scopes = frozenset(recorded)
        adapter.auth_account = cfg.get('account') or '<account>'
        return leave_gate.bind(adapter, account_cfg)

    # ----- 文字数 ----------------------------------------------------------

    @classmethod
    def count_text(cls, text: str) -> int:
        """X の重み付きの長さ（twitter-text v3・`x_text.weighted_length`）。

        conformance（validate.yml の v3 の重み付き計数・extract.yml の URL）の全件と
        合わせてある。合わせきれていない所は `x_text` の冒頭の但し書き。
        """
        return x_text.weighted_length(text)

    COUNT_UNIT = 'X の重み付き'
    # 超えたときの言い方を「284/280（X の重み付き）」にする（`queuefile.length_figure`）。
    LENGTH_FIGURE = True

    def _scrub(self, text) -> str:
        out = str(text)
        if self.access_token:
            out = out.replace(self.access_token, '***')
        return redact_mod.redact(out) or ''

    # ----- 投稿 ------------------------------------------------------------

    def publish(self, post: base.Post, *, dry_run: bool, on_container_created=None,
                before_publish=None) -> base.PublishResult:
        """`POST /2/tweets` を 1 回（添付があれば `x_media` の経路）。

        **月間投稿数の予約が先**（既定 0＝`x_post_budget_exhausted` で要求の前に
        止まる）。X には Threads の container に当たる二段が無いので
        `on_container_created` は呼ばない。
        """
        ts = jst.iso()
        if dry_run:
            return base.PublishResult(None, None, ts, error=None, failure='none')
        community = getattr(post, 'community_id', None)
        if community and (post.reply_to or not COMMUNITY_ID.fullmatch(str(community))):
            # 手前（lint・select・send）が断っているはず。予約も添付の upload もする前に止める。
            return base.PublishResult(None, None, ts, error='x_community_id_invalid',
                                      failure='publish_vetoed')
        from .. import budget_x_posts
        try:
            slot = budget_x_posts.reserve(self.auth_account)
        except budget_x_posts.PostBudgetError as exc:
            return base.PublishResult(None, None, ts, error=str(exc), failure='publish_vetoed')
        try:
            if post.media_manifest:
                from . import x_media
                return x_media.publish(self, post, before_publish=before_publish, slot=slot)
            return self._publish_text(post, ts, before_publish, slot)
        finally:
            slot.finish()

    def _publish_text(self, post, ts, before_publish, slot):
        if not (post.text or '').strip():
            return base.PublishResult(None, None, ts, error='x_text_required',
                                      failure='publish_vetoed')
        if before_publish is not None:
            veto = before_publish()
            if veto:
                return base.PublishResult(None, None, ts, error=str(veto), failure='publish_vetoed')
        body = tweet_body(post)
        try:
            slot.dispatched()
            status, value = request(self, 'POST', '/2/tweets', json_body=body)
        except urllib.error.HTTPError as exc:
            reason = ('provider_rate_limited' if exc.code == 429
                      else 'x_publish_http_' + str(exc.code))
            if 400 <= exc.code < 500 and exc.code != 429:
                known = publish_refusal(exc)
                if known:
                    reason += ': ' + known
            failure = 'publish_definite' if 400 <= exc.code < 500 else 'publish_ambiguous'
            detail = rate_limit(exc) if exc.code == 429 else {}
            return base.PublishResult(None, None, ts, error=reason, failure=failure,
                                      api_diagnostic=detail or None)
        except (httpsafe.EndpointRejected, urllib.error.URLError, TimeoutError, OSError, ValueError, base.AdapterError) as exc:
            # AdapterError（応答が大きすぎる・JSON が dict でない）も「結果不明」として返す（監査 2.14 P2-1）。
            definite = isinstance(exc, httpsafe.EndpointRejected)
            return base.PublishResult(None, None, ts, error=self._scrub(exc) or 'x_publish_failed',
                                      failure='publish_definite' if definite else 'publish_ambiguous')
        identifier = post_id_of(value)
        if status not in (200, 201) or identifier is None:
            return base.PublishResult(None, None, ts, error='x_publish_response_invalid',
                                      failure='publish_ambiguous')
        slot.settle()
        return base.PublishResult(identifier, post_url(self.username, identifier), ts,
                                  error=None, failure='none')

    def delete_post(self, post_id: str) -> dict:
        """`DELETE /2/tweets/:id`（**承認の二段を通った後にだけ呼ばれる**）。"""
        value = str(post_id)
        if not POST_ID.fullmatch(value):
            raise AdapterError('x_post_id_invalid: ' + self.POST_ID_FORM_HINT)
        try:
            status, body = request(self, 'DELETE', '/2/tweets/' + value)
        except urllib.error.HTTPError as exc:
            raise AdapterError('x_retract_http_' + str(exc.code),
                               http_status=exc.code) from None
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            raise AdapterError('x_retract_failed: ' + self._scrub(exc)) from None
        data = body.get('data')
        if status != 200 or not isinstance(data, dict) or data.get('deleted') is not True:
            raise AdapterError('x_retract_unconfirmed')
        return {'deleted': True, 'post_id': value}

    # ----- 反応（設計 3.15.0） ----------------------------------------------

    @classmethod
    def reaction_scope_missing(cls, token, kind, *, undo=False):
        """`.token` の `scopes_source: response` の一覧に要る scope が無ければその名前（不明は None）。"""
        permission = cls.reaction_permission(kind, undo=undo)
        token = token or {}
        granted = token.get('scopes')
        if not permission or token.get('scopes_source') != 'response' or type(granted) is not list:
            return None
        return None if permission in granted else permission

    def _react(self, kind, method, path, *, body=None, expect_key, expect_value):
        permission = self.REACTION_PERMISSIONS[kind]
        if self.granted_scopes is not None and permission not in self.granted_scopes:
            raise base.ScopeMissing(permission, self.auth_account)
        if not POST_ID.fullmatch(self.user_id or ''):
            raise AdapterError('x_user_id_unavailable: thth auth <account> --by <名前> をやり直してください')
        try:
            status, value = request(self, method, path, json_body=body)
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):
                raise base.PermissionMissing(permission, 'x_reaction_http_' + str(exc.code)) from None
            if exc.code == 429:
                detail = rate_limit(exc)
                raise AdapterError('provider_rate_limited'
                                   + (f' {json.dumps(detail, sort_keys=True)}' if detail else ''),
                                   http_status=429) from None
            raise AdapterError('x_reaction_http_' + str(exc.code) + ': 反応したとは言えません',
                               http_status=exc.code) from None
        except (httpsafe.EndpointRejected, urllib.error.URLError, TimeoutError, OSError, ValueError,
                base.AdapterError) as exc:
            raise AdapterError('x_reaction_failed: 結果不明（' + self._scrub(exc) + '）') from None
        data = value.get('data')
        if status not in (200, 201) or not isinstance(data, dict) or data.get(expect_key) is not expect_value:
            raise AdapterError('x_reaction_unconfirmed: 応答に ' + expect_key + ': '
                               + str(expect_value).lower() + ' がありません')
        return data

    def _tweet_id(self, post_id):
        value = str(post_id or '').strip()
        if not POST_ID.fullmatch(value):
            raise AdapterError('x_post_id_invalid: ' + self.POST_ID_FORM_HINT)
        return value

    def repost(self, post_id: str) -> dict:
        """`POST /2/users/:id/retweets` {"tweet_id"} を 1 回。X の再投稿に別の id は返らない（元の id を残す）。"""
        value = self._tweet_id(post_id)
        data = self._react('repost', 'POST', '/2/users/' + self.user_id + '/retweets',
                           body={'tweet_id': value}, expect_key='retweeted', expect_value=True)
        rest = data.get('rest_id') or data.get('id')
        return {'reaction_id': rest if isinstance(rest, str) and POST_ID.fullmatch(rest) else value}

    def unrepost(self, reaction_id: str, *, post_id: str | None = None) -> dict:
        value = self._tweet_id(post_id)
        self._react('repost', 'DELETE', '/2/users/' + self.user_id + '/retweets/' + value,
                    expect_key='retweeted', expect_value=False)
        return {'undone': True}

    def like(self, post_id: str) -> dict:
        value = self._tweet_id(post_id)
        self._react('like', 'POST', '/2/users/' + self.user_id + '/likes',
                    body={'tweet_id': value}, expect_key='liked', expect_value=True)
        return {'reaction_id': value}

    def unlike(self, reaction_id: str, *, post_id: str | None = None) -> dict:
        value = self._tweet_id(post_id)
        self._react('like', 'DELETE', '/2/users/' + self.user_id + '/likes/' + value,
                    expect_key='liked', expect_value=False)
        return {'undone': True}

    # ----- 非公開リスト（3.17.0・`thth list`） -----------------------------------
    #
    # 一次資料（**L1** docs.x.com）: `POST /2/lists`（name 1〜25 字・description 100 字まで・
    # private）・`POST /2/lists/:id/members` {user_id}・`DELETE /2/lists/:id/members/:user_id`・
    # `GET /2/lists/:id/tweets`。scope は作る `list.write`＋`list.read`、足す／外す `list.write`、
    # 読む `list.read`（`tweet.read`・`users.read` はいまのトークンにある）。料金: 作る 0.010 USD・
    # 足す／外す 0.005 USD（`budget_x.list_write`）・読むは返ってきた 1 本 0.005 USD（Posts: Read と
    # 同じ台帳・1 日の本数にも数える）。**公開のリストは作らない**（`private` は常に true）。
    LIST_SCOPES = {'create': ('list.write', 'list.read'), 'add': ('list.write',),
                   'remove': ('list.write',), 'show': ('list.read',)}
    LIST_NAME_MAX = 25
    LIST_DESCRIPTION_MAX = 100
    LIST_TWEETS_DEFAULT_LIMIT = 10
    LIST_TWEETS_MAX_LIMIT = 25
    USERNAME = re.compile(r'[A-Za-z0-9_]{1,15}')

    @classmethod
    def list_scope_missing(cls, token, operation):
        """`.token` の `scopes_source: response` の一覧に要る scope が無ければその名前（不明は None）。"""
        token = token or {}
        granted = token.get('scopes')
        if token.get('scopes_source') != 'response' or type(granted) is not list:
            return None
        return next((name for name in cls.LIST_SCOPES[operation] if name not in granted), None)

    def _list_check_scope(self, operation):
        for name in self.LIST_SCOPES[operation]:
            if self.granted_scopes is not None and name not in self.granted_scopes:
                raise base.ScopeMissing(name, self.auth_account)

    @staticmethod
    def _list_id(list_id):
        value = str(list_id or '').strip()
        if not POST_ID.fullmatch(value):
            raise AdapterError('x_list_id_invalid: リストの id は 19 桁までの数字です')
        return value

    def _list_write(self, method, path, *, body=None, units, expect_key, expect_value, what):
        """リストの書き込み 1 回を `budget_x.list_write` の予約の中で。**再試行しない。**

        断りの応答（4xx・5xx）は書いていない——0 単位で確定。応答が読めなかったら結果不明として
        押さえたまま（uncertain）。2xx で期待の値が無いときは、書けたかもしれないので単位を確定して
        `x_list_unconfirmed` で止める。
        """
        from .. import budget_x
        try:
            with budget_x.list_write(self.auth_account, units) as slot:
                slot.dispatch()
                try:
                    status, value = request(self, method, path, json_body=body)
                except urllib.error.HTTPError as exc:
                    slot.settle(0)
                    if exc.code in (401, 403):
                        raise base.PermissionMissing('list.write', 'x_list_http_' + str(exc.code)) from None
                    if exc.code == 429:
                        detail = rate_limit(exc)
                        raise AdapterError('provider_rate_limited'
                                           + (f' {json.dumps(detail, sort_keys=True)}' if detail else ''),
                                           http_status=429) from None
                    raise AdapterError(what + ': x_list_http_' + str(exc.code), http_status=exc.code) from None
                except (httpsafe.EndpointRejected, urllib.error.URLError, TimeoutError, OSError, ValueError,
                        base.AdapterError) as exc:
                    raise AdapterError(what + ': x_list_failed: 結果不明（' + self._scrub(exc) + '）') from None
                slot.settle(units)
        except budget_x.BudgetError as exc:
            code = str(exc)
            message = ('今月の X の予算（読み取りと合わせて）が足りません。thth admin budget x で確かめてください'
                       if code == 'budget_exhausted' else 'X の予算の予約ができません')
            raise AdapterError(f'{code}: {message}') from None
        data = value.get('data')
        if status not in (200, 201) or not isinstance(data, dict) or (
                expect_key is not None and data.get(expect_key) is not expect_value):
            raise AdapterError(what + ': x_list_unconfirmed: 応答で確かめられません（X の画面で確かめてください）')
        return data

    def list_create(self, name: str, *, description: str | None = None) -> dict:
        """`POST /2/lists` を 1 回。**`private` は常に true**（公開のリストは作らない）。"""
        self._list_check_scope('create')
        body = {'name': name, 'private': True}
        if description:
            body['description'] = description
        data = self._list_write('POST', '/2/lists', body=body, units=self._units('create'),
                                expect_key=None, expect_value=None, what='リストを作る')
        identifier = data.get('id')
        if not isinstance(identifier, str) or not POST_ID.fullmatch(identifier):
            raise AdapterError('リストを作る: x_list_unconfirmed: 応答に id がありません（X の画面で確かめてください）')
        return {'list_id': identifier, 'name': data.get('name') if isinstance(data.get('name'), str) else name,
                'private': True}

    @staticmethod
    def _user_id(user_id):
        value = str(user_id or '').strip()
        if not POST_ID.fullmatch(value):
            raise AdapterError('x_user_id_invalid: 人の id は 19 桁までの数字です')
        return value

    @staticmethod
    def _units(operation):
        from .. import budget_x
        return budget_x.LIST_CREATE_UNITS if operation == 'create' else budget_x.LIST_MANAGE_UNITS

    def list_add_member(self, list_id: str, user_id: str) -> dict:
        """`POST /2/lists/:id/members` {user_id} を 1 回。"""
        self._list_check_scope('add')
        value = self._list_id(list_id)
        member = self._user_id(user_id)
        self._list_write('POST', '/2/lists/' + value + '/members', body={'user_id': member},
                         units=self._units('add'), expect_key='is_member', expect_value=True,
                         what='リストに足す')
        return {'list_id': value, 'user_id': member, 'is_member': True}

    def list_remove_member(self, list_id: str, user_id: str) -> dict:
        """`DELETE /2/lists/:id/members/:user_id` を 1 回。"""
        self._list_check_scope('remove')
        value = self._list_id(list_id)
        member = self._user_id(user_id)
        self._list_write('DELETE', '/2/lists/' + value + '/members/' + member,
                         units=self._units('remove'), expect_key='is_member', expect_value=False,
                         what='リストから外す')
        return {'list_id': value, 'user_id': member, 'is_member': False}

    def user_by_username(self, username: str) -> dict:
        """`GET /2/users/by/username/:username`（handle → id）。本人確認と同じ予約（1 回 0.010 USD）。"""
        from .. import budget_x
        handle = str(username or '').strip().lstrip('@')
        if not self.USERNAME.fullmatch(handle):
            raise AdapterError('x_username_invalid: @ の後は英数と _ の 15 字までです')
        path = '/2/users/by/username/' + handle
        try:
            with budget_x.user_read(self.auth_account):
                budget_x.before_read(path)
                try:
                    status, value = request(self, 'GET', path)
                except urllib.error.HTTPError as exc:
                    raise AdapterError('x_user_lookup_http_' + str(exc.code), http_status=exc.code) from None
                except (httpsafe.EndpointRejected, urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
                    raise AdapterError('x_user_lookup_failed: ' + self._scrub(exc)) from None
                budget_x.observed(value)
        except budget_x.BudgetError as exc:
            code = str(exc)
            message = ('今月の X の予算が足りません。thth admin budget x で確かめてください'
                       if code == 'budget_exhausted' else 'X の読み取りの予約ができません')
            raise AdapterError(f'{code}: {message}') from None
        data = value.get('data')
        if status != 200 or not isinstance(data, dict) or not isinstance(data.get('id'), str) \
                or not POST_ID.fullmatch(data['id']):
            raise AdapterError('x_user_not_found: @' + handle + ' が見つかりません')
        return {'user_id': data['id'], 'username': data.get('username') if isinstance(data.get('username'), str) else handle}

    def list_tweets(self, list_id: str, *, limit: int | None = None) -> list:
        """`GET /2/lists/:id/tweets`（1 回・既定 10 本・最大 25 本）。他人の投稿の読み取りの予約の中で。"""
        self._list_check_scope('show')
        value = self._list_id(list_id)
        cap = self.LIST_TWEETS_DEFAULT_LIMIT if limit is None else limit
        if type(cap) is not int or not 1 <= cap <= self.LIST_TWEETS_MAX_LIMIT:
            raise AdapterError(f'X のリストは 1 回 1〜{self.LIST_TWEETS_MAX_LIMIT} 本です（{limit!r}）'
                               '——有償の読み取りなので範囲外の指定は断ります')
        query = {'max_results': cap, **self.READ_FIELDS}
        body, n = self._paid_get('/2/lists/' + value + '/tweets', query, limit=cap, what='リストの投稿')
        meta = body.get('meta') if isinstance(body.get('meta'), dict) else {}
        # 続きがある（`next_token`）ことだけ覚える。続きは読まない（1 回だけ）。
        self.list_more = bool(meta.get('next_token')) or n > cap
        return self._rows(body)[:cap]

    # ----- 読む口（**読取予算の中でだけ動く**） ------------------------------

    def whoami(self) -> dict:
        """`GET /2/users/me`。**従量の読み取り**なので読取予算の予約を取る。"""
        from .. import budget_x
        path = '/2/users/me'
        with budget_x.user_read(self.auth_account):
            budget_x.before_read(path)
            try:
                status, value = request(self, 'GET', path)
            except urllib.error.HTTPError as exc:
                raise AdapterError('x_whoami_http_' + str(exc.code),
                                   http_status=exc.code) from None
            except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
                raise AdapterError('x_whoami_failed: ' + self._scrub(exc)) from None
            budget_x.observed(value)
        data = value.get('data')
        if status != 200 or not isinstance(data, dict) or not isinstance(data.get('id'), str) or not data['id']:
            raise AdapterError('x_whoami_response_invalid')
        return {'user_id': data['id'], 'username': data.get('username')}

    def recent_posts(self, *, limit: int = 25) -> list:
        """`GET /2/users/:id/tweets`（最小の field）。**読取予算の中でだけ動く。**

        `user_id` は `.token`（`thth auth` が `GET /2/users/me` から書いたもの）を
        使う——ここで暗黙に `whoami()` を叩くと、**1 回の一覧で 2 回課金される**。
        """
        from .. import budget_x
        if not POST_ID.fullmatch(self.user_id or ''):
            raise AdapterError('x_user_id_unavailable: thth auth <account> --by <名前> をやり直してください')
        path = '/2/users/' + self.user_id + '/tweets'
        query = {'max_results': max(5, min(int(limit), 100)),
                 'tweet.fields': 'created_at,text,note_tweet'}
        with budget_x.user_read(self.auth_account):
            budget_x.before_read(path)
            try:
                status, value = request(self, 'GET', path, query=query)
            except urllib.error.HTTPError as exc:
                raise AdapterError('x_posts_http_' + str(exc.code), http_status=exc.code) from None
            except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
                raise AdapterError('x_posts_failed: ' + self._scrub(exc)) from None
            budget_x.observed(value)
        rows = value.get('data', [])
        if status != 200 or not isinstance(rows, list):
            # **「取れなかった」を「取れて 0 件」にしない**（`_get_list` と同じ規律）。
            raise AdapterError('x_posts_response_invalid')
        out = []
        for row in rows:
            if not isinstance(row, dict) or not isinstance(row.get('id'), str) or not row['id']:
                continue
            out.append({'post_id': row['id'], 'timestamp': row.get('created_at'),
                        'url': post_url(self.username, row['id']),
                        'text': full_text(row) or '', 'topic': None})
        return out

    # ----- 他人の投稿を読む口（**Posts: Read の予約の中でだけ動く**） ------------

    def _paid_get(self, path, query, *, limit, what, kind='posts'):
        """1 回の GET を `budget_x.posts_read(account, limit)` の中で投げる。

        `kind='archive'`（4.5.0）は全期間の検索の台帳（`budget_x.archive_read`）で数える。

        戻りは `(value, n_returned)`。**課金の数は応答に入っていた投稿の本数**（呼び手が
        画面に出す本数で切り詰めても、返ってきた本数は返ってきた本数）。予算の断りは
        `AdapterError`（`budget_exhausted`・`x_daily_read_cap` で始まる 1 行）に写す。
        """
        from .. import budget_x
        archive = kind == budget_x.ARCHIVE
        counter = self._archive if archive else self._reads
        reserve = budget_x.archive_read if archive else budget_x.posts_read
        try:
            with reserve(self.auth_account, limit) as slot:
                slot.dispatch()
                counter['calls'] += 1
                try:
                    status, value = request(self, 'GET', path, query=query)
                except urllib.error.HTTPError as exc:
                    # 断りの応答（4xx・5xx）は投稿を返していない——0 本で確定する。
                    # 応答そのものが無い（下の except）は押さえた上限のまま uncertain。
                    slot.settle(0)
                    charged = 0
                    if exc.code == 403:
                        raise base.PermissionMissing('tweet.read', 'x_read_http_403') from None
                    if exc.code == 429:
                        detail = rate_limit(exc)
                        raise AdapterError('provider_rate_limited'
                                           + (f' {json.dumps(detail, sort_keys=True)}' if detail else ''),
                                           http_status=429) from None
                    hint = '（thth auth <account> --by <名前> をやり直してください）' if exc.code == 401 else ''
                    raise AdapterError('x_read_http_' + str(exc.code) + hint,
                                       http_status=exc.code) from None
                except (httpsafe.EndpointRejected, urllib.error.URLError, TimeoutError, OSError, ValueError,
                        base.AdapterError) as exc:
                    # 応答が読めなかった: 台帳は押さえた上限のまま uncertain（多めに数える側）。
                    self._count(limit, counter)
                    raise AdapterError(what + ': x_read_failed: ' + self._scrub(exc)) from None
                data = value.get('data')
                if status != 200 or not (isinstance(data, (list, dict)) or isinstance(value.get('meta'), dict)):
                    self._count(limit, counter)
                    raise AdapterError(what + ': x_read_response_invalid')
                n = len(data) if isinstance(data, list) else (1 if isinstance(data, dict) else 0)
                slot.settle(n)
                charged = slot.count if slot.count is not None else limit
        except budget_x.BudgetError as exc:
            code = str(exc)
            message = {
                'budget_exhausted': '今月の X の読み取り予算（本人確認と合わせて）が足りません。thth admin budget x で確かめてください',
                'x_daily_read_cap': '今日（JST）の X の他人の投稿の読み取りがこの account の上限に達します。'
                                    'thth account set <名前> x_daily_reads <本数> --by <名前> で変えられます',
                'x_archive_daily_read_cap': '今日（JST）の X の全期間の検索がこの account の上限に達します'
                                            '（既定 0＝読まない）。thth account set <名前> x_archive_daily_reads '
                                            '<本数> --by <名前> で変えられます',
                'x_archive_daily_read_cap_unreadable': '台帳の x_archive_daily_reads が読めません',
            }.get(code, 'X の読み取りの予約ができません')
            raise AdapterError(f'{code}: {message}') from None
        self._count(charged, counter)
        return value, n

    def _count(self, n, counter=None):
        from decimal import Decimal
        from .. import budget_x
        counter = self._reads if counter is None else counter
        counter['posts'] += n
        counter['usd'] = str(Decimal(counter['usd']) + budget_x.POSTS_PRICE * n)

    @staticmethod
    def _metric(metrics, key):
        """`public_metrics` の 1 つ。無い・型が違う（bool・負・数でない）は None（0 にしない）。"""
        value = metrics.get(key) if isinstance(metrics, dict) else None
        return value if type(value) is int and value >= 0 else None

    @classmethod
    def _users(cls, value):
        """`includes.users` を id → `{username, followers, posts}` に。

        フォロワー数・投稿数（`public_metrics`）は**候補を選ぶその場でだけ使う**——
        行に載せて返すだけで、台帳・runs・engagements・study には書かない
        （報告 r20261012-4409f401・本文と username と同じ扱い）。
        """
        users = (value.get('includes') or {}).get('users') if isinstance(value.get('includes'), dict) else None
        out = {}
        for user in users or []:
            if isinstance(user, dict) and isinstance(user.get('id'), str):
                metrics = user.get('public_metrics')
                out[user['id']] = {
                    'username': user.get('username') if isinstance(user.get('username'), str) else None,
                    'followers': cls._metric(metrics, 'followers_count'),
                    'posts': cls._metric(metrics, 'tweet_count'),
                }
        return out

    def _message_row(self, row, users):
        """X の 1 本を `Message` の形に写す（`threads._message_row` と同じ鍵）。"""
        identifier = row.get('id')
        user = users.get(row.get('author_id')) or {}
        username = user.get('username')
        # 応答の名前は `referenced_tweets`（10/7 に実機で通った `tweet.fields` の系）。資料は
        # `referenced_posts` と書くようになったので、どちらでも読む。
        refs = row.get('referenced_tweets') or row.get('referenced_posts') or []
        replied = next((ref.get('id') for ref in refs
                        if isinstance(ref, dict) and ref.get('type') == 'replied_to'
                        and isinstance(ref.get('id'), str)), None)
        conversation = row.get('conversation_id') if isinstance(row.get('conversation_id'), str) else None
        return {
            'message_id': identifier,
            'username': username,
            'text': full_text(row),
            'timestamp': row.get('created_at'),
            'replied_to': {'id': replied} if replied else None,
            'root_post': {'id': conversation} if conversation else None,
            'medium': MEDIUM,
            'author_key': base.author_key(MEDIUM, username),
            'reply_deadline': None,
            'permalink': post_url(username, identifier),
            # 投稿者のフォロワー数・投稿数（`user.fields=public_metrics`・同じ呼び出しの
            # 同じ user）。無ければ None。**保存しない**（`_users()` の注記）。
            'author_followers': user.get('followers'),
            'author_posts': user.get('posts'),
            # 返信の数（`public_metrics.reply_count`・検索で欄を頼んだときだけ）。無い・型違いは None。
            'reply_count': self._reply_count(row),
        }

    @staticmethod
    def _reply_count(row):
        metrics = row.get('public_metrics')
        value = metrics.get('reply_count') if isinstance(metrics, dict) else None
        return value if type(value) is int and value >= 0 else None

    def _rows(self, value, users=None):
        data = value.get('data')
        if data is None:
            data = []
        if not isinstance(data, list):
            raise AdapterError('x_read_response_invalid')
        users = self._users(value) if users is None else users
        return [self._message_row(row, users) for row in data
                if isinstance(row, dict) and isinstance(row.get('id'), str) and POST_ID.fullmatch(row['id'])]

    @staticmethod
    def _start_time(since):
        """`since`（ISO）を `start_time` に。recent search の 7 日より古ければ渡さない。"""
        import datetime
        at = jst.parse(since) if isinstance(since, str) else None
        if at is None:
            return None
        utc = at.astimezone(datetime.timezone.utc)
        floor = jst.now_jst().astimezone(datetime.timezone.utc) - datetime.timedelta(days=7) + datetime.timedelta(minutes=1)
        if utc <= floor:
            return None
        return utc.strftime('%Y-%m-%dT%H:%M:%SZ')

    def keyword_search(self, q: str, *, search_type: str = 'RECENT', limit: int = 10,
                       since: str | None = None) -> list:
        """`GET /2/tweets/search/recent`（直近 7 日）。**1 回・最大 25 本。**

        `max_results` は一次資料の下限 10 に合わせる（`max(10, limit)`）。画面には `limit`
        本まで返すが、**課金は応答に入っていた本数**で数える。
        """
        if not isinstance(q, str) or not q.strip():
            raise AdapterError('検索の語が空です')
        if search_type not in self.SEARCH_TYPES:
            raise AdapterError(f"search_type は {' / '.join(self.SEARCH_TYPES)} のどちらかです（{search_type!r}）")
        if type(limit) is not int or not 1 <= limit <= self.KEYWORD_SEARCH_MAX_LIMIT:
            raise AdapterError(f'X の検索は 1 回 1〜{self.KEYWORD_SEARCH_MAX_LIMIT} 本です（{limit!r}）'
                               '——有償の読み取りなので上限を超える指定は断ります')
        query = {'query': q.strip(), 'max_results': max(self.SEARCH_MIN_RESULTS, limit),
                 'sort_order': 'recency' if search_type == 'RECENT' else 'relevancy', **self.READ_FIELDS}
        # 返信の数（`where --no-replies`・3.18.0）。投稿の `public_metrics.reply_count`。欄を足すだけで
        # 呼び出し数も返る本数も変わらない（課金は返った本数）。
        query['tweet.fields'] += ',public_metrics'

        start = self._start_time(since)
        if start:
            query['start_time'] = start
        value, _ = self._paid_get('/2/tweets/search/recent', query,
                                  limit=query['max_results'], what='投稿の検索')
        return self._rows(value)[:limit]

    def fetch_post(self, post_id: str) -> dict:
        """`GET /2/tweets/:id`（1 本・`conversation_id` も取る）。同じ実体の中では 1 回だけ叩く。"""
        value = str(post_id or '').strip()
        if not POST_ID.fullmatch(value):
            raise AdapterError('x_post_id_invalid: ' + self.POST_ID_FORM_HINT)
        if value in self._post_cache:
            return dict(self._post_cache[value])
        body, _ = self._paid_get('/2/tweets/' + value, dict(self.READ_FIELDS), limit=1, what='投稿の取得')
        data = body.get('data')
        if not isinstance(data, dict) or data.get('id') != value:
            raise AdapterError('投稿の取得: x_read_response_invalid')
        row = self._message_row(data, self._users(body))
        self._post_cache[value] = row
        return dict(row)

    @classmethod
    def check_conversation_limit(cls, limit):
        """`thth thread --limit N` の N（10〜50）。範囲外は**要求も予約もせずに**断る。"""
        if type(limit) is not int or not cls.CONVERSATION_MIN <= limit <= cls.CONVERSATION_MAX:
            raise AdapterError(f'X の枝は 1 回 {cls.CONVERSATION_MIN}〜{cls.CONVERSATION_MAX} 本です'
                               f'（{limit!r}）——有償の読み取りなので範囲外の指定は断ります')
        return limit

    def conversation(self, post_id: str, *, since: str | None = None,
                     limit: int | None = None) -> list:
        """枝（`conversation_id:<id>` の recent search・**既定 50 本・`limit` で 10〜50**・直近 7 日）。

        根（`post_id` 自身）は行に入れない（`thread_read` は根を `fetch_post` で別に引く）。
        上限を超えた分は読まない（`read_cost()['truncated']`）。`limit` は 1 日 60 本の中で
        配分するため（masaru 10/12 決定 5）。課金は返ってきた本数（予約は `limit` 本）。
        """
        cap = self.CONVERSATION_MAX if limit is None else self.check_conversation_limit(limit)
        root = self.fetch_post(post_id)
        conversation = (root.get('root_post') or {}).get('id') or root['message_id']
        query = {'query': 'conversation_id:' + conversation, 'max_results': cap,
                 'sort_order': 'recency', **self.READ_FIELDS}
        start = self._start_time(since)
        if start:
            query['start_time'] = start
        value, n = self._paid_get('/2/tweets/search/recent', query,
                                  limit=cap, what='枝の取得')
        rows = [row for row in self._rows(value) if row['message_id'] != root['message_id']]
        meta = value.get('meta') if isinstance(value.get('meta'), dict) else {}
        if meta.get('next_token') or n > cap:
            self._reads['truncated'] = True
        return rows[:cap]

    # ----- 自分の投稿の数字（Owned Reads・**owned_read の予約の中でだけ動く**） ----

    def owned_metrics(self, post_ids, *, now=None) -> dict:
        """自分の投稿の公開の数字を `GET /2/tweets?ids=…` で読む（100 本ずつ 1 回）。

        戻り: `{'by_post': {post_id: {'metrics': {...}, 'available': [...]}}, 'rejected': n,
        'api_errors': n, 'not_returned': [post_id...], 'posts': n, 'requests': n, 'usd_estimate': str}`。
        `by_post` の値は `insights()` と同じ形（`adapter_base.metrics_of` で開ける）。取れなかった
        指標は入れない（0 と混ぜない）。**`author_id` が自分でない行は `x_metrics` が落とす**——
        渡された id が他人の投稿でも、数字は台帳に入らない（課金は返ってきた本数のまま）。
        """
        import datetime
        from .. import x_metrics
        ids = []
        for value in post_ids:
            value = str(value or '').strip()
            if not POST_ID.fullmatch(value):
                raise AdapterError('x_post_id_invalid: ' + self.POST_ID_FORM_HINT)
            if value not in ids:
                ids.append(value)
        out = {'by_post': {}, 'rejected': 0, 'api_errors': 0, 'not_returned': [],
               'posts': 0, 'requests': 0, 'usd_estimate': '0'}
        if not ids:
            return out
        if not re.fullmatch(r'[1-9][0-9]{0,19}', self.user_id or ''):
            raise AdapterError('x_user_id_unavailable: thth auth <account> --by <名前> をやり直してください')
        at = now if now is not None else jst.now_jst()
        stamp = at.astimezone(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
        for start in range(0, len(ids), self.OWNED_BATCH):
            chunk = ids[start:start + self.OWNED_BATCH]
            value = self._owned_get(chunk)
            if value.get('data') is not None:
                try:
                    report = x_metrics.normalize_owned_metrics(
                        value, connected_user_id=self.user_id, observed_at=stamp, now=stamp)
                except x_metrics.XMetricsError as exc:
                    raise AdapterError('自分の投稿の数字: x_owned_response_invalid: ' + str(exc)) from None
                out['rejected'] += report['coverage']['rejected_records']
                out['api_errors'] += report['coverage']['api_error_count']
                for record in report['records']:
                    if record['post_id'] not in chunk:
                        continue
                    metrics = {name: item['value'] for name, item in record['metrics'].items()
                               if item['value'] is not None}
                    out['by_post'][record['post_id']] = {'metrics': metrics,
                                                         'available': list(self.OWNED_METRICS)}
            else:
                errors = value.get('errors')
                out['api_errors'] += len(errors) if isinstance(errors, list) else 0
            out['not_returned'] += [pid for pid in chunk if pid not in out['by_post']]
        out['posts'] = self._owned['posts']
        out['requests'] = self._owned['calls']
        out['usd_estimate'] = self._owned['usd']
        return out

    def _owned_get(self, chunk):
        """1 回の `GET /2/tweets?ids=…` を `budget_x.owned_read(account, len(chunk))` の中で。

        作法は `_paid_get` と同じ: 断りの応答（4xx・5xx）は 0 本で確定、応答が読めなければ
        押さえた上限のまま uncertain。全部が見つからない応答（`data` 無し・`errors` だけ）は
        0 本で確定する（投稿は返っていない）。
        """
        from decimal import Decimal
        from .. import budget_x
        limit = len(chunk)
        query = {'ids': ','.join(chunk), 'tweet.fields': self.OWNED_FIELDS}
        charged = limit
        try:
            with budget_x.owned_read(self.auth_account, limit) as slot:
                slot.dispatch()
                self._owned['calls'] += 1
                try:
                    status, value = request(self, 'GET', '/2/tweets', query=query)
                except urllib.error.HTTPError as exc:
                    slot.settle(0)
                    charged = 0
                    if exc.code == 403:
                        raise base.PermissionMissing('tweet.read', 'x_owned_read_http_403') from None
                    if exc.code == 429:
                        detail = rate_limit(exc)
                        raise AdapterError('provider_rate_limited'
                                           + (f' {json.dumps(detail, sort_keys=True)}' if detail else ''),
                                           http_status=429) from None
                    hint = '（thth auth <account> --by <名前> をやり直してください）' if exc.code == 401 else ''
                    raise AdapterError('自分の投稿の数字: x_owned_read_http_' + str(exc.code) + hint,
                                       http_status=exc.code) from None
                except (httpsafe.EndpointRejected, urllib.error.URLError, TimeoutError, OSError, ValueError,
                        base.AdapterError) as exc:
                    raise AdapterError('自分の投稿の数字: x_read_failed: ' + self._scrub(exc)) from None
                data = value.get('data')
                if status == 200 and isinstance(data, list):
                    n = len(data)
                elif status == 200 and isinstance(data, dict):
                    n = 1
                elif status == 200 and data is None and isinstance(value.get('errors'), list):
                    n = 0
                else:
                    raise AdapterError('自分の投稿の数字: x_read_response_invalid')
                slot.settle(n)
                if slot.count is None:
                    raise AdapterError('自分の投稿の数字: x_read_response_invalid（頼んだより多い）')
                charged = n
        except budget_x.BudgetError as exc:
            code = str(exc)
            message = ('今月の X の読み取り予算（本人確認・他人の投稿と合わせて）が足りません。'
                       'thth admin budget x で確かめてください') if code == 'budget_exhausted' else 'X の読み取りの予約ができません'
            raise AdapterError(f'{code}: {message}') from None
        self._owned['posts'] += charged
        self._owned['usd'] = str(Decimal(self._owned['usd']) + budget_x.OWNED_PRICE * charged)
        return value

    # ----- コミュニティの検索（3.19.0・**community_read の予約の中でだけ動く**） ----
    #
    # 一次資料（**L1** docs.x.com search-communities）: `GET /2/communities/search`・`query`
    # （1〜4096 字）・`max_results`（10〜100・既定 10）・`community.fields`（access・created_at・
    # description・id・join_policy・member_count・name）。認可は tweet.read＋users.read（いまの
    # トークンにある）。料金表 Community: Read 0.005 USD／resource。**参加しているかを示す欄は
    # 無い**（is_member 等は資料に無い）。参加の API も資料に無い（10/13 確認）。
    COMMUNITY_FIELDS = 'access,created_at,description,id,join_policy,member_count,name'
    COMMUNITY_SEARCH_MIN = 10
    COMMUNITY_SEARCH_DEFAULT_LIMIT = 10
    COMMUNITY_SEARCH_MAX_LIMIT = 25
    COMMUNITY_QUERY_MAX = 4096
    COMMUNITY_DESCRIPTION_MAX = 280

    def community_search(self, q: str, *, limit: int | None = None) -> list:
        """`GET /2/communities/search` を 1 回（既定 10 件・最大 25 件）。続きは読まない。

        `max_results` は資料の下限 10 に合わせる（`max(10, limit)`）。予約はその数で押さえ、
        **課金は応答に入っていた件数**で確定する（画面に出すのは `limit` 件まで）。
        行は公開の欄だけ（id・名前・説明・人数・join_policy・access・作成日）。保存しない。
        """
        from decimal import Decimal
        from .. import budget_x
        text = q.strip() if isinstance(q, str) else ''
        if not text:
            raise AdapterError('検索の語が空です')
        if len(text) > self.COMMUNITY_QUERY_MAX:
            raise AdapterError(f'検索の語は {self.COMMUNITY_QUERY_MAX} 字までです')
        cap = self.COMMUNITY_SEARCH_DEFAULT_LIMIT if limit is None else limit
        if type(cap) is not int or not 1 <= cap <= self.COMMUNITY_SEARCH_MAX_LIMIT:
            raise AdapterError(f'X のコミュニティの検索は 1 回 1〜{self.COMMUNITY_SEARCH_MAX_LIMIT} 件です'
                               f'（{limit!r}）——有償の読み取りなので範囲外の指定は断ります')
        reserve = max(self.COMMUNITY_SEARCH_MIN, cap)
        query = {'query': text, 'max_results': reserve, 'community.fields': self.COMMUNITY_FIELDS}
        # 数える件数: 発射する前に断られたら 0・発射して応答が読めなければ押さえた数
        # （台帳の uncertain と同じ・多めに数える側）・断りの応答は 0・読めたら応答の件数。
        charged = 0
        self.community_more = False
        try:
            with budget_x.community_read(self.auth_account, reserve) as slot:
                slot.dispatch()
                charged = reserve
                self._communities['calls'] += 1
                try:
                    status, value = request(self, 'GET', '/2/communities/search', query=query)
                except urllib.error.HTTPError as exc:
                    slot.settle(0)
                    charged = 0
                    if exc.code == 429:
                        detail = rate_limit(exc)
                        raise AdapterError('provider_rate_limited'
                                           + (f' {json.dumps(detail, sort_keys=True)}' if detail else ''),
                                           http_status=429) from None
                    hint = '（thth auth <account> --by <名前> をやり直してください）' if exc.code == 401 else ''
                    known = publish_refusal(exc) if 400 <= exc.code < 500 else None
                    raise AdapterError('x_community_search_http_' + str(exc.code) + hint
                                       + (': ' + known if known else ''), http_status=exc.code) from None
                except (httpsafe.EndpointRejected, urllib.error.URLError, TimeoutError, OSError, ValueError,
                        base.AdapterError) as exc:
                    raise AdapterError('コミュニティの検索: x_read_failed: 結果不明（' + type(exc).__name__ + ': ' + self._scrub(exc) + '）') from None
                data = value.get('data')
                if status == 200 and isinstance(data, list):
                    n = len(data)
                elif status == 200 and data is None and (isinstance(value.get('meta'), dict)
                                                         or isinstance(value.get('errors'), list)):
                    n = 0
                else:
                    raise AdapterError('コミュニティの検索: x_read_response_invalid')
                slot.settle(n)
                if slot.count is None:
                    raise AdapterError('コミュニティの検索: x_read_response_invalid（頼んだより多い）')
                charged = n
        except budget_x.BudgetError as exc:
            code = str(exc)
            message = ('今月の X の予算（読み取りと合わせて）が足りません。thth admin budget x で確かめてください'
                       if code == 'budget_exhausted' else 'X の読み取りの予約ができません')
            raise AdapterError(f'{code}: {message}') from None
        finally:
            self._communities['count'] += charged
            self._communities['usd'] = str(Decimal(self._communities['usd'])
                                           + budget_x.COMMUNITY_PRICE * charged)
        meta = value.get('meta') if isinstance(value.get('meta'), dict) else {}
        self.community_more = bool(meta.get('next_token')) or n > cap
        rows = []
        for row in data or []:
            item = self._community_row(row)
            if item is not None:
                rows.append(item)
        return rows[:cap]

    @classmethod
    def _community_row(cls, row):
        """1 つのコミュニティを公開の欄だけの行に。id が数字でなければ落とす。型違いの欄は None。"""
        if not isinstance(row, dict) or not isinstance(row.get('id'), str) or not COMMUNITY_ID.fullmatch(row['id']):
            return None

        def text(key, maximum):
            value = row.get(key)
            if not isinstance(value, str):
                return None
            value = ' '.join(value.split())
            return value[:maximum] if value else None
        members = row.get('member_count')
        return {
            'community_id': row['id'],
            'name': text('name', 100),
            'description': text('description', cls.COMMUNITY_DESCRIPTION_MAX),
            'member_count': members if type(members) is int and members >= 0 else None,
            'join_policy': text('join_policy', 40),
            'access': text('access', 40),
            'created_at': text('created_at', 40),
            'url': 'https://x.com/i/communities/' + row['id'],
        }

    def community_read_cost(self) -> dict:
        """この実体で読んだコミュニティの件数と推定 USD・今月の数字（画面と `--json`）。"""
        from decimal import Decimal
        from .. import budget_x
        try:
            whole = budget_x.report()
            month = {'month_utc': whole['month_utc'],
                     'month_used_usd': budget_x.decimal_text(
                         Decimal(whole['spent_estimate_usd']) + Decimal(whole['held_usd'])),
                     'cap_usd': whole['cap_usd']}
        except (budget_x.BudgetError, OSError, ValueError, TypeError):
            month = {'month_utc': None, 'month_used_usd': None, 'cap_usd': None}
        return {'communities': self._communities['count'], 'usd_estimate': self._communities['usd'],
                'requests': self._communities['calls'],
                'price_usd_per_community': str(budget_x.COMMUNITY_PRICE),
                'price_version': budget_x.COMMUNITY_PRICE_VERSION,
                'daily_read_cap': 'not_counted',
                'basis': 'estimate_not_actual_charge', **month}

    # ----- トレンドと語ごとの投稿数（3.19.0・**request_read の予約の中でだけ動く**） ----
    #
    # 一次資料（**L1** docs.x.com）: `GET /2/trends/by/woeid/:woeid`（`max_trends` 1〜50・既定 20・
    # `trend.fields` trend_name・tweet_count）、`GET /2/tweets/counts/recent`（`query` 1〜4096 字・
    # rate limit の表では 512 字・`granularity` minute／hour／day・直近 7 日）。料金表: Trends
    # 0.010 USD／request・Counts: Recent 0.005 USD／request。どちらも OAuth2UserToken か
    # BearerToken（資料の security）。rate limit の表は app ごとの欄だけ（75／15 分・300／15 分）で
    # 利用者ごとの欄は「—」——利用者のトークンで通るかは実機で未確認。
    TRENDS_DEFAULT = 20
    TRENDS_MAX = 50
    COUNTS_QUERY_MAX = 512
    COUNTS_GRANULARITIES = ('hour', 'day')

    def _request_read(self, kind, path, query, *, what):
        """要求 1 回 = 1 単位の GET を `budget_x.request_read(kind, account)` の中で。戻りは応答の dict。"""
        from .. import budget_x
        try:
            with budget_x.request_read(kind, self.auth_account) as slot:
                slot.dispatch()
                self._requests[kind] = self._requests.get(kind, 0) + 1
                try:
                    status, value = request(self, 'GET', path, query=query)
                except urllib.error.HTTPError as exc:
                    slot.settle(0)
                    self._requests[kind] -= 1
                    if exc.code == 429:
                        detail = rate_limit(exc)
                        raise AdapterError('provider_rate_limited'
                                           + (f' {json.dumps(detail, sort_keys=True)}' if detail else ''),
                                           http_status=429) from None
                    hint = '（thth auth <account> --by <名前> をやり直してください）' if exc.code == 401 else ''
                    known = publish_refusal(exc) if 400 <= exc.code < 500 else None
                    raise AdapterError('x_' + kind + '_http_' + str(exc.code) + hint
                                       + (': ' + known if known else ''), http_status=exc.code) from None
                except (httpsafe.EndpointRejected, urllib.error.URLError, TimeoutError, OSError, ValueError,
                        base.AdapterError) as exc:
                    raise AdapterError(what + ': x_read_failed: 結果不明（' + type(exc).__name__ + ': ' + self._scrub(exc) + '）') from None
                if status != 200 or not (isinstance(value.get('data'), list) or value.get('data') is None
                                         and (isinstance(value.get('meta'), dict) or isinstance(value.get('errors'), list))):
                    raise AdapterError(what + ': x_read_response_invalid')
                slot.settle(1)
        except budget_x.BudgetError as exc:
            code = str(exc)
            message = ('今月の X の予算（読み取りと合わせて）が足りません。thth admin budget x で確かめてください'
                       if code == 'budget_exhausted' else 'X の読み取りの予約ができません')
            raise AdapterError(f'{code}: {message}') from None
        return value

    def trends(self, woeid: int, *, max_trends: int | None = None) -> list:
        """`GET /2/trends/by/woeid/:woeid` を 1 回（既定 20・最大 50）。行は名前と投稿数だけ。"""
        if type(woeid) is not int or not 1 <= woeid <= 2 ** 31 - 1:
            raise AdapterError('woeid は正の整数です')
        cap = self.TRENDS_DEFAULT if max_trends is None else max_trends
        if type(cap) is not int or not 1 <= cap <= self.TRENDS_MAX:
            raise AdapterError(f'max_trends は 1〜{self.TRENDS_MAX} です（{max_trends!r}）')
        from .. import budget_x
        value = self._request_read(budget_x.TRENDS, '/2/trends/by/woeid/' + str(woeid),
                                   {'max_trends': cap, 'trend.fields': 'trend_name,tweet_count'},
                                   what='トレンド')
        out = []
        for row in value.get('data') or []:
            if not isinstance(row, dict) or not isinstance(row.get('trend_name'), str):
                continue
            name = ' '.join(row['trend_name'].split())[:100]
            if not name:
                continue
            count = row.get('tweet_count')
            out.append({'trend_name': name,
                        'tweet_count': count if type(count) is int and count >= 0 else None})
        return out[:cap]

    def post_counts(self, q: str, *, granularity: str = 'day') -> dict:
        """`GET /2/tweets/counts/recent` を 1 回（直近 7 日・続きは読まない）。区間ごとの投稿数。"""
        text = q.strip() if isinstance(q, str) else ''
        if not text:
            raise AdapterError('検索の語が空です')
        if len(text) > self.COUNTS_QUERY_MAX:
            raise AdapterError(f'語は {self.COUNTS_QUERY_MAX} 字までです（{len(text)} 字）')
        if granularity not in self.COUNTS_GRANULARITIES:
            raise AdapterError(f"granularity は {' / '.join(self.COUNTS_GRANULARITIES)} のどちらかです")
        from .. import budget_x
        value = self._request_read(budget_x.COUNTS, '/2/tweets/counts/recent',
                                   {'query': text, 'granularity': granularity}, what='投稿数')
        buckets = []
        for row in value.get('data') or []:
            if not isinstance(row, dict):
                continue
            count = row.get('post_count', row.get('tweet_count'))
            start, end = row.get('start'), row.get('end')
            if not isinstance(start, str) or not isinstance(end, str) or type(count) is not int or count < 0:
                continue
            buckets.append({'start': start[:40], 'end': end[:40], 'post_count': count})
        meta = value.get('meta') if isinstance(value.get('meta'), dict) else {}
        total = meta.get('total_post_count', meta.get('total_tweet_count'))
        return {'buckets': buckets,
                'total_post_count': total if type(total) is int and total >= 0 else None,
                'more': bool(meta.get('next_token'))}

    # ----- 全期間の検索と件数（4.5.0・**人の居る道でだけ・予算の中でだけ**） ----------

    ARCHIVE_PER_CALL_MIN = 10      # 一次資料の `max_results` の下限
    ARCHIVE_PER_CALL_MAX = 100     # 一次資料は 500 まで。台帳の 1 回の上限（100）に合わせて小さく
    ARCHIVE_PER_CALL_DEFAULT = 10
    ARCHIVE_TOTAL_MAX = 500        # 1 回の命令で辿る総本数の上限（曲 1 つに 20 本の想定）
    ARCHIVE_TOTAL_DEFAULT = 20
    ARCHIVE_SORTS = ('recency', 'relevancy')
    # ページの数の上限（空のページに `next_token` が付いて続くことがある・空のページは 0 本で課金なし）。
    ARCHIVE_PAGES_DEFAULT = 5
    ARCHIVE_PAGES_MAX = 50
    # 全期間の検索の rate limit は 1 秒に 1 回（app・利用者とも）。ページの間はこれだけ空ける。
    ARCHIVE_PAGE_PAUSE_SECONDS = 1.1
    COUNTS_ALL_PAGES_DEFAULT = 1
    COUNTS_ALL_PAGES_MAX = 300     # 2006-03 からの全期間は約 245 ページ（31 日ごと）
    COUNTS_ALL_GRANULARITIES = ('day', 'hour')

    @staticmethod
    def _refuse_unattended():
        from .. import x_archive
        if x_archive.is_unattended():
            raise AdapterError('x_archive_unattended: 全期間の読み取りは人が打った命令でだけ動きます'
                               '（timer・collect・morning・worker からは叩きません）')

    @classmethod
    def check_archive_limits(cls, per_call, max_total):
        """1 回の本数（10〜100）と総本数（10〜500・1 回の本数以上）。範囲外は**要求も予約もせずに**断る。"""
        if type(per_call) is not int or not cls.ARCHIVE_PER_CALL_MIN <= per_call <= cls.ARCHIVE_PER_CALL_MAX:
            raise AdapterError(f'x_archive_limit: 1 回の本数は {cls.ARCHIVE_PER_CALL_MIN}〜'
                               f'{cls.ARCHIVE_PER_CALL_MAX} です（{per_call!r}）——有償の読み取りなので範囲外は断ります')
        if type(max_total) is not int or not cls.ARCHIVE_PER_CALL_MIN <= max_total <= cls.ARCHIVE_TOTAL_MAX:
            raise AdapterError(f'x_archive_limit: 総本数は {cls.ARCHIVE_PER_CALL_MIN}〜'
                               f'{cls.ARCHIVE_TOTAL_MAX} です（{max_total!r}）——有償の読み取りなので範囲外は断ります')
        if per_call > max_total:
            raise AdapterError(f'x_archive_limit: 1 回の本数（{per_call}）は総本数（{max_total}）以下にしてください')
        return per_call, max_total

    def _archive_row(self, row):
        """出典に使う形（id・URL・作られた時刻・本文・author の handle）。"""
        return {'post_id': row['message_id'], 'url': row['permalink'], 'created_at': row['timestamp'],
                'text': row['text'], 'author_username': row['username'],
                'conversation_id': (row.get('root_post') or {}).get('id')}

    def archive_search(self, q: str, *, per_call: int = ARCHIVE_PER_CALL_DEFAULT,
                       max_total: int = ARCHIVE_TOTAL_DEFAULT, start_time: str, end_time: str | None = None,
                       sort_order: str = 'recency', max_pages: int = ARCHIVE_PAGES_DEFAULT,
                       sleep=None) -> dict:
        """`GET /2/tweets/search/all` を `next_token` で辿る。**総本数は `max_total` を超えない。**

        1 ページの要求は `min(per_call, 残り)` 本（下限 10）。残りが 10 未満になったら辿らない
        （X は 10 未満を頼めない＝頼めば上限を超えて課金されうる）。ページごとに予約→発射→
        返った本数で確定。2 ページ目より後の断り・結果不明は**投げ直さずに止め**、それまでの行を
        返す（`stopped`）。1 ページ目の断りは上げる。
        """
        import time
        from .. import budget_x
        self._refuse_unattended()
        per_call, max_total = self.check_archive_limits(per_call, max_total)
        if type(max_pages) is not int or not 1 <= max_pages <= self.ARCHIVE_PAGES_MAX:
            raise AdapterError(f'x_archive_limit: ページは 1〜{self.ARCHIVE_PAGES_MAX} です（{max_pages!r}）')
        if sort_order not in self.ARCHIVE_SORTS:
            raise AdapterError(f"sort_order は {' / '.join(self.ARCHIVE_SORTS)} のどちらかです")
        text = q.strip() if isinstance(q, str) else ''
        if not text or len(text) > 1024:
            raise AdapterError('x_archive_query: 語は 1〜1024 字です')
        pause = time.sleep if sleep is None else sleep
        base_query = {'query': text, 'start_time': start_time, 'sort_order': sort_order, **self.READ_FIELDS}
        if end_time:
            base_query['end_time'] = end_time
        rows, seen, token, pages, stopped, charged = [], set(), None, 0, None, 0
        while pages < max_pages:
            want = min(per_call, max_total - charged)
            if want < self.ARCHIVE_PER_CALL_MIN:
                break
            query = dict(base_query, max_results=want)
            if token:
                query['next_token'] = token
                pause(self.ARCHIVE_PAGE_PAUSE_SECONDS)
            try:
                value, n = self._paid_get('/2/tweets/search/all', query, limit=want,
                                          what='全期間の検索', kind=budget_x.ARCHIVE)
            except (AdapterError, base.AdapterError) as exc:
                if pages == 0:
                    raise
                stopped = str(exc).split(':', 1)[0].strip() or 'x_read_failed'
                break
            pages += 1
            charged += n
            for row in self._rows(value):
                if row['message_id'] not in seen:
                    seen.add(row['message_id'])
                    rows.append(self._archive_row(row))
            meta = value.get('meta') if isinstance(value.get('meta'), dict) else {}
            token = meta.get('next_token') if isinstance(meta.get('next_token'), str) and meta.get('next_token') else None
            if not token:
                break
        more = bool(token)
        self._archive['truncated'] = more
        return {'posts': rows[:max_total], 'pages': pages, 'more': more, 'stopped': stopped}

    def archive_counts(self, q: str, *, start_time: str, end_time: str | None = None,
                       granularity: str = 'day', max_pages: int = COUNTS_ALL_PAGES_DEFAULT,
                       pages_needed: int | None = None, sleep=None) -> dict:
        """`GET /2/tweets/counts/all`（1 回 0.010 USD・31 日ごとに 1 ページ）を `min(max_pages, pages_needed)` まで。

        `pages_needed` は窓を数えきるのに要るページ数（`x_archive.counts_pages_needed`・CLI の見積もり）。
        X が窓の外まで `next_token` を返し続けても、見積もりより多くは叩かない。
        """
        import time
        from .. import budget_x
        self._refuse_unattended()
        if type(max_pages) is not int or not 1 <= max_pages <= self.COUNTS_ALL_PAGES_MAX:
            raise AdapterError(f'x_archive_limit: ページは 1〜{self.COUNTS_ALL_PAGES_MAX} です（{max_pages!r}）')
        if granularity not in self.COUNTS_ALL_GRANULARITIES:
            raise AdapterError(f"granularity は {' / '.join(self.COUNTS_ALL_GRANULARITIES)} のどちらかです")
        text = q.strip() if isinstance(q, str) else ''
        if not text or len(text) > 1024:
            raise AdapterError('x_archive_query: 語は 1〜1024 字です')
        pause = time.sleep if sleep is None else sleep
        base_query = {'query': text, 'start_time': start_time, 'granularity': granularity}
        if end_time:
            base_query['end_time'] = end_time
        if pages_needed is not None and (type(pages_needed) is not int or pages_needed < 1):
            raise AdapterError(f'x_archive_limit: pages_needed は 1 以上です（{pages_needed!r}）')
        stop_at = max_pages if pages_needed is None else min(max_pages, pages_needed)
        buckets, total, token, pages, stopped = [], 0, None, 0, None
        while pages < stop_at:
            query = dict(base_query)
            if token:
                query['next_token'] = token
                pause(self.ARCHIVE_PAGE_PAUSE_SECONDS)
            try:
                value = self._request_read(budget_x.COUNTS_ALL, '/2/tweets/counts/all', query,
                                           what='全期間の投稿数')
            except AdapterError as exc:
                if pages == 0:
                    raise
                stopped = str(exc).split(':', 1)[0].strip() or 'x_read_failed'
                break
            pages += 1
            for row in value.get('data') or []:
                if not isinstance(row, dict):
                    continue
                count = row.get('post_count', row.get('tweet_count'))
                start, end = row.get('start'), row.get('end')
                if isinstance(start, str) and isinstance(end, str) and type(count) is int and count >= 0:
                    buckets.append({'start': start[:40], 'end': end[:40], 'post_count': count})
            meta = value.get('meta') if isinstance(value.get('meta'), dict) else {}
            page_total = meta.get('total_post_count', meta.get('total_tweet_count'))
            total += page_total if type(page_total) is int and page_total >= 0 else 0
            token = meta.get('next_token') if isinstance(meta.get('next_token'), str) and meta.get('next_token') else None
            if not token:
                break
        buckets.sort(key=lambda row: row['start'])
        return {'buckets': buckets, 'total_post_count': total if pages else None,
                'pages': pages, 'more': bool(token), 'stopped': stopped}

    def archive_read_cost(self) -> dict:
        """この実体の全期間の検索の本数と推定 USD・今日（全期間の枠）と今月の数字。"""
        from .. import budget_x
        try:
            status = budget_x.archive_status(self.auth_account)
        except (budget_x.BudgetError, OSError, ValueError, TypeError):
            status = {'today': None, 'daily_cap': None, 'day_jst': None, 'month_utc': None,
                      'month_used_usd': None, 'cap_usd': None}
        counts = self._requests.get(budget_x.COUNTS_ALL, 0)
        return {'posts': self._archive['posts'], 'requests': self._archive['calls'],
                'usd_estimate': self._archive['usd'], 'truncated': self._archive['truncated'],
                'counts_requests': counts,
                'counts_usd_estimate': budget_x.decimal_text(budget_x.COUNTS_ALL_PRICE * counts),
                'price_usd_per_post': str(budget_x.ARCHIVE_PRICE),
                'price_usd_per_counts_request': str(budget_x.COUNTS_ALL_PRICE),
                'price_version': budget_x.ARCHIVE_PRICE_VERSION,
                'daily_cap_key': budget_x.ARCHIVE_DAILY_READS_KEY,
                'basis': 'estimate_not_actual_charge', **status}

    def request_read_cost(self, kind) -> dict:
        """この実体で投げた要求の回数と推定 USD・今月の数字（`trends`・`counts` の画面と `--json`）。"""
        from decimal import Decimal
        from .. import budget_x
        price = budget_x._LEDGERS[kind][1]
        n = self._requests.get(kind, 0)
        try:
            whole = budget_x.report()
            month = {'month_utc': whole['month_utc'],
                     'month_used_usd': budget_x.decimal_text(
                         Decimal(whole['spent_estimate_usd']) + Decimal(whole['held_usd'])),
                     'cap_usd': whole['cap_usd']}
        except (budget_x.BudgetError, OSError, ValueError, TypeError):
            month = {'month_utc': None, 'month_used_usd': None, 'cap_usd': None}
        return {'requests': n, 'usd_estimate': budget_x.decimal_text(price * n),
                'price_usd_per_request': str(price), 'price_version': budget_x._LEDGERS[kind][2],
                'daily_read_cap': 'not_counted', 'basis': 'estimate_not_actual_charge', **month}

    def read_cost(self) -> dict:
        """この実体で読んだ他人の投稿の本数と推定 USD・今日と今月の数字（画面と `--json`）。"""
        from .. import budget_x
        try:
            status = budget_x.posts_status(self.auth_account)
        except (budget_x.BudgetError, OSError, ValueError, TypeError):
            status = {'today': None, 'daily_cap': None, 'day_jst': None, 'month_utc': None,
                      'month_used_usd': None, 'cap_usd': None}
        return {'posts': self._reads['posts'], 'usd_estimate': self._reads['usd'],
                'requests': self._reads['calls'], 'truncated': self._reads['truncated'],
                'price_usd_per_post': str(budget_x.POSTS_PRICE),
                'price_version': budget_x.POSTS_PRICE_VERSION,
                'search_window_days': self.SEARCH_WINDOW_DAYS,
                'basis': 'estimate_not_actual_charge', **status}

    def refresh_token(self, token):
        raise NotImplementedError(
            'X の更新は thth refresh / thth maintain が adapters.auth_x で回す')
