"""Loudness Check — måler LUFS og True Peak for hele timelinens mix (YouTube-standard).

Renderer mixet midlertidigt til en WAV, måler efter ITU-R BS.1770-4 / EBU R128,
viser resultatet og foreslår limiter-indstillinger til Bus 1. Den midlertidige fil,
render-jobbet og render-indstillingerne ryddes op / gendannes bagefter.
Timeline og klip røres ikke.
"""

import array
import math
import os
import shutil
import struct
import sys
import tempfile
import time

# YouTube loudness-preset
TARGET_LUFS = -14.0
TARGET_TP = -1.0
# Peaks under denne værdi regnes for unødigt langt fra grænsen
TP_WINDOW_LOW = -1.3
TOLERANCE_LU = 1.0
# Ceiling med lille margen, da en sample-peak limiter kan lade intersample peaks slippe igennem
LIMITER_CEILING = -1.1

TEMP_PRESET = "__LoudnessCheck_restore"


# ---------------------------------------------------------------- WAV-læsning

def read_wav(path):
    """Returnerer (samplerate, [kanal-arrays med floats i -1..1])."""
    with open(path, "rb") as f:
        data = f.read()
    if data[0:4] not in (b"RIFF", b"RF64") or data[8:12] != b"WAVE":
        raise ValueError("Ikke en WAV-fil")
    pos = 12
    fmt = None
    pcm = None
    ds64_size = None
    while pos + 8 <= len(data):
        cid = data[pos:pos + 4]
        size = struct.unpack_from("<I", data, pos + 4)[0]
        body = pos + 8
        if cid == b"ds64":
            ds64_size = struct.unpack_from("<Q", data, body + 8)[0]
        elif cid == b"fmt ":
            tag, nch, sr, _, _, bits = struct.unpack_from("<HHIIHH", data, body)
            if tag == 0xFFFE:
                tag = struct.unpack_from("<H", data, body + 24)[0]
            fmt = (tag, nch, sr, bits)
        elif cid == b"data":
            if size == 0xFFFFFFFF and ds64_size is not None:
                size = ds64_size
            pcm = data[body:body + size]
            break
        pos = body + size + (size & 1)
    if fmt is None or pcm is None:
        raise ValueError("WAV mangler fmt- eller data-chunk")

    tag, nch, sr, bits = fmt
    if tag == 3 and bits == 32:
        samples = array.array("f")
        samples.frombytes(pcm[:len(pcm) // 4 * 4])
        scale = 1.0
    elif tag == 1 and bits == 16:
        samples = array.array("h")
        samples.frombytes(pcm[:len(pcm) // 2 * 2])
        scale = 1.0 / 32768.0
    elif tag == 1 and bits == 24:
        n = len(pcm) // 3
        buf = bytearray(n * 4)
        buf[1::4] = pcm[0:n * 3:3]
        buf[2::4] = pcm[1:n * 3:3]
        buf[3::4] = pcm[2:n * 3:3]
        samples = array.array("i")
        samples.frombytes(bytes(buf))
        scale = 1.0 / 2147483648.0
    elif tag == 1 and bits == 32:
        samples = array.array("i")
        samples.frombytes(pcm[:len(pcm) // 4 * 4])
        scale = 1.0 / 2147483648.0
    else:
        raise ValueError("Ukendt WAV-format (tag %d, %d bit)" % (tag, bits))
    if sys.byteorder != "little":
        samples.byteswap()

    channels = []
    for c in range(nch):
        ch = samples[c::nch]
        if scale != 1.0:
            ch = array.array("d", [v * scale for v in ch])
        channels.append(ch)
    return sr, channels


# ---------------------------------------------------------------- Loudness (BS.1770-4)

def k_weighting(sr):
    """De to biquads (high shelf + high pass) for K-vægtning ved vilkårlig samplerate."""
    f0, gain, q = 1681.974450955533, 3.999843853973347, 0.7071752369554196
    k = math.tan(math.pi * f0 / sr)
    vh = 10.0 ** (gain / 20.0)
    vb = vh ** 0.4996667741545416
    a0 = 1.0 + k / q + k * k
    shelf = ((vh + vb * k / q + k * k) / a0, 2.0 * (k * k - vh) / a0,
             (vh - vb * k / q + k * k) / a0, 2.0 * (k * k - 1.0) / a0,
             (1.0 - k / q + k * k) / a0)
    f0, q = 38.13547087602444, 0.5003270373238773
    k = math.tan(math.pi * f0 / sr)
    a0 = 1.0 + k / q + k * k
    hp = (1.0, -2.0, 1.0, 2.0 * (k * k - 1.0) / a0, (1.0 - k / q + k * k) / a0)
    return shelf, hp


def channel_bins(ch, sr, step):
    """K-vægtet energi summeret i bins af `step` samples (100 ms)."""
    (b0, b1, b2, a1, a2), (c0, c1, c2, d1, d2) = k_weighting(sr)
    x1 = x2 = y1 = y2 = w1 = w2 = 0.0
    bins = []
    acc = 0.0
    n = 0
    for v in ch:
        y = b0 * v + b1 * x1 + b2 * x2 - a1 * y1 - a2 * y2
        x2 = x1
        x1 = v
        z = c0 * y + c1 * y1 + c2 * y2 - d1 * w1 - d2 * w2
        y2 = y1
        y1 = y
        w2 = w1
        w1 = z
        acc += z * z
        n += 1
        if n == step:
            bins.append(acc)
            acc = 0.0
            n = 0
    return bins


def channel_weights(nch):
    if nch == 6:  # L R C LFE Ls Rs
        return [1.0, 1.0, 1.0, 0.0, 1.41, 1.41]
    return [1.0] * nch


def lufs(power):
    return -0.691 + 10.0 * math.log10(power) if power > 0 else float("-inf")


def loudness_stats(channels, sr):
    step = sr // 10
    weights = channel_weights(len(channels))
    per_ch = [channel_bins(ch, sr, step) for ch, w in zip(channels, weights) if w > 0]
    used_w = [w for w in weights if w > 0]
    nbins = min(len(b) for b in per_ch)
    bins = [sum(w * b[i] for w, b in zip(used_w, per_ch)) for i in range(nbins)]

    def blocks(nb):
        # Middel-energi for vinduer på nb bins med hop på én bin (100 ms)
        out = []
        run = sum(bins[:nb])
        for i in range(nb, nbins + 1):
            out.append(run / (nb * step))
            if i < nbins:
                run += bins[i] - bins[i - nb]
        return out

    momentary = blocks(4)    # 400 ms, 75 % overlap
    short_term = blocks(30)  # 3 s

    gated = [p for p in momentary if lufs(p) > -70.0]
    integrated = float("-inf")
    if gated:
        rel = lufs(sum(gated) / len(gated)) - 10.0
        gated2 = [p for p in gated if lufs(p) > rel]
        if gated2:
            integrated = lufs(sum(gated2) / len(gated2))

    # Loudness Range (EBU Tech 3342)
    lra = 0.0
    st = [p for p in short_term if lufs(p) > -70.0]
    if st:
        rel = lufs(sum(st) / len(st)) - 20.0
        st = sorted(lufs(p) for p in st if lufs(p) > rel)
        if len(st) > 1:
            lo = st[int(round(0.10 * (len(st) - 1)))]
            hi = st[int(round(0.95 * (len(st) - 1)))]
            lra = hi - lo

    return {
        "integrated": integrated,
        "lra": lra,
        "max_momentary": max((lufs(p) for p in momentary), default=float("-inf")),
        "max_short_term": max((lufs(p) for p in short_term), default=float("-inf")),
    }


# ---------------------------------------------------------------- True Peak (4x oversampling)

def _bessel_i0(x):
    s = term = 1.0
    k = 1
    while term > 1e-12 * s:
        term *= (x / (2.0 * k)) ** 2
        s += term
        k += 1
    return s


def _phase_filters(taps_per_phase=16, beta=8.0):
    """Kaiser-vinduet sinc-interpolation for positionerne 1/4, 2/4 og 3/4 mellem samples."""
    half = taps_per_phase // 2
    offsets = list(range(-half + 1, half + 1))  # sample-indeks relativt til k
    phases = []
    for p in (1, 2, 3):
        coeffs = []
        for j in offsets:
            t = p / 4.0 - j
            sinc = 1.0 if t == 0 else math.sin(math.pi * t) / (math.pi * t)
            r = t / half
            win = _bessel_i0(beta * math.sqrt(max(0.0, 1.0 - r * r))) / _bessel_i0(beta)
            coeffs.append(sinc * win)
        s = sum(coeffs)
        phases.append([c / s for c in coeffs])
    return offsets, phases


def true_peak(channels):
    """Eksakt maks over 4x-oversamplede værdier, men springer stykker over hvor
    en øvre grænse (maks |x| i vinduet * sum|h|) ikke kan slå den hidtil højeste peak."""
    offsets, phases = _phase_filters()
    gain_bound = max(sum(abs(c) for c in ph) for ph in phases)
    lo_off, hi_off = offsets[0], offsets[-1]
    chunk = 256
    best = 0.0
    sample_peak = 0.0
    for ch in channels:
        n = len(ch)
        absmax = [max(map(abs, ch[i:i + chunk])) for i in range(0, n, chunk)]
        sample_peak = max(sample_peak, max(absmax, default=0.0))
        best = max(best, sample_peak)
        nchunks = len(absmax)
        order = []
        for c in range(nchunks):
            m = max(absmax[max(0, c - 1):c + 2])
            order.append((m, c))
        order.sort(reverse=True)
        for m, c in order:
            if m * gain_bound <= best:
                break
            start = max(c * chunk, -lo_off)
            end = min((c + 1) * chunk, n - hi_off)
            for k in range(start, end):
                window = ch[k + lo_off:k + hi_off + 1]
                for ph in phases:
                    v = abs(sum(a * b for a, b in zip(window, ph)))
                    if v > best:
                        best = v
    return best, sample_peak


def db(x):
    return 20.0 * math.log10(x) if x > 0 else float("-inf")


def measure(path):
    sr, channels = read_wav(path)
    stats = loudness_stats(channels, sr)
    tp, sp = true_peak(channels)
    stats["true_peak"] = db(tp)
    stats["sample_peak"] = db(sp)
    stats["duration"] = len(channels[0]) / float(sr)
    stats["channels"] = len(channels)
    stats["samplerate"] = sr
    return stats


# ---------------------------------------------------------------- Anbefaling

# Fairlight Limiter: Input Level -18..18 dB, Ceiling -24..0 dBTP, Release 0.01..1000 ms
LEVEL_RANGE = (-18.0, 18.0)
CEILING_RANGE = (-24.0, 0.0)
RELEASE_RANGE = (0.01, 1000.0)
RELEASE_DEFAULT = 1.0


def recommend(s):
    """Returnerer {status, headline, notes, level, ceiling}.
    level er en ændring i dB, der lægges til limiterens nuværende Input Level (None = uændret).
    ceiling er en absolut værdi i dBTP (None = uændret)."""
    out = {"status": "juster", "headline": "", "notes": [], "level": None, "ceiling": None}
    i, tp = s["integrated"], s["true_peak"]
    if i == float("-inf"):
        out.update(status="ingen", headline="Der er ingen målbar lyd på timelinen.")
        return out
    gain = TARGET_LUFS - i
    tp_after = tp + gain
    lufs_ok = abs(gain) <= TOLERANCE_LU
    tp_ok = tp <= TARGET_TP

    if lufs_ok and tp_ok:
        if tp >= TP_WINDOW_LOW:
            out.update(status="ok", headline="Mixet rammer YouTube-standarden. Ingen ændring nødvendig.")
            return out
        out["headline"] = "Loudness er fin, men peaks ligger %.1f dB under grænsen." % (TARGET_TP - tp)
        out["ceiling"] = LIMITER_CEILING
        out["notes"].append("Arbejder limiteren slet ikke (ingen gain reduction), ligger peaks bare naturligt "
                            "lavere. Så er det fint.")
        return out

    notes = out["notes"]
    if gain > 0 and not lufs_ok:
        out["headline"] = "Mixet er %.1f LU for lavt. YouTube hæver ikke lyden." % gain
    elif gain < 0 and not lufs_ok:
        out["headline"] = "Mixet er %.1f LU for højt. YouTube skruer det ned." % -gain
    else:
        out["headline"] = "True Peak er %.1f dB over grænsen." % (tp - TARGET_TP)

    out["ceiling"] = LIMITER_CEILING
    if not lufs_ok or (gain > 0 and not tp_ok):
        out["level"] = gain
        if gain > LEVEL_RANGE[1]:
            notes.append("Det er mere end limiterens maks på +18 dB. Løft de stille klip i stedet.")
        if gain > 0:
            reduction = max(0.0, tp_after - LIMITER_CEILING)
            if reduction > 6.0:
                notes.append("Limiteren vil tage ca. %.1f dB af peaks. Det er meget og kan høres. "
                             "Overvej at løfte de stille klip i stedet." % reduction)
            elif reduction > 0:
                notes.append("Limiteren vil tage ca. %.1f dB af de højeste peaks." % reduction)
            if reduction > 0:
                notes.append("Limiting koster lidt loudness. Læg 0.3–0.5 dB oveni, hvis næste måling er lidt under.")
    return out


# ---------------------------------------------------------------- Render af mixet

def render_mix(project, timeline, out_dir, progress=None):
    """Renderer timelinens mix til en WAV i out_dir og returnerer stien.
    Render-indstillinger gendannes og render-jobbet slettes bagefter."""
    if project.IsRenderingInProgress():
        raise RuntimeError("Der kører allerede en render. Vent til den er færdig.")

    if TEMP_PRESET in (project.GetRenderPresetList() or []):
        project.DeleteRenderPreset(TEMP_PRESET)
    saved = project.SaveAsNewRenderPreset(TEMP_PRESET)
    job = None
    try:
        project.SetCurrentRenderMode(1)
        project.SetRenderSettings({"ExportVideo": False, "ExportAudio": True})
        ok = project.SetRenderSettings({
            "SelectAllFrames": True,
            "AudioFormat": "wav",
            "AudioCodec": "lpcm",
            "AudioBitDepth": 24,
            "TargetDir": out_dir,
            "CustomName": "loudness_check",
            "UseUniqueFilenames": False,
        })
        if not ok:
            raise RuntimeError("Kunne ikke sætte render-indstillinger.")
        job = project.AddRenderJob()
        if not job:
            raise RuntimeError("Kunne ikke oprette render-job.")
        if not project.StartRendering([job], False):
            raise RuntimeError("Renderen kunne ikke startes.")
        while project.IsRenderingInProgress():
            if progress:
                st = project.GetRenderJobStatus(job) or {}
                progress(st.get("CompletionPercentage", 0))
            time.sleep(0.25)
        st = project.GetRenderJobStatus(job) or {}
        if st.get("JobStatus") != "Complete":
            raise RuntimeError("Render fejlede: %s" % (st.get("Error") or st.get("JobStatus")))
    finally:
        if job:
            project.DeleteRenderJob(job)
        if saved:
            project.LoadRenderPreset(TEMP_PRESET)
            project.DeleteRenderPreset(TEMP_PRESET)

    wavs = [f for f in os.listdir(out_dir) if f.lower().endswith(".wav")]
    if not wavs:
        raise RuntimeError("Renderen lavede ingen WAV-fil.")
    return os.path.join(out_dir, wavs[0])


def analyze(project, progress=None):
    timeline = project.GetCurrentTimeline()
    if not timeline:
        raise RuntimeError("Ingen timeline er åben.")
    tmp = tempfile.mkdtemp(prefix="loudness_check_")
    try:
        wav = render_mix(project, timeline, tmp, progress)
        if progress:
            progress(-1)
        stats = measure(wav)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    stats["timeline"] = timeline.GetName()
    return stats


# ---------------------------------------------------------------- Knap-grafik (PNG i ren Python)

BLUE = (47, 128, 237)
ORANGE = (255, 159, 26)


def _write_png(path, w, h, rgba):
    import zlib
    raw = bytearray()
    stride = w * 4
    for y in range(h):
        raw.append(0)
        raw += rgba[y * stride:(y + 1) * stride]

    def chunk(tag, data):
        c = struct.pack(">I", len(data)) + tag + data
        return c + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(bytes(raw), 9)) + chunk(b"IEND", b"")
    with open(path, "wb") as f:
        f.write(png)


class _Canvas:
    """Minimal antialiaseret tegneflade: figurer beskrives som signed distance-funktioner."""

    def __init__(self, w, h):
        self.w, self.h = w, h
        self.px = [0.0] * (w * h * 4)  # præmultipliceret RGBA, 0..1

    def fill(self, bbox, sdf, color, alpha=1.0):
        x0, y0, x1, y1 = bbox
        x0, y0 = max(0, int(x0) - 1), max(0, int(y0) - 1)
        x1, y1 = min(self.w, int(x1) + 2), min(self.h, int(y1) + 2)
        px = self.px
        for y in range(y0, y1):
            fy = y + 0.5
            for x in range(x0, x1):
                a = 0.5 - sdf(x + 0.5, fy)
                if a <= 0.0:
                    continue
                a = (1.0 if a > 1.0 else a) * alpha
                r, g, b = color(x, y) if callable(color) else color
                i = (y * self.w + x) * 4
                k = 1.0 - a
                px[i] = r / 255.0 * a + px[i] * k
                px[i + 1] = g / 255.0 * a + px[i + 1] * k
                px[i + 2] = b / 255.0 * a + px[i + 2] * k
                px[i + 3] = a + px[i + 3] * k

    def save(self, path):
        out = bytearray(self.w * self.h * 4)
        px = self.px
        for i in range(0, len(px), 4):
            a = px[i + 3]
            if a > 0.0:
                out[i] = min(255, int(px[i] / a * 255.0 + 0.5))
                out[i + 1] = min(255, int(px[i + 1] / a * 255.0 + 0.5))
                out[i + 2] = min(255, int(px[i + 2] / a * 255.0 + 0.5))
                out[i + 3] = min(255, int(a * 255.0 + 0.5))
        _write_png(path, self.w, self.h, out)


def _angle_of(frac):
    # 0 = nede til venstre (225°), 1 = nede til højre (-45°), via toppen
    return math.radians(225.0 - 270.0 * frac)


def _draw_knob(path, size, frac, arc_from, highlight, dim):
    cv = _Canvas(size, size)
    c = size / 2.0
    accent = ORANGE if highlight else BLUE
    fade = 0.45 if dim else 1.0

    def ring(radius, thick, f0, f1):
        lo, hi = min(f0, f1), max(f0, f1)

        def sdf(x, y):
            dx, dy = x - c, c - y
            r = math.hypot(dx, dy)
            d_rad = abs(r - radius) - thick / 2.0
            f = (225.0 - math.degrees(math.atan2(dy, dx))) % 360.0 / 270.0
            if f > (1.0 + 360.0 / 270.0) / 2.0:
                f -= 360.0 / 270.0  # punkter i "hullet" forneden tilhører nærmeste ende
            d_ang = max(lo - f, f - hi, 0.0) * math.radians(270.0) * radius
            return max(d_rad, d_ang) if d_ang > 0 else d_rad
        return sdf

    def circle(radius):
        return lambda x, y: math.hypot(x - c, y - c) - radius

    box = (0, 0, size, size)
    if highlight:
        cv.fill(box, ring(size * 0.47, size * 0.025, 0.0, 1.0), ORANGE, 0.35)
    r_arc, t_arc = size * 0.40, size * 0.045
    cv.fill(box, ring(r_arc, t_arc, 0.0, 1.0), (22, 22, 24), fade)
    if abs(frac - arc_from) > 1e-3:
        cv.fill(box, ring(r_arc, t_arc, arc_from, frac), accent, fade)
    cv.fill(box, circle(size * 0.33), (14, 14, 16), fade)
    top, bottom = (214, 214, 217), (138, 138, 143)
    span = size * 0.58

    def grad(x, y):
        t = min(1.0, max(0.0, (y - (c - span / 2)) / span))
        return tuple(int(top[k] + (bottom[k] - top[k]) * t) for k in range(3))
    cv.fill(box, circle(size * 0.29), grad, fade)

    ang = _angle_of(frac)
    ux, uy = math.cos(ang), -math.sin(ang)
    ax, ay = c + ux * size * 0.08, c + uy * size * 0.08
    bx, by = c + ux * size * 0.27, c + uy * size * 0.27
    w = size * 0.035

    def seg(x, y):
        px_, py_ = x - ax, y - ay
        vx, vy = bx - ax, by - ay
        t = max(0.0, min(1.0, (px_ * vx + py_ * vy) / (vx * vx + vy * vy)))
        return math.hypot(px_ - vx * t, py_ - vy * t) - w / 2.0
    cv.fill((min(ax, bx) - w, min(ay, by) - w, max(ax, bx) + w, max(ay, by) + w), seg, (20, 20, 22), fade)
    cv.save(path)


def draw_knob(img_dir, name, size, frac, arc_from, highlight=False, dim=False):
    """Skriver name.png og name@2x.png (Qt vælger selv @2x på Retina). Returnerer stien til 1x."""
    frac = max(0.0, min(1.0, frac))
    base = os.path.join(img_dir, name)
    _draw_knob(base + ".png", size, frac, arc_from, highlight, dim)
    _draw_knob(base + "@2x.png", size * 2, frac, arc_from, highlight, dim)
    return base + ".png"


# ---------------------------------------------------------------- UI

BG = "#1c1c1e"
CARD = "#26262a"
PANEL = "#2c2c30"
MUTED = "#8e8e93"
TEXT = "#e8e8ea"
GREEN, RED, AMBER = "#5fd068", "#ff6b6b", "#ffb84d"
HL = "#ff9f1a"


def fmt(v, unit=""):
    if v == float("-inf"):
        return "–∞"
    return "%.1f" % v + (" " + unit if unit else "")


def _metric_card(label, value, unit, target, color):
    return (
        "<td width='50%%' bgcolor='%s' style='padding:12px 14px'>"
        "<span style='color:%s;font-size:11px'>%s</span><br>"
        "<span style='color:%s;font-size:28px;font-weight:600'>%s</span>"
        "<span style='color:%s;font-size:13px'> %s</span><br>"
        "<span style='color:%s;font-size:11px'>Mål: %s</span></td>"
        % (CARD, MUTED, label.upper(), color, value, MUTED, unit, MUTED, target))


def _value_box(text, caption, highlight, dim):
    # Qt's rich text viser ikke CSS-borders på tabeller, så fremhævning sker med baggrundsfarve
    color = HL if highlight else (MUTED if dim else TEXT)
    bg = "#4a3010" if highlight else "#111113"
    return ("<table align='center' cellspacing='0' cellpadding='5'>"
            "<tr><td align='center' width='96' bgcolor='%s' style='color:%s;font-size:14px;font-weight:600'>%s</td></tr>"
            "<tr><td align='center' style='color:%s;font-size:10px'>%s</td></tr></table>"
            % (bg, color, text, HL if highlight else MUTED, caption))


def _knob_cell(img, title, scale, box, highlight, dim):
    tcol = HL if highlight else (MUTED if dim else TEXT)
    return ("<td align='center' valign='bottom' style='padding:6px 10px'>"
            "<span style='color:%s;font-size:12px'>%s</span><br>"
            "<img src='%s'><br>"
            "<span style='color:%s;font-size:10px'>%s</span><br>%s</td>"
            % (tcol, title, img, MUTED, scale, box))


def limiter_panel(rec, img_dir, tag):
    level, ceiling = rec["level"], rec["ceiling"]
    lo, hi = LEVEL_RANGE
    lvl_shown = 0.0 if level is None else max(lo, min(hi, level))
    lvl_img = draw_knob(img_dir, "level_%s" % tag, 56, (lvl_shown - lo) / (hi - lo), 0.5,
                        highlight=level is not None, dim=level is None)
    lo, hi = CEILING_RANGE
    ceil_shown = -1.0 if ceiling is None else ceiling
    ceil_img = draw_knob(img_dir, "ceiling_%s" % tag, 76, (ceil_shown - lo) / (hi - lo), 1.0,
                         highlight=ceiling is not None, dim=ceiling is None)
    lo, hi = RELEASE_RANGE
    rel_frac = math.log10(RELEASE_DEFAULT / lo) / math.log10(hi / lo)
    rel_img = draw_knob(img_dir, "release_%s" % tag, 56, rel_frac, 0.0, dim=True)

    if level is None:
        lvl_box = _value_box("–", "uændret", False, True)
    else:
        lvl_box = _value_box("%+.1f" % level, "læg til nuværende", True, False)
    if ceiling is None:
        ceil_box = _value_box("–", "uændret", False, True)
    else:
        ceil_box = _value_box("%.1f" % ceiling, "sæt til", True, False)

    head = ("<span style='color:%s;font-size:12px;font-weight:600'>%s</span>")
    return (
        "<table width='100%%' cellspacing='0' cellpadding='10' bgcolor='%s'><tr><td>"
        "<span style='color:%s'>●</span> <span style='color:%s;font-weight:600'>Limiter</span>"
        "<span style='color:%s;font-size:11px'>&nbsp;&nbsp;·&nbsp;&nbsp;Bus 1</span>"
        "<table width='100%%' cellspacing='0' cellpadding='0' style='margin-top:8px'><tr>"
        "<td width='34%%' align='center' bgcolor='%s' style='border:1px solid #3a3a3f;padding:6px'>%s"
        "<table width='100%%'><tr>%s</tr></table></td>"
        "<td width='66%%' align='center' bgcolor='%s' style='border:1px solid #3a3a3f;padding:6px'>%s"
        "<table width='100%%'><tr>%s%s</tr></table></td>"
        "</tr></table></td></tr></table>"
        % (PANEL, RED, TEXT, MUTED,
           "#232326", head % (TEXT, "Input"),
           _knob_cell(lvl_img, "Level", "-18 &nbsp;dB&nbsp; 18", lvl_box, level is not None, level is None),
           "#232326", head % (TEXT, "Limit"),
           _knob_cell(ceil_img, "Ceiling", "-24 &nbsp;dBTP&nbsp; 0", ceil_box, ceiling is not None, ceiling is None),
           _knob_cell(rel_img, "Release", "0.01 &nbsp;ms&nbsp; 1000", _value_box("1.00", "uændret", False, True),
                      False, True)))


def result_html(s, img_dir, tag="0"):
    rec = recommend(s)
    i_col = GREEN if abs(s["integrated"] - TARGET_LUFS) <= TOLERANCE_LU else RED
    if s["true_peak"] > TARGET_TP:
        tp_col = RED
    elif s["true_peak"] < TP_WINDOW_LOW:
        tp_col = AMBER
    else:
        tp_col = GREEN
    mins, secs = divmod(int(round(s["duration"])), 60)

    h = ["<body style='background:%s;color:%s'>" % (BG, TEXT)]
    h.append("<p style='margin:0'><span style='font-size:17px;font-weight:600'>%s</span><br>"
             "<span style='color:%s;font-size:11px'>%d:%02d &nbsp;·&nbsp; %d kanaler &nbsp;·&nbsp; %d kHz</span></p>"
             % (s["timeline"], MUTED, mins, secs, s["channels"], s["samplerate"] // 1000))
    h.append("<table width='100%' cellspacing='0' cellpadding='0' style='margin-top:8px'><tr>")
    h.append(_metric_card("Integrated", fmt(s["integrated"]), "LUFS", fmt(TARGET_LUFS, "LUFS"), i_col))
    h.append("<td width='8'></td>")
    h.append(_metric_card("True Peak", fmt(s["true_peak"]), "dBTP",
                          "%s til %s" % (fmt(TP_WINDOW_LOW), fmt(TARGET_TP, "dBTP")), tp_col))
    h.append("</tr></table>")

    stats = [("Loudness Range", fmt(s["lra"], "LU")), ("Max short-term", fmt(s["max_short_term"], "LUFS")),
             ("Max momentary", fmt(s["max_momentary"], "LUFS")), ("Sample peak", fmt(s["sample_peak"], "dBFS"))]
    h.append("<table width='100%' cellspacing='0' cellpadding='2'><tr>")
    for label, val in stats:
        h.append("<td><span style='color:%s;font-size:10px'>%s</span><br>"
                 "<span style='font-size:12px'>%s</span></td>" % (MUTED, label, val))
    h.append("</tr></table>")

    col = {"ok": GREEN, "juster": AMBER}.get(rec["status"], MUTED)
    icon = {"ok": "✓", "juster": "▲"}.get(rec["status"], "•")
    h.append("<p style='margin-top:14px;color:%s;font-size:13px;font-weight:600'>%s&nbsp; %s</p>"
             % (col, icon, rec["headline"]))

    if rec["status"] != "ingen":
        h.append(limiter_panel(rec, img_dir, tag))
    if rec["status"] == "juster":
        h.append("<p style='color:%s;font-size:11px;margin-top:6px'>Indstil de <span style='color:%s'>"
                 "orange</span> felter, og kør analysen igen for at kontrollere.</p>" % (MUTED, HL))
    for n in rec["notes"]:
        h.append("<p style='color:%s;font-size:11px;margin:2px 0'>•&nbsp; %s</p>" % (MUTED, n))
    h.append("</body>")
    return "".join(h)


def get_resolve():
    g = globals()
    if g.get("resolve"):
        return g["resolve"]
    if g.get("bmd"):
        return g["bmd"].scriptapp("Resolve")
    import DaVinciResolveScript as dvr
    return dvr.scriptapp("Resolve")


def g_bmd():
    g = globals()
    if g.get("bmd"):
        return g["bmd"]
    import DaVinciResolveScript as dvr
    return dvr


WINDOW_CSS = """
QWidget { background-color: %s; color: %s; }
QTextEdit { border: none; }
QTextEdit QScrollBar:horizontal { height: 0px; }
QLabel#Title { font-size: 15px; font-weight: 600; }
QLabel#Status { color: %s; font-size: 11px; }
QPushButton { background-color: #3a3a3f; border: none; border-radius: 6px; padding: 7px 16px; color: %s; }
QPushButton:hover { background-color: #46464c; }
QPushButton#Analyze { background-color: #2f80ed; font-weight: 600; }
QPushButton#Analyze:hover { background-color: #3d8ff5; }
QPushButton#Analyze:disabled { background-color: #2a3c55; color: #8ea6c6; }
""" % (BG, TEXT, MUTED, TEXT)

WELCOME_HTML = (
    "<body style='background:%s'><p style='color:%s;font-size:12px;margin-top:40px' align='center'>"
    "Tryk <b style='color:%s'>Analysér</b> for at måle hele timelinens mix.<br><br>"
    "<span style='font-size:11px'>Mixet renderes midlertidigt, måles og slettes igen.<br>"
    "Timeline og klip bliver ikke ændret.</span></p></body>" % (BG, MUTED, TEXT))


def main():
    res = get_resolve()
    ui = res.Fusion().UIManager
    disp = g_bmd().UIDispatcher(ui)
    img_dir = tempfile.mkdtemp(prefix="loudness_ui_")
    run = [0]

    win = disp.AddWindow(
        {"ID": "LoudnessCheck", "WindowTitle": "Loudness Check", "Geometry": [200, 120, 560, 720],
         "StyleSheet": WINDOW_CSS},
        ui.VGroup({"Spacing": 10}, [
            ui.HGroup({"Weight": 0}, [
                ui.Label({"ID": "Title", "Text": "Loudness Check", "Weight": 1}),
                ui.Label({"ID": "Target", "Text": "YouTube · %.0f LUFS · %.0f dBTP" % (TARGET_LUFS, TARGET_TP),
                          "Alignment": {"AlignRight": True, "AlignVCenter": True}, "StyleSheet": "color:%s" % MUTED}),
            ]),
            ui.TextEdit({"ID": "Result", "ReadOnly": True, "Weight": 1, "HTML": WELCOME_HTML}),
            ui.HGroup({"Weight": 0}, [
                ui.Label({"ID": "Status", "Text": "", "Weight": 1, "WordWrap": True}),
                ui.Button({"ID": "Close", "Text": "Luk", "Weight": 0}),
                ui.Button({"ID": "Analyze", "Text": "Analysér", "Weight": 0}),
            ]),
        ]),
    )
    items = win.GetItems()

    def set_status(text):
        items["Status"].Text = text

    def progress(pct):
        set_status("Måler loudness og true peak …" if pct < 0 else "Renderer mix … %d %%" % pct)

    def on_analyze(ev):
        items["Analyze"].Enabled = False
        items["Analyze"].Text = "Analyserer …"
        set_status("Renderer mix …")
        try:
            project = res.GetProjectManager().GetCurrentProject()
            t0 = time.time()
            stats = analyze(project, progress)
            run[0] += 1
            items["Result"].HTML = result_html(stats, img_dir, str(run[0]))
            set_status("Færdig på %.0f sek." % (time.time() - t0))
        except Exception as e:
            set_status("Fejl: %s" % e)
        items["Analyze"].Text = "Analysér igen" if run[0] else "Analysér"
        items["Analyze"].Enabled = True

    def on_close(ev):
        disp.ExitLoop()

    win.On.Analyze.Clicked = on_analyze
    win.On.Close.Clicked = on_close
    win.On.LoudnessCheck.Close = on_close
    win.Show()
    try:
        disp.RunLoop()
    finally:
        win.Hide()
        shutil.rmtree(img_dir, ignore_errors=True)


if __name__ == "__main__" and not os.environ.get("LOUDNESS_CHECK_NO_UI"):
    main()
