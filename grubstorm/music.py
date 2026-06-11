"""Procedural music: composed, not shuffled.

Every launch composes fresh pieces from real harmony — curated chord
progressions, pentatonic melodies with call-and-response phrasing — and
renders them through hand-built synths designed to sound organic:

- pads: five detuned saw/triangle voices per note, slow attack, gentle
  tremolo, per-note filter variation, wide stereo
- plucks: Karplus-Strong physical strings (an actual vibrating delay line)
- lead: a breathy flute-ish voice with delayed vibrato and soft harmonics
- bells: inharmonic FM partials with long decays
- percussion: a heartbeat sine kick and brushed noise, barely there
- humanized timing (+-12 ms), velocity variation, occasional rests
- seamless loops: the reverb is a circular FFT convolution over the loop
  length, so the tail of the last bar blooms into the first

Music is render-only and never touches the simulation RNG.
"""
import math
import random
import threading

import numpy as np

try:
    import pygame
    _HAVE = True
except Exception:                                   # pragma: no cover
    _HAVE = False

SR = 22050

# ----------------------------------------------------------------- dsp ----
def _fir_lowpass(sig, cutoff, taps=47):
    if cutoff >= SR * 0.45:
        return sig
    t = np.arange(taps) - (taps - 1) / 2
    h = np.sinc(2 * cutoff / SR * t) * np.hanning(taps)
    h /= h.sum()
    return np.convolve(sig, h, mode="same")


def _adsr(n, a, d, s, r):
    out = np.full(n, s, np.float32)
    an, dn, rn = int(a * SR), int(d * SR), int(r * SR)
    an = min(an, n); dn = min(dn, max(0, n - an)); rn = min(rn, n)
    out[:an] = np.linspace(0, 1, an)
    out[an:an + dn] = np.linspace(1, s, dn)
    if rn:
        out[-rn:] *= np.linspace(1, 0, rn)
    return out


def _saw(phase):
    return 2.0 * (phase % 1.0) - 1.0


def _tri(phase):
    return 2.0 * np.abs(_saw(phase)) - 1.0


def _pad_note(rnd, f, dur, cutoff, trem_hz):
    """Detuned analog-style pad voice. The slow drift is the organic part."""
    n = int(dur * SR)
    t = np.arange(n) / SR
    sig = np.zeros(n)
    for cents, amp in ((-9, .8), (-4, .9), (0, 1.0), (5, .9), (9, .7)):
        ff = f * 2 ** (cents / 1200)
        drift = 1 + 0.0015 * np.sin(2 * np.pi * rnd.uniform(.05, .15) * t
                                    + rnd.uniform(0, 6.28))
        ph = np.cumsum(ff * drift) / SR
        sig += amp * (0.6 * _saw(ph) + 0.4 * _tri(ph))
    sig = _fir_lowpass(sig, cutoff * rnd.uniform(0.85, 1.2))
    trem = 1 + 0.13 * np.sin(2 * np.pi * trem_hz * t + rnd.uniform(0, 6.28))
    sig *= trem * _adsr(n, dur * 0.35, 0.1, 0.85, dur * 0.4)
    return sig * 0.16


def _pluck(rnd, f, dur, bright=0.5):
    """Karplus-Strong: a real string model, every note slightly different."""
    d = max(2, int(SR / f))
    n = int(dur * SR)
    buf = np.zeros(n + d + 2)
    burst = rnd_state(rnd).uniform(-1, 1, d)
    burst = _fir_lowpass(burst, 1200 + 6000 * bright, taps=15)
    buf[1:d + 1] = burst
    decay = 0.994 + 0.004 * bright
    pos = d + 1
    while pos < n + d + 1:
        m = min(d, n + d + 1 - pos)
        buf[pos:pos + m] = decay * 0.5 * (
            buf[pos - d:pos - d + m] + buf[pos - d - 1:pos - d - 1 + m])
        pos += m
    out = buf[1:n + 1]
    return out * _adsr(n, 0.002, 0.05, 0.85, dur * 0.5) * 0.8


def _lead_note(rnd, f, dur, breath=0.05):
    """Flute-ish voice: soft harmonics, delayed vibrato, a hint of air."""
    n = int(dur * SR)
    t = np.arange(n) / SR
    vib_on = np.clip((t - 0.22) / 0.5, 0, 1)
    vib = 1 + 0.011 * vib_on * np.sin(2 * np.pi * rnd.uniform(4.8, 5.8) * t)
    ph = np.cumsum(f * vib) / SR
    sig = (np.sin(2 * np.pi * ph) + 0.32 * np.sin(4 * np.pi * ph)
           + 0.1 * np.sin(6 * np.pi * ph))
    air = _fir_lowpass(rnd_state(rnd).uniform(-1, 1, n), 2200, taps=31)
    sig += breath * air * (0.4 + 0.6 * vib_on)
    sig *= _adsr(n, 0.08, 0.1, 0.85, min(0.5, dur * 0.45))
    return sig * 0.5


def _bell(rnd, f, dur):
    """Inharmonic FM partials — glassy, long, very pretty in reverb."""
    n = int(dur * SR)
    t = np.arange(n) / SR
    idx = 4.5 * np.exp(-t * 3.0)
    mod = np.sin(2 * np.pi * f * 1.41 * t) * idx
    sig = np.sin(2 * np.pi * f * t + mod)
    sig += 0.4 * np.sin(2 * np.pi * f * 2.76 * t + 0.4 * mod) * np.exp(-t * 4)
    return sig * np.exp(-t * 2.0) * 0.4


def _kick(dur=0.35):
    n = int(dur * SR)
    t = np.arange(n) / SR
    f = 110 * np.exp(-t * 18) + 42
    return np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t * 9) * 0.9


def _brush(rnd, dur=0.09):
    n = int(dur * SR)
    sig = rnd_state(rnd).uniform(-1, 1, n)
    sig = sig - _fir_lowpass(sig, 3500, taps=21)        # crude highpass
    return sig * np.exp(-np.arange(n) / SR * 40) * 0.5


def rnd_state(rnd):
    return np.random.default_rng(rnd.getrandbits(32))


def _reverb_loop(sig, rnd, wet=0.35, secs=2.4, tone=4200):
    """Circular FFT convolution: the tail wraps into the loop start, so the
    piece loops seamlessly with its own reverb bloom."""
    n = len(sig)
    irn = int(secs * SR)
    t = np.arange(irn) / SR
    ir = rnd_state(rnd).uniform(-1, 1, irn) * np.exp(-t / (secs / 3.2))
    ir = _fir_lowpass(ir, tone, taps=31)
    ir[:40] *= np.linspace(0, 1, 40)
    ir /= np.sqrt((ir ** 2).sum()) * 6
    h = np.zeros(n)
    h[:min(irn, n)] = ir[:min(irn, n)]
    out = np.fft.irfft(np.fft.rfft(sig) * np.fft.rfft(h), n)
    return sig * (1 - wet * 0.6) + out * wet


# ------------------------------------------------------------ harmony ----
# (degree offsets in semitones within the chosen scale colour)
_PROGS_WARM = [
    [(0, "m9"), (8, "M"), (3, "M9"), (10, "M")],     # i  VI  III VII
    [(0, "m9"), (5, "m"), (8, "M"), (10, "M")],      # i  iv  VI  VII
    [(0, "m"), (10, "M"), (8, "M9"), (5, "m")],      # i  VII VI  iv
    [(3, "M9"), (10, "M"), (0, "m"), (8, "M")],      # III VII i  VI
]
_PROGS_DARK = [
    [(0, "m"), (1, "M"), (0, "m"), (10, "m")],       # phrygian lean
    [(0, "m"), (5, "m"), (1, "M"), (0, "m")],
    [(0, "m9"), (8, "M"), (6, "dim"), (10, "m")],
]
_CHORD = {
    "m":   [0, 3, 7, 12],
    "m9":  [0, 3, 7, 14],
    "M":   [0, 4, 7, 12],
    "M9":  [0, 4, 7, 14],
    "dim": [0, 3, 6, 12],
}
_PENTA_MINOR = [0, 3, 5, 7, 10]

MOODS = {
    #         bpm  root   bright  wet   arp   lead  perc  bell
    "menu": ( 84,  110.0, 2600,  0.34, True,  True, 0.5,  0.8),
    "warm": ( 80,  110.0, 2300,  0.32, True,  True, 0.45, 0.6),
    "cold": ( 70,  123.5, 1900,  0.45, False, True, 0.25, 1.0),
    "dark": ( 76,   98.0, 1500,  0.38, False, True, 0.8,  0.4),
    "deep": ( 74,  103.8, 1300,  0.48, True,  False, 0.6, 0.7),
}


def compose(seed, mood="menu", bars=16):
    """Compose and render one seamless loop. Returns float32 stereo (n, 2)."""
    rnd = random.Random(seed)
    bpm, root, bright, wet, use_arp, use_lead, perc_amt, bell_amt = \
        MOODS.get(mood, MOODS["menu"])
    bpm += rnd.randint(-4, 4)
    spb = 60.0 / bpm
    bar = 4 * spb
    n = int(bars * bar * SR)
    L = np.zeros(n)
    R = np.zeros(n)

    def add(sig, t0, pan=0.0, gain=1.0):
        i0 = int(t0 * SR) % n
        gl = gain * math.cos((pan + 1) * math.pi / 4)
        gr = gain * math.sin((pan + 1) * math.pi / 4)
        m = len(sig)
        first = min(m, n - i0)
        L[i0:i0 + first] += sig[:first] * gl
        R[i0:i0 + first] += sig[:first] * gr
        if m > first:                       # wrap into the loop start
            L[:m - first] += sig[first:] * gl
            R[:m - first] += sig[first:] * gr

    def hz(semis, octave=0):
        return root * 2 ** ((semis + 12 * octave) / 12)

    progs = _PROGS_DARK if mood == "dark" else _PROGS_WARM
    prog = rnd.choice(progs)
    # section B re-colours the last half
    prog_b = rnd.choice([p for p in progs if p is not prog])
    chords = (prog + prog_b)                # 8 chords x 2 bars = 16 bars

    # --- pads ---
    for ci, (deg, qual) in enumerate(chords):
        t0 = ci * 2 * bar
        tones = _CHORD[qual]
        for k, semi in enumerate(tones):
            f = hz(deg + semi, octave=0 if k < 2 else 0)
            sig = _pad_note(rnd, f, 2 * bar * 1.05, bright, rnd.uniform(.15, .4))
            add(sig, t0, pan=rnd.uniform(-0.5, 0.5), gain=1.0)

    # --- bass ---
    for ci, (deg, qual) in enumerate(chords):
        t0 = ci * 2 * bar
        for b in range(2):
            for beat, p in ((0, 0.95), (2.5, 0.5), (3.5, 0.25)):
                if rnd.random() > p:
                    continue
                semi = deg if beat == 0 else deg + rnd.choice([0, 7, 12])
                f = hz(semi, octave=-1)
                dur = spb * (1.6 if beat == 0 else 0.7)
                m = int(dur * SR)
                t = np.arange(m) / SR
                sig = np.tanh(1.8 * _tri(f * t)) * _adsr(m, 0.01, 0.2, 0.6, dur * 0.4)
                add(sig, t0 + (b * 4 + beat) * spb + rnd.gauss(0, 0.006),
                    pan=0.0, gain=0.5 * rnd.uniform(0.8, 1.0))

    # --- plucked arpeggio (Karplus-Strong strings) ---
    if use_arp:
        for ci, (deg, qual) in enumerate(chords):
            t0 = ci * 2 * bar
            tones = _CHORD[qual]
            order = [0, 1, 2, 3, 2, 1, 3, 0]
            for step in range(16):           # eighths over 2 bars
                if rnd.random() < 0.22:
                    continue                  # breathe
                semi = deg + tones[order[step % 8]]
                f = hz(semi, octave=rnd.choice([0, 0, 1]))
                sig = _pluck(rnd, f, spb * rnd.uniform(1.5, 2.4),
                             bright=rnd.uniform(0.35, 0.7))
                add(sig, t0 + step * spb * 0.5 + rnd.gauss(0, 0.009),
                    pan=math.sin(step * 0.8) * 0.55,
                    gain=0.34 * rnd.uniform(0.6, 1.0))

    # --- lead melody: call and response over bars 5-8 and 13-16 ---
    if use_lead:
        for sect in (0, 1):
            base_t = (sect * 8 + 4) * bar
            phrase_deg = []
            cur = rnd.choice([7, 10, 12])
            for _ in range(rnd.randint(4, 6)):
                phrase_deg.append(cur)
                cur += rnd.choice([-3, -2, 2, 3, 5, -5])
                cur = max(0, min(17, cur))
            t = base_t + rnd.uniform(0, spb)
            for i, dg in enumerate(phrase_deg):
                semi = _PENTA_MINOR[dg % 5] + 12 * (dg // 5)
                dur = spb * rnd.choice([1.5, 2, 2, 3])
                sig = _lead_note(rnd, hz(semi, octave=1), dur)
                add(sig, t + rnd.gauss(0, 0.01), pan=rnd.uniform(-0.25, 0.25),
                    gain=0.5 * rnd.uniform(0.7, 1.0))
                t += dur * rnd.uniform(0.95, 1.3)

    # --- bells: sparkles on section seams ---
    for ci in range(0, len(chords), 2):
        if rnd.random() < 0.75:
            deg, qual = chords[ci]
            semi = deg + rnd.choice(_CHORD[qual][:3])
            sig = _bell(rnd, hz(semi, octave=2), 3.5)
            add(sig, ci * 2 * bar + rnd.uniform(0, bar),
                pan=rnd.uniform(-0.7, 0.7), gain=0.3 * bell_amt)

    # --- heartbeat percussion ---
    if perc_amt > 0:
        for b in range(bars):
            for beat, p in ((0, 0.9), (2, 0.75)):
                if rnd.random() < p:
                    add(_kick(), (b * 4 + beat) * spb + rnd.gauss(0, 0.004),
                        gain=0.30 * perc_amt)
            for half in range(8):
                if rnd.random() < 0.3:
                    add(_brush(rnd), (b * 4 + half * 0.5) * spb,
                        pan=rnd.uniform(-0.4, 0.4),
                        gain=0.12 * perc_amt * rnd.uniform(0.4, 1.0))

    # --- glue: seamless loop reverb, soft saturation, normalize ---
    L2 = _reverb_loop(L, rnd, wet=wet)
    R2 = _reverb_loop(R, rnd, wet=wet)
    mix = np.stack([L2, R2], axis=1)
    mix = np.tanh(mix * 1.1) * 0.9
    peak = np.abs(mix).max() or 1.0
    mix *= 0.88 / peak
    return mix.astype(np.float32)


# ------------------------------------------------------------- player ----
BIOME_MOOD = {
    "island": "warm", "desert": "warm", "candy": "warm", "moon": "cold",
    "tundra": "cold", "volcano": "dark", "junkyard": "dark",
    "sewer": "deep", "cavern": "deep", "mine": "deep", "lab": "deep",
}


class MusicPlayer:
    """Background composer/renderer with crossfading playback."""

    def __init__(self, settings):
        self.settings = settings
        self.ok = _HAVE and pygame.mixer.get_init() is not None
        self.current_mood = None
        self.sound = None
        self._pending = None       # (mood, Sound) rendered, not yet playing
        self._rendering = None
        self._lock = threading.Lock()

    def want(self, mood):
        """Ask for a mood; rendering and crossfade happen in the background."""
        if not self.ok or mood == self.current_mood:
            return
        with self._lock:
            if self._rendering == mood:
                return
            self._rendering = mood
        threading.Thread(target=self._render, args=(mood,),
                         daemon=True).start()

    def _render(self, mood):
        try:
            seed = random.randrange(1 << 30)
            mix = compose(seed, mood)
            pcm = (mix * 32000).astype(np.int16)
            snd = pygame.sndarray.make_sound(np.ascontiguousarray(pcm))
        except Exception:
            with self._lock:
                self._rendering = None
            return
        with self._lock:
            self._pending = (mood, snd)
            self._rendering = None

    def update(self):
        """Call once per frame: applies volume, starts pending tracks."""
        if not self.ok:
            return
        vol = float(self.settings.get("music", 0.6)) * 0.9
        pend = None
        with self._lock:
            if self._pending is not None:
                pend = self._pending
                self._pending = None
        if pend is not None:
            mood, snd = pend
            if self.sound is not None:
                self.sound.fadeout(1800)
            snd.set_volume(vol)
            snd.play(loops=-1, fade_ms=2200)
            self.sound = snd
            self.current_mood = mood
        elif self.sound is not None:
            self.sound.set_volume(vol)
