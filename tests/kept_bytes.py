"""「何も残さない」試験の共通の読み方。置き場のファイルの中身を、偶然の一致を除いて返す。"""


def kept_bytes(path) -> bytes:
    raw = path.read_bytes()
    if ".git" in path.parts and "logs" in path.parts:
        # git の reflog は「古い sha 新しい sha 名前 <mail> 時刻 帯\t文」。sha と時刻の数字が偶然
        # 目印に合うことがある（11/10 の CI で 1762737001 の 7001 に当たった）ので、文の所だけを見る。
        raw = b"\n".join(line.split(b"\t", 1)[1] if b"\t" in line else b"" for line in raw.splitlines())
    return raw
