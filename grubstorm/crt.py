"""CRT presentation: phosphor glow, bloom, slot mask, scanlines, vignette,
chromatic fringe and gentle flicker — collapsed into as few full-screen
operations as possible so the cabinet can run at high refresh rates.
Everything is a slider in the options menu."""
import random

import numpy as np
import pygame

from .constants import GRID_W, GRID_H


class CRT:
    def __init__(self, settings, scale=3):
        self.settings = settings
        self.scale = scale
        self.vw, self.vh = GRID_W * scale, GRID_H * scale
        self.big = pygame.Surface((self.vw, self.vh))
        self._phosphor = None
        self._shine = None
        self._built_for = None
        self._build_overlays()

    def _build_overlays(self):
        w, h, sc = self.vw, self.vh, self.scale
        crt = float(self.settings.get("crt", 0.8))
        self._built_for = crt
        # slot mask + scanlines + vignette in ONE multiply overlay
        col = np.full((w, h, 3), 255.0, np.float32)
        xs = np.arange(w)
        for lane, (a, b) in enumerate(((1, 2), (0, 2), (0, 1))):
            sel = xs % 3 == lane
            col[sel, :, a] -= 13
            col[sel, :, b] -= 16
        ys = np.arange(h)
        col[:, ys % sc == 0, :] -= 40
        if sc >= 3:
            col[:, ys % sc == 1, :] -= 12
        # intensity: lerp the mask toward plain white
        col = 255.0 - (255.0 - col) * min(1.0, crt * 1.2)
        # vignette is multiplicative darkening — fold it into the same overlay
        nx = (xs / (w - 1))[:, None] * 2 - 1
        ny = (ys / (h - 1))[None, :] * 2 - 1
        d = np.sqrt(nx * nx * 1.05 + ny * ny * 0.95)
        vig = np.clip((d - 0.72) * 2.0, 0, 1) ** 1.6 * \
            (0.55 * min(1.0, crt + 0.15))
        col *= (1.0 - vig)[:, :, None]
        col[:4, :, :] *= 0.65
        col[-4:, :, :] *= 0.65
        col[:, :4, :] *= 0.65
        col[:, -4:, :] *= 0.65
        surf = pygame.Surface((w, h))
        pygame.surfarray.blit_array(surf, col.astype(np.uint8))
        self._phosphor = surf
        # curved-glass shine: one faint additive ellipse, prerendered
        shine = pygame.Surface((w, h))
        pygame.draw.ellipse(shine, (int(10 * crt),) * 3,
                            (-w * 0.2, -h * 0.75, w * 1.4, h * 1.1))
        self._shine = shine

    def present(self, view, screen):
        s = self.settings
        crt = float(s.get("crt", 0.8))
        if abs(crt - self._built_for) > 0.02:
            self._build_overlays()
        # chromatic fringe at grid res (cheap, reads right after upscale)
        if crt > 0.25 and s.get("aberration", True):
            arr = pygame.surfarray.pixels3d(view)
            arr[1:, :, 0] = arr[:-1, :, 0]
            arr[:-1, :, 2] = arr[1:, :, 2]
            del arr
        # bloom: bright-pass at low res, blurred, added before upscale
        bloom_amt = float(s.get("bloom", 0.7))
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
            blo.set_alpha(int(170 * bloom_amt))
            view.blit(blo, (0, 0), special_flags=pygame.BLEND_RGB_ADD)
        pygame.transform.scale(view, (self.vw, self.vh), self.big)
        big = self.big
        if crt > 0.05:
            big.blit(self._phosphor, (0, 0),
                     special_flags=pygame.BLEND_RGB_MULT)
            if crt > 0.5:
                big.blit(self._shine, (0, 0),
                         special_flags=pygame.BLEND_RGB_ADD)
            if not s.get("reduce_flash") and crt > 0.4:
                d = 247 + random.randint(0, 8)
                big.fill((d, d, d), special_flags=pygame.BLEND_RGB_MULT)
        sw, sh = screen.get_size()
        if (sw, sh) != (self.vw, self.vh):
            pygame.transform.scale(big, (sw, sh), screen)
        else:
            screen.blit(big, (0, 0))
