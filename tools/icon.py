"""生成软件图标（app.png / app.ico / app.icns）。需要 Pillow 和一款中文粗体字体。"""
import os
import sys
from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, 'assets')
FONT = sys.argv[1] if len(sys.argv) > 1 else '/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc'


def draw(size=1024):
    img = Image.new('RGBA', (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    pad = int(size * 0.06)
    r = int(size * 0.22)
    # 深蓝底 + 浅色“账页”线条 + 白色“账”字
    d.rounded_rectangle([pad, pad, size - pad, size - pad], radius=r, fill=(31, 79, 143, 255))
    for i in range(4):
        y = int(size * (0.68 + i * 0.065))
        d.line([(int(size * 0.24), y), (int(size * 0.76), y)], fill=(122, 166, 224, 255), width=max(2, size // 64))
    font = ImageFont.truetype(FONT, int(size * 0.50), index=2)  # 索引 2 = Noto Sans CJK SC
    box = d.textbbox((0, 0), '账', font=font)
    w, h = box[2] - box[0], box[3] - box[1]
    d.text(((size - w) / 2 - box[0], size * 0.36 - h / 2 - box[1]), '账', font=font, fill=(255, 255, 255, 255))
    return img


if __name__ == '__main__':
    os.makedirs(OUT, exist_ok=True)
    big = draw(1024)
    big.save(os.path.join(OUT, 'app.png'))
    big.save(os.path.join(OUT, 'app.ico'), sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    big.save(os.path.join(OUT, 'app.icns'))
    big.resize((64, 64), Image.LANCZOS).save(os.path.join(HERE, '..', 'web', 'favicon.png'))
    print('ok')
