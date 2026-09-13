# 候補 4 本の出どころ（設計 v2-4 §1・4-3）

**取得日: 2026-09-13**（各ファイルの 1 行目の `<!-- source: … fetched: … -->` と同じ）。
**証拠段階 L2**（一次資料＝各自が公開しているもの）。

**引用の範囲**: 各候補は**先頭 40 行だけ**。試験の材料として必要な範囲に限る
（それ以上は取らない）。伏せた形（`blind.py` の出力）で被験者に渡す。

| ファイル | 候補 | 取れたか | 出どころ |
|---|---|---|---|
| `thth.md` | THTH | 取れた | この repo の `README.en.md` の先頭 40 行 |
| `postiz.md` | Postiz | 取れた | GitHub `gitroomhq/postiz-app` の README（raw・markdown のまま） |
| `buffer.md` | Buffer | 取れた | 製品ページ `https://buffer.com/`（HTML から本文を取り出した） |
| `typefully.md` | Typefully | 取れた | 製品ページ `https://typefully.com/`（同上） |

**4 本とも取れた。** 代わりの説明文を書いた候補は無い。

## 打ったもの（再現できる形で）

```
# Postiz（README そのもの）
curl -sSL https://raw.githubusercontent.com/gitroomhq/postiz-app/main/README.md

# Buffer・Typefully（製品ページ）
curl -sSL -A "Mozilla/5.0" https://buffer.com/
curl -sSL -A "Mozilla/5.0" https://typefully.com/
```

HTML からの本文の取り出しは**決まった規則だけ**（人が選び直していない）:

1. `script` / `style` / `noscript` / `svg` / `nav` / `header` / `footer` / `template` の
   中身を落とす。
2. 残りのタグを改行にして、HTML の実体参照を戻す。
3. 空行を詰めて、**先頭 40 行**を取る。

## 公平さについて（読むときの断り）

- **Postiz は README そのままなので、先頭にバッジと HTML の飾りが入る。** 見た目は
  他の 3 本より読みにくいが、**これが実際の README の先頭 40 行**なので直していない
  （こちらで整えると、試験の材料をこちらが作ったことになる）。
- **Buffer と Typefully は README ではなく製品ページ**（どちらも公開 README を持たない）。
  ページの性質上、申し込みの導線や数字が混じる。これも直していない。
- 候補どうしが互いの名前を出していることがある（Postiz の README は Buffer に言及する）。
  `blind.py` は**全部の候補の名前を全部の本文から**伏せるので、そこも `Tool X` になる。
