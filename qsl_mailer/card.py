"""QSL card images.

Three ways to get a card, from zero effort to full control:
  generated   nothing to configure: callsign, operator, grid, QTH, country and zones from
              Wavelog's Station Location on a themed background
  background  any photo (background.jpg next to config.ini, or [card] background): cropped,
              darkened and used behind the same generated layout
  design      a finished card design ([card] image) with a free area for the QSO data ([card] box)
"""

import io
import random
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

from . import config as conf

ASSETS = Path(__file__).resolve().parent / 'assets'
SIZE = (1650, 1050)  # 11:7, the usual QSL card shape
BOX = (845, 705, 1605, 1005)
INK, LABEL = (22, 18, 43), (122, 58, 99)

THEMES = {  # top, middle, bottom of the sky; text accents
    'dusk': {'sky': [(14, 22, 54), (44, 36, 87), (122, 58, 99), (224, 112, 74)], 'accent': (255, 207, 110), 'soft': (243, 226, 168)},
    'ocean': {'sky': [(6, 20, 40), (12, 52, 92), (20, 108, 148), (88, 196, 200)], 'accent': (255, 214, 120), 'soft': (206, 236, 240)},
    'forest': {'sky': [(10, 24, 18), (22, 56, 40), (46, 104, 64), (196, 170, 92)], 'accent': (255, 220, 130), 'soft': (226, 236, 200)},
}


def format_freq(hz):
    """Always at least kHz (14.200, 7.074); finer only when the QSO has it (14.0745)."""
    if not hz:
        return ''
    whole, frac = f'{int(hz) / 1e6:.6f}'.split('.')
    frac = frac.rstrip('0').ljust(3, '0')
    return f'{whole}.{frac[:4]}'


def format_grid(grid):
    grid = (grid or '').strip()
    return grid[:4].upper() + grid[4:6].lower() if grid else ''


class Card:
    def __init__(self, cfg, stations):
        self.stations = stations
        self.quality = int(conf.get(cfg, 'card', 'jpeg_quality', 88))
        self.bold = str(ASSETS / 'Montserrat-Bold.ttf')
        self.semi = str(ASSETS / 'Montserrat-SemiBold.ttf')
        self.theme = THEMES.get(conf.get(cfg, 'card', 'theme', 'dusk'), THEMES['dusk'])
        self.tagline = conf.get(cfg, 'card', 'tagline', 'Tnx for QSO · 73!')

        design = conf.get(cfg, 'card', 'image')
        background = conf.get(cfg, 'card', 'background')
        if not background and (cfg.path.parent / 'background.jpg').exists():
            background = 'background.jpg'
        if design:
            self.mode = 'design'
            self.design = Image.open(conf.resolve(cfg, design)).convert('RGB')
            self.box = [int(v) for v in conf.csv_list(conf.get(cfg, 'card', 'box', ','.join(map(str, BOX))))]
            self.draw_box = conf.get_bool(cfg, 'card', 'draw_box', False)
            # what the design shows; by default the first Station Location in station_ids
            main = next(iter(stations.values()), {}) if stations else {}
            self.printed_call = (conf.get(cfg, 'card', 'printed_callsign') or main.get('callsign') or '').upper()
            self.printed_grid = (conf.get(cfg, 'card', 'printed_grid') or main.get('grid') or '').upper()
        else:
            self.mode = 'background' if background else 'generated'
            self.background = self._photo(conf.resolve(cfg, background)) if background else self._sky()
            self.box = list(BOX)
            self.draw_box = True
        self._base = {}

    # --- drawing helpers ---

    def font(self, path, size):
        return ImageFont.truetype(path, size)

    def fit(self, text, path, size, width):
        f = self.font(path, size)
        while f.getlength(text) > width and size > 12:
            size -= 2
            f = self.font(path, size)
        return f

    def spaced(self, d, xy, text, font, fill, spacing, anchor='la'):
        """Text with letter spacing (Pillow has no tracking)."""
        width = sum(font.getlength(ch) for ch in text) + spacing * (len(text) - 1)
        x, y = xy
        if anchor[0] == 'r':
            x -= width
        for ch in text:
            d.text((x, y), ch, font=font, fill=fill, anchor='l' + anchor[1])
            x += font.getlength(ch) + spacing

    def _sky(self):
        """Vertical gradient with stars and faint radio waves."""
        w, h = SIZE
        stops = self.theme['sky']
        img = Image.new('RGB', SIZE)
        px = img.load()
        for y in range(h):
            t = y / (h - 1) * (len(stops) - 1)
            i = min(int(t), len(stops) - 2)
            f = t - i
            row = tuple(int(stops[i][c] + (stops[i + 1][c] - stops[i][c]) * f) for c in range(3))
            for x in range(w):
                px[x, y] = row
        d = ImageDraw.Draw(img, 'RGBA')
        rnd = random.Random(73)
        for _ in range(140):
            x, y, r = rnd.randrange(w), rnd.randrange(int(h * 0.55)), rnd.choice((1, 1, 2, 2, 3))
            d.ellipse((x - r, y - r, x + r, y + r), fill=(255, 255, 255, rnd.randrange(70, 200)))
        cx, cy = 140, h + 40  # waves rising from the bottom-left corner
        for i, r in enumerate(range(260, 1500, 150)):
            d.arc((cx - r, cy - r, cx + r, cy + r), 270, 360, fill=(255, 255, 255, max(14, 46 - i * 4)), width=4)
        return img

    def _photo(self, path):
        img = ImageOps.fit(Image.open(path).convert('RGB'), SIZE, Image.LANCZOS)
        shade = Image.new('L', SIZE)
        sd = ImageDraw.Draw(shade)
        for y in range(SIZE[1]):  # darker at the top (header text) and bottom (tagline)
            t = y / SIZE[1]
            sd.line((0, y, SIZE[0], y), fill=int(150 * max(0, 1 - t * 2.4) ** 1.3 + 140 * max(0, t - 0.55) ** 1.2 + 40))
        return Image.composite(Image.new('RGB', SIZE, (8, 10, 24)), img, shade)

    def _header(self, station):
        """Generated layout without QSO data; cached per station callsign + grid."""
        key = (station['callsign'], station['grid'], station.get('ref', ''))
        if key in self._base:
            return self._base[key]
        img = self.background.copy()
        layer = Image.new('RGBA', SIZE, (0, 0, 0, 0))
        d = ImageDraw.Draw(layer)
        accent, soft, white = self.theme['accent'], self.theme['soft'], (255, 255, 255)
        w, _ = SIZE

        call = station['callsign']
        cf = self.fit(call, self.bold, 210, 900)
        d.text((70, 60), call, font=cf, fill=white)
        call_bottom = 60 + cf.getbbox(call)[3]
        if station.get('operator'):
            self.spaced(d, (76, call_bottom + 30), f"OP. {station['operator'].upper()}", self.font(self.semi, 46), accent, 7)

        right = w - 70
        grid = format_grid(station['grid'])
        y = 70
        if grid:
            d.text((right, y), grid, font=self.font(self.bold, 96), fill=white, anchor='ra')
            y += 130
        place = ' · '.join(p for p in (station.get('city'), station.get('country')) if p)
        if place:
            d.text((right, y), place, font=self.fit(place, self.semi, 44, 640), fill=soft, anchor='ra')
            y += 62
        zones = ' · '.join(z for z in (f"CQ {station['cq']}" if station.get('cq') else '',
                                        f"ITU {station['itu']}" if station.get('itu') else '') if z)
        if zones:
            d.text((right, y), zones, font=self.font(self.semi, 44), fill=soft, anchor='ra')
            y += 62
        if station.get('ref'):
            d.text((right, y), station['ref'], font=self.font(self.bold, 44), fill=accent, anchor='ra')

        if self.tagline:
            tf = self.fit(self.tagline, self.bold, 64, self.box[0] - 120)
            d.text((60, SIZE[1] - 125), self.tagline, font=tf, fill=white)

        shadow = layer.filter(ImageFilter.GaussianBlur(6))  # soft shadow keeps text readable on photos
        dark = Image.new('RGBA', SIZE, (0, 0, 0, 0))
        dark.putalpha(shadow.getchannel('A').point(lambda a: int(a * 0.55)))
        img = Image.alpha_composite(img.convert('RGBA'), dark)
        img = Image.alpha_composite(img, layer)
        self._base[key] = img
        return img

    # --- card for one QSO ---

    def qso_station(self, qso):
        """The sending station as it was for this QSO (call and grid from the QSO, the rest from Wavelog)."""
        s = dict(self.stations.get(qso.get('station_id')) or next(iter(self.stations.values()), {}) or {})
        s['callsign'] = (qso.get('station_callsign') or s.get('callsign') or '').upper()
        s['grid'] = qso.get('my_grid') or s.get('grid') or ''
        refs = [f'{name} {ref.strip()}' for name, ref in (('POTA', qso.get('my_pota')), ('WWFF', qso.get('my_wwff')),
                                                          ('SOTA', qso.get('my_sota'))) if ref and ref.strip()]
        s['ref'] = ' · '.join(refs)
        return s

    def station_line(self, qso):
        """For a finished design: 'FROM SP4BL/P · KO13bc · POTA …' when the QSO differs from the print."""
        if self.mode != 'design':
            return ''
        s = self.qso_station(qso)
        grid = (s['grid'] or '').strip()
        other_grid = grid and self.printed_grid and grid[:6].upper() != self.printed_grid[:6]
        if (not self.printed_call or s['callsign'] == self.printed_call) and not other_grid and not s['ref']:
            return ''
        parts = ['FROM ' + s['callsign']] + ([format_grid(grid)] if other_grid else []) + ([s['ref']] if s['ref'] else [])
        return ' · '.join(parts)

    def render(self, qso):
        if self.mode == 'design':
            img = self.design.copy().convert('RGBA')
        else:
            img = self._header(self.qso_station(qso)).copy()
        x0, y0, x1, y1 = self.box
        if self.draw_box:
            box = Image.new('RGBA', img.size, (0, 0, 0, 0))
            ImageDraw.Draw(box).rounded_rectangle(self.box, radius=28, fill=(246, 244, 248, 245))
            img = Image.alpha_composite(img, box)
        d = ImageDraw.Draw(img)
        pad = 37
        inner = x1 - x0 - 2 * pad
        if self.draw_box:
            self.spaced(d, (x0 + pad, y0 + 32), 'CONFIRMING QSO', self.font(self.semi, 26), LABEL, 5)

        callsign = qso['callsign']
        cf = self.fit(callsign, self.bold, 64, inner - 360)
        d.text((x1 - pad, y0 + 26), callsign, font=cf, fill=INK, anchor='ra')
        line = self.station_line(qso)
        if line:
            sf = self.fit(line, self.semi, 22, inner - cf.getlength(callsign) - 30)
            d.text((x0 + pad, y0 + 74), line, font=sf, fill=LABEL)

        mode = qso.get('submode') or qso.get('mode') or ''
        band = (qso.get('band') or '').lower()
        if qso.get('sat_name'):
            band = f"{band} · {qso['sat_name']}" if band else qso['sat_name']
        t = qso['time_on']
        rows = ([('DATE', f'{t:%Y-%m-%d}'), ('UTC', f'{t:%H:%M}'), ('BAND', band or '—')],
                [('MODE', mode or '—'), ('RST', qso.get('rst_sent') or '—'), ('MHz', format_freq(qso.get('freq')) or '—')])
        widths = [0.40, 0.25, 0.35]
        lf = self.font(self.semi, 20)
        for r, items in enumerate(rows):
            ly = y0 + 112 + r * 92
            x = x0 + pad
            for (name, value), w in zip(items, widths):
                cw = inner * w
                d.text((x, ly), name, font=lf, fill=LABEL)
                d.text((x, ly + 26), value, font=self.fit(value, self.bold, 36, cw - 12), fill=INK)
                x += cw
        out = io.BytesIO()
        img.convert('RGB').save(out, 'JPEG', quality=self.quality, optimize=True)
        return out.getvalue()
