"""Hand-built 3x5 bitmap pixel font with drop shadow.

Anti-aliased vector text is the fastest way to make pixel art look
amateurish — every string in the game goes through this instead.
Glyphs render at integer scales so they stay crisp through the CRT.
"""
import pygame

_G = {
    "A": ("010", "101", "111", "101", "101"),
    "B": ("110", "101", "110", "101", "110"),
    "C": ("011", "100", "100", "100", "011"),
    "D": ("110", "101", "101", "101", "110"),
    "E": ("111", "100", "110", "100", "111"),
    "F": ("111", "100", "110", "100", "100"),
    "G": ("011", "100", "101", "101", "011"),
    "H": ("101", "101", "111", "101", "101"),
    "I": ("111", "010", "010", "010", "111"),
    "J": ("001", "001", "001", "101", "010"),
    "K": ("101", "101", "110", "101", "101"),
    "L": ("100", "100", "100", "100", "111"),
    "M": ("101", "111", "111", "101", "101"),
    "N": ("110", "101", "101", "101", "101"),
    "O": ("010", "101", "101", "101", "010"),
    "P": ("110", "101", "110", "100", "100"),
    "Q": ("010", "101", "101", "110", "011"),
    "R": ("110", "101", "110", "101", "101"),
    "S": ("011", "100", "010", "001", "110"),
    "T": ("111", "010", "010", "010", "010"),
    "U": ("101", "101", "101", "101", "011"),
    "V": ("101", "101", "101", "010", "010"),
    "W": ("101", "101", "111", "111", "101"),
    "X": ("101", "101", "010", "101", "101"),
    "Y": ("101", "101", "010", "010", "010"),
    "Z": ("111", "001", "010", "100", "111"),
    "0": ("111", "101", "101", "101", "111"),
    "1": ("010", "110", "010", "010", "111"),
    "2": ("110", "001", "010", "100", "111"),
    "3": ("110", "001", "010", "001", "110"),
    "4": ("101", "101", "111", "001", "001"),
    "5": ("111", "100", "110", "001", "110"),
    "6": ("011", "100", "110", "101", "010"),
    "7": ("111", "001", "010", "010", "010"),
    "8": ("010", "101", "010", "101", "010"),
    "9": ("010", "101", "011", "001", "110"),
    " ": ("000", "000", "000", "000", "000"),
    ".": ("000", "000", "000", "000", "010"),
    ",": ("000", "000", "000", "010", "100"),
    ":": ("000", "010", "000", "010", "000"),
    "!": ("010", "010", "010", "000", "010"),
    "?": ("110", "001", "010", "000", "010"),
    "-": ("000", "000", "111", "000", "000"),
    "+": ("000", "010", "111", "010", "000"),
    "=": ("000", "111", "000", "111", "000"),
    "/": ("001", "001", "010", "100", "100"),
    "\\": ("100", "100", "010", "001", "001"),
    "(": ("001", "010", "010", "010", "001"),
    ")": ("100", "010", "010", "010", "100"),
    "[": ("011", "010", "010", "010", "011"),
    "]": ("110", "010", "010", "010", "110"),
    "'": ("010", "010", "000", "000", "000"),
    '"': ("101", "101", "000", "000", "000"),
    "%": ("101", "001", "010", "100", "101"),
    "<": ("001", "010", "100", "010", "001"),
    ">": ("100", "010", "001", "010", "100"),
    "_": ("000", "000", "000", "000", "111"),
    "*": ("000", "101", "010", "101", "000"),
    "#": ("101", "111", "101", "111", "101"),
}

_TRANSLATE = str.maketrans({
    "—": "-", "–": "-", "·": ".", "…": ".", "×": "x", "→": ">", "←": "<",
    "ä": "a", "ö": "o", "ü": "u", "Ä": "A", "Ö": "O", "Ü": "U", "ß": "s",
    "∞": "#",
})

_SHADOW = (14, 10, 12)


class PixelFont:
    """Drop-in replacement for the pygame font API we use:
    render(text, antialias, color) -> Surface (antialias is ignored).
    outline=True draws a full 1px dark outline — readable on any
    background (worm names, HUD labels over bright terrain)."""

    def __init__(self, scale=1, shadow=True, outline=False):
        self.scale = scale
        self.shadow = shadow and not outline
        self.outline = outline
        self._cache = {}

    def get_height(self):
        return 5 * self.scale + (2 if self.outline else
                                 1 if self.shadow else 0)

    def size(self, text):
        n = len(text)
        pad = 2 if self.outline else (1 if self.shadow else 0)
        return (max(0, n * 4 - 1) * self.scale + pad,
                5 * self.scale + pad)

    @staticmethod
    def _stamp(surf, text, sc, col, offx, offy):
        """Fill the glyph pixels of one text layer at the given offset."""
        for ci, ch in enumerate(text):
            rows = _G.get(ch, _G["?"])
            cx = ci * 4 * sc + offx
            for ry, row in enumerate(rows):
                for rx, bit in enumerate(row):
                    if bit == "1":
                        surf.fill(col, (cx + rx * sc, ry * sc + offy,
                                        sc, sc))

    def render(self, text, a, b=None):
        color = tuple(b if b is not None else a)
        text = str(text).translate(_TRANSLATE).upper()
        key = (text, color)
        surf = self._cache.get(key)
        if surf is not None:
            return surf
        sc = self.scale
        pad = 2 if self.outline else (1 if self.shadow else 0)
        w = max(1, (len(text) * 4 - 1) * sc + pad)
        h = 5 * sc + pad
        surf = pygame.Surface((w, h), pygame.SRCALPHA)
        if self.outline:
            # stamp the dark layer once, blit it at the 8 ring offsets —
            # identical pixels to 8 separate stamps, at a fraction of the
            # cost (glyph pixels are fully opaque, the rest fully clear)
            mask = pygame.Surface((w, h), pygame.SRCALPHA)
            self._stamp(mask, text, sc, _SHADOW, 0, 0)
            for off in ((0, 0), (1, 0), (2, 0), (0, 1), (2, 1),
                        (0, 2), (1, 2), (2, 2)):
                surf.blit(mask, off)
            self._stamp(surf, text, sc, color, 1, 1)
        elif self.shadow:
            self._stamp(surf, text, sc, _SHADOW, sc, sc)
            self._stamp(surf, text, sc, color, 0, 0)
        else:
            self._stamp(surf, text, sc, color, 0, 0)
        if len(self._cache) > 3000:
            self._cache.clear()
        self._cache[key] = surf
        return surf
