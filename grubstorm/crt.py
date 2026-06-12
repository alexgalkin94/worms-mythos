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

# every knob of the tube, individually tunable (all 0..1)
CRT_PARAMS = ("crt_smear", "crt_scanline", "crt_mask", "crt_fringe",
              "crt_halation", "crt_persist", "crt_flicker", "crt_vignette",
              "crt_curve", "bloom")

CRT_PRESETS = {
    "CLEAN":     dict(crt_smear=0.0, crt_scanline=0.0, crt_mask=0.0,
                      crt_fringe=0.0, crt_halation=0.0, crt_persist=0.0,
                      crt_flicker=0.0, crt_vignette=0.0, crt_curve=0.0,
                      bloom=0.25),
    "SUBTLE":    dict(crt_smear=0.45, crt_scanline=0.35, crt_mask=0.25,
                      crt_fringe=0.12, crt_halation=0.35, crt_persist=0.2,
                      crt_flicker=0.1, crt_vignette=0.35, crt_curve=0.0,
                      bloom=0.5),
    "ARCADE":    dict(crt_smear=0.8, crt_scanline=0.7, crt_mask=0.55,
                      crt_fringe=0.25, crt_halation=0.6, crt_persist=0.45,
                      crt_flicker=0.3, crt_vignette=0.55, crt_curve=0.0,
                      bloom=0.7),
    "TRINITRON": dict(crt_smear=0.6, crt_scanline=0.85, crt_mask=0.8,
                      crt_fringe=0.08, crt_halation=0.45, crt_persist=0.25,
                      crt_flicker=0.15, crt_vignette=0.3, crt_curve=0.0,
                      bloom=0.55),
    "HAUNTED":   dict(crt_smear=1.0, crt_scanline=0.8, crt_mask=0.7,
                      crt_fringe=0.55, crt_halation=0.9, crt_persist=0.8,
                      crt_flicker=0.6, crt_vignette=0.8, crt_curve=0.6,
                      bloom=0.9),
}


def migrate_crt_settings(s):
    """Old saves had one master 'crt' slider — expand it once."""
    if "crt_smear" in s:
        return
    old = float(s.get("crt", 0.8))
    base = CRT_PRESETS["ARCADE"]
    for k, v in base.items():
        if k != "bloom":
            s[k] = round(v * min(1.0, old * 1.25), 2)
    s.setdefault("bloom", 0.7)
    if s.get("curvature"):
        s["crt_curve"] = 0.5


class CRT:
    def __init__(self, settings, scale=3):
        self.settings = settings
        self.scale = scale
        self.vw, self.vh = GRID_W * scale, GRID_H * scale
        self.big = pygame.Surface((self.vw, self.vh))
        self._wide = pygame.Surface((self.vw, GRID_H))   # h-smeared, pre-rows
        self._persist = pygame.Surface((GRID_W, GRID_H))
        self._persist.fill((0, 0, 0))
        self._persist_t = time.monotonic()
        self._phosphor = None
        self._shine = None
        self._built_for = None
        self._slider_val = None
        self._slider_t = 0.0
        self._warp_x = self._warp_y = None
        self._hal = pygame.Surface((self.vw, self.vh))
        self._wide2 = pygame.Surface((self.vw, GRID_H))  # sharp, pre-rows
        self._blo = None               # bright-pass upscale, view-sized
        self._shine_r = None           # bounding rect of the shine ellipse
        self._ren = None               # GPU chain (attach_gpu), else CPU
        self._win = None
        self._gpu_built_for = None
        # rolling cost of Renderer.present() in ms (GPU mode). ~0 when the
        # swap is free-running; pinned near 16.7 when something downstream
        # (driver setting, compositor) still forces vsync despite our
        # requests — THE number to check when GPU mode caps at 60 fps.
        self.present_block_ms = 0.0
        self._build_overlays()

    # ----------------------------------------------------------------- GPU
    def attach_gpu(self, renderer, window):
        """Run the big-surface chain on the GPU: upscales and the MUL/ADD
        overlay blends become texture draws. The per-pixel stages that need
        numpy (persist, fringe, bloom threshold, warp) stay on the CPU at
        view resolution, where they are cheap.

        Streaming uploads (the view, the halation bright-pass) rotate
        through a small ring of textures: Texture.update on a texture the
        GPU may still be sampling from the previous frame can make the
        driver synchronize the whole pipeline, which shows up as mystery
        present-side stalls under load. Ring depth is 3 (override with the
        `gpu_upload_ring` setting, 1..4; 1 = old single-texture behavior).

        The linear half of the smear crossfade is no longer uploaded at
        all: a 1:1 draw is filter-independent, so the nearest-filtered view
        texture is GPU-copied into a linear-filtered target texture and
        sampled from there. One upload serves both draws — this halves the
        upload bandwidth AND the per-upload format-conversion cost (the
        XRGB view surface is converted to the texture's ARGB layout inside
        Texture.update on every call)."""
        from pygame._sdl2.video import Texture
        self._ren, self._win = renderer, window
        try:
            ring = int(self.settings.get("gpu_upload_ring", 3))
        except (TypeError, ValueError):
            ring = 3
        ring = max(1, min(4, ring))
        self._ring_i = self._ring_b = 0
        self._tx_near = [Texture(renderer, (GRID_W, GRID_H),
                                 streaming=True, scale_quality=0)
                         for _ in range(ring)]
        # GPU-side clone of the view for linear sampling (never uploaded;
        # a render target only, so no update-stall concern and no ring)
        self._tx_lin = Texture(renderer, (GRID_W, GRID_H),
                               target=True, scale_quality=1)
        self._tx_lin.blend_mode = pygame.BLENDMODE_BLEND
        self._tx_wide = Texture(renderer, (self.vw, GRID_H),
                                target=True, scale_quality=0)
        self._tx_blo = [Texture(renderer, (GRID_W // 8, GRID_H // 8),
                                streaming=True, scale_quality=1)
                        for _ in range(ring)]
        for t in self._tx_blo:
            t.blend_mode = pygame.BLENDMODE_ADD
        white = pygame.Surface((1, 1)); white.fill((255, 255, 255))
        self._tx_white = Texture.from_surface(renderer, white)
        self._tx_white.blend_mode = pygame.BLENDMODE_MOD
        self._gpu_built_for = None

    def _gpu_rebuild(self):
        from pygame._sdl2.video import Texture
        # blend modes are fixed per role — set once here (textures are
        # recreated on overlay rebuilds), not per frame in _present_gpu
        self._tx_phos = Texture.from_surface(self._ren, self._phosphor)
        self._tx_phos.blend_mode = pygame.BLENDMODE_MOD
        self._tx_shine = Texture.from_surface(self._ren, self._shine)
        self._tx_shine.blend_mode = pygame.BLENDMODE_ADD
        self._gpu_built_for = self._built_for

    def _present_gpu(self, view, q, bloom_amt):
        s = self.settings
        ren = self._ren
        if self._gpu_built_for != self._built_for:
            self._gpu_rebuild()
        # pixel-melt crossfade, exactly like the CPU path: sharp horizontal
        # stretch, linear stretch alpha-blended on top, then the vertical
        # row replication happens in the final nearest draw to the window
        smear = float(s.get("crt_smear", 0.8))
        self._ring_i = (self._ring_i + 1) % len(self._tx_near)
        tx_near = self._tx_near[self._ring_i]
        tx_near.update(view)                  # the ONE view upload per frame
        if smear > 0.003:
            # clone for linear sampling: 1:1, blend NONE — pixel-exact
            ren.target = self._tx_lin
            tx_near.draw(dstrect=(0, 0, GRID_W, GRID_H))
            self._tx_lin.alpha = int(255 * min(1.0, smear))
        ren.target = self._tx_wide
        tx_near.draw(dstrect=(0, 0, self.vw, GRID_H))
        if smear > 0.003:
            self._tx_lin.draw(dstrect=(0, 0, self.vw, GRID_H))
        ren.target = None
        ww, wh = self._win.size
        full = (0, 0, ww, wh)
        self._tx_wide.draw(dstrect=full)
        self._tx_phos.draw(dstrect=full)
        hal = float(s.get("crt_halation", 0.6))
        if q is not None and hal > 0.03:
            self._ring_b = (self._ring_b + 1) % len(self._tx_blo)
            tx_blo = self._tx_blo[self._ring_b]
            tx_blo.update(q)
            tx_blo.alpha = int((50 + 70 * bloom_amt) * hal)
            tx_blo.draw(dstrect=full)
        if float(s.get("crt_vignette", 0.55)) > 0.05:
            self._tx_shine.draw(dstrect=full)
        flick = float(s.get("crt_flicker", 0.3))
        if not s.get("reduce_flash") and flick > 0.03:
            depth = int(9 * flick)
            d = 255 - depth + random.randint(0, depth)
            self._tx_white.color = (d, d, d)
            self._tx_white.draw(dstrect=full)
        t0 = time.perf_counter()
        ren.present()
        # exp-smoothed swap cost: vsync/compositor back-pressure lives here
        dt_ms = (time.perf_counter() - t0) * 1000.0
        self.present_block_ms += (dt_ms - self.present_block_ms) * 0.1

    # ------------------------------------------------------------- overlays
    def _overlay_key(self):
        s = self.settings
        return (round(float(s.get("crt_mask", 0.55)), 2),
                round(float(s.get("crt_scanline", 0.7)), 2),
                round(float(s.get("crt_vignette", 0.55)), 2),
                round(float(s.get("crt_curve", 0.0)), 2))

    def _build_overlays(self):
        w, h, sc = self.vw, self.vh, self.scale
        s = self.settings
        mask_s = float(s.get("crt_mask", 0.55))
        scan_s = float(s.get("crt_scanline", 0.7))
        vign_s = float(s.get("crt_vignette", 0.55))
        self._built_for = self._overlay_key()
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
        m[gap] *= 1.0 - 0.18 * mask_s
        # scanlines: gaussian beam profile per source row — bright centre,
        # symmetric falloff. Combined with the horizontal smear this turns
        # every pixel into a glowing "pill" instead of a square.
        c = (sc - 1) / 2.0
        sigma = sc * 0.34
        beam = np.exp(-(((ys % sc) - c) / sigma) ** 2).astype(np.float32)
        floor = 1.0 - 0.74 * scan_s
        m *= np.maximum(beam, floor)[None, :]
        # per-channel lane attenuation (phosphor triads)
        col = np.empty((w, h, 3), np.float32)
        col[:] = m[:, :, None]
        for ch in range(3):
            col[lane != ch, :, ch] *= 1.0 - 0.34 * mask_s
        # vignette = multiplicative corner darkening
        nx = (xs / (w - 1))[:, None] * 2 - 1
        ny = (ys / (h - 1))[None, :] * 2 - 1
        d = np.sqrt(nx * nx * 1.05 + ny * ny * 0.95)
        vig = 1.0 - np.clip((d - 0.78) * 1.9, 0, 1) ** 1.7 * 0.62 * vign_s
        col *= vig[:, :, None]
        # rounded-corner cut
        cx = np.minimum(xs, w - 1 - xs)[:, None]
        cy = np.minimum(ys, h - 1 - ys)[None, :]
        rad = 0.018 * w
        corner = np.clip((cx + cy + 2 - rad) / rad * 2.5, 0, 1)
        col *= corner[:, :, None]
        # gamma-correct: a CRT multiplies light, not sRGB values. Encoding
        # the multipliers with 1/2.2 keeps perceived brightness intact.
        col = np.power(np.clip(col, 0.0, 1.0), 1.0 / 2.2) * 255.0
        surf = pygame.Surface((w, h))
        pygame.surfarray.blit_array(surf, col.astype(np.uint8))
        self._phosphor = surf
        # curved-glass shine: one faint additive ellipse, prerendered
        shine = pygame.Surface((w, h))
        pygame.draw.ellipse(shine, (int(6 + 6 * vign_s),) * 3,
                            (-w * 0.2, -h * 0.75, w * 1.4, h * 1.1))
        self._shine = shine
        # the ellipse only covers the upper band of the screen; adding the
        # zero rows below is a no-op, so present() blits just this rect
        nz = pygame.surfarray.array3d(shine).any(axis=2)
        sxs = np.flatnonzero(nz.any(axis=1))
        sys_ = np.flatnonzero(nz.any(axis=0))
        self._shine_r = (pygame.Rect(int(sxs[0]), int(sys_[0]),
                                     int(sxs[-1] - sxs[0]) + 1,
                                     int(sys_[-1] - sys_[0]) + 1)
                         if len(sxs) else None)
        self._build_warp(float(s.get("crt_curve", 0.0)))

    def _build_warp(self, curve=0.5):
        """Precomputed barrel-distortion gather indices at view res."""
        k = 0.045 * max(0.05, curve)
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

    def _brightpass_up(self, q):
        """Upscale the bright-pass to view res and return it with the
        bounding box of its lit pixels (with margin for the bilinear filter
        support). Outside the box the upscale is exactly zero and adding
        zero is a no-op — so the ADD blits and the halation upscale (often
        most of the frame) shrink to the box, or vanish on dark frames."""
        sw, sh = q.get_size()
        qa = pygame.surfarray.pixels3d(q)
        xs = np.flatnonzero(qa.any(axis=(1, 2)))
        if not len(xs):
            return None, None                 # nothing bright this frame
        ys = np.flatnonzero(qa.any(axis=(0, 2)))
        del qa
        x0 = max(0, int(xs[0]) - 2)
        x1 = min(sw, int(xs[-1]) + 3)
        y0 = max(0, int(ys[0]) - 2)
        y1 = min(sh, int(ys[-1]) + 3)
        bx = x0 * GRID_W // sw
        bx1 = min(GRID_W, -((x1 * -GRID_W) // sw))
        by = y0 * GRID_H // sh
        by1 = min(GRID_H, -((y1 * -GRID_H) // sh))
        if self._blo is None:
            self._blo = pygame.Surface((GRID_W, GRID_H))
        pygame.transform.smoothscale(q, (GRID_W, GRID_H), self._blo)
        return self._blo, pygame.Rect(bx, by, bx1 - bx, by1 - by)

    def present(self, view, screen):
        s = self.settings
        # debounced overlay rebuild: dragging sliders doesn't hitch — the
        # expensive mask rebuild waits until the values settle
        now = time.monotonic()
        key = self._overlay_key()
        if key != self._slider_val:
            self._slider_val = key
            self._slider_t = now
        if self._built_for is None or (key != self._built_for
                                       and now - self._slider_t > 0.25):
            self._build_overlays()
        bloom_amt = float(s.get("bloom", 0.7))

        # phosphor persistence: faint trails on bright movement. The decay
        # is time-based (framerate independent) and scales with the slider:
        # 0 = off, 0.45 ~ a 70 ms half-life shimmer, 1.0 ~ a 300 ms haunt.
        pers = float(s.get("crt_persist", 0.45))
        dt = max(0.0, min(0.05, now - self._persist_t))
        self._persist_t = now
        if pers > 0.02 and not s.get("reduce_flash"):
            half_life = 0.015 + 0.285 * pers * pers
            k = 0.5 ** (dt / half_life)
            d = int(255 * k)
            self._persist.fill((max(0, d - 8), max(0, d - 5), d),
                               special_flags=pygame.BLEND_RGB_MULT)
            view.blit(self._persist, (0, 0),
                      special_flags=pygame.BLEND_RGB_MAX)
            self._persist.blit(view, (0, 0))
        else:
            self._persist.fill((0, 0, 0))

        # deconvergence: blended shift, strength on its own slider
        ab = float(s.get("crt_fringe", 0.25))
        if ab > 0.03:
            a8 = int(min(1.0, ab) * 256)
            arr = pygame.surfarray.pixels3d(view)
            r = arr[:, :, 0].astype(np.uint16)
            b = arr[:, :, 2].astype(np.uint16)
            arr[1:, :, 0] = ((r[1:] * (256 - a8) + r[:-1] * a8) >> 8).astype(np.uint8)
            arr[:-1, :, 2] = ((b[:-1] * (256 - a8) + b[1:] * a8) >> 8).astype(np.uint8)
            del arr

        # bright-pass for bloom + halation, at low res
        blo = q = blo_r = None
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
            blo, blo_r = self._brightpass_up(q)
            if blo is None:
                q = None
            else:
                blo.set_alpha(int(150 * bloom_amt))
                view.blit(blo, blo_r.topleft, area=blo_r,
                          special_flags=pygame.BLEND_RGB_ADD)

        # barrel curvature: real per-pixel remap (costs ~2.5 ms when on)
        if float(s.get("crt_curve", 0.0)) > 0.05:
            arr = pygame.surfarray.pixels3d(view)
            arr[:] = arr[self._warp_x, self._warp_y]
            del arr

        if self._ren is not None:
            self._present_gpu(view, q, bloom_amt)
            return True

        # THE pixel-melt, crossfaded by its own slider. When the window is
        # exactly big-sized (the default), compose straight into it — the
        # first scale below overwrites every pixel, so no clear is needed
        # and the final full-screen copy disappears.
        sw, sh = screen.get_size()
        direct = (sw, sh) == (self.vw, self.vh)
        big = screen if direct else self.big
        smear = float(s.get("crt_smear", 0.8))
        if smear >= 0.999:
            pygame.transform.smoothscale(view, (self.vw, GRID_H), self._wide)
            pygame.transform.scale(self._wide, (self.vw, self.vh), big)
        elif smear <= 0.001:
            pygame.transform.scale(view, (self.vw, self.vh), big)
        else:
            # crossfade at pre-row-replication width: the vertical scale is
            # pure row replication, so blending before it is pixel-exact —
            # and runs on a surface `scale`x smaller than big
            pygame.transform.scale(view, (self.vw, GRID_H), self._wide2)
            pygame.transform.smoothscale(view, (self.vw, GRID_H), self._wide)
            self._wide.set_alpha(int(255 * smear))
            self._wide2.blit(self._wide, (0, 0))
            self._wide.set_alpha(None)
            pygame.transform.scale(self._wide2, (self.vw, self.vh), big)

        big.blit(self._phosphor, (0, 0), special_flags=pygame.BLEND_RGB_MULT)
        # halation: glass-scattered light fills the scanline gaps around
        # bright areas (reads as the beam widening on hot pixels)
        hal = float(s.get("crt_halation", 0.6))
        if blo is not None and hal > 0.03:
            sc = self.scale
            big_r = pygame.Rect(blo_r.x * sc, blo_r.y * sc,
                                blo_r.w * sc, blo_r.h * sc)
            pygame.transform.scale(blo.subsurface(blo_r), big_r.size,
                                   self._hal.subsurface(big_r))
            self._hal.set_alpha(int((50 + 70 * bloom_amt) * hal))
            big.blit(self._hal, big_r.topleft, area=big_r,
                     special_flags=pygame.BLEND_RGB_ADD)
        if float(s.get("crt_vignette", 0.55)) > 0.05 and self._shine_r:
            big.blit(self._shine, self._shine_r.topleft, area=self._shine_r,
                     special_flags=pygame.BLEND_RGB_ADD)
        flick = float(s.get("crt_flicker", 0.3))
        if not s.get("reduce_flash") and flick > 0.03:
            depth = int(9 * flick)
            d = 255 - depth + random.randint(0, depth)
            if d < 255:        # (v * 255 + 255) >> 8 == v: exact identity
                big.fill((d, d, d), special_flags=pygame.BLEND_RGB_MULT)
        if not direct:
            pygame.transform.scale(big, (sw, sh), screen)
        return False
