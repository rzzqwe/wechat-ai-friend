"""Original geometric cat stickers. MIT. No downloaded artwork or fonts."""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
SIZE, SCALE = 512, 4
INK, CREAM, PINK = '#50382f', '#fff5df', '#ffa9b5'


class Drawing:
    def __init__(self):
        self.image = Image.new('RGBA', (SIZE * SCALE, SIZE * SCALE))
        self.draw = ImageDraw.Draw(self.image)
        self.svg = []

    def ellipse(self, box, fill, stroke=INK, width=8):
        x1, y1, x2, y2 = box
        self.draw.ellipse(tuple(round(value * SCALE) for value in box), fill=fill,
            outline=stroke, width=width * SCALE)
        self.svg.append(f'<ellipse cx="{(x1+x2)/2}" cy="{(y1+y2)/2}" rx="{(x2-x1)/2}" ry="{(y2-y1)/2}" fill="{fill}" stroke="{stroke}" stroke-width="{width}"/>')

    def polygon(self, points, fill, stroke=INK, width=8):
        scaled = [(round(x * SCALE), round(y * SCALE)) for x, y in points]
        self.draw.polygon(scaled, fill=fill)
        self.draw.line(scaled + [scaled[0]], fill=stroke, width=width * SCALE, joint='curve')
        value = ' '.join(f'{x},{y}' for x, y in points)
        self.svg.append(f'<polygon points="{value}" fill="{fill}" stroke="{stroke}" stroke-width="{width}" stroke-linejoin="round"/>')

    def line(self, points, stroke=INK, width=8):
        self.draw.line([(round(x * SCALE), round(y * SCALE)) for x, y in points], fill=stroke,
            width=width * SCALE, joint='curve')
        value = ' '.join(f'{x},{y}' for x, y in points)
        self.svg.append(f'<polyline points="{value}" fill="none" stroke="{stroke}" stroke-width="{width}" stroke-linecap="round" stroke-linejoin="round"/>')

    def save(self, png, svg):
        self.image.resize((SIZE, SIZE), Image.Resampling.LANCZOS).save(png)
        body = '\n'.join(self.svg)
        svg.write_text(f'<!-- SPDX-License-Identifier: MIT; original project geometry -->\n<svg xmlns="http://www.w3.org/2000/svg" width="512" height="512" viewBox="0 0 512 512">\n{body}\n</svg>\n', encoding='utf-8')


def star(x, y, radius=20):
    return [(round(x + math.cos(-math.pi/2 + i*math.pi/5)*(radius if i % 2 == 0 else radius*0.42)),
             round(y + math.sin(-math.pi/2 + i*math.pi/5)*(radius if i % 2 == 0 else radius*0.42))) for i in range(10)]


def cat(mood):
    d = Drawing()
    d.polygon([(118,186),(122,83),(220,134)], CREAM)
    d.polygon([(292,134),(390,83),(394,186)], CREAM)
    d.polygon([(144,150),(146,113),(182,137)], PINK, PINK, 2)
    d.polygon([(330,137),(366,113),(368,150)], PINK, PINK, 2)
    d.ellipse((104,124,408,410), CREAM)
    if mood in ('happy','goodnight'):
        direction = -1 if mood == 'happy' else 1
        for x in (188,312):
            d.line([(x-20,228),(x,228+direction*13),(x+20,228)])
    else:
        for x in (188,312):
            d.ellipse((x-8,210,x+8,237), INK, INK, 2)
    d.ellipse((136,247,178,270), PINK, PINK, 2)
    d.ellipse((334,247,376,270), PINK, PINK, 2)
    d.polygon([(246,248),(266,248),(256,260)], PINK, PINK, 2)
    d.line([(232,274),(245,287),(256,278),(267,287),(280,274)], width=6)
    for y in (273,291):
        d.line([(117,y),(158,y+4)], width=5)
        d.line([(354,y+4),(395,y)], width=5)
    if mood == 'hello':
        d.ellipse((388,251,468,344), CREAM)
        d.line([(449,245),(460,222)], '#e99b59', 7)
        d.line([(464,267),(487,256)], '#e99b59', 7)
    elif mood == 'happy':
        d.ellipse((224,307,288,353), '#f07888')
        d.polygon(star(79,100), '#ffd56c', '#d4a849', 4)
        d.polygon(star(442,122), '#ffd56c', '#d4a849', 4)
    elif mood == 'hug':
        d.polygon([(256,407),(180,346),(175,326),(187,305),(211,300),(233,310),(256,334),
                   (279,310),(301,300),(325,305),(337,326),(332,346)], '#f586a3')
        d.ellipse((140,332,201,380), CREAM)
        d.ellipse((311,332,372,380), CREAM)
    elif mood == 'goodnight':
        d.ellipse((393,42,479,128), '#ffdb77', '#d7ad47', 5)
        d.polygon(star(73,119,17), '#ffdb77', '#d7ad47', 3)
        d.line([(185,422),(327,422)], '#d6c3ef', 20)
    elif mood == 'okay':
        d.ellipse((346,324,458,436), '#a6dfb4', '#3f8b58', 6)
        d.line([(370,381),(394,405),(434,357)], '#276d3e', 12)
    elif mood == 'confused':
        d.line([(419,107),(432,89),(452,89),(468,101),(467,119),(444,138),(443,153)], '#916ca8', 9)
        d.ellipse((437,170,449,182), '#916ca8', '#916ca8', 2)
    return d


def generate(root=ROOT):
    folder = Path(root) / 'assets/stickers'
    vectors = folder / 'sources'
    vectors.mkdir(parents=True, exist_ok=True)
    records = []
    for mood, label in [('hello','你好'),('happy','开心'),('hug','抱抱'),('goodnight','晚安'),('okay','收到'),('confused','疑惑')]:
        png, svg = folder / (mood + '.png'), vectors / (mood + '.svg')
        cat(mood).save(png, svg)
        records.append({'file': png.name, 'label': label, 'license': 'MIT',
                        'source_type': 'original-procedural-geometry',
                        'source_svg': 'sources/' + svg.name, 'generator': 'scripts/generate_builtin_stickers.py',
                        'uses_external_artwork': False, 'uses_fonts': False,
                        'sha256': hashlib.sha256(png.read_bytes()).hexdigest()})
    (folder / 'builtin.json').write_text(json.dumps(records, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return records


if __name__ == '__main__':
    print('Generated', len(generate()), 'original MIT cat stickers.')
