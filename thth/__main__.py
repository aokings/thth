"""`python -m thth …` の入口（`bin/thth` と同じ `thth.cli.main` を呼ぶだけ）。

なぜ要るか（2026-09-12・C2 の乾式試験で踏んだ）: まっさらな clone を置いた直後は
`bin/thth` に PATH が通っていない。そこで最初に打たれるのは `python -m thth doctor`
だが、`thth/__main__.py` が無いと **`'thth' is a package and cannot be directly
executed`** で落ちる。**道具が無いのではなく入口が無いだけ**なのに、そうは読めない。

`bin/thth` は残す（symlink して PATH に置く運用・`tests/test_cli_entrypoint.py`）。
ここは判断を持たず、`sys.path` にも触らない（`-m` で起動された時点で解決済み）。
"""
from __future__ import annotations

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
