"""計画 Meta 全部盛り: Conversions API for Business Messaging の最小の口（`thth dm event`）。

    thth dm event <FB|IG の口座> --type purchase|lead|… (--conversation <会話の id> | --psid <相手の id>)
                  [--value 1200 --currency JPY] --by 名前 [--confirm D] [--refresh-dataset]

メッセージの会話から起きた購入・問い合わせを Meta に送る（広告の最適化と計測のため）。審査の権限
`pages_events`（Dashboard の綴りは `page_events` の見込み）・`instagram_manage_events`・
`whatsapp_business_manage_events` を、1 つの口で映す。

**公式の文書で確かめたこと**（developers.facebook.com/docs/marketing-api/conversions-api/business-messaging・11/09）:
  - `POST /{DATASET_ID}/events`。本文は `data[]` と `partner_agent`。1 件は `event_name`・`event_time`（Unix 秒）・
    `action_source: "business_messaging"`・`messaging_channel`（messenger／whatsapp／instagram）・`user_data`・
    `custom_data`（`currency`・`value`）。
  - `user_data`: Messenger は `page_id`＋`page_scoped_user_id`、Instagram は `instagram_business_account_id`＋`ig_sid`、
    WhatsApp は `whatsapp_business_account_id`＋`ctwa_clid`（無いときは `page_id`＋ハッシュした `em`／`ph`）。
  - dataset は `POST /{PAGE_ID|IG_USER_ID|WABA_ID}/dataset`（1 ページ 1 つ・既にあれば同じ id が返る）。
  - 出来事の名前: Purchase・LeadSubmitted・InitiateCheckout・AddToCart・ViewContent・OrderCreated・OrderShipped・
    OrderDelivered・OrderCanceled・OrderReturned・CartAbandoned・QualifiedLead・RatingProvided・ReviewProvided。
  - 要る権限: Messenger `page_events`、Instagram `instagram_manage_events`、WhatsApp `whatsapp_business_management`＋
    `whatsapp_business_manage_events`（どれも Advanced＋Marketing API Access Tier）。
  【未確認】応答の形（`events_received` の有無）・`event_time` の許す幅・`partner_agent` が必須か・dataset の応答の欄の名前
  （`dataset_id` か `id`）・Purchase に `value`／`currency` が必須か（THTH は Purchase に要求する自前の柵を持つ）。

**二段確認**（`thth dm send` と同じ型）: 一段目は中身と digest を見せて何もしない。二段目 `--confirm <digest>` は、台帳に
`production: true` があり、安全装置が止めていないときだけ 1 回打つ（dataset が無ければ同じロックの中で先に作る）。
Meta は重複を除かないので、結果が分からないときは打ち直さない。

**打ち直しの柵**（masaru 11/10「入れて」）: Meta は重複を除かないので、同じ口座・同じ日（日本時間）に同じ digest の出来事は
2 回送らない。二段目でロックの中、媒体を呼ぶ前に印を置き、Meta がはっきり断ったときだけ印を外す（結果が分からないときは
残す＝打ち直さない）。印は digest と日付を混ぜたハッシュで、相手の id・金額は読み戻せない。同じ相手が同じ日に同じ金額で
本当に 2 回買ったときは `--nth 2`（digest が変わる）で別の出来事として送る。印の置き場が読めない・書けないときは送らない。

**保存**: dataset の id（`state/<口座>/event_dataset.json`・秘密でも個人でもない）、その日の送った印
（`state/<口座>/event_sent.json`・日付とハッシュだけ・翌日に捨てる）と、runs の 1 行（いつ・種類・件数 1・
使った権限・結果）だけ。相手の id・金額・会話はどこにも残さない。
"""
from __future__ import annotations

import argparse
import decimal
import hashlib
import json
import os
import re

from . import accounts as accounts_mod
from . import approval as approval_mod
from . import jst
from . import meta_write as mw
from . import secrets_fs
from .adapters import base as adapter_base

# 柔らかい名前 → Meta の名前（`event_name`）。Meta の名前そのもの（大文字小文字を問わない）も通す。
EVENT_TYPES = {
    "purchase": "Purchase", "lead": "LeadSubmitted", "qualified_lead": "QualifiedLead",
    "initiate_checkout": "InitiateCheckout", "add_to_cart": "AddToCart", "view_content": "ViewContent",
    "order_created": "OrderCreated", "order_shipped": "OrderShipped", "order_delivered": "OrderDelivered",
    "order_canceled": "OrderCanceled", "order_returned": "OrderReturned", "cart_abandoned": "CartAbandoned",
    "rating_provided": "RatingProvided", "review_provided": "ReviewProvided",
}
ACTION_SOURCE = "business_messaging"
CHANNELS = ("messenger", "instagram", "whatsapp")
MEDIA_CHANNEL = {"facebook": "messenger", "instagram": "instagram"}
CURRENCY = re.compile(r"[A-Z]{3}")
PERSON_ID = re.compile(r"[0-9]{1,32}")
VALUE_MAX = decimal.Decimal("1000000000")
DATASET_FILE = "event_dataset.json"
SENT_FILE = "event_sent.json"
SENT_MAX = 10000   # 1 日の印の数の上限（ファイルが際限なく育たないように）


# ------------------------------------------------------------------ 純粋な部分（試験が直接見る）

def event_name(kind) -> str:
    """`--type` → Meta の `event_name`。知らない名前は前向きに断る。"""
    text = str(kind or "").strip()
    found = EVENT_TYPES.get(text.lower().replace("-", "_"))
    if found is None:
        found = next((name for name in EVENT_TYPES.values() if name.lower() == text.lower()), None)
    if found is None:
        mw._refuse("meta_event_type_unknown", f"--type {text or '（空）'} は送れる出来事の名前にありません"
                   f"（{'・'.join(EVENT_TYPES)}）", rc=2)
    return found


def money(value, currency, name):
    """`(value, currency)`。どちらか一方だけは断る。Purchase は両方を求める（THTH の自前の柵）。"""
    given_value = value is not None and str(value).strip() != ""
    given_currency = currency is not None and str(currency).strip() != ""
    if name == "Purchase" and not (given_value and given_currency):
        mw._refuse("meta_event_value_invalid", "Purchase には --value と --currency（例 1200 JPY）を両方付けてください", rc=2)
    if given_value != given_currency:
        mw._refuse("meta_event_value_invalid", "--value と --currency は両方付けるか、両方付けないかです", rc=2)
    if not given_value:
        return None, None
    try:
        number = decimal.Decimal(str(value).strip())
    except decimal.InvalidOperation:
        number = None
    if number is None or not number.is_finite() or number < 0 or number > VALUE_MAX:
        mw._refuse("meta_event_value_invalid", f"--value {value} は 0 以上の数にしてください", rc=2)
    code = str(currency).strip().upper()
    if not CURRENCY.fullmatch(code):
        mw._refuse("meta_event_value_invalid", f"--currency {currency} は 3 文字の通貨コード（JPY・USD など）にしてください", rc=2)
    return (int(number) if number == number.to_integral_value() else float(number)), code


def user_data(channel, *, page_id=None, person_id=None, ig_user_id=None, waba_id=None, ctwa_clid=None) -> dict:
    """チャネルごとの `user_data`（公式の頁の例の欄の名前）。WhatsApp の口座は THTH にまだ無く、形と試験だけ。"""
    if channel == "messenger":
        return {"page_id": str(page_id), "page_scoped_user_id": str(person_id)}
    if channel == "instagram":
        return {"instagram_business_account_id": str(ig_user_id), "ig_sid": str(person_id)}
    if channel == "whatsapp":
        return {"whatsapp_business_account_id": str(waba_id), "ctwa_clid": str(ctwa_clid)}
    raise ValueError(f"channel: {channel}")


def event_body(name, channel, data, *, event_time, value=None, currency=None) -> dict:
    """`data[]` の 1 件。"""
    event = {"event_name": name, "event_time": int(event_time), "action_source": ACTION_SOURCE,
             "messaging_channel": channel, "user_data": data}
    if value is not None:
        event["custom_data"] = {"currency": currency, "value": value}
    return event


# ------------------------------------------------------------------ dataset の記録

def _dataset_path(account) -> str:
    return os.path.join(accounts_mod.state_dir_for(account), DATASET_FILE)


def stored_dataset(account, owner_id, channel):
    """前に作った dataset の id（結んだ相手とチャネルが同じときだけ）。無ければ None。"""
    try:
        with open(_dataset_path(account), encoding="utf-8") as stream:
            row = json.loads(stream.read(4097))
    except (OSError, ValueError):
        return None
    if (isinstance(row, dict) and row.get("owner_id") == str(owner_id) and row.get("channel") == channel
            and isinstance(row.get("dataset_id"), str) and re.fullmatch(r"[0-9]{1,32}", row["dataset_id"])):
        return row["dataset_id"]
    return None


def remember_dataset(account, owner_id, channel, dataset_id) -> None:
    secrets_fs.atomic_write_json(_dataset_path(account), {
        "dataset_id": str(dataset_id), "owner_id": str(owner_id), "channel": channel, "saved_at": jst.iso()})


def sent_mark(digest, day) -> str:
    """その日の送った印。digest（相手の id・金額から作る）と日付を混ぜたハッシュで、元の値は読み戻せない。"""
    return hashlib.sha256(f"thth-dm-event-sent\x1f{digest}\x1f{day}".encode("utf-8")).hexdigest()[:24]


def _sent_path(account) -> str:
    return os.path.join(accounts_mod.state_dir_for(account), SENT_FILE)


def _sent_marks(account, day):
    """今日の印の list。ファイルが無い・前の日のものなら空。読めない・壊れているなら None（送らない側に倒す）。"""
    try:
        with open(_sent_path(account), encoding="utf-8") as stream:
            row = json.loads(stream.read())
    except FileNotFoundError:
        return []
    except (OSError, ValueError):
        return None
    if not isinstance(row, dict) or not isinstance(row.get("marks"), list):
        return None
    if row.get("date") != day:
        return []
    return [m for m in row["marks"] if isinstance(m, str)]


def _save_marks(account, day, marks) -> None:
    secrets_fs.atomic_write_json(_sent_path(account), {"date": day, "marks": marks[-SENT_MAX:]})


def _cannot_count(account, reason):
    mw._refuse("meta_event_already_sent", f"{account}: 今日送った出来事の印が{reason}ので、同じ出来事を二重に送って"
               "いないか確かめられません。確かめられないまま送ると Meta で二重に数えられうるので送りません", rc=1)


def claim_mark(account, digest, day) -> str:
    """二段目（ロックの中）で、媒体を呼ぶ前に今日の印を置く。既にあれば断る。"""
    marks = _sent_marks(account, day)
    if marks is None:
        _cannot_count(account, "読めない")
    mark = sent_mark(digest, day)
    if mark in marks:
        mw._refuse("meta_event_already_sent", f"{account}: 同じ出来事（同じ相手・種類・金額）を今日（日本時間）もう送って"
                   "います。Meta は重複を除かないので、もう一度送ると二重に数えられます。送っていません", rc=1)
    try:
        _save_marks(account, day, [*marks, mark])
    except OSError:
        _cannot_count(account, "書けない")
    return mark


def release_mark(account, mark, day) -> None:
    """Meta がはっきり断った（何も数えられていない）ときだけ印を外す。外せなくても害は打ち直せないことだけ。"""
    marks = _sent_marks(account, day)
    if not marks or mark not in marks:
        return
    try:
        _save_marks(account, day, [m for m in marks if m != mark])
    except OSError:
        pass


def _nth(value) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise argparse.ArgumentTypeError("--nth は 1〜100 の整数です") from None
    if not 1 <= number <= 100:
        raise argparse.ArgumentTypeError("--nth は 1〜100 の整数です")
    return number


# ------------------------------------------------------------------ 命令

def _event_permission(args, adapter_cls, token) -> str:
    """要る権限が鍵に無いと分かっていれば呼ぶ前に断る。権限の参照の綴りの揺れ（`pages_events`／`page_events`）は
    どちらで乗っていても通す。一覧が無い鍵は Meta の答えで分かる。"""
    primary = adapter_cls.EVENT_PERMISSION
    scopes = (token or {}).get("scopes")
    names = (primary, *adapter_cls.EVENT_PERMISSION_ALIASES)
    if isinstance(scopes, list) and not any(name in scopes for name in names):
        from .adapters import base as adapter_base
        mw._refuse("meta_write_permission_missing",
                   adapter_base.not_granted_message(primary).replace("<account>", args.account), permission=primary)
    return primary


def _person(args, adapter, channel) -> str:
    """相手の id（`--psid` はそのまま、`--conversation` は会話を読んで相手が 1 人のときだけ）。"""
    conversation = (getattr(args, "conversation", None) or "").strip()
    psid = (getattr(args, "psid", None) or "").strip()
    if bool(conversation) == bool(psid):
        mw._refuse("meta_event_recipient_unknown", "相手を 1 つだけ渡してください（--conversation <会話の id> か --psid <相手の id>）",
                   rc=1)
    if psid:
        if not PERSON_ID.fullmatch(psid):
            mw._refuse("meta_event_recipient_unknown", "--psid は数字の id です（thth dm list・thth dm read に出る相手の id）", rc=1)
        return psid
    convo = mw._read("会話", lambda: adapter.dm_messages(conversation))
    if len(convo["with"]) != 1:
        mw._refuse("meta_event_recipient_unknown", f"会話 {conversation} の相手が 1 人に決まりません（{len(convo['with'])} 人）", rc=1)
    return convo["with"][0]["id"]


def cmd_event(args) -> int:
    channel_flag = (getattr(args, "channel", None) or "").strip().lower() or None
    if channel_flag is not None and channel_flag not in CHANNELS:
        mw._refuse("meta_event_channel_unknown", f"--channel {channel_flag} は {'・'.join(CHANNELS)} のどれかです", rc=2)
    if channel_flag == "whatsapp":
        mw._refuse("meta_event_whatsapp_unavailable", "WhatsApp の口座の種類がまだ THTH にありません", rc=2)
    by = mw._by(args)
    name = event_name(args.type)
    value, currency = money(args.value, args.currency, name)
    account_cfg, adapter_cls, token = mw._load(args, "direct_messages", "ダイレクトメッセージ")
    media = account_cfg.get("media")
    channel = MEDIA_CHANNEL.get(media)
    if channel is None or (channel_flag is not None and channel_flag != channel):
        mw._refuse("meta_write_unsupported", f"{media} の口座では{channel_flag or 'この'}チャネルのイベントを送れません"
                   "（Facebook ページ＝messenger、Instagram＝instagram）")
    permission = _event_permission(args, adapter_cls, token)
    adapter = mw._adapter(account_cfg, token)
    if getattr(args, "conversation", None):
        mw._need(args, adapter_cls, token, [adapter_cls.MESSAGING_PERMISSION])
    person = _person(args, adapter, channel)
    owner = adapter.event_owner_id()
    saved = stored_dataset(args.account, owner, channel)
    refresh = bool(getattr(args, "refresh_dataset", False))
    nth = getattr(args, "nth", None) or 1
    digest = mw.digest_of("dm_event", args.account, channel, name, person, value if value is not None else "",
                          currency or "", *([f"nth={nth}"] if nth > 1 else []))
    if not args.confirm:
        shown = f"{value} {currency}" if value is not None else "—"
        return mw._stage_one(args, digest=digest, payload={
            "channel": channel, "event_name": name, "value": value, "currency": currency, "to": person, "by": by,
            "dataset": "saved" if saved and not refresh else "will_create", "storage": "display_only"}, lines=[
            f"送りません（確認の一段目です）: {args.account} → {channel} の相手 {person}",
            f"  出来事: {name}  金額: {shown}", f"  by    : {by}",
            ("  dataset: 前に作ったものを使います" if saved and not refresh
             else "  dataset: 無いので二段目で作ります（1 ページ 1 つ・既にあれば Meta が同じ id を返します）"),
            "  Meta に送るのは出来事の種類・金額・相手の id です（広告の最適化と計測のため）。Meta は重複を除かないので、"
            "同じ出来事は 1 回だけ送ってください",
            "  同じ出来事は今日はもう送れません（同じ相手が同じ金額で本当に 2 回なら --nth 2）",
            "  THTH に残すのは dataset の id・今日送った印（ハッシュ）と、いつ・種類・件数の 1 行だけです（相手の id・金額は残しません）",
            *([] if approval_mod.is_true(account_cfg.get("production"))
              else ["  ⚠ 台帳に production: true が無いので、二段目でも媒体は呼びません"])],
            again=(f"thth dm event {args.account} --type {args.type}"
                   + (f" --conversation {args.conversation}" if getattr(args, "conversation", None) else f" --psid {person}")
                   + (f" --value {args.value} --currency {args.currency}" if value is not None else "")
                   + (f" --nth {nth}" if nth > 1 else "")
                   + f" --by {json.dumps(by, ensure_ascii=False)}"))
    mw._check_confirm(args, digest)
    mw._ready(args, account_cfg)

    day = jst.now_jst().date().isoformat()

    def send():
        mark = claim_mark(args.account, digest, day)        # ロックの中・媒体を呼ぶ前
        try:
            return _send()
        except adapter_base.AdapterError as exc:
            if getattr(exc, "failure", None) != "ambiguous" and not str(exc).startswith("messaging_window_closed"):
                release_mark(args.account, mark, day)       # はっきり断られた: 何も数えられていない
            raise

    def _send():
        dataset = None if refresh else saved
        if dataset is None:
            dataset = adapter.event_dataset()
            remember_dataset(args.account, owner, channel, dataset)
        body = event_body(name, channel, adapter.event_user_data(person),
                          event_time=jst.now_jst().timestamp(), value=value, currency=currency)
        return adapter.event_send(dataset, body)

    result = mw._write(args, account_cfg, "dm_event", permission, send, extra={"event_type": name, "n": 1})
    if args.json:
        mw._print_json({"ok": True, "account": args.account, "channel": channel, "event_name": name,
                        "received": result.get("received"), "by": by, "storage": "display_only"})
    else:
        print(f"送りました: {args.account} → {channel} に {name} を 1 件（{by}・相手の id と金額は保存していません）")
    return 0
