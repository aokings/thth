"""Instagram のストーリーを `thth send --story` で出す口（11/24・`thth/story.py`）。

11/24 本物: Threads の `--share-to-instagram` は SUCCESS でもストーリーが作られないことがあり、IG の口座から直接
（`post_options: {story: true}`）なら出た。ここは旗・断り・指紋を縛る。本物の Graph API は叩かない。
"""
from __future__ import annotations

import json

import pytest

from thth import approval, cli, core, read_coordination, refusal
from thth import story as story_mod

IG_CFG = {"media": "instagram", "handle": "kopicha_test"}


class _R:
    exit_code, error, action, mode, post_id, url, digest = 0, None, "dry_run", "rehearsal", None, None, "d"
    message = next_at = reason = publish_at = file = None


@pytest.fixture
def sent(monkeypatch):
    """`core.send_once` に渡った引数（呼ばれなければ空）。台帳は試験の中の IG の口座。"""
    seen = {}

    def fake(account, **kw):
        seen.update(kw)
        return _R()
    monkeypatch.setattr(core, "send_once", fake)
    monkeypatch.setattr(read_coordination, "invoke", lambda args, command, call=None: call() if call else args.func(args))
    monkeypatch.setattr(cli.accounts_mod, "load_account", lambda name: dict(IG_CFG))
    return seen


def test_storyの旗はpost_optionsのstoryとして渡る(sent):
    assert cli.main(["send", "kopicha-ig", "--story", "--media", "a.jpg", "--alt", "赤い点"]) == 0
    assert sent["post_options"] == {"story": True}
    assert sent["media_rows"] == [{"file": "a.jpg", "alt": "赤い点"}]
    sent.clear()
    assert cli.main(["send", "kopicha-ig", "--media", "a.jpg", "--alt", "赤い点"]) == 0
    assert "post_options" not in sent


@pytest.mark.parametrize("extra,detail", [
    (["--media", "a.jpg", "--alt", "a", "--reply-to", "17900000000000001"], "reply"),
    (["--media", "a.jpg", "--alt", "a", "--paid-partnership"], "paid_partnership"),
    (["--media", "a.jpg", "--alt", "a", "--paid-partnership", "--sponsor", "17841400000000001"], "paid_partnership"),
    (["--media", "a.jpg", "--alt", "a", "--topic", "話題"], "other_flags"),
    (["--media", "a.jpg", "--alt", "a", "--media", "b.jpg", "--alt", "b"], "media_count"),
    (["--media", "a.jpg", "--alt", "a", "--text", "本文"], "text_and_photo"),
    (["--text", " "], "empty"),
])
def test_storyの組み合わせの誤りは3つで断り_送らない(sent, capsys, extra, detail):
    assert cli.main(["send", "kopicha-ig", "--story", *extra]) == 2
    assert sent == {}
    err = capsys.readouterr().err
    first = err.splitlines()[0]
    assert first.startswith("story_unavailable: " + story_mod.LINES[detail].split("{")[0])
    for head in (refusal.WHY, refusal.FIX, refusal.BETTER):
        assert any(line.startswith(head) for line in err.splitlines()), (detail, head, err)


def test_Instagramでない口座のstoryは断る(sent, monkeypatch, capsys):
    monkeypatch.setattr(cli.accounts_mod, "load_account", lambda name: {"media": "threads"})
    assert cli.main(["send", "kopicha", "--story", "--media", "a.jpg", "--alt", "a"]) == 2
    assert sent == {}
    err = capsys.readouterr().err
    assert "Instagram の口座だけです（この口座は threads）" in err
    assert "Threads の投稿を見せたいときも" in err   # 詳細の語ごとの もっと良く


def test_storyの断りはjsonにも3つと詳細の語が載る(sent, capsys):
    assert cli.main(["send", "kopicha-ig", "--story", "--json", "--media", "a.jpg", "--alt", "a",
                     "--reply-to", "17900000000000001"]) == 2
    body = json.loads(capsys.readouterr().out)
    assert body["error"] == "story_unavailable" and body["reason"] == "reply"
    assert body["why"] and body["fix"].startswith("--reply-to を外して") and body["better"]


def test_storyは承認の指紋に入る():
    """ストーリーとフィードで digest が違う（ストーリーのつもりの承認でフィードには出ない）。"""
    row = {"file": "a.jpg", "alt": "赤い点", "source_sha256": "0" * 64, "size": 100, "role": "media",
           "index": 1, "public_sha256": "1" * 64, "public_size": 100, "format": "jpeg", "kind": "image",
           "width": 1080, "height": 1920, "duration": None, "orientation": 1}
    feed = {"files": [dict(row)], "attachments": [], "post_options": {}, "captions": []}
    story = {"files": [dict(row)], "attachments": [], "post_options": {"story": True}, "captions": []}
    args = dict(text="", account="kopicha-ig", reply_to=None, topic=None)
    assert approval.compute_send_digest(**args, media_manifest=feed) != \
        approval.compute_send_digest(**args, media_manifest=story)


# ================================================================== 縦横の比・本文のカード（Pillow のある機械だけ）

import ast
import pathlib

from thth import media
from thth import story_image

# publish の箱（PyYAML も Pillow も無い最小の環境）では飛ばす。印の形は yaml_needed と同じ。
image_needed = pytest.mark.skipif(not story_image.available(), reason="Pillow が無い機械（pip install 'thth[image]'）")
font_needed = pytest.mark.skipif(not (story_image.available() and story_image.find_font()),
                                 reason="Pillow か日本語の書体が無い機械")


def _photo(repo, name, size, color=(200, 120, 60)):
    from PIL import Image
    Image.new("RGB", size, color).save(repo / name, format="JPEG", quality=90)


def _cfg(repo):
    return {"media": "instagram", "repo_dir": str(repo), "handle": "kopicha_test"}


@image_needed
def test_4対5の写真は切れる側を言い_余白を足した1080x1920の絵に替える(tmp_path):
    _photo(tmp_path, "a.jpg", (1080, 1350))
    chosen, notes = story_mod.fit_photo(_cfg(tmp_path), {"file": "a.jpg", "alt": "赤い点"})
    assert notes[0] == "ストーリーは 9:16（1080×1920）で、この写真は 1080×1350 なので左右が切れます"
    assert notes[1].startswith("上下に余白を足して 1080×1920 にした絵を出します: thth-story/")
    assert chosen["file"].startswith("thth-story/") and chosen["alt"] == "赤い点"
    row = media.manifest_for({"media": [chosen], "post_options": {"story": True}}, _cfg(tmp_path))["files"][0]
    assert (row["width"], row["height"], row["format"]) == (1080, 1920, "jpeg")
    # 同じ写真なら同じ名前の同じ絵（一段目と二段目で digest が変わらない）。
    again, _ = story_mod.fit_photo(_cfg(tmp_path), {"file": "a.jpg", "alt": "赤い点"})
    assert again == chosen
    # 置き場は自分を git から外す（口座の repo の .gitignore は触らない）。
    assert (tmp_path / "thth-story" / ".gitignore").read_text(encoding="utf-8").splitlines()[-1] == "*"


@image_needed
def test_縦長の写真は左右に余白を足す_9対16はそのまま(tmp_path):
    _photo(tmp_path, "tall.jpg", (900, 2000))
    chosen, notes = story_mod.fit_photo(_cfg(tmp_path), {"file": "tall.jpg", "alt": "縦"})
    assert "上下が切れます" in notes[0] and notes[1].startswith("左右に余白を足して")
    _photo(tmp_path, "ok.jpg", (1080, 1920))
    assert story_mod.fit_photo(_cfg(tmp_path), {"file": "ok.jpg", "alt": "ちょうど"}) == \
        ({"file": "ok.jpg", "alt": "ちょうど"}, [])


def test_絵を作る部品が無ければ写真は切れる側を言うだけ(tmp_path, monkeypatch):
    """Pillow の無い機械（VM の今・publish の箱）。写真は替えずに、切れることと直し方を言う。"""
    row = {"kind": "image", "format": "jpeg", "width": 1080, "height": 1350, "orientation": 1}
    monkeypatch.setattr(media, "manifest_for", lambda fm, cfg: {"files": [row]})
    monkeypatch.setattr(story_image, "available", lambda: False)
    chosen, notes = story_mod.fit_photo(_cfg(tmp_path), {"file": "a.jpg", "alt": "赤い点"})
    assert chosen == {"file": "a.jpg", "alt": "赤い点"}
    assert notes[0].endswith("この写真は 1080×1350 なので左右が切れます")
    assert "Pillow" in notes[1] and "9:16 の写真に替えると" in notes[1]
    assert not (tmp_path / "thth-story").exists()


@font_needed
def test_本文だけのストーリーは1080x1920のカードの絵になる(tmp_path):
    chosen, notes = story_mod.make_card(_cfg(tmp_path), "kopicha-ig", "きょうの一杯。\n新しい豆です")
    assert chosen["alt"] == "本文のカード: きょうの一杯。"
    assert notes[0].startswith("本文を 1080×1920 のカードの絵にして出します: thth-story/")
    assert "@kopicha_test" in notes[0]
    row = media.manifest_for({"media": [chosen], "post_options": {"story": True}}, _cfg(tmp_path))["files"][0]
    assert (row["width"], row["height"], row["format"]) == (1080, 1920, "jpeg")
    assert story_mod.make_card(_cfg(tmp_path), "kopicha-ig", "きょうの一杯。\n新しい豆です")[0] == chosen
    # 字が描かれている（真っ白の札でない）: 札の中ほどに地と違う暗い点がある。
    from PIL import Image
    with Image.open(tmp_path / chosen["file"]) as image:
        assert min(image.convert("L").getdata()) < 80


@font_needed
def test_長すぎる本文のカードは断る(tmp_path):
    with pytest.raises(story_mod.CardRefused) as caught:
        story_mod.make_card(_cfg(tmp_path), "kopicha-ig", "あ" * 500)
    assert caught.value.detail == "too_long" and "500 字" in caught.value.line
    with pytest.raises(story_mod.CardRefused) as caught:
        story_mod.make_card(_cfg(tmp_path), "kopicha-ig", "あ" * (story_image.MAX_CHARS + 1))
    assert caught.value.detail == "too_long"


def test_部品か書体が無ければカードは3つで断り_送らない(sent, monkeypatch, capsys):
    monkeypatch.setattr(story_image, "available", lambda: False)
    assert cli.main(["send", "kopicha-ig", "--story", "--text", "本文"]) == 2
    assert sent == {}
    err = capsys.readouterr().err
    assert err.splitlines()[0].startswith("story_card_unavailable: この機械には絵を作る部品（Pillow）が無い")
    assert "pip install 'thth[image]'" in err and any(l.startswith(refusal.BETTER) for l in err.splitlines())
    monkeypatch.setattr(story_image, "available", lambda: True)
    monkeypatch.setattr(story_image, "find_font", lambda: None)
    assert cli.main(["send", "kopicha-ig", "--story", "--text", "本文"]) == 2
    err = capsys.readouterr().err
    assert "日本語の字の書体が見つからない" in err and "fonts-noto-cjk" in err


@font_needed
def test_本文のストーリーはカードの絵を添付にして送り_本文は記録に残る(sent, monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(cli.accounts_mod, "load_account", lambda name: _cfg(tmp_path))
    assert cli.main(["send", "kopicha-ig", "--story", "--text", "きょうの一杯"]) == 0
    assert sent["post_options"] == {"story": True}
    assert sent["text"] == "きょうの一杯"
    assert sent["media_rows"][0]["file"].startswith("thth-story/")
    assert "カードの絵にして出します" in capsys.readouterr().out


def test_折り返しは禁則を守り_単語の途中で折らない():
    measure = lambda s: float(len(s))   # 1 字 1 の幅
    assert story_image.wrap("あいうえお、かきく", 5, measure) == ["あいうえお、", "かきく"]      # 行頭に「、」を置かない
    assert story_image.wrap("あいう「えお」", 4, measure) == ["あいう", "「えお」"]          # 行末に「「」を置かない
    assert story_image.wrap("ab Instagram", 6, measure) == ["ab", "Instag", "ram"]            # 長すぎる単語だけ割る
    assert story_image.wrap("abc def", 5, measure) == ["abc", "def"]
    assert story_image.wrap("一\n\n二", 5, measure) == ["一", "", "二"]                       # 改行と空行はそのまま
    assert story_image.clean_text("a\r\nb\x07\tc") == "a\nb c"


PILLOW_HOME = "story_image.py"


def pillow_imports(directory: pathlib.Path) -> list[str]:
    """`directory` の下の .py で PIL を import している所（許すのは story_image.py の 1 枚だけ）。"""
    wrong = []
    for path in sorted(directory.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names = ([alias.name for alias in node.names] if isinstance(node, ast.Import)
                     else [node.module or ""] if isinstance(node, ast.ImportFrom) else [])
            if any(name.split(".")[0] == "PIL" for name in names) and \
                    str(path.relative_to(directory)) != PILLOW_HOME:
                wrong.append(str(path.relative_to(directory)))
    return wrong


def test_柵_Pillowのimportはstory_imageの1枚だけ():
    assert pillow_imports(pathlib.Path(__file__).resolve().parent.parent / "thth") == []


def test_壊れた例_Pillowの柵はほかのfileのimportを拾う(tmp_path):
    (tmp_path / "story_image.py").write_text("from PIL import Image\n", encoding="utf-8")
    (tmp_path / "other.py").write_text("import PIL.Image\n", encoding="utf-8")
    assert pillow_imports(tmp_path) == ["other.py"]


def test_カードの名札に数字だけのhandleを使わない():
    from thth.story import _label
    assert _label({"handle": "17841437318552220", "project": "kopicha"}, "kopicha-ig") == "kopicha"
    assert _label({"handle": "17841437318552220", "username": "kopi_chaba"}, "kopicha-ig") == "@kopi_chaba"
    assert _label({"handle": "@kopicha_test"}, "kopicha-ig") == "@kopicha_test"
    assert _label({}, "kopicha-ig") == "kopicha-ig"
