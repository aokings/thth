"""4.0 S3 §5・§9: 変換（画像の断りを外す・囲みの中を数えない・送る直前の置き換え）・指紋は S1 のまま・
形式と容量の断り（T-S3-3a〜3e）。はてなには何も送らない。
"""
from __future__ import annotations

import pytest

from tests.hatena_s1_flow_helpers import ACCOUNT, FakeAdapter, approve, call, fm_of, lane  # noqa: F401
from tests.hatena_s1_helpers import world  # noqa: F401
from tests.hatena_s2_helpers import lint_codes, stage1, throw
from tests.hatena_s3_helpers import GIF, JPEG, PNG, SVG, WEBP
from thth import approval, fingerprint_golden, hatena_convert, hatena_entry
from thth.hatena_convert import ConvertError, embed_images, render

PLACES = {"articles/images/a.jpg": {"syntax": "f:id:lane-owner:20261020075901j:image",
                                    "image_url": "https://cdn-ak.f.st-hatena.com/images/fotolife/l/lane-owner/20261020/20261020075901.jpg"},
          "images/b.png": {"syntax": "f:id:lane-owner:20261020075902p:image",
                           "image_url": "https://cdn.example/b.png"}}


# ---------------------------------------------------------------- T-S3-3a

def test_T_S3_3a_画像の断りは外れ_手元の画像はpathとsha256とbytesで並び_httpsは数えない():
    assert hatena_convert.REFUSE_LOCAL_IMAGES is False
    shas = {"articles/images/a.jpg": ("a" * 64, 10), "images/b.png": ("b" * 64, 20)}
    body, images = render("![a](images/a.jpg)\n\n![b](../images/b.png)\n\n![外](https://i.example/x.png)\n",
                          article_source="articles/x.md", image_sha=lambda p: shas[p])
    assert images == [{"path": "articles/images/a.jpg", "source_sha256": "a" * 64, "bytes": 10},
                      {"path": "images/b.png", "source_sha256": "b" * 64, "bytes": 20}]
    assert body == "![a](images/a.jpg)\n\n![b](../images/b.png)\n\n![外](https://i.example/x.png)"


def test_T_S3_3a_画像つきの記事がlint_1段目_timerを通る(lane):  # noqa: F811
    path = lane.queue()
    assert lint_codes(path) == []
    rc, out = stage1(path)
    assert "digest: " in out and "images/bean.png（21 bytes・sha256 " in out
    approve(path)
    adapter = FakeAdapter()
    assert throw(adapter).action == "post"


# ---------------------------------------------------------------- T-S3-3b

def test_T_S3_3b_承認の指紋はS1のまま():
    """柵: 指紋に fotolife_id（配置）を混ぜると golden がずれて赤。"""
    assert fingerprint_golden.hatena_drift() == []
    assert approval.FINGERPRINT_VERSION == 2
    assert approval.HATENA_ENTRY_VERSION == "thth-hatena-entry-1"
    assert hatena_convert.VERSION == "s2-rules-1"


def test_T_S3_3b_画像の台帳の状態で指紋は変わらない(lane):  # noqa: F811
    """上げる前と上げたあとで、同じ記事と画像の承認の指紋は同じ（timer の照合が approval_stale にならない）。"""
    path = lane.queue()
    before = hatena_entry.material(fm_of(path), _cfg(lane), ACCOUNT)["approved_sha"]
    approve(path)
    assert throw(FakeAdapter()).action == "post"   # 偽物のフォトライフに上がり台帳ができる
    from thth import hatena_fotolife
    assert hatena_fotolife.list_records("hatena-main")[0]
    path2 = lane.queue("b.md")
    after = hatena_entry.material(fm_of(path2), _cfg(lane), ACCOUNT)["approved_sha"]
    assert before == after


def _cfg(lane):  # noqa: F811
    from thth import accounts
    return accounts.load_account(ACCOUNT)


# ---------------------------------------------------------------- T-S3-3c

@pytest.mark.parametrize("ref", ["![豆](images/a.jpg)", "![豆](<images/a.jpg>)", '![豆](images/a.jpg "題")',
                                 "![豆](./images/a.jpg)"])
def test_T_S3_3c_参照の形によらずフォトライフ記法に置き換わる(ref):
    body = f"前\n\n{ref}\n\n後"
    out = embed_images(body, PLACES, article_source="articles/x.md")
    assert out == "前\n\n[f:id:lane-owner:20261020075901j:plain]\n\n後"


def test_T_S3_3c_別の階層からの相対パス_markdown_url_他所の画像_決定的():
    body = "![b](../images/b.png) ![外](https://i.example/x.png)"
    out = embed_images(body, PLACES, article_source="articles/x.md")
    assert out == "[f:id:lane-owner:20261020075902p:plain] ![外](https://i.example/x.png)"
    assert embed_images(body, PLACES, article_source="articles/x.md") == out
    url = embed_images(body, PLACES, article_source="articles/x.md", form="markdown_url")
    assert url == "![b](<https://cdn.example/b.png>) ![外](https://i.example/x.png)"
    assert hatena_convert.IMAGE_FORM == "fotolife_plain"


def test_T_S3_3c_配置が無い_userが違う_形が違うは断る():
    with pytest.raises(ConvertError):
        embed_images("![x](images/none.jpg)", PLACES, article_source="a.md")
    with pytest.raises(ConvertError):
        embed_images("![b](images/b.png)", PLACES, article_source="a.md", hatena_id="someone-else")
    with pytest.raises(ConvertError):
        embed_images("![b](images/b.png)", {"images/b.png": {"syntax": "bad"}}, article_source="a.md")
    with pytest.raises(ConvertError):
        embed_images("![b](images/b.png)", PLACES, article_source="a.md", form="html")


# ---------------------------------------------------------------- T-S3-3d

def test_T_S3_3d_囲みとspanの中の画像は数えず置き換えない():
    """柵: `_images` の区切り（`_map_outside_code`）を消すと、数が 3 になって赤。"""
    text = ("![外の画像](images/b.png)\n\n`![span](images/c.png)`\n\n```md\n![囲み](images/d.png)\n```\n\n"
            "~~~\n![チルダ](images/e.png)\n~~~\n")
    seen = []
    body, images = render(text, article_source="x.md", image_sha=lambda p: (seen.append(p) or ("0" * 64, 1)))
    assert [r["path"] for r in images] == ["images/b.png"] and seen == ["images/b.png"]
    out = embed_images(body, PLACES, article_source="x.md")
    assert out.startswith("[f:id:lane-owner:20261020075902p:plain]")
    assert "`![span](images/c.png)`" in out and "![囲み](images/d.png)" in out and "![チルダ](images/e.png)" in out


# ---------------------------------------------------------------- T-S3-3e

def test_T_S3_3e_形式は先頭のバイト列で決める_拡張子は見ない():
    """柵: 拡張子で判定すると赤（.png の名前で中身が JPEG）。"""
    assert hatena_convert.image_content_type(JPEG) == "image/jpeg"
    assert hatena_convert.image_content_type(PNG) == "image/png"
    assert hatena_convert.image_content_type(GIF) == "image/gif"
    assert hatena_convert.image_content_type(b"GIF87a...") == "image/gif"
    for bad in (WEBP, SVG, b"", b"\x89PNG fake"):
        assert hatena_convert.image_content_type(bad) is None


def _image_lane(lane, data, name="bean.png", n=1):  # noqa: F811
    refs = "\n\n".join(f"![p{i}](../images/{i}-{name})" for i in range(n))
    for i in range(n):
        lane.write(f"images/{i}-{name}", data, commit=False)
    lane.write("articles/a.md", f"# アラビカ\n\n{refs}\n", commit=False)
    from tests.conftest import run_git
    run_git(lane.repo, ["add", "-A"])
    run_git(lane.repo, ["commit", "-m", "img"])
    run_git(lane.repo, ["push"])
    return lane.queue()


@pytest.mark.parametrize("data, code", [(WEBP, "image_type_unsupported"), (SVG, "image_type_unsupported"),
                                        (b"", "image_missing")])
def test_T_S3_3e_WebPとSVGと空は断る(lane, data, code):  # noqa: F811
    path = _image_lane(lane, data)
    assert code in lint_codes(path)
    rc, out = stage1(path)
    assert code in out and "digest: " not in out


def test_T_S3_3e_拡張子がpngでも中身がJPEGなら通りimage_jpeg(lane):  # noqa: F811
    path = _image_lane(lane, JPEG, name="photo.png")
    assert lint_codes(path) == []
    approve(path)
    assert throw(FakeAdapter()).action == "post"
    from thth import hatena_fotolife
    (row,) = hatena_fotolife.list_records("hatena-main")[0]
    assert row["content_type"] == "image/jpeg"


def test_T_S3_3e_8MiBと1バイトはimage_too_large(lane):  # noqa: F811
    path = _image_lane(lane, JPEG + b"\x00" * (8 * 1024 * 1024 + 1 - len(JPEG)))
    assert "image_too_large" in lint_codes(path)
    lane.world.edit_principal("hatena-main", fotolife={"max_image_bytes": 16 * 1024 * 1024})
    assert lint_codes(path) == []


def test_T_S3_3e_21枚はtoo_many_images(lane):  # noqa: F811
    path = _image_lane(lane, PNG, n=21)
    assert "too_many_images" in lint_codes(path)


def test_T_S3_3e_合計の上限はimages_too_large(lane):  # noqa: F811
    lane.world.edit_principal("hatena-main", fotolife={"max_total_bytes": 1024 * 1024,
                                                       "max_image_bytes": 1024 * 1024})
    path = _image_lane(lane, JPEG + b"\x00" * (600 * 1024), n=2)
    assert "images_too_large" in lint_codes(path)


def test_T_S3_3e_承認のあとに上限を下げるとtimerはその符丁で断り何も送らない(lane):  # noqa: F811
    """S2 の断りと同じく、timer の照合でも approval_stale に丸めずに符丁のまま（人が直す先が違う）。"""
    path = _image_lane(lane, PNG, n=2)
    approve(path)
    delivery = fm_of(path)["delivery"]
    lane.world.edit_principal("hatena-main", fotolife={"max_images_per_entry": 1})
    adapter = FakeAdapter()
    result = throw(adapter)
    assert result.action == "none" and adapter.calls == []
    assert {"file": "a.md", "reason": "too_many_images"} in result.rejections
    from thth import deliveries, hatena_fotolife
    assert [r["state"] for r in deliveries.read(ACCOUNT, delivery)["history"]] == ["wanted"]
    assert hatena_fotolife.list_records("hatena-main") == ([], [])
