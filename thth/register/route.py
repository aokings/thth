"""台帳の器 段 3「短い 1 本の道」（設計 `docs/設計_台帳の器_2026-11-13.md` §5.3・§5.6・§7.2・§9 の 3 と 5・§10 の段 3）。

判断を足す → 受け入れて封をする → 材料を書く → 公開の直前に封と公開鍵で確かめる、を 1 本の道にする。
段 1（`vessel.py`・`judgment.py`）と段 2（`keep.py`）は**使うだけ**で、ここでは直さない。

- **出し口の宣言**（`.thth/outlets.json`・`thth.outlets/1`）: 出し口ごとに、読む者（読者・内の道具）・種類・
  粒（記録か内側の単位か）・出してよいかの条件（`requires`）・事実としての条件（`fact`）・留保としての条件
  （`reservation`: 問い・結論・注にしてよい理由の種類）・共通の台帳の種類（`common`）・狭まったときの扱い
  （`on_narrow`: `stop` は赤で止まる・`drop` は CI が外して配る）。決まりは理由の**種類**だけを読み、説明の文は
  読まない（説明は材料に付けて渡す）。
- **使えるか**は、問い合わせのたびに計算する（保存しない）。入力は記録・判断・決まり・判定の日時・用途（出し口と
  何として）。**広げる判断は封の中だけ**（受け入れた判断の集まり＝`judgments.jsonl` の頭から封が言うバイトまで）。
  **狭める物は封を待たない**: 封の後に足した判断も読み、使える段（事実 ＞ 留保 ＞ 使えない）を下げる向きにだけ効かせる。
  止める・期限が切れた・依る所が変わった・照らした物（根拠など）の版が変わった、はどれも封を待たずに効く。
- **止める**は問い `stop`（全部の出し口）か `stop@<出し口>`（その出し口だけ。染めと化学の図鑑の頁）への「だめ」。
  記録の番号に付き、中身が変わっても外れない。外すのは「止めない」（同じ問いへの「よい」）で、広げる判断なので封が要る。
  共通の台帳の「止める」は、採り入れを待たずに、それを採っている全部のメディアの次の材料から外れる（§7.2）。
- **採る**（`take`）は、共通の台帳の記録の版を、メディアの判断として持つ（問い `take`・対象に `ledger: common`）。
  共通を直しても、採り直すまでメディアの材料は変わらない（採り入れ待ち）。採った版の中身は、共通の台帳の
  器の控え（`.thth/kept/objects/`）から取り出す。写しはメディアに作らない。
- **材料の控えと封**（`.thth/seals/<出し口>.json`）: 受け入れた判断の集まり（バイトの数と指紋）・入力の版の組
  （記録と共通の版）・宣言の指紋・判定の器の版・出し口・判定の日時・書いたファイルの sha256・載せた物の一覧・
  **世代の番号**（増えるだけ）・**指紋**（`fingerprints`: 載せた項目ごとの中身の指紋 `items` と、表ごとの書いた行の
  sha256 の並び `tables`。頭は見出し。`on_narrow: drop` で外して配るとき、残りが封のときの物と同じかを見る。11/14 の
  検収の重い 2 で足した。版は上げない。指紋の無い封では外して配らず、受け入れ直す）。持ち主の鍵（`seal.py`）で署名する。
- **確かめ**（`verify`）: 封を、repo の外の公開鍵で確かめる → 判断の記録が封の続きか → 前に配った世代の封
  （`--previous`。公開の場所から CI の手順が取ってくる）の続きか・世代が戻っていないか → 宣言が封のときのままか →
  封の中の判断だけ（と、封を待たない狭める物）で今の日時に計算し直す → 置いてある材料と、封の出力と比べる。
  緑なら、配る物（封と、配った時の判断の記録の頭）を `--publish` に書く。次の回は、判断の記録がその頭の続きでなければ
  赤（封をし直さずに止めるで外して配った回も、行を消して戻せない）。封の出し口と確かめる出し口も照らす。
- **手元でも封は確かめてから使う**（`use`・`export`・`accept`）: 署名が合わない・公開鍵が無い・別の出し口の封は、
  封なしとして扱う（広げない。狭める物は効く）。
- **封の中か外かは、判断の記録の中の位置で決める**（封が言うバイトまでの行が封の中。番号の集まりでは決めない）。
  判断の記録は、番号が中身から作った物と合わない行・中身の違う行と番号が重なる行・日時の読めない行を、読む入口で断る。
- 判断の新しさ: **封の外の判断は、`at` に何が書いてあっても封の中の判断より新しい**（次の受け入れまで。封の外の
  止める・だめが、日時の書き方で封の中の止めない・よいに負けない）。その中では日時を時刻として比べ（字で比べない）、
  同じ時刻なら判断の記録の中で後に足した物（番号の文字の順にはしない）。止めないにも期限を見る（切れたら止めるが効く）。

網に出ない。台帳に書くのは判断の記録（`judgments.jsonl`・足すだけ）と封（`.thth/seals/`）だけ。
"""
from __future__ import annotations

import datetime
import hashlib
import json
import os
import re
from dataclasses import dataclass

from thth import __version__
from . import judgment as J
from . import keep as K
from . import seal as S
from . import vessel as V
from .errors import RegisterRefused

OUTLETS_FILE = "outlets.json"
OUTLETS_SCHEMA = "thth.outlets/1"
SEALS_DIR = "seals"
SEAL_SCHEMA = "thth.seal/1"
TAKE = "take"
STOP = "stop"
COMMON = "common"
AS = ("fact", "reservation")
RANK = {"none": 0, "reservation": 1, "fact": 2}
MATERIALS_DIR = "materials"
JST = datetime.timezone(datetime.timedelta(hours=9))
QUESTION = re.compile(r"[a-z][a-z0-9_]{0,31}(@[a-z][a-z0-9_]{0,31})?\Z")
_O_KEYS = ("audience", "kinds", "level", "requires", "fact", "reservation", "common", "on_narrow", "note")


# ------------------------------------------------------------------ 出し口の宣言

@dataclass
class Outlet:
    name: str
    audience: str              # reader か internal
    kinds: list
    level: str                 # record か unit
    requires: list             # [(問い, 結論)]: 記録への「出してよいか」
    fact: list                 # [(問い, 結論)]: 事実として
    reservation: dict | None   # {"question", "conclusion", "reason_kinds"}
    common: str | None         # 共通の台帳の種類（採った版を書く）
    on_narrow: str             # stop か drop


def _conds(raw, word) -> list:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise V.DeclarationError(word)
    out = []
    for item in raw:
        if not isinstance(item, dict) or set(item) - {"question", "conclusion", "note"}:
            raise V.DeclarationError(word)
        q, c = item.get("question"), item.get("conclusion", "good")
        if not isinstance(q, str) or not QUESTION.fullmatch(q) or c not in V.CONCLUSIONS:
            raise V.DeclarationError(word)
        out.append((q, c))
    return out


def parse_outlets(data, kinds: dict) -> dict:
    if not isinstance(data, dict) or set(data) - {"schema", "outlets", "note"} or data.get("schema") != OUTLETS_SCHEMA:
        raise V.DeclarationError("outlets")
    raw = data.get("outlets")
    if not isinstance(raw, dict):
        raise V.DeclarationError("outlets")
    out = {}
    for name, o in raw.items():
        if not isinstance(name, str) or not V.NAME.fullmatch(name) or not isinstance(o, dict):
            raise V.DeclarationError("outlets")
        if set(o) - set(_O_KEYS):
            raise V.DeclarationError(f"outlets:{name}")
        audience = o.get("audience", "reader")
        level = o.get("level", "record")
        on_narrow = o.get("on_narrow", "stop")
        names = o.get("kinds", [])
        common = o.get("common")
        if audience not in ("reader", "internal") or level not in ("record", "unit") or on_narrow not in ("stop", "drop"):
            raise V.DeclarationError(f"outlets:{name}")
        if not isinstance(names, list) or any(not isinstance(k, str) or k not in kinds for k in names):
            raise V.DeclarationError(f"outlets:{name}")
        if common is not None and (not isinstance(common, str) or not V.NAME.fullmatch(common)):
            raise V.DeclarationError(f"outlets:{name}")
        if not names and common is None:
            raise V.DeclarationError(f"outlets:{name}")
        if level == "unit" and any(kinds[k].units is None for k in names):
            raise V.DeclarationError(f"outlets:{name}")
        res = o.get("reservation")
        if res is not None:
            if not isinstance(res, dict) or set(res) - {"question", "conclusion", "reason_kinds", "note"}:
                raise V.DeclarationError(f"outlets:{name}")
            (q, c), = _conds([{"question": res.get("question"), "conclusion": res.get("conclusion", "pending")}],
                             f"outlets:{name}")
            kinds_ok = res.get("reason_kinds")
            if not isinstance(kinds_ok, list) or any(not isinstance(k, str) or not V.FIELD.fullmatch(k) for k in kinds_ok):
                raise V.DeclarationError(f"outlets:{name}")
            res = {"question": q, "conclusion": c, "reason_kinds": list(kinds_ok)}
        out[name] = Outlet(name, audience, list(names), level, _conds(o.get("requires"), f"outlets:{name}"),
                           _conds(o.get("fact"), f"outlets:{name}"), res, common, on_narrow)
    return out


def _read_json(path: str, limit: int = V.DECL_MAX):
    if os.path.islink(path):
        raise V.DeclarationError("symlink")
    with open(path, "rb") as f:
        data = f.read(limit + 1)
    if len(data) > limit:
        raise V.DeclarationError("too_large")

    def pairs(items):
        seen = {}
        for k, v in items:
            if k in seen:
                raise V.DeclarationError("key_duplicate")
            seen[k] = v
        return seen
    try:
        value = json.loads(data.decode("utf-8"), object_pairs_hook=pairs)
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise V.DeclarationError("json") from None
    if V.value_problem(value):
        raise V.DeclarationError("json")
    return value


def load_outlets(root: str, vessel: V.Vessel) -> dict:
    path = os.path.join(root, V.DECL_DIR, OUTLETS_FILE)
    if not os.path.exists(path):
        return {}
    try:
        return parse_outlets(_read_json(path), vessel.kinds)
    except V.DeclarationError as exc:
        raise RegisterRefused("register_outlet_unknown", f"outlets: {exc.word}"[:80]) from None


def outlet_of(root: str, vessel: V.Vessel, name) -> Outlet:
    outlets = load_outlets(root, vessel)
    if name not in outlets:
        raise RegisterRefused("register_outlet_unknown", "outlet", outlets=sorted(outlets))
    return outlets[name]


def decl_fp(root: str) -> str:
    """宣言（種類・憲法・番号の表・出し口）の指紋。封のときと変わっていれば、決まりが変わった（確かめで赤）。"""
    base = os.path.join(root, V.DECL_DIR)
    parts = {}
    kinds_dir = os.path.join(base, V.KINDS_DIR)
    names = [f"{V.KINDS_DIR}/{n}" for n in sorted(os.listdir(kinds_dir))
             if n.endswith(".json") and not n.startswith(".")] if os.path.isdir(kinds_dir) else []
    names += [n for n in (V.CONSTITUTION_FILE, V.IDS_FILE, OUTLETS_FILE) if os.path.isfile(os.path.join(base, n))]
    for name in names:
        with open(os.path.join(base, *name.split("/")), "rb") as f:
            parts[name] = hashlib.sha256(f.read()).hexdigest()
    return V.fp(parts)


# ------------------------------------------------------------------ 日時

def parse_time(raw, *, word: str = "as_of") -> datetime.datetime:
    """判定の日時（ISO の日付か日時。時間帯が無ければ日本の時刻）。読めなければ断る。"""
    if isinstance(raw, datetime.datetime):
        return raw if raw.tzinfo else raw.replace(tzinfo=JST)
    if not isinstance(raw, str) or not raw.strip():
        raise RegisterRefused("register_args_invalid", word)
    value = raw.strip()
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            day = datetime.date.fromisoformat(value)
            return datetime.datetime(day.year, day.month, day.day, tzinfo=JST)
        dt = datetime.datetime.fromisoformat(value[:-1] + "+00:00" if value.endswith("Z") else value)
    except ValueError:
        raise RegisterRefused("register_args_invalid", word) from None
    return dt if dt.tzinfo else dt.replace(tzinfo=JST)


def until_end(raw):
    """判断の「いつまで」が効かなくなる時刻（その日・その月・その年の終わり）。読めなければ None（＝もう効かない）。"""
    if not isinstance(raw, str):
        return None
    value = raw.strip()
    try:
        if re.fullmatch(r"\d{4}", value):
            return datetime.datetime(int(value) + 1, 1, 1, tzinfo=JST)
        if re.fullmatch(r"\d{4}-\d{2}", value):
            y, m = int(value[:4]), int(value[5:])
            return datetime.datetime(y + (m == 12), 1 if m == 12 else m + 1, 1, tzinfo=JST)
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            return datetime.datetime.combine(datetime.date.fromisoformat(value) + datetime.timedelta(days=1),
                                             datetime.time(), tzinfo=JST)
        return parse_time(value, word="until")
    except (ValueError, RegisterRefused):
        return None


def check_until(raw) -> str | None:
    if raw is None:
        return None
    if until_end(raw) is None:
        raise RegisterRefused("register_args_invalid", "until")
    return raw.strip()


def _passed(j: dict, as_of: datetime.datetime) -> bool:
    raw = j.get("until")
    if raw in (None, ""):
        return False
    end = until_end(raw)
    return end is None or as_of >= end


# ------------------------------------------------------------------ 判断の記録（バイトで読む）

def judgments_raw(root: str) -> bytes:
    path = J.path_of(root)
    if not os.path.exists(path):
        return b""
    if os.path.islink(path):
        raise RegisterRefused("register_judgment_invalid", "symlink")
    with open(path, "rb") as f:
        data = f.read(J.MAX_BYTES + 1)
    if len(data) > J.MAX_BYTES:
        raise RegisterRefused("register_judgment_invalid", "too_large")
    return data


def parse_judgments(raw: bytes, root: str | None = None) -> list:
    """`judgment.read` と同じ点検（形は `J.shape_problem`）で、バイトから読む（封の中の頭だけを読むため）。
    段 3 は加えて、番号の重なり（`id_same_as_line N`）・中身から作り直した番号との食い違い（`id_not_from_content`）・
    読めない日時（`at`）の行を断る。封の中か外かは記録の中の位置で決めるが、番号で判断を名指しする所
    （結び直す口など）が別の行を指さないように、番号は中身から決まる物だけを受ける（同じ中身の行の重なりは受ける）。
    段 5: 消す求めで印の形にした行（`erased`）は、番号と中身を比べない。代わりに、台帳の根 `root` の
    `.thth/erase/<求め>/` の結果にその行の番号が載っていることを見る（載っていなければ断る・`root` が無ければ断る）。"""
    out = []
    seen = {}
    for number, line in enumerate(raw.decode("utf-8", errors="replace").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except (ValueError, RecursionError):
            raise RegisterRefused("register_judgment_invalid", f"line {number}") from None
        if not isinstance(item, dict) or item.get("schema") != J.SCHEMA or not isinstance(item.get("target"), dict):
            raise RegisterRefused("register_judgment_invalid", f"line {number}")
        if V.value_problem(item):
            raise RegisterRefused("register_judgment_invalid", f"line {number}")
        problem = J.shape_problem(item)
        if problem:
            raise RegisterRefused("register_judgment_invalid", f"line {number} {problem}")
        first = seen.get(item["id"])
        if first is not None and V.canonical(first[1]) != V.canonical(item):
            # 同じ中身の行が 2 つ（同じ秒に同じ命令を 2 回）は、番号も同じで害が無い。中身の違う行が番号を借りたら断る。
            raise RegisterRefused("register_judgment_invalid", f"line {number} id_same_as_line {first[0]}")
        if J.is_erased(item):
            if not J.erased_ok(root, item):
                raise RegisterRefused("register_judgment_invalid", f"line {number} erased_not_in_result")
        elif item["id"] != J.new_id(item):
            raise RegisterRefused("register_judgment_invalid", f"line {number} id_not_from_content")
        if item.get("at") not in (None, "") and _when_or_none(item["at"]) is None:
            raise RegisterRefused("register_judgment_invalid", f"line {number} at")
        seen.setdefault(item["id"], (number, item))
        out.append(item)
    return out


def _when_or_none(raw):
    try:
        return parse_time(raw, word="at")
    except RegisterRefused:
        return None


_EARLIEST = datetime.datetime.min.replace(tzinfo=datetime.timezone.utc)


def _when(j: dict) -> datetime.datetime:
    """判断の日時を時刻として（字の比べにしない。UTC の書き方と日本の時刻が混ざっても正しい）。無ければいちばん前。"""
    raw = j.get("at")
    if raw in (None, ""):
        return _EARLIEST
    return _when_or_none(raw) or _EARLIEST


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ------------------------------------------------------------------ 共通の台帳

@dataclass
class Common:
    root: str
    vessel: V.Vessel
    judgments: list


def open_common(path) -> Common | None:
    if path is None:
        return None
    if not isinstance(path, str) or not os.path.isdir(path) or os.path.islink(path):
        raise RegisterRefused("register_common_unusable", "path")
    root = os.path.abspath(path)
    return Common(root, V.open_vessel(root), parse_judgments(judgments_raw(root), root))


def common_bytes(common: Common, ref: str, version: str) -> bytes:
    """共通の記録の、その版の中身。今の版なら今のファイル、違えば共通の台帳の器の控えから。取り出せなければ断る。"""
    rec = common.vessel.unique(ref)
    if rec is not None and rec.version == version:
        with open(os.path.join(common.root, *rec.path.split("/")), "rb") as f:
            data = f.read()
        if sha(data) == version:
            return data
    return K.get_object(common.root, version)


def common_path(common: Common | None, ref: str, known: dict) -> str:
    rec = common.vessel.unique(ref) if common else None
    if rec is not None:
        return rec.path
    return known.get(ref) or ref.split(":", 1)[-1] + ".json"


# ------------------------------------------------------------------ 使えるか

@dataclass
class Ctx:
    root: str
    vessel: V.Vessel
    sealed: list               # 封の中の判断（広げる判断はここからだけ）
    every: list                # 全部の判断（狭める物はここから）
    as_of: datetime.datetime
    common: Common | None = None
    n_sealed: int = 0          # 封の中の判断の数（判断の記録の頭から何件めまでが封の中か）

    def __post_init__(self):
        self.n_sealed = len(self.sealed)


def _key(pair, n_sealed: int | None = None):
    """`(位置, 判断)` の新しさ。封の外（位置が封の件数より後ろ）の判断は、`at` に何が書いてあっても封の中の判断より
    新しい（次の受け入れまで。封の外の狭める判断が、日時の書き方で封の中の広げる判断に負けないため）。
    その中では日時（時刻として比べる）、同じ日時なら記録の中で後に足した物（番号の文字の順にはしない）。
    封の中の判断は判断の記録の頭なので、位置は封の中でも全部でも同じ。`n_sealed` が無ければ全部を封の中とみる。"""
    outside = n_sealed is not None and pair[0] >= n_sealed
    return (outside, _when(pair[1]), pair[0])


def _against_ok(vessel: V.Vessel, j: dict) -> bool:
    """照らした物（根拠など）の版が、今も同じか。違えば、その判断は効かない（根拠だけが変わったとき）。"""
    for item in j.get("against") or []:
        rec = vessel.unique(item.get("record")) if isinstance(item, dict) else None
        if rec is None or rec.version != item.get("version"):
            return False
    return True


def conclusion_of(vessel: V.Vessel, j: dict):
    """判断の結論と理由の種類（台帳の語は憲法で、問いごとに結論へ当てる。段 1 の当て方をそのまま使う）。"""
    return J.conclusion_of(vessel, j)


def answer(ctx: Ctx, js: list, match, question: str) -> dict:
    """1 つの問いへの答え（その判断の集まりの中で、効いている最後の判断）。"""
    mine = [(n, j) for n, j in enumerate(js) if j.get("question") == question and match(j.get("target") or {})]
    if not mine:
        return {"conclusion": None, "why": "no_judgment"}
    effective = [p for p in mine if J.evaluate(ctx.vessel, p[1]) == "effective"]
    if not effective:
        return {"conclusion": None, "why": "no_inspection_for_new_version"}
    # 今の版に効く最後の判断が答え。それが期限切れ・根拠の変わりで効かないとき、前の判断へ戻らない
    # （前の判断を蘇らせると、期限を付け直した判断が切れた途端に、期限の無い古い判断で使えてしまう）。
    last = max(effective, key=lambda p: _key(p, ctx.n_sealed))[1]
    if not _against_ok(ctx.vessel, last):
        return {"conclusion": None, "why": "against_changed", "judgment": last}
    if _passed(last, ctx.as_of):
        return {"conclusion": None, "why": "expired", "judgment": last}
    conclusion, reason_kind = conclusion_of(ctx.vessel, last)
    if conclusion is None:
        return {"conclusion": None, "why": reason_kind or "word_unmapped", "judgment": last}
    return {"conclusion": conclusion, "reason_kind": reason_kind, "judgment": last, "why": None}


def _record_match(ref):
    return lambda t: t.get("record") == ref and not t.get("unit") and not t.get("ledger")


def _unit_match(ref, pointer):
    # 消す求めで指し先を除いた印の行（`unit_dropped`）は、その記録の全部の単位に当たる（狭める向きにだけ効くので）。
    return lambda t: t.get("record") == ref and (t.get("unit") == pointer or bool(t.get("unit_dropped"))) \
        and not t.get("value") and not t.get("ledger")


def _level(ctx: Ctx, js: list, outlet: Outlet, ref: str, pointer) -> tuple:
    """`(段, 使えないわけ, 理由の種類)`。段は fact・reservation・none。"""
    for question, want in outlet.requires:
        got = answer(ctx, js, _record_match(ref), question)
        if got["conclusion"] != want:
            # 出してよいの判断が無い・だめ・まだは「出し口が無い」と同じ（MMR の public_song の欄が無い組も）。
            # 古い・期限切れ・根拠の変わりは、そのわけのまま返す（直し方が違うので）。
            why = got["why"] if got["why"] in ("no_inspection_for_new_version", "expired", "against_changed") else None
            return "none", why or "not_permitted", None
    match = _unit_match(ref, pointer) if pointer else _record_match(ref)
    why = None
    fact_ok = True
    for question, want in outlet.fact:
        got = answer(ctx, js, match, question)
        if got["conclusion"] != want:
            fact_ok = False
            why = why or got["why"] or f"not_{want}"
    if fact_ok:
        return "fact", None, None
    res = outlet.reservation
    if res is not None:
        got = answer(ctx, js, match, res["question"])
        if got["conclusion"] == res["conclusion"]:
            if got.get("reason_kind") in res["reason_kinds"]:
                return "reservation", None, got.get("reason_kind")
            return "none", "reason_kind_not_allowed", got.get("reason_kind")
        why = got["why"] or why
    return "none", why, None


def _stop_state(js_every: list, n_sealed: int, vessel: V.Vessel | None, ref: str, pointer, outlet: str,
                as_of) -> dict | None:
    """止めているなら、その判断。止めるは封を待たない。止めないは封の中（判断の記録の頭から `n_sealed` 件め
    まで。番号でなく位置で決める）だけで、期限が切れていない物。封の外の止めるは、`at` によらず封の中の止めないに勝つ。"""
    questions = {STOP, f"{STOP}@{outlet}"}
    mine = [(n, j) for n, j in enumerate(js_every) if j.get("question") in questions
            and (j.get("target") or {}).get("record") == ref
            and (not (j.get("target") or {}).get("unit") or j["target"]["unit"] == pointer)]
    stops, lifts = [], []
    for n, j in mine:
        word = ((j.get("answer") or {}).get("conclusion"))
        if word == "bad" and not _passed(j, as_of):
            stops.append((n, j))
        elif word == "good" and n < n_sealed and not _passed(j, as_of) and not J.is_erased(j):
            # 印の形にした「止めない」は、対象の版を確かめられないので効かない（段 5 §3.4）
            lifts.append((n, j))
    if not stops:
        return None
    last = max(stops, key=lambda p: _key(p, n_sealed))
    if any(_key(l, n_sealed) > _key(last, n_sealed) for l in lifts):
        return None
    return last[1]


def decide(ctx: Ctx, outlet: Outlet, ref: str, pointer=None) -> dict:
    """1 つの記録（か内側の単位）を、この出し口に、何として使えるか。"""
    stop = _stop_state(ctx.every, ctx.n_sealed, ctx.vessel, ref, pointer, outlet.name, ctx.as_of)
    if stop is not None:
        return {"as": None, "why": "stopped"}
    if outlet.audience == "internal":
        return {"as": "internal", "why": None}
    sealed_level, sealed_why, sealed_kind = _level(ctx, ctx.sealed, outlet, ref, pointer)
    every_level, every_why, _ = _level(ctx, ctx.every, outlet, ref, pointer)
    final = sealed_level if RANK[sealed_level] <= RANK[every_level] else every_level
    if final == "none":
        if sealed_level == "none" and every_level != "none":
            why = "not_accepted"   # 受け入れていない判断なら使えるが、封の外なので広げない
        else:
            why = sealed_why if sealed_level == "none" else (every_why or "narrowed")
        return {"as": None, "why": why}
    out = {"as": final, "why": None}
    if final == "reservation":
        out["reason_kind"] = sealed_kind
    if RANK[final] < RANK[sealed_level]:
        out["narrowed"] = every_why or "narrowed"
    return out


# ------------------------------------------------------------------ 出し口の中身を計算する

def _unit_note(kind: V.Kind, unit: V.Unit, question: str):
    q = V.question_of(kind, question)
    if q is None or not q.reason_text:
        return None
    return unit.item.get(q.reason_text)


def _takes(js: list) -> dict:
    """共通の記録ごとに、最後に採った版（`{ref: 判断}`）。"""
    out = {}
    for n, j in enumerate(js):
        t = j.get("target") or {}
        if j.get("question") == TAKE and t.get("ledger") == COMMON and isinstance(t.get("record"), str):
            if t["record"] not in out or _key((n, j)) > _key(out[t["record"]]):
                out[t["record"]] = (n, j)
    return {ref: pair[1] for ref, pair in out.items()}


def _common_stopped(ctx: Ctx, ref: str, outlet: str):
    """共通の台帳で止めているなら、その判断。メディアが後から採り直しても外れない（止めるのは共通の台帳だけが外せる）。
    共通で「止めない」と言い直したとき、メディアの材料に戻るのは、メディアが受け入れて封をし直してから
    （止めたあとの封には載っていないので、受け入れまでは「封の外で広がった」として赤）。"""
    if ctx.common is None:
        return None
    return _stop_state(ctx.common.judgments, len(ctx.common.judgments), None, ref, None, outlet,
                       ctx.as_of)


def compute(ctx: Ctx, outlet: Outlet, *, common_known: dict | None = None) -> dict:
    """`{"items": [...], "files": {道: bytes}, "inputs": [...], "excluded": [...], "pending_takes": [...]}`。"""
    items, excluded, inputs = [], [], []
    files, tables = {}, {}
    vessel = ctx.vessel
    by_path = {}
    for rec in vessel.all_records:
        if rec.kind not in outlet.kinds:
            continue
        if rec.id is None or rec.problems or vessel.is_duplicated(rec.ref):
            excluded.append({"record": rec.ref if rec.id else rec.path, "why": "record_problem"})
            continue
        inputs.append({"record": rec.ref, "version": rec.version})
        kind = vessel.kinds[rec.kind]
        if outlet.level == "unit":
            pointers = [u.pointer for u in rec.units]
            if len(set(pointers)) != len(pointers):
                excluded.append({"record": rec.ref, "why": "unit_ambiguous"})
                continue
            for unit in rec.units:
                got = decide(ctx, outlet, rec.ref, unit.pointer)
                if got["as"] is None:
                    excluded.append({"record": rec.ref, "unit": unit.pointer, "why": got["why"]})
                    continue
                item = {"record": rec.ref, "unit": unit.pointer, "as": got["as"], "key": unit.key,
                        "values": unit.item}
                if got["as"] == "reservation":
                    item["reason_kind"] = got["reason_kind"]
                    item["note"] = _unit_note(kind, unit, outlet.reservation["question"])
                items.append(item)
            continue
        got = decide(ctx, outlet, rec.ref)
        if got["as"] is None:
            excluded.append({"record": rec.ref, "why": got["why"]})
            continue
        item = {"record": rec.ref, "as": got["as"], "version": rec.version, "path": rec.path}
        if got["as"] == "reservation":
            item["reason_kind"] = got["reason_kind"]
        items.append(item)
        by_path.setdefault(rec.path, set()).add(rec.version)
    # 記録ごとの表とファイル
    if outlet.level == "record":
        for path in sorted({rec.path for rec in vessel.all_records if rec.kind in outlet.kinds}):
            kind = next(vessel.kinds[r.kind] for r in vessel.all_records if r.path == path and r.kind in outlet.kinds)
            keep = by_path.get(path, set())
            with open(os.path.join(ctx.root, *path.split("/")), "rb") as f:
                data = f.read()
            if kind.records == "rows" and kind.format == "csv":
                files[path], tables[path] = _table_bytes(data, keep)
            elif keep:
                files[path] = data
        facts = [i for i in items if i["as"] == "reservation"]
        if outlet.reservation is not None:
            files[f"{MATERIALS_DIR}/{outlet.name}.reservations.json"] = _json_bytes(
                [{k: i.get(k) for k in ("record", "reason_kind")} for i in facts])
    elif outlet.kinds:
        files[f"{MATERIALS_DIR}/{outlet.name}.json"] = _json_bytes(
            sorted(items, key=lambda i: (i["record"], i["unit"])))
    # 共通の台帳から採った物
    pending = []
    if outlet.common is not None:
        sealed_takes = _takes(ctx.sealed)
        all_takes = _takes(ctx.every)
        for ref in sorted(set(sealed_takes) | set(all_takes)):
            if not ref.startswith(outlet.common + ":"):
                continue
            take_j = sealed_takes.get(ref)
            if take_j is None:
                excluded.append({"record": f"{COMMON}:{ref}", "why": "not_accepted"})
                continue
            version = (take_j.get("seen") or {}).get("version")
            inputs.append({"record": f"{COMMON}:{ref}", "version": version})
            if ctx.common is not None:
                now = ctx.common.vessel.unique(ref)
                if now is not None and now.version != version:
                    pending.append({"record": f"{COMMON}:{ref}", "taken": version, "now": now.version})
            if _common_stopped(ctx, ref, outlet.name) is not None or _stop_state(
                    ctx.every, ctx.n_sealed, None, f"{COMMON}:{ref}", None, outlet.name, ctx.as_of) is not None:
                excluded.append({"record": f"{COMMON}:{ref}", "why": "stopped"})
                continue
            path = f"{COMMON}/{common_path(ctx.common, ref, common_known or {})}"
            if ctx.common is None:
                items.append({"record": f"{COMMON}:{ref}", "as": "fact", "version": version, "path": path,
                              "unchecked": True})
                continue
            files[path] = common_bytes(ctx.common, ref, version)
            items.append({"record": f"{COMMON}:{ref}", "as": "fact", "version": version, "path": path})
    inputs.sort(key=lambda i: i["record"])
    return {"items": items, "files": files, "inputs": inputs, "excluded": excluded, "pending_takes": pending,
            "tables": tables}


def _json_bytes(value) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=1) + "\n").encode("utf-8")


def _table_bytes(data: bytes, keep: set) -> tuple:
    """表から、使える行だけを元のバイトのまま残す（見出しと空の行はそのまま）。全部使えるなら元と同じバイト。
    `(バイト, 書いた行ごとの sha256 の並び)`。並びの頭は見出し（BOM を含む）。封の指紋（`fingerprints.tables`）に使う。"""
    bom = b"\xef\xbb\xbf" if data.startswith(b"\xef\xbb\xbf") else b""
    text = data[len(bom):].decode("utf-8")
    rows = V.split_csv_rows(text)
    if not rows:
        return data, [sha(data)]
    out = [rows[0]]
    for raw in rows[1:]:
        if not raw.strip("\r\n") or V.bytes_version(raw.encode("utf-8")) in keep:
            out.append(raw)
    shas = [sha(bom + out[0].encode("utf-8"))] + [sha(r.encode("utf-8")) for r in out[1:]]
    return bom + "".join(out).encode("utf-8"), shas


def item_fp(item: dict) -> str:
    """載せた項目の中身の指紋（版・値・理由の種類・注）。置き場の道（ファイルの道は出力の指紋で見る。共通の台帳を
    読めない CI では道を当てで作る）と、共通の台帳を読めなかった印は入れない。"""
    return sha(V.canonical({k: v for k, v in item.items() if k not in ("unchecked", "path")}).encode("utf-8"))


def _only_dropped(body: dict, result: dict, expected: dict) -> bool:
    """`on_narrow: drop` で外して配るとき、今の材料が「封の出力から、外した項目を抜いた物」か。
    残った項目の中身が封の指紋と同じ・表は見出しと残った行が封のときのバイトと並び・ほかのファイルは封の出力と同じ。
    指紋の無い封（この直しの前の封）は確かめられないので、外して配らない（受け入れ直す）。"""
    fp = body.get("fingerprints")
    if not isinstance(fp, dict) or not isinstance(fp.get("items"), dict) or not isinstance(fp.get("tables"), dict):
        return False
    for item in result["items"]:
        if fp["items"].get(item_key(item)) != item_fp(item):
            return False
    for path, digest_ in expected.items():
        if path in result["tables"]:
            now, then = result["tables"][path], fp["tables"].get(path)
            if not isinstance(then, list) or not now or not then or now[0] != then[0]:
                return False
            kept = set(now)
            if not kept <= set(then) or now != [r for r in then if r in kept]:
                return False
        elif path.startswith(MATERIALS_DIR + "/"):
            continue            # 項目から作るファイル（中身は項目の指紋で見た）
        elif body["outputs"].get(path) != digest_:
            return False
    return True


def item_key(item: dict) -> str:
    return item["record"] + (f"#{item['unit']}" if item.get("unit") else "") + f"={item['as']}"


# ------------------------------------------------------------------ 封

def seal_path(root: str, outlet: str) -> str:
    return os.path.join(root, V.DECL_DIR, SEALS_DIR, f"{outlet}.json")


def read_seal(root: str, outlet: str) -> dict | None:
    path = seal_path(root, outlet)
    if not os.path.exists(path):
        return None
    try:
        value = _read_json(path, J.MAX_BYTES)
    except (V.DeclarationError, OSError):
        raise RegisterRefused("register_seal_unverified", "seal_shape") from None
    return check_seal_shape(value)


def check_seal_shape(value) -> dict:
    if (not isinstance(value, dict) or not isinstance(value.get("body"), dict) or not isinstance(value.get("signature"), str)
            or value["body"].get("schema") != SEAL_SCHEMA):
        raise RegisterRefused("register_seal_unverified", "seal_shape")
    body = value["body"]
    jm = body.get("judgments")
    if (not isinstance(jm, dict) or not isinstance(jm.get("bytes"), int) or not isinstance(jm.get("sha256"), str)
            or not isinstance(body.get("generation"), int) or not isinstance(body.get("outputs"), dict)
            or not isinstance(body.get("items"), list)):
        raise RegisterRefused("register_seal_unverified", "seal_shape")
    return value


SEAL_STATES = ("none", "verified", "shape", "other_outlet", "public_key", "signature")


def trusted_seal(root: str, outlet: str) -> tuple:
    """手元で封を使う前の確かめ（`use`・`export`・`accept`）。`(封 | None, 状態)`。
    状態が `verified` のときだけ封を返す。形が崩れた・別の出し口の物・公開鍵が無い・署名が合わない封は、
    封なしとして扱う（広げる判断は効かない。狭める物は封を待たないので、そのまま効く）。"""
    try:
        seal = read_seal(root, outlet)
    except RegisterRefused:
        return None, "shape"
    if seal is None:
        return None, "none"
    if seal["body"].get("outlet") != outlet:
        return None, "other_outlet"
    try:
        pem, _origin = S.trusted_public_pem()
    except RegisterRefused:
        return None, "public_key"
    if not S.verify(V.canonical(seal["body"]).encode("utf-8"), seal["signature"], pem):
        return None, "signature"
    return seal, "verified"


def _refuse_unverified(outlet: str, state: str):
    if state == "none":
        raise RegisterRefused("register_seal_missing", outlet)
    raise RegisterRefused("register_seal_unverified", state, seal=state)


def sealed_judgments(raw: bytes, seal: dict | None, root: str | None = None) -> list:
    if seal is None:
        return []
    jm = seal["body"]["judgments"]
    if len(raw) < jm["bytes"] or sha(raw[:jm["bytes"]]) != jm["sha256"]:
        raise RegisterRefused("register_seal_unverified", "judgments_rewritten")
    return parse_judgments(raw[:jm["bytes"]], root)


def context(root: str, as_of, *, common_root=None, raw: bytes | None = None, seal: dict | None = None,
            sealed_all: bool = False) -> Ctx:
    vessel = V.open_vessel(root)
    raw = judgments_raw(root) if raw is None else raw
    every = parse_judgments(raw, root)
    sealed = every if sealed_all else sealed_judgments(raw, seal, root)
    return Ctx(root, vessel, sealed, every, parse_time(as_of), open_common(common_root))


def _outputs(files: dict) -> dict:
    return {path: sha(data) for path, data in sorted(files.items())}


def seal_body(root: str, outlet: Outlet, ctx: Ctx, result: dict, raw: bytes, previous: dict | None) -> dict:
    return {"schema": SEAL_SCHEMA, "outlet": outlet.name, "audience": outlet.audience,
            "generation": (previous["body"]["generation"] + 1) if previous else 1,
            "as_of": ctx.as_of.isoformat(timespec="seconds"),
            "judgments": {"bytes": len(raw), "count": len(ctx.every), "sha256": sha(raw)},
            "decl": decl_fp(root), "thth": __version__, "inputs": result["inputs"],
            "outputs": _outputs(result["files"]),
            "items": sorted(item_key(i) for i in result["items"]),
            "fingerprints": {"items": {item_key(i): item_fp(i) for i in result["items"]},
                             "tables": result["tables"]},
            "common_unchecked": any(i.get("unchecked") for i in result["items"]),
            "previous": ({"generation": previous["body"]["generation"],
                          "judgments": previous["body"]["judgments"]} if previous else None)}


def digest(body: dict) -> str:
    return hashlib.sha256(V.canonical(body).encode("utf-8")).hexdigest()[:16]


# ------------------------------------------------------------------ 材料を置く

def _inside(path: str, directory: str) -> bool:
    path, directory = os.path.realpath(path), os.path.realpath(directory)
    try:
        return os.path.commonpath([path, directory]) == directory
    except ValueError:
        return False


def check_to(root: str, to) -> str:
    if not isinstance(to, str) or not to.strip():
        raise RegisterRefused("register_args_invalid", "to")
    to = os.path.abspath(os.path.expanduser(to))
    if _inside(to, root):
        raise RegisterRefused("register_adopt_unusable", "inside")
    return to


def _outlet_patterns(vessel: V.Vessel, outlet: Outlet) -> list:
    pats = [p for k in outlet.kinds for p in vessel.kinds[k].places]
    pats.append(f"{MATERIALS_DIR}/{outlet.name}.json")
    pats.append(f"{MATERIALS_DIR}/{outlet.name}.reservations.json")
    if outlet.common:
        pats.append(f"{COMMON}/*")
    return pats


def placed(root: str, vessel: V.Vessel, outlet: Outlet, to: str, names) -> dict:
    """書き出し先にある、この出し口の物（一覧の名前と、出し口の置き場の型に当たる物）の sha256。台帳の中は見ない。"""
    import fnmatch
    out = {}
    pats = _outlet_patterns(vessel, outlet)
    if os.path.isdir(to):
        for directory, dirs, files in os.walk(to, followlinks=False):
            dirs[:] = sorted(d for d in dirs if not d.startswith(".") and d != "node_modules"
                             and not _inside(os.path.join(directory, d), root))
            for name in sorted(files):
                full = os.path.join(directory, name)
                rel = os.path.relpath(full, to).replace(os.sep, "/")
                if rel in names or any(fnmatch.fnmatchcase(rel, p) for p in pats) or (
                        outlet.common and rel.startswith(COMMON + "/")):
                    out[rel] = _file_sha(full)
    for rel in names:
        if rel not in out:
            full = os.path.join(to, *rel.split("/"))
            if os.path.isfile(full):
                out[rel] = _file_sha(full)
    return out


def _file_sha(path: str) -> str:
    if os.path.islink(path):
        return "symlink"
    with open(path, "rb") as f:
        return sha(f.read())


def write_files(to: str, files: dict) -> None:
    for rel, data in files.items():
        if rel.startswith("/") or ".." in rel.split("/"):
            raise RegisterRefused("register_write_unsafe", "path")
        path = os.path.join(to, *rel.split("/"))
        if os.path.islink(path):
            raise RegisterRefused("register_write_unsafe", "symlink")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = f"{path}.{os.getpid()}.tmp"
        with open(tmp, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)


def _changes(before_items, after_items) -> dict:
    before, after = set(before_items or []), set(after_items or [])
    return {"leaving": sorted(before - after), "entering": sorted(after - before)}


# ------------------------------------------------------------------ 命令の中身

def export(root: str, outlet_name: str, to, as_of, *, common_root=None, check: bool = False) -> dict:
    """封の中の判断（と、封を待たない狭める物）で材料を作る。鍵は使わない（狭めるだけなので）。"""
    vessel = V.open_vessel(root)
    outlet = outlet_of(root, vessel, outlet_name)
    to = check_to(root, to)
    seal, state = trusted_seal(root, outlet.name)
    if seal is None:
        _refuse_unverified(outlet.name, state)
    raw = judgments_raw(root)
    ctx = context(root, as_of, common_root=common_root, raw=raw, seal=seal)
    result = compute(ctx, outlet, common_known=_known_paths(seal))
    outputs = _outputs(result["files"])
    now = placed(root, ctx.vessel, outlet, to, set(outputs))
    stale = sorted(p for p in now if p not in outputs)
    value = {"outlet": outlet.name, "as_of": ctx.as_of.isoformat(timespec="seconds"), "to": to,
             "generation": seal["body"]["generation"], "files": len(outputs), "items": len(result["items"]),
             "changes": _changes(seal["body"]["items"], [item_key(i) for i in result["items"]]),
             "excluded": result["excluded"], "pending_takes": result["pending_takes"], "stale": stale,
             "same_as_seal": outputs == seal["body"]["outputs"],
             "differs": sorted(p for p in set(outputs) | set(now) if outputs.get(p) != now.get(p)),
             "check": check}
    if check:
        return value
    if stale:
        raise RegisterRefused("register_material_stale", f"{len(stale)} files", stale=stale[:50])
    write_files(to, result["files"])
    return value


def _known_paths(seal: dict | None) -> dict:
    out = {}
    for path in (seal or {}).get("body", {}).get("outputs", {}):
        if path.startswith(COMMON + "/"):
            out[path] = path
    return out


def accept_plan(root: str, outlet_name: str, to, as_of, *, common_root=None) -> dict:
    """受け入れの 1 段め: 今の判断の全部を受け入れたら、何が変わるか（書かない）と、確かめの印。"""
    vessel = V.open_vessel(root)
    outlet = outlet_of(root, vessel, outlet_name)
    to = check_to(root, to)
    raw = judgments_raw(root)
    if raw and not raw.endswith(b"\n"):
        raise RegisterRefused("register_judgment_invalid", "no_final_newline")
    previous, state = trusted_seal(root, outlet.name)
    erased_prev = None
    if state == "signature":
        # 消す求めで項目の指紋を外した封（署名は合わない）だけは、世代の番号を継いで受け入れ直せる（段 5 §3.7）。
        # 広げる向きには何も継がない（封の中の判断は今の全部を受け入れ直す）。ほかの署名の合わない封は今までどおり断る。
        erased_prev = erased_seal(root, outlet.name)
        if erased_prev is None:
            _refuse_unverified(outlet.name, state)
    elif state != "none" and previous is None:
        _refuse_unverified(outlet.name, state)   # 署名の合わない封から、世代と判断の頭を継がない
    if previous is not None:
        sealed_judgments(raw, previous, root)   # 封のあとで判断の記録の頭が書き換わっていれば、受け入れずに断る
    ctx = context(root, as_of, common_root=common_root, raw=raw, sealed_all=True)
    base = previous or erased_prev
    result = compute(ctx, outlet, common_known=_known_paths(base))
    body = seal_body(root, outlet, ctx, result, raw, base)
    block = erase_block(root, erased_prev, previous)
    if block is not None:
        body["erase"] = block
    if previous is not None:
        new_judgments = parse_judgments(raw[previous["body"]["judgments"]["bytes"]:], root)
    elif erased_prev is not None:
        new_judgments = ctx.every[int(erased_prev["body"]["judgments"].get("count") or 0):]
    else:
        new_judgments = ctx.every
    now = placed(root, ctx.vessel, outlet, to, set(body["outputs"]))
    return {"outlet": outlet.name, "to": to, "body": body, "result": result, "digest": digest(body),
            "new_judgments": [_judgment_line(j) for j in new_judgments],
            "changes": _changes(base["body"]["items"] if base else [], body["items"]),
            "stale": sorted(p for p in now if p not in body["outputs"])}


def erased_seal(root: str, outlet: str) -> dict | None:
    """消す求めで項目の指紋を外した封（`body.erased` に求めの番号）。どの求めも、結果の記録で、この出し口の封を
    書き換えたと言っているときだけ返す（言っていなければ None。ただの署名の合わない封として断る）。"""
    try:
        seal = read_seal(root, outlet)
    except RegisterRefused:
        return None
    if seal is None or seal["body"].get("outlet") != outlet:
        return None
    requests = seal["body"].get("erased")
    if not isinstance(requests, list) or not requests:
        return None
    for req in requests:
        result = _erase_result(root, req)
        if result is None or outlet not in (result.get("seals") or []):
            return None
    return seal


def _erase_result(root: str, req) -> dict | None:
    if not isinstance(req, str) or not J.ERASE_ID.fullmatch(req):
        return None
    base = os.path.join(root, V.DECL_DIR, J.ERASE_DIR, req)
    if os.path.islink(base) or not os.path.isfile(os.path.join(base, J.ERASE_SCOPE)):
        return None
    try:
        with open(os.path.join(base, J.ERASE_RESULT), "rb") as f:
            value = json.loads(f.read().decode("utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return None
    return value if isinstance(value, dict) and value.get("request") == req else None


def erase_block(root: str, erased_prev: dict | None, previous: dict | None) -> dict | None:
    """受け入れ直した封が持つ「消す求めの結果」（段 5 §3.7）: 求めの番号・書き換えた判断の行の番号・前の封の判断の
    記録の頭（バイトの数と指紋）。前の封が持っていた物は継ぐ（配る前に受け入れを重ねても、CI が前に配った世代と
    比べられるように）。消した中身もその指紋も持たない。"""
    prior = (previous or {}).get("body", {}).get("erase") if previous else None
    if erased_prev is None:
        return prior if isinstance(prior, dict) else None
    requests = set((prior or {}).get("requests") or [])
    lines = set((prior or {}).get("lines") or [])
    for req in erased_prev["body"]["erased"]:
        result = _erase_result(root, req) or {}
        requests.add(req)
        lines.update(x for x in result.get("rewritten_lines") or [] if isinstance(x, str))
    pj = erased_prev["body"]["judgments"]
    return {"requests": sorted(requests), "lines": sorted(lines),
            "previous": {"generation": erased_prev["body"]["generation"],
                         "judgments": {"bytes": pj["bytes"], "sha256": pj["sha256"]}}}


def line_skeleton(j: dict, with_unit: bool = True) -> str:
    """判断の行の骨（番号・問い・対象の番号・結論・日時・誰が・いつまで）の指紋。消す求めで印の形にしても変わらない
    欄だけから作る。CI は、前に配った頭の行ごとの骨（`deployed.lines`）と今の行を比べ、封の言う行だけを印の形の
    書き換えとして通す（段 5 §3.7）。`with_unit=False` は指し先を除いた骨（消す求めで指し先を除いた行と比べる）。"""
    t = j.get("target") if isinstance(j.get("target"), dict) else {}
    a = j.get("answer") if isinstance(j.get("answer"), dict) else {}
    body = {"id": j.get("id"), "question": j.get("question"), "record": t.get("record"), "ledger": t.get("ledger"),
            "word": a.get("word"), "conclusion": a.get("conclusion"), "at": j.get("at"), "by": j.get("by"),
            "until": j.get("until")}
    if with_unit:
        body["unit"] = t.get("unit")
        body["value"] = t.get("value")
    return sha(V.canonical(body).encode("utf-8"))[:32]


def line_skeletons(raw: bytes) -> list:
    out = []
    for line in raw.decode("utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except (ValueError, RecursionError):
            item = {}
        item = item if isinstance(item, dict) else {}
        out.append([line_skeleton(item, True), line_skeleton(item, False)])
    return out


def erase_continued(root: str, raw: bytes, dep: dict, erase) -> str | None:
    """前に配った頭（`dep`）の続きでないとき、消す求めの書き換えとして通せるか。通せれば None、通せなければ語。
    行が減っていない・前に配った行ごとの骨が今と同じ・印の形の行は署名の合った封が言う行（と求め）だけ。"""
    if not isinstance(erase, dict):
        return "no_erase"
    lines = dep.get("lines")
    if not isinstance(lines, list) or any(not (isinstance(p, list) and len(p) == 2) for p in lines):
        return "previous_lines_missing"
    try:
        now = parse_judgments(raw, root)
    except RegisterRefused:
        return "lines_unreadable"
    if len(now) < len(lines):
        return "lines_reduced"
    allowed = set(erase.get("lines") or [])
    requests = set(erase.get("requests") or [])
    for i, (with_unit, without_unit) in enumerate(lines):
        j = now[i]
        if J.is_erased(j):
            if j.get("id") not in allowed or j["erased"].get("request") not in requests:
                return "line_not_in_erase"
            has_unit = bool((j.get("target") or {}).get("unit"))
            if line_skeleton(j, has_unit) != (with_unit if has_unit else without_unit):
                return "line_changed"
        elif line_skeleton(j, True) != with_unit:
            return "line_changed"
    return None


def _judgment_line(j: dict) -> dict:
    a = j.get("answer") or {}
    return {"id": j.get("id"), "target": j.get("target"), "question": j.get("question"), "word": a.get("word"),
            "conclusion": a.get("conclusion"), "by": j.get("by"), "until": j.get("until"), "path": j.get("path")}


def accept(root: str, outlet_name: str, to, as_of, *, confirm: str, at: str, common_root=None) -> dict:
    """受け入れの 2 段め: 1 段めの印が合えば、材料を書き、封をする（材料が先・封があと）。"""
    plan = accept_plan(root, outlet_name, to, as_of, common_root=common_root)
    if confirm != plan["digest"]:
        raise RegisterRefused("register_confirm_mismatch", "confirm", digest=plan["digest"])
    if plan["stale"]:
        raise RegisterRefused("register_material_stale", f"{len(plan['stale'])} files", stale=plan["stale"][:50])
    body = dict(plan["body"], at=at, public_key=S.fingerprint(S.local_public_pem()))
    signature = S.sign(V.canonical(body).encode("utf-8"), register_root=root)
    write_files(plan["to"], plan["result"]["files"])
    path = seal_path(root, plan["outlet"])
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(json.dumps({"body": body, "signature": signature}, ensure_ascii=False, sort_keys=True, indent=1) + "\n")
    os.replace(tmp, path)
    return {"outlet": plan["outlet"], "generation": body["generation"], "files": len(body["outputs"]),
            "items": len(body["items"]), "judgments": body["judgments"]["count"], "changes": plan["changes"],
            "pending_takes": plan["result"]["pending_takes"], "seal": os.path.relpath(path, root)}


# ------------------------------------------------------------------ 確かめ（公開の直前・CI）

PROBLEMS = ("no_seal", "seal_shape", "public_key", "signature", "judgments_rewritten", "previous_unknown",
            "previous_signature", "previous_other_outlet", "previous_not_deployed", "seal_other_outlet",
            "generation_back", "generation_reused",
            "judgments_not_continued", "decl_changed", "material_mismatch", "narrowed_after_seal",
            "widened_after_seal", "stale_material")


def deployed_of(prev: dict) -> dict | None:
    """前に配った物の控え（`deployed`）。形が合わなければ None。封の頭より短い頭は受けない。"""
    pb, d = prev["body"], prev.get("deployed")
    if not isinstance(d, dict) or d.get("outlet") != pb.get("outlet") or d.get("generation") != pb.get("generation"):
        return None
    dj = d.get("judgments")
    if (not isinstance(dj, dict) or not isinstance(dj.get("bytes"), int) or not isinstance(dj.get("sha256"), str)
            or dj["bytes"] < pb["judgments"]["bytes"]):
        return None
    return d


def write_published(path: str, seal: dict, raw: bytes, now: datetime.datetime, dropped: list) -> None:
    """配る物: 封（署名つき）と、配った時の判断の記録の頭（`deployed`）。次の回の CI が「前に配った物」として読む。
    `deployed` は署名の外（CI は鍵を持たない）。守るのは、公開の場所へ置けるのがこの確かめを通った配りだけ、という事。"""
    body = seal["body"]
    value = {"body": body, "signature": seal["signature"],
             "deployed": {"outlet": body["outlet"], "generation": body["generation"],
                          "judgments": {"bytes": len(raw), "sha256": sha(raw)},
                          # 行ごとの骨（段 5 §3.7。消す求めの書き換えを、次の回の CI が行ごとに比べる）
                          "lines": line_skeletons(raw[:len(raw)]),
                          "at": now.isoformat(timespec="seconds"), "dropped": dropped}}
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=1) + "\n")
    os.replace(tmp, path)


def verify(root: str, outlet_name: str, material, now, *, previous_path=None, no_previous: bool = False,
           common_root=None, publish=None) -> dict:
    """公開の直前の確かめ。`{"ok", "problems": [{"word", ...}], "warnings": [...]}`。公開鍵は repo の外からだけ。
    `publish` を渡すと、緑のときだけ、配る物（封と配った時の判断の記録の頭）をそこに書く。"""
    problems, warnings = [], []

    def red(word, **extra):
        problems.append({"word": word, **extra})
    vessel = V.open_vessel(root)
    outlet = outlet_of(root, vessel, outlet_name)
    material = check_to(root, material)
    as_of = parse_time(now, word="now")
    try:
        seal = read_seal(root, outlet.name)
    except RegisterRefused:
        red("seal_shape")
        return _verdict(problems, warnings)
    if seal is None:
        red("no_seal")
        return _verdict(problems, warnings)
    try:
        pem, origin = S.trusted_public_pem()
    except RegisterRefused:
        red("public_key")
        return _verdict(problems, warnings)
    body = seal["body"]
    if not S.verify(V.canonical(body).encode("utf-8"), seal["signature"], pem):
        red("signature")
        return _verdict(problems, warnings, key=origin)
    if body.get("outlet") != outlet.name:
        red("seal_other_outlet", sealed_for=body.get("outlet"))
        return _verdict(problems, warnings, key=origin)
    if publish is not None and _inside(os.path.abspath(publish), root):
        raise RegisterRefused("register_adopt_unusable", "inside")
    raw = judgments_raw(root)
    jm = body["judgments"]
    if len(raw) < jm["bytes"] or sha(raw[:jm["bytes"]]) != jm["sha256"]:
        red("judgments_rewritten")
        return _verdict(problems, warnings, key=origin)
    # 前に配った世代（公開の場所から CI の手順が取ってくる封）
    if previous_path is not None:
        try:
            prev = check_seal_shape(_read_json(previous_path, J.MAX_BYTES))
        except (RegisterRefused, V.DeclarationError, OSError):
            prev = None
            red("previous_signature")
        if prev is not None:
            pb = prev["body"]
            if not S.verify(V.canonical(pb).encode("utf-8"), prev["signature"], pem):
                red("previous_signature")
            elif pb.get("outlet") != outlet.name:
                red("previous_other_outlet")
            else:
                if body["generation"] < pb["generation"]:
                    red("generation_back", previous=pb["generation"], now=body["generation"])
                elif body["generation"] == pb["generation"] and V.canonical(body) != V.canonical(pb):
                    red("generation_reused", generation=body["generation"])
                pj = pb["judgments"]
                dep = deployed_of(prev)
                broken = []
                if len(raw) < pj["bytes"] or sha(raw[:pj["bytes"]]) != pj["sha256"]:
                    broken.append({"previous_bytes": pj["bytes"], "now_bytes": len(raw)})
                # 封をし直さずに配った回（止めるで外した・期限で外した）の判断も、前に配った物の続きでなければ赤。
                # 止めるの行を消して古い材料に戻すと、封の頭は続いていても、配った時の頭が続かない。
                if dep is None:
                    red("previous_not_deployed")
                else:
                    dj = dep["judgments"]
                    if len(raw) < dj["bytes"] or sha(raw[:dj["bytes"]]) != dj["sha256"]:
                        broken.append({"deployed_bytes": dj["bytes"], "now_bytes": len(raw)})
                if broken:
                    # 消す求めで印の形にした行だけが違うなら、署名の合った封が言う行に限って通す（段 5 §3.7）。
                    why = erase_continued(root, raw, dep, body.get("erase")) if dep is not None else "no_erase"
                    if why is None:
                        warnings.append({"word": "erase_rewrite", "requests": body["erase"]["requests"],
                                         "lines": len(body["erase"]["lines"])})
                    else:
                        for one in broken:
                            red("judgments_not_continued", erase=why, **one)
    elif not no_previous:
        red("previous_unknown")
    else:
        warnings.append({"word": "previous_skipped"})
    if decl_fp(root) != body.get("decl"):
        red("decl_changed")
    # 封の中の判断だけ（と、封を待たない狭める物）で、今の日時で計算し直す
    ctx = Ctx(root, vessel, parse_judgments(raw[:jm["bytes"]], root), parse_judgments(raw, root), as_of,
              open_common(common_root))
    result = compute(ctx, outlet, common_known=_known_paths(seal))
    expected = _outputs(result["files"])
    expected_items = sorted(item_key(i) for i in result["items"])
    sealed_outputs = body["outputs"]
    if ctx.common is None and outlet.common is not None:
        warnings.append({"word": "common_unchecked"})
        # 共通の台帳を読めないとき、共通から採った物は封の出力のまま確かめる（止めるを確かめられないまま）
        for path, digest_ in sealed_outputs.items():
            if path.startswith(COMMON + "/"):
                expected[path] = digest_
    names = set(sealed_outputs) | set(expected)
    here = placed(root, vessel, outlet, material, names)
    stale = sorted(p for p in here if p not in sealed_outputs and p not in expected)
    if stale:
        red("stale_material", files=stale[:50])
    narrowed = sorted(set(body["items"]) - set(expected_items))
    widened = sorted(set(expected_items) - set(body["items"]))
    why = {f"{e['record']}" + (f"#{e['unit']}" if e.get("unit") else ""): e["why"] for e in result["excluded"]}
    narrowed_detail = [{"item": k, "why": why.get(k.rsplit("=", 1)[0], "narrowed")} for k in narrowed]
    placed_sealed = all(here.get(p) == d for p, d in sealed_outputs.items()) and not (set(here) - set(sealed_outputs))
    placed_expected = all(here.get(p) == d for p, d in expected.items()) and not (set(here) - set(expected))
    if widened:
        red("widened_after_seal", items=widened[:50])
    if narrowed or expected != sealed_outputs:
        if outlet.on_narrow == "drop" and not widened and placed_expected and _only_dropped(body, result, expected):
            warnings.append({"word": "dropped", "items": narrowed_detail[:50]})
        else:
            red("narrowed_after_seal", items=narrowed_detail[:50])
            if not placed_sealed:
                red("material_mismatch", files=sorted(p for p in names | set(here)
                                                      if here.get(p) != sealed_outputs.get(p))[:50])
    elif not placed_sealed:
        red("material_mismatch", files=sorted(p for p in names | set(here) if here.get(p) != sealed_outputs.get(p))[:50])
    verdict = _verdict(problems, warnings, key=origin, generation=body["generation"],
                       pending_takes=result["pending_takes"])
    if publish is not None and verdict["ok"]:
        write_published(publish, seal, raw, as_of, narrowed)
        verdict["published"] = os.path.abspath(publish)
    return verdict


def _verdict(problems, warnings, **extra) -> dict:
    return {"ok": not problems, "problems": problems, "warnings": warnings, **extra}


# ------------------------------------------------------------------ 判断を足す

def _stamp(body: dict) -> dict:
    body.pop("id", None)
    body["id"] = J.new_id(body)
    return body


def judge(root: str, ref: str, question: str, *, by: str, at: str, unit=None, value=None, word=None,
          conclusion=None, reason_kind=None, reason_text=None, how=None, until=None, against=None) -> dict:
    if not isinstance(question, str) or not QUESTION.fullmatch(question) or question == TAKE:
        raise RegisterRefused("register_args_invalid", "question")
    if conclusion is not None and conclusion not in V.CONCLUSIONS:
        raise RegisterRefused("register_args_invalid", "conclusion")
    if word is None and conclusion is None:
        raise RegisterRefused("register_args_invalid", "word_or_conclusion")
    if reason_kind is not None and not V.FIELD.fullmatch(reason_kind):
        raise RegisterRefused("register_args_invalid", "reason_kind")
    vessel = V.open_vessel(root)
    target = {"record": ref}
    if unit:
        target["unit"] = unit
    if value:
        target["value"] = value
    pinned = []
    for one in against or []:
        rec = vessel.unique(one)
        if rec is None:
            raise RegisterRefused("register_record_missing", "against")
        pinned.append({"record": rec.ref, "version": rec.version})
    body = J.make(vessel, target, question, by=by, at=at, word=word, conclusion=conclusion,
                  reason_kind=reason_kind, reason_text=reason_text, how=how, until=check_until(until),
                  extra={"against": pinned} if pinned else None)
    J.append(root, [body])
    return body


def stop(root: str, ref: str, *, by: str, at: str, outlet=None, lift: bool = False, unit=None, until=None,
         reason_text=None) -> dict:
    """止める（だめ・封を待たない）か、止めない（よい・広げるので封が要る）。番号に付き、中身が変わっても外れない。"""
    question = f"{STOP}@{outlet}" if outlet else STOP
    if not QUESTION.fullmatch(question):
        raise RegisterRefused("register_args_invalid", "outlet")
    vessel = V.open_vessel(root)
    rec = vessel.get(ref)
    target = {"record": rec.ref}
    if unit:
        if V.unit_at(rec, unit) is None:
            raise RegisterRefused("register_record_missing", "unit")
        target["unit"] = unit
    body = {"schema": J.SCHEMA, "target": target, "question": question,
            "answer": {"word": "resume" if lift else "stop", "conclusion": "good" if lift else "bad", "scope": None},
            "reason": {"kind": None, "text": reason_text}, "by": by, "how": None, "at": at,
            "until": check_until(until), "path": "acceptance", "carries": None,
            "seen": {"version": rec.version, "depend": None}}
    J.append(root, [_stamp(body)])
    return body


def baseline(root: str, outlet_name: str, *, by: str, at: str) -> list:
    """基準（設計 §8.2 の 5）: 今の版の、読者に出る出し口の記録を「出してよい」として、出し口の条件の問いごとに
    1 回で受ける。状態の欄を持つ問いは、その版に書いてあった語（観測）を受け入れる。持たない問いは「よい」。"""
    vessel = V.open_vessel(root)
    outlet = outlet_of(root, vessel, outlet_name)
    if outlet.audience != "reader":
        raise RegisterRefused("register_args_invalid", "outlet_internal")
    questions = [q for q, _c in outlet.requires + outlet.fact]
    if outlet.reservation is not None:
        questions.append(outlet.reservation["question"])
    out = []
    for kind_name in outlet.kinds:
        kind = vessel.kinds[kind_name]
        for question in dict.fromkeys(questions):
            q = V.question_of(kind, question)
            if q is not None and q.state:
                out.extend(J.accept_baseline(vessel, kind_name, question, by=by, at=at))
                continue
            if q is not None and q.level == "unit":
                continue
            for rec in vessel.all_records:
                if rec.kind != kind_name or rec.id is None or rec.problems or vessel.is_duplicated(rec.ref):
                    continue
                out.append(J.make(vessel, {"record": rec.ref}, question, by=by, at=at, path="acceptance",
                                  conclusion="good", reason_text="基準の受け入れ"))
    J.append(root, out)
    return out


def carry(root: str, judgment_id: str, *, to: str, by: str, at: str, changed_by) -> dict:
    """結び直す口（設計 §5.3）: 前の判断の元の検収を、新しい版（記録）か新しい指し先（単位）へ結ぶ**新しい判断**。
    中身を変えた者と同じ者は結べない。記録の版へ結ぶときは、`to` に今の版（の頭 8 字以上）を名指しする。"""
    vessel = V.open_vessel(root)
    every = J.read(root)
    old = next((j for j in every if j.get("id") == judgment_id), None)
    if old is None:
        raise RegisterRefused("register_record_missing", "judgment")
    target = old.get("target") or {}
    if target.get("unit"):
        body = J.carry(vessel, old, to, by=by, at=at, changed_by=changed_by)
    else:
        if not isinstance(changed_by, str) or not changed_by.strip():
            raise RegisterRefused("register_carry_unnamed", "changed_by")
        if by == changed_by:
            raise RegisterRefused("register_judgment_same_person", "by")
        rec = vessel.get(target.get("record"))
        if not isinstance(to, str) or len(to) < 8 or not rec.version.startswith(to):
            raise RegisterRefused("register_args_invalid", "to_version")
        a, r = old.get("answer") or {}, old.get("reason") or {}
        body = J.make(vessel, dict(target), old["question"], by=by, at=at, path="carry", word=a.get("word"),
                      conclusion=a.get("conclusion"), reason_kind=r.get("kind"), reason_text=r.get("text"),
                      how=old.get("how"), carries=old.get("id"), until=old.get("until"))
    J.append(root, [_stamp(body)])
    return body


def take(root: str, common_root, records, *, by: str, at: str) -> dict:
    """共通の記録の今の版を、まとめて採る（`records` が空なら、前に採った物を全部進める）。"""
    common = open_common(common_root)
    if common is None:
        raise RegisterRefused("register_common_unusable", "common")
    every = J.read(root)
    last = _takes(every)
    refs = list(records or []) or sorted(last)
    if not refs:
        raise RegisterRefused("register_args_invalid", "record")
    taken, same = [], []
    for ref in refs:
        rec = common.vessel.unique(ref)
        if rec is None:
            raise RegisterRefused("register_record_missing", "common_record")
        if common.vessel.kinds[rec.kind].records != "file":
            raise RegisterRefused("register_common_unusable", "rows")
        with open(os.path.join(common.root, *rec.path.split("/")), "rb") as f:
            data = f.read()
        if sha(data) != rec.version:
            raise RegisterRefused("register_batch_conflict", "moved")
        K.put_object(common.root, data)   # 採った版を、共通の台帳の器の控えから取り出せるように
        before = (last.get(ref) or {}).get("seen", {}).get("version")
        if before == rec.version:
            same.append(ref)
            continue
        body = {"schema": J.SCHEMA, "target": {"ledger": COMMON, "record": ref}, "question": TAKE,
                "answer": {"word": None, "conclusion": "good", "scope": None}, "reason": {"kind": None, "text": None},
                "by": by, "how": None, "at": at, "until": None, "path": "acceptance", "carries": None,
                "seen": {"version": rec.version, "depend": None}, "from_version": before}
        taken.append(_stamp(body))
    J.append(root, taken)
    return {"taken": [{"record": j["target"]["record"], "from": j["from_version"], "to": j["seen"]["version"]}
                      for j in taken], "same": same}


def use(root: str, ref: str, outlet_name: str, as_: str, as_of, *, unit=None, common_root=None) -> dict:
    """手元で「使えるか」を問う。CI と同じ計算（封の中の判断と、封を待たない狭める物）。"""
    if as_ not in AS:
        raise RegisterRefused("register_args_invalid", "as")
    vessel = V.open_vessel(root)
    outlet = outlet_of(root, vessel, outlet_name)
    seal, state = trusted_seal(root, outlet.name)
    ctx = context(root, as_of, common_root=common_root, seal=seal)
    rec = ctx.vessel.get(ref)
    if unit is not None and V.unit_at(rec, unit) is None:
        raise RegisterRefused("register_record_missing", "unit")
    if outlet.level == "unit" and unit is None:
        raise RegisterRefused("register_args_invalid", "unit")
    if rec.kind not in outlet.kinds:
        return {"record": ref, "unit": unit, "outlet": outlet.name, "usable": False, "as": None,
                "why": "kind_not_in_outlet"}
    got = decide(ctx, outlet, rec.ref, unit)
    usable = got["as"] == as_ or (as_ == "reservation" and got["as"] == "fact")
    why = got["why"]
    if not usable and got["as"] == "reservation":
        why = "only_reservation"
    out = {"record": rec.ref, "unit": unit, "outlet": outlet.name, "asked": as_, "as": got["as"], "usable": usable,
           "why": None if usable else why, "sealed": seal is not None, "seal": state,
           "generation": seal["body"]["generation"] if seal else None}
    if got.get("reason_kind"):
        out["reason_kind"] = got["reason_kind"]
    if seal is None and not usable:
        out["why"] = "no_seal" if state == "none" else "seal_unverified"
    return out
