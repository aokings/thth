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
    # `max_results` の下限（一次資料: recent search は 10〜100）。
    SEARCH_MIN_RESULTS = 10
    SEARCH_WINDOW_DAYS = 7
    SEARCH_TYPES = ('TOP', 'RECENT')
    READ_FIELDS = {'tweet.fields': 'created_at,author_id,conversation_id,referenced_tweets',
                   'expansions': 'author_id', 'user.fields': 'username'}
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
        # この実体（＝1 回のコマンド）で読んだ他人の投稿（本数・推定 USD・打ち切り）。
        self._reads = {'posts': 0, 'usd': '0', 'calls': 0, 'truncated': False}
        self._post_cache = {}

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
    def _users(value):
        users = (value.get('includes') or {}).get('users') if isinstance(value.get('includes'), dict) else None
        out = {}
        for user in users or []:
            if isinstance(user, dict) and isinstance(user.get('id'), str):
                out[user['id']] = user.get('username') if isinstance(user.get('username'), str) else None
        return out

    def _message_row(self, row, users):
        """X の 1 本を `Message` の形に写す（`threads._message_row` と同じ鍵）。"""
        identifier = row.get('id')
        username = users.get(row.get('author_id'))
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
        }

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

    def conversation(self, post_id: str, *, since: str | None = None) -> list:
        """枝（`conversation_id:<id>` の recent search・**最大 50 本**・直近 7 日）。

        根（`post_id` 自身）は行に入れない（`thread_read` は根を `fetch_post` で別に引く）。
        50 本を超えた分は読まない（`read_cost()['truncated']`）。
        """
        root = self.fetch_post(post_id)
        conversation = (root.get('root_post') or {}).get('id') or root['message_id']
        query = {'query': 'conversation_id:' + conversation, 'max_results': self.CONVERSATION_MAX,
                 'sort_order': 'recency', **self.READ_FIELDS}
        start = self._start_time(since)
        if start:
            query['start_time'] = start
        value, n = self._paid_get('/2/tweets/search/recent', query,
                                  limit=self.CONVERSATION_MAX, what='枝の取得')
        rows = [row for row in self._rows(value) if row['message_id'] != root['message_id']]
        meta = value.get('meta') if isinstance(value.get('meta'), dict) else {}
        if meta.get('next_token') or n > self.CONVERSATION_MAX:
            self._reads['truncated'] = True
        return rows[:self.CONVERSATION_MAX]

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
