"""assets/hakari.ico uretir: koyu yuvarlak kare uzerinde altin renkli 秤 (hakari, "terazi").

Kullanim: py tools/make_icon.py   (Pillow ve Windows'un Yu Gothic fontu gerekir)
"""

import os

from PIL import Image, ImageDraw, ImageFont

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "assets", "hakari.ico")
FONT = r"C:\Windows\Fonts\YuGothB.ttc"
BG = (20, 22, 27, 255)
GOLD = (240, 180, 60, 255)
SIZE = 256


def main() -> None:
    img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((8, 8, SIZE - 8, SIZE - 8), radius=48, fill=BG, outline=GOLD, width=10)
    font = ImageFont.truetype(FONT, 176)
    x0, y0, x1, y1 = d.textbbox((0, 0), "秤", font=font)
    d.text(((SIZE - (x1 - x0)) / 2 - x0, (SIZE - (y1 - y0)) / 2 - y0), "秤", font=font, fill=GOLD)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    img.save(OUT, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    img.save(OUT.replace(".ico", ".png"))
    print(OUT)


if __name__ == "__main__":
    main()
