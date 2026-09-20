# 投票選択肢の文字数

Mastodon の投票は instance の `configuration.polls` を毎回確認し、選択肢を extended grapheme cluster で数える。固定 upstream `3cdcf93dd30e7f1f221b6ac2dd107e8d35146c44` の [PollOptionsValidator](https://github.com/mastodon/mastodon/blob/3cdcf93dd30e7f1f221b6ac2dd107e8d35146c44/app/validators/poll_options_validator.rb) は Ruby `each_grapheme_cluster` を使用する。単なるPython `len` や既存Blueskyの近似countで代用しない。instance側RubyのUnicode版は未観測であり、その差による最終受理はproviderが決める。

実装は Unicode **17.0.0**、[UAX #29 revision 47](https://www.unicode.org/reports/tr29/tr29-47.html) の default extended grapheme cluster 規則のみ。`unicodedata` のPython別Unicode版には依存しない。正規化や入力本文の変更は行わない。文字を前から1回読み、固定property表をbinary searchし、定数量の状態を保持する。上限超過が確定したら数え終えず止められる。元の文字列以外に全clusterのコピーを作らない。単一clusterが長い場合も後方反復走査をしない。

2026-09-21直接取得した原文:

- [GraphemeBreakProperty](https://www.unicode.org/Public/17.0.0/ucd/auxiliary/GraphemeBreakProperty.txt)
- [DerivedCoreProperties](https://www.unicode.org/Public/17.0.0/ucd/DerivedCoreProperties.txt) の InCB だけ
- [emoji-data](https://www.unicode.org/Public/17.0.0/ucd/emoji/emoji-data.txt) の Extended_Pictographic だけ
- [GraphemeBreakTest](https://www.unicode.org/Public/17.0.0/ucd/auxiliary/GraphemeBreakTest.txt) 全vector（製品test fixtureへbyte同一コピー）
- [Unicode License V3](https://www.unicode.org/license.txt)

各inputのSHA256は `tools/generate_graphemes.py:SOURCES` と生成物 `thth/_grapheme_data.py:SOURCE_SHA256` に固定。配布許諾文を生成物冒頭へ全文同梱している。変更する場合は原文を保存し、`python tools/generate_graphemes.py <原文directory> thth/_grapheme_data.py` で再生成する。hashが違う入力は拒否する。生成されたPython tableはwheel/sdist内に入り、実行時のdownloadや外部packageを必要としない。

既存Bluesky本文countの近似契約はこの変更で置き換えない。将来このhelperを他の入力へ適用する際は、そのAPIの単位を別途照合する。
