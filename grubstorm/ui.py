"""Tiny immediate-mode UI toolkit, drawn at grid resolution so the whole
interface lives inside the CRT glass like a proper arcade cabinet."""
import math

import pygame

from .constants import GRID_W, GRID_H, CELL_SCALE

ACCENT = (255, 170, 60)
ACCENT2 = (120, 200, 255)
FG = (235, 235, 245)
DIM = (140, 140, 165)
BG = (16, 16, 28)
BG2 = (28, 28, 46)


class UI:
    """Immediate mode: call begin() each frame with events, then widgets."""
    def __init__(self, audio):
        self.audio = audio
        self.font = pygame.font.Font(None, 12)
        self.font_m = pygame.font.Font(None, 16)
        self.font_b = pygame.font.Font(None, 24)
        self.font_t = pygame.font.Font(None, 44)
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
        glow = self.font_t.render(text, True,
                                  tuple(min(255, c + 40) for c in color))
        s = self.font_t.render(text, True, color)
        for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            sh = self.font_t.render(text, True, (30, 20, 10))
            surf.blit(sh, (x - sh.get_width() // 2 + dx, y + dy))
        surf.blit(s, (x - s.get_width() // 2, y))

    def button(self, surf, rect, text, enabled=True, accent=False, font=None):
        rect = pygame.Rect(rect)
        hov = self._hover(rect) and enabled
        if hov and self._hover_prev != text:
            self.audio.play("hover", 0.3)
            self._hover_prev = text
        elif not hov and self._hover_prev == text:
            self._hover_prev = None
        base = BG2 if not hov else (44, 44, 70)
        if accent:
            base = (70, 45, 20) if not hov else (110, 70, 25)
        pygame.draw.rect(surf, base, rect, border_radius=3)
        edge = ACCENT if (hov or accent) else (70, 70, 100)
        pygame.draw.rect(surf, edge if enabled else (50, 50, 60), rect, 1,
                         border_radius=3)
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
        pygame.draw.rect(surf, BG2, body, border_radius=3)
        pygame.draw.rect(surf, (70, 70, 100), body, 1, border_radius=3)
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
        pygame.draw.rect(surf, BG2, track, border_radius=2)
        frac = (value - lo) / (hi - lo) if hi > lo else 0
        fill = pygame.Rect(track.x, track.y, int(track.w * frac), 4)
        pygame.draw.rect(surf, ACCENT, fill, border_radius=2)
        hx = track.x + int(track.w * frac)
        knob = pygame.Rect(hx - 2, track.y - 2, 5, 8)
        pygame.draw.rect(surf, FG, knob, border_radius=2)
        zone = pygame.Rect(rect.x - 2, rect.y + 6, rect.w + 4, 16)
        if self.mouse_down and self._hover(zone):
            frac = max(0.0, min(1.0, (self.mx - track.x) / track.w))
            value = lo + frac * (hi - lo)
        return value

    def toggle(self, surf, rect, label, value):
        rect = pygame.Rect(rect)
        hov = self._hover(rect)
        pygame.draw.rect(surf, BG2 if not hov else (44, 44, 70), rect,
                         border_radius=3)
        pygame.draw.rect(surf, (70, 70, 100), rect, 1, border_radius=3)
        box = pygame.Rect(rect.x + 4, rect.centery - 4, 8, 8)
        pygame.draw.rect(surf, BG, box, border_radius=2)
        pygame.draw.rect(surf, DIM, box, 1, border_radius=2)
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
        pygame.draw.rect(surf, BG2, body, border_radius=3)
        pygame.draw.rect(surf, ACCENT if active else (70, 70, 100), body, 1,
                         border_radius=3)
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
        p.fill((14, 14, 26, 225))
        surf.blit(p, rect.topleft)
        pygame.draw.rect(surf, (70, 70, 110), rect, 1, border_radius=4)
        if title:
            s = self.font_b.render(title, True, ACCENT)
            surf.blit(s, (rect.centerx - s.get_width() // 2, rect.y + 5))
