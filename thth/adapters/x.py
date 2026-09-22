"""X（api.x.com の v2）の投稿 adapter。設計 2.14.0 §2・一次資料は同 §7。

**この版で出せるもの**: 本文・返信・画像 4 枚・GIF 1・動画 1・投票・返信制限・alt。
**引用は出さない**——`quote_tweet_id` は自己サーブの tier では使えない（§7 の
"Quote-posting … requires an Enterprise plan"）。「API に無い」のではないので、
表には `unsupported: provider_tier_enterprise_only` と理由を付けて載せる
（`thth/media_capabilities.py`）。**この module は `quote_tweet_id` を組み立てない。**

**読む口**: `whoami`（`GET /2/users/me`）と `posts`（`GET /2/users/:id/tweets`）だけ。
どちらも従量の読み取りなので **2.12 の読取予算（USD）の中でしか動かない**
（既定 0＝動かない）。`where`／`mentions`／`insights` は次版（設計 §3）。

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
# 読み取りが従量で計上される口（`budget_x.before_read` が受ける path）。
READ_PATHS = ('/2/users/me',)


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

    CAPABILITIES: frozenset = frozenset({'recent_posts'})
    TOKEN_KEYS = ('access_token',)
    TOKEN_SETUP_HINT = 'thth auth'
    POST_ID_FORM_HINT = 'X の post_id は 19 桁までの数字です。'
    DELETE_PERMISSION = 'tweet.write'
    prepared_media_supported = True

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
            failure = 'publish_definite' if 400 <= exc.code < 500 else 'publish_ambiguous'
            detail = rate_limit(exc) if exc.code == 429 else {}
            return base.PublishResult(None, None, ts, error=reason, failure=failure,
                                      api_diagnostic=detail or None)
        except (httpsafe.EndpointRejected, urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
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

    def refresh_token(self, token):
        raise NotImplementedError(
            'X の更新は thth refresh / thth maintain が adapters.auth_x で回す')
