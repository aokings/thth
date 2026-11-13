"""封の鍵と署名（設計 `docs/設計_台帳の器_2026-11-13.md` §5.6「CI と承認」・§9 の 5・§10 の段 3）。

- **鍵は持ち主に 1 つ**（Ed25519）。秘密の鍵は THTH の側に置く: `<XDG_CONFIG_HOME か ~/.config>/thth/register_seal.pem`
  （0600・置き場のフォルダは 0700）。**台帳にも repo にも置かない**（台帳のフォルダの中・台帳を含む git の repo の中に
  あれば使わずに断る）。鍵の値はどこにも出さない（出すのは公開鍵と、その指紋だけ）。
- 署名と確かめは openssl（3 か 4）。今の THTH が relay の署名に使っているのと同じ道具で、Python の部品は増えない。
  決まった場所の openssl だけを使い、PATH もシェルも使わない（`relay.openssl` と同じ決め方）。網には出ない。
- **確かめに使う公開鍵は、repo の木の外からだけ取る**: 環境の変数 `THTH_REGISTER_SEAL_PUBLIC_KEY`（CI の変数・PEM か
  DER の base64）か、手元の秘密の鍵から作った公開鍵。repo の中の鍵のファイルは読まない（設計 §5.6・試験 3 (f)）。

`subprocess` を使うのは、この部品と `draft.py` だけ（`tests/test_register_base.py` の `SUBPROCESS_ALLOWED`）。
"""
from __future__ import annotations

import base64
import hashlib
import os
import re
import subprocess
import tempfile

from .errors import RegisterRefused

ENV_PUBLIC = "THTH_REGISTER_SEAL_PUBLIC_KEY"
KEY_NAME = "register_seal.pem"
BINARIES = ("/opt/homebrew/bin/openssl", "/usr/bin/openssl", "/usr/local/bin/openssl")
_ENV = {"PATH": "/usr/bin:/bin", "OPENSSL_CONF": "/dev/null"}


def key_dir() -> str:
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, "thth")


def key_path() -> str:
    return os.path.join(key_dir(), KEY_NAME)


def openssl() -> str:
    """決まった場所の OpenSSL 3・4（LibreSSL は Ed25519 の生の署名を持たないので使わない）。"""
    for binary in BINARIES:
        if not os.path.isfile(binary):
            continue
        try:
            result = subprocess.run([binary, "version"], capture_output=True, timeout=5, env=_ENV)
        except (OSError, subprocess.SubprocessError):
            continue
        if result.returncode == 0 and re.match(rb"OpenSSL [34]\.", result.stdout):
            return binary
    raise RegisterRefused("register_seal_key_missing", "openssl")


def _run(arguments, *, data: bytes = b"") -> subprocess.CompletedProcess:
    try:
        return subprocess.run([openssl(), *arguments], input=data, capture_output=True, timeout=60, env=_ENV)
    except (OSError, subprocess.SubprocessError):
        raise RegisterRefused("register_seal_key_missing", "openssl") from None


def _git_top(path: str) -> str | None:
    """`path` を含む git の作業木の根（`.git` のある最も近い上のフォルダ）。無ければ None。"""
    here = os.path.realpath(path)
    while True:
        if os.path.exists(os.path.join(here, ".git")):
            return here
        parent = os.path.dirname(here)
        if parent == here:
            return None
        here = parent


def _inside(path: str, directory: str) -> bool:
    path, directory = os.path.realpath(path), os.path.realpath(directory)
    try:
        return os.path.commonpath([path, directory]) == directory
    except ValueError:
        return False


def check_key_place(register_root: str | None) -> None:
    """秘密の鍵の置き場が、台帳のフォルダの中か、台帳を含む git の repo の中なら断る。"""
    path = key_path()
    if register_root is None:
        return
    top = _git_top(register_root)
    if _inside(path, register_root) or (top is not None and _inside(path, top)):
        raise RegisterRefused("register_seal_key_unsafe", "inside_register")


def _private_ok(path: str) -> None:
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        raise RegisterRefused("register_seal_key_missing", "key") from None
    if os.path.islink(path) or not os.path.isfile(path) or (info.st_mode & 0o077) or info.st_size > 4096:
        raise RegisterRefused("register_seal_key_unsafe", "key_mode")


def init_key() -> dict:
    """秘密の鍵を作る（あれば作り直さない・黙って替えない）。返すのは公開鍵と指紋だけ。"""
    path = key_path()
    if os.path.lexists(path):
        raise RegisterRefused("register_seal_key_unsafe", "key_exists")
    os.makedirs(key_dir(), mode=0o700, exist_ok=True)
    result = _run(["genpkey", "-algorithm", "ed25519"])
    if result.returncode or b"PRIVATE KEY" not in result.stdout:
        raise RegisterRefused("register_seal_key_missing", "openssl")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(result.stdout)
        stream.flush()
        os.fsync(stream.fileno())
    return public_info()


def local_public_pem() -> str:
    """手元の秘密の鍵から作った公開鍵（PEM）。"""
    path = key_path()
    _private_ok(path)
    result = _run(["pkey", "-in", path, "-pubout"])
    if result.returncode or b"PUBLIC KEY" not in result.stdout:
        raise RegisterRefused("register_seal_key_missing", "key")
    return result.stdout.decode("ascii")


def _der_of(pem: str) -> bytes:
    body = "".join(line for line in pem.strip().splitlines() if not line.startswith("-----"))
    try:
        return base64.b64decode(body, validate=True)
    except ValueError:
        raise RegisterRefused("register_seal_key_missing", "public_key_form") from None


def pem_of(text: str) -> str:
    """環境の変数の値（PEM か、DER の base64 の 1 行）を PEM にそろえる。"""
    text = (text or "").strip()
    if text.startswith("-----BEGIN PUBLIC KEY-----"):
        _der_of(text)
        return text + "\n"
    if not re.fullmatch(r"[A-Za-z0-9+/=]{40,200}", text):
        raise RegisterRefused("register_seal_key_missing", "public_key_form")
    lines = [text[i:i + 64] for i in range(0, len(text), 64)]
    return "-----BEGIN PUBLIC KEY-----\n" + "\n".join(lines) + "\n-----END PUBLIC KEY-----\n"


def fingerprint(pem: str) -> str:
    """公開鍵の指紋（DER の sha256）。封に書き、どの鍵で封をしたかを人が見分けるため（確かめには使わない）。"""
    return hashlib.sha256(_der_of(pem)).hexdigest()


def public_info() -> dict:
    pem = local_public_pem()
    return {"public_key": pem, "public_key_base64": base64.b64encode(_der_of(pem)).decode("ascii"),
            "fingerprint": fingerprint(pem)}


def trusted_public_pem() -> tuple:
    """確かめに使う公開鍵と、その出どころ（`env` か `local`）。repo の中のファイルは読まない。"""
    raw = os.environ.get(ENV_PUBLIC)
    if raw:
        return pem_of(raw), "env"
    if os.path.exists(key_path()):
        return local_public_pem(), "local"
    raise RegisterRefused("register_seal_key_missing", "public_key")


def sign(data: bytes, *, register_root: str | None) -> str:
    """`data` の Ed25519 の署名（base64）。秘密の鍵の置き場を先に確かめる。"""
    check_key_place(register_root)
    path = key_path()
    _private_ok(path)
    with tempfile.TemporaryDirectory(prefix="thth-seal-") as tmp:
        message = os.path.join(tmp, "m")
        with open(message, "wb") as f:
            f.write(data)
        result = _run(["pkeyutl", "-sign", "-inkey", path, "-rawin", "-in", message])
    if result.returncode or not result.stdout:
        raise RegisterRefused("register_seal_key_missing", "sign")
    return base64.b64encode(result.stdout).decode("ascii")


def verify(data: bytes, signature: str, public_pem: str) -> bool:
    try:
        raw = base64.b64decode(signature or "", validate=True)
    except ValueError:
        return False
    if not raw:
        return False
    with tempfile.TemporaryDirectory(prefix="thth-seal-") as tmp:
        paths = {}
        for name, value in (("m", data), ("s", raw), ("p", public_pem.encode("ascii"))):
            paths[name] = os.path.join(tmp, name)
            with open(paths[name], "wb") as f:
                f.write(value)
        result = _run(["pkeyutl", "-verify", "-pubin", "-inkey", paths["p"], "-rawin", "-in", paths["m"],
                       "-sigfile", paths["s"]])
    return result.returncode == 0
