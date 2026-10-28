"""Generatore di QR code minimale (ISO/IEC 18004), senza dipendenze esterne.

Modalità byte, correzione d'errore M, versioni 1–20 (fino a ~ 400 caratteri). Basato sull'algoritmo
di riferimento di Project Nayuki (licenza MIT). Uso: qr_matrix("http://…") -> lista di righe di bool.
"""
from .i18n import tr

# correzione M: codeword ECC per blocco e numero di blocchi, per versione (indice = versione)
_ECC_PER_BLOCK_M = (-1, 10, 16, 26, 18, 24, 16, 18, 22, 22, 26, 30, 22, 22, 24, 24, 28, 28, 26, 26, 26)
_NUM_BLOCKS_M = (-1, 1, 1, 1, 2, 2, 4, 4, 4, 5, 5, 5, 8, 9, 9, 10, 10, 11, 13, 14, 16)
_FORMAT_BITS_M = 0
MAX_VERSION = 20


def _gf_mul(x, y):
    z = 0
    for i in reversed(range(8)):
        z = (z << 1) ^ ((z >> 7) * 0x11D)
        z ^= ((y >> i) & 1) * x
    return z


def _rs_divisor(degree):
    result = [0] * (degree - 1) + [1]
    root = 1
    for _ in range(degree):
        for j in range(degree):
            result[j] = _gf_mul(result[j], root)
            if j + 1 < degree:
                result[j] ^= result[j + 1]
        root = _gf_mul(root, 0x02)
    return result


def _rs_remainder(data, divisor):
    result = [0] * len(divisor)
    for b in data:
        factor = b ^ result.pop(0)
        result.append(0)
        for i, coef in enumerate(divisor):
            result[i] ^= _gf_mul(coef, factor)
    return result


def _raw_modules(ver):
    result = (16 * ver + 128) * ver + 64
    if ver >= 2:
        na = ver // 7 + 2
        result -= (25 * na - 10) * na - 55
        if ver >= 7:
            result -= 36
    return result


def _data_codewords(ver):
    return _raw_modules(ver) // 8 - _ECC_PER_BLOCK_M[ver] * _NUM_BLOCKS_M[ver]


def _align_positions(ver, size):
    if ver == 1:
        return []
    na = ver // 7 + 2
    step = (ver * 8 + na * 3 + 5) // (na * 4 - 4) * 2
    result = [size - 7 - i * step for i in range(na - 1)] + [6]
    return list(reversed(result))


def qr_matrix(text):
    data = text.encode("utf-8")
    for ver in range(1, MAX_VERSION + 1):
        cc_bits = 8 if ver <= 9 else 16
        if 4 + cc_bits + len(data) * 8 <= _data_codewords(ver) * 8:
            break
    else:
        raise ValueError(tr("testo troppo lungo per il QR code"))
    cap = _data_codewords(ver) * 8
    bits = []

    def put(val, n):
        bits.extend((val >> i) & 1 for i in reversed(range(n)))
    put(0b0100, 4)
    put(len(data), cc_bits)
    for b in data:
        put(b, 8)
    put(0, min(4, cap - len(bits)))
    put(0, (-len(bits)) % 8)
    pad = 0xEC
    while len(bits) < cap:
        put(pad, 8)
        pad ^= 0xEC ^ 0x11
    codewords = [int("".join(map(str, bits[i:i + 8])), 2) for i in range(0, len(bits), 8)]

    # blocchi ECC e interleaving
    nb, ecl = _NUM_BLOCKS_M[ver], _ECC_PER_BLOCK_M[ver]
    raw = _raw_modules(ver) // 8
    n_short = nb - raw % nb
    short_len = raw // nb
    div = _rs_divisor(ecl)
    blocks, k = [], 0
    for i in range(nb):
        dat = codewords[k:k + short_len - ecl + (0 if i < n_short else 1)]
        k += len(dat)
        ecc = _rs_remainder(dat, div)
        if i < n_short:
            dat = dat + [0]
        blocks.append(dat + ecc)
    final = []
    for i in range(len(blocks[0])):
        for j, blk in enumerate(blocks):
            if i != short_len - ecl or j >= n_short:
                final.append(blk[i])

    size = ver * 4 + 17
    mod = [[False] * size for _ in range(size)]
    fun = [[False] * size for _ in range(size)]

    def setf(x, y, dark):
        mod[y][x] = dark
        fun[y][x] = True
    for i in range(size):
        setf(6, i, i % 2 == 0)
        setf(i, 6, i % 2 == 0)
    for cx, cy in ((3, 3), (size - 4, 3), (3, size - 4)):
        for dy in range(-4, 5):
            for dx in range(-4, 5):
                x, y = cx + dx, cy + dy
                if 0 <= x < size and 0 <= y < size:
                    setf(x, y, max(abs(dx), abs(dy)) not in (2, 4))
    pos = _align_positions(ver, size)
    last = len(pos) - 1
    for i, ax in enumerate(pos):
        for j, ay in enumerate(pos):
            if (i == 0 and j == 0) or (i == 0 and j == last) or (i == last and j == 0):
                continue
            for dy in range(-2, 3):
                for dx in range(-2, 3):
                    setf(ax + dx, ay + dy, max(abs(dx), abs(dy)) != 1)

    def draw_format(mask):
        d = _FORMAT_BITS_M << 3 | mask
        rem = d
        for _ in range(10):
            rem = (rem << 1) ^ ((rem >> 9) * 0x537)
        fb = (d << 10 | rem) ^ 0x5412
        g = lambda i: (fb >> i) & 1 == 1
        for i in range(6):
            setf(8, i, g(i))
        setf(8, 7, g(6))
        setf(8, 8, g(7))
        setf(7, 8, g(8))
        for i in range(9, 15):
            setf(14 - i, 8, g(i))
        for i in range(8):
            setf(size - 1 - i, 8, g(i))
        for i in range(8, 15):
            setf(8, size - 15 + i, g(i))
        setf(8, size - 8, True)
    draw_format(0)       # riserva le aree del formato
    if ver >= 7:
        rem = ver
        for _ in range(12):
            rem = (rem << 1) ^ ((rem >> 11) * 0x1F25)
        vb = ver << 12 | rem
        for i in range(18):
            bit = (vb >> i) & 1 == 1
            a, b = size - 11 + i % 3, i // 3
            setf(a, b, bit)
            setf(b, a, bit)

    # dati a zig-zag
    i = 0
    right = size - 1
    total = len(final) * 8
    while right >= 1:
        if right == 6:
            right = 5
        for vert in range(size):
            for j in range(2):
                x = right - j
                upward = ((right + 1) & 2) == 0
                y = size - 1 - vert if upward else vert
                if not fun[y][x] and i < total:
                    mod[y][x] = (final[i >> 3] >> (7 - (i & 7))) & 1 == 1
                    i += 1
        right -= 2

    masks = (lambda x, y: (x + y) % 2 == 0, lambda x, y: y % 2 == 0, lambda x, y: x % 3 == 0,
             lambda x, y: (x + y) % 3 == 0, lambda x, y: (x // 3 + y // 2) % 2 == 0,
             lambda x, y: x * y % 2 + x * y % 3 == 0, lambda x, y: (x * y % 2 + x * y % 3) % 2 == 0,
             lambda x, y: ((x + y) % 2 + x * y % 3) % 2 == 0)

    def apply(m):
        f = masks[m]
        for y in range(size):
            for x in range(size):
                if not fun[y][x] and f(x, y):
                    mod[y][x] = not mod[y][x]

    def penalty():
        p = 0
        for rows in (mod, [list(c) for c in zip(*mod)]):
            for row in rows:
                run, prev = 0, None
                for c in row:
                    if c == prev:
                        run += 1
                    else:
                        if run >= 5:
                            p += run - 2
                        run, prev = 1, c
                if run >= 5:
                    p += run - 2
        for y in range(size - 1):
            for x in range(size - 1):
                c = mod[y][x]
                if c == mod[y][x + 1] == mod[y + 1][x] == mod[y + 1][x + 1]:
                    p += 3
        dark = sum(sum(r) for r in mod)
        p += abs(dark * 20 - size * size * 10) // (size * size) * 10
        return p

    best, best_p = 0, None
    for m in range(8):
        apply(m)
        draw_format(m)
        pv = penalty()
        if best_p is None or pv < best_p:
            best, best_p = m, pv
        apply(m)          # XOR: annulla
    apply(best)
    draw_format(best)
    return mod


def qr_image(text, scale=6, border=4):
    """QImage in bianco e nero del QR code."""
    from PySide6.QtGui import QColor, QImage, QPainter
    m = qr_matrix(text)
    n = len(m)
    side = (n + 2 * border) * scale
    img = QImage(side, side, QImage.Format_RGB32)
    img.fill(QColor(255, 255, 255))
    p = QPainter(img)
    p.setPen(QColor(0, 0, 0, 0))
    p.setBrush(QColor(0, 0, 0))
    for y, row in enumerate(m):
        for x, dark in enumerate(row):
            if dark:
                p.drawRect((x + border) * scale, (y + border) * scale, scale, scale)
    p.end()
    return img
