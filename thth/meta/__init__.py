"""Meta の共通層（設計 MetaAPI 計画 B-1・段 2・10/29）。`graph.facebook.com` の口が共に使うもの。

- `graph.py`   呼び出し・版 1 か所・`appsecret_proof`・宛先の検査・量の制限の前の待ち
- `errors.py`  エラーの振り分け（permission / rate_limited / definite / ambiguous）
- `usage.py`   `X-App-Usage`・`X-Business-Use-Case-Usage`・`x-fb-ads-insights-throttle` を読み state に残す
- `tokens.py`  `debug_token` の読み・`data_access_expires_at` の見立て・再認可の URL（`auth_type=reauthorize`）
- `media.py`   公開 URL の置き場（Threads の relay を Meta 向けに貸す・段 3 の Instagram から使う）

**Threads（`graph.threads.net`）は扱わない。** 別のアプリ・別の鍵・別の文書で、本番で動いている。
中身は `adapters/facebook.py`・`adapters/auth_facebook.py` から抜き出した（抜き出しの前後で
`tests/test_facebook_adapter.py`・`test_facebook_auth.py`・`test_facebook_stage1.py`・
`test_meta_stage0.py` を 1 行も変えずに通すことが条件）。
"""
# 先に adapters を読ませる（`errors.RateLimited` は `adapters.base.AdapterError` の子。
# `thth.meta` を先に import しても、adapters → facebook → meta の循環が同じ順で解ける）。
from ..adapters import base as _adapters_base  # noqa: F401
