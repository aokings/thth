"""台帳の器 段 7 の後半 §7.6「出した物の控えと関門」（指示 `docs/指示_台帳の器_段6と段7_書く口と出口の残り_2026-11-14.md`
§7.6・「済み」の線の 4。設計 `docs/設計_台帳の器_2026-11-13.md` §5.6 の末尾）。

    thth register published <台帳> --outlet <出し口> --list <一覧> [--record] [--now <日時>]
                            (--previous <前に配った控えの頭> | --no-previous) [--publish <配る控えの頭の置き場>] [--json]

- **控え**（`.thth/published/<出し口>.jsonl`・追記だけ）: 1 行 = 出した URL（か道）・初めて出した世代と日時・今の扱い
  （`live` 出ている・`redirect` 転送・`gone` 消した）・この扱いを書いた日時。同じ URL の扱いが変われば、行を足す
  （後の行が今の扱い）。行は書き換えない・消さない。
- **控えの頭**（`.thth/published/<出し口>.head.json`）: 最後に `--record` で書いたときのバイトの数と sha256。控えは
  この頭の続きでなければ赤（`log_rewritten`。行を消す・書き換える・頭ごと消す）。頭も一緒に書き換えられる所は、
  `--previous`（前に配った控えの頭。`--publish` が緑のときに書く物を、CI の手順が公開の場所から取る）で見る。
  封の `--previous` と同じ考え（`route.py` の頭の説明）。
- **関門**: build のあとの一覧（URL か道を 1 行 1 つ。空の行と `#` の行は読まない。タブのあとは読まない）と控えを
  比べる。**前に出した URL が一覧に無ければ赤**。通すのは、出し口の宣言の `published` に書いた転送（`redirects`）と
  例外（`gone`）だけ。転送は、許す番号（`redirect_status`。既定 301・308）で、転送先が一覧に在るときだけ通す
  （先がさらに転送なら鎖として赤・先がよその URL か型（`*`・`:`）なら確かめられないとして知らせ）。
  `gone_redirect_status`（既定は空）の番号は、`gone` に載った URL のときだけ通す（関東の一時の転送）。
  二重（一覧に同じ URL が 2 度・`duplicate`）と、転送元なのに一覧にも在る（`redirect_with_page`）は、既定で赤
  （宣言で `warn` にできる）。
- `--record` を付けたときだけ、緑なら控えに足す（新しい URL と、扱いの変わった URL）。付けなければ比べるだけで、
  台帳に何も書かない。器は網に出ない（URL が 200 かは見ない。それは各メディアの門）。

URL の比べ方（`normalize`）: `path`（既定）は、今の 4 メディアの台本と同じ形（URL なら道だけ・パーセントの字を戻す・
`//` を 1 つに・拡張子の無い道は末尾に `/` を 1 つ・問いと断片は比べない）。`exact` は字のまま。
"""
from __future__ import annotations

import datetime
import hashlib
import json
import os
import re

from . import route as R
from . import vessel as V
from .errors import RegisterRefused

DIR = "published"
HEAD_SCHEMA = "thth.published_head/1"
STATES = ("live", "redirect", "gone")
LEVELS = ("red", "warn")
REDIRECTS_MAX = 100_000
GONE_MAX = 100_000
URL_MAX = 2048
LIST_BYTES_MAX = 64 * 1024 * 1024
LIST_LINES_MAX = 1_000_000
SHOWN_MAX = 200
_RULE_KEYS = ("normalize", "redirect_status", "gone_redirect_status", "redirects", "gone", "duplicate",
              "redirect_with_page", "note")
DEFAULT_STATUS = (301, 308)

# 赤の語と、直し方（前向きな断り: なぜ・通る直し方。CLI の人の読む出力に使う）
FIX = {
    "missing": "前に出した URL が一覧に無い。転送を出し口の宣言の published.redirects に書くか、消すと決めたなら "
               "published.gone に理由の種類と書く（控えの行は消さない）",
    "redirect_status": "転送の番号が許す番号でない。301 か 308 にするか、宣言の redirect_status に足す",
    "redirect_target_missing": "転送先が一覧に無い。先の頁を出すか、転送先を今ある頁に直す",
    "redirect_chain": "転送先がさらに転送されている。元から最後の先へ直に転送する",
    "duplicate": "一覧に同じ URL が 2 度ある。build の一覧の作り方を直す",
    "redirect_with_page": "転送元なのに一覧にも頁がある。転送の行を外すか、頁を出さない",
    "log_rewritten": "控えの行が書き換えられたか消された。控えは追記だけ。git の前の版から戻す",
    "head_missing": "控えはあるのに控えの頭が無い。git の前の版から頭を戻す",
    "previous_not_continued": "前に配った控えの頭の続きになっていない（行を消して頭も書き直した）。前の版から戻す",
    "log_invalid": "控えの行が読めない形。git の前の版から戻す",
}


# ------------------------------------------------------------------ 宣言

def _codes(raw, word) -> tuple:
    if not isinstance(raw, list) or len(raw) > 16 or any(
            isinstance(c, bool) or not isinstance(c, int) or not 100 <= c <= 599 for c in raw):
        raise V.DeclarationError(word)
    return tuple(dict.fromkeys(raw))


def _url(raw, word) -> str:
    if not isinstance(raw, str) or not raw.strip() or len(raw) > URL_MAX or any(c in raw for c in "\r\n\t\x00"):
        raise V.DeclarationError(word)
    return raw.strip()


def parse_rules(raw, word: str) -> dict:
    """出し口の宣言 `outlets.<出し口>.published` を読む。どんな値を書かれても `DeclarationError` だけを出す。"""
    if not isinstance(raw, dict) or set(raw) - set(_RULE_KEYS):
        raise V.DeclarationError(word)
    normalize = raw.get("normalize", "path")
    if normalize not in ("path", "exact"):
        raise V.DeclarationError(f"{word}:normalize")
    status = _codes(raw.get("redirect_status", list(DEFAULT_STATUS)), f"{word}:redirect_status")
    temp = _codes(raw.get("gone_redirect_status", []), f"{word}:gone_redirect_status")
    levels = {}
    for key in ("duplicate", "redirect_with_page"):
        value = raw.get(key, "red")
        if value not in LEVELS:
            raise V.DeclarationError(f"{word}:{key}")
        levels[key] = value
    redirects = raw.get("redirects", [])
    if not isinstance(redirects, list) or len(redirects) > REDIRECTS_MAX:
        raise V.DeclarationError(f"{word}:redirects")
    rules = []
    for i, r in enumerate(redirects):
        if not isinstance(r, dict) or set(r) - {"from", "to", "status", "note"} or "from" not in r or "to" not in r:
            raise V.DeclarationError(f"{word}:redirects")
        code = r.get("status", 301)
        if isinstance(code, bool) or not isinstance(code, int) or not 100 <= code <= 599:
            raise V.DeclarationError(f"{word}:redirects")
        rules.append({"from": _url(r["from"], f"{word}:redirects"), "to": _url(r["to"], f"{word}:redirects"),
                      "status": code, "n": i + 1})
    gone_raw = raw.get("gone", [])
    if not isinstance(gone_raw, list) or len(gone_raw) > GONE_MAX:
        raise V.DeclarationError(f"{word}:gone")
    gone = []
    for g in gone_raw:
        if not isinstance(g, dict) or set(g) - {"url", "reason_kind", "note"} or "url" not in g:
            raise V.DeclarationError(f"{word}:gone")
        kind = g.get("reason_kind")
        if not isinstance(kind, str) or not V.FIELD.fullmatch(kind):
            raise V.DeclarationError(f"{word}:gone")   # 理由の種類の無い例外は受けない（今の台本の「理由が空なら赤」）
        gone.append({"url": _url(g["url"], f"{word}:gone"), "reason_kind": kind})
    return {"normalize": normalize, "redirect_status": status, "gone_redirect_status": temp, "redirects": rules,
            "gone": gone, **levels}


# ------------------------------------------------------------------ URL の比べ方
# 台帳の機械は網の部品を import しない（`tests/test_register_base.py` の柵）。urllib.parse の urlsplit・unquote の、
# ここで要る所だけを同じ答えになるように書く。

_PCT = re.compile(r"(?:%[0-9A-Fa-f]{2})+")
_SCHEME = re.compile(r"[A-Za-z][A-Za-z0-9+.\-]*:")


def unquote(text: str) -> str:
    """パーセントの字を戻す（続いた `%XX` を UTF-8 として読み、読めない所は置き換えの字。urllib と同じ）。"""
    if "%" not in text:
        return text
    return _PCT.sub(lambda m: bytes.fromhex(m.group(0).replace("%", "")).decode("utf-8", "replace"), text)


def _split(raw: str) -> tuple:
    """`(scheme, netloc, path)`。問いと断片は捨てる。"""
    rest, scheme, netloc = raw, "", ""
    m = _SCHEME.match(rest)
    if m and not rest[:m.end() - 1].isdigit():
        scheme, rest = rest[:m.end() - 1], rest[m.end():]
    if rest.startswith("//"):
        rest = rest[2:]
        cut = min([i for i in (rest.find("/"), rest.find("?"), rest.find("#")) if i >= 0] or [len(rest)])
        netloc, rest = rest[:cut], rest[cut:]
    for mark in ("#", "?"):
        if mark in rest:
            rest = rest[:rest.index(mark)]
    return scheme, netloc, rest


def normalize_path(raw: str) -> str:
    """今の 4 メディアの台本（`gate_published_urls.py`・関東の `published_urls_gate.py`）の `normalize_path` と同じ形。"""
    s = raw.strip()
    path = _split(s)[2]
    path = unquote(path)
    if not path.startswith("/"):
        path = "/" + path
    while "//" in path:
        path = path.replace("//", "/")
    if "." in path.rsplit("/", 1)[-1]:
        return path
    if not path.endswith("/"):
        path += "/"
    return path


def _norm(rules: dict, raw: str) -> str:
    return normalize_path(raw) if rules["normalize"] == "path" else raw.strip()


def _pattern(raw: str) -> bool:
    path = _split(raw)[2] if "://" in raw else raw
    return "*" in path or ":" in path


def _external(rules: dict, raw: str) -> bool:
    if rules["normalize"] != "path":
        return False
    scheme, netloc, _path = _split(raw.strip())
    return bool(scheme or netloc) or not raw.strip().startswith("/")


class _Rules:
    def __init__(self, rules: dict):
        self.rules = rules
        self.exact, self.prefix = {}, []
        for r in rules["redirects"]:
            src = r["from"] if rules["normalize"] != "path" else unquote(r["from"])
            if src.endswith("/*"):
                self.prefix.append((src[:-1], r))
            elif not _pattern(src):
                self.exact.setdefault(_norm(rules, src), r)
        self.gone = {_norm(rules, g["url"]): g for g in rules["gone"]}

    def covered(self, url: str, skip=None):
        hit = self.exact.get(url)
        if hit is not None and hit is not skip:
            return hit
        for pre, r in self.prefix:
            if r is not skip and url.startswith(pre):
                return r
        return None


# ------------------------------------------------------------------ 読む

def log_path(root: str, outlet: str) -> str:
    return os.path.join(root, V.DECL_DIR, DIR, f"{outlet}.jsonl")


def head_path(root: str, outlet: str) -> str:
    return os.path.join(root, V.DECL_DIR, DIR, f"{outlet}.head.json")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_list(path, rules: dict) -> list:
    """build のあとの一覧（1 行 1 つ）。空の行と `#` の行は読まない。タブのあとは読まない。"""
    if not isinstance(path, str) or not path:
        raise RegisterRefused("register_args_invalid", "list")
    if not os.path.isfile(path):
        raise RegisterRefused("register_args_invalid", "list_missing")
    with open(path, "rb") as f:
        data = f.read(LIST_BYTES_MAX + 1)
    if len(data) > LIST_BYTES_MAX:
        raise RegisterRefused("register_args_invalid", "list_too_large")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise RegisterRefused("register_args_invalid", "list_not_utf8") from None
    out = []
    for line in text.splitlines():
        cell = line.split("\t", 1)[0].strip()
        if not cell or cell.startswith("#"):
            continue
        if len(cell) > URL_MAX or "\x00" in cell:
            raise RegisterRefused("register_args_invalid", "list_line")
        out.append(_norm(rules, cell))
        if len(out) > LIST_LINES_MAX:
            raise RegisterRefused("register_args_invalid", "list_too_large")
    return out


def _read_head(path: str):
    """頭のファイル: None（無い）・dict（読めた）・False（形が崩れた）。"""
    if not os.path.exists(path):
        return None
    try:
        with open(path, "rb") as f:
            value = json.loads(f.read(64 * 1024).decode("utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return False
    if (not isinstance(value, dict) or value.get("schema") != HEAD_SCHEMA or not isinstance(value.get("bytes"), int)
            or isinstance(value.get("bytes"), bool) or value["bytes"] < 0 or not isinstance(value.get("sha256"), str)):
        return False
    return value


def read_log(root: str, outlet: str, rules: dict, previous=None) -> tuple:
    """`(今の扱い {URL: 最後の行}, 控えのバイト, 問題 [...])`。控えが頭（と前に配った頭）の続きでなければ問題に出す。"""
    path, hpath = log_path(root, outlet), head_path(root, outlet)
    problems = []
    raw = b""
    if os.path.islink(path) or os.path.islink(hpath):
        problems.append({"word": "log_rewritten", "why": "symlink"})
        return {}, raw, problems
    if os.path.exists(path):
        with open(path, "rb") as f:
            raw = f.read(LIST_BYTES_MAX + 1)
        if len(raw) > LIST_BYTES_MAX:
            problems.append({"word": "log_invalid", "why": "too_large"})
            return {}, raw, problems
    head = _read_head(hpath)
    heads = [("head", head)]
    if previous is not None:
        prev = _read_head(previous) if os.path.exists(previous) else False
        if prev is not None and prev is not False and prev.get("outlet") not in (None, outlet):
            prev = False
        heads.append(("previous", prev))
    for name, h in heads:
        word = "log_rewritten" if name == "head" else "previous_not_continued"
        if h is False:
            problems.append({"word": word if name == "previous" else "log_invalid", "why": f"{name}_shape"})
        elif h is None:
            if raw:
                problems.append({"word": "head_missing"})
        elif len(raw) < h["bytes"] or _sha(raw[:h["bytes"]]) != h["sha256"]:
            problems.append({"word": word, "why": "shrunk" if len(raw) < h["bytes"] else "changed",
                             "head_bytes": h["bytes"], "bytes": len(raw)})
    state = {}
    if raw and not raw.endswith(b"\n"):
        problems.append({"word": "log_invalid", "why": "no_final_newline"})
    try:
        lines = raw.decode("utf-8").split("\n")
    except UnicodeDecodeError:
        problems.append({"word": "log_invalid", "why": "not_utf8"})
        return {}, raw, problems
    for n, line in enumerate(lines[:-1] if raw.endswith(b"\n") else lines, 1):
        if not line:
            if raw:
                problems.append({"word": "log_invalid", "line": n})
            continue
        try:
            row = json.loads(line)
        except ValueError:
            problems.append({"word": "log_invalid", "line": n})
            continue
        if (not isinstance(row, dict) or not isinstance(row.get("url"), str) or row.get("state") not in STATES
                or not isinstance(row.get("first_at"), str) or not isinstance(row.get("at"), str)
                or not (row.get("first_generation") is None or (isinstance(row.get("first_generation"), int)
                                                                and not isinstance(row.get("first_generation"), bool)))):
            problems.append({"word": "log_invalid", "line": n})
            continue
        state[_norm(rules, row["url"])] = row
    return state, raw, problems


# ------------------------------------------------------------------ 関門

def gate(rules: dict, listed: list, log_state: dict) -> dict:
    """控えの今の扱いと一覧を比べる（書かない）。`{"problems", "warnings", "counts", "changes"}`。"""
    r = _Rules(rules)
    now = set(listed)
    problems, warnings, changes = [], [], []

    def add(bucket, item):
        bucket.append(item)

    seen = set()
    for url in listed:
        if url in seen:
            add(problems if rules["duplicate"] == "red" else warnings, {"word": "duplicate", "url": url})
        seen.add(url)
    for src, rule in sorted(r.exact.items()):
        if src in now:
            add(problems if rules["redirect_with_page"] == "red" else warnings,
                {"word": "redirect_with_page", "url": src, "rule": rule["n"]})
    redirected = gone_n = 0
    for url in sorted(log_state):
        row = log_state[url]
        if url in now:
            if row["state"] != "live":
                changes.append({"url": url, "state": "live", "row": row})
            continue
        hit = r.covered(url)
        if hit is None:
            g = r.gone.get(url)
            if g is not None:
                gone_n += 1
                if row["state"] != "gone":
                    changes.append({"url": url, "state": "gone", "row": row})
                continue
            add(problems, {"word": "missing", "url": url, "first_at": row.get("first_at"),
                           "first_generation": row.get("first_generation")})
            continue
        if hit["status"] not in rules["redirect_status"] and not (
                hit["status"] in rules["gone_redirect_status"] and url in r.gone):
            add(problems, {"word": "redirect_status", "url": url, "status": hit["status"], "rule": hit["n"]})
            continue
        redirected += 1
        dst = hit["to"]
        if _external(rules, dst) or _pattern(dst):
            add(warnings, {"word": "redirect_target_unchecked", "url": url, "to": dst, "rule": hit["n"]})
        else:
            dn = _norm(rules, dst)
            if dn not in now:
                chained = r.covered(dn, skip=hit)
                add(problems, {"word": "redirect_chain" if chained else "redirect_target_missing", "url": url,
                               "to": dn, "rule": hit["n"]})
                continue
        if row["state"] != "redirect" or row.get("to") != dst:
            changes.append({"url": url, "state": "redirect", "to": dst, "row": row})
    new = sorted(now - set(log_state))
    counts = {"listed": len(listed), "logged": len(log_state), "redirected": redirected, "gone": gone_n,
              "new": len(new), "problems": len(problems), "warnings": len(warnings)}
    return {"problems": problems, "warnings": warnings, "counts": counts, "changes": changes, "new": new}


def _generation(root: str, outlet: str):
    try:
        seal = R.read_seal(root, outlet)
    except RegisterRefused:
        return None
    return seal["body"]["generation"] if seal else None


def _write_head(path: str, outlet: str, raw: bytes) -> dict:
    value = {"schema": HEAD_SCHEMA, "outlet": outlet, "bytes": len(raw), "sha256": _sha(raw),
             "lines": raw.count(b"\n")}
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=1) + "\n")
    os.replace(tmp, path)
    return value


def published(root: str, outlet_name: str, list_path, now, *, record: bool = False, previous=None,
              publish=None, no_previous: bool = False) -> dict:
    """§7.6 の関門。`{"ok", "problems", "warnings", "counts", "new", "recorded"}`。
    `record` が真で緑のときだけ控えに足す（と頭を書き直す）。`publish` を渡すと、緑のときだけ控えの頭をそこに書く
    （次の回の `previous`）。どちらも無ければ台帳に何も書かない。"""
    vessel = V.open_vessel(root)
    outlet = R.outlet_of(root, vessel, outlet_name)
    rules = outlet.published
    if rules is None:
        rules = parse_rules({}, "published")   # 宣言が無ければ既定の厳しさ（転送も例外も無し）
    when = R.parse_time(now, word="now")
    listed = read_list(list_path, rules)
    log_state, raw, problems = read_log(root, outlet.name, rules, previous)
    result = gate(rules, listed, log_state)
    problems = problems + result["problems"]
    if previous is None and no_previous:
        result["warnings"] = [{"word": "previous_skipped"}] + list(result["warnings"])
    out = {"outlet": outlet.name, "ok": not problems, "problems": problems[:SHOWN_MAX],
           "more": max(0, len(problems) - SHOWN_MAX), "warnings": result["warnings"][:SHOWN_MAX],
           "counts": dict(result["counts"], problems=len(problems)), "new": result["new"][:SHOWN_MAX],
           "changes": [{k: v for k, v in c.items() if k != "row"} for c in result["changes"]][:SHOWN_MAX],
           "declared": outlet.published is not None, "recorded": 0}
    if not out["ok"]:
        # 関門の赤は、受け入れの 1 段めと同じく終わりの番号 1 と verdict で返す（呼ぶ側の台本の読み方を変えない）。
        # verdict に断りの符丁と 3 つの文を載せる（段 4 §8 の断りの語）。
        out.update(_gate_refusal())
        return out
    if record and (result["new"] or result["changes"]):
        at = when.isoformat(timespec="seconds")
        gen = _generation(root, outlet.name)
        lines = []
        for url in result["new"]:
            lines.append({"url": url, "first_generation": gen, "first_at": at, "state": "live", "at": at})
        for c in result["changes"]:
            row = {"url": c["url"], "first_generation": c["row"].get("first_generation"),
                   "first_at": c["row"]["first_at"], "state": c["state"], "at": at}
            if c.get("to") is not None:
                row["to"] = c["to"]
            lines.append(row)
        data = "".join(json.dumps(x, ensure_ascii=False, sort_keys=True) + "\n" for x in lines).encode("utf-8")
        path = log_path(root, outlet.name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "ab") as f:
            f.write(data)
        raw = raw + data
        _write_head(head_path(root, outlet.name), outlet.name, raw)
        out["recorded"] = len(lines)
    if publish:
        out["head"] = _write_head(str(publish), outlet.name, raw)
    return out


def _gate_refusal() -> dict:
    """関門の赤の符丁 `register_published_gate` と、目録の 3 つの文（なぜ・通る直し方・もっと良く）。"""
    from thth import refusal
    found = refusal.parts("register_published_gate") or {}
    return {"code": "register_published_gate", **{k: found[k] for k in ("why", "fix", "better") if found.get(k)}}


def seed_lines(urls, *, at: str, generation=None) -> bytes:
    """今のメディアの控え（published-urls.txt）を器の控えへ移すときの行（台本・試験が使う。書くのは呼ぶ側）。"""
    return "".join(json.dumps({"url": u, "first_generation": generation, "first_at": d or at, "state": "live",
                               "at": d or at}, ensure_ascii=False, sort_keys=True) + "\n"
                   for u, d in urls).encode("utf-8")


def write_seed(root: str, outlet: str, urls, *, at: str) -> int:
    """`seed_lines` を控えに書き、頭を書く（移すときの 1 回だけ。控えがもうあれば断る）。"""
    path = log_path(root, outlet)
    if os.path.exists(path):
        raise RegisterRefused("register_args_invalid", "published_log_exists")
    data = seed_lines(urls, at=at)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)
    _write_head(head_path(root, outlet), outlet, data)
    return data.count(b"\n")


def now_default() -> str:
    return datetime.datetime.now(R.JST).isoformat(timespec="seconds")
