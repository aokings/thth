"""Google 広告（Google Ads API）の口（読むだけ・段 2 の最初の口）。

- `auth.py`: 利用者の Google アカウントの OAuth（端末のアプリの形・loopback・PKCE）と refresh token の置き場。
- `api.py`: REST（urllib だけ）・版の定数・Google の断りを THTH の符丁へ。
- `cli.py`: `thth gads auth|accounts|keywords|report`。

書く口（作る・止める・予算）はこの段に無い。調べ: `docs/調べ_Google広告とAdSenseのAPI_2026-11-06.md`。
"""
