"""`thth doctor` は Threads の **11 権限すべて**を 1 行ずつ並べる（2026-09-14）。

背景: `DEFAULT_SCOPES` は 11 権限を要求する（設計 §8-14「権限は例外なく全部」）
のに、doctor が確かめていたのは 4 権限だけだった。残り 7 つは「乗っているはず
だが確かめていない」——「判らないことを判った形で残さない」に反する。

ここで固定するもの:
  - 11 権限が漏れなく並ぶ（1 権限に 1 行以上）
  - `ok=None`（読み取りでは確かめられない・確かめられなかった）は rc を 1 にしない
  - 権限不足の応答は「乗っていません」、5xx・接続断は「確かめられませんでした」
    ——**「乗っていない」と「確かめられない」を混ぜない**
  - `threads_delete`・`threads_share_to_instagram`・`threads_manage_replies` は
    読み取りの口が無いので `ok=None` で理由つき（× にしない）
  - **削除・投稿の口を 1 度も叩かない**（偽サーバが GET 以外を受けたら即 fail）
  - 検索の語は固定で各 1 回

本物の Threads API には一切触れない（`http.server` の偽 API にだけ向ける）。
"""
from __future__ import annotations

import contextlib
import http.server
import json
import threading
import urllib.parse

import pytest

from thth import doctor as doctor_mod
from thth import scopes as scopes_mod
from thth.adapters import threads as threads_mod

PERMISSION_ERROR = {"error": {"message": "(#10) Application does not have permission "
                                         "for this action",
                              "type": "OAuthException", "code": 10}}


class _ReadOnlyThreads(http.server.BaseHTTPRequestHandler):
    """読み取り専用の偽 Threads API。**GET 以外は全部記録して 405**。

    `behavior` は「path の末尾 → モード」。モードは "ok" | "permission" | "5xx" |
    "4xx"。無ければ "ok"。`/debug_token` だけ "absent"（404）・"partial"（delete と
    share_to_instagram が無い一覧）・"malformed"（200 だが scopes 無し）も受ける。
    """
    debug_scopes = list(scopes_mod.DEFAULT_SCOPES)
    partial_scopes = [x for x in scopes_mod.DEFAULT_SCOPES
                      if x not in ("threads_delete", "threads_share_to_instagram")]
    behavior: dict = {}
    requests: list = []          # (method, path, params)

    def _json(self, status, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _mode(self, path):
        for suffix, mode in self.behavior.items():
            if path.endswith(suffix):
                return mode
        return "ok"

    def do_GET(self):  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        params = dict(urllib.parse.parse_qsl(parsed.query))
        self.requests.append(("GET", parsed.path, params))
        mode = self._mode(parsed.path)
        if parsed.path == "/v1.0/debug_token":
            if mode == "absent":
                return self._json(404, {"error": {"message": "Unknown path", "code": 803}})
            if mode == "malformed":
                return self._json(200, {"data": {"is_valid": True}})
            if mode == "partial":
                return self._json(200, {"data": {"scopes": self.partial_scopes}})
            if mode == "ok":
                return self._json(200, {"data": {"scopes": self.debug_scopes,
                                                 "is_valid": True, "user_id": "999999"}})
        if mode == "permission":
            return self._json(400, PERMISSION_ERROR)
        if mode == "5xx":
            return self._json(503, {"error": {"message": "temporarily unavailable",
                                              "code": 2}})
        if mode == "4xx":
            return self._json(400, {"error": {"message": "Invalid parameter",
                                              "code": 100}})
        p = parsed.path
        if p == "/v1.0/me":
            return self._json(200, {"id": "999999", "username": "nigamilab"})
        if p.endswith("/threads"):
            return self._json(200, {"data": [{"id": "POST1", "permalink": "https://x/p",
                                              "timestamp": "2026-09-10T00:00:00+0000"}]})
        if p.endswith("/profile_lookup"):
            return self._json(200, {"username": params.get("username")})
        return self._json(200, {"data": []})

    def _refuse(self):
        parsed = urllib.parse.urlparse(self.path)
        self.requests.append((self.command, parsed.path, {}))
        self._json(405, {"error": {"message": "read-only fake"}})

    do_POST = do_PUT = do_PATCH = do_DELETE = _refuse  # noqa: N815

    def log_message(self, format, *args):  # noqa: A002
        pass


@contextlib.contextmanager
def _server(behavior=None):
    requests: list = []
    handler = type("H", (_ReadOnlyThreads,), {"behavior": dict(behavior or {}),
                                               "requests": requests})
    server = http.server.HTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", requests
    finally:
        server.shutdown()
        thread.join(timeout=5)


def _write_token(path, *, scopes=None, scopes_source=None):
    data = {"access_token": "ALLPERM-SECRET-TOKEN",
            "obtained_at": "2026-09-09T00:00:00+09:00", "expires_in": 5184000,
            "user_id": "999999", "username": "nigamilab", "scopes": scopes}
    if scopes_source is not None:
        data["scopes_source"] = scopes_source
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f)


@pytest.fixture
def account(tmp_path, isolated_account_factory):
    token_path = str(tmp_path / "a.token")
    acc = isolated_account_factory(token=token_path, handle="nigamilab")
    _write_token(token_path)
    acc["token_path"] = token_path
    return acc


def _diagnose(monkeypatch, account, behavior=None):
    with _server(behavior) as (base_url, requests):
        monkeypatch.setenv("THTH_THREADS_BASE_URL", base_url)
        report = doctor_mod.diagnose(account["name"])
    return report, requests


def _by_permission(report):
    out: dict = {}
    for p in report["probes"]:
        out.setdefault(p["permission"], []).append(p)
    return out


UNVERIFIABLE = ("threads_manage_replies", "threads_delete", "threads_share_to_instagram")


# ---------------------------------------------------------------- 11 権限が並ぶ

def test_11権限すべてが少なくとも1行に出る(monkeypatch, account):
    report, _ = _diagnose(monkeypatch, account)
    got = {p["permission"] for p in report["probes"]}
    assert got == set(scopes_mod.DEFAULT_SCOPES), (
        f"足りない: {set(scopes_mod.DEFAULT_SCOPES) - got}・余計: "
        f"{got - set(scopes_mod.DEFAULT_SCOPES)}")
    assert len(scopes_mod.DEFAULT_SCOPES) == 11


def test_読み取りの口がある権限は全部叩いて丸になる(monkeypatch, account):
    report, requests = _diagnose(monkeypatch, account, NO_DEBUG)
    rows = _by_permission(report)
    for perm in set(scopes_mod.DEFAULT_SCOPES) - set(UNVERIFIABLE):
        assert all(p["ok"] is True for p in rows[perm]
                   if p["key"] != threads_mod.ThreadsAdapter.DEBUG_TOKEN_KEY), (
            perm, rows[perm])
    paths = [path for _m, path, _q in requests]
    assert "/v1.0/keyword_search" in paths
    assert "/v1.0/999999/mentions" in paths
    assert "/v1.0/profile_lookup" in paths
    assert "/v1.0/location_search" in paths


NO_DEBUG = {"/debug_token": "absent"}


def test_deleteとshare_to_instagramとmanage_repliesはNoneで理由つき(monkeypatch, account):
    """`/debug_token` が取れないときは従来どおり None（読み取りでは確かめられない）。"""
    report, requests = _diagnose(monkeypatch, account, NO_DEBUG)
    rows = _by_permission(report)
    for perm in UNVERIFIABLE:
        assert len(rows[perm]) == 1, rows[perm]
        p = rows[perm][0]
        assert p["ok"] is None, p
        assert threads_mod.UNVERIFIABLE_BY_READ in p["detail"], p
        assert len(p["detail"]) > len(threads_mod.UNVERIFIABLE_BY_READ) + 4, (
            "理由が書かれていない", p)
        assert threads_mod.NOT_GRANTED not in p["detail"], "× の文言と混ぜない"
    # 叩いていない（口が無いので叩けない）。
    assert not [r for r in requests if "delete" in r[1] or "manage_reply" in r[1]]


# --------------------------------------------------------------- rc と表示

# `/debug_token` が 5xx → その行も 3 行も None（rc に数えない）。404 のような
# 4xx は「API が断った」で ×（他の probe と同じ物差し・rc=1）。
DEBUG_5XX = {"/debug_token": "5xx"}


def test_okNoneはrcを1にしない_人向け(monkeypatch, account):
    with _server(DEBUG_5XX) as (base_url, _requests):
        monkeypatch.setenv("THTH_THREADS_BASE_URL", base_url)
        lines: list = []
        rc = doctor_mod.run_doctor(account["name"], log=lines.append)
    out = "\n".join(lines)
    assert rc == 0, out
    marks = [line for line in lines if line.startswith("  ― ")]
    assert len(marks) == len(UNVERIFIABLE) + 1, out          # +1 は debug_token の行
    assert "debug_token の scope: 取れませんでした" in out, out
    assert "読み取りでは確かめられません" in out
    assert "ALLPERM-SECRET-TOKEN" not in out


def test_okNoneはrcを1にしない_json(monkeypatch, account):
    with _server(DEBUG_5XX) as (base_url, _requests):
        monkeypatch.setenv("THTH_THREADS_BASE_URL", base_url)
        lines: list = []
        rc = doctor_mod.run_doctor(account["name"], as_json=True, log=lines.append)
    assert rc == 0
    payload = json.loads(lines[-1])
    oks = [p["ok"] for p in payload["probes"]]
    assert oks.count(None) == len(UNVERIFIABLE) + 1, oks    # +1 は debug_token の行
    assert set(oks) <= {True, False, None}
    assert "ALLPERM-SECRET-TOKEN" not in lines[-1]


# ------------------------------------------- 「乗っていない」と「確かめられない」

def test_権限不足の応答は乗っていませんと言う(monkeypatch, account):
    report, _ = _diagnose(monkeypatch, account, {"/keyword_search": "permission"})
    p = _by_permission(report)["threads_keyword_search"][0]
    assert p["ok"] is False, p
    assert threads_mod.NOT_GRANTED in p["detail"], p
    assert threads_mod.COULD_NOT_VERIFY not in p["detail"], p


def test_権限不足はrcを1にする(monkeypatch, account):
    with _server({"/keyword_search": "permission"}) as (base_url, _requests):
        monkeypatch.setenv("THTH_THREADS_BASE_URL", base_url)
        rc = doctor_mod.run_doctor(account["name"], log=lambda _l: None)
    assert rc == 1


def test_5xxは確かめられませんでしたと言いrcを1にしない(monkeypatch, account):
    with _server({"/mentions": "5xx"}) as (base_url, _requests):
        monkeypatch.setenv("THTH_THREADS_BASE_URL", base_url)
        report = doctor_mod.diagnose(account["name"])
        lines: list = []
        rc = doctor_mod.run_doctor(account["name"], log=lines.append)
    p = _by_permission(report)["threads_manage_mentions"][0]
    assert p["ok"] is None, p
    assert threads_mod.COULD_NOT_VERIFY in p["detail"], p
    assert threads_mod.NOT_GRANTED not in p["detail"], "5xx を権限不足と混ぜた"
    assert rc == 0, "\n".join(lines)


def test_接続できないときも確かめられませんでした(monkeypatch, account):
    # 閉じた port（誰も listen していない）。
    with _server() as (base_url, _requests):
        pass
    monkeypatch.setenv("THTH_THREADS_BASE_URL", base_url)
    report = doctor_mod.diagnose(account["name"])
    for p in report["probes"]:
        if p["permission"] in UNVERIFIABLE:
            continue
        assert p["ok"] is not False, ("接続断を × にした", p)
        if p["key"] != "replies":
            assert threads_mod.COULD_NOT_VERIFY in p["detail"], p
    assert "ALLPERM-SECRET-TOKEN" not in json.dumps(report, ensure_ascii=False)


def test_その他の4xxは今までどおりバツ(monkeypatch, account):
    report, _ = _diagnose(monkeypatch, account, {"/location_search": "4xx"})
    p = _by_permission(report)["threads_location_tagging"][0]
    assert p["ok"] is False, p
    assert threads_mod.NOT_GRANTED not in p["detail"], p
    assert threads_mod.COULD_NOT_VERIFY not in p["detail"], p


# ------------------------------------------------ 書き込みの口を 1 度も叩かない

def test_書き込みの口を1度も叩かない(monkeypatch, account):
    _report, requests = _diagnose(monkeypatch, account)
    methods = {m for m, _p, _q in requests}
    assert methods == {"GET"}, requests
    for _m, path, _q in requests:
        assert "threads_publish" not in path.replace("threads_publishing_limit", "")
        assert "manage_reply" not in path and "repost" not in path


def test_検索語は固定で各1回だけ(monkeypatch, account):
    _report, requests = _diagnose(monkeypatch, account)
    kw = [q for _m, p, q in requests if p == "/v1.0/keyword_search"]
    loc = [q for _m, p, q in requests if p == "/v1.0/location_search"]
    prof = [q for _m, p, q in requests if p == "/v1.0/profile_lookup"]
    assert len(kw) == 1 and kw[0]["q"] == threads_mod.ThreadsAdapter.KEYWORD_SEARCH_QUERY
    assert len(loc) == 1 and loc[0]["q"] == threads_mod.ThreadsAdapter.LOCATION_SEARCH_QUERY
    # **L2**（threads-profiles）: 標準アクセスで引けるのは Meta の公式アカウント
    # だけ。自分の handle を引いても権限の有無は見分けられない。
    assert len(prof) == 1 and prof[0]["username"] == "threads"
    assert prof[0]["username"] == threads_mod.ThreadsAdapter.PROFILE_LOOKUP_USERNAME


# ----------------------------------------------------- 記録上の scope（先頭の 1 行）

def test_記録上のscopeが先頭に出る_一覧あり(monkeypatch, tmp_path, isolated_account_factory):
    token_path = str(tmp_path / "b.token")
    acc = isolated_account_factory(token=token_path, handle="nigamilab")
    _write_token(token_path, scopes=list(scopes_mod.DEFAULT_SCOPES), scopes_source="requested")
    with _server() as (base_url, _requests):
        monkeypatch.setenv("THTH_THREADS_BASE_URL", base_url)
        lines: list = []
        doctor_mod.run_doctor(acc["name"], log=lines.append)
    out = "\n".join(lines)
    assert "記録上の scope: 11 個（source=requested）" in out, out
    # 先頭＝アカウント行の直後（probe より前）。
    idx_scope = out.index("記録上の scope")
    idx_probe = out.index("threads_basic")
    assert idx_scope < idx_probe


def test_記録上のscopeが先頭に出る_null(monkeypatch, account):
    with _server() as (base_url, _requests):
        monkeypatch.setenv("THTH_THREADS_BASE_URL", base_url)
        lines: list = []
        doctor_mod.run_doctor(account["name"], log=lines.append)
        lines_json: list = []
        doctor_mod.run_doctor(account["name"], as_json=True, log=lines_json.append)
    out = "\n".join(lines)
    assert "記録上の scope: 不明" in out and "source=unknown" in out, out
    payload = json.loads(lines_json[-1])
    assert payload["scopes_recorded"] == {"count": None, "scopes": None,
                                          "source": "unknown"}


# ----------------------------------------------------- /debug_token（監査後の追加）

DEBUG_KEY = threads_mod.ThreadsAdapter.DEBUG_TOKEN_KEY


def _debug_row(report):
    return next(p for p in report["probes"] if p["key"] == DEBUG_KEY)


def test_debug_tokenが取れれば3行が乗っているに格上げされる(monkeypatch, account):
    report, requests = _diagnose(monkeypatch, account)          # 既定: 11 個全部
    rows = _by_permission(report)
    for perm in UNVERIFIABLE:
        p = rows[perm][0]
        assert p["ok"] is True, p
        assert "debug_token" in p["detail"], p
        assert threads_mod.UNVERIFIABLE_BY_READ not in p["detail"], p
    d = _debug_row(report)
    assert d["ok"] is True and d["scopes"] == list(scopes_mod.DEFAULT_SCOPES)
    assert d["missing"] == [] and d["extra"] == []
    # 口は 1 回・自分のトークンを input_token にも渡す・GET。
    calls = [(m, q) for m, p, q in requests if p == "/v1.0/debug_token"]
    assert len(calls) == 1 and calls[0][0] == "GET"
    assert calls[0][1]["input_token"] == calls[0][1]["access_token"]


def test_debug_tokenに無い権限は乗っていませんになる(monkeypatch, account):
    report, _ = _diagnose(monkeypatch, account, {"/debug_token": "partial"})
    rows = _by_permission(report)
    assert rows["threads_manage_replies"][0]["ok"] is True
    for perm in ("threads_delete", "threads_share_to_instagram"):
        p = rows[perm][0]
        assert p["ok"] is False, p
        assert threads_mod.NOT_GRANTED in p["detail"], p
    d = _debug_row(report)
    assert d["missing"] == ["threads_delete", "threads_share_to_instagram"], d
    assert d["extra"] == []


def test_debug_tokenが取れなければ3行はNoneのまま(monkeypatch, account):
    for mode in ("absent", "5xx", "malformed"):
        report, _ = _diagnose(monkeypatch, account, {"/debug_token": mode})
        rows = _by_permission(report)
        for perm in UNVERIFIABLE:
            p = rows[perm][0]
            assert p["ok"] is None, (mode, p)
            assert threads_mod.UNVERIFIABLE_BY_READ in p["detail"], (mode, p)
        d = _debug_row(report)
        assert d["ok"] is not True and d["scopes"] is None, (mode, d)


def test_debug_tokenの行が記録上のscopeの直後に出る(monkeypatch, account):
    with _server() as (base_url, _requests):
        monkeypatch.setenv("THTH_THREADS_BASE_URL", base_url)
        lines: list = []
        rc = doctor_mod.run_doctor(account["name"], log=lines.append)
    assert rc == 0, "\n".join(lines)
    i = next(i for i, l in enumerate(lines) if l.startswith("記録上の scope"))
    assert lines[i + 1] == "debug_token の scope: 11 個（DEFAULT_SCOPES と一致）", lines[i + 1]
    with _server({"/debug_token": "partial"}) as (base_url, _requests):
        monkeypatch.setenv("THTH_THREADS_BASE_URL", base_url)
        lines = []
        rc = doctor_mod.run_doctor(account["name"], log=lines.append)
    assert rc == 1
    line = next(l for l in lines if l.startswith("debug_token の scope"))
    assert line == ("debug_token の scope: 9 個（不一致: "
                    "足りない=threads_delete,threads_share_to_instagram）"), line


def test_doctorはtokenを書き換えない(monkeypatch, account):
    """`.token` の scopes が null で debug_token が取れても、**読むだけ**。"""
    import os
    before = open(account["token_path"], "rb").read()
    mtime = os.stat(account["token_path"]).st_mtime_ns
    with _server() as (base_url, _requests):
        monkeypatch.setenv("THTH_THREADS_BASE_URL", base_url)
        doctor_mod.run_doctor(account["name"], log=lambda _l: None)
        doctor_mod.run_doctor(account["name"], as_json=True, log=lambda _l: None)
    assert open(account["token_path"], "rb").read() == before
    assert os.stat(account["token_path"]).st_mtime_ns == mtime
    assert json.loads(before)["scopes"] is None
