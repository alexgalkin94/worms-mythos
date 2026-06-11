"""Procedurally synthesized sound effects. No asset files needed."""
import numpy as np

try:
    import pygame
    _HAVE_MIXER = True
except Exception:                                       # pragma: no cover
    _HAVE_MIXER = False

SR = 22050


def _env(n, attack=0.005, decay=None):
    t = np.linspace(0, 1, n)
    a = int(max(1, attack * n))
    env = np.ones(n)
    env[:a] = np.linspace(0, 1, a)
    if decay is None:
        env *= np.exp(-4 * t)
    else:
        env *= np.exp(-decay * t)
    return env


def _noise(dur, lowpass=None):
    n = int(SR * dur)
    x = np.random.default_rng(7).uniform(-1, 1, n)
    if lowpass:
        k = max(1, int(SR / lowpass))
        kernel = np.ones(k) / k
        x = np.convolve(x, kernel, mode="same")
    return x


def _tone(dur, f0, f1=None, shape="sine"):
    n = int(SR * dur)
    t = np.arange(n) / SR
    f1 = f1 if f1 is not None else f0
    freq = np.linspace(f0, f1, n)
    phase = np.cumsum(2 * np.pi * freq / SR)
    if shape == "square":
        return np.sign(np.sin(phase))
    if shape == "saw":
        return ((phase / np.pi) % 2) - 1
    return np.sin(phase)


def _mk(data, vol=1.0):
    data = np.clip(data * vol, -1, 1)
    pcm = (data * 32000).astype(np.int16)
    stereo = np.column_stack([pcm, pcm])
    return pygame.sndarray.make_sound(np.ascontiguousarray(stereo))


class Audio:
    def __init__(self, settings):
        self.settings = settings
        self.ok = False
        self.sounds = {}
        if not _HAVE_MIXER:
            return
        try:
            pygame.mixer.pre_init(SR, -16, 2, 512)
            pygame.mixer.init(SR, -16, 2, 512)
            pygame.mixer.set_num_channels(24)
            self._build()
            self.ok = True
        except Exception:
            self.ok = False

    def _build(self):
        s = self.sounds
        s["boom"] = _mk(_noise(0.9, 700) * _env(int(SR * 0.9), decay=5) * 2.4 +
                        _tone(0.9, 90, 30) * _env(int(SR * 0.9), decay=4) * 0.9)
        s["boom_s"] = _mk(_noise(0.45, 1200) * _env(int(SR * 0.45), decay=7) * 1.8)
        s["thud"] = _mk(_tone(0.18, 110, 50) * _env(int(SR * 0.18), decay=9))
        s["shoot"] = _mk(_noise(0.15, 3000) * _env(int(SR * 0.15), decay=12) +
                         _tone(0.15, 600, 180) * _env(int(SR * 0.15), decay=10) * 0.4)
        s["splat"] = _mk(_noise(0.25, 900) * _env(int(SR * 0.25), decay=8))
        s["sizzle"] = _mk(_noise(0.3, 5000) * _env(int(SR * 0.3), decay=4) * 0.5)
        s["zap"] = _mk((_tone(0.25, 1800, 200, "square") * 0.4 +
                        _noise(0.25, 6000) * 0.6) * _env(int(SR * 0.25), decay=8))
        s["tic"] = _mk(_tone(0.05, 900, 700) * _env(int(SR * 0.05), decay=14) * 0.6)
        s["pickup"] = _mk(np.concatenate([
            _tone(0.09, 660) * _env(int(SR * 0.09)),
            _tone(0.12, 990) * _env(int(SR * 0.12))]))
        s["warp"] = _mk(_tone(0.4, 300, 1200) * _env(int(SR * 0.4), decay=3) * 0.6)
        s["death"] = _mk(_tone(0.5, 500, 80, "saw") * _env(int(SR * 0.5), decay=4) * 0.5)
        s["bubble"] = _mk(_tone(0.08, 300, 700) * _env(int(SR * 0.08), decay=6) * 0.4)
        s["alarm"] = _mk(np.concatenate([
            _tone(0.18, 880, 880, "square"), _tone(0.18, 660, 660, "square"),
            _tone(0.18, 880, 880, "square")]) * 0.25)
        notes = [523, 659, 784, 1047]
        s["fanfare"] = _mk(np.concatenate(
            [_tone(0.16, f) * _env(int(SR * 0.16), decay=3) for f in notes] +
            [_tone(0.5, 1319) * _env(int(SR * 0.5), decay=3)]) * 0.6)
        s["click"] = _mk(_tone(0.04, 1200) * _env(int(SR * 0.04), decay=16) * 0.5)
        s["hover"] = _mk(_tone(0.03, 800) * _env(int(SR * 0.03), decay=16) * 0.3)
        s["charge"] = _mk(_tone(0.06, 300, 500) * _env(int(SR * 0.06), decay=8) * 0.3)
        bang = _noise(0.22, 5000) * _env(int(SR * 0.22), decay=14) * 2.2
        thump = _tone(0.12, 220, 70) * _env(int(SR * 0.12), decay=12) * 0.8
        bang[:len(thump)] += thump
        s["bang"] = _mk(bang)
        s["throw"] = _mk(_noise(0.12, 1600) * _env(int(SR * 0.12), decay=10) * 0.7)
        s["spray"] = _mk(_noise(0.5, 2400) * _env(int(SR * 0.5), decay=3) * 0.5)
        s["freeze"] = _mk(_tone(0.5, 1800, 500) * _env(int(SR * 0.5), decay=4) * 0.35 +
                          _noise(0.5, 7000) * _env(int(SR * 0.5), decay=5) * 0.2)
        s["whoosh"] = _mk(_noise(0.4, 900) * _env(int(SR * 0.4), decay=4) * 1.2)
        s["rope"] = _mk(_tone(0.16, 700, 320) * _env(int(SR * 0.16), decay=9) * 0.5)
        s["grind"] = _mk((_noise(0.35, 700) + _tone(0.35, 70, 60, "saw") * 0.5)
                         * _env(int(SR * 0.35), decay=4) * 0.6)
        s["lockon"] = _mk(_tone(0.07, 1200) * _env(int(SR * 0.07), decay=10) * 0.4)

    # ------------------------------------------------- biome ambience ----
    def ambience(self, mood):
        """Procedural 10s ambient loop per arena mood, cached."""
        if not self.ok:
            return None
        if not hasattr(self, "_amb_cache"):
            self._amb_cache = {}
        if mood in self._amb_cache:
            return self._amb_cache[mood]
        n = SR * 10
        t = np.linspace(0, 10, n)
        rng = np.random.default_rng(99)
        base = rng.uniform(-1, 1, n)
        if mood in ("warm", "menu"):              # coastal wind + gulls? no.
            sig = np.convolve(base, np.ones(40) / 40, "same")
            sig *= 0.5 + 0.3 * np.sin(2 * np.pi * 0.13 * t)
        elif mood == "cold":                      # thin howling wind
            sig = np.convolve(base, np.ones(24) / 24, "same")
            sig *= 0.4 + 0.4 * np.sin(2 * np.pi * 0.21 * t + 1)
        elif mood == "dark":                      # rumble + ember crackle
            sig = np.convolve(base, np.ones(90) / 90, "same") * 1.6
            crack = (rng.uniform(0, 1, n) > 0.9995).astype(float)
            sig += np.convolve(crack, np.exp(-np.arange(200) / 30), "same") * 0.4
        else:                                     # deep: cave air + drips
            sig = np.convolve(base, np.ones(120) / 120, "same") * 1.4
            for _ in range(8):
                p = rng.integers(0, n - 4000)
                f = rng.uniform(900, 1600)
                drip = np.sin(2 * np.pi * f * np.arange(3000) / SR) * \
                    np.exp(-np.arange(3000) / 700)
                sig[p:p + 3000] += drip * 0.12
        # loop-smooth the seam
        fade = np.linspace(0, 1, 4000)
        sig[:4000] = sig[:4000] * fade + sig[-4000:] * (1 - fade)
        snd = _mk(sig, vol=0.32)
        self._amb_cache[mood] = snd
        return snd

    def play(self, name, vol=1.0):
        if not self.ok:
            return
        snd = self.sounds.get(name)
        if snd is None:
            return
        master = float(self.settings.get("volume", 0.8))
        if master <= 0.01:
            return
        snd.set_volume(min(1.0, vol * master))
        snd.play()
