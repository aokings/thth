"""VM の私有の置き場（`$THTH_ROOT/state/_<名前>/<id>.json`）の共通の骨。

報告の口（3.1.2・`thth/report_inbox.py`）で作った骨を、施策の広場（3.4.0・
`thth/plaza.py`）でも使うために切り出した。**同じコードを 2 か所に写さない**
——片方だけ直して、もう片方が symlink を辿る・0644 で書く、という事故を作らない。

規律（報告の口と同じ）:

  (a) 置き場は祖先ごと O_NOFOLLOW で開く（`handoff_cursor._directory`）。
      ディレクトリは 0700、1 件は 0600。
  (b) 読むだけの口で置き場がまだ無ければ 0 件（`None`）。在るのに開けない
      （symlink・権限）ときだけ「置き場が読めない」。
  (c) 書き込みは一時ファイル → fsync → rename。件数の上限と重複の検査は、
      書き込みと同じ排他（`.lock` の flock）の中で行う。
  (d) 壊れた 1 件で一覧全体を止めない——数えて飛ばす。
  (e) 変更ログに書けなければ変更を戻す（記録の無い変更を残さない）。

断りは呼ぶ側の例外（静的な理由コードだけを持つ）で上げる。`error(reason)` は
その例外を作る関数、`unavailable`・`not_found`・`log_unavailable` は呼ぶ側の
理由コード。
"""
from __future__ import annotations

import fcntl
import json
import os
import stat
import uuid

from . import accounts, admin_log, handoff_cursor


class Store:
    """1 つの私有の置き場。`id_key` は 1 件の id の鍵（`report_id`・`plaza_id`）。"""

    def __init__(self, directory, *, id_key, id_pattern, valid, error,
                 unavailable, not_found, log_unavailable, max_bytes=1024 * 1024,
                 temporary_prefix=".record-"):
        self.directory = directory
        self.id_key = id_key
        self.id_pattern = id_pattern
        self.valid = valid
        self.error = error
        self.unavailable = unavailable
        self.not_found = not_found
        self.log_unavailable = log_unavailable
        self.max_bytes = max_bytes
        self.temporary_prefix = temporary_prefix

    # -------------------------------------------------------------- 置き場

    def open(self, create=False):
        """置き場を祖先ごと O_NOFOLLOW で開く（fd を返す）。無ければ `None`（読むだけの口）。"""
        if not create and not os.path.lexists(accounts.state_dir_for(self.directory)):
            return None
        try:
            return handoff_cursor._directory(self.directory, create=create)
        except FileNotFoundError:
            return None
        except (OSError, ValueError) as exc:
            raise self.error(self.unavailable) from exc

    def locked(self):
        return _Locked(self)

    # ---------------------------------------------------------------- 読む

    def read_one(self, directory, name):
        fd = os.open(name, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW, dir_fd=directory)
        with os.fdopen(fd, "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise ValueError("not_regular")
            data = stream.read(self.max_bytes + 1)
        if len(data) > self.max_bytes:
            raise ValueError("too_large")
        record = json.loads(data)
        if not self.valid(record) or record[self.id_key] + ".json" != name:
            raise ValueError("invalid_record")
        return record

    def load_all(self, directory):
        """置き場の全件（壊れた件数も返す）。`(at, id)` の順。"""
        records, broken = [], 0
        if directory is None:
            return records, broken
        try:
            names = sorted(os.listdir(directory))
        except OSError as exc:
            raise self.error(self.unavailable) from exc
        for name in names:
            if not name.endswith(".json") or not self.id_pattern.match(name[:-5]):
                continue
            try:
                records.append(self.read_one(directory, name))
            except (OSError, ValueError, TypeError, RecursionError):
                broken += 1
        records.sort(key=lambda row: (row["at"], row[self.id_key]))
        return records, broken

    def load(self):
        """読むだけの口（ロックは取らない）。置き場がまだ無ければ 0 件。"""
        directory = self.open()
        try:
            return self.load_all(directory)
        finally:
            if directory is not None:
                os.close(directory)

    def find(self, directory, record_id):
        if not isinstance(record_id, str) or not self.id_pattern.match(record_id):
            raise self.error(self.not_found)
        if directory is None:
            raise self.error(self.not_found)
        try:
            return self.read_one(directory, record_id + ".json")
        except FileNotFoundError:
            raise self.error(self.not_found) from None
        except (OSError, ValueError, TypeError, RecursionError):
            raise self.error(self.unavailable) from None

    def get(self, record_id):
        """1 件を読む（ロックは取らない）。無い id は `not_found`。"""
        directory = self.open()
        try:
            return self.find(directory, record_id)
        finally:
            if directory is not None:
                os.close(directory)

    # ---------------------------------------------------------------- 書く

    def write(self, directory, record, name=None):
        """一時ファイル（0600・O_EXCL・O_NOFOLLOW）→ fsync → rename。"""
        name = name or record[self.id_key] + ".json"
        data = json.dumps(record, ensure_ascii=False, allow_nan=False, indent=1).encode("utf-8")
        self.write_raw(directory, name, data)

    def write_raw(self, directory, name, data):
        """バイト列を 1 ファイルに（`write` と同じ一時ファイル → fsync → rename）。

        観測の地図（3.5.0）の月ごとの ndjson のように、1 件 1 JSON でない置き場も
        同じ骨で書くために切り出した。
        """
        temporary = self.temporary_prefix + uuid.uuid4().hex
        try:
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600,
                         dir_fd=directory)
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, name, src_dir_fd=directory, dst_dir_fd=directory)
            os.fsync(directory)
        except OSError as exc:
            raise self.error(self.unavailable) from exc
        finally:
            try:
                os.unlink(temporary, dir_fd=directory)
            except OSError:
                pass

    def read_raw(self, directory, name):
        """1 ファイルのバイト列（O_NOFOLLOW・通常のファイルだけ・上限つき）。無ければ `None`。"""
        try:
            fd = os.open(name, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW, dir_fd=directory)
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise self.error(self.unavailable) from exc
        try:
            with os.fdopen(fd, "rb") as stream:
                if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                    raise self.error(self.unavailable)
                data = stream.read(self.max_bytes + 1)
        except OSError as exc:
            raise self.error(self.unavailable) from exc
        if len(data) > self.max_bytes:
            raise self.error(self.unavailable)
        return data

    def remove_name(self, directory, name):
        """1 ファイルを消す（無ければ何もしない）。"""
        try:
            os.unlink(name, dir_fd=directory)
        except FileNotFoundError:
            pass
        except OSError as exc:
            raise self.error(self.unavailable) from exc

    def remove(self, directory, record_id):
        try:
            os.unlink(record_id + ".json", dir_fd=directory)
        except FileNotFoundError:
            pass
        except OSError as exc:
            raise self.error(self.unavailable) from exc

    def create(self, directory, record, log):
        """1 件を置いて `log()` を呼ぶ。ログに書けなければ置いた 1 件を戻す。"""
        self.write(directory, record)
        try:
            log(record)
        except LOG_ERRORS:
            try:
                os.unlink(record[self.id_key] + ".json", dir_fd=directory)
            except OSError:
                pass
            raise self.error(self.log_unavailable) from None

    def update(self, record_id, change, log):
        """1 件を書き換えて `log(record)` を呼ぶ。ログに書けなければ元に戻す。

        `change(record)` は record を書き換える（断るなら呼ぶ側の例外を上げる）。
        """
        with self.locked() as directory:
            record = self.find(directory, record_id)
            before = json.loads(json.dumps(record))
            change(record)
            self.write(directory, record)
            try:
                log(record)
            except LOG_ERRORS:
                self.write(directory, before)
                raise self.error(self.log_unavailable) from None
        return record


class _Locked:
    """置き場の排他（件数の上限と重複の検査を、書き込みと同じロックの中で行う）。"""

    def __init__(self, store):
        self.store = store

    def __enter__(self):
        self.directory = self.store.open(create=True)
        try:
            self.fd = os.open(".lock", os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600,
                              dir_fd=self.directory)
            fcntl.flock(self.fd, fcntl.LOCK_EX)
        except OSError as exc:
            os.close(self.directory)
            raise self.store.error(self.store.unavailable) from exc
        return self.directory

    def __exit__(self, *exc):
        try:
            os.close(self.fd)
        finally:
            os.close(self.directory)
        return False


# 変更ログ（`admin_log`）の失敗として扱う例外。
LOG_ERRORS = (OSError, ValueError, admin_log.AdminLogError)


# ------------------------------------------------------------------ 自由文

def check_text(value, limit, *, error, invalid, too_long, required=True, one_line=False):
    """自由文の検査。空・型違い・制御文字は `invalid`、長さは `too_long`。"""
    if value is None and not required:
        return None
    if not isinstance(value, str):
        raise error(invalid)
    value = value.replace("\r\n", "\n").strip()
    if not value:
        if required:
            raise error(invalid)
        return None
    if one_line and "\n" in value:
        raise error(invalid)
    if any(ord(c) < 32 and c not in "\n\t" for c in value) or "\x7f" in value:
        raise error(invalid)
    if len(value) > limit:
        raise error(too_long)
    return value


def fold_paths(value):
    """本文の中の `$THTH_ROOT` とホームの絶対パスを畳む（応答・書き出しに出さない）。"""
    if value is None:
        return None
    candidates = []
    for raw in (accounts.thth_root(), os.path.realpath(accounts.thth_root())):
        if raw and os.path.isabs(raw) and raw != os.sep:
            candidates.append((raw.rstrip(os.sep), "$THTH_ROOT"))
    home = os.path.expanduser("~")
    for raw in (home, os.path.realpath(home)):
        if raw and os.path.isabs(raw) and raw != os.sep:
            candidates.append((raw.rstrip(os.sep), "~"))
    # 長い方から（ホームの下に root があると、先にホームを畳むと root が残る）。
    for raw, mark in sorted(set(candidates), key=lambda item: len(item[0]), reverse=True):
        value = value.replace(raw, mark)
    return value


def read_input(path, limit, *, error, invalid):
    """`--body-file` 等を読む（`-` は標準入力）。読めなければ `invalid`。"""
    import sys
    if path is None:
        return None
    try:
        if path == "-":
            return sys.stdin.read(limit * 4 + 1)
        with open(path, encoding="utf-8") as stream:
            return stream.read(limit * 4 + 1)
    except (OSError, UnicodeError):
        raise error(invalid) from None
