"""Google Search Console の口（読むだけ）。

- `auth.py`: 利用者の Google アカウントの OAuth（`thth/google_oauth.py` を `thth gads` と共通で使う・scope は
  `webmasters.readonly` だけ・鍵は gads と別のファイル）。
- `api.py`: REST（urllib だけ）・Search Console の断りを THTH の符丁へ。
- `cli.py`: `thth gsc auth|sites|query|compare|inspect`。

書く口（サイトの追加・サイトマップの送信）は無い。
"""
