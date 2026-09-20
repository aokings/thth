"""`thth doctor <account>`: そのトークンで実際に何ができるかを読み取りだけで測る。

なぜ要るか（2026-09-09）: Meta の管理画面で権限を 11 個「アプリレビューに追加」しても、
tester の認可画面に降りてくるのは 5 つだけだった（設計 §2.2 の訂正 3）。**画面の表示と
トークンの実力が一致しない**ので、実際に叩いて確かめる道具を持つ。アカウントを 1 本
足すたびに走らせる。

守ること:
  - **読み取りだけ。投稿・返信・削除は絶対に呼ばない**（doctor が副作用を持つと、
    「様子を見るつもりが出てしまった」が起きうる）。
  - **トークンの値を出力に出さない。** エラー文も `redact()` を通す。
"""
from __future__ import annotations

from . import leave_gate

import json
import sys
import urllib.parse
import urllib.request

from . import account_cli as account_cli_mod
from . import accounts as accounts_mod
from . import adapters as adapters_mod
from . import appenv as appenv_mod
from . import incident as incident_mod
from . import httpsafe
from . import redact as redact_mod
from . import topics as topics_mod
from .adapters import base as adapter_base
from .adapters import threads as threads_mod

TIMEOUT_SECONDS = 20.0

# 導入手順のどこが足りないかを、節番号で指す（設計 §2 の C1・節番号は
# Track C2 の導入文書と合わせて決め打ち: §2 Meta アプリ・§3 app.env・
# §4 アカウント台帳・§5 トークン・§6 timer）。
NEXT_STEP_APP_ENV = "次の一手: 導入文書 §3 client 準備（Threads は app.env）を見てください。"
NEXT_STEP_ACCOUNT = "次の一手: 導入文書 §4 アカウント台帳 を見てください。"
NEXT_STEP_TOKEN = "次の一手: 導入文書 §5 トークン を見てください。"

# **雛形のダミーが残っている台帳を「異常なし」で返さない**（監査 2・C10・
# masaru 裁定 2026-09-13「手がかかっても最善を」）。`thth account add` が写す
# 雛形には、そのままでは通らない値が入っている（`redirect_uri` は
# `https://example.invalid/`・Mastodon の `instance` は `https://mastodon.example`・
# handle は `demo`）。**`add` と `thth auth` の間に、どこにも書かれていない手作業が
# 挟まっていた**——しかも診断の道具が黙っていた。どの欄がダミーかは
# `accounts.dummy_fields()` の 1 か所が知っている（`thth auth` も同じ知識で断る）。
DUMMY_LABEL = "**ダミーのままです**"

# **app.env が「無い」のは、直すべき欠落ではない**（masaru 裁定 2026-09-13）。
# app.env（`THREADS_APP_ID`・`THREADS_APP_SECRET`）を使うのは `thth auth` だけで、
# 管理画面で発行したトークンを `thth token set` で入れる運用——いまの本番 4 本が
# そう——では VM に置かなくても正しく動く（延長 `refresh_access_token` は app
# secret を使わない・`thth/oauth.py`）。それなのに doctor は毎回「次の一手: 導入
# 文書 §3」と言っていた。**正しい状態を毎回「足りない」と言う道具は、本当に
# 足りないときに読まれなくなる。** 無いときは「任意」と言うだけにして、§3 は
# **置いたのに使えないとき**（項目が空・読めない）だけに残す。
APP_ENV_ABSENT_NOTICE = (
    "app.env: 無し（任意。`thth auth` を使うときだけ要ります。"
    "管理画面で発行したトークンを `thth token set` で入れる運用なら不要。"
    "置くなら `thth app set`）")
# トークンが無い／使えないときだけ、`thth auth` へ進む道も一応示す（そちらを
# 選ぶなら app.env が先に要る）。**app.env が無いときにしか出さない。**
NEXT_STEP_AUTH_NEEDS_APP_ENV = "`thth auth` を使うなら先に `thth app set`（--by masaru・導入文書 §3）。"

# **probe の中身は媒体側へ移した**（設計 v2 §4.2・T-B5）。どの口を叩けばどの
# 権限が確かめられるかは媒体の知識で、doctor の知識ではない。doctor に残るのは
# 「**読み取りしか呼ばない取得口**」（下の `_get`）と、結果の並べ方だけ。
# 表示してよい鍵の一覧・`_Probe`・結果の要約は `thth/adapters/threads.py` にある。
_Probe = threads_mod._Probe
_KEEP = threads_mod.PROBE_KEEP
_ApiBodyError = threads_mod.ProbeBodyError


def _get(base_url: str, path: str, params: dict, token: str) -> dict:
    p = dict(params)
    p["access_token"] = token
    url = base_url.rstrip("/") + path + "?" + urllib.parse.urlencode(p)
    with httpsafe.urlopen(url, timeout=TIMEOUT_SECONDS) as resp:
        body = json.loads(resp.read() or b"{}")
    # **doctor 自前の `_get()` には、この直しがまだ入っていなかった**
    # （外部レビュー再々判定 N7・2026-09-12）。`thth/adapters/threads.py` の
    # `_get()` は HTTP 200 でも body に `error` があれば失敗として上げるように
    # 直してあったが、doctor は別の `_get()` を持っていて、そちらは素通しの
    # ままだった。全 GET が HTTP 200 の `error` を返す fixture で、5 probe が
    # ○になり返信 probe が「未投稿のため検査できない」になった（事実と違う）。
    if isinstance(body, dict) and body.get("error"):
        raise _ApiBodyError(
            f"API が error を返しました（HTTP 200）: "
            f"{redact_mod.redact(str(body['error']))[:200]}")
    return body


def _auth_needs_app_env(account_name: str) -> bool:
    """その媒体の `thth auth` が Meta の app.env を要るか（既定は「要らない」）。

    台帳が読めない・媒体を知らないときは False——**判らないときに、要るとは
    言わない**（要らない一手を勧めるほうが、黙っているより手を止める）。
    """
    try:
        cfg = accounts_mod.load_account(account_name)
        return bool(adapters_mod.adapter_class(cfg.get("media")).AUTH_NEEDS_APP_ENV)
    except Exception:   # noqa: BLE001 — 診断の付け足しで診断を止めない
        return False


def diagnose(account_name: str) -> dict:
    from . import stop_observation
    static=stop_observation.diagnostic(account_name)
    if static['error']:return static
    report=_diagnose(account_name)
    report['directory_checks']=static['directory_checks']
    return report


@leave_gate.scoped
def _diagnose(account_name: str) -> dict:
    """読み取りだけで能力を測る。トークンの値は返り値にも入れない。

    **probe は媒体が持つ**（設計 v2 §4.2・T-B5）。doctor は台帳の `media` から
    アダプタを作り、`probe()` に自分の取得口（`_get`——読み取りしか呼ばないこと
    を source の検査で担保している）を渡して、結果を並べるだけ。
    **Threads の出力は現行と同じ。**
    """
    account_cfg = accounts_mod.load_account(account_name)
    token = accounts_mod.load_token(account_cfg)

    if account_cfg.get('media') == 'x':
        observation = read_observation(account_name) or {}
        return dict(account=account_name, error='X は認可だけ対応しています。投稿・採集とその probe は未対応です。認可の修復は thth auth <account> --by <actor>。',
                    probes=[], auth_only=True, auth_via=(token.get('auth_via') if isinstance(token,dict) and token.get('auth_via') in ('relay','paste','token_set') else None),
                    auth_observed_at=observation.get('auth_observed_at'), scopes_recorded=recorded_scopes(token))

    # **媒体を先に引く。** トークンの「在る／無い」の判定が媒体ごとに違うので
    # （下）、知らない媒体をここで loud に断らないと、`media` の誤字が
    # 「トークンが無い」という**別の理由**に化けて出る（T-B0）。
    try:
        adapter_cls = adapters_mod.adapter_class(account_cfg.get("media"))
    except adapter_base.AdapterError as e:
        # 「probe が 0 件で異常なし」にしない——`run_doctor()` が `error` を見て rc=2。
        return {"account": account_name, "error": str(e), "probes": []}

    # **トークンの鍵は媒体が知っている**（`TOKEN_KEYS`・T3 の配線 2026-09-13）。
    # 以前はここが `token.get("access_token")` 固定だったので、**Bluesky は
    # `thth auth` で正しく認可しても「トークンが無い」と言われた**（`.token` に
    # 入るのは `identifier` と `app_password`）。次の一手も媒体ごとに違う
    # （`TOKEN_SETUP_HINT`）——Bluesky に `thth token set` を勧めても入らない。
    if not adapter_cls.has_token(token):
        return {"account": account_name,
                "error": f"トークンが無い（{adapter_cls.TOKEN_SETUP_HINT} を先に）",
                "probes": []}

    user_id = token.get("user_id") or account_cfg.get("user_id") or ""
    try:
        adapter = adapters_mod.make_adapter(account_cfg, token)
    except adapter_base.AdapterError as e:
        return {"account": account_name, "error": str(e), "probes": []}

    required_scope_info={}
    if account_cfg.get('media')=='mastodon':
        from .scopes import MASTODON_SCOPES
        granted=token.get('scopes')
        known=(token.get('scopes_source')=='response' and type(granted) is list and all(type(v) is str for v in granted))
        required_scope_info={'required_scopes':list(MASTODON_SCOPES),
                             'missing_scopes_recorded':sorted(set(MASTODON_SCOPES)-set(granted)) if known else None}
    results = adapter.probe(get=_get)

    for r in results:
        r.pop("body", None)
    return {"account": account_name, "handle": account_cfg.get("handle"),
            "username": token.get("username"), "user_id": user_id,
            "scopes_recorded": recorded_scopes(token),
            "auth_via": token.get("auth_via") if token.get("auth_via") in ("relay","paste","token_set") else None,
            "auth_observed_at": (read_observation(account_name) or {}).get("auth_observed_at"),
            "probes": results, **required_scope_info}


# **記録上の scope**（運用の観測 2026-09-14）。VM の `.token` は Threads 4 本・
# Mastodon 1 本とも `scopes` が null だった——`thth token set` は管理画面発行の
# トークンなので認可の範囲を知りようがなく、null を書く（嘘の一覧は書かない）。
# `thth auth` は `/debug_token` の応答（`scopes_source: "response"`）か、無ければ
# 要求した一覧（`"requested"`）を書き、どちらを書いたかを残す（`thth/oauth.py`）。
# doctor はその記録を **probe（実力）とは別の行**として先頭に出す。**記録と
# 実力を混ぜない**——記録は「認可を求めた／API が言った」であって、叩いて
# 確かめた結果ではない。読み手は null を「不明」と扱う。
SCOPES_SOURCE_UNKNOWN = "unknown"


def recorded_scopes(token: dict) -> dict:
    """`.token` の `scopes`／`scopes_source` を「不明」を不明のまま返す。"""
    token = token if isinstance(token,dict) else {}
    scopes = token.get("scopes")
    source = token.get("scopes_source")
    if not isinstance(scopes, list):
        return {"count": None, "scopes": None,
                "source": source or SCOPES_SOURCE_UNKNOWN}
    return {"count": len(scopes), "scopes": list(scopes),
            "source": source or SCOPES_SOURCE_UNKNOWN}


def debug_token_line(probes: list) -> str | None:
    """`/debug_token` の行（`key == "debug_token"`）から、**記録上の scope の
    直後に出す 1 行**を作る。無い媒体（Bluesky・Mastodon）は None。

    突き合わせの相手は `scopes.DEFAULT_SCOPES`（設計 §8-14・11 権限）。**doctor は
    `.token` を書き換えない**（読むだけの道具）——一覧が取れて `.token` の
    `scopes` が null でも、ここで言うだけ。
    """
    row = next((p for p in probes
                if p.get("key") == threads_mod.ThreadsAdapter.DEBUG_TOKEN_KEY), None)
    if row is None:
        return None
    if row.get("ok") is not True or not isinstance(row.get("scopes"), list):
        return f"debug_token の scope: 取れませんでした（{row.get('detail')}）"
    n = len(row["scopes"])
    missing = row.get("missing") or []
    extra = row.get("extra") or []
    if not missing and not extra:
        return f"debug_token の scope: {n} 個（DEFAULT_SCOPES と一致）"
    parts = []
    if missing:
        parts.append("足りない=" + ",".join(missing))
    if extra:
        parts.append("余計=" + ",".join(extra))
    return f"debug_token の scope: {n} 個（不一致: {'・'.join(parts)}）"


def recorded_scopes_line(rec: dict | None) -> str:
    rec = rec or {}
    if rec.get("count") is None:
        return (f"記録上の scope: 不明（.token に一覧が無い・"
                f"source={rec.get('source') or SCOPES_SOURCE_UNKNOWN}）")
    return f"記録上の scope: {rec['count']} 個（source={rec.get('source')}）"


OBSERVATION_FIELDS = ('failure', 'key', 'label', 'permission', 'ok', 'detail', 'status',
                      'http_status', 'http', 'reason')


def _observation_probes(probes):
    from . import admin_log
    if not isinstance(probes, list):
        raise ValueError('doctor_observation_unavailable')
    rows = []
    for probe in probes:
        if not isinstance(probe, dict) or not isinstance(probe.get('key'), str):
            raise ValueError('doctor_observation_unavailable')
        row = {}
        for key in OBSERVATION_FIELDS:
            value = probe.get(key)
            if key == 'ok':
                valid = value is None or type(value) is bool
            elif key in ('http', 'http_status'):
                valid = value is None or type(value) is int and 100 <= value <= 599
            else:
                valid = value is None or isinstance(value, str)
            if not valid:
                raise ValueError('doctor_observation_unavailable')
            row[key] = admin_log.clean(value)
        rows.append(row)
    return rows


def _credential_generation(cfg):
    import hashlib
    from pathlib import Path
    from . import authflow
    token_path = cfg.get('token')
    if not token_path:
        return None
    token_generation = authflow._generation(authflow._token_snapshot(Path(token_path)))
    if token_generation is None:
        return None
    # Bind the observed credential to the exact ledger used for the request.
    # A changed origin/identity/path cannot inherit an old permission result.
    # Keep this digest private; public observations expose only current/unknown.
    binding = json.dumps({'ledger': cfg, 'token_generation': token_generation},
                         sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)
    return hashlib.sha256(binding.encode()).hexdigest()


def _observation_raw(account_name):
    from . import handoff_cursor, jst
    value = handoff_cursor.read_snapshot(account_name, 'doctor.json')
    if (not isinstance(value, dict) or type(value.get('schema_version')) is not int or value.get('schema_version') not in (1, 2)
            or value.get('account') != account_name):
        return None
    probed = value.get('probed_at')
    if probed is not None and (not jst.parse(probed) or jst.parse(probed) > jst.now_jst()):
        return None
    auth_at = value.get('auth_observed_at')
    if auth_at is not None and (not jst.parse(auth_at) or jst.parse(auth_at) > jst.now_jst()):
        return None
    if probed is None and (value.get('schema_version') == 1 or not auth_at):
        return None
    if value.get('auth_via') not in (None, 'relay', 'paste', 'token_set'):
        return None
    if value.get('error') is not None and not isinstance(value['error'], str):
        return None
    for key in ('auth_credential_generation', 'probe_credential_generation'):
        gen=value.get(key)
        if gen is not None and (not isinstance(gen,str) or len(gen)!=64 or any(c not in '0123456789abcdef' for c in gen)):
            return None
    try:
        _observation_probes(value.get('probes'))
    except (TypeError, ValueError):
        return None
    return value


def record_auth(account_name, cfg, token, *, log=print):
    """After token+event commit only. Failure does not turn auth success into a probe."""
    from . import admin_log, handoff_cursor, jst
    try:
        if token.get('auth_via') not in ('relay','paste','token_set'):
            raise ValueError('auth_via_unavailable')
        with leave_gate.lease(account_name),admin_log.transaction():
            if accounts_mod.load_account(account_name)!=cfg or accounts_mod.load_token(cfg)!=token:
                raise ValueError('auth_credential_changed')
            previous=_observation_raw(account_name) or {}
            value=dict(schema_version=2,account=account_name,
                       probed_at=previous.get('probed_at'),probes=_observation_probes(previous.get('probes',[])),error=admin_log.clean(previous.get('error')),
                       probe_credential_generation=previous.get('probe_credential_generation'),
                       auth_via=token['auth_via'],auth_observed_at=jst.iso(),
                       auth_credential_generation=_credential_generation(cfg))
            handoff_cursor.write_snapshot(account_name,'doctor.json',value)
        return True
    except (OSError,ValueError,TypeError,accounts_mod.AccountError,admin_log.AdminLogError):
        log('auth_observation_not_recorded: 認可は保存済みですが doctor の認可経路を記録できませんでした')
        return False


_GENERATION_UNSET = object()

def record_observation(account_name, report, *, probed_at=None, credential_generation=_GENERATION_UNSET):
    """Persist explicit probe results, retaining separate authorization history."""
    from . import admin_log, handoff_cursor, jst
    cfg=accounts_mod.load_account(account_name)
    admin_log.register_account_secrets(cfg)
    if cfg.get('media')=='x':
        return read_observation(account_name) # Auth-only: no API probe took place.
    error=report.get('error')
    if error is not None and not isinstance(error,str):raise ValueError('doctor_observation_unavailable')
    with leave_gate.lease(account_name),admin_log.transaction():
        previous=_observation_raw(account_name) or {}
        value=dict(schema_version=2,account=account_name,probed_at=probed_at or jst.iso(),
                   probes=_observation_probes(report.get('probes')),error=admin_log.clean(error),
                   probe_credential_generation=(_credential_generation(cfg) if credential_generation is _GENERATION_UNSET else credential_generation),
                   auth_via=previous.get('auth_via'),auth_observed_at=previous.get('auth_observed_at'),
                   auth_credential_generation=previous.get('auth_credential_generation'))
        handoff_cursor.write_snapshot(account_name,'doctor.json',value)
    return value


def read_observation(account_name):
    from . import admin_log
    value=_observation_raw(account_name)
    if value is None:return None
    try:current=_credential_generation(accounts_mod.load_account(account_name))
    except (OSError,ValueError,accounts_mod.AccountError):current=None
    probe_gen=value.get('probe_credential_generation')
    return dict(probed_at=value.get('probed_at'),probes=_observation_probes(value.get('probes')),
                error=admin_log.clean(value.get('error')),auth_via=value.get('auth_via'),
                auth_observed_at=value.get('auth_observed_at'),
                auth_current_credentials=bool(current and current==value.get('auth_credential_generation')),
                probe_current_credentials=(bool(current and current==probe_gen) if probe_gen or value.get('auth_observed_at') or value.get('schema_version') == 2 else None))


def run_doctor(account_name: str, *, as_json: bool = False, log=print) -> int:
    """導入手順のどこが足りないかを、実際にトークンを叩く前に机上で見る（設計
    §2 の C1）。順番は app.env → accounts/<account>.json → token。**既存の検査
    （トークンを実際に叩く診断）は壊さない・その手前に足す。** 足りないものごとに
    「次の一手」（導入文書の節番号）を 1 行言う。値は一切出力しない——app.env は
    存在と項目の有無だけを見て、中身は読み捨てる。
    """
    from . import stop_observation
    static=stop_observation.diagnostic(account_name)
    if static['error']:
        if as_json:log(json.dumps(static,ensure_ascii=False))
        else:
            log(static.get('message') or static['error'])
            for row in static['directory_checks']:
                if row['warning']:log(row['directory']+': '+row['warning']+' (mode='+str(row['mode'])+')')
        return 2
    notices: list[str] = []

    # **台帳の置き場を 1 行で言う**（設計 v2 §3・v2-2a）。台帳が「外」なのか
    # 「repo の中（互換）」なのかで、`thth account add` した 1 本が見えたり
    # 見えなかったりする。**どこを読んだかを言わない診断は、台帳が見つからない
    # ときに役に立たない。** notices（＝足りないもの）とは別枠で常に出す。
    どこ = account_cli_mod.where_line()
    accounts_dir_info = accounts_mod.accounts_dir_info()

    # `--json` のときは stdout を JSON 1 個だけにする（設計 §6）ので、
    # パーミッション直しの警告（`secrets_fs.ensure_mode_600`）を **stdout へ
    # 素通しできない。** そこで以前は `--json` のとき捨てていたが、それだと
    # **道具がファイルを 600 に書き換えた事実が、機械の読み手からは完全に
    # 消える**（独立監査 1・P3-11）。**捨てずに `notices` へ回す。**
    # 値そのものはどちらにしても出さない（出るのは path と「直した」だけ）。
    直した: list[str] = []
    def env_log(msg):
        直した.append(str(msg))
    # `absent` / `ok` / `broken`。**無い（任意）と、置いたのに使えない（要修理）を
    # 分ける**（masaru 裁定 2026-09-13・上の `APP_ENV_ABSENT_NOTICE` の理由）。
    try: auth_only = accounts_mod.load_account(account_name).get('media') == 'x'
    except accounts_mod.AccountError: auth_only = False
    app_env_state, app_env_detail = (appenv_mod.ABSENT, '') if auth_only else appenv_mod.probe(log=env_log)
    # **app.env が要るのはその媒体の `thth auth` だけ**（`AUTH_NEEDS_APP_ENV`・
    # 独立監査 1・P2-3・2026-09-13）。以前はここが媒体を見ずに出していたので、
    # Bluesky の診断に「`thth auth` を使うときだけ app.env が要ります」と出た
    # ——Bluesky の `thth auth` は App Password を対話で受けるだけで app.env を
    # 読まない。**要らない準備を勧める道具は、そこで人の手を止める**（NEXT_STEP_
    # AUTH_NEEDS_APP_ENV を媒体で絞ったのと同じ理由・同じ判定を使う）。
    # 「置いたのに壊れている」は媒体に関わらず言う（実在するファイルの異常）。
    if app_env_state == appenv_mod.ABSENT and _auth_needs_app_env(account_name):
        notices.append(APP_ENV_ABSENT_NOTICE)
    if app_env_state == appenv_mod.BROKEN:
        notices.append(f"app.env: {app_env_detail}")
        notices.append(NEXT_STEP_APP_ENV)
    if 直した:
        notices.append("app.env のパーミッションを 600 に直しました"
                        f"（{appenv_mod.default_path()}）")

    # **トピックの台帳が壊れていたら、それも診断で言う**（独立監査 1・P1-1）。
    # 台帳は `--advise` と承認の一段目が毎回読む。**壊れていることに気づける
    # 場所が「壊れたあと」しか無かった。** 診断の道具が黙っているのが穴。
    topics_shelf = None
    try:
        topics_mod.load()
    except topics_mod.ShelfBroken as e:
        topics_shelf = {"error": "topics_shelf_broken", "path": e.path,
                        "detail": e.detail}
        notices.append(str(e))

    try:
        account_cfg = accounts_mod.load_account(account_name)
    except accounts_mod.AccountError as e:
        notices.append(f"アカウント台帳: {e}")
        notices.append(NEXT_STEP_ACCOUNT)
        if as_json:
            payload = {"account": account_name, "error": str(e), "probes": [],
                       "notices": notices, "app_env": app_env_state,
                       "accounts_dir": accounts_dir_info, "dummy_fields": []}
            if topics_shelf:
                payload["topics_shelf_broken"] = topics_shelf
            log(json.dumps(payload, ensure_ascii=False))
        else:
            # **台帳が読めなかったときこそ、どこを読んだかを言う。**
            log(どこ)
            for n in notices:
                log(n)
        return 2

    if not all(incident_mod.readiness(account_cfg).values()):
        notices.append("停止メール通知が未設定です。thth notifications status/config/test を実行してください（既存の投稿動作は維持）。")

    # **台帳は読めた。その中身が雛形のダミーのままなら、名指しで言う**（C10）。
    # rc の意味づけは変えない——ここは「台帳が読めない」（2）でも「トークンが
    # 無い」（2）でもなく、**診断はできたが×がある**ので、probe が落ちたときと
    # 同じ 1。台帳の値は人が直すもので、道具は測るだけ（設計 §4.2）。
    dummies = accounts_mod.dummy_fields(account_cfg)
    for d in dummies:
        notices.append(f"台帳の `{d['field']}` が{DUMMY_LABEL}: {d['value']}")
        notices.append(f"次の一手: {d['next']}")

    try: probe_generation = _credential_generation(account_cfg)
    except (OSError,ValueError): probe_generation = None
    report = diagnose(account_name)
    try:
        record_observation(account_name, report, credential_generation=probe_generation)
    except (OSError, ValueError, TypeError, accounts_mod.AccountError):
        # Keep the existing diagnostic payload/exit meaning; recording failure is
        # separate and bounded, never the raw filesystem or provider exception.
        print('doctor_observation_unavailable: diagnostic was not recorded', file=sys.stderr)
    if report.get("error"):
        notices.append(NEXT_STEP_TOKEN)
        if app_env_state == appenv_mod.ABSENT and _auth_needs_app_env(account_name):
            # トークンを入れる道は 2 つある。`thth token set`（app.env 不要）と
            # `thth auth`（app.env が要る）。後者を選ぶ人が §3 へ戻れる 1 行。
            # **要らない媒体には勧めない**（T3・2026-09-13）——Bluesky の
            # `thth auth` は App Password を対話で受けるだけで app.env を読まない。
            # 診断の道具が「要らない準備」を指図すると、そこで手が止まる。
            notices.append(NEXT_STEP_AUTH_NEEDS_APP_ENV)

    if as_json:
        report["directory_checks"] = static["directory_checks"]
        report["notices"] = notices
        report["app_env"] = app_env_state
        report["accounts_dir"] = accounts_dir_info
        report["dummy_fields"] = dummies
        if topics_shelf:
            report["topics_shelf_broken"] = topics_shelf
        log(json.dumps(report, ensure_ascii=False))
        if report.get("auth_only"):
            return 2
        if topics_shelf:
            # **壊れた台帳を「異常なし」で返さない**（独立監査 1・P1-1）。
            return 2
        if dummies:
            # 同じ筋（C10）——**ダミーの残った台帳を 0 で返さない。**
            return 1
        return 0 if report.get("probes") and all(
            p["ok"] is not False for p in report["probes"]) else 1

    log(どこ)
    for n in notices:
        log(n)
    log("")

    if report.get("error"):
        log(report["error"])
        return 2
    log(f"{report['account']}（{report['username']}・user_id={report['user_id']}）")
    log(recorded_scopes_line(report.get("scopes_recorded")))
    if report.get('required_scopes'):
        log('必要な scope: '+ ' '.join(report['required_scopes']))
        if report.get('missing_scopes_recorded'):
            log('記録上不足: '+', '.join(report['missing_scopes_recorded'])+'（thth auth <account> --by <名前> で再認可）')
    debug_line = debug_token_line(report["probes"])
    if debug_line:
        log(debug_line)
    log(f"認可経路: {report.get('auth_via') or '不明'}（記録時刻: {report.get('auth_observed_at') or '不明'}。probe の実測時刻とは別）")
    log("")
    failed = 0
    for p in report["probes"]:
        # `―` は「読み取りでは確かめられません」（権限不足の × とは違う）。
        # **`None` は失敗に数えない**——判らないことを「駄目」と言わない。
        mark = "○" if p["ok"] else ("―" if p["ok"] is None else "×")
        if p["ok"] is False:
            failed += 1
        log(f"  {mark} {p['label']}（{p['permission']}）")
        log(f"      {p['detail']}")
    log("")
    log("読み取りだけを試しました。投稿・返信・削除は呼んでいません。")
    if any(p["ok"] is None for p in report["probes"]):
        log("― は「読み取りでは確かめられません」（権限不足の印ではありません）。")
    # **診断は最後まで出す。ただし壊れた台帳を「異常なし」で返さない**
    # （独立監査 1・P1-1）。probe が全部○でも rc=2。
    if topics_shelf:
        return 2
    return 1 if (failed or dummies) else 0
