"""A small PDF writer — text, lines, boxes and a PNG logo — so a student's
phone downloads a real file (said 28 October 2026: "the invoice should be
downloaded as a file"). The project has no PDF library and the release takes
no new packages; an invoice needs nothing more than this.

The fonts are the PDF's own Helvetica and Courier, which every reader has, in
the Windows-1252 encoding; their widths are below so text can be measured,
right-aligned and wrapped.
"""

import struct
import zlib

A4 = (595.28, 841.89)

_HELVETICA = {
    ' ': 278, '!': 278, '"': 355, '#': 556, '$': 556, '%': 889, '&': 667, "'": 191, '(': 333, ')': 333,
    '*': 389, '+': 584, ',': 278, '-': 333, '.': 278, '/': 278, ':': 278, ';': 278, '<': 584, '=': 584,
    '>': 584, '?': 556, '@': 1015, 'A': 667, 'B': 667, 'C': 722, 'D': 722, 'E': 667, 'F': 611, 'G': 778,
    'H': 722, 'I': 278, 'J': 500, 'K': 667, 'L': 556, 'M': 833, 'N': 722, 'O': 778, 'P': 667, 'Q': 778,
    'R': 722, 'S': 667, 'T': 611, 'U': 722, 'V': 667, 'W': 944, 'X': 667, 'Y': 667, 'Z': 611, '[': 278,
    '\\': 278, ']': 278, '^': 469, '_': 556, '`': 333, 'a': 556, 'b': 556, 'c': 500, 'd': 556, 'e': 556,
    'f': 278, 'g': 556, 'h': 556, 'i': 222, 'j': 222, 'k': 500, 'l': 222, 'm': 833, 'n': 556, 'o': 556,
    'p': 556, 'q': 556, 'r': 333, 's': 500, 't': 278, 'u': 556, 'v': 500, 'w': 722, 'x': 500, 'y': 500,
    'z': 500, '{': 334, '|': 260, '}': 334, '~': 584, '·': 278, '—': 1000, '–': 556,
}
_HELVETICA_BOLD = {
    **_HELVETICA,
    '!': 333, '"': 474, '&': 722, "'": 238, ':': 333, ';': 333, '?': 611, '@': 975, 'A': 722, 'B': 722,
    'J': 556, 'K': 722, 'L': 611, '[': 333, ']': 333, '^': 584, 'b': 611, 'c': 556, 'd': 611, 'f': 333,
    'g': 611, 'h': 611, 'i': 278, 'j': 278, 'k': 556, 'l': 278, 'm': 889, 'n': 611, 'o': 611, 'p': 611,
    'q': 611, 'r': 389, 's': 556, 't': 333, 'u': 611, 'v': 556, 'w': 778, 'x': 556, 'y': 556, '{': 389,
    '|': 280, '}': 389,
}
for _digit in '0123456789':
    _HELVETICA[_digit] = _HELVETICA_BOLD[_digit] = 556

FONTS = {'regular': ('F1', 'Helvetica', _HELVETICA), 'bold': ('F2', 'Helvetica-Bold', _HELVETICA_BOLD),
         'mono': ('F3', 'Courier-Bold', None)}


def width(text, size, font='regular'):
    """The width of `text` in points."""
    table = FONTS[font][2]
    if table is None:                               # Courier: every character 600
        return len(text) * 600 * size / 1000
    return sum(table.get(ch, 556) for ch in text) * size / 1000


def wrap(text, size, max_width, font='regular'):
    """`text` broken into lines no wider than `max_width`."""
    lines = []
    for paragraph in str(text).splitlines() or ['']:
        line = ''
        for word in paragraph.split(' '):
            trial = f'{line} {word}'.strip()
            if line and width(trial, size, font) > max_width:
                lines.append(line)
                line = word
            else:
                line = trial
        lines.append(line)
    return lines


def _escape(text):
    raw = str(text).encode('cp1252', 'replace')
    return raw.replace(b'\\', b'\\\\').replace(b'(', b'\\(').replace(b')', b'\\)')


def _rgb(colour):
    colour = colour.lstrip('#')
    return ' '.join(f'{int(colour[i:i + 2], 16) / 255:.3f}' for i in (0, 2, 4))


def png_image(path):
    """An 8-bit RGB or greyscale, non-interlaced PNG as a PDF image: its
    compressed data goes in as it is, the PNG predictors named."""
    data = open(path, 'rb').read()
    if data[:8] != b'\x89PNG\r\n\x1a\n':
        raise ValueError('Not a PNG.')
    pos, idat, header = 8, b'', None
    while pos < len(data):
        length, kind = struct.unpack('>I4s', data[pos:pos + 8])
        chunk = data[pos + 8:pos + 8 + length]
        if kind == b'IHDR':
            header = struct.unpack('>IIBBBBB', chunk)
        elif kind == b'IDAT':
            idat += chunk
        pos += 12 + length
    w, h, depth, colour, _, _, interlace = header
    if depth != 8 or interlace or colour not in (0, 2):
        raise ValueError('Only 8-bit RGB or grey, non-interlaced PNGs.')
    colours = 3 if colour == 2 else 1
    return {'width': w, 'height': h, 'data': idat, 'colours': colours}


class Document:
    """Pages of drawing operations, y measured from the top of the page."""

    def __init__(self, size=A4):
        self.page_w, self.page_h = size
        self.pages, self.images = [], {}
        self.new_page()

    def new_page(self):
        self.ops = []
        self.pages.append(self.ops)

    def _y(self, y):
        return self.page_h - y

    def text(self, x, y, text, *, size=10, font='regular', colour='#1E293B', align='left'):
        if text is None or text == '':
            return
        text = str(text)
        if align == 'right':
            x -= width(text, size, font)
        elif align == 'center':
            x -= width(text, size, font) / 2
        name = FONTS[font][0]
        self.ops.append(f'BT {_rgb(colour)} rg /{name} {size} Tf {x:.2f} {self._y(y):.2f} Td ('.encode()
                        + _escape(text) + b') Tj ET')

    def line(self, x1, y1, x2, y2, *, colour='#CBD5E1', thickness=.6):
        self.ops.append(f'{_rgb(colour)} RG {thickness} w {x1:.2f} {self._y(y1):.2f} m {x2:.2f} '
                        f'{self._y(y2):.2f} l S'.encode())

    def rect(self, x, y, w, h, *, fill=None, stroke=None, thickness=.6):
        path = f'{x:.2f} {self._y(y + h):.2f} {w:.2f} {h:.2f} re'
        if fill and stroke:
            self.ops.append(f'{_rgb(fill)} rg {_rgb(stroke)} RG {thickness} w {path} B'.encode())
        elif fill:
            self.ops.append(f'{_rgb(fill)} rg {path} f'.encode())
        else:
            self.ops.append(f'{_rgb(stroke or "#CBD5E1")} RG {thickness} w {path} S'.encode())

    def image(self, image, x, y, w, h):
        key = f'Im{len(self.images) + 1}'
        self.images[key] = image
        self.ops.append(f'q {w:.2f} 0 0 {h:.2f} {x:.2f} {self._y(y + h):.2f} cm /{key} Do Q'.encode())

    def output(self, *, title=''):
        objects = []

        def add(body):
            objects.append(body)
            return len(objects)

        catalog, pages_id = add(None), add(None)
        fonts = {name: add(f'<< /Type /Font /Subtype /Type1 /BaseFont /{base} /Encoding /WinAnsiEncoding >>'.encode())
                 for name, base, _ in FONTS.values()}
        images = {}
        for key, image in self.images.items():
            colourspace = '/DeviceRGB' if image['colours'] == 3 else '/DeviceGray'
            images[key] = add(
                f'<< /Type /XObject /Subtype /Image /Width {image["width"]} /Height {image["height"]} '
                f'/ColorSpace {colourspace} /BitsPerComponent 8 /Filter /FlateDecode /DecodeParms << /Predictor 15 '
                f'/Colors {image["colours"]} /BitsPerComponent 8 /Columns {image["width"]} >> '
                f'/Length {len(image["data"])} >>\nstream\n'.encode() + image['data'] + b'\nendstream')
        resources = ('<< /Font << ' + ' '.join(f'/{name} {ref} 0 R' for name, ref in fonts.items()) + ' >> '
                     '/XObject << ' + ' '.join(f'/{key} {ref} 0 R' for key, ref in images.items()) + ' >> >>')
        kids = []
        for ops in self.pages:
            content = zlib.compress(b'\n'.join(ops))
            stream = add(f'<< /Length {len(content)} /Filter /FlateDecode >>\nstream\n'.encode() + content
                         + b'\nendstream')
            kids.append(add(f'<< /Type /Page /Parent {pages_id} 0 R /MediaBox [0 0 {self.page_w} {self.page_h}] '
                            f'/Resources {resources} /Contents {stream} 0 R >>'.encode()))
        objects[pages_id - 1] = (f'<< /Type /Pages /Kids [{" ".join(f"{k} 0 R" for k in kids)}] '
                                 f'/Count {len(kids)} >>').encode()
        objects[catalog - 1] = f'<< /Type /Catalog /Pages {pages_id} 0 R >>'.encode()
        info = add(b'<< /Title (' + _escape(title) + b') /Producer (EduTrack) >>')

        out, offsets = bytearray(b'%PDF-1.4\n%\xe2\xe3\xcf\xd3\n'), []
        for number, body in enumerate(objects, start=1):
            offsets.append(len(out))
            out += f'{number} 0 obj\n'.encode() + body + b'\nendobj\n'
        xref = len(out)
        out += f'xref\n0 {len(objects) + 1}\n0000000000 65535 f \n'.encode()
        out += b''.join(f'{offset:010d} 00000 n \n'.encode() for offset in offsets)
        out += (f'trailer\n<< /Size {len(objects) + 1} /Root {catalog} 0 R /Info {info} 0 R >>\n'
                f'startxref\n{xref}\n%%EOF\n').encode()
        return bytes(out)
