"""第 8-10 段の独立監査の直し（VM 側）。

3.13.0 で承認ページ（添付の見本を載せる session）を消したので、承認の session へ送る本文の
境界の試験は外した。残るのは alt の天井と analytics_report の断り文句。
"""
import pytest

from thth import media as media_mod, media_uploads


def test_alt_has_one_ceiling_for_the_manuscript_and_the_invited_mouth():
    assert media_mod.MAX_ALT_BYTES == media_uploads.MAX_ALT_BYTES == 2000
    media_mod.validate([{'file': 'a.png', 'alt': 'x' * 2000}])
    with pytest.raises(media_mod.MediaError) as caught:
        media_mod.validate([{'file': 'a.png', 'alt': 'x' * 2001}])
    assert str(caught.value) == 'media: alt_too_long'
    # 数えるのは byte（招待者の口と同じ）。
    with pytest.raises(media_mod.MediaError):
        media_mod.validate([{'file': 'a.png', 'alt': 'あ' * 667}])
    # 代表画像の alt も同じ関門を通る。
    with pytest.raises(media_mod.MediaError):
        media_mod.validate([{'file': 'a.png', 'alt': 'ok',
                             'thumbnail_file': 'b.png', 'thumbnail_alt': 'x' * 2001}])


def test_analytics_report_by_names_every_accepted_word(tmp_path, monkeypatch):
    """`attachment_kind` は受け付けるのに、断り文句だけが古いままだった。"""
    import importlib.util
    from pathlib import Path as _Path
    root = _Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location('thth_mcp_server_audit', root / 'mcp/server.py')
    server = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(server)
    words = server._schema_for('analytics_report')['properties']['by']['enum']
    assert 'attachment_kind' in words
    with pytest.raises(server.ToolInputError) as caught:
        server.validate_arguments('analytics_report', {'by': 'attachment_kind'})
    for word in words:
        assert word in str(caught.value), word
