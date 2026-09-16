# 手順: PyPI と MCP registry に v2.0.0 を出す（2026-09-14）

masaru が上から順に打てば終わる手順です。各行に **[masaru の手]** か **[開発でよい]** を
付けています。**[開発でよい]** はこの後も Claude セッションに投げてよい操作、
**[masaru の手]** は認証・秘密・本番反映を含むので masaru 本人が打つ操作です
（CLAUDE.md の secret 規約・「PyPI・registry・upload・publish は masaru の手」）。

前提: `pyproject.toml`（hatchling・`thth/VERSION` = 2.0.0）、`server.json`
（`io.github.aokings/thth`・pypi `thth`・2.0.0）、README 末尾の
`mcp-name: io.github.aokings/thth` は用意済み。`tests/test_packaging.py`・
`tests/test_server_json.py` は通っている（2026-09-14 に worktree で確認済み・
`dist/` の中身も unzip -l / tar tzf で確認済み——雛形は入って台帳と docs は入っていない）。

---

## (a) PyPI のアカウントと API トークン【masaru の手】

1. https://pypi.org でアカウントを作る（まだ無ければ）。2 段階認証を有効にする
   （PyPI は 2026 時点で必須）。
2. Account settings → API tokens → **Add API token**。
   - **初回は Scope: Entire account**（プロジェクトがまだ存在しないので選べない）。
   - **初回 upload が終わったら、この初回トークンを削除し、
     Scope をプロジェクト `thth` 限定に絞った新しいトークンを作り直す。**
     以後はそのプロジェクト限定トークンだけを使う。
3. トークンの値（`pypi-` で始まる長い文字列）は **`~/.pypirc` か環境変数
   `TWINE_PASSWORD` に置く**。チャット・repo 内のファイル・スクリーンショットに
   **値を出さない**（CLAUDE.md の secret 規約と同じ理由——AI セッションのログに
   残ると漏れる）。

   `~/.pypirc` の形（`username` は文字どおり `__token__`）:

   ```ini
   [pypi]
   username = __token__
   password = pypi-＜ここは埋めない。貼るのは自分の端末で＞

   [testpypi]
   repository = https://test.pypi.org/legacy/
   username = __token__
   password = pypi-＜同上・testpypi は別トークン＞
   ```

   `~/.pypirc` は `chmod 600` にする。

---

## (b) build して upload する【masaru の手（実行は開発でもよいが upload は本人）】

worktree または repo の根で（**この worktree では実行しない・upload は本番反映**）:

```bash
python -m build
python -m twine check dist/*
```

ここまでは何度でも安全（**[開発でよい]** — 実際 2026-09-14 にこの worktree で
実行し、`twine check` は wheel・sdist とも `PASSED`）。

**先に testpypi で試す（1 行・masaru の手・取り消せないので upload の前に）:**

```bash
python -m twine upload --repository testpypi dist/*
```

うまく行ったら https://test.pypi.org/project/thth/ で見た目を確認してから本番へ:

```bash
python -m twine upload dist/*
```

`twine upload` は `~/.pypirc` を読むので、コマンド自体にトークンを書かない。
`~/.pypirc` が無ければ `TWINE_USERNAME=__token__ TWINE_PASSWORD=pypi-... twine upload dist/*`
でもよいが、**シェル履歴に残る**ので `~/.pypirc` を推奨。

---

## (c) upload 後の確認【masaru の手（別 venv での install・実機で叩く）】

```bash
python -m venv /tmp/thth-check
/tmp/thth-check/bin/pip install thth==2.0.0
/tmp/thth-check/bin/thth --version
```

`thth 2.0.0` と出れば通っている。この venv は使い捨てなので確認後に消してよい
（`rm -rf /tmp/thth-check`）。

---

## (d) MCP registry へ登録する【masaru の手】

### 入れ方

```bash
# brew があれば
brew install mcp-publisher

# 無ければ pre-built binary（quickstart の 1 行）
curl -L "https://github.com/modelcontextprotocol/registry/releases/latest/download/mcp-publisher_$(uname -s | tr '[:upper:]' '[:lower:]')_$(uname -m | sed 's/x86_64/amd64/;s/aarch64/arm64/').tar.gz" | tar xz mcp-publisher && sudo mv mcp-publisher /usr/local/bin/
```

確認: `mcp-publisher --help`。

### ログインと publish

```bash
mcp-publisher login github
```

表示される `https://github.com/login/device` を開いてコードを入力し、GitHub アカウント
（`aokings`）で認可する。**この repo（`server.json` の `name: io.github.aokings/thth`）は
GitHub 認証だと `io.github.aokings/` で始まる名前しか publish できない**ので、
GitHub 側も `aokings` でログインすること。

```bash
# repo の根（server.json がある場所）で
mcp-publisher publish
```

`server.json` をそのまま読む。**PyPI 側の README に `mcp-name: io.github.aokings/thth`
の行が無いと registry がここで断る**（所有確認・設計 v2-4 §3）——今回は入っている
ことを 2026-09-14 に確認済み。

### 確認

```bash
curl "https://registry.modelcontextprotocol.io/v0.1/servers?search=io.github.aokings/thth"
```

`"name":"io.github.aokings/thth"` を含む JSON が返れば登録完了。

---

## (e) 版を上げるとき【開発でよい・commit までは開発、tag と配布は masaru】

版を 1 つ上げるたびに、数字を書く場所は **3 か所**（うち機械が一致を強制するのは
`server.json` の 2 か所——`tests/test_server_json.py`）:

| ファイル | 場所 |
|---|---|
| `thth/VERSION` | 1 行そのもの |
| `server.json` | `version`（トップレベル） |
| `server.json` | `packages[0].version` |

3 つとも揃えて commit したら `tests/test_packaging.py`・`tests/test_server_json.py` を
再度通す。README の版と全件の件数も直す。

---

## (f) 2026-09-16 から: **tag を push すれば PyPI と registry は機械が出す**

`.github/workflows/publish.yml`（OIDC）を入れた。**秘密はこの repo にも Actions にも置かない**
——GitHub がその場で発行する短命の証明で、PyPI（Trusted Publishing）と MCP registry
（`mcp-publisher login github-oidc`）の両方に認証する。それまで版ごとに要った
`mcp-publisher login github`（ブラウザ・**資格は実測 40 分で切れる**）と `~/.pypirc` の
API トークンは、**もう要らない**。

**版を出す手順（新）**:

1. 版上げを commit（上の 3 か所＋ README）し、全件が緑であることを確かめる。
2. **`release` を進める**（`git push origin main:refs/heads/release`）——本番（VM）の配布。
   **これは人の判断**（機械はやらない・`docs/定型_開発セッションの引き継ぎ開始文.md`）。
3. `git tag -a vX.Y.Z -m "…" && git push origin vX.Y.Z` —— ここで workflow が走る。
   tag と `thth/VERSION` が違えば**出る前に落ちる**。全件テストも配布物の検査
   （台帳・記録・秘密が混ざっていないか）も workflow の中で通る。
4. 確認: `https://pypi.org/project/thth/<版>/` と
   `curl "https://registry.modelcontextprotocol.io/v0.1/servers?search=io.github.aokings/thth"`。

**PyPI の登録は 2026-09-16 に済んだ**（masaru の申告・L3——PyPI の管理画面はこちらから見えないので、最初の tag で workflow が通ったことを見て L1 にする）。

（最初にやったこと・記録として）PyPI の画面で「この repo のこの workflow を
信頼する」と登録する。Your projects → `thth` → Manage → Publishing →
owner `aokings`・repository `thth`・workflow `publish.yml`・environment `publish`。
**API トークンを貼る作業ではない**（貼るのは repo 名と workflow 名）。登録が済めば
`~/.pypirc` のトークンは消してよい。

GitHub 側の `publish` 環境（Settings → Environments）は、無ければ最初の実行時に作られる。
枝や tag を絞りたければそこで制限する。

**Linux で全件が通ることは `test.yml` が main への push ごとに確かめる**（開発は macOS で回すので、Linux でだけ落ちるテストを配布の日より前に知るため）。tag を打つ前に、その commit の `test` が緑であることを見る。
