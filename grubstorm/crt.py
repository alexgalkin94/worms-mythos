"""CRT presentation, modeled on how real tubes actually destroy pixel art
in the best possible way:

- the electron beam smears adjacent pixels into continuous color
  (horizontal bilinear upscale instead of nearest — THE difference between
  "modern TV" crispy squares and a real CRT photo)
- the phosphor slot mask provides the fine structure the pixel grid used to
- mask + scanlines are folded into ONE gamma-correct multiply overlay, so
  dark lines don't crush overall brightness
- halation: bright light scatters in the glass and fills the scanline gaps
  around hot pixels (this also reads as brightness-dependent beam width)
- phosphor persistence leaves faint trails on fast bright things
- subtle barrel curvature, deconvergence fringe, vignette, glass shine,
  and a gentle flicker

Everything scales with the CRT/bloom sliders; CRT at 0 = clean pixels.
"""
import random
import time

import numpy as np
import pygame

from .constants import GRID_W, GRID_H


class CRT:
    def __init__(self, settings, scale=3):
        self.settings = settings
        self.scale = scale
        self.vw, self.vh = GRID_W * scale, GRID_H * scale
        self.big = pygame.Surface((self.vw, self.vh))
        self._wide = pygame.Surface((self.vw, GRID_H))   # h-smeared, pre-rows
        self._persist = pygame.Surface((GRID_W, GRID_H))
        self._persist.fill((0, 0, 0))
        self._phosphor = None
        self._shine = None
        self._built_for = None
        self._slider_val = None
        self._slider_t = 0.0
        self._warp_x = self._warp_y = None
        self._hal = pygame.Surface((self.vw, self.vh))
        self._smear_tmp = pygame.Surface((self.vw, self.vh))
        self._build_overlays()
        self._build_warp()

    # ------------------------------------------------------------- overlays
    def _build_overlays(self):
        w, h, sc = self.vw, self.vh, self.scale
        crt = float(self.settings.get("crt", 0.8))
        self._built_for = crt
        self._slider_t = 0.0
        # linear-light multipliers for slot mask + scanlines + vignette
        m = np.ones((w, h), np.float32)
        xs = np.arange(w)
        ys = np.arange(h)
        # slot mask: RGB lanes with staggered horizontal slot gaps
        lane = xs % 3
        slot_h = sc * 2                       # slot height in screen pixels
        stagger = ((xs // 3) % 2) * (slot_h // 2)
        slot_row = (ys[None, :] + stagger[:, None]) % slot_h
        gap = slot_row == slot_h - 1
        m[gap] *= 0.85
        # scanlines: gaussian beam profile per source row — bright centre,
        # symmetric falloff. Combined with the horizontal smear this turns
        # every pixel into a glowing "pill" instead of a square.
        c = (sc - 1) / 2.0
        sigma = sc * 0.34
        beam = np.exp(-(((ys % sc) - c) / sigma) ** 2).astype(np.float32)
        m *= np.maximum(beam, 0.30)[None, :]
        # per-channel lane attenuation (phosphor triads)
        col = np.empty((w, h, 3), np.float32)
        col[:] = m[:, :, None]
        for ch in range(3):
            col[lane != ch, :, ch] *= 0.72
        # vignette = multiplicative corner darkening
        nx = (xs / (w - 1))[:, None] * 2 - 1
        ny = (ys / (h - 1))[None, :] * 2 - 1
        d = np.sqrt(nx * nx * 1.05 + ny * ny * 0.95)
        vig = 1.0 - np.clip((d - 0.78) * 1.9, 0, 1) ** 1.7 * 0.5
        col *= vig[:, :, None]
        # rounded-corner cut
        cx = np.minimum(xs, w - 1 - xs)[:, None]
        cy = np.minimum(ys, h - 1 - ys)[None, :]
        rad = 0.018 * w
        corner = np.clip((cx + cy + 2 - rad) / rad * 2.5, 0, 1)
        col *= corner[:, :, None]
        # intensity slider: lerp the whole treatment toward plain white
        amt = min(1.0, crt * 1.15)
        col = 1.0 - (1.0 - col) * amt
        # gamma-correct: a CRT multiplies light, not sRGB values. Encoding
        # the multipliers with 1/2.2 keeps perceived brightness intact.
        col = np.power(np.clip(col, 0.0, 1.0), 1.0 / 2.2) * 255.0
        surf = pygame.Surface((w, h))
        pygame.surfarray.blit_array(surf, col.astype(np.uint8))
        self._phosphor = surf
        # curved-glass shine: one faint additive ellipse, prerendered
        shine = pygame.Surface((w, h))
        pygame.draw.ellipse(shine, (int(9 * crt),) * 3,
                            (-w * 0.2, -h * 0.75, w * 1.4, h * 1.1))
        self._shine = shine

    def _build_warp(self):
        """Precomputed barrel-distortion gather indices at view res."""
        k = 0.025
        ys, xs = np.mgrid[0:GRID_H, 0:GRID_W].astype(np.float32)
        nx = xs / (GRID_W - 1) * 2 - 1
        ny = ys / (GRID_H - 1) * 2 - 1
        r2 = nx * nx + ny * ny
        f = 1.0 + k * r2
        wx = ((nx * f) + 1) * 0.5 * (GRID_W - 1)
        wy = ((ny * f) + 1) * 0.5 * (GRID_H - 1)
        self._warp_x = np.clip(wx.round(), 0, GRID_W - 1).astype(np.int32).T
        self._warp_y = np.clip(wy.round(), 0, GRID_H - 1).astype(np.int32).T

    # -------------------------------------------------------------- present
    @staticmethod
    def _ramp(v, a, b):
        return min(1.0, max(0.0, (v - a) / (b - a)))

    def present(self, view, screen):
        s = self.settings
        crt = float(s.get("crt", 0.8))
        # debounced overlay rebuild: dragging the slider doesn't hitch —
        # the expensive mask rebuild waits until the value settles
        now = time.monotonic()
        if crt != self._slider_val:
            self._slider_val = crt
            self._slider_t = now
        if self._built_for is None or (abs(crt - self._built_for) > 0.02
                                        and now - self._slider_t > 0.25):
            self._build_overlays()
        bloom_amt = float(s.get("bloom", 0.7))

        # phosphor persistence fades in smoothly with the slider
        pers = self._ramp(crt, 0.30, 0.75)
        if pers > 0.02 and not s.get("reduce_flash"):
            d = int(255 - 87 * pers)
            self._persist.fill((d, d + 4, d + 10),
                               special_flags=pygame.BLEND_RGB_MULT)
            view.blit(self._persist, (0, 0),
                      special_flags=pygame.BLEND_RGB_MAX)
            self._persist.blit(view, (0, 0))

        # deconvergence: blended shift, no hard on/off pop
        ab = self._ramp(crt, 0.12, 0.38) if s.get("aberration", True) else 0.0
        if ab > 0.03:
            a8 = int(ab * 256)
            arr = pygame.surfarray.pixels3d(view)
            r = arr[:, :, 0].astype(np.uint16)
            b = arr[:, :, 2].astype(np.uint16)
            arr[1:, :, 0] = ((r[1:] * (256 - a8) + r[:-1] * a8) >> 8).astype(np.uint8)
            arr[:-1, :, 2] = ((b[:-1] * (256 - a8) + b[1:] * a8) >> 8).astype(np.uint8)
            del arr

        # bright-pass for bloom + halation, at low res
        blo = None
        if bloom_amt > 0.05:
            q = pygame.transform.smoothscale(view, (GRID_W // 4, GRID_H // 4))
            qa = pygame.surfarray.pixels3d(q)
            tmp = qa.astype(np.int16)
            tmp -= 150
            np.clip(tmp, 0, 115, out=tmp)
            tmp *= 2
            qa[:] = tmp.astype(np.uint8)
            del qa
            q = pygame.transform.smoothscale(q, (GRID_W // 8, GRID_H // 8))
            blo = pygame.transform.smoothscale(q, (GRID_W, GRID_H))
            blo.set_alpha(int(150 * bloom_amt))
            view.blit(blo, (0, 0), special_flags=pygame.BLEND_RGB_ADD)

        # barrel curvature: real per-pixel remap, opt-in (costs ~2.5 ms)
        if crt > 0.3 and s.get("curvature"):
            arr = pygame.surfarray.pixels3d(view)
            arr[:] = arr[self._warp_x, self._warp_y]
            del arr

        # THE pixel-melt, fading in smoothly: crisp pixels at low CRT,
        # fully melted beam at high — crossfaded in between
        big = self.big
        smear = self._ramp(crt, 0.10, 0.34)
        if smear >= 0.999:
            pygame.transform.smoothscale(view, (self.vw, GRID_H), self._wide)
            pygame.transform.scale(self._wide, (self.vw, self.vh), big)
        elif smear <= 0.001:
            pygame.transform.scale(view, (self.vw, self.vh), big)
        else:
            pygame.transform.scale(view, (self.vw, self.vh), big)
            pygame.transform.smoothscale(view, (self.vw, GRID_H), self._wide)
            pygame.transform.scale(self._wide, (self.vw, self.vh),
                                   self._smear_tmp)
            self._smear_tmp.set_alpha(int(255 * smear))
            big.blit(self._smear_tmp, (0, 0))

        if crt > 0.02:
            big.blit(self._phosphor, (0, 0),
                     special_flags=pygame.BLEND_RGB_MULT)
            # halation: glass-scattered light fills the scanline gaps around
            # bright areas (reads as the beam widening on hot pixels)
            hal = self._ramp(crt, 0.16, 0.42)
            if blo is not None and hal > 0.03:
                pygame.transform.scale(blo, (self.vw, self.vh), self._hal)
                self._hal.set_alpha(int((60 + 70 * bloom_amt) * hal))
                big.blit(self._hal, (0, 0), special_flags=pygame.BLEND_RGB_ADD)
            if crt > 0.3:                  # shine brightness already ~crt
                big.blit(self._shine, (0, 0),
                         special_flags=pygame.BLEND_RGB_ADD)
            flick = self._ramp(crt, 0.25, 0.70)
            if not s.get("reduce_flash") and flick > 0.03:
                depth = int(8 * flick)
                d = 255 - depth + random.randint(0, depth)
                big.fill((d, d, d), special_flags=pygame.BLEND_RGB_MULT)
        sw, sh = screen.get_size()
        if (sw, sh) != (self.vw, self.vh):
            pygame.transform.scale(big, (sw, sh), screen)
        else:
            screen.blit(big, (0, 0))
