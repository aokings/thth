"""広告の書く操作（計画 段 5・D-1〜D-4・監査 A-1〜A-6）。Meta に書くのは `graphwrite.Writer` だけで、
それを呼ぶのはこの包みの決まった関数だけ（`graphwrite.ALLOWED_CALLERS`・試験が縛る）。

**非対称**（D-1 の 4・D-2）:

| 操作 | 止めた口座 | 量の制限の待ち | 柵 |
|---|---|---|---|
| `boost`（作る・全部 `PAUSED`） | **断る** | 待つ | `production`・通貨 JPY・`end_time` 必須・1 操作の上限（通しの予算・期間） |
| `activate`（始める） | **断る** | 待つ | 2 段の digest（**2 段目で Meta から木を読み直す**）＋ hub の番号の記録・`ads_enabled`・`production`・月の上限の予約・`spend_cap`＞0・鍵の残り 14 日以上・`end_time`・1 操作の上限 |
| `extend`（延ばす・増やす） | **断る** | 待つ | `activate` と同じ |
| `pause`・`stop`（止める） | 通す | **待たない** | なし |
| `lower_budget`・`end`（減らす・早める） | 通す | **待たない** | 今より小さい／早い値だけ |
| `lower_ceiling`（月の上限などを下げる・台帳） | 通す | — | 今より小さい値だけ |

**上げる口は無い**: `spend_cap` を上げる・外す、月の上限・1 操作の上限・`ads_enabled`・`production` を
上げる命令は CLI にも MCP にも無い（D-1 の 3）。`spend_cap` は `stop` が `amount_spent` まで下げるだけ。

**THTH の承認は本人でない者を止める柵ではない**（D-1 の 2・監査 A-1）: digest は取り違えを防ぐ柵、hub の
番号は THTH が照合しない「記録」。最後の柵は masaru が管理画面で入れる `spend_cap` と、`end_time` 必須。

**記録** `adaccount_state/<名前>/ops/<op_id>-<op>.json`（0600・1 操作 1 ファイル・結果不明も残す）。
`watch._thth_activated` は `op: activate` の `ids` を「THTH が始めた物」と読む（A-6）。`stop` が「止まった」と
言えなかった記録は `watch` の出来事 `ads_stop_unconfirmed`（最優先）になる。

**hub への知らせ**: THTH は hub を呼ばない（`cli._gate_problem` の注・VM に hub は無い）。`stop` が止まったと
言えないときは、(1) 出来事 `ads_stop_unconfirmed` を `thth events`・`thth morning`・dashboard の最優先に出し、
(2) 打った人（運用・利用者の LLM）が hub に送る 1 行（`waiting_on_masaru`）を出力に載せる。
"""
from __future__ import annotations

import datetime
import json
import os
import re

from .. import accounts, admin_log, approval_ledger, jst, secrets_fs, sent
from ..meta import errors as meta_errors
from . import budget, digest, graphread, graphwrite, ledger, watch

OPS_DIR = "ops"
# 「止まった」と言ってよい `effective_status`（D-4 の 2）。消した・しまった物も止まっている側。
PAUSED_EFFECTIVE = frozenset({"PAUSED", "CAMPAIGN_PAUSED", "ADSET_PAUSED", "DELETED", "ARCHIVED"})
DONE_STATUS = frozenset({"PAUSED", "DELETED", "ARCHIVED"})
SKIP_ACTIVATE = frozenset({"DELETED", "ARCHIVED"})
POST_ID_RE = re.compile(r"([0-9]{1,32})_([0-9]{1,32})")
PLATFORMS = ("facebook", "instagram")
# ページを広告に使えない系の断り（G-11）のときに添える案内。
PAGE_HINT = ("ページを広告に使えない断りなら: ページの持ち主のビジネスから吾徳（広告アカウントのビジネス）へ"
             "ページの共有（広告の権限）をリクエストして承認し、広告を出す人（masaru）をそのページに割り当てる。"
             "済んだら同じ boost を打ち直す（作りかけの物は PAUSED のまま残る）")


class OpsRefused(ValueError):
    """断り。`code` は静的な符丁。お金は動いていない（Meta に書いていない、または確かに断られた）。"""

    def __init__(self, code, message="", *, hint=None):
        super().__init__(f"{code}: {message}" if message else code)
        self.code = code
        self.message = message
        self.hint = hint


# --------------------------------------------------------------------------
# 記録
# --------------------------------------------------------------------------

def ops_dir(name) -> str:
    return os.path.join(ledger.state_dir_for(name), OPS_DIR)


def _record(name, op_id, row) -> str:
    directory = ops_dir(name)
    os.makedirs(directory, mode=0o700, exist_ok=True)
    path = os.path.join(directory, f"{op_id}-{row['op']}.json")
    secrets_fs.atomic_write_json(path, row, mode=0o600)
    return path


def read_ops(name) -> list:
    """記録の全部（古い順）。読めないファイルは飛ばす。"""
    try:
        entries = sorted(os.listdir(ops_dir(name)))
    except OSError:
        return []
    out = []
    for entry in entries:
        if not entry.endswith(".json"):
            continue
        try:
            with open(os.path.join(ops_dir(name), entry), encoding="utf-8") as f:
                row = json.load(f)
        except (OSError, ValueError):
            continue
        if isinstance(row, dict):
            out.append(row)
    return out


def _log(name, op, by, via, diff) -> None:
    try:
        admin_log.append("adaccount_op", name, {"media": ledger.MEDIA}, by=by,
                         via=via if via in ("cli", "mcp") else "cli", diff=dict(diff, op=[None, op]))
    except Exception:  # noqa: BLE001 — 変更ログに残せなくても操作の記録（ops/）が正本
        pass


# --------------------------------------------------------------------------
# 小道具
# --------------------------------------------------------------------------

_OFFSET = re.compile(r"([+-]\d{2})(\d{2})$")


def parse_meta_time(value) -> datetime.datetime | None:
    """Meta の時刻（`2026-11-02T10:00:00+0900`）→ aware。読めなければ None（3.10 でも読めるように直す）。"""
    if not isinstance(value, str):
        return None
    return jst.parse(_OFFSET.sub(r"\1:\2", value.strip()))


def parse_time(text, cfg, now) -> datetime.datetime:
    """`now`・時間帯つきの ISO・時間帯なし（広告アカウントの時間帯で読む）。"""
    if text == "now":
        return now
    if not isinstance(text, str) or not text.strip():
        raise OpsRefused("ads_time_invalid", "時刻は ISO 8601（例 2026-11-01T10:00+09:00）か now です")
    value = parse_meta_time(text)
    if value is not None:
        return value
    try:
        naive = datetime.datetime.fromisoformat(text.strip())
    except ValueError:
        raise OpsRefused("ads_time_invalid", f"時刻が読めません: {text!r}") from None
    if naive.tzinfo is not None:
        return jst.to_jst(naive)
    return naive.replace(tzinfo=watch._zone(cfg))


def meta_time(value: datetime.datetime) -> str:
    return value.isoformat(timespec="seconds")


def _actor(by) -> str:
    try:
        return admin_log.actor(by)
    except ValueError:
        raise OpsRefused("admin_by_required", "--by（打った人の名前）が要ります") from None


def _token(cfg) -> dict:
    token = ledger.load_token(cfg)
    if not token or token.get("unreadable") or not isinstance(token.get("access_token"), str):
        raise OpsRefused("ads_token_missing", "鍵がありません（thth ads auth）")
    return token


def _guarded(name, cfg) -> None:
    """作る・始める・延ばすの前（D-1 の 4）。止まっていれば `AccountStopped`、通貨が JPY 以外なら断る。"""
    ledger.require_active(name)
    refusal = ledger.write_refusal(cfg)
    if refusal:
        raise OpsRefused(refusal, "通貨が JPY でない（または分からない）口座では書く操作を断ります（B-13）")


def _token_fence(cfg, now) -> None:
    """`activate`・`extend` の鍵の柵（C-2 の 5・A-4）: 効く期限の残りが 14 日未満なら断る。"""
    state = watch.token_state(cfg, now=now)
    if state["state"] in (watch.NO_TOKEN, watch.UNREADABLE):
        raise OpsRefused("ads_token_missing", "鍵がありません（thth ads auth）")
    if state["remaining_days"] is None:
        raise OpsRefused("ads_token_expiry_unknown",
                         "鍵の期限が分かりません。thth ads doctor で /debug_token の今の値を書き直してから")
    if state["remaining_days"] < watch.TOKEN_WARN_DAYS:
        raise OpsRefused("ads_token_expiring",
                         f"鍵（またはデータに触れる期限）の残りが {state['remaining_days']:.1f} 日です。14 日を切った鍵では"
                         "配信を始めません（配信の途中で止める道の鍵が切れないため）。thth ads auth で取り直す")
    if state["state"] == watch.ADS_NOT_GRANTED:
        raise OpsRefused("ads_not_granted", "鍵に ads_read がありません")


def _flags(cfg) -> None:
    if not cfg.get("production"):
        raise OpsRefused("ads_not_production", "台帳の production が false です（上げるのは VM の台帳を直接書く）")
    if not cfg.get("ads_enabled"):
        raise OpsRefused("ads_disabled", "台帳の ads_enabled が false です（上げるのは VM の台帳を直接書く）")


def _reader(name, cfg, token, *, base_url=None):
    return graphread.Reader(name, cfg, token, base_url=base_url)


def _writer(name, cfg, token, *, base_url=None):
    return graphwrite.Writer(name, cfg, token, base_url=base_url)


def _failure_code(exc) -> str:
    if isinstance(exc, meta_errors.RateLimited):
        return f"meta_rate_limited（code {exc.code}・{'網に出ていない' if not exc.sent else 'Meta が断った'}）"
    return getattr(exc, "code", None) or type(exc).__name__


def _is_page_refusal(exc) -> bool:
    """ページを使えない系の断り（G-11）の見立て。権限の断り、または code 100 の断り。"""
    return isinstance(exc, graphwrite.AdsWriteError) and (
        exc.failure == meta_errors.PERMISSION or exc.graph_code in (100, 200, 10))


# --------------------------------------------------------------------------
# boost（作る・全部 PAUSED）
# --------------------------------------------------------------------------

def _object_story(name, cfg, *, post=None, object_story_id=None) -> tuple:
    """`(object_story_id, page_id, 出所)`。`--post` は結んだページの口座の `sent/` から引く。"""
    if post:
        for account in cfg.get("pages") or []:
            try:
                record = sent.read(accounts.state_dir_for(account), post)
                account_cfg = accounts.load_account(account)
            except (accounts.AccountError, OSError, ValueError):
                continue
            if record is None:
                continue
            match = POST_ID_RE.fullmatch(post)
            page_id = str(account_cfg.get("page_id") or account_cfg.get("handle") or "")
            if not match or match.group(1) != page_id:
                raise OpsRefused("ads_post_invalid", f"{post} は {account} のページの投稿の形ではありません")
            return post, page_id, f"sent:{account}"
        raise OpsRefused("ads_post_not_found",
                         f"{post} は結んだページの口座（{', '.join(cfg.get('pages') or []) or 'なし'}）の送った記録に"
                         "ありません。THTH で出していない投稿は --object-story-id <ページID>_<投稿の数字>")
    match = POST_ID_RE.fullmatch(object_story_id or "")
    if not match:
        raise OpsRefused("ads_object_story_id_invalid", "--object-story-id は <ページID>_<投稿の数字> です")
    return object_story_id, match.group(1), "object_story_id"


def _instagram_user(cfg) -> str:
    for account in cfg.get("ig_accounts") or []:
        try:
            account_cfg = accounts.load_account(account)
        except accounts.AccountError:
            continue
        value = str(account_cfg.get("handle") or "")
        if graphread.ID_RE.fullmatch(value):
            return value
    raise OpsRefused("ads_instagram_unlinked", "instagram に出すには台帳の ig_accounts に IG の口座が要ります")


def plan_boost(name, cfg, *, post=None, object_story_id=None, lifetime_minor, start, end, platforms=("facebook",),
               objective="engagement", now) -> dict:
    """作る前の検査（網には出ない）。だめなら `OpsRefused`。"""
    if objective not in graphwrite.OBJECTIVES:
        raise OpsRefused("ads_objective_invalid", "目的は engagement か traffic です")
    platforms = tuple(dict.fromkeys(platforms or ("facebook",)))
    unknown = [p for p in platforms if p not in PLATFORMS]
    if unknown:
        raise OpsRefused("ads_platform_unsupported",
                         f"出し先は facebook・instagram だけです（threads は後の段）: {','.join(unknown)}")
    if not cfg.get("production"):
        raise OpsRefused("ads_not_production", "台帳の production が false です（作るのも断る・D-2）")
    ceilings = ledger.ceilings(cfg)
    if not end:
        raise OpsRefused("ads_end_time_required", "--end（終了日）は必須です（終了日の無い配信は作らない・A-2）")
    start_at = parse_time(start or "now", cfg, now)
    end_at = parse_time(end, cfg, now)
    if end_at <= start_at or end_at <= now:
        raise OpsRefused("ads_time_invalid", "終了は開始より後・今より後にしてください")
    days = budget.days_between(start_at, end_at)
    if ceilings["days_max"] <= 0 or days > ceilings["days_max"]:
        raise OpsRefused("ads_days_over_ceiling",
                         f"期間 {days:.2f} 日が 1 操作の上限 days_max {ceilings['days_max']} 日を越えます")
    if isinstance(lifetime_minor, bool) or not isinstance(lifetime_minor, int) or lifetime_minor <= 0:
        raise OpsRefused("ads_amount_invalid", "--lifetime は正の整数（最小単位・JPY なら円）です")
    if lifetime_minor > ceilings["lifetime_budget_max"]:
        raise OpsRefused("ads_lifetime_over_ceiling",
                         f"通しの予算 {lifetime_minor} が 1 操作の上限 lifetime_budget_max "
                         f"{ceilings['lifetime_budget_max']} を越えます")
    story, page_id, source = _object_story(name, cfg, post=post, object_story_id=object_story_id)
    ig_user = _instagram_user(cfg) if "instagram" in platforms else None
    targeting = {"geo_locations": {"countries": ["JP"]}, "age_min": 18,
                 "publisher_platforms": list(platforms),
                 "targeting_automation": {"advantage_audience": 0}}
    return {"object_story_id": story, "page_id": page_id, "source": source, "instagram_user_id": ig_user,
            "lifetime_minor": lifetime_minor, "start": meta_time(start_at), "end": meta_time(end_at),
            "days": round(days, 3), "platforms": list(platforms), "objective": objective, "targeting": targeting}


def boost(name, *, by, via="cli", post=None, object_story_id=None, lifetime_minor, start, end,
          platforms=("facebook",), objective="engagement", now=None, base_url=None) -> dict:
    """キャンペーン・広告セット・クリエイティブ・広告を**全部 `PAUSED`** で作る（お金は動かない）。"""
    now = now or jst.now_jst()
    by = _actor(by)
    cfg = ledger.load(name)
    _guarded(name, cfg)
    plan = plan_boost(name, cfg, post=post, object_story_id=object_story_id, lifetime_minor=lifetime_minor,
                      start=start, end=end, platforms=platforms, objective=objective, now=now)
    token = _token(cfg)
    op_id = budget.new_op_id(now)
    row = {"op": "boost", "op_id": op_id, "at": jst.iso(now), "by": by, "via": via, "adaccount": name,
           "act_id": cfg["act_id"], "result": "started", "plan": {k: v for k, v in plan.items() if k != "targeting"},
           "created": {}, "status": "PAUSED"}
    label = f"THTH {plan['object_story_id']} {now.strftime('%Y-%m-%d %H:%M')}"
    with budget.lock(name):
        writer = _writer(name, cfg, token, base_url=base_url)
        _record(name, op_id, row)
        step = "campaign"
        try:
            row["created"]["campaign_id"] = writer.create_campaign(name=label, objective=plan["objective"])
            _record(name, op_id, row)
            step = "adset"
            row["created"]["adset_id"] = writer.create_adset(
                name=label, campaign_id=row["created"]["campaign_id"], lifetime_minor=plan["lifetime_minor"],
                start=plan["start"], end=plan["end"], objective=plan["objective"], targeting=plan["targeting"],
                page_id=plan["page_id"])
            _record(name, op_id, row)
            step = "creative"
            row["created"]["creative_id"] = writer.create_creative(
                name=label, object_story_id=plan["object_story_id"], instagram_user_id=plan["instagram_user_id"])
            _record(name, op_id, row)
            step = "ad"
            row["created"]["ad_id"] = writer.create_ad(name=label, adset_id=row["created"]["adset_id"],
                                                       creative_id=row["created"]["creative_id"])
        except (graphwrite.AdsWriteError, meta_errors.RateLimited) as exc:
            ambiguous = getattr(exc, "failure", None) == meta_errors.AMBIGUOUS
            row.update(result="unknown" if ambiguous else "failed", failed_step=step, error=_failure_code(exc))
            _record(name, op_id, row)
            _log(name, "boost", by, via, {"result": [None, row["result"]]})
            hint = PAGE_HINT if step in ("creative", "ad") and _is_page_refusal(exc) else None
            raise OpsRefused(
                "ads_boost_unknown" if ambiguous else "ads_boost_failed",
                f"{step} を作る所で{'結果が分かりません（打ち直さない。thth ads show で確かめる）' if ambiguous else '断られました'}"
                f"（{_failure_code(exc)}）。作れた物（全部 PAUSED・お金は動かない）: {row['created'] or 'なし'}",
                hint=hint) from None
        row["result"] = "ok"
        _record(name, op_id, row)
    _log(name, "boost", by, via, {"campaign_id": [None, row["created"]["campaign_id"]],
                                   "lifetime_minor": [None, plan["lifetime_minor"]]})
    return {"op_id": op_id, "status": "PAUSED", "created": row["created"], "plan": row["plan"],
            "next": f"thth ads show {name} で見てから thth ads activate {name} {row['created']['campaign_id']} --by <名前>"}


# --------------------------------------------------------------------------
# activate・extend（2 段・木の読み直し）
# --------------------------------------------------------------------------

def _tree_money(cfg, tree, *, now, change=None) -> dict:
    """木の広告セットの期間・予算を 1 操作の上限で検査し、予約の額を出す。`change` は extend の新しい値。"""
    ceilings = ledger.ceilings(cfg)
    total = 0
    ends = []
    adsets = tree.get("adsets") or []
    if not adsets:
        raise OpsRefused("ads_tree_empty", "広告セットがありません")
    for row in adsets:
        if row.get("status") in SKIP_ACTIVATE:
            continue
        values = dict(row)
        if change and change.get("adset_id") == row.get("id"):
            values.update({k: v for k, v in change.items() if k in ("end_time", "lifetime_budget") and v is not None})
        start = parse_meta_time(values.get("start_time"))
        end = parse_meta_time(values.get("end_time"))
        if end is None:
            raise OpsRefused("ads_end_time_missing",
                             f"広告セット {row.get('id')} に終了日がありません（終了日の無い配信は始めない・A-2）")
        if start is None:
            raise OpsRefused("ads_start_time_missing", f"広告セット {row.get('id')} の開始が読めません")
        if end <= now:
            raise OpsRefused("ads_time_invalid", f"広告セット {row.get('id')} の終了日が過ぎています")
        days = budget.days_between(start, end)
        if ceilings["days_max"] <= 0 or days > ceilings["days_max"]:
            raise OpsRefused("ads_days_over_ceiling",
                             f"広告セット {row.get('id')} の期間 {days:.2f} 日が days_max {ceilings['days_max']} を越えます")
        lifetime, daily = values.get("lifetime_budget"), values.get("daily_budget")
        if isinstance(lifetime, int) and lifetime > 0:
            amount = lifetime
        elif isinstance(daily, int) and daily > 0:
            amount = daily * int(-(-days // 1))   # 日予算は日数（切り上げ）分を通しの額とみなす
        else:
            raise OpsRefused("ads_budget_unreadable", f"広告セット {row.get('id')} の予算が読めません")
        if ceilings["lifetime_budget_max"] <= 0 or amount > ceilings["lifetime_budget_max"]:
            raise OpsRefused("ads_lifetime_over_ceiling",
                             f"広告セット {row.get('id')} の通しの額 {amount} が lifetime_budget_max "
                             f"{ceilings['lifetime_budget_max']} を越えます")
        total += amount
        ends.append(end)
    return {"amount_minor": total, "end_time": meta_time(max(ends)) if ends else None}


def _account_fence(cfg, reader) -> dict:
    account = reader.account()
    if account.get("currency") != cfg.get("currency"):
        raise OpsRefused("adaccount_changed", "台帳の通貨と Meta の通貨が違います")
    if account.get("account_status") != 1:
        raise OpsRefused("ads_account_not_active", f"account_status が {account.get('account_status')} です")
    cap = account.get("spend_cap")
    if not isinstance(cap, int) or cap <= 0:
        raise OpsRefused("ads_spend_cap_missing",
                         "spend_cap が入っていません（0 は Meta では上限なし）。管理画面で masaru が入れる（THTH からは入れない）")
    return account


def _prepare(name, op, *, campaign_id, by, now, base_url, change=None):
    """`activate`・`extend` の 1 段目・2 段目で共通の検査と、Meta から読み直した木の digest。"""
    cfg = ledger.load(name)
    _guarded(name, cfg)
    _flags(cfg)
    _token_fence(cfg, now)
    token = _token(cfg)
    reader = _reader(name, cfg, token, base_url=base_url)
    try:
        account = _account_fence(cfg, reader)
        tree = reader.campaign_tree(campaign_id)
        spend = budget.campaign_spend_this_month(reader)
    except (graphread.AdsReadError, meta_errors.RateLimited) as exc:
        raise OpsRefused("ads_read_failed", f"Meta から読めませんでした（{_failure_code(exc)}）") from None
    if not tree.get("complete"):
        raise OpsRefused("ads_tree_incomplete", "木を全部は読めませんでした（全部を見ないまま始めない）")
    money = _tree_money(cfg, tree, now=now, change=change)
    view = budget.usage(name, cfg, now=now, campaign_spend=spend)
    value = digest.compute_ads_digest(op=op, act_id=cfg["act_id"], currency=cfg.get("currency"), tree=tree,
                                      change=change)
    return cfg, token, reader, account, tree, money, view, value


def _summary(tree) -> dict:
    campaign = tree.get("campaign") or {}
    return {"campaign": {k: campaign.get(k) for k in ("id", "name", "status", "effective_status")},
            "adsets": [{k: r.get(k) for k in ("id", "status", "lifetime_budget", "daily_budget", "start_time",
                                               "end_time", "publisher_platforms")} for r in tree.get("adsets") or []],
            "ads": [{"id": r.get("id"), "status": r.get("status"),
                     "object_story_id": (r.get("creative") or {}).get("object_story_id")} for r in tree.get("ads") or []]}


def _gate(cfg, approval) -> None:
    """hub の番号（**記録**であって照合はしない・D-1 の 2）。`require_gate` は既定 true。"""
    required = cfg.get("require_gate", True) is not False
    if approval in (None, ""):
        if required:
            raise OpsRefused("approval_gate_required",
                             "hub の承認の番号（--approval A261031-1 の形）が要ります（THTH は形だけ見て記録する）")
        return
    if not approval_ledger.valid_gate(approval):
        raise OpsRefused("approval_gate_required", f"--approval の形が違います: {approval!r}")


def activation_ids(tree) -> list:
    """`ACTIVE` にする id（**digest に入れた木の物だけ**・広告 → 広告セット → キャンペーンの順）。"""
    out = [r["id"] for r in tree.get("ads") or [] if r.get("status") not in SKIP_ACTIVATE]
    out += [r["id"] for r in tree.get("adsets") or [] if r.get("status") not in SKIP_ACTIVATE]
    out.append((tree.get("campaign") or {})["id"])
    return out


def activate(name, campaign_id, *, by, via="cli", confirm=None, approval=None, now=None, base_url=None) -> dict:
    """配信を始める（2 段）。1 段目は digest を返すだけ。2 段目は**木を読み直した digest**が一致したときだけ。"""
    now = now or jst.now_jst()
    by = _actor(by)
    if confirm is None:
        cfg, _t, _r, account, tree, money, view, value = _prepare(name, "activate", campaign_id=campaign_id, by=by,
                                                                  now=now, base_url=base_url)
        budget.check(view, money["amount_minor"])
        return {"stage": 1, "digest": value, "amount_minor": money["amount_minor"], "currency": cfg.get("currency"),
                "end_time": money["end_time"], "budget": view, "spend_cap": account.get("spend_cap"),
                "amount_spent": account.get("amount_spent"), "tree": _summary(tree),
                "next": (f"thth ads activate {name} {campaign_id} --confirm {value} --approval <hub の番号> --by <名前>"
                         "（2 段目で Meta から木を読み直し、違えば ads_tree_changed で断る）")}
    with budget.lock(name):
        cfg, token, _r, account, tree, money, view, value = _prepare(name, "activate", campaign_id=campaign_id,
                                                                     by=by, now=now, base_url=base_url)
        if confirm != value:
            raise OpsRefused("ads_tree_changed",
                             "1 段目のあとで木（広告セット・広告・予算・期間・状態）が変わったか、digest が違います。"
                             "1 段目からやり直してください（何も始めていません）")
        _gate(cfg, approval)
        budget.check(view, money["amount_minor"])
        ids = activation_ids(tree)
        op_id = budget.new_op_id(now)
        month = view["month"]
        budget.reserve(name, month, op_id, amount_minor=money["amount_minor"], campaign_id=campaign_id,
                       end_time=money["end_time"], op="activate", now=now)
        row = {"op": "activate", "op_id": op_id, "at": jst.iso(now), "by": by, "via": via, "adaccount": name,
               "act_id": cfg["act_id"], "campaign_id": campaign_id, "digest": value, "approval": approval,
               "ids": ids, "amount_minor": money["amount_minor"], "currency": cfg.get("currency"),
               "end_time": money["end_time"], "month": month, "result": "started", "activated": []}
        _record(name, op_id, row)   # 打つ前に「THTH が始める物」を残す（A-6・途中で落ちても外の配信と言わない）
        writer = _writer(name, cfg, token, base_url=base_url)
        for object_id in ids:
            try:
                writer.set_active(object_id)
            except (graphwrite.AdsWriteError, meta_errors.RateLimited) as exc:
                ambiguous = getattr(exc, "failure", None) == meta_errors.AMBIGUOUS
                if not row["activated"] and not ambiguous:
                    row["result"] = "failed"
                    budget.mark(name, month, op_id, "released")
                else:
                    row["result"] = "unknown" if ambiguous else "partial"
                    budget.mark(name, month, op_id, "unknown" if ambiguous else "fired")
                row.update(error=_failure_code(exc), failed_id=object_id)
                _record(name, op_id, row)
                _log(name, "activate", by, via, {"result": [None, row["result"]], "approval": [None, approval]})
                raise OpsRefused(f"ads_activate_{row['result']}",
                                 f"{object_id} を始める所で{'結果が分かりません' if ambiguous else '断られました'}"
                                 f"（{_failure_code(exc)}）。始まった物: {row['activated'] or 'なし'}。"
                                 + ("" if row["result"] == "failed" else f"止めるなら thth ads stop {name} --by <名前>"))
            row["activated"].append(object_id)
            _record(name, op_id, row)
        row["result"] = "ok"
        budget.mark(name, month, op_id, "fired")
        _record(name, op_id, row)
    _log(name, "activate", by, via, {"campaign_id": [None, campaign_id], "amount_minor": [None, money["amount_minor"]],
                                      "approval": [None, approval]})
    return {"stage": 2, "op_id": op_id, "digest": value, "activated": row["activated"],
            "amount_minor": money["amount_minor"], "end_time": money["end_time"], "approval": approval,
            "next": f"15 分後に thth ads show {name} で amount_spent と effective_status を見る。止めるのは thth ads stop {name}"}


def extend(name, adset_id, *, by, via="cli", end=None, lifetime_minor=None, confirm=None, approval=None,
           now=None, base_url=None) -> dict:
    """期間を延ばす・通しの予算を増やす（2 段・`activate` と同じ柵）。減らす・早めるは `lower_budget`・`end`。"""
    now = now or jst.now_jst()
    by = _actor(by)
    if end is None and lifetime_minor is None:
        raise OpsRefused("ads_nothing_to_write", "--end か --lifetime が要ります")
    cfg = ledger.load(name)
    _guarded(name, cfg)
    token = _token(cfg)
    try:
        node = _reader(name, cfg, token, base_url=base_url).node(adset_id, "adset")
    except (graphread.AdsReadError, meta_errors.RateLimited) as exc:
        raise OpsRefused("ads_read_failed", f"広告セットを読めませんでした（{_failure_code(exc)}）") from None
    change = {"adset_id": adset_id, "end_time": None, "lifetime_budget": None}
    added = 0
    if end is not None:
        new_end = parse_time(end, cfg, now)
        current = parse_meta_time(node.get("end_time"))
        if current is not None and new_end <= current:
            raise OpsRefused("ads_not_later", "延ばすのは今の終了日より後だけです（早めるのは thth ads end）")
        change["end_time"] = meta_time(new_end)
        daily = node.get("daily_budget")
        if isinstance(daily, int) and daily > 0 and current is not None:
            added += daily * int(-(-budget.days_between(current, new_end) // 1))
    if lifetime_minor is not None:
        current_budget = node.get("lifetime_budget")
        if not isinstance(current_budget, int) or current_budget <= 0:
            raise OpsRefused("ads_budget_unreadable", "通しの予算の無い広告セットは --lifetime で増やせません")
        if isinstance(lifetime_minor, bool) or not isinstance(lifetime_minor, int) or lifetime_minor <= current_budget:
            raise OpsRefused("ads_not_higher", "増やすのは今の通しの予算より大きい値だけです（減らすのは budget lower）")
        change["lifetime_budget"] = lifetime_minor
        added += lifetime_minor - current_budget
    campaign_id = node.get("campaign_id")

    def prepared():
        return _prepare(name, "extend", campaign_id=campaign_id, by=by, now=now, base_url=base_url, change=change)

    if confirm is None:
        cfg2, _t, _r, account, tree, money, view, value = prepared()
        budget.check(view, added)
        return {"stage": 1, "digest": value, "change": change, "added_minor": added, "budget": view,
                "tree": _summary(tree),
                "next": (f"thth ads extend {name} {adset_id} ... --confirm {value} --approval <hub の番号> --by <名前>"
                         "（同じ --end・--lifetime で）")}
    with budget.lock(name):
        cfg2, token2, _r, account, tree, money, view, value = prepared()
        if confirm != value:
            raise OpsRefused("ads_tree_changed", "1 段目のあとで木が変わったか、digest が違います。1 段目からやり直す")
        _gate(cfg2, approval)
        budget.check(view, added)
        op_id = budget.new_op_id(now)
        month = view["month"]
        if added:
            budget.reserve(name, month, op_id, amount_minor=added, campaign_id=campaign_id,
                           end_time=change["end_time"] or node.get("end_time"), op="extend", now=now)
        row = {"op": "extend", "op_id": op_id, "at": jst.iso(now), "by": by, "via": via, "adaccount": name,
               "act_id": cfg2["act_id"], "campaign_id": campaign_id, "adset_id": adset_id, "change": change,
               "added_minor": added, "digest": value, "approval": approval, "result": "started"}
        _record(name, op_id, row)
        writer = _writer(name, cfg2, token2, base_url=base_url)
        try:
            if change["lifetime_budget"] is not None:
                writer.set_adset_budget(adset_id, lifetime_minor=change["lifetime_budget"], urgent=False)
            if change["end_time"] is not None:
                writer.set_adset_end(adset_id, change["end_time"], urgent=False)
        except (graphwrite.AdsWriteError, meta_errors.RateLimited) as exc:
            ambiguous = getattr(exc, "failure", None) == meta_errors.AMBIGUOUS
            row.update(result="unknown" if ambiguous else "failed", error=_failure_code(exc))
            if added:
                budget.mark(name, month, op_id, "unknown" if ambiguous else "fired")
            _record(name, op_id, row)
            raise OpsRefused("ads_extend_" + row["result"], f"延ばす所で失敗しました（{_failure_code(exc)}）") from None
        row["result"] = "ok"
        if added:
            budget.mark(name, month, op_id, "fired")
        _record(name, op_id, row)
    _log(name, "extend", by, via, {"adset_id": [None, adset_id], "added_minor": [None, added],
                                    "approval": [None, approval]})
    return {"stage": 2, "op_id": op_id, "change": change, "added_minor": added}


# --------------------------------------------------------------------------
# 止める・減らす（承認なし・止めた口座でも通す・量の制限で待たない）
# --------------------------------------------------------------------------

def _stop_reader_writer(name, cfg, *, base_url):
    """止める道の読み書き。台帳の印（通貨）で書く操作を断る口座でも止める（`Writer` は通貨を見るので、
    止める操作は `_StopWriter` を通す）。"""
    token = _token(cfg)
    return _reader(name, cfg, token, base_url=base_url), _StopWriter(name, cfg, token, base_url=base_url)


class _StopWriter(graphwrite.Writer):
    """止める・下げる口だけに使う `Writer`。通貨の断り（JPY 以外）でも `status=PAUSED` は書けるようにする
    （止める道を塞がない・D-1 の 4）。金額を書く口は `write_amount` が通貨で断るので、ここでも JPY だけ。"""

    def __init__(self, name, cfg, token, *, base_url=None):
        relaxed = dict(cfg, writes_refused=None, currency="JPY")
        super().__init__(name, relaxed, token, base_url=base_url)
        self.currency = cfg.get("currency")


def pause_targets(writer, ids) -> list:
    """`status=PAUSED` を順に書く（待たない）。`[(id, None | 失敗の符丁)]`。1 つ失敗しても残りは書く。"""
    out = []
    for object_id in ids:
        try:
            writer.set_paused(object_id)
            out.append((object_id, None))
        except (graphwrite.AdsWriteError, meta_errors.RateLimited) as exc:
            out.append((object_id, _failure_code(exc)))
    return out


def _all_targets(tree) -> list:
    """止める対象（キャンペーン → 広告セット → 広告の順・もう止まっている物は除く）。"""
    out = []
    for key in ("campaigns", "adsets", "ads"):
        out += [r["id"] for r in tree.get(key) or [] if isinstance(r.get("id"), str)
                and r.get("status") not in DONE_STATUS]
    return out


def unpaused(tree) -> list:
    """読み直した木で止まっていない物（D-4 の 2）。"""
    out = []
    for key in ("campaigns", "adsets", "ads"):
        for row in tree.get(key) or []:
            if row.get("effective_status") not in PAUSED_EFFECTIVE and row.get("status") not in ("DELETED", "ARCHIVED"):
                out.append({"id": row.get("id"), "name": row.get("name"), "effective_status": row.get("effective_status")})
    return out


def pause(name, *, by, via="cli", all_=False, ids=(), now=None, base_url=None) -> dict:
    """`status=PAUSED`。**承認なし・口座が止まっていても通す・量の制限で待たない。**"""
    now = now or jst.now_jst()
    by = _actor(by)
    cfg = ledger.load(name)
    reader, writer = _stop_reader_writer(name, cfg, base_url=base_url)
    targets = list(ids)
    if all_:
        try:
            targets = _all_targets(reader.tree(urgent=True))
        except (graphread.AdsReadError, meta_errors.RateLimited) as exc:
            raise OpsRefused("ads_read_failed", f"止める物を読めませんでした（{_failure_code(exc)}）。管理画面で止めてください") from None
    for object_id in targets:
        if not isinstance(object_id, str) or not graphread.ID_RE.fullmatch(object_id):
            raise OpsRefused("ads_id_invalid", f"id の形が違います: {object_id!r}")
    results = pause_targets(writer, targets)
    op_id = budget.new_op_id(now)
    row = {"op": "pause", "op_id": op_id, "at": jst.iso(now), "by": by, "via": via, "adaccount": name,
           "act_id": cfg["act_id"], "targets": targets, "failed": [{"id": i, "error": e} for i, e in results if e],
           "result": "ok" if all(e is None for _i, e in results) else "failed"}
    _record(name, op_id, row)
    _log(name, "pause", by, via, {"n": [None, len(targets)], "result": [None, row["result"]]})
    return row


def hub_line(name, cfg, unpaused_rows) -> str:
    names = "・".join(f"{r.get('name') or r.get('id')}（{r.get('effective_status')}）" for r in unpaused_rows[:5])
    return (f"waiting_on_masaru: THTH から広告を止めたと確かめられません。管理画面で止めてください"
            f"（広告アカウント act_{cfg['act_id']}・{name}" + (f"・{names}" if names else "") + "）")


def stop(name, *, by, via="cli", now=None, base_url=None) -> dict:
    """D-4 の順: ① 全部 pause → ② Meta から読み直して全部止まったか → ③ `spend_cap` を `amount_spent` まで
    下げる（補助）→ ④ THTH の止めの印。**「止まった」と言うのは ② が通ったときだけ。**"""
    now = now or jst.now_jst()
    by = _actor(by)
    cfg = ledger.load(name)
    op_id = budget.new_op_id(now)
    row = {"op": "stop", "op_id": op_id, "at": jst.iso(now), "by": by, "via": via, "adaccount": name,
           "act_id": cfg["act_id"], "steps": {}, "confirmed": False, "result": "started"}
    _record(name, op_id, row)
    still = None
    try:
        reader, writer = _stop_reader_writer(name, cfg, base_url=base_url)
    except (OpsRefused, graphwrite.AdsWriteError, graphread.AdsReadError) as exc:
        reader = writer = None
        row["steps"]["pause"] = {"ok": False, "error": _failure_code(exc)}
    if writer is not None:
        # ① pause
        try:
            targets = _all_targets(reader.tree(urgent=True))
            results = pause_targets(writer, targets)
            failed = [{"id": i, "error": e} for i, e in results if e]
            row["steps"]["pause"] = {"ok": not failed, "targets": targets, "failed": failed}
        except (graphread.AdsReadError, meta_errors.RateLimited) as exc:
            row["steps"]["pause"] = {"ok": False, "error": _failure_code(exc)}
        # ② 読み直し
        try:
            still = unpaused(reader.tree(urgent=True))
            row["steps"]["verify"] = {"ok": not still, "unpaused": still}
        except (graphread.AdsReadError, meta_errors.RateLimited) as exc:
            row["steps"]["verify"] = {"ok": False, "error": _failure_code(exc)}
        # ③ spend_cap を下げる（補助・これだけでは「止まった」と言わない・G-18）
        try:
            account = reader.account(urgent=True)
            spent, cap = account.get("amount_spent"), account.get("spend_cap")
            if not isinstance(spent, int) or spent <= 0:
                row["steps"]["spend_cap"] = {"ok": None, "skipped": "amount_spent が 0 か読めない（0 は上限なしになるので書かない）"}
            elif isinstance(cap, int) and cap != 0 and spent >= cap:
                row["steps"]["spend_cap"] = {"ok": None, "skipped": "既に amount_spent 以下"}
            else:
                writer.lower_spend_cap(spent, current_minor=cap)
                row["steps"]["spend_cap"] = {"ok": True, "before": cap, "after": spent}
        except (graphread.AdsReadError, graphwrite.AdsWriteError, meta_errors.RateLimited) as exc:
            row["steps"]["spend_cap"] = {"ok": False, "error": _failure_code(exc)}
    # ④ THTH の止め（ほかが全部だめでも打つ）
    try:
        ledger.stop(name, "owner", by=by, via=via, now=now)
        row["steps"]["thth_stop"] = {"ok": True}
    except Exception as exc:  # noqa: BLE001 — 止めの印が書けなくても、Meta 側の結果は返す
        row["steps"]["thth_stop"] = {"ok": False, "error": type(exc).__name__}
    row["confirmed"] = bool(row["steps"].get("pause", {}).get("ok")) and bool(row["steps"].get("verify", {}).get("ok"))
    row["result"] = "ok" if row["confirmed"] else "unconfirmed"
    if not row["confirmed"]:
        row["hub_line"] = hub_line(name, cfg, still or [])
    _record(name, op_id, row)
    _log(name, "stop", by, via, {"confirmed": [None, row["confirmed"]]})
    return row


def lower_budget(name, adset_id, *, by, via="cli", lifetime_minor=None, daily_minor=None, now=None,
                 base_url=None) -> dict:
    """広告セットの予算を**減らす**（承認なし・止まっていても通す・待たない）。今以上の値は断る。"""
    now = now or jst.now_jst()
    by = _actor(by)
    cfg = ledger.load(name)
    reader, writer = _stop_reader_writer(name, cfg, base_url=base_url)
    try:
        node = reader.node(adset_id, "adset", urgent=True)
    except (graphread.AdsReadError, meta_errors.RateLimited) as exc:
        raise OpsRefused("ads_read_failed", f"広告セットを読めませんでした（{_failure_code(exc)}）") from None
    for key, value in (("lifetime_budget", lifetime_minor), ("daily_budget", daily_minor)):
        if value is None:
            continue
        current = node.get(key)
        if not isinstance(current, int) or current <= 0:
            raise OpsRefused("ads_budget_unreadable", f"今の {key} が読めません")
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0 or value >= current:
            raise OpsRefused("ads_not_lower", f"{key} は今 {current} です。下げる値（0 より大きく {current} より小さい）だけ")
    if lifetime_minor is None and daily_minor is None:
        raise OpsRefused("ads_nothing_to_write", "--lifetime か --daily が要ります")
    op_id = budget.new_op_id(now)
    row = {"op": "lower", "op_id": op_id, "at": jst.iso(now), "by": by, "via": via, "adaccount": name,
           "act_id": cfg["act_id"], "adset_id": adset_id,
           "before": {"lifetime_budget": node.get("lifetime_budget"), "daily_budget": node.get("daily_budget")},
           "after": {"lifetime_budget": lifetime_minor, "daily_budget": daily_minor}, "result": "started"}
    try:
        writer.set_adset_budget(adset_id, lifetime_minor=lifetime_minor, daily_minor=daily_minor, urgent=True)
        row["result"] = "ok"
    except (graphwrite.AdsWriteError, meta_errors.RateLimited) as exc:
        row.update(result="failed", error=_failure_code(exc))
    _record(name, op_id, row)
    _log(name, "lower", by, via, {"adset_id": [None, adset_id], "result": [None, row["result"]]})
    return row


def end(name, adset_id, *, by, via="cli", end_at, now=None, base_url=None) -> dict:
    """終了日を**早める**（承認なし・止まっていても通す・待たない）。今より後の値は断る（延ばすのは extend）。"""
    now = now or jst.now_jst()
    by = _actor(by)
    cfg = ledger.load(name)
    reader, writer = _stop_reader_writer(name, cfg, base_url=base_url)
    new_end = parse_time(end_at, cfg, now)
    try:
        node = reader.node(adset_id, "adset", urgent=True)
    except (graphread.AdsReadError, meta_errors.RateLimited) as exc:
        raise OpsRefused("ads_read_failed", f"広告セットを読めませんでした（{_failure_code(exc)}）") from None
    current = parse_meta_time(node.get("end_time"))
    if current is not None and new_end >= current:
        raise OpsRefused("ads_not_earlier", f"終了日は今 {node.get('end_time')} です。早める値だけ（延ばすのは extend）")
    op_id = budget.new_op_id(now)
    row = {"op": "end", "op_id": op_id, "at": jst.iso(now), "by": by, "via": via, "adaccount": name,
           "act_id": cfg["act_id"], "adset_id": adset_id, "before": node.get("end_time"),
           "after": meta_time(new_end), "result": "started"}
    try:
        writer.set_adset_end(adset_id, meta_time(new_end), urgent=True)
        row["result"] = "ok"
    except (graphwrite.AdsWriteError, meta_errors.RateLimited) as exc:
        row.update(result="failed", error=_failure_code(exc))
    _record(name, op_id, row)
    _log(name, "end", by, via, {"adset_id": [None, adset_id], "result": [None, row["result"]]})
    return row


def lower_ceiling(name, key, value, *, by, via="cli") -> dict:
    """台帳の上限を**下げる**だけ（`ledger.lower_ceiling`）。止まっていても通す。"""
    by = _actor(by)
    try:
        return ledger.lower_ceiling(name, key, value, by=by, via=via)
    except ledger.AdAccountError as exc:
        raise OpsRefused(exc.code, str(exc)) from None


def stop_unconfirmed(name) -> dict | None:
    """最後の `stop` が「止まったと確かめられなかった」なら、その記録（`watch` の最優先の出来事）。"""
    stops = [r for r in read_ops(name) if r.get("op") == "stop" and r.get("result") in ("ok", "unconfirmed")]
    if not stops:
        return None
    last = max(stops, key=lambda r: str(r.get("op_id") or ""))
    return last if last.get("result") == "unconfirmed" else None
