"""ストーリーの絵を作る（11/24・`thth send --story`）。**Pillow を import するのはこの 1 枚だけ。**

THTH の依存は 0 のまま（pyproject の先頭）。Pillow は `pip install 'thth[image]'` で入れた機械だけが使い、
入れていない機械では `available()` が偽になる——写真の縦横の注意は言うだけ、文字のカードは断る（なぜ・通すには・
もっと良く）。柵: `tests/test_story.py` の「Pillow の import はこの 1 枚だけ」。

作る絵は 2 つ:

- `pad()`: 9:16 でない写真の上下か左右に、写真の平均の色の余白を足して 1080×1920 に収める（どこも切れない）。
- `card()`: 本文を、白っぽい地に角の丸い白い札（口座名と本文）を置いた 1080×1920 の絵にする
  （11/24 に masaru が Threads から手で共有したときの見た目が目安）。

作った絵は口座の repo（`repo_dir`）の `thth-story/` に、入力から決まる名前で置く（`save()`）。同じ入力なら同じ
名前の同じ絵を使い直すので、一段目（下見）と二段目（`--production`）で digest が変わらない。承認の指紋に入るのは
この送る絵そのもの（添付の source SHA と public SHA）。Meta に渡すのは今までの添付と同じ relay の公開の URL
（`media_relay`・Worker の `/m/`）で、新しい置き場や URL は作らない。
"""
from __future__ import annotations

import hashlib
import io
import os
import tempfile
import unicodedata

WIDTH, HEIGHT = 1080, 1920
RATIO = WIDTH / HEIGHT
# 9:16 と見なす幅（1080×1920 の 1% 以内・1080×1918 のような端数は余白を足さない）。
TOLERANCE = 0.01
OUT_DIR = "thth-story"
# 絵の作り方を変えたら上げる（名前が変わるので、古い絵を新しい作り方の絵と取り違えない）。
RENDER_VERSION = "1"
JPEG_QUALITY = 90

# 字の書体。環境の THTH_STORY_FONT が先。無ければ下の順に探す（Ubuntu の fonts-noto-cjk・IPAex・macOS のヒラギノ）。
FONT_ENV = "THTH_STORY_FONT"
FONT_CANDIDATES = (
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJKjp-Regular.otf",
    "/usr/share/fonts/truetype/noto/NotoSansJP-Regular.ttf",
    "/usr/share/fonts/opentype/ipaexfont-gothic/ipaexg.ttf",
    "/usr/share/fonts/truetype/fonts-japanese-gothic.ttf",
    "/System/Library/Fonts/ヒラギノ角ゴシック W4.ttc",
    "/System/Library/Fonts/ヒラギノ角ゴシック W3.ttc",
)

# カードの見た目（1080×1920 の上の px）。
BACKGROUND = (242, 242, 242)
CARD = (255, 255, 255)
CARD_BORDER = (224, 224, 224)
INK = (17, 17, 17)
SUBTLE = (110, 110, 110)
AVATAR = (34, 34, 34)
OUTER = 72            # 地の端から札までの左右の余り
INNER = 60            # 札の内側の余り
RADIUS = 40
LABEL_SIZE = 40
AVATAR_SIZE = 84
HEADER_GAP = 40
LINE_SPACING = 1.55
# 大きい字から順に試し、収まる最大の字で組む。どれでも収まらなければ長すぎる（断る）。
TEXT_SIZES = (64, 58, 52, 46, 42)
MAX_CARD_HEIGHT = HEIGHT - 2 * 200   # 上下に 200 px は残す（IG の画面の上の名前と下の返信の欄に掛からない）
MAX_CHARS = 600                      # 組む前に見る上限（それより長い本文は組まずに断る）

# 行頭に来ない字・行末に来ない字（禁則の基本だけ）。
NO_HEAD = set("、。，．,.・：；:;？！?!ー‐–—）)］]｝}」』】〕〉》〙〗’”ゝゞヽヾ々ぁぃぅぇぉっゃゅょゎゕゖァィゥェォッャュョヮヵヶ")
NO_TAIL = set("（(［[｛{「『【〔〈《〘〖‘“")


def available() -> bool:
    """Pillow が入っているか（`pip install 'thth[image]'`）。"""
    try:
        import PIL.Image  # noqa: F401
    except ImportError:
        return False
    return True


def find_font() -> str | None:
    """日本語の字の書体のファイル（環境の THTH_STORY_FONT が先）。無ければ None。"""
    chosen = os.environ.get(FONT_ENV)
    if chosen:
        return chosen if os.path.isfile(chosen) else None
    for path in FONT_CANDIDATES:
        if os.path.isfile(path):
            return path
    return None


def is_story_shape(width: int, height: int) -> bool:
    return abs(width / height - RATIO) <= RATIO * TOLERANCE


def cut_sides(width: int, height: int) -> str:
    """9:16 の画面いっぱいに出すとき切れる側（横に広い写真は左右・縦に長い写真は上下）。"""
    return "左右" if width / height > RATIO else "上下"


def pad_sides(width: int, height: int) -> str:
    """切らずに収めるとき余白を足す側（切れる側の逆）。"""
    return "上下" if width / height > RATIO else "左右"


# ---------------------------------------------------------------- 行の折り返し（Pillow なしで試せる）

def _tokens(paragraph: str) -> list[str]:
    """英数字のつながりは 1 つ（単語の途中で折らない）、ほかは 1 字ずつ。"""
    out, word = [], ""
    for ch in paragraph:
        if ch.isascii() and (ch.isalnum() or ch in "-_'@#/%&+"):
            word += ch
            continue
        if word:
            out.append(word)
            word = ""
        out.append(ch)
    if word:
        out.append(word)
    return out


def _split_long(token: str, width: float, measure) -> list[str]:
    pieces, current = [], ""
    for ch in token:
        if current and measure(current + ch) > width:
            pieces.append(current)
            current = ch
        else:
            current += ch
    if current:
        pieces.append(current)
    return pieces


def wrap(text: str, width: float, measure) -> list[str]:
    """本文を `width` に収まる行に分ける。`measure(文字列) -> 幅` を渡す（試験は Pillow なしで呼べる）。

    改行はそのまま行を分ける。行頭に来ない字（、。」など）は前の行の末に付けてはみ出させ（ぶら下げ）、
    行末に来ない字（「（など）は次の行へ送る。行頭の空白は落とす。
    """
    lines: list[str] = []
    for paragraph in text.split("\n"):
        line = ""
        for token in _tokens(paragraph):
            if not line and token == " ":
                continue
            if measure(line + token) <= width or not line:
                if not line and measure(token) > width:
                    pieces = _split_long(token, width, measure)
                    lines.extend(pieces[:-1])
                    line = pieces[-1]
                else:
                    line += token
                continue
            if token[0] in NO_HEAD:
                line += token          # ぶら下げ（札の内側の余りに少しはみ出す）
                continue
            carry = ""
            while line and line[-1] in NO_TAIL:
                carry = line[-1] + carry
                line = line[:-1]
            lines.append(line.rstrip())
            line = (carry + token).lstrip(" ")
            if measure(line) > width:
                pieces = _split_long(line, width, measure)
                lines.extend(pieces[:-1])
                line = pieces[-1]
        lines.append(line.rstrip())
    while lines and not lines[-1]:
        lines.pop()
    while lines and not lines[0]:
        lines.pop(0)
    return lines


def clean_text(text: str) -> str:
    """カードに組む本文: 改行を \\n にそろえ、制御文字（改行とタブ以外）を落とし、タブは空白にする。"""
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n").replace("\t", " ")
    return "".join(ch for ch in text if ch == "\n" or unicodedata.category(ch) != "Cc").strip()


# ---------------------------------------------------------------- 絵（Pillow が要る）

def _jpeg(image) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=JPEG_QUALITY, optimize=False, progressive=False)
    return buffer.getvalue()


def pad(data: bytes) -> bytes:
    """写真を切らずに 1080×1920 に収め、余った所を写真の平均の色で埋めた JPEG。"""
    from PIL import Image, ImageOps, ImageStat
    with Image.open(io.BytesIO(data)) as opened:
        image = ImageOps.exif_transpose(opened).convert("RGB")
    scale = min(WIDTH / image.width, HEIGHT / image.height)
    size = (max(1, round(image.width * scale)), max(1, round(image.height * scale)))
    resized = image.resize(size, Image.LANCZOS)
    color = tuple(int(v) for v in ImageStat.Stat(image).mean[:3])
    canvas = Image.new("RGB", (WIDTH, HEIGHT), color)
    canvas.paste(resized, ((WIDTH - size[0]) // 2, (HEIGHT - size[1]) // 2))
    return _jpeg(canvas)


class Layout:
    """組んだ結果（字の大きさと行）。収まらなければ `lines` は None。"""

    def __init__(self, size, lines, line_height):
        self.size, self.lines, self.line_height = size, lines, line_height


def layout(text: str, font_path: str) -> Layout:
    """収まる最大の字で組む。どの大きさでも札が `MAX_CARD_HEIGHT` を越えるなら `lines` が None。"""
    from PIL import ImageFont
    width = WIDTH - 2 * OUTER - 2 * INNER
    for size in TEXT_SIZES:
        font = ImageFont.truetype(font_path, size)
        lines = wrap(text, width, font.getlength)
        line_height = round(size * LINE_SPACING)
        body = line_height * len(lines)
        if 2 * INNER + AVATAR_SIZE + HEADER_GAP + body <= MAX_CARD_HEIGHT:
            return Layout(size, lines, line_height)
    return Layout(TEXT_SIZES[-1], None, round(TEXT_SIZES[-1] * LINE_SPACING))


def card(text: str, label: str, font_path: str, fitted: Layout | None = None) -> bytes:
    """本文の札の絵（1080×1920 の JPEG）。`fitted` は `layout()` の結果（収まっていること）。"""
    from PIL import Image, ImageDraw, ImageFont
    fitted = fitted or layout(text, font_path)
    if fitted.lines is None:
        raise ValueError("text does not fit")
    body_font = ImageFont.truetype(font_path, fitted.size)
    label_font = ImageFont.truetype(font_path, LABEL_SIZE)
    initial_font = ImageFont.truetype(font_path, round(AVATAR_SIZE * 0.46))
    card_w = WIDTH - 2 * OUTER
    card_h = 2 * INNER + AVATAR_SIZE + HEADER_GAP + fitted.line_height * len(fitted.lines)
    left, top = OUTER, (HEIGHT - card_h) // 2
    canvas = Image.new("RGB", (WIDTH, HEIGHT), BACKGROUND)
    draw = ImageDraw.Draw(canvas)
    draw.rounded_rectangle((left, top, left + card_w, top + card_h), radius=RADIUS, fill=CARD,
                           outline=CARD_BORDER, width=2)
    # 札の頭: 丸に頭文字・その右に口座名。
    ax, ay = left + INNER, top + INNER
    draw.ellipse((ax, ay, ax + AVATAR_SIZE, ay + AVATAR_SIZE), fill=AVATAR)
    initial = (label.lstrip("@")[:1] or "?").upper()
    draw.text((ax + AVATAR_SIZE / 2, ay + AVATAR_SIZE / 2), initial, font=initial_font, fill=CARD, anchor="mm")
    draw.text((ax + AVATAR_SIZE + 24, ay + AVATAR_SIZE / 2), label, font=label_font, fill=INK, anchor="lm")
    # 本文: 行の高さの真ん中に字の中心を置く。
    y = ay + AVATAR_SIZE + HEADER_GAP
    for line in fitted.lines:
        if line:
            draw.text((left + INNER, y + fitted.line_height / 2), line, font=body_font, fill=INK, anchor="lm")
        y += fitted.line_height
    return _jpeg(canvas)


# ---------------------------------------------------------------- 置き場

def key(*parts: str) -> str:
    digest = hashlib.sha256()
    for part in (RENDER_VERSION, *parts):
        digest.update(part.encode("utf-8"))
        digest.update(b"\x00")
    return digest.hexdigest()[:24]


def save(repo_dir: str, name_key: str, make) -> str:
    """`<repo_dir>/thth-story/<name_key>.jpg` に置いて repo 相対の道を返す。あればそのまま使い直す。

    `make()` は絵の bytes を返す（無いときだけ呼ぶ）。置き場の `.gitignore`（`*`）で、口座の repo の
    `git status` に出ない（repo の決まりの `.gitignore` は触らない）。
    """
    folder = os.path.join(repo_dir, OUT_DIR)
    os.makedirs(folder, exist_ok=True)
    ignore = os.path.join(folder, ".gitignore")
    if not os.path.exists(ignore):
        with open(ignore, "w", encoding="utf-8") as f:
            f.write("# thth send --story が作った絵の置き場（git に載せない）\n*\n")
    relative = f"{OUT_DIR}/{name_key}.jpg"
    path = os.path.join(repo_dir, relative)
    if os.path.isfile(path) and not os.path.islink(path):
        return relative
    data = make()
    fd, temporary = tempfile.mkstemp(dir=folder, prefix=".making-", suffix=".jpg")
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    os.chmod(temporary, 0o644)
    os.replace(temporary, path)
    return relative
