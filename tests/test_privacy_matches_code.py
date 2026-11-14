"""privacy とコードの一致の柵（設計「監査と審査の機械化と前向きな断り」§6 の (A)・段 D）。

`callback/public/privacy/index.html` が言う数（保持・期限・回数・上限）を、その数を決めているコードの定数から
作った文と突き合わせる。コードの数を変えて privacy を直さないと赤になる（逆も同じ）。

既にある privacy の試験（`tests/test_site.py`・`tests/test_v212_relay.py`・`tests/test_v3180_where_keep.py`）は
文の有無を見るが、文の中の数をコードの定数から引かない（字面の写し）。ここはその「数の出所」を結ぶ最小の形。
privacy の文は `tools/build_site.py` から生成する（`test_site.py` が一致を見る）。
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from thth import map_store, map_world, meta_public, meta_write, meter, permission_watch, relay
from thth.meta import calls as meta_calls
from thth.shop import ledger as shop_ledger
from thth.shop import wire as shop_wire
from thth.shop import access as shop_access
from thth.shop import shopify as shop_shopify
from thth.shop import kinds as shop_kinds
from thth.register import form as register_form
from thth.register import kinds as register_kinds
from thth.register import holes as register_holes
from thth.register import questions as register_questions

ROOT = Path(__file__).resolve().parent.parent
PRIVACY = ROOT / "callback" / "public" / "privacy" / "index.html"
SRC = ROOT / "callback" / "src"


def js_const(file: str, name: str) -> int:
    """`callback/src/<file>` の `NAME=1_000` の形の数の定数（`export const A=…, B=…` の並びも読む）。"""
    text = (SRC / file).read_text(encoding="utf-8")
    found = re.search(rf"\b{name}\s*=\s*([\d_]+)\b", text)
    assert found, f"{file} に {name} が無い"
    return int(found.group(1).replace("_", ""))


def seconds(ms: int) -> int:
    assert ms % 1000 == 0
    return ms // 1000


def minutes(ms: int) -> int:
    assert ms % 60_000 == 0
    return ms // 60_000


def hours_en(ms: int) -> str:
    assert ms % 3_600_000 == 0
    n = ms // 3_600_000
    return "one hour" if n == 1 else f"{n} hours"


# （何の数か, コードの値から作った privacy の文の断片）。

def _draft_has_no_image() -> bool:
    """店の下書きに画像の URL が入らないこと（画像の URL のある商品で本文を作って確かめる）。"""
    from thth.shop import draft as shop_draft
    row = {"id": 1, "name": "x", "price": 1, "stock": 1, "visible": True, "sales_status": "active",
           "image_url": "https://img.example.invalid/marker-image.jpg", "item_url": "https://x.example.invalid/p/1",
           "modified_at": "t"}
    text = shop_draft.text_for("s", "a", "threads", {"id": 1, "kind": "new", "draft_kind": "new"}, row,
                               shop_url="https://x.example.invalid", read_at="t", publish_at="t")
    return "marker-image" not in text and "from_store_image: none" in text

def _stores_user_keeps_id_and_name() -> bool:
    """STORES の店の応答から残すのが id と名前だけ（URL は台帳の値）であること。"""
    from thth.shop import stores as shop_stores
    payload = b'{"id": "abc", "name": "n", "mail": "marker@example.invalid", "url": "https://marker.example.invalid"}'
    shop = shop_stores.Client({"public_url": "https://ledger.example.invalid"}, "c",
                              transport=lambda m, u, h: (200, payload)).user()
    return (shop == {"shop_id": "abc", "shop_name": "n", "shop_url": "https://ledger.example.invalid",
                     "able_to_business": None})


def _register_private_defaults() -> bool:
    return (register_kinds.private_by_default("store") == {"address", "phone"}
            and register_kinds.private_by_default("special") == {"reason"})


def _register_sample_draft() -> str:
    import datetime
    from thth import jst
    from thth.register import draft as register_draft
    row = register_form.parse("x.md", "---\nid: s\nkind: special\nstatus: 本採用\nsource: owner\nconfirmed: 本人が認めた\n"
                              "review_by: 2099-01-01\npublic: true\ndate: 2099-01-02\nclosed: true\nreason: 理由の目印\n"
                              "image: https://img.example.invalid/marker-image.jpg\n---\n本文\n")
    now = datetime.datetime(2099, 1, 1, 9, 0, tzinfo=jst.JST)
    until = datetime.datetime(2099, 1, 2, 23, 59, tzinfo=jst.JST)
    return register_draft.text_for("r", "a", "threads", "special_soon", row, commit="none", now=now, not_after=until)


def _register_draft_has_no_image() -> bool:
    text = _register_sample_draft()
    return "marker-image" not in text and "理由の目印" not in text


def _register_draft_has_not_after() -> bool:
    text = _register_sample_draft()
    return "not_after: 2099-01-02T23:59:00+09:00" in text and "from_register: r/s" in text


def _answer_respects_two_stage() -> bool:
    import inspect
    from thth.register import cli as register_cli
    return "kinds.two_stage(row.kind)" in inspect.getsource(register_cli.cmd_answer)


def _register_paste_offline() -> bool:
    """外の場所に貼る束（段 4）: places と paste・pasted・compare の口が網の部品も書く部品も持たず、compare は写した文を残さない。"""
    import inspect
    from thth.register import cli as register_cli, places as register_places
    source = inspect.getsource(register_places)
    compare = inspect.getsource(register_cli.cmd_compare)
    return (not any(word in source for word in ("urllib", "requests", "http.client", "socket"))
            and "write" not in compare and "open(" not in compare
            and register_kinds.private_by_default("place") == {"url", "admin_by"})


def _register_fields_en() -> str:
    names = [f"<code>{name}</code>" for name in register_form.MANAGED]
    return ", ".join(names[:-1]) + " and " + names[-1] if len(names) > 1 else names[0]


def _register_offline() -> bool:
    from tests.test_register_base import REGISTER_DIR, net_imports
    return net_imports(REGISTER_DIR) == []


def _register_uses_no_shop() -> bool:
    """台帳の機械が店の口（`thth.shop`）を使わない（監査 5.19.0 直したほうがよい 6）。

    import は許した表の外なら赤（`tests/test_register_base.import_problems`）で、表に `thth.shop` が無く、表の部品も
    `thth.shop` を import しない（関数の中の import も見る）。
    """
    from tests.test_register_base import REGISTER_ALLOWED_THTH, REGISTER_DIR, _absolute_imports, net_imports
    if net_imports(REGISTER_DIR) or any(n == "thth.shop" or n.startswith("thth.shop.") for n in REGISTER_ALLOWED_THTH):
        return False
    for name in REGISTER_ALLOWED_THTH:
        path = ROOT / (name.replace(".", "/") + ".py")
        if any(n == "thth.shop" or n.startswith("thth.shop.") for n in _absolute_imports(path, "thth")):
            return False
    return True


def _register_draft_record_keys() -> list:
    """`register draft` が控えに書く頭の行の名前（`draft.text_for` の頭から作る・recheck が比べる並び）。"""
    from thth.register import recheck as register_recheck
    import inspect
    from thth.register import cli as register_cli
    source = inspect.getsource(register_cli.cmd_draft)
    assert "draft_origin.record(" in source and '"not_after": jst.iso(until)' in source
    return list(register_recheck.ORIGIN_KEYS) + ["not_after"]


def _register_fence_codes() -> bool:
    """柵が断る 5 つ（推測・違うと言った・見直し日・退役・出してよい印）が `fence.state` にある。"""
    import inspect
    from thth.register import fence
    source = inspect.getsource(fence.state)
    return all(code in source for code in ("register_row_guess", "register_row_denied", "register_row_review_passed",
                                           "register_row_retired", "register_row_not_public"))


def _account_leave_keeps_registers() -> bool:
    """口座を外しても台帳の控えと判断を消さない（台帳の器 段 8）。`leave.run` が台帳の控えを消す口を呼ばず、
    `leave.command` が「残った」と言い、外す範囲は `project` と `owner` の 2 つだけ。"""
    import inspect
    from thth import leave
    from thth.register import owner
    run = inspect.getsource(leave.run)
    return ("_purge_registers" not in run and "remove_control" not in run and "remove_state" not in run
            and "register_kept" in inspect.getsource(leave.command) and owner.SCOPES == ("project", "owner"))


def _project_leave_keeps_common() -> bool:
    """メディアを外す見積りは、共通の台帳の控えを消す物に入れない（`owner.register_plan` を偽の控えで動かす）。
    持ち主ごとやめる見積りは共通も入れる。封の秘密の鍵は持ち主ごとのときだけ（`leave.scope_plan`）。"""
    import inspect
    from thth import leave
    from thth.register import owner
    controls = {"m": {"project": "p", "path": "/nonexistent/m"},
                "c": {"project": "c", "path": "/nonexistent/c", "role": "common"}}
    saved = owner._controls, owner._files_in, owner.folders
    owner._controls, owner._files_in, owner.folders = (lambda: (controls, [])), (lambda folder: 0), (lambda c=None: [])
    try:
        project = owner.register_plan("project", "p")
        everyone = owner.register_plan("owner")
    finally:
        owner._controls, owner._files_in, owner.folders = saved
    plan = inspect.getsource(leave.scope_plan)
    return (project["registers"] == ["m"] and project["common"] == ["c"] and everyone["registers"] == ["c", "m"]
            and "principals_gone,principals_bad,key=[],[],False" in plan and "seal.key_path()" in plan)


def _register_places() -> dict:
    """台帳の器が台帳のフォルダに書く置き場（コードの定数から作る）。"""
    from thth.register import judgment, keep, published, route, vessel
    d = vessel.DECL_DIR
    return {"judgments": f"{d}/{judgment.FILE}", "seals": f"{d}/{route.SEALS_DIR}/", "kept": f"{d}/{keep.KEPT}/",
            "batch": f"{d}/{keep.BATCH}/", "erase": f"{d}/{judgment.ERASE_DIR}/", "published": f"{d}/{published.DIR}/",
            "kinds": f"{d}/{vessel.KINDS_DIR}/"}


def _seal_key_default() -> str:
    """封の秘密の鍵の既定の置き場（`seal.key_dir` が XDG の無いとき `~/.config/thth`）と、台帳の中を断ること・0600。"""
    import inspect
    from thth.register import seal
    key_dir = inspect.getsource(seal.key_dir)
    ok = ("inside_register" in inspect.getsource(seal.check_key_place)
          and "0o077" in inspect.getsource(seal._private_ok)
          and '".config"' in key_dir and '"thth"' in key_dir)
    return f"~/.config/thth/{seal.KEY_NAME}</code>, mode 600" if ok else "封の鍵の置き場か権限が privacy と違う"


def _erase_outside_unchecked() -> bool:
    """消す求めが確かめない場所（`erase.OUTSIDE`）に git の履歴・材料・頁・写し・共通の台帳があり、手を入れる場所
    （`erase.RUN_ORDER`）に判断の記録・封・今の本文・前の版・段取りがある。"""
    from thth.register import erase
    return ({"git_history", "materials", "pages", "copies", "clones", "common"} <= set(erase.OUTSIDE)
            and {"judgments", "seals", "current_text", "kept_objects", "batch", "snapshots"} <= set(erase.RUN_ORDER))


def _salt_inside_register() -> str:
    """台帳ごとの秘密の塩の置き場（`route.salt_path`）と、作るのが 0600・台帳の中だけ（`ensure_salt`）。"""
    import inspect
    from thth.register import route, vessel
    ok = "0o600" in inspect.getsource(route.ensure_salt) and route.salt_path("R") == "R/" + "/".join(
        (vessel.DECL_DIR, route.SEALS_DIR, route.SALT_FILE))
    return f"<code>{vessel.DECL_DIR}/{route.SEALS_DIR}/{route.SALT_FILE}</code>" if ok else "塩の置き場が privacy と違う"


# 台帳の中の封の欄（privacy の「台帳の中の封に入る物」）。欄が増えたら privacy を見直す（赤）。
SEAL_BODY_KEYS = {"schema", "outlet", "audience", "generation", "as_of", "judgments", "bytes", "count", "sha256",
                  "decl", "thth", "inputs", "outputs", "items", "fingerprints", "tables", "common_unchecked",
                  "previous"}
# 公開の場所に出す写し（`route.public_of`）の頭の欄と要約の欄、配る物（`route.write_published`）の欄。
PUBLIC_KEYS = {"schema", "outlet", "audience", "generation", "as_of", "at", "decl", "thth", "public_key",
               "common_unchecked", "summary", "judgments", "previous", "erase"}
PUBLIC_SUMMARY_KEYS = {"inputs", "items", "fingerprints", "outputs", "body"}
PUBLISHED_KEYS = {"public", "signature", "deployed"}
DEPLOYED_KEYS = {"outlet", "generation", "judgments", "lines", "at", "dropped"}


def _sample_seal_body() -> dict:
    """番号・版・素の指紋に見分けの付く字を入れた封（公開の写しに漏れないかを見る）。"""
    return {"schema": "thth.seal/1", "outlet": "site", "audience": "reader", "generation": 2,
            "as_of": "2026-11-14T00:00:00+09:00", "judgments": {"bytes": 10, "count": 1, "sha256": "f" * 64},
            "decl": "d" * 16, "thth": "0", "inputs": [{"record": "store:LEAKID", "version": "LEAKVER"}],
            "outputs": {"a.json": "e" * 64}, "items": ["store:LEAKID=fact"],
            "fingerprints": {"items": {"store:LEAKID=fact": "c" * 64}, "tables": {}}, "common_unchecked": False,
            "previous": {"generation": 1, "judgments": {"bytes": 5, "count": 1, "sha256": "b" * 64}}}


def _seal_body_fields() -> bool:
    import inspect
    from thth.register import route
    return set(re.findall(r'"(\w+)":', inspect.getsource(route.seal_body))) == SEAL_BODY_KEYS


def _public_copy_salted() -> bool:
    """公開の写しと配る物を実際に作り、欄の並びが決めた物と同じで、番号・版・素の指紋が入らないことを見る。"""
    import datetime
    import json
    import tempfile
    from thth.register import route
    body, salt = _sample_seal_body(), b"s" * route.SALT_BYTES
    public = route.public_of(body, salt)
    raw = json.dumps({"id": "j-LEAKLINE", "by": "LEAKWHO", "question": "use", "target": {"record": "store:LEAKID"},
                      "answer": {"word": "ok"}, "at": "2026-11-14T00:00:00+09:00"}).encode() + b"\n"
    with tempfile.TemporaryDirectory() as d:
        path = d + "/thth-seal.site.json"
        route.write_published(path, {"body": body, "signature": "SIG"}, raw,
                              datetime.datetime(2026, 11, 14, tzinfo=datetime.timezone.utc), ["x"], salt)
        with open(path, encoding="utf-8") as f:
            published = json.load(f)
    text = json.dumps(published)
    return (set(public) == PUBLIC_KEYS and set(public["summary"]) == PUBLIC_SUMMARY_KEYS
            and set(published) == PUBLISHED_KEYS and set(published["deployed"]) == DEPLOYED_KEYS
            and published["public"] == public and published["deployed"]["dropped"] == 1
            and not any(w in text for w in ("LEAK", "f" * 64, "c" * 64, "e" * 64, "b" * 64)))


def _seal_holds_no_values() -> bool:
    return _seal_body_fields() and _public_copy_salted()


def _signature_over_public() -> bool:
    """封の署名の対象は公開の写しの中身（`route.seal_message` が `public_of` の決まった並び）。"""
    from thth.register import route, vessel
    body, salt = _sample_seal_body(), b"s" * route.SALT_BYTES
    return route.seal_message(body, salt) == vessel.canonical(route.public_of(body, salt)).encode("utf-8")


def _erase_drops_signature_and_rechecks() -> bool:
    """消す求めが範囲の封から署名も外し（`erase._place_seals` の中）、持ち主の「済んだ」の最後の確かめは、器が
    もう一度読んでから受ける（`erase.done` が `self_check` を呼ぶ）。"""
    import inspect
    from thth.register import erase
    seals = inspect.getsource(erase)
    done = inspect.getsource(erase.done)
    return ('pop("signature"' in seals and 'place == "self_check"' in done and "self_check(root" in done)


def _leave_refuses_register_like() -> bool:
    """THTH の置き場の中の台帳は控えない（`ledger.check_outside_thth`）・消す木の中の `.thth/` を持つフォルダで止まる
    （`owner` の頭の点検）・見積りの所属を記録に残す・済んだ記録は `done/` に残す。"""
    import inspect
    from thth.register import ledger, owner
    src = inspect.getsource(owner)
    return ("inside_thth_state" in inspect.getsource(ledger.check_outside_thth)
            and "`.thth/` を持つフォルダ" in src and owner.DONE_DIR == "done" and "見積りのときの控えの所属" in src)


def _shared_private_not_inputs() -> bool:
    """運営者と共通の出さない欄で記録ごと外した物は、封の入力（`inputs.pop()`）にも差し込み（`_resolve_inserts` が
    `shared_private` を見る）にも出ない。"""
    import inspect
    from thth.register import route
    compute = inspect.getsource(route.compute)
    return (compute.count("inputs.pop()") >= 2 and "shared_private" in inspect.getsource(route._resolve_inserts))


def _shared_private_fence() -> bool:
    """共通の出さない欄（段 8 §3）: 運営者の種類は記録ごと出さない既定を、共通の宣言を読んだ**あと**に重ね（外せない）、
    メディアの出し口の宣言は読まない。除けない記録は外す。"""
    import inspect
    from thth.register import route
    src = inspect.getsource(route.shared_private)
    return (route.SHARED_PRIVATE_ALWAYS == {"operator": "*"}
            and src.index("out.update(SHARED_PRIVATE_ALWAYS)") > src.index("parse_shared_private")
            and "shared_private_unremovable" in inspect.getsource(route._shared_fence))


def facts() -> list[tuple[str, str]]:
    return [
        ("relay の認可コード（relay-object.js CODE_MS）",
         f"at most {seconds(js_const('relay-object.js', 'CODE_MS'))} seconds after receipt"),
        ("relay の認可コード・日本語", f"{seconds(js_const('relay-object.js', 'CODE_MS'))}秒"),
        ("relay の session（relay-object.js STATE_MS）",
         f"{seconds(js_const('relay-object.js', 'STATE_MS'))}-second session lifetime"),
        ("PBKDF2 の回数（callback person.js ITERATIONS）",
         f"{js_const('person.js', 'ITERATIONS'):,} iterations"),
        ("PBKDF2 の回数（thth/relay.py ITERATIONS）", f"{relay.ITERATIONS:,} iterations"),
        ("/activity の要約と頼まれた操作（person-object.js ACTIVITY_TTL）",
         f"Activity summaries and requested actions on thth.me: at most "
         f"{hours_en(js_const('person-object.js', 'ACTIVITY_TTL'))}"),
        ("頼まれた操作（person-object.js ACTION_TTL）",
         f"Activity summaries and requested actions on thth.me: at most "
         f"{hours_en(js_const('person-object.js', 'ACTION_TTL'))}"),
        ("api/v1 の頼み（person-object.js API_REQUEST_MS）",
         f"Command requests on thth.me/api/v1: at most {seconds(js_const('person-object.js', 'API_REQUEST_MS'))} seconds"),
        ("api/v1 の結果（person-object.js API_RESULT_MS）",
         f"their results: at most {minutes(js_const('person-object.js', 'API_RESULT_MS'))} minutes"),
        ("/activity のサインイン（login-object.js LOGIN_TTL）",
         f"Sign-in to the activity page: {minutes(js_const('login-object.js', 'LOGIN_TTL'))} minutes"),
        ("アップロードの capability URL（media.js MEDIA_TTL）",
         f"{minutes(js_const('media.js', 'MEDIA_TTL'))} minutes for uploads"),
        ("R2 の置き場の消し（media.js RAW_RETENTION）",
         f"{js_const('media.js', 'RAW_RETENTION') // 3_600_000} hours after it was created"),
        ("トピックマップの語の数（map_store.MAX_NODES）", f"at most {map_store.MAX_NODES} per project"),
        ("トピックマップの語の数・日本語", f"最大 {map_store.MAX_NODES} 語"),
        ("トピックマップの保持（map_store.MAX_RETENTION_DAYS）", f"for at most {map_store.MAX_RETENTION_DAYS} days"),
        ("トピックマップの保持・日本語", f"最大 {map_store.MAX_RETENTION_DAYS} 日保存します"),
        ("少ない数は保存しない（map_world.SMALL_SAMPLE）", f"A number below {map_world.SMALL_SAMPLE}"),
        ("少ない数は保存しない・日本語", f"{map_world.SMALL_SAMPLE} 未満の数"),
        ("Meta の通知の保持（webhook-object.js RETENTION・7 * 24 … の頭の日数）",
         f"at most {js_const('webhook-object.js', 'RETENTION')} days"),
        ("Meta の通知の保持・日本語", f"最長 {js_const('webhook-object.js', 'RETENTION')} 日"),
        ("命令の控えの保持（meter.KEEP_DAYS）", f"sent nowhere): {meter.KEEP_DAYS} days"),
        ("命令の控えの保持・日本語", f"外へ送りません）: {meter.KEEP_DAYS}日"),
        # 計画 Meta 全部盛り M6: M3・M4・M5 の数（DM の窓・ハッシュタグの柵・広告の呼び出しの数・権限の不使用）。
        ("DM の窓（meta_write.WINDOW）",
         f"within {_hours(meta_write.WINDOW)} hours of the other person's last message"),
        ("DM の窓・日本語", f"相手の最後の通から {_hours(meta_write.WINDOW)} 時間の内"),
        ("人の担当者の窓（meta_write.HUMAN_AGENT_WINDOW）",
         f"human_agent tag, within {meta_write.HUMAN_AGENT_WINDOW.days} days of it"),
        ("人の担当者の窓・日本語", f"人の担当者が {meta_write.HUMAN_AGENT_WINDOW.days} 日の内に"),
        ("非公開の返事の窓（meta_write.PRIVATE_REPLY_WINDOW）",
         f"is sent within {meta_write.PRIVATE_REPLY_WINDOW.days} days of the comment"),
        ("非公開の返事の窓・日本語", f"コメントから {meta_write.PRIVATE_REPLY_WINDOW.days} 日の内に"),
        ("IG のハッシュタグの柵（meta_public.HASHTAG_LIMIT・HASHTAG_WINDOW）",
         f"at most {meta_public.HASHTAG_LIMIT} distinct hashtags in {meta_public.HASHTAG_WINDOW.days} days"),
        ("IG のハッシュタグの柵・日本語", f"{meta_public.HASHTAG_WINDOW.days} 日で {meta_public.HASHTAG_LIMIT} 種"),
        ("IG のハッシュタグの控えの保持（meta_public.HASHTAG_WINDOW）",
         f"the hashtag and the first time only): {meta_public.HASHTAG_WINDOW.days} days"),
        ("IG のハッシュタグの控えの保持・日本語", f"タグと最初の時刻だけ）: {meta_public.HASHTAG_WINDOW.days} 日"),
        ("広告の呼び出しの数の保持（meta/calls.KEEP_DAYS）", f"per-day counts for the last {meta_calls.KEEP_DAYS} days"),
        ("広告の呼び出しの数の保持・日本語", f"日ごとの数を直近 {meta_calls.KEEP_DAYS} 日"),
        ("広告の呼び出しの成否の窓（meta/calls.RATE_WINDOW）", f"each of the last {meta_calls.RATE_WINDOW} calls"),
        ("広告の呼び出しの成否の窓・日本語", f"直近 {meta_calls.RATE_WINDOW} 回"),
        ("広告の予算を延ばす口（ads extend・二段確認）", "lifetime budget within the owner's limits after a two-step"),
        ("広告の予算を延ばす口・日本語", "上限の内側で広告セットの終了日や通しの予算を延ばす"),
        ("店を読む送り先（shop/wire.BASE_HOST）", f"GET requests to <code>{shop_wire.BASE_HOST}</code>, to two paths"),
        ("店を読む送り先・日本語", f"<code>{shop_wire.BASE_HOST}</code> への GET だけで、道は {len(shop_wire.BASE_PATHS)} つ"),
        ("店を読む道の数（shop/wire.BASE_PATHS）", "to two paths" if len(shop_wire.BASE_PATHS) == 2 else "道の数が privacy と違う"),
        ("店の鍵の置き場（shop/ledger.default_token）",
         f"<code>{shop_ledger.default_token('x').rsplit('/', 1)[0]}/</code>, mode 600); THTH has no path that sends it"),
        ("店の鍵の置き場・日本語", f"<code>{shop_ledger.default_token('x').rsplit('/', 1)[0]}/</code>・600）に置きます"),
        ("店の注文を読まない道（BASE_PATHS に注文が無い）",
         "THTH does not read orders" if not any("order" in x for x in shop_wire.BASE_PATHS) else "注文の道がある"),
        ("承認の読み直しは鍵のある機械だけ（shop/recheck）", "on a computer without the token it does not read again"),
        ("承認の読み直しは鍵のある機械だけ・日本語", "トークンの無いコンピューターでは読み直さず"),
        ("投稿の時刻には読み直さない", "does not read the item again at posting time"),
        ("投稿の時刻には読み直さない・日本語", "投稿する時刻には読み直しません"),
        ("leave は打った機械の分だけ（leave._purge_stores）", "deletes the shop files on the computer where it runs"),
        ("leave は打った機械の分だけ・日本語", "打ったコンピューターにあるショップのファイルを消します"),
        ("鍵の期限の知らせ（shop/ledger.EXPIRY_NOTICE_DAYS）", f"can warn {shop_ledger.EXPIRY_NOTICE_DAYS} days ahead"),
        ("鍵の期限の知らせ・日本語", f"{shop_ledger.EXPIRY_NOTICE_DAYS} 日前から知らせられます"),
        ("店の注文を読まない", "THTH does not read orders or anything about the shop's customers"),
        ("店の注文を読まない・日本語", "注文やショップのお客さんの情報は読まず"),
        # Shopify（5.17.0・設計 Shopify と STORES §9）。
        ("Shopify の送り先（shop/wire.SHOPIFY_SUFFIX）",
         f"shop at <code>&lt;shop&gt;{shop_wire.SHOPIFY_SUFFIX}</code>, of two kinds"),
        ("Shopify の送り先・日本語", f"<code>&lt;shop&gt;{shop_wire.SHOPIFY_SUFFIX}</code> だけで、"),
        ("Shopify の問い合わせの数（shop/wire.QUERIES）",
         f"and {len(shop_wire.QUERIES)} fixed queries that read the shop's name and the products"),
        ("Shopify の問い合わせの数・日本語", f"ショップの名前と商品を読む決まった {len(shop_wire.QUERIES)} つの問い合わせ"),
        ("Shopify の道は POST だけ（shop/wire.ROUTES）",
         "They are POST requests" if {m for m, _p in shop_wire.ROUTES["shopify"]["paths"]} == {"POST"}
         else "Shopify の道に POST でないものがある"),
        ("Shopify の短い鍵の置き場（shop/ledger.default_access）",
         f"in a separate file in the same place (<code>{shop_ledger.default_access('x').rsplit('/', 1)[0]}/</code>, mode 600)"),
        ("Shopify の短い鍵の置き場・日本語",
         f"同じ置き場の別のファイル（<code>{shop_ledger.default_access('x').rsplit('/', 1)[0]}/</code>・600）"),
        ("Shopify の求める scope（shop/shopify.REQUIRED_SCOPE）",
         f"with the read scope <code>{shop_shopify.REQUIRED_SCOPE}</code>"),
        ("Shopify の短い鍵の長さ（shop/access.MAX_LIFETIME_SECONDS）",
         "valid for about 24 hours" if shop_access.MAX_LIFETIME_SECONDS == 24 * 3600 else "短い鍵の長さが privacy と違う"),
        # STORES（5.18.0・設計 Shopify と STORES §9）。注文を読まない柵は道の表だけ（クレデンシャルに何が許されているかを THTH は選べない）。
        ("STORES の送り先（shop/wire.STORES_HOST）",
         f"THTH sends only GET requests to <code>{shop_wire.STORES_HOST}</code>, to "
         + ("two" if len(shop_wire.STORES_PATHS) == 2 else "道の数が privacy と違う") + " paths"),
        ("STORES の送り先・日本語",
         f"<code>{shop_wire.STORES_HOST}</code> への GET だけで、道は {len(shop_wire.STORES_PATHS)} つです"),
        ("STORES の道は GET だけ（shop/wire.ROUTES）",
         f"only GET requests to <code>{shop_wire.STORES_HOST}</code>"
         if {m for m, _p in shop_wire.ROUTES["stores"]["paths"]} == {"GET"} else "STORES の道に GET でないものがある"),
        ("STORES の注文と入金を読まない（STORES_PATHS に無い）",
         "so it does not read orders or payouts"
         if not any(w in p for p in shop_wire.STORES_PATHS for w in ("order", "remittance")) else "注文か入金の道がある"),
        ("STORES の注文と入金を読まない・日本語",
         "注文や入金は読みません"
         if not any(w in p for p in shop_wire.STORES_PATHS for w in ("order", "remittance")) else "注文か入金の道がある"),
        ("STORES の店の URL は持ち主が入れる（kinds の台帳の欄）",
         "the owner enters the shop page's URL when adding the shop"
         if "public_url" in shop_kinds.KINDS["stores"]["ledger_fields"] else "店の URL を台帳に持たない"),
        ("STORES の期限の知らせ（shop/ledger.EXPIRY_NOTICE_DAYS）",
         f"the credential's expiry date, THTH keeps it, and <code>thth morning</code> on that computer can warn "
         f"{shop_ledger.EXPIRY_NOTICE_DAYS} days ahead"),
        ("STORES の残す欄（店の id と名前だけ）",
         "of which THTH keeps only the shop's ID and name" if _stores_user_keeps_id_and_name() else "店の欄が増えた"),
        # 監査 5.17.0 直したほうがよい 3: 画像の URL は下書きに入れない（draft の頭が from_store_image: none）。
        ("下書きに画像の URL を入れない（shop/draft.text_for）",
         "drafts carry the item page's URL but not image URLs" if _draft_has_no_image() else "下書きに画像の URL がある"),
        ("下書きに画像の URL を入れない・日本語",
         "下書きに入るのは商品の頁の URL で、画像の URL は入れません" if _draft_has_no_image() else "下書きに画像の URL がある"),
        ("店のリダイレクトを追わない（shop/wire.OPENER）",
         "THTH does not follow redirects from the shop"
         if any(isinstance(h, shop_wire._NoRedirect) for h in shop_wire.OPENER.handlers) else "リダイレクトを追う"),
        ("店のリダイレクトを追わない・日本語",
         "ショップからのリダイレクトは追いません"
         if any(isinstance(h, shop_wire._NoRedirect) for h in shop_wire.OPENER.handlers) else "リダイレクトを追う"),
        # 台帳の機械（計画 実店舗 段 1〜4）。台帳に書く欄・網に出ない・控えに残す物・退出で消える物。
        ("台帳に THTH が書く欄（register/form.MANAGED）",
         "THTH writes in the register only the fixed head fields " + _register_fields_en()),
        ("台帳に THTH が書く欄・日本語",
         "台帳に THTH が書くのは、頭の決まった欄 " + "・".join(f"<code>{n}</code>" for n in register_form.MANAGED) + " と、"),
        ("台帳の機械は網に出ない（thth/register が網の部品を import しない）",
         "this part of THTH has no network access" if _register_offline() else "台帳の機械が網の部品を import している"),
        ("台帳の機械は網に出ない・日本語",
         "この部分の THTH は網に出ません" if _register_offline() else "台帳の機械が網の部品を import している"),
        ("台帳の下書きには店の頁もレジも使わない（register の import の表に thth.shop が無い）",
         "these drafts do not use the shop's web pages or its till" if _register_uses_no_shop()
         else "台帳の機械が店の口を import している"),
        ("台帳の下書きには店の頁もレジも使わない・日本語",
         "この下書きには、店の頁もレジも使いません" if _register_uses_no_shop() else "台帳の機械が店の口を import している"),
        ("台帳の下書きの控えの欄（register/recheck.ORIGIN_KEYS と not_after・監査 5.19.0 の 1）",
         "the row, the fingerprint, the register's commit, the trigger and the deadline"
         if _register_draft_record_keys() == ["from_register", "from_register_digest", "from_register_commit",
                                              "from_register_trigger", "not_after"] else "下書きの控えの欄が違う"),
        ("台帳の下書きの控えの欄・日本語", "頭に書いた元の行・中身の指紋・台帳の commit・きっかけ・出さない期限"
         if _register_draft_record_keys() == ["from_register", "from_register_digest", "from_register_commit",
                                              "from_register_trigger", "not_after"] else "下書きの控えの欄が違う"),
        ("台帳の柵が断る行（register/fence）",
         "THTH refuses to use a row that is a guess, that the owner said is wrong, that is past its review date, "
         "that is retired or that is not marked as public" if _register_fence_codes() else "柵の断りが privacy と違う"),
        # 台帳の器（段 1〜8）。外す範囲・置き場・封の鍵・封に入る物・消す求めの範囲・共通の出さない欄。
        ("口座を外しても台帳の控えと判断は残る（leave.run・register/owner.SCOPES）",
         "Removing an account keeps those files and the register's decisions, even for the project's last account"
         if _account_leave_keeps_registers() else "口座の leave が台帳の控えを消す"),
        ("口座を外しても台帳の控えと判断は残る・日本語",
         "口座を外しても、そのファイルと台帳の判断は残ります（プロジェクトの最後の口座でも同じです）"
         if _account_leave_keeps_registers() else "口座の leave が台帳の控えを消す"),
        ("メディアを外すと共通の台帳は残り・封の鍵は持ち主ごとのときだけ（owner.register_plan・leave.scope_plan）",
         "Removing a media project deletes its accounts and THTH's files for its registers, and keeps the shared "
         "register and other projects" if _project_leave_keeps_common() else "外す範囲が privacy と違う"),
        ("持ち主ごとやめると封の秘密の鍵も消す",
         "THTH's files for every register (the shared one too), the owner's principals with the keys THTH holds for "
         "them, and the seal's private key" if _project_leave_keeps_common() else "外す範囲が privacy と違う"),
        ("メディアを外す・持ち主ごと・日本語",
         "共通の台帳とほかのプロジェクトは残します。持ち主ごとやめると" if _project_leave_keeps_common()
         else "外す範囲が privacy と違う"),
        ("判断の記録の置き場（judgment.FILE）", f"<code>{_register_places()['judgments']}</code>"),
        ("封の置き場（route.SEALS_DIR）", f"<code>{_register_places()['seals']}</code>"),
        ("前の版の控え（keep.KEPT）", f"<code>{_register_places()['kept']}</code>"),
        ("入れ替えの段取り（keep.BATCH）", f"<code>{_register_places()['batch']}</code>"),
        ("消す求め（judgment.ERASE_DIR）", f"<code>{_register_places()['erase']}</code>"),
        ("出したアドレスの控え（published.DIR）", f"<code>{_register_places()['published']}</code>"),
        ("共通の台帳の種類の宣言（vessel.KINDS_DIR）", f"<code>{_register_places()['kinds']}</code>"),
        ("封の秘密の鍵の置き場と権限（seal.KEY_NAME・key_dir・_private_ok・check_key_place）",
         _seal_key_default()),
        ("封の秘密の鍵・日本語",
         _seal_key_default().replace("</code>, mode 600", "</code>・600）に置き、台帳の中にも")),
        ("台帳ごとの秘密の塩（route.salt_path・ensure_salt）", _salt_inside_register()),
        ("台帳の中の封の欄（route.seal_body）",
         "The seal in the register holds the outlet's name and who it is for" if _seal_body_fields()
         else "台帳の中の封の欄が privacy の並びと違う"),
        ("台帳の中の封の欄・日本語",
         "台帳の中の封に入るのは、出し口の名前と読む者" if _seal_body_fields() else "台帳の中の封の欄が privacy の並びと違う"),
        ("公開の写しは塩を混ぜた要約だけ（route.public_of・write_published を動かす）",
         "The published copy does not hold record IDs, versions or fingerprints without the salt"
         if _public_copy_salted() else "公開の写しの欄が privacy の並びと違うか、番号・版・素の指紋が漏れる"),
        ("公開の写しは塩を混ぜた要約だけ・日本語",
         "公開する写しには、記録の番号・版・塩を混ぜない指紋は入りません"
         if _public_copy_salted() else "公開の写しの欄が privacy の並びと違うか、番号・版・素の指紋が漏れる"),
        ("署名は公開の写しに作る（route.seal_message）",
         "The signature is made over the published copy" if _signature_over_public() else "署名の対象が違う"),
        ("署名は公開の写しに作る・日本語",
         "署名は公開する写しの中身に作ります" if _signature_over_public() else "署名の対象が違う"),
        ("封と写しに値と説明が入らない（封の欄と公開の写しの欄）",
         "neither the seal nor its copy holds the records' values or the explanations of decisions"
         if _seal_holds_no_values() else "封か写しの欄が privacy の並びと違う"),
        ("封と写しに値と説明が入らない・日本語",
         "封にも写しにも、記録の値と判断の説明は入りません" if _seal_holds_no_values()
         else "封か写しの欄が privacy の並びと違う"),
        ("消す求めは封の署名も外し・最後の確かめは器が読み直す（erase._place_seals・done）",
         "including their signatures (that outlet has no seal until the owner accepts again)"
         if _erase_drops_signature_and_rechecks() else "消す求めの封と確かめが privacy と違う"),
        ("消す求めの最後の確かめ",
         "when the owner marks the final check as done, THTH reads the folder again before accepting it"
         if _erase_drops_signature_and_rechecks() else "消す求めの封と確かめが privacy と違う"),
        ("消す求めの封と最後の確かめ・日本語",
         "言うときは、THTH がフォルダをもう一度読んでから受けます" if _erase_drops_signature_and_rechecks()
         else "消す求めの封と確かめが privacy と違う"),
        ("外すときの台帳らしい物と所属（ledger.check_outside_thth・owner）",
         "THTH refuses to keep a register inside its own storage" if _leave_refuses_register_like()
         else "外すときの点検が privacy と違う"),
        ("外すときの台帳らしい物・英",
         "a folder holding <code>.thth/</code>" if _leave_refuses_register_like() else "外すときの点検が privacy と違う"),
        ("外すときの台帳らしい物と所属・日本語",
         "台帳らしいフォルダ（<code>.thth/</code> を持つフォルダ）があれば、何も消さずに止まります"
         if _leave_refuses_register_like() else "外すときの点検が privacy と違う"),
        ("共通の出さない欄で外した記録は封の入力にも差し込みにも出ない（route.compute・_resolve_inserts）",
         "Records left out this way are not used as seal inputs or inserted values either"
         if _shared_private_not_inputs() else "外した記録が封の入力か差し込みに出る"),
        ("共通の出さない欄で外した記録・日本語",
         "こうして外した記録は、封の入力にも差し込みの値にも使わず" if _shared_private_not_inputs()
         else "外した記録が封の入力か差し込みに出る"),
        ("消す求めが確かめない場所（erase.OUTSIDE・RUN_ORDER）",
         "THTH does not change or check git history, material already written or published, other copies or the "
         "shared register" if _erase_outside_unchecked() else "消す求めの範囲が privacy と違う"),
        ("消す求めが確かめない場所・日本語",
         "git の履歴・書き出した材料や出した頁・ほかの写し・共通の台帳は、THTH は変えず、確かめもしません"
         if _erase_outside_unchecked() else "消す求めの範囲が privacy と違う"),
        ("共通の出さない欄（route.SHARED_PRIVATE_ALWAYS・shared_private）",
         "Records of the operator's details are not put into any material, and fields that the shared register "
         "marks as private are removed from every project's material" if _shared_private_fence()
         else "共通の出さない欄の柵が privacy と違う"),
        ("共通の出さない欄・日本語",
         "運営者の情報の記録はどの材料にも入れず、共通の台帳の側が出さないと決めた欄は、どのプロジェクトの材料からも除きます"
         if _shared_private_fence() else "共通の出さない欄の柵が privacy と違う"),
        # 段 2: 実店舗の形と下書き（既定で出さない欄・画像なし・出さない期限・二段で認める種類）。
        ("店の住所と電話と休みの理由は既定で出さない（register/kinds.private_by_default）",
         "The shop's address and phone number, and the reason for a closure, are not put into drafts unless the owner "
         "marks that field as public" if _register_private_defaults() else "既定で出さない欄が privacy と違う"),
        ("店の住所と電話と休みの理由・日本語",
         "店の住所と電話、臨時の休みの理由は、持ち主がその欄を外に出してよいと書いたときのほかは"
         if _register_private_defaults() else "既定で出さない欄が privacy と違う"),
        ("台帳の下書きに写真を入れない（register/draft.text_for）",
         "THTH puts no photos into these drafts" if _register_draft_has_no_image() else "台帳の下書きに画像がある"),
        ("台帳の下書きの出さない期限と読み直し（register/draft・recheck）",
         "Each draft records the row it came from and a time after which it is not posted"
         if _register_draft_has_not_after() else "台帳の下書きに出さない期限が無い"),
        ("二段で認める種類（register/kinds.two_stage）",
         "Opening hours and temporary closures are confirmed in two steps"
         if [k for k in register_kinds.RULES if register_kinds.two_stage(k)] == ["hours", "special"]
         else "二段で認める種類が privacy と違う"),
        ("二段で認める種類・日本語",
         "営業の時間と臨時の休みは二段で認めます"
         if [k for k in register_kinds.RULES if register_kinds.two_stage(k)] == ["hours", "special"]
         else "二段で認める種類が privacy と違う"),
        # 段 3: 聞いて埋める（問いの記録の置き場・拍子の数・答えだけでは二段の種類を認めない）。
        ("問いの記録の置き場（register/questions.LOG_FILE）",
         f"adds lines to the question log (<code>{register_questions.LOG_FILE}</code>)"),
        ("問いの記録の置き場・日本語", f"問いの記録（<code>{register_questions.LOG_FILE}</code>）に足す行だけ"),
        ("聞く数の上限（register/holes.ASK_MAX_PER_DAY・ASK_GAP_DAYS）",
         f"at most {register_holes.ASK_MAX_PER_DAY} a day, and does not ask about the same thing again within "
         f"{register_holes.ASK_GAP_DAYS} days"),
        ("聞く数の上限・日本語",
         f"1 日 {register_holes.ASK_MAX_PER_DAY} 問までで、同じことは {register_holes.ASK_GAP_DAYS} 日"),
        ("外の場所の頁を読まず貼る文だけ作る（register/places・cli.cmd_compare）",
         "does not read those places' pages, does not sign in to their admin screens and sends them nothing"
         if _register_paste_offline() else "外の場所に貼る束が網に出るか、写した文を残す"),
        ("外の場所の頁を読まず貼る文だけ作る・日本語",
         "THTH はそれらの場所の頁を読まず、管理画面に入らず、何も送りません" if _register_paste_offline()
         else "外の場所に貼る束が網に出るか、写した文を残す"),
        ("比べた文を残さない（register/cli.cmd_compare）",
         "does not keep the copied text" if _register_paste_offline() else "写した文を残す"),
        ("比べた文を残さない・日本語", "写した文は残しません" if _register_paste_offline() else "写した文を残す"),
        ("答えだけでは二段の種類を認めない（register/cli.cmd_answer）",
         "An answer alone does not confirm opening hours or a temporary closure"
         if _answer_respects_two_stage() else "答えで営業の時間を認めてしまう"),
        ("最小の読みの日（permission_watch.KEEP_DAYS）", f"has not been used for {permission_watch.KEEP_DAYS} days"),
        ("最小の読みの日・日本語", f"{permission_watch.KEEP_DAYS} 日使われていない"),
        ("権限の不使用の上限（permission_watch.LIMIT_DAYS）", f"not used for {permission_watch.LIMIT_DAYS} days"),
        ("権限の不使用の上限・日本語", f"{permission_watch.LIMIT_DAYS} 日使わなかった権限"),
        ("利用者ごとの期限（permission_watch.USER_LIMIT_DAYS）",
         f"expire {permission_watch.USER_LIMIT_DAYS} days after the user was last active"),
        ("利用者ごとの期限・日本語", f"{permission_watch.USER_LIMIT_DAYS} 日のデータアクセスの期限"),
    ]


def _hours(delta) -> int:
    assert delta.total_seconds() % 3600 == 0
    return int(delta.total_seconds() // 3600)


@pytest.mark.parametrize("what,phrase", facts(), ids=[f[0] for f in facts()])
def test_privacyの数はコードの定数と同じ(what, phrase):
    page = PRIVACY.read_text(encoding="utf-8")
    assert phrase in page, f"privacy に「{phrase}」が無い（{what}）。コードと privacy（tools/build_site.py）のどちらかを直す"


def test_PBKDF2の回数はWorkerとththで同じ():
    assert js_const("person.js", "ITERATIONS") == relay.ITERATIONS


def _x_mismatches(page, caps, *, metrics, conversation):
    """X の項と adapter の口の食い違いを両方の向きで並べる（空なら合っている）。

    「口があるのに文が無い」と「口が無くなったのに文が残る」の両方を見る（監査 5.14.0b の 6 の残り）。
    """
    item = re.search(r"<li>X: (.*?)</li>", page, re.S)
    if not item:
        return ["privacy に X の項が無い"]
    text = item.group(1)
    search = re.search(r"<strong>Keyword search\.</strong>(.*?)</p>", page, re.S)
    search_en = bool(search and "Mastodon and X" in search.group(1))
    claims = {
        # 口: (英の項にその文があるか, 日の文があるか)
        "metrics": ("metrics" in text and "not collected" not in text, None),
        "conversation": ("replies to its posts" in text, None),
        "repost_like": ("reposts and likes" in text, None),
        "keyword_search": ("search of other people's recent public posts" in text and search_en,
                           "Mastodon・X の検索" in page),
        "thread_read": ("replies in one post's conversation" in text, None),
    }
    has = {
        "metrics": metrics,
        "conversation": conversation,
        "repost_like": {"repost", "like"} <= caps,
        "keyword_search": "keyword_search" in caps,
        "thread_read": "thread_read" in caps,
    }
    out = []
    for name, (en, ja) in claims.items():
        said = en if ja is None else (en or ja)
        whole = en if ja is None else (en and ja)
        if has[name] and not whole:
            out.append(f"{name}: 口があるのに privacy に文が無い")
        if not has[name] and said:
            out.append(f"{name}: 口が無いのに privacy に文が残っている")
    reads_others = has["keyword_search"] or has["thread_read"]
    if reads_others and "not saved" not in text:
        out.append("他人の投稿を読むのに「保存しない」が無い")
    return out


def test_Xの項はadapterの口と合う():
    """X の adapter の口と privacy の X の項が、両方の向きで合う（11/09・監査 5.14.0 と 5.14.0b）。

    9/27 の文「Replies and metrics are not collected」が、c8511ae（5.13.0）で数を記録したあとも残っていた。
    """
    from thth.adapters.x import XAdapter

    page = PRIVACY.read_text(encoding="utf-8")
    assert _x_mismatches(page, set(XAdapter.CAPABILITIES), metrics=hasattr(XAdapter, "owned_metrics"),
                         conversation=hasattr(XAdapter, "conversation")) == []


def test_X_口が無くなったのに文が残れば赤になる():
    """柵が逆の向きでも効くことを、口を 1 つずつ外した形で確かめる（わざと壊して赤を見る）。"""
    from thth.adapters.x import XAdapter

    page = PRIVACY.read_text(encoding="utf-8")
    caps = set(XAdapter.CAPABILITIES)
    for gone in ("keyword_search", "thread_read"):
        found = _x_mismatches(page, caps - {gone}, metrics=True, conversation=True)
        assert f"{gone}: 口が無いのに privacy に文が残っている" in found
    assert any("metrics" in m for m in _x_mismatches(page, caps, metrics=False, conversation=True))
    assert any("conversation" in m for m in _x_mismatches(page, caps, metrics=True, conversation=False))
    assert any("repost_like" in m for m in _x_mismatches(page, caps - {"like"}, metrics=True, conversation=True))


def test_WhatsAppの項は口と合い_保存しないと言う():
    """thth wa があるなら、privacy に WhatsApp の節があり、番号と本文を保存しないと日英で言う（監査 5.14.0b の 3）。
    端末や確認の段数に縛った言い方はしない（原則 2026-11-08）。"""
    import importlib.util
    if importlib.util.find_spec("thth.wa.cli") is None:
        return
    page = PRIVACY.read_text(encoding="utf-8")
    en = re.search(r"<h2>WhatsApp</h2>\s*<p>(.*?)</p>", page, re.S)
    assert en, "privacy に WhatsApp の節が無い"
    body = en.group(1)
    assert "recipient's number" in body and "does not save them" in body
    assert "terminal" not in body
    ja = re.findall(r"<h2>WhatsApp</h2>\s*<p>(.*?)</p>", page, re.S)
    assert len(ja) == 2, "WhatsApp の節が日英の 2 つでない"
    assert "相手の番号" in ja[1] and "保存しません" in ja[1] and "端末" not in ja[1]


# ------------------------------------------------------------------ WordPress の記事（5.16.0 監査 直したほうがよい 3）

_NUMBER_WORDS = {2: "two", 3: "three", 4: "four", 5: "five"}


def _wp_section(page: str, heading: str) -> str:
    found = re.search(rf"<h2>{re.escape(heading)}</h2>\s*<p>(.*?)</p>", page, re.S)
    assert found, f"privacy に「{heading}」の節が無い"
    return found.group(1)


def test_WordPressの節の道はadapterのROUTESと同じ():
    """privacy の WordPress の節が名指しする REST の道が、adapter の `ROUTES`（これ以外は組まない）と同じ数・同じ名。"""
    from thth.adapters import wordpress

    page = PRIVACY.read_text(encoding="utf-8")
    en = _wp_section(page, "Blog articles on WordPress")
    ja = _wp_section(page, "WordPress の記事")
    n = len(wordpress.ROUTES)
    assert f"It uses {_NUMBER_WORDS[n]} REST paths" in en
    assert f"道は {n} つです" in ja
    names = {"posts": ("posts (", "投稿（"), "categories": ("categories", "カテゴリー"), "tags": ("tags", "タグ"),
             "users/me": ("users/me", "users/me")}
    assert set(wordpress.ROUTES) == set(names), "adapter の道が増えた・減った。privacy の WordPress の節を直す"
    for route in wordpress.ROUTES:
        assert names[route][0] in en and names[route][1] in ja, route


def test_WordPressのadapterはROUTESの外でRESTを組まない():
    """REST の URL は `_route` の 1 か所でだけ組む（privacy の 4 つの道の柵が、道を足しても素通りしないように）。"""
    src = (ROOT / "thth" / "adapters" / "wordpress.py").read_text(encoding="utf-8")
    uses = [line for line in src.splitlines() if "REST_PATH}" in line]
    assert uses == ['        return f"{self.site}{REST_PATH}/{route}"'], uses


def test_WordPressの照合で読む件数はRECENT_POSTSと同じ():
    from thth.adapters import wordpress

    page = PRIVACY.read_text(encoding="utf-8")
    assert f"reads the {wordpress.RECENT_POSTS} most recent posts" in _wp_section(page, "Blog articles on WordPress")
    assert f"最近の {wordpress.RECENT_POSTS} 件" in _wp_section(page, "WordPress の記事")
    src = (ROOT / "thth" / "adapters" / "wordpress.py").read_text(encoding="utf-8")
    assert '"per_page": RECENT_POSTS' in src


def test_WordPressの鍵を消す道は今ある旗():
    """privacy が書く `thth principal auth <主体> --forget` が、命令の旗として今ある。"""
    from thth import cli

    parser = cli.build_parser()
    sub = next(a for a in parser._actions if getattr(a, "choices", None) and "principal" in a.choices)
    auth = next(a for a in sub.choices["principal"]._actions if getattr(a, "choices", None)
                and "auth" in a.choices).choices["auth"]
    assert "--forget" in {o for a in auth._actions for o in a.option_strings}
    page = PRIVACY.read_text(encoding="utf-8")
    assert "thth principal auth &lt;name&gt; --forget" in _wp_section(page, "Blog articles on WordPress")
    assert "thth principal auth &lt;主体&gt; --forget" in _wp_section(page, "WordPress の記事")


# 5.18.2（監査 次の版・2026-11-13 の直したほうがよい 2）: users/me の応答の `link`（著者の頁）のホストを入口の
# ホストと比べ、違うときは doctor が両方のホストを診断の記録（doctor.json の detail）に残す。privacy の文はこの
# 事実と結ぶ。コードが比べるのをやめる・記録に残すのをやめたら、文も直さないと赤になる（逆も同じ）。
_WP_HOST_SENTENCES = {
    "reads": ("reads the address of that user's author page and compares its host name with the host name it "
              "sends requests to",
              "その利用者の著者の頁のアドレスも読み、そのホスト名を THTH が要求を送るホスト名と比べます"),
    "keeps": ("THTH shows both host names on the terminal and keeps them in the account's check record",
              "2 つのホスト名を端末に出し、口座の診断の記録に残します"),
    "route": ("users/me (to check which user the password belongs to, its roles, and whether the site builds its "
              "page addresses on the host name THTH sends requests to)",
              "users/me（パスワードがどの利用者のものか、その役割は何か、サイトが頁のアドレスを THTH の送り先と同じ"
              "ホスト名で組んでいるか）"),
}


def _wp_host_facts(monkeypatch) -> dict:
    """網に出ずに、whoami が `link` のホストを入口と比べるか・doctor が両方のホストを記録の欄に入れるかを見る。"""
    from thth import doctor, principals as principals_mod
    from thth.adapters import wordpress

    principal = {"principal": "wp-x", "kind": "wordpress", "wp_user": "editor1",
                 "blogs": [{"blog_id": "example.com", "lanes": ["lane-x"]}]}
    adapter = wordpress.WordPressAdapter({"blog_id": "example.com"}, principal,
                                         token={"application_password": "abcd efgh ijkl mnop"})
    body = {"id": 2, "username": "editor1", "roles": ["editor"], "link": "https://www.example.com/author/editor1/"}

    def fake_get(self, url, *, op="get"):
        assert url.endswith("/wp/v2/users/me?context=edit"), url
        return body
    monkeypatch.setattr(wordpress.WordPressAdapter, "_get", fake_get)
    who = adapter.whoami()
    reads = who.get("home_host") == "www.example.com" and adapter.site_host == "example.com"
    monkeypatch.setattr(principals_mod, "load_principal", lambda name: principal)
    monkeypatch.setattr(wordpress, "factory", lambda account_cfg, principal_cfg: adapter)
    got = doctor._diagnose_wordpress("wp-x-acct", {"principal": "wp-x", "blog_id": "example.com"})
    [probe] = got["probes"]
    keeps = ("www.example.com" in probe["detail"] and "example.com" in probe["detail"].replace("www.example.com", "")
             and "detail" in doctor.OBSERVATION_FIELDS)
    return {"reads": reads, "keeps": keeps, "route": reads}


def test_WordPressの記事のURLのホストを比べて記録に残すことをprivacyが書く(monkeypatch):
    facts = _wp_host_facts(monkeypatch)
    assert facts["reads"] and facts["keeps"], facts               # 今のコードの事実（変えたら下の文も変える）
    page = PRIVACY.read_text(encoding="utf-8")
    en = _wp_section(page, "Blog articles on WordPress")
    ja = _wp_section(page, "WordPress の記事")
    for key, (en_text, ja_text) in _WP_HOST_SENTENCES.items():
        assert (en_text in en) is facts[key], key
        assert (ja_text in ja) is facts[key], key


# 文字列だけの柵（コードの定数と結んでいない・字面がある・無いだけを見る）。WordPress の節の事実のうち、
# 定数に寄せられないもの（https・転送・上書きしない・題と本文と分類）。
WORDPRESS_WORDS_ONLY = (
    ("文字列だけ: https で送る", "over HTTPS (plain HTTP is accepted only for a test site", "HTTPS で送ります"),
    ("文字列だけ: 外の手直しは上書きしない", "THTH stops and reports instead of overwriting it", "上書きせずに止めて知らせます"),
    ("文字列だけ: 送る中身", "the title, the body converted to HTML, the categories and tags",
     "題・HTML に変えた本文・カテゴリーとタグ"),
    ("文字列だけ: 鍵の置き場と 600", "<code>~/.config/thth/</code>, mode 600", "<code>~/.config/thth/</code>・600"),
)


@pytest.mark.parametrize("what,en,ja", WORDPRESS_WORDS_ONLY, ids=[w[0] for w in WORDPRESS_WORDS_ONLY])
def test_WordPressの節の文字列だけの柵(what, en, ja):
    page = PRIVACY.read_text(encoding="utf-8")
    assert en in _wp_section(page, "Blog articles on WordPress"), what
    assert ja in _wp_section(page, "WordPress の記事"), what


def test_店の画像のURLを下書きに書くとは言わない():
    """監査 5.17.0 直したほうがよい 3: 前の文「下書きに書くだけ」は画像の URL について事実と違った。"""
    page = PRIVACY.read_text(encoding="utf-8")
    for old in ("are only written into drafts", "下書きに書くだけ"):
        assert old not in page


def test_STORESの鍵の中身を言い切らずTHTHの事実だけを書く():
    """監査 5.18.0 直したほうがよい 3: 「権限を絞れず、注文も読める鍵」は STORES の側のことで【推測】だった。"""
    page = PRIVACY.read_text(encoding="utf-8")
    for old in ("cannot be narrowed", "it can also read orders", "権限を絞れず", "注文も読める鍵"):
        assert old not in page, old
    assert "THTH cannot choose what the credential is allowed to do" in page
    assert "クレデンシャルに何が許されているかを THTH は選べません" in page


# ------------------------------------------------------------------ 記事の口座の退出（5.18.1）

def _leave_section(page: str, heading: str) -> str:
    found = re.search(rf"<h2[^>]*>{re.escape(heading)}</h2>(.*?)<h2", page, re.S)
    assert found, f"privacy に「{heading}」の節が無い"
    return found.group(1)


def _article_leave_facts():
    """privacy の退出の節の記事の口座の文を、`thth/leave.py` の振る舞いから組む（違えば赤になる文を返す）。"""
    from thth import article_media, leave

    media = sorted(article_media.ARTICLE_MEDIA)
    named = media == ["hatenablog", "wordpress"]
    keeps = all(leave._principal_owns_token({"media": m}) for m in media)
    def remote(m):
        try:
            return leave.revoke({"media": m}, {}, [], lambda: None)
        except ValueError as exc:
            return str(exc)

    manual = all(remote(m) == "unconfirmed_manual" for m in media)
    stops = callable(getattr(leave, "_guard_principal_credentials", None))
    wp_place = "プロフィール" in leave.MANUAL_REVOKE["wordpress"]
    hatena_place = "連携アプリ" in leave.MANUAL_REVOKE["hatenablog"]
    return [
        ("記事の媒体は WordPress とはてなブログ（article_media.ARTICLE_MEDIA）",
         "For a WordPress or Hatena Blog account" if named else "記事の媒体が増えた・減った",
         "WordPress とはてなブログの口座では" if named else "記事の媒体が増えた・減った"),
        ("主体の鍵は口座の leave で残す（leave._principal_owns_token）",
         "so <code>thth account leave</code> keeps that file" if keeps else "口座の leave が主体の鍵を数える",
         "<code>thth account leave</code> はそのファイルを残します" if keeps else "口座の leave が主体の鍵を数える"),
        ("遠隔の取り消しは本人の手（leave.revoke が unconfirmed_manual）",
         "Revoking it is done by the person" if manual else "THTH が遠隔で取り消す",
         "取り消しは本人が行います" if manual else "THTH が遠隔で取り消す"),
        ("WordPress の取り消しの場所（leave.MANUAL_REVOKE と同じ）",
         "under the user's profile (Application Passwords)" if wp_place else "完了の案内と場所が違う",
         "利用者のプロフィール（アプリケーションパスワード）" if wp_place else "完了の案内と場所が違う"),
        ("はてなの取り消しの場所（leave.MANUAL_REVOKE と同じ）",
         "removing THTH from the applications linked" if hatena_place else "完了の案内と場所が違う",
         "アカウントの設定の連携アプリから THTH を外します" if hatena_place else "完了の案内と場所が違う"),
        ("主体の鍵が消す所の中にあれば何も消さずに止まる（leave._guard_principal_credentials・監査 5.18.1 の 1）",
         "the exit stops before deleting anything" if stops else "主体の鍵が消す所にあっても進む",
         "にあるときは、何も消さずに止まります" if stops else "主体の鍵が消す所にあっても進む"),
        ("主体の鍵を消す命令（--forget）",
         "<code>thth principal auth &lt;name&gt; --forget</code> deletes it on the computer where it runs",
         "<code>thth principal auth &lt;主体&gt; --forget</code> で、打ったコンピューターのファイルを消せます"),
    ]


@pytest.mark.parametrize("what,en,ja", _article_leave_facts(), ids=[f[0] for f in _article_leave_facts()])
def test_記事の口座の退出の文はleaveの振る舞いと同じ(what, en, ja):
    page = PRIVACY.read_text(encoding="utf-8")
    assert en in _leave_section(page, "Stopping access and deleting data"), what
    assert ja in _leave_section(page, "停止とデータの削除"), what


def test_5181_種類ごとの値段と下書きの上限はコードと同じ():
    """5.18.1: 写しと下書きに種類ごとの値段が入った（160c664）。下書きの上限は draft.VARIANTS_IN_DRAFT_MAX。"""
    from thth.shop import draft as shop_draft
    page = PRIVACY.read_text(encoding="utf-8")
    n = shop_draft.VARIANTS_IN_DRAFT_MAX
    assert f"the price of each variant with its ID (at most {n} variants)" in page
    assert f"種類ごとの値段とその ID（{n} 種類まで）" in page
    assert "the price of each variant with the variant's ID" in page
    assert "種類（バリエーション）ごとの値段とその種類の ID" in page


def test_5181_Shopifyの送り直しの文は回数の上限があるときだけ():
    """「数回だけ送り直す」は、shopify.py の 1 回の読み全体の上限（打ち直しの和・眠る秒の和）と結ぶ（4e4dd4d・監査 5.18.1 の 2）。

    問い合わせごとの回数（`MAX_SHRINKS`・`THROTTLE_RETRIES`）だけでは、頁ごとに混むと読み全体で数十回になりうる。
    """
    page = PRIVACY.read_text(encoding="utf-8")
    assert 0 < shop_shopify.MAX_SHRINKS <= 3 and 0 < shop_shopify.THROTTLE_RETRIES <= 3
    n, seconds = shop_shopify.READ_RESENDS_MAX, shop_shopify.READ_WAIT_SECONDS_MAX
    assert 0 < n <= 4 and float(seconds).is_integer() and 0 < seconds <= 60
    assert "sends the same fixed query again a few" in page
    assert (f"(at most {n} times in one read, and at most {int(seconds)} seconds in all of waiting after Shopify "
            "says the shop is busy;") in page
    assert "同じ決まった問い合わせを、頁を小さくするか待ってから数回だけ送り直します" in page
    assert f"（1 回の読みで合わせて {n} 回まで、混んでいると言われて待つのは合わせて {int(seconds)} 秒まで。" in page
    # 「混んでいると言われて待つ」の秒だけを上限に数える（頁の間の待ちは数えない・監査 5.18.1 の確かめの危ない 1）。
    # 頁の間の待ちを上限に数え直すと、文の「混んでいると言われて」が偽になるので赤。
    client = shop_shopify.Client({"shop": "x", "store": "x"}, {})
    real = shop_shopify.SLEEP
    shop_shopify.SLEEP = lambda seconds: None
    try:
        client._pace(1.0)
        assert client.slept == 0.0
        client._sleep(1.0)
        assert client.slept == 1.0
    finally:
        shop_shopify.SLEEP = real
    assert "Between pages, THTH also waits until Shopify's allowance has refilled enough" in page
    assert "頁と頁の間でも、次の頁に足りるだけ Shopify の枠が戻るまで待ちます。" in page


def test_5181_WordPressは外の網のアドレスにだけつなぐ文とadapterの口が合う():
    """5.18.1（6760757）: WordPress の口は httpsafe.public_opener を使う。文があるのに口が外れたら赤。"""
    from thth import httpsafe
    page = PRIVACY.read_text(encoding="utf-8")
    source = (ROOT / "thth" / "adapters" / "wordpress.py").read_text(encoding="utf-8")
    assert callable(getattr(httpsafe, "public_opener", None))
    assert re.search(r"httpsafe\.urlopen\([^\n]*via=httpsafe\.public_opener\(\)\)", source)
    assert "connects only to an address" in _wp_section(page, "Blog articles on WordPress")
    assert "引いた先が外の網のアドレスのときだけつなぎます" in _wp_section(page, "WordPress の記事")


# ------------------------------------------------------------------ 実店舗のレジ（Square・スマレジ・計画 実店舗 段 5）

def _square_location_keeps() -> set:
    """Square の店舗の応答から残す欄（住所・電話・メールを入れた応答で口を通して確かめる）。"""
    import json as _json
    from thth.shop import square as shop_square
    loc = {"id": "L1", "name": "n", "status": "ACTIVE", "merchant_id": "M1", "address": {"x": "address-marker"},
           "phone_number": "phone-marker", "business_email": "e@example.invalid",
           "business_hours": {"periods": [{"day_of_week": "MON", "start_local_time": "9:00:00",
                                           "end_local_time": "18:00:00"}]}}
    client = shop_square.Client({"location": "L1", "public_url": "https://n.example.invalid", "publish": {}}, "t",
                                transport=lambda *a: (200, _json.dumps({"locations": [loc]}).encode()))
    user = client.user()
    assert "marker" not in _json.dumps(user)
    return set(user)


def _pos_route_count(kind) -> int:
    route = shop_wire.ROUTES[kind]
    return len(route["paths"]) + len(route.get("patterns", ()))


def _pos_methods(kind) -> set:
    route = shop_wire.ROUTES[kind]
    return {m for m, _p in route["paths"]} | {m for m, _p, _n in route.get("patterns", ())}


def _smaregi_fields_required(name: str) -> bool:
    """網の柵が、その道の `fields`（1 字も違わない値）の無い問い合わせを打たない（監査 5.19.0 の 5 の a）。"""
    path = {"products": "/c1/pos/products", "store": "/c1/pos/stores/1", "stock": "/c1/pos/stock"}[name]
    base = f"https://{shop_wire.SMAREGI_API_HOST}{path}"
    extra = "&store_id=1&product_id=2" if name == "stock" else ""
    try:
        shop_wire.check_request("GET", base + "?" + extra.lstrip("&") if extra else base)
        return False
    except shop_wire.StoreRefused:
        pass
    shop_wire.check_request("GET", base + "?fields=" + shop_wire.SMAREGI_FIELDS[name] + extra)
    return True


def pos_facts():
    from thth.shop import pos as shop_pos
    from thth.shop import smaregi as shop_smaregi
    from thth.shop import square as shop_square
    words = {3: "three"}
    sq_n, sm_n = _pos_route_count("square"), _pos_route_count("smaregi")
    scopes = shop_wire.SMAREGI_SCOPE.split()
    return [
        ("レジの品は既定で使わない（pos.empty_publish・kinds の台帳の欄）",
         "by default THTH uses none of the register's items"
         if shop_pos.nothing_published(shop_pos.empty_publish())
         and "publish" in shop_kinds.KINDS["square"]["ledger_fields"] else "既定で使う品がある"),
        ("レジの品は既定で使わない・日本語", "THTH は既定でレジのどの品も使いません"
         if shop_pos.nothing_published(shop_pos.empty_publish()) else "既定で使う品がある"),
        ("在庫の数を読む上限（square/smaregi.INVENTORY_MAX）",
         f"at most {shop_square.INVENTORY_MAX} counts in one read"
         if shop_square.INVENTORY_MAX == shop_smaregi.INVENTORY_MAX else "2 つのレジで上限が違う"),
        ("在庫の数を読む上限・日本語", f"1 回の読みで {shop_square.INVENTORY_MAX} までです"),
        ("Square は GET だけ（shop/wire.ROUTES）",
         f"THTH sends only GET requests to <code>{shop_wire.SQUARE_HOST}</code>, to {words.get(sq_n, '道の数が違う')} paths"
         if _pos_methods("square") == {"GET"} else "Square の道に GET でないものがある"),
        ("Square は GET だけ・日本語", f"<code>{shop_wire.SQUARE_HOST}</code> への GET だけで、道は {sq_n} つです"
         if _pos_methods("square") == {"GET"} else "Square の道に GET でないものがある"),
        ("Square の店舗で残す欄（square.Client.user）",
         "keeps only the store's name, whether it is active, the merchant's ID and the weekly business hours"
         if _square_location_keeps() == {"shop_id", "shop_name", "shop_url", "able_to_business", "location", "hours"}
         else "店舗の欄が増えた"),
        ("Square の絞った鍵の scope（pos.KEY_NOTES）",
         "<code>ITEMS_READ</code>, <code>INVENTORY_READ</code> and <code>MERCHANT_PROFILE_READ</code>"
         if all(s in shop_pos.KEY_NOTES["square"][1] for s in ("ITEMS_READ", "INVENTORY_READ", "MERCHANT_PROFILE_READ"))
         else "絞った鍵の scope が privacy と違う"),
        ("スマレジの鍵を取る POST（shop/wire.ROUTES の smaregi_id）",
         f"one kind of POST request, to <code>{shop_wire.SMAREGI_ID_HOST}</code>"
         if _pos_methods("smaregi_id") == {"POST"} and _pos_route_count("smaregi_id") == 1 else "鍵を取る口が違う"),
        ("スマレジの頼む scope（shop/wire.SMAREGI_SCOPE）",
         "for only " + ", ".join(f"<code>{x}</code>" for x in scopes[:-1]) + f" and <code>{scopes[-1]}</code>"),
        ("スマレジの頼む scope・日本語", "・".join(f"<code>{x}</code>" for x in scopes) + " だけの"),
        ("スマレジの GET（shop/wire.ROUTES の smaregi）",
         f"only GET requests to <code>{shop_wire.SMAREGI_API_HOST}</code>, to {words.get(sm_n, '道の数が違う')} paths"
         if _pos_methods("smaregi") == {"GET"} else "スマレジの道に GET でないものがある"),
        ("スマレジの GET・日本語", f"<code>{shop_wire.SMAREGI_API_HOST}</code> への GET だけで、道は {sm_n} つです"),
        ("スマレジの店舗は ID と名前だけ（shop/wire.SMAREGI_FIELDS）",
         "the store (asking only for its ID and name)"
         if shop_wire.SMAREGI_FIELDS["store"] == "storeId,storeName"
         and _smaregi_fields_required("store") else "店舗の欄が増えた（か、fields の無い問い合わせを網の柵が通す）"),
        ("スマレジの商品の欄（shop/wire.SMAREGI_FIELDS）",
         "asking only for the ID, category, name, price and whether stock is counted"
         if shop_wire.SMAREGI_FIELDS["products"] == "productId,categoryId,productName,price,stockControlDivision"
         and _smaregi_fields_required("products") else "商品の欄が増えた（か、fields の無い問い合わせを網の柵が通す）"),
        ("スマレジの取った鍵を残さない（smaregi.py は台帳と鍵のファイルに書かない）",
         "it uses that token for the one command and does not keep it"
         if "ledger" not in (Path(shop_smaregi.__file__).read_text(encoding="utf-8")) else "取った鍵を書く道がある"),
        ("レジの売上と顧客を読まない（道の表に語が無い・tests/test_store_pos_wire.py）",
         "THTH does not read sales, transactions, customers or members, staff, timecards or payments"),
        ("レジの売上と顧客を読まない・日本語", "売上・取引・お客さんや会員・スタッフ・勤怠・支払いは読まず"),
        ("Square の提供でない（kinds の not_by）", "THTH is not provided by Square."
         if "Square が提供するものではありません" in shop_kinds.KINDS["square"]["not_by"] else "not_by が無い"),
        ("スマレジの提供でない（kinds の not_by）", "THTH はスマレジが提供するもの"
         if "スマレジが提供するものではありません" in shop_kinds.KINDS["smaregi"]["not_by"] else "not_by が無い"),
    ]


@pytest.mark.parametrize("what,phrase", pos_facts(), ids=[f[0] for f in pos_facts()])
def test_privacyのレジの文はコードと同じ(what, phrase):
    page = PRIVACY.read_text(encoding="utf-8")
    assert phrase in page, f"privacy に「{phrase}」が無い（{what}）。コードと privacy（tools/build_site.py）のどちらかを直す"
