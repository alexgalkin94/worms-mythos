"""Tiny immediate-mode UI toolkit, drawn at grid resolution so the whole
interface lives inside the CRT glass like a proper arcade cabinet."""
import math

import pygame

from .constants import GRID_W, GRID_H, CELL_SCALE
from .pixelfont import PixelFont

# Noita-toned interface: parchment text on near-black panels with thin
# worn-bronze borders. No rounded corners, no anti-aliasing, ever.
ACCENT = (214, 168, 84)        # worn gold
ACCENT2 = (122, 168, 188)      # cold runic blue
FG = (224, 210, 178)           # parchment
DIM = (158, 144, 118)
BG = (12, 10, 9)
BG2 = (26, 22, 18)
EDGE = (94, 80, 56)
EDGE_HOT = (190, 152, 84)


class UI:
    """Immediate mode: call begin() each frame with events, then widgets."""
    def __init__(self, audio):
        self.audio = audio
        self.font = PixelFont(1)
        self.font_m = PixelFont(1)
        self.font_b = PixelFont(2)
        self.font_t = PixelFont(4)
        self.mx = self.my = 0
        self.clicked = False
        self.mouse_down = False
        self.typed = ""
        self.backspace = False
        self.hot = None
        self._hover_prev = None
        self.t = 0

    def begin(self, events):
        self.t += 1
        mx, my = pygame.mouse.get_pos()
        win = pygame.display.get_surface().get_size()
        self.mx = mx * GRID_W // max(1, win[0])
        self.my = my * GRID_H // max(1, win[1])
        self.clicked = False
        self.typed = ""
        self.backspace = False
        self.mouse_down = pygame.mouse.get_pressed()[0]
        for e in events:
            if e.type == pygame.MOUSEBUTTONDOWN and e.button == 1:
                self.clicked = True
            if e.type == pygame.TEXTINPUT:
                self.typed += e.text
            if e.type == pygame.KEYDOWN and e.key == pygame.K_BACKSPACE:
                self.backspace = True
        self.hot = None

    def _hover(self, rect):
        return rect.collidepoint(self.mx, self.my)

    # ------------------------------------------------------------- widgets
    def label(self, surf, x, y, text, color=FG, font=None, center=False):
        f = font or self.font_m
        s = f.render(text, True, color)
        if center:
            x -= s.get_width() // 2
        surf.blit(s, (x, y))
        return s.get_height()

    def title(self, surf, x, y, text, color=ACCENT):
        s = self.font_t.render(text, True, color)
        under = self.font_t.render(text, True, (52, 34, 14))
        surf.blit(under, (x - s.get_width() // 2, y + 2))
        surf.blit(s, (x - s.get_width() // 2, y))

    def button(self, surf, rect, text, enabled=True, accent=False, font=None):
        rect = pygame.Rect(rect)
        hov = self._hover(rect) and enabled
        if hov and self._hover_prev != text:
            self.audio.play("hover", 0.3)
            self._hover_prev = text
        elif not hov and self._hover_prev == text:
            self._hover_prev = None
        base = BG2 if not hov else (42, 35, 26)
        if accent:
            base = (52, 38, 18) if not hov else (76, 56, 24)
        pygame.draw.rect(surf, base, rect)
        edge = EDGE_HOT if (hov or accent) else EDGE
        pygame.draw.rect(surf, edge if enabled else (52, 46, 38), rect, 1)
        # worn corner ticks, Noita-style
        for cx, cy in (rect.topleft, (rect.right - 1, rect.top),
                       (rect.left, rect.bottom - 1),
                       (rect.right - 1, rect.bottom - 1)):
            surf.set_at((cx, cy), BG)
        f = font or self.font_m
        col = FG if enabled else (90, 90, 100)
        if hov:
            col = (255, 255, 255)
        s = f.render(text, True, col)
        surf.blit(s, (rect.centerx - s.get_width() // 2,
                      rect.centery - s.get_height() // 2))
        if hov and self.clicked:
            self.audio.play("click", 0.5)
            return True
        return False

    def selector(self, surf, rect, label, value, on_prev, on_next):
        rect = pygame.Rect(rect)
        self.label(surf, rect.x, rect.y - 1, label, DIM, self.font)
        body = pygame.Rect(rect.x, rect.y + 9, rect.w, rect.h - 9)
        pygame.draw.rect(surf, BG2, body)
        pygame.draw.rect(surf, EDGE, body, 1)
        lb = pygame.Rect(body.x, body.y, 12, body.h)
        rb = pygame.Rect(body.right - 12, body.y, 12, body.h)
        for b, ch, cb in ((lb, "<", on_prev), (rb, ">", on_next)):
            hov = self._hover(b)
            col = ACCENT if hov else DIM
            s = self.font_m.render(ch, True, col)
            surf.blit(s, (b.centerx - s.get_width() // 2,
                          b.centery - s.get_height() // 2))
            if hov and self.clicked:
                self.audio.play("click", 0.5)
                cb()
        s = self.font_m.render(str(value), True, FG)
        surf.blit(s, (body.centerx - s.get_width() // 2,
                      body.centery - s.get_height() // 2))

    def slider(self, surf, rect, label, value, lo=0.0, hi=1.0):
        """Returns possibly-updated value."""
        rect = pygame.Rect(rect)
        self.label(surf, rect.x, rect.y - 1, label, DIM, self.font)
        track = pygame.Rect(rect.x, rect.y + 12, rect.w, 4)
        pygame.draw.rect(surf, BG2, track)
        pygame.draw.rect(surf, EDGE, track, 1)
        frac = (value - lo) / (hi - lo) if hi > lo else 0
        fill = pygame.Rect(track.x + 1, track.y + 1, int((track.w - 2) * frac), 2)
        pygame.draw.rect(surf, ACCENT, fill)
        hx = track.x + int(track.w * frac)
        knob = pygame.Rect(hx - 1, track.y - 2, 3, 8)
        pygame.draw.rect(surf, FG, knob)
        zone = pygame.Rect(rect.x - 2, rect.y + 6, rect.w + 4, 16)
        if self.mouse_down and self._hover(zone):
            frac = max(0.0, min(1.0, (self.mx - track.x) / track.w))
            value = lo + frac * (hi - lo)
        return value

    def toggle(self, surf, rect, label, value):
        rect = pygame.Rect(rect)
        hov = self._hover(rect)
        pygame.draw.rect(surf, BG2 if not hov else (42, 35, 26), rect)
        pygame.draw.rect(surf, EDGE_HOT if hov else EDGE, rect, 1)
        box = pygame.Rect(rect.x + 4, rect.centery - 4, 8, 8)
        pygame.draw.rect(surf, BG, box)
        pygame.draw.rect(surf, DIM, box, 1)
        if value:
            pygame.draw.rect(surf, ACCENT, box.inflate(-4, -4))
        s = self.font_m.render(label, True, FG)
        surf.blit(s, (rect.x + 16, rect.centery - s.get_height() // 2))
        if hov and self.clicked:
            self.audio.play("click", 0.5)
            return not value
        return value

    def textinput(self, surf, rect, label, value, active, max_len=16,
                  upper=False):
        """Returns (value, active)."""
        rect = pygame.Rect(rect)
        self.label(surf, rect.x, rect.y - 1, label, DIM, self.font)
        body = pygame.Rect(rect.x, rect.y + 9, rect.w, rect.h - 9)
        hov = self._hover(body)
        pygame.draw.rect(surf, BG2, body)
        pygame.draw.rect(surf, EDGE_HOT if active else EDGE, body, 1)
        if self.clicked:
            active = hov
        if active:
            if self.typed:
                t = self.typed.upper() if upper else self.typed
                value = (value + t)[:max_len]
            if self.backspace:
                value = value[:-1]
        disp = value + ("_" if active and (self.t // 20) % 2 else "")
        s = self.font_m.render(disp, True, FG)
        surf.blit(s, (body.x + 4, body.centery - s.get_height() // 2))
        return value, active

    def panel(self, surf, rect, title=None):
        rect = pygame.Rect(rect)
        p = pygame.Surface((rect.w, rect.h), pygame.SRCALPHA)
        p.fill((10, 8, 7, 232))
        surf.blit(p, rect.topleft)
        pygame.draw.rect(surf, EDGE, rect, 1)
        # double-line top edge with corner ticks
        pygame.draw.line(surf, (56, 48, 34), (rect.x + 2, rect.y + 2),
                         (rect.right - 3, rect.y + 2))
        for cx, cy in (rect.topleft, (rect.right - 1, rect.top),
                       (rect.left, rect.bottom - 1),
                       (rect.right - 1, rect.bottom - 1)):
            surf.set_at((cx, cy), EDGE_HOT)
        if title:
            s = self.font_b.render(title, True, ACCENT)
            surf.blit(s, (rect.centerx - s.get_width() // 2, rect.y + 5))
