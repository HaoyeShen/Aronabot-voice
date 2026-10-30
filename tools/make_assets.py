"""Generate the local UI and sample sticker artwork.

This script is only used at build time.  The application itself never captures
the screen; every image here is either derived from the supplied portrait or
drawn from scratch with Pillow.
"""

from __future__ import annotations

import math
import random
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "assets"
STICKERS = ROOT / "data" / "stickers"
ASSETS.mkdir(parents=True, exist_ok=True)
STICKERS.mkdir(parents=True, exist_ok=True)

FONT_BOLD = r"C:\Windows\Fonts\msyhbd.ttc"
FONT_REG = r"C:\Windows\Fonts\msyh.ttc"

CYAN = (70, 216, 247)
CYAN_DEEP = (25, 160, 225)
NAVY = (20, 49, 91)
NAVY_2 = (12, 34, 67)
PINK = (255, 164, 198)
PINK_DEEP = (244, 106, 159)
SKIN = (255, 226, 221)
SKIN_SHADE = (249, 199, 201)
HAIR = (139, 220, 251)
HAIR_DARK = (81, 180, 230)
HAIR_LIGHT = (192, 240, 255)
WHITE = (255, 255, 255)


def font(size: int, bold: bool = True) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(FONT_BOLD if bold else FONT_REG, size)


def circle_crop(source: Image.Image, box: tuple[int, int, int, int], size: int) -> Image.Image:
    im = source.crop(box).convert("RGBA")
    side = min(im.size)
    im = im.crop(((im.width - side) // 2, (im.height - side) // 2,
                  (im.width + side) // 2, (im.height + side) // 2))
    im = im.resize((size, size), Image.Resampling.LANCZOS)
    mask = Image.new("L", (size * 4, size * 4), 0)
    d = ImageDraw.Draw(mask)
    d.ellipse((0, 0, size * 4 - 1, size * 4 - 1), fill=255)
    mask = mask.resize((size, size), Image.Resampling.LANCZOS)
    out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    out.paste(im, (0, 0), mask)
    return out


def build_portraits() -> None:
    src = Image.open(ASSETS / "arona.jpg").convert("RGB")
    # Focus on the face / upper torso rather than the entire square.
    face = circle_crop(src, (215, 70, 900, 750), 1024)

    for size in (256, 128, 96):
        face.resize((size, size), Image.Resampling.LANCZOS).save(ASSETS / f"avatar_{size}.png")

    # Transparent hero portrait with a soft cyan ring.  Pre-composed so Tk's
    # lack of alpha compositing does not matter.
    S = 760
    out = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    glow = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    pad = 78
    for i, alpha in ((34, 34), (24, 55), (14, 90), (6, 145)):
        gd.ellipse((pad - i, pad - i, S - pad + i, S - pad + i),
                   outline=(70, 222, 255, alpha), width=10)
    glow = glow.filter(ImageFilter.GaussianBlur(15))
    out.alpha_composite(glow)

    d = ImageDraw.Draw(out)
    d.ellipse((pad - 4, pad - 4, S - pad + 4, S - pad + 4),
              outline=(255, 255, 255, 235), width=9)
    d.ellipse((pad + 6, pad + 6, S - pad - 6, S - pad - 6),
              outline=(61, 213, 248, 220), width=5)
    # Halo behind the head, a signature Arona element.
    d.ellipse((S // 2 - 145, 16, S // 2 + 145, 76), outline=(109, 235, 255, 245), width=13)
    d.ellipse((S // 2 - 136, 23, S // 2 + 136, 69), outline=(255, 255, 255, 210), width=3)
    inset = pad + 16
    portrait = face.resize((S - inset * 2, S - inset * 2), Image.Resampling.LANCZOS)
    mask = Image.new("L", portrait.size, 0)
    ImageDraw.Draw(mask).ellipse((0, 0, portrait.width - 1, portrait.height - 1), fill=255)
    out.paste(portrait, (inset, inset), mask)
    # Pink/cyan corner ticks.
    for angle, color in ((0, CYAN), (90, PINK), (180, CYAN), (270, PINK)):
        a = math.radians(angle)
        cx, cy = S / 2 + math.cos(a) * (S / 2 - 38), S / 2 + math.sin(a) * (S / 2 - 38)
        d.rounded_rectangle((cx - 20, cy - 4, cx + 20, cy + 4), radius=4, fill=color)
    out.save(ASSETS / "arona_hero.png")

    icon = Image.new("RGBA", (1024, 1024), (0, 0, 0, 0))
    d = ImageDraw.Draw(icon)
    d.rounded_rectangle((24, 24, 1000, 1000), radius=230, fill=NAVY)
    d.rounded_rectangle((48, 48, 976, 976), radius=210, outline=CYAN, width=12)
    ring = Image.new("RGBA", (1024, 1024), (0, 0, 0, 0))
    rd = ImageDraw.Draw(ring)
    rd.ellipse((126, 312, 898, 390), outline=(100, 232, 255, 240), width=22)
    ring = ring.filter(ImageFilter.GaussianBlur(2))
    icon.alpha_composite(ring)
    face_icon = face.resize((730, 730), Image.Resampling.LANCZOS)
    mask = Image.new("L", (730, 730), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, 729, 729), fill=255)
    icon.paste(face_icon, (147, 220), mask)
    d.rounded_rectangle((24, 734, 1000, 1024), radius=140, fill=NAVY)
    d.ellipse((132, 790, 892, 990), outline=(72, 217, 247), width=10)
    icon.save(ASSETS / "app_icon.png")
    icon.save(ASSETS / "arona_icon.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48),
                                                (64, 64), (128, 128), (256, 256)])


def rounded_panel(d: ImageDraw.ImageDraw, box, radius, fill, outline=None, width=1):
    d.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=width)


def draw_eye(d: ImageDraw.ImageDraw, cx: int, cy: int, rx: int, ry: int,
             shine: tuple[int, int] = (0, 0), closed: bool = False,
             look: tuple[int, int] = (0, 0)) -> None:
    if closed:
        d.arc((cx - rx, cy - ry // 2, cx + rx, cy + ry // 2), 10, 170,
              fill=NAVY, width=10)
        return
    d.ellipse((cx - rx, cy - ry, cx + rx, cy + ry), fill=NAVY)
    d.ellipse((cx - rx * 0.64 + look[0], cy - ry * 0.6 + look[1],
               cx + rx * 0.64 + look[0], cy + ry * 0.64 + look[1]),
              fill=(47, 146, 224))
    d.ellipse((cx - rx * 0.54 + look[0], cy - ry * 0.48 + look[1],
               cx + rx * 0.54 + look[0], cy + ry * 0.54 + look[1]),
              fill=(25, 78, 152))
    d.ellipse((cx - rx * 0.34 + shine[0], cy - ry * 0.48 + shine[1],
               cx - rx * 0.04 + shine[0], cy - ry * 0.13 + shine[1]), fill=WHITE)
    d.ellipse((cx + rx * 0.14 + shine[0], cy + ry * 0.18 + shine[1],
               cx + rx * 0.36 + shine[0], cy + ry * 0.42 + shine[1]),
              fill=(122, 244, 255))
    d.arc((cx - rx, cy - ry, cx + rx, cy + ry), 195, 345, fill=(18, 36, 73), width=7)


def draw_chibi(mood: str) -> Image.Image:
    random.seed(4)
    S = 1000
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # Sticker drop shadow and white backing.
    shadow = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle((96, 102, 904, 928), radius=150,
                                             fill=(16, 47, 86, 60))
    img.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(22)))
    rounded_panel(d, (82, 72, 918, 898), 150, (255, 255, 255, 250),
                  outline=(210, 245, 255, 255), width=9)
    d.rounded_rectangle((82, 72, 918, 210), radius=140, fill=(232, 251, 255, 255))
    d.rectangle((82, 170, 918, 214), fill=(232, 251, 255, 255))
    # Accent strips.
    d.rounded_rectangle((110, 94, 292, 110), radius=8, fill=CYAN)
    d.rounded_rectangle((306, 94, 372, 110), radius=8, fill=PINK)
    for i in range(5):
        d.ellipse((806 + i * 18, 96, 814 + i * 18, 104), fill=(119, 220, 244))

    # Halo glow.
    halo = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    hd = ImageDraw.Draw(halo)
    for width, alpha in ((32, 50), (18, 100), (8, 235)):
        hd.ellipse((318, 188, 682, 260), outline=(74, 226, 255, alpha), width=width)
    halo = halo.filter(ImageFilter.GaussianBlur(10))
    img.alpha_composite(halo)
    d.ellipse((338, 205, 662, 248), outline=(255, 255, 255, 220), width=4)
    d.ellipse((310, 188, 690, 258), outline=(62, 215, 247, 235), width=9)

    # Hair back + side buns.
    d.ellipse((204, 244, 796, 810), fill=HAIR_DARK)
    d.ellipse((166, 460, 318, 690), fill=HAIR)
    d.ellipse((682, 460, 834, 690), fill=HAIR)
    d.ellipse((218, 512, 310, 660), fill=(255, 179, 210, 220))
    d.ellipse((690, 512, 782, 660), fill=(255, 179, 210, 220))
    d.ellipse((188, 318, 812, 790), fill=HAIR)

    # Neck and sailor uniform.
    d.rounded_rectangle((432, 652, 568, 770), radius=54, fill=SKIN)
    d.polygon([(330, 920), (360, 742), (470, 694), (530, 694), (640, 742), (670, 920)],
              fill=(250, 253, 255))
    d.polygon([(340, 780), (456, 704), (486, 786), (420, 860)], fill=(225, 246, 255))
    d.polygon([(660, 780), (544, 704), (514, 786), (580, 860)], fill=(225, 246, 255))
    d.polygon([(430, 750), (500, 790), (570, 750), (552, 820), (500, 850), (448, 820)],
              fill=(52, 173, 228))
    d.polygon([(470, 775), (500, 795), (530, 775), (522, 822), (500, 838), (478, 822)],
              fill=(255, 170, 202))
    d.ellipse((480, 866, 520, 906), fill=(67, 199, 238))
    d.ellipse((490, 876, 510, 896), fill=(237, 253, 255))

    # Face.
    d.ellipse((280, 302, 720, 712), fill=SKIN)
    d.ellipse((282, 520, 370, 602), fill=(255, 193, 195, 120))
    d.ellipse((630, 520, 718, 602), fill=(255, 193, 195, 120))
    for i in range(3):
        d.ellipse((326 + i * 24, 530, 336 + i * 24, 538), fill=(255, 151, 174))
        d.ellipse((650 + i * 16, 530, 660 + i * 16, 538), fill=(255, 151, 174))

    # Bangs: layered pointed strands.
    d.polygon([(248, 416), (306, 262), (404, 224), (464, 302), (430, 410), (370, 452),
               (344, 368), (302, 446)], fill=HAIR_LIGHT)
    d.polygon([(386, 222), (500, 210), (604, 250), (696, 430), (628, 438), (578, 340),
               (540, 448), (492, 330), (448, 436)], fill=HAIR)
    d.polygon([(612, 246), (712, 290), (752, 430), (690, 480), (648, 380), (646, 458),
               (600, 402)], fill=HAIR_LIGHT)
    d.polygon([(640, 300), (704, 354), (660, 402), (624, 366)], fill=(255, 247, 252))
    d.polygon([(334, 300), (398, 338), (360, 392), (318, 354)], fill=(255, 248, 252))

    # Brows and eyes vary by mood.
    eye_y = 480
    look = (0, 0)
    if mood in ("shy",):
        look = (-8, 7)
    if mood == "think":
        look = (8, -9)
    if mood == "sad":
        look = (0, 8)
    closed = mood in ("happy", "sleepy")
    draw_eye(d, 390, eye_y, 66, 88, shine=(-18, -24), closed=closed, look=look)
    draw_eye(d, 610, eye_y, 66, 88, shine=(-18, -24), closed=closed, look=look)

    # Brows.
    if mood == "angry":
        d.line((326, 384, 438, 416), fill=NAVY, width=15)
        d.line((674, 384, 562, 416), fill=NAVY, width=15)
    elif mood == "sad":
        d.line((330, 410, 436, 392), fill=NAVY, width=13)
        d.line((670, 410, 564, 392), fill=NAVY, width=13)
    else:
        d.arc((324, 376, 452, 424), 190, 345, fill=HAIR_DARK, width=11)
        d.arc((548, 376, 676, 424), 195, 350, fill=HAIR_DARK, width=11)

    # Mouth.
    if mood == "happy":
        d.arc((446, 564, 554, 640), 10, 170, fill=(185, 65, 92), width=11)
        d.ellipse((470, 584, 530, 622), fill=(255, 145, 164))
    elif mood == "angry":
        d.arc((456, 606, 544, 654), 190, 350, fill=(185, 65, 92), width=10)
    elif mood == "sad":
        d.arc((456, 610, 544, 660), 190, 350, fill=(185, 65, 92), width=10)
    elif mood == "shy":
        d.line((470, 600, 486, 590, 500, 602, 516, 590, 532, 600),
               fill=(185, 65, 92), width=8, joint="curve")
    elif mood == "think":
        d.ellipse((476, 606, 524, 630), fill=(185, 65, 92))
    elif mood == "sleepy":
        d.ellipse((484, 610, 516, 632), fill=(197, 92, 112))
    else:
        d.arc((458, 574, 542, 630), 20, 160, fill=(185, 65, 92), width=9)
    d.ellipse((484, 556, 516, 572), fill=(255, 220, 220, 140))

    # Expression props.
    if mood == "hello":
        # Waving hand.
        d.ellipse((738, 560, 872, 694), fill=SKIN)
        for i in range(4):
            d.rounded_rectangle((760 + i * 26, 522 + abs(i - 1) * 8, 780 + i * 26,
                                 586 + abs(i - 1) * 8), radius=12, fill=SKIN,
                                outline=(244, 190, 188), width=3)
        d.arc((742, 570, 872, 700), 120, 300, fill=(241, 188, 190), width=4)
    elif mood == "happy":
        for x, y, r in ((790, 330, 12), (824, 292, 8), (760, 280, 7)):
            d.ellipse((x - r, y - r * 3, x + r, y + r * 3), fill=PINK)
            d.ellipse((x - r * 3, y - r, x + r * 3, y + r), fill=PINK)
        d.arc((730, 390, 840, 500), 120, 300, fill=CYAN, width=10)
    elif mood == "think":
        d.ellipse((714, 330, 812, 398), fill=(236, 250, 255), outline=CYAN, width=5)
        d.text((748, 332), "?", font=font(46), fill=CYAN_DEEP)
        d.ellipse((692, 400, 722, 430), fill=(236, 250, 255), outline=CYAN, width=4)
    elif mood == "sad":
        d.ellipse((360, 566, 394, 618), fill=(115, 214, 255))
        d.polygon([(377, 574), (360, 596), (394, 596)], fill=(115, 214, 255))
        d.arc((322, 340, 450, 440), 10, 130, fill=CYAN_DEEP, width=5)
    elif mood == "angry":
        for off in (0, 1):
            d.line((720 + off * 10, 330, 758 + off * 10, 372), fill=PINK_DEEP, width=12)
            d.line((758 + off * 10, 330, 720 + off * 10, 372), fill=PINK_DEEP, width=12)
    elif mood == "sleepy":
        d.text((760, 310), "Z", font=font(48), fill=CYAN_DEEP)
        d.text((808, 260), "z", font=font(36), fill=PINK_DEEP)
    elif mood == "confused":
        d.text((748, 318), "?", font=font(64), fill=CYAN_DEEP)
        d.text((808, 358), "?", font=font(38), fill=PINK_DEEP)
        d.arc((330, 350, 430, 420), 210, 330, fill=CYAN_DEEP, width=5)

    # Bottom name plate.
    rounded_panel(d, (292, 804, 708, 878), 37, NAVY)
    names = {
        "hello": "在呢，老师！", "happy": "开心！", "shy": "唔…害羞", "think": "让我想想",
        "sad": "有点委屈", "angry": "气鼓鼓", "sleepy": "困困…", "confused": "诶？",
    }
    label = names.get(mood, "阿洛娜")
    f = font(34)
    w = d.textlength(label, font=f)
    d.text(((S - w) / 2, 818), label, font=f, fill=WHITE)
    d.ellipse((292, 834, 316, 858), fill=CYAN)
    d.ellipse((684, 834, 708, 858), fill=PINK)
    return img.resize((420, 420), Image.Resampling.LANCZOS)


def build_stickers() -> None:
    definitions = [
        ("hello", "挥手问候", "开心,问候,在吗,你好", "阿洛娜挥着手，笑眯眯地回应老师。", "开心"),
        ("happy", "开心转圈", "开心,表扬,好耶,庆祝", "阿洛娜眼睛发亮、周围冒着小星星。", "开心"),
        ("shy", "害羞脸红", "害羞,喜欢,被夸,不好意思", "阿洛娜红着脸、视线飘向一边。", "害羞"),
        ("think", "认真思考", "思考,疑问,让我想想,分析", "阿洛娜托着下巴，头顶冒出一个问号。", "思考"),
        ("sad", "小小委屈", "委屈,难过,安慰,求抱抱", "阿洛娜眼角挂着泪珠，可怜巴巴。", "难过"),
        ("angry", "气鼓鼓", "生气,抗议,不满,哼", "阿洛娜鼓起脸颊，头上冒出粉色怒符。", "生气"),
        ("sleepy", "困困打盹", "困,晚安,累了,睡觉", "阿洛娜半闭着眼，旁边飘着 Zzz。", "困倦"),
        ("confused", "满头问号", "疑惑,不懂,惊讶,诶", "阿洛娜歪着头，旁边冒出好几个问号。", "疑惑"),
    ]
    for mood, _, _, _, _ in definitions:
        draw_chibi(mood).save(STICKERS / f"arona_{mood}.png")


if __name__ == "__main__":
    build_portraits()
    build_stickers()
    print("assets generated")
