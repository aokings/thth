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

**書く口の上限**: `thth admin budget x-posts --monthly <本数>`（`thth/budget_x_posts.py`）。
既定 0＝**投稿しない**。予約は要求の前・確定は応答の後で、**結果不明は自動で
投げ直さない**（数え上げでは「不明」として別に残る）。

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
from . import base

MEDIUM = 'x'
DEFAULT_BASE_URL = 'https://api.x.com'
BASE_URL_ENV = 'THTH_X_BASE_URL'
DEFAULT_TIMEOUT_SECONDS = 10.0

# **本文の数え方**（設計 2.14.0 §2）。公式ページに 280 の明記は無く（"Text Length
# Rule: Not specified"）、X の重み付き計数（CJK を 2 と数える等）も実測していない。
# **暫定でコードポイント 280** とし、lint は毎回その旨を 1 行添える——「確かめて
# いない」を「確かめた」の顔で出さない。重み付けは実機の測定項目。
MAX_TEXT_CODEPOINTS = 280
LENGTH_NOTE = 'warning: x_length_weighting_unverified'

REPLY_SETTINGS = ('following', 'mentionedUsers', 'subscribers', 'verified')
POST_ID = re.compile(r'[0-9]{1,19}')


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
# 文面は X の開発者フォーラムで報告されている形（一次資料には無い・L3）。
PUBLISH_REFUSALS = (
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
    READ_FIELDS = {'tweet.fields': 'created_at,author_id,conversation_id,referenced_tweets',
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
        """**暫定の数え方**: Unicode コードポイント（`MAX_TEXT_CODEPOINTS` の但し書き）。

        X の重み付き計数を実測していないので、**重い側に倒さない代わりに、
        数え方が未確認であることを lint が毎回言う**（`LENGTH_NOTE`）。
        """
        return len(text)

    COUNT_UNIT = 'codepoint（暫定・重み付け未確認）'

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
                 'tweet.fields': 'created_at,text'}
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
                        'text': row.get('text') or '', 'topic': None})
        return out

    # ----- 他人の投稿を読む口（**Posts: Read の予約の中でだけ動く**） ------------

    def _paid_get(self, path, query, *, limit, what):
        """1 回の GET を `budget_x.posts_read(account, limit)` の中で投げる。

        戻りは `(value, n_returned)`。**課金の数は応答に入っていた投稿の本数**（呼び手が
        画面に出す本数で切り詰めても、返ってきた本数は返ってきた本数）。予算の断りは
        `AdapterError`（`budget_exhausted`・`x_daily_read_cap` で始まる 1 行）に写す。
        """
        from .. import budget_x
        try:
            with budget_x.posts_read(self.auth_account, limit) as slot:
                slot.dispatch()
                self._reads['calls'] += 1
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
                    self._count(limit)
                    raise AdapterError(what + ': x_read_failed: ' + self._scrub(exc)) from None
                data = value.get('data')
                if status != 200 or not (isinstance(data, (list, dict)) or isinstance(value.get('meta'), dict)):
                    self._count(limit)
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
            }.get(code, 'X の読み取りの予約ができません')
            raise AdapterError(f'{code}: {message}') from None
        self._count(charged)
        return value, n

    def _count(self, n):
        from decimal import Decimal
        from .. import budget_x
        self._reads['posts'] += n
        self._reads['usd'] = str(Decimal(self._reads['usd']) + budget_x.POSTS_PRICE * n)

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
            'text': row.get('text'),
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
