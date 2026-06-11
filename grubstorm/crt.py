"""CRT presentation: phosphor glow, bloom, slot mask, scanlines, vignette,
chromatic fringe and gentle flicker. Tuned to feel like warm glass, not a
cheap overlay — and everything is sliders in the options menu."""
import random

import numpy as np
import pygame

from .constants import GRID_W, GRID_H, CELL_SCALE, VIEW_W, VIEW_H


class CRT:
    def __init__(self, settings):
        self.settings = settings
        self.big = pygame.Surface((VIEW_W, VIEW_H))
        self._phosphor = None
        self._vignette = None
        self._built_for = None
        self._build_overlays()

    def _build_overlays(self):
        w, h = VIEW_W, VIEW_H
        crt = float(self.settings.get("crt", 0.8))
        self._built_for = crt
        # phosphor = slot mask x scanlines in a single multiply overlay,
        # faded toward white by the CRT intensity slider
        col = np.full((w, h, 3), 255.0, np.float32)
        xs = np.arange(w)
        for lane, (a, b) in enumerate(((1, 2), (0, 2), (0, 1))):
            sel = xs % 3 == lane
            col[sel, :, a] -= 13
            col[sel, :, b] -= 16
        ys = np.arange(h)
        rows0 = ys % CELL_SCALE == 0
        col[:, rows0, :] -= 40
        if CELL_SCALE >= 3:
            rows1 = ys % CELL_SCALE == 1
            col[:, rows1, :] -= 12
        # intensity: lerp toward plain white
        col = 255.0 - (255.0 - col) * min(1.0, crt * 1.2)
        surf = pygame.Surface((w, h))
        pygame.surfarray.blit_array(surf, col.astype(np.uint8))
        self._phosphor = surf
        # vignette: smooth radial corner darkening from an upscaled gradient
        sw, sh = 96, 54
        yy, xx = np.mgrid[0:sh, 0:sw]
        nx = (xx / (sw - 1)) * 2 - 1
        ny = (yy / (sh - 1)) * 2 - 1
        d = np.sqrt(nx * nx * 1.05 + ny * ny * 0.95)
        alpha = np.clip((d - 0.72) * 2.0, 0, 1) ** 1.6 * 150 * min(1.0, crt + 0.15)
        small = pygame.Surface((sw, sh), pygame.SRCALPHA)
        pa = pygame.surfarray.pixels_alpha(small)
        pa[:] = alpha.T.astype(np.uint8)
        del pa
        vg = pygame.transform.smoothscale(small, (w, h))
        pygame.draw.rect(vg, (0, 0, 12, int(60 * crt)), (0, 0, w, h), width=5)
        pygame.draw.ellipse(vg, (255, 255, 255, int(9 * crt)),
                            (-w * 0.2, -h * 0.75, w * 1.4, h * 1.1))
        self._vignette = vg

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
            # bright-pass: only genuinely hot pixels feed the bloom
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
        pygame.transform.scale(view, (VIEW_W, VIEW_H), self.big)
        big = self.big
        if crt > 0.05:
            big.blit(self._phosphor, (0, 0),
                     special_flags=pygame.BLEND_RGB_MULT)
            big.blit(self._vignette, (0, 0))
            if not s.get("reduce_flash") and crt > 0.4:
                d = 247 + random.randint(0, 8)
                big.fill((d, d, d), special_flags=pygame.BLEND_RGB_MULT)
        sw, sh = screen.get_size()
        if (sw, sh) != (VIEW_W, VIEW_H):
            pygame.transform.smoothscale(big, (sw, sh), screen)
        else:
            screen.blit(big, (0, 0))
