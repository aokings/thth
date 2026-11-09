"""WhatsApp の最小の口（`thth/wa/`・`thth wa …`）。

見るのは:
  - `wa add` は production false・0600 で台帳を書き、ほかの種類の台帳と同じ名前を断る。
  - `wa auth` は WABA が鍵で見えるときだけ保存する（見えなければ保存しない）。
  - `wa numbers|templates` は id・名前・状態・言語・品質までで、本文（components）を出さない。
  - 書く口（template create・send）は二段確認・production・止めの印・1 日の上限・結果不明の扱い。
  - 送るのは承認済みのテンプレートだけ。自由文の引数は無い。
  - **送った相手の番号とテンプレート・メッセージの本文は、どのファイルにも残らない。**
  - 網の出入りは GET 1 か所・POST 1 か所で、書く関数を呼ぶのは cli.py だけ。
偽の Graph API（loopback）だけを使う。本物の Meta は叩かない。
"""
from __future__ import annotations

import json
import pathlib
import re
import secrets
import time
import urllib.parse

import pytest

from thth import appreview, cli, jst, scopes
from thth import permission_watch as pw
from thth import refusal_catalog
from thth.meta import graph as meta_graph
from thth.wa import auth as wa_auth
from thth.wa import graph as wa_graph
from thth.wa import ledger
from tests.test_facebook_adapter import fake_graph

V = "/" + meta_graph.GRAPH_VERSION
NAME = "m6-wa"
WABA = "1234567890"
NUMBER_ID = "106540352242922"
TO = "819012345678"
BODY = "ご予約の確認の目印本文です"
WA_DIR = pathlib.Path(__file__).resolve().parents[1] / "thth" / "wa"


def main(capsys, *argv):
    rc = cli.main(list(argv))
    out, err = capsys.readouterr()
    return rc, out, err


def two_steps(capsys, *argv):
    rc, out, err = main(capsys, *argv, "--json")
    assert rc == 1, err
    first = json.loads(out)
    assert first["stage"] == 1 and re.fullmatch(r"[0-9a-f]{12}", first["digest"])
    return main(capsys, *argv, "--confirm", first["digest"], "--json"), first


def posts(env):
    return [c for c in env["calls"] if c["method"] == "POST"]


def nothing_kept(env, *markers):
    for path in pathlib.Path(env["tmp"]).rglob("*"):
        if path.is_file() and path.name != "app.env":     # app.env は運営者が置いた設定（試験の道具）
            raw = path.read_bytes()
            for marker in markers:
                assert marker.encode() not in raw, f"{path} に {marker} が残っている"


@pytest.fixture
def env(tmp_path, monkeypatch):
    root = tmp_path / "root"; root.mkdir()
    home = tmp_path / "home"; home.mkdir()
    ledgers = tmp_path / "accounts"; ledgers.mkdir()
    for key, value in [("THTH_ROOT", root), ("HOME", home), ("XDG_CONFIG_HOME", home),
                       ("THTH_ACCOUNTS_DIR", ledgers)]:
        monkeypatch.setenv(key, str(value))
    data = dict(app_id=str(secrets.randbelow(10**15)), app_secret=secrets.token_urlsafe(32),
                code=secrets.token_urlsafe(32), short=secrets.token_urlsafe(32), long=secrets.token_urlsafe(32),
                tmp=tmp_path, root=root, lines=[], human=[], waba_visible=True,
                templates=[{"name": "hello_world", "status": "APPROVED", "language": "en_US", "category": "UTILITY",
                            "id": "1", "components": [{"type": "BODY", "text": "秘密の本文の目印"}]},
                           {"name": "pending_one", "status": "PENDING", "language": "ja", "category": "UTILITY",
                            "id": "2", "components": []}],
                send_reply=(200, {"messaging_product": "whatsapp", "contacts": [{"input": TO, "wa_id": TO}],
                                  "messages": [{"id": "wamid.X", "message_status": "accepted"}]}))
    app = tmp_path / "app.env"
    app.write_text("META_APP_ID=" + data["app_id"] + "\nMETA_APP_SECRET=" + data["app_secret"] + "\n")
    app.chmod(0o600)
    monkeypatch.setenv("THTH_APP_ENV_PATH", str(app))

    def token_route(query, form, raw):
        if query.get("code") == [data["code"]]:
            return 200, {"access_token": data["short"], "token_type": "bearer"}
        if query.get("fb_exchange_token") == [data["short"]]:
            return 200, {"access_token": data["long"], "token_type": "bearer", "expires_in": 5183944}
        return 400, {"error": {"message": "bad", "code": 100}}

    def waba_route(query, form, raw):
        if not data["waba_visible"]:
            return 400, {"error": {"message": "(#100) Unsupported get request", "code": 100}}
        return 200, {"id": WABA, "name": "目印の WABA", "currency": "JPY"}

    routes = {
        ("GET", V + "/oauth/access_token"): token_route,
        ("GET", V + "/debug_token"): lambda q, f, r: (200, {"data": {
            "app_id": data["app_id"], "is_valid": True, "type": "USER", "user_id": "777",
            "expires_at": int(time.time()) + 59 * 86400, "data_access_expires_at": int(time.time()) + 80 * 86400,
            "scopes": list(wa_auth.SCOPES)}}),
        ("GET", V + "/" + WABA): waba_route,
        ("GET", V + f"/{WABA}/phone_numbers"): (200, {"data": [
            {"id": NUMBER_ID, "display_phone_number": "+1 555-0100", "verified_name": "目印の店",
             "quality_rating": "GREEN"}]}),
        ("GET", V + f"/{WABA}/message_templates"): lambda q, f, r: (200, {"data": data["templates"]}),
        ("POST", V + f"/{WABA}/message_templates"): (200, {"id": "999", "status": "PENDING", "category": "UTILITY"}),
        ("POST", V + f"/{NUMBER_ID}/messages"): lambda q, f, r: data["send_reply"],
    }
    with fake_graph(routes) as (url, calls):
        monkeypatch.setenv(meta_graph.BASE_URL_ENV, url)
        data["calls"], data["routes"] = calls, routes
        yield data


def add(capsys, name=NAME):
    return main(capsys, "wa", "add", name, "--waba", WABA, "--project", "kopicha", "--by", "operator")


def ready(env, capsys, *, production=True, scopes_list=None):
    rc, _out, err = add(capsys)
    assert rc == 0, err
    cfg = ledger.load(NAME)
    ledger.write_token(cfg, {"access_token": "tok-" + secrets.token_hex(12), "obtained_at": jst.iso(),
                             "scopes": scopes_list if scopes_list is not None else list(wa_auth.SCOPES)})
    if production:
        path = pathlib.Path(ledger.path_for(NAME))
        value = json.loads(path.read_text())
        value["production"] = True
        path.write_text(json.dumps(value))
    return ledger.load(NAME)


def template_args(**over):
    base = {"--name": "order_ok", "--language": "ja", "--category": "UTILITY", "--body": BODY}
    base.update(over)
    return ["wa", "template", "create", NAME, *[x for kv in base.items() for x in kv], "--by", "m"]


def send_args(**over):
    base = {"--from": NUMBER_ID, "--to": TO, "--template": "hello_world", "--language": "en_US"}
    base.update(over)
    return ["wa", "send", NAME, *[x for kv in base.items() for x in kv], "--by", "m"]


# ================================================================== 台帳

def test_addはproductionがfalseで0600_ほかの種類と同じ名前は断る(env, capsys):
    rc, out, err = add(capsys)
    assert rc == 0, err
    path = pathlib.Path(ledger.path_for(NAME))
    value = json.loads(path.read_text())
    assert value["production"] is False and value["waba_id"] == WABA and value["send_daily_max"] == 10
    assert oct(path.stat().st_mode & 0o777) == "0o600"
    rc, _out, err = add(capsys)
    assert rc == 2 and "既にあります" in err
    (pathlib.Path(env["tmp"]) / "accounts" / "taken.json").write_text("{}")
    rc, _out, err = add(capsys, "taken")
    assert rc == 2 and "waba_name_taken" in err


def test_台帳の検査():
    with pytest.raises(ledger.WabaError):
        ledger.validate("x", {"waba": "x", "waba_id": "abc", "project": "p", "token": "t"})
    with pytest.raises(ledger.WabaError):
        ledger.validate("x", {"waba": "x", "waba_id": "1", "project": "p", "token": "t", "production": "yes"})
    ledger.validate("x", {"waba": "x", "waba_id": "1", "project": "p", "token": "t"})


# ================================================================== 認可

def _paste(name):
    def entered():
        session = wa_auth._read_session(name)
        return wa_auth.CALLBACK + "?" + urllib.parse.urlencode({"state": session["state"], "code": env_code[0]})
    return entered


env_code = [None]


def test_authはWABAが見えれば鍵を保存し_見えなければ保存しない(env, capsys):
    rc, _out, err = add(capsys)
    assert rc == 0, err
    env_code[0] = env["code"]
    env["waba_visible"] = False
    assert wa_auth.run_auth(NAME, by="operator", input_func=_paste(NAME), human_output=env["human"].append,
                            log=env["lines"].append) == 2
    assert ledger.load_token(ledger.load(NAME)) is None
    assert any("wa_waba_not_granted" in line for line in env["lines"])
    env["waba_visible"] = True
    assert wa_auth.run_auth(NAME, by="operator", input_func=_paste(NAME), human_output=env["human"].append,
                            log=env["lines"].append) == 0
    token = ledger.load_token(ledger.load(NAME))
    assert token["access_token"] == env["long"] and token["waba_id"] == WABA
    assert set(wa_auth.SCOPES) <= set(token["scopes"])
    url = env["human"][0]
    assert "scope=" in url and "whatsapp_business_management" in urllib.parse.unquote(url)
    nothing_kept(env, env["app_secret"], env["code"], env["short"])
    assert all(c["method"] == "GET" for c in env["calls"])


def test_waの認可の権限は外の人の招待の権限に足さない():
    for extra in scopes.META_INVITE_EXTRA.values():
        assert not {s for s in extra if s.startswith("whatsapp")}
    assert not {s for media in ("facebook", "instagram", "ads") for s in scopes.meta_invite_scopes(media, review=True)
                if s.startswith("whatsapp")}


# ================================================================== 読む

def test_numbersとtemplatesは本文を出さず_読むだけでPOSTは来ない(env, capsys):
    ready(env, capsys)
    rc, out, err = main(capsys, "wa", "numbers", NAME, "--json")
    assert rc == 0, err
    value = json.loads(out)
    assert value["numbers"] == [{"phone_number_id": NUMBER_ID, "verified_name": "目印の店", "quality_rating": "GREEN"}]
    assert "555-0100" not in out
    rc, out, err = main(capsys, "wa", "templates", NAME, "--json")
    assert rc == 0, err
    value = json.loads(out)
    assert [t["name"] for t in value["templates"]] == ["hello_world", "pending_one"]
    assert "秘密の本文の目印" not in out and "components" not in out
    assert posts(env) == []
    nothing_kept(env, "秘密の本文の目印", "目印の店")
    from thth.wa import ledger as wl
    found = pw._wa_row(NAME, jst.now_jst())["found"]
    assert {"wa_numbers", "wa_templates"} <= set(found)


def test_鍵が無ければ読めず_権限が乗っていなければ呼ぶ前に断る(env, capsys):
    rc, _out, err = add(capsys)
    assert rc == 0
    rc, _out, err = main(capsys, "wa", "numbers", NAME)
    assert rc == 2 and "meta_write_permission_missing" in err
    ready_cfg = ledger.load(NAME)
    ledger.write_token(ready_cfg, {"access_token": "tok-" + secrets.token_hex(8), "scopes": ["public_profile"]})
    rc, _out, err = main(capsys, "wa", "numbers", NAME)
    assert rc == 2 and "whatsapp_business_management" in err
    assert env["calls"] == []


# ================================================================== テンプレートを作る

def test_テンプレートは二段で作る_本文はJSONの_components(env, capsys):
    ready(env, capsys)
    (rc, out, err), first = two_steps(capsys, *template_args())
    assert rc == 0, err
    assert first["name"] == "order_ok" and first["category"] == "UTILITY"
    call = posts(env)[0]
    assert call["path"] == V + f"/{WABA}/message_templates"
    body = json.loads(call["raw"])
    assert body["name"] == "order_ok" and body["language"] == "ja" and body["category"] == "UTILITY"
    assert body["components"] == [{"type": "body", "text": BODY}]
    nothing_kept(env, BODY)
    assert "wa_template_create" in pw._wa_row(NAME, jst.now_jst())["found"]


def test_一段目は書かない_productionが無ければ二段目でも書かない(env, capsys):
    ready(env, capsys, production=False)
    rc, out, err = main(capsys, *template_args())
    assert rc == 1 and "digest:" in out and "production: true が無い" in out
    assert posts(env) == []
    digest = re.search(r"digest: ([0-9a-f]{12})", out).group(1)
    rc, _out, err = main(capsys, *template_args(), "--confirm", digest)
    assert rc == 1 and "production_disabled" in err
    assert posts(env) == []


def test_送るもproductionが無ければ二段目でも打たない(env, capsys):
    ready(env, capsys, production=False)
    rc, out, err = main(capsys, *send_args())
    assert rc == 1 and "production: true が無い" in out
    digest = re.search(r"digest: ([0-9a-f]{12})", out).group(1)
    rc, _out, err = main(capsys, *send_args(), "--confirm", digest)
    assert rc == 1 and "production_disabled" in err
    assert posts(env) == []


def test_変数入りの本文と同名のテンプレートは断る(env, capsys):
    ready(env, capsys)
    rc, _out, err = main(capsys, *template_args(**{"--body": "こんにちは {{1}} さん"}))
    assert rc == 2 and "wa_args_invalid" in err
    rc, _out, err = main(capsys, *template_args(**{"--name": "Bad Name"}))
    assert rc == 2 and "wa_args_invalid" in err
    rc, _out, err = main(capsys, *template_args(**{"--category": "ads"}))
    assert rc == 2 and "wa_args_invalid" in err
    rc, _out, err = main(capsys, *template_args(**{"--name": "hello_world", "--language": "en_US"}))
    assert rc == 2 and "同じ名前" in err
    assert posts(env) == []


# ================================================================== 送る

def test_送るのは承認済みのテンプレートだけ_二段で1通_本文は付けない(env, capsys):
    ready(env, capsys)
    (rc, out, err), first = two_steps(capsys, *send_args())
    assert rc == 0, err
    assert first["left_today"] == 10
    call = posts(env)[0]
    assert call["path"] == V + f"/{NUMBER_ID}/messages"
    body = json.loads(call["raw"])
    assert body["messaging_product"] == "whatsapp" and body["type"] == "template" and body["to"] == TO
    assert body["template"] == {"name": "hello_world", "language": {"code": "en_US"}}
    assert "components" not in body["template"] and "text" not in body
    assert TO not in out and "message_status" in out
    assert len(posts(env)) == 1


def test_相手の番号と本文はどのファイルにも残らない(env, capsys):
    ready(env, capsys)
    for argv in (template_args(), send_args()):
        (rc, _out, err), _first = two_steps(capsys, *argv)
        assert rc == 0, err
    rc, out, err = main(capsys, "wa", "templates", NAME)
    assert rc == 0
    nothing_kept(env, TO, BODY, "秘密の本文の目印", "wamid.X", "+" + TO)
    runs = list(pathlib.Path(ledger.state_dir_for(NAME)).glob("runs-*.ndjson"))
    assert runs
    for line in runs[0].read_text().splitlines():
        row = json.loads(line)
        assert row["medium"] == "whatsapp" and row["action"].startswith("wa_")
        assert set(row) <= {"account", "run_id", "mode", "action", "file", "post_id", "collected", "refreshed",
                            "quota", "status", "error", "topic", "mismatch_fields", "trigger", "messages",
                            "truncated", "words", "n", "medium", "author_key", "met", "profile_fetched",
                            "engagement_write_failed", "engagement_author_lookup_failed", "reply_to_file",
                            "resolved_from", "permission", "via", "credential", "event_type"}


def test_承認前のテンプレートと無いテンプレートと他人の番号は断る(env, capsys):
    ready(env, capsys)
    rc, _out, err = main(capsys, *send_args(**{"--template": "pending_one", "--language": "ja"}))
    assert rc == 1 and "wa_template_not_ready" in err and "PENDING" in err
    rc, _out, err = main(capsys, *send_args(**{"--template": "nope"}))
    assert rc == 1 and "wa_template_not_ready" in err
    rc, _out, err = main(capsys, *send_args(**{"--from": "999"}))
    assert rc == 2 and "wa_args_invalid" in err
    rc, _out, err = main(capsys, *send_args(**{"--to": "abc"}))
    assert rc == 2 and "wa_args_invalid" in err
    assert posts(env) == []


def test_自由文の引数は無い(env, capsys):
    ready(env, capsys)
    with pytest.raises(SystemExit):
        cli.main(["wa", "send", NAME, "--from", NUMBER_ID, "--to", TO, "--template", "hello_world",
                  "--language", "en_US", "--text", "自由文", "--by", "m"])
    capsys.readouterr()
    assert posts(env) == []


def test_止めの印があれば送らない_一段目は通る(env, capsys):
    ready(env, capsys)
    first = json.loads(main(capsys, *send_args(), "--json")[1])
    rc, _out, err = main(capsys, "wa", "stop", NAME, "--by", "m")
    assert rc == 0, err
    rc, _out, err = main(capsys, *send_args(), "--confirm", first["digest"])
    assert rc == 1 and "account_stopped" in err
    assert posts(env) == []
    rc, _out, err = main(capsys, "wa", "numbers", NAME)       # 読むは止まっていても通す
    assert rc == 0


def test_1日の上限を越えたら送らない(env, capsys):
    ready(env, capsys)
    path = pathlib.Path(ledger.path_for(NAME))
    value = json.loads(path.read_text())
    value["send_daily_max"] = 1
    path.write_text(json.dumps(value))
    (rc, _out, err), _ = two_steps(capsys, *send_args())
    assert rc == 0, err
    rc, _out, err = main(capsys, *send_args())
    assert rc == 1 and "wa_daily_limit" in err
    assert len(posts(env)) == 1


def test_結果が分からなければ済んだと言わず_打ち直さない(env, capsys):
    ready(env, capsys)
    env["send_reply"] = (200, {"messaging_product": "whatsapp"})     # メッセージの id が無い
    (rc, out, err), _ = two_steps(capsys, *send_args())
    assert rc == 1 and json.loads(out)["error"] == "meta_write_unknown"
    assert len(posts(env)) == 1
    rows = [json.loads(line) for line in next(pathlib.Path(ledger.state_dir_for(NAME)).glob("runs-*.ndjson"))
            .read_text().splitlines()]
    assert rows[-1]["status"] == "unknown"
    rc, _out, err = main(capsys, *send_args())          # 今日の通数に数える（unknown も）
    assert rc == 1 and "digest:" not in err


def test_Metaが断れば何も変わっていないと言う(env, capsys):
    ready(env, capsys)
    env["send_reply"] = (400, {"error": {"message": "(#131030) Recipient phone number not in allowed list",
                                         "code": 131030}})
    (rc, out, err), _ = two_steps(capsys, *send_args())
    value = json.loads(out)
    assert rc == 1 and value["error"] == "meta_write_refused" and "何も変わっていません" in value["message"]


def test_digestが変われば二段目は通らない(env, capsys):
    ready(env, capsys)
    first = json.loads(main(capsys, *send_args(), "--json")[1])
    env["templates"][0]["status"] = "PAUSED"
    rc, _out, err = main(capsys, *send_args(), "--confirm", first["digest"])
    assert rc == 1 and "wa_template_not_ready" in err
    env["templates"][0]["status"] = "APPROVED"
    rc, _out, err = main(capsys, *send_args(**{"--to": "819099999999"}), "--confirm", first["digest"])
    assert rc == 1 and "meta_write_digest_mismatch" in err
    assert posts(env) == []


# ================================================================== 柵

def test_網の出入りはGETとPOSTの各1か所_書く関数を呼ぶのはcliだけ():
    sources = {p.name: p.read_text() for p in WA_DIR.glob("*.py")}
    all_text = "\n".join(sources.values())
    import ast
    verbs = []
    for text in sources.values():
        for node in ast.walk(ast.parse(text)):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "request"
                    and node.args and isinstance(node.args[0], ast.Constant)):
                verbs.append(node.args[0].value)
    assert sorted(verbs) == ["GET", "POST"]
    assert not re.search(r"urlopen|requests\.|http\.client", re.sub(r"#.*", "", sources["graph.py"] + sources["cli.py"]
                                                                   + sources["ledger.py"]))
    for name, text in sources.items():
        for call in ("create_template(", "send_template("):
            if name not in ("cli.py", "graph.py"):
                assert call not in text
    assert "create_template(" in sources["cli.py"] and "send_template(" in sources["cli.py"]
    # 自由文の引数も本文を受ける口も無い（テンプレートの名前だけ）
    assert '"--text' not in sources["cli.py"] and "--text-file" not in sources["cli.py"]


def test_入口は手元のCLIだけ_MCPと遠くの道に出さない():
    root = pathlib.Path(__file__).resolve().parents[1] / "thth"
    for name in ("mcp_server.py", "remote.py"):
        path = root / name
        if path.exists():
            assert "thth wa" not in path.read_text() and "wa_send" not in path.read_text()


def test_権限は審査の表と見張りに移った():
    for name in ("whatsapp_business_management", "whatsapp_business_messaging"):
        assert name in pw.PERMISSIONS and name not in appreview.PLANNED
        row = next(item for item in appreview.items() if item["name"] == name)
        assert row["requested_by_thth"] and row["doors"] and row["segments"]
    assert {"thth wa numbers", "thth wa templates"} <= set(
        next(i for i in appreview.items() if i["name"] == "whatsapp_business_management")["doors"])
    assert next(i for i in appreview.items() if i["name"] == "whatsapp_business_messaging")["doors"] == ["thth wa numbers", "thth wa send"]


def test_断りの符丁は目録に3つそろう():
    for code in ("wa_args_invalid", "wa_template_not_ready", "wa_daily_limit"):
        found = refusal_catalog.entry(code) if hasattr(refusal_catalog, "entry") else None
        if found is not None:
            assert found["why"] and found["fix"] and found["better"]
