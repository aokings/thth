#!/usr/bin/env python3
"""`docs/原稿_添付_2.13.md` の網羅表を `thth/media_capabilities.py` から作る。

**表を二か所に書かない**（設計 2.13.0 §0.1・依頼 第 10 段）。道具が出す表と原稿の
表が別々に育つと、どちらが正本か誰にも判らなくなる。ここは Python の表を
markdown に写すだけで、判断はしない。

    python3 tools/render_media_capabilities.py           # 書き込む
    python3 tools/render_media_capabilities.py --check   # 差があれば rc=1

`tests/test_v213_capabilities.py` が `--check` と同じ比較をするので、表を直して
原稿を作り直さないと試験が落ちる。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from thth import media_capabilities  # noqa: E402

DOC = Path(__file__).resolve().parent.parent / 'docs' / '原稿_添付_2.13.md'
BEGIN = '<!-- capabilities:begin tools/render_media_capabilities.py で生成。手で直さない -->'
END = '<!-- capabilities:end -->'


def render():
    """表と但し書きの markdown（前後の marker を含む）。"""
    media = list(media_capabilities.MEDIA)
    table = media_capabilities.table()
    notes = media_capabilities.notes()
    keys = sorted(set().union(*(set(table[name]) for name in media)))
    lines = [BEGIN, '',
             '| key | ' + ' | '.join(media) + ' |',
             '|---|' + '---|' * len(media)]
    for key in keys:
        lines.append('| `' + key + '` | '
                     + ' | '.join(table[name].get(key, '—') for name in media) + ' |')
    vocabulary = ' / '.join('`' + value + ('<理由>`' if value.endswith(': ') else '`')
                            for value in media_capabilities.VOCABULARY)
    lines += ['', '値の語彙: ' + vocabulary
              + '。`unsupported` / `unverified` / `ascii_only` は静的な理由を必ず持ちます。'
              '**「未確認」を「非対応」とは書きません。**',
              '',
              'この表は `thth doctor`（本文と `--json`）と `handoff-report --since-last-read` の '
              '`tool.capabilities` に同じ内容で出ます。**静的な表であって、provider を叩いた'
              '結果ではありません。**実機での測定が要る行は検収依頼に並びます。', '']
    rows = [(name, key, notes[name][key]) for name in media for key in sorted(notes.get(name, {}))]
    if rows:
        lines += ['但し書き（`supported` の一語では足りないもの）:', '']
        lines += ['- `' + name + '.' + key + '`: ' + note for name, key, note in rows]
        lines.append('')
    lines.append(END)
    return '\n'.join(lines)


def replace(text, block):
    head, _, rest = text.partition(BEGIN)
    if not rest:
        raise SystemExit('marker not found: ' + BEGIN)
    _, _, tail = rest.partition(END)
    return head + block + tail


def main(argv):
    text = DOC.read_text(encoding='utf-8')
    updated = replace(text, render())
    if '--check' in argv:
        if updated != text:
            print('docs/原稿_添付_2.13.md の網羅表が表と食い違っています', file=sys.stderr)
            return 1
        return 0
    DOC.write_text(updated, encoding='utf-8')
    return 0


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:]))
