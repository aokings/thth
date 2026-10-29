"""Facebook ページの `thth auth`（`thth/adapters/auth_facebook.py`）・`doctor`・`token set`。

偽の Graph API（loopback）と、試験の中で作った偽の app id・secret だけを使う。
"""
from __future__ import annotations

import json
import secrets
import urllib.parse
from pathlib import Path

import pytest

from thth import accounts, admin_log, appenv, authflow, doctor, oauth
from thth.adapters import auth_facebook as afb
from thth.adapters import facebook as fb
from tests.test_facebook_adapter import fake_graph

PAGE = "61596075596464"
V = "/" + fb.GRAPH_VERSION


def opaque():
    return secrets.token_urlsafe(32)


@pytest.fixture
def env(tmp_path, monkeypatch):
    root = tmp_path / "root"; root.mkdir()
    home = tmp_path / "home"; home.mkdir()
    ledgers = tmp_path / "accounts"; ledgers.mkdir()
    for key, value in [("THTH_ROOT", root), ("HOME", home), ("XDG_CONFIG_HOME", home),
                       ("THTH_ACCOUNTS_DIR", ledgers)]:
        monkeypatch.setenv(key, str(value))
    data = dict(app_id=str(secrets.randbelow(10**15)), app_secret=opaque(), code=opaque(),
                short=opaque(), long=opaque(), page_token=opaque(), other_token=opaque(),
                config_id=None, behavior={}, auth_queries=[], lines=[])
    app = tmp_path / "app.env"
    data["app"] = app

    def write_app(*, config_id=None, meta=True):
        lines = ["THREADS_APP_ID=1", "THREADS_APP_SECRET=" + opaque()]
        if meta:
            lines += ["META_APP_ID=" + data["app_id"], "META_APP_SECRET=" + data["app_secret"]]
        if config_id:
            lines.append("META_LOGIN_CONFIG_ID=" + config_id)
        app.write_text("\n".join(lines) + "\n"); app.chmod(0o600)
    data["write_app"] = write_app
    write_app()
    monkeypatch.setenv("THTH_APP_ENV_PATH", str(app))
    cfg = json.loads((Path(__file__).parents[1] / "accounts.example/facebook.json").read_text())
    cfg.update(account="alpha", project="demo", handle=PAGE, page_id=PAGE,
               repo_dir=str(root / "repos/_none"), token=str(root / "alpha.token"),
               env=str(root / "alpha.env"))
    (ledgers / "alpha.json").write_text(json.dumps(cfg))
    data["cfg"] = accounts.load_account("alpha")

    def token_route(query, form, raw):
        if query.get("code") == [data["code"]] and query.get("client_secret") == [data["app_secret"]]:
            return 200, {"access_token": data["short"], "token_type": "bearer"}
        if (query.get("grant_type") == ["fb_exchange_token"]
                and query.get("fb_exchange_token") == [data["short"]]):
            return 200, {"access_token": data["long"], "token_type": "bearer", "expires_in": 5183944}
        return 400, {"error": {"message": "bad", "code": 100}}

    def accounts_route(query, form, raw):
        if query.get("access_token") != [data["long"]]:
            return 400, {"error": {"code": 190}}
        return data["behavior"].get("accounts", (200, {"data": [
            {"id": "111", "name": "別のページ", "access_token": data["other_token"], "tasks": ["MANAGE"]},
            {"id": PAGE, "name": "こぴちゃ", "access_token": data["page_token"],
             "tasks": ["ADVERTISE", "ANALYZE", "CREATE_CONTENT", "MESSAGING", "MODERATE", "MANAGE"]}]}))

    def debug_route(query, form, raw):
        assert query.get("access_token") == [data["app_id"] + "|" + data["app_secret"]]
        return data["behavior"].get("debug", (200, {"data": {
            "app_id": data["app_id"], "is_valid": True, "type": "PAGE", "profile_id": PAGE,
            "expires_at": 0, "data_access_expires_at": 1800000000, "scopes": list(fb.SCOPES)}}))

    def me_route(query, form, raw):
        token = (query.get("access_token") or [None])[0]
        if token == data["page_token"]:
            return data["behavior"].get("me", (200, {"id": PAGE, "name": "こぴちゃ"}))
        return 200, {"id": "111", "name": "別のページ"}

    routes = {("GET", V + "/oauth/access_token"): token_route,
              ("GET", V + "/me/accounts"): accounts_route,
              ("GET", V + "/debug_token"): debug_route,
              ("GET", V + "/me"): me_route}
    data["routes"] = routes
    with fake_graph(routes) as (url, calls):
        monkeypatch.setenv(fb.BASE_URL_ENV, url)
        data["calls"] = calls
        yield data


def run(env):
    def human(value):
        env["auth_queries"].append(value)

    def entered():
        session = authflow._read_session("alpha")
        env["session"] = session
        return afb.CALLBACK + "?" + urllib.parse.urlencode({"state": session["state"], "code": env["code"]}) + "#_=_"

    return oauth.run_auth("alpha", by="operator", input_func=entered, human_output=human,
                          log=env["lines"].append)


def saved(env):
    path = Path(env["cfg"]["token"])
    return json.loads(path.read_text()) if path.exists() else None


def secrets_of(env):
    return [env[k] for k in ("app_secret", "code", "short", "long", "page_token", "other_token")]


def test_構成が無ければscopeでページの鍵を取り権限と期限を記録する(env, capsys):
    assert run(env) == 0
    url = env["auth_queries"][0]
    assert url.startswith("https://www.facebook.com/" + fb.GRAPH_VERSION + "/dialog/oauth?")
    query = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
    assert query["client_id"] == [env["app_id"]] and query["redirect_uri"] == [afb.CALLBACK]
    assert query["response_type"] == ["code"] and query["state"] == [env["session"]["state"]]
    assert query["scope"] == [",".join(fb.SCOPES)] and "config_id" not in query
    token = saved(env)
    assert token["access_token"] == env["page_token"]
    assert token["user_id"] == PAGE and token["username"] == PAGE and token["page_name"] == "こぴちゃ"
    assert token["scopes"] == list(fb.SCOPES) and token["scopes_source"] == "response"
    assert token["no_expiry"] is True and "expires_in" not in token and token["expiry_source"] == "debug_token"
    assert token["login_mode"] == "scope" and "CREATE_CONTENT" in token["page_tasks"]
    assert token["data_access_expires_at"] == 1800000000 and token["auth_via"] == "paste"
    # **ユーザーの鍵は保存しない。**
    raw = Path(env["cfg"]["token"]).read_text()
    assert env["long"] not in raw and env["short"] not in raw and env["other_token"] not in raw
    rows, broken = admin_log.read()
    assert broken == 0 and rows[-1]["diff"]["auth_via"] == [None, "paste"]
    output = "\n".join(env["lines"]) + json.dumps(rows) + capsys.readouterr().out
    for value in secrets_of(env):
        assert value not in output


def test_構成のIDがあればconfig_idだけを送る(env):
    env["write_app"](config_id="998877665544")
    assert run(env) == 0
    query = urllib.parse.parse_qs(urllib.parse.urlsplit(env["auth_queries"][0]).query)
    assert query["config_id"] == ["998877665544"] and "scope" not in query
    assert saved(env)["login_mode"] == "config_id"


def test_ページが1つも無ければ保存しない(env):
    old = opaque(); Path(env["cfg"]["token"]).write_text(old)
    env["behavior"]["accounts"] = (200, {"data": []})
    assert run(env) == 2
    assert Path(env["cfg"]["token"]).read_text() == old
    assert any("facebook_no_pages" in line for line in env["lines"])


def test_台帳のページが許可に無ければ取り違えとして保存しない(env):
    env["behavior"]["accounts"] = (200, {"data": [
        {"id": "111", "name": "別のページ", "access_token": env["other_token"]}]})
    assert run(env) == 2
    assert saved(env) is None
    text = "\n".join(env["lines"])
    assert "facebook_page_not_granted" in text and "111" in text and env["other_token"] not in text


def test_ページの鍵が別のページを指していたら保存しない(env):
    env["behavior"]["me"] = (200, {"id": "111"})
    assert run(env) == 2 and saved(env) is None
    assert any("facebook_identity_mismatch" in line for line in env["lines"])


@pytest.mark.parametrize("change,reason", [({"profile_id": "111"}, "facebook_page_token_page_mismatch"),
                                           ({"app_id": "42"}, "facebook_page_token_app_mismatch"),
                                           ({"is_valid": False}, "facebook_page_token_invalid")])
def test_debug_tokenが食い違いを言えば保存しない(env, change, reason):
    data = {"app_id": env["app_id"], "is_valid": True, "profile_id": PAGE, "scopes": list(fb.SCOPES), **change}
    env["behavior"]["debug"] = (200, {"data": data})
    assert run(env) == 2 and saved(env) is None
    assert any(reason in line for line in env["lines"])


def test_debug_tokenに訊けなければ求めた一覧と文書の期限なしを記録する(env):
    env["behavior"]["debug"] = (500, {"error": {"code": 1}})
    assert run(env) == 0
    token = saved(env)
    assert token["scopes"] == list(fb.SCOPES) and token["scopes_source"] == "requested"
    assert token["no_expiry"] is True and token["expiry_source"] == "documented"


def test_構成のときにdebug_tokenに訊けなければ一覧は不明のまま(env):
    env["write_app"](config_id="998877665544")
    env["behavior"]["debug"] = (500, {"error": {"code": 1}})
    assert run(env) == 0
    token = saved(env)
    assert token["scopes"] is None and token["scopes_source"] == "unknown"


def test_期限が書いてあれば期限を採る(env):
    import time
    env["behavior"]["debug"] = (200, {"data": {"app_id": env["app_id"], "is_valid": True,
                                               "expires_at": int(time.time()) + 86400, "scopes": ["pages_show_list"]}})
    assert run(env) == 0
    token = saved(env)
    assert "no_expiry" not in token and 0 < token["expires_in"] <= 86400
    assert token["scopes"] == ["pages_show_list"]


def test_app_envにMETAの鍵が無ければ認可のURLを出さない(env):
    env["write_app"](meta=False)
    assert run(env) == 2
    assert env["auth_queries"] == [] and env["calls"] == []
    assert any("facebook_app_env_unavailable" in line and "META_APP_ID" in line for line in env["lines"])
    for line in env["lines"]:
        assert env["app_secret"] not in line


def test_台帳にpage_idが無ければ断る(env):
    path = Path(accounts.accounts_dir()) / "alpha.json"
    cfg = json.loads(path.read_text()); cfg.pop("page_id"); path.write_text(json.dumps(cfg))
    assert run(env) == 2 and env["auth_queries"] == []
    assert any("facebook_page_id_required" in line for line in env["lines"])


def test_戻り先は中継だけ(env):
    lines = []
    assert oauth.run_auth("alpha", by="operator", redirect_uri="https://example.com/cb",
                          log=lines.append, human_output=lambda _v: None) == 2
    assert any("facebook_callback_must_be_relay" in line for line in lines)


def test_宛先の検査を外れたら交換しない(env, monkeypatch):
    monkeypatch.setenv(fb.BASE_URL_ENV, "https://graph.facebook.com.evil.example")
    assert run(env) == 2 and saved(env) is None
    assert env["calls"] == []
    assert any("facebook_endpoint_invalid" in line for line in env["lines"]), env["lines"]


def test_認可の途中で構成を替えたら保存しない(env, monkeypatch):
    original = afb.graph_get

    def swap(path, params):
        if path == "/me/accounts":
            env["write_app"](config_id="111222333")
        return original(path, params)
    monkeypatch.setattr(afb, "graph_get", swap)
    assert run(env) == 2 and saved(env) is None
    assert any("auth_session_changed" in line for line in env["lines"]), env["lines"]


def test_app_setはMETAの行を残す(env):
    from thth import appconfig
    assert appconfig.run("threads", app_id="12345", input_func=lambda: "new-secret-value",
                         by="operator", log=lambda _l: None) == 0
    text = env["app"].read_text()
    assert "THREADS_APP_ID=12345" in text and "META_APP_ID=" + env["app_id"] in text
    assert appenv.load_meta_app_env()[0] == env["app_id"]


# --- doctor・token set --------------------------------------------------------

def write_token(env, **over):
    token = {"access_token": env["page_token"], "obtained_at": "2026-10-29T10:00:00+09:00",
             "user_id": PAGE, "username": PAGE, "scopes": list(fb.SCOPES), "scopes_source": "response",
             "no_expiry": True, "auth_via": "paste", **over}
    path = Path(env["cfg"]["token"]); path.write_text(json.dumps(token)); path.chmod(0o600)


def test_doctorはfacebookのprobeを並べる(env):
    env["routes"][("GET", V + "/debug_token")] = (200, {"data": {"scopes": list(fb.SCOPES), "expires_at": 0}})
    env["routes"][("GET", V + f"/{PAGE}/posts")] = (200, {"data": []})
    write_token(env)
    report = doctor.diagnose("alpha")
    assert not report.get("error"), report.get("error")
    keys = [p["key"] for p in report["probes"]]
    assert keys[:4] == ["whoami", fb.FacebookAdapter.DEBUG_TOKEN_KEY, fb.FacebookAdapter.DATA_ACCESS_KEY,
                        "posts"]
    # 応答に data_access_expires_at が無ければ「言えない」（－）で、× にしない。
    access = next(p for p in report["probes"] if p["key"] == fb.FacebookAdapter.DATA_ACCESS_KEY)
    assert access["ok"] is None and "data_access_expires_at" in access["detail"]
    assert report["scopes_recorded"]["count"] == len(fb.SCOPES)
    assert env["page_token"] not in json.dumps(report, ensure_ascii=False)


def test_doctorはトークンが無ければthth_authを勧める(env):
    report = doctor.diagnose("alpha")
    assert "thth auth" in report["error"]


def test_token_setはページの鍵を照合して期限なしで保存する(env):
    lines = []
    assert oauth.run_token_set("alpha", input_func=lambda: env["page_token"], by="operator",
                               log=lines.append) == 0
    token = saved(env)
    assert token["user_id"] == PAGE and token["no_expiry"] is True and "expires_in" not in token
    assert token["scopes_source"] == "unknown"
    assert all(env["page_token"] not in line for line in lines)


def test_token_setは別のページの鍵を保存しない(env):
    lines = []
    assert oauth.run_token_set("alpha", input_func=lambda: env["other_token"], by="operator",
                               log=lines.append) == 1
    assert saved(env) is None


def test_台帳とトークンからアダプタを組み立てる(env):
    from thth import adapters
    write_token(env)
    adapter = adapters.make_adapter(env["cfg"], accounts.load_token(env["cfg"]))
    assert isinstance(adapter, fb.FacebookAdapter) and adapter.page_id == PAGE
    assert adapter.granted_scopes == frozenset(fb.SCOPES)


def test_account_addはページのidを必須にしpage_idに写す(env, capsys):
    from thth import account_cli, cli
    data = account_cli.build_ledger("beta", media="facebook", project="demo", handle=PAGE)
    assert data["page_id"] == PAGE and data["handle"] == PAGE and data["media"] == "facebook"
    assert data["production"] is False and "redirect_uri" not in data
    assert accounts.dummy_fields(dict(data, handle="0", page_id="0"))[0]["field"] == "handle"
    assert cli.main(["account", "add", "gamma", "--media", "facebook", "--project", "demo",
                     "--handle", "kopicha", "--by", "operator"]) == 2
    assert "ページの id" in capsys.readouterr().err
    assert cli.main(["account", "add", "gamma", "--media", "facebook", "--project", "demo",
                     "--handle", PAGE, "--by", "operator"]) == 0
    written = accounts.load_account("gamma")
    assert written["page_id"] == PAGE and written["media"] == "facebook"
