# Loudness Check

**Download:** [github.com/augustagerskov/Davinci-goodies](https://github.com/augustagerskov/Davinci-goodies)

Måler loudness (LUFS) og True Peak for hele timelinens mix i DaVinci Resolve og tjekker det mod YouTube-standarden. Viser bagefter præcis hvilke værdier du skal sætte på en Limiter på Bus 1 — tegnet som Fairlights egen Limiter, med de felter du skal ændre fremhævet i orange.

Timeline og klip bliver **ikke** ændret. Mixet renderes midlertidigt, måles og slettes igen.

## Installation

Kopiér `Loudness Check.py` til:

**macOS:**
```
~/Library/Application Support/Blackmagic Design/DaVinci Resolve/Fusion/Scripts/Utility/
```

Scriptet vises herefter under `Workspace ▸ Scripts ▸ Loudness Check` på alle sider. Genstart Resolve, hvis det ikke dukker op.

**Krav:** DaVinci Resolve Studio og Python 3 installeret (det samme Python som Resolve bruger til scripts). Ingen ekstra pakker — ingen ffmpeg, ingen numpy.

## Brug

1. Åbn den timeline du vil tjekke
2. Kør `Workspace ▸ Scripts ▸ Loudness Check`
3. Klik **Analysér** — det tager typisk få sekunder
4. Sæt de **orange** værdier på Limiteren på Bus 1 (i Fairlight-mixeren: en effekt-slot på Bus 1 ▸ Fairlight FX ▸ Limiter)
5. Klik **Analysér igen** for at kontrollere resultatet

**Level** er en ændring der lægges til limiterens *nuværende* Input Level (analysen måler mixet inklusive den limiter, der allerede sidder på Bus 1). Står Level på 2.3 og scriptet siger +0.5, skal den altså stå på 2.8. **Ceiling** er en absolut værdi.

## Vinduet

```
┌──────────────────────────────────────────────────┐
│ Loudness Check          YouTube · -14 LUFS · -1  │
├──────────────────────────────────────────────────┤
│ Min timeline                                     │
│ 0:46 · 2 kanaler · 48 kHz                        │
│ ┌──────────────────────┐ ┌─────────────────────┐ │
│ │ INTEGRATED           │ │ TRUE PEAK           │ │
│ │ -16.3 LUFS           │ │ -1.4 dBTP           │ │
│ │ Mål: -14.0 LUFS      │ │ Mål: -1.3 til -1.0  │ │
│ └──────────────────────┘ └─────────────────────┘ │
│ LRA 10.7 LU · Max ST -12.0 · Max M -10.4 · ...   │
│                                                  │
│ ▲ Mixet er 2.3 LU for lavt. YouTube hæver ...    │
│ ┌─ ● Limiter · Bus 1 ──────────────────────────┐ │
│ │   Input    │          Limit                  │ │
│ │   Level    │   Ceiling        Release        │ │
│ │    (◔)     │     (◕)            (◔)          │ │
│ │  [ +2.3 ]  │   [ -1.1 ]       [ 1.00 ]       │ │
│ │ læg til    │   sæt til        uændret        │ │
│ └────────────┴─────────────────────────────────┘ │
│ • Limiteren vil tage ca. 2.0 dB af peaks         │
├──────────────────────────────────────────────────┤
│ Færdig på 3 sek.           [ Luk ] [ Analysér ]  │
└──────────────────────────────────────────────────┘
```

| Farve | Betydning |
|---|---|
| 🟢 Grøn | Inden for målet |
| 🟠 Orange | True Peak under -1.3 dBTP (unødigt langt fra grænsen) / felter du skal ændre |
| 🔴 Rød | Uden for målet |

## Målene (YouTube)

| | Mål |
|---|---|
| Integrated loudness | -14 LUFS (±1 LU) |
| True Peak | -1.3 til -1.0 dBTP |
| Anbefalet Limiter Ceiling | -1.1 dBTP |

Ceiling foreslås til -1.1 og ikke -1.0, fordi intersample peaks typisk kan slippe 0.1–0.2 dB forbi en limiter. Grænserne står øverst i scriptet (`TARGET_LUFS`, `TARGET_TP`, `TP_WINDOW_LOW`, `LIMITER_CEILING`), hvis du vil bruge andre.

## Hvad scriptet gør

- Gemmer dine render-indstillinger som et midlertidigt preset
- Renderer hele timelinens mix (Bus 1-outputtet) til en midlertidig 24-bit WAV
- Måler efter **ITU-R BS.1770-4 / EBU R128**: Integrated, Loudness Range, Max short-term, Max momentary
- Måler **True Peak** med 4× oversampling
- Sletter WAV-filen og render-jobbet og gendanner dine render-indstillinger
- Andre jobs i render-køen bliver ikke rørt

Målingen er valideret mod EBU-testsignaler (1 kHz sinus ved -23 dBFS giver -23.0 LUFS, og intersample peaks fanges korrekt).

## Bemærk

- **Scriptet kan ikke selv sætte limiteren på.** Resolves script-API har ingen adgang til Fairlight-effekter, så værdierne skal sættes manuelt.
- Hæv gain med limiterens **Input Level** — ikke med Bus 1-faderen. Faderen sidder efter limiteren, så peaks ville gå over ceiling.
- Hvis limiteren slet ikke arbejder (ingen gain reduction), er lave peaks bare et tegn på et dynamisk mix — det er fint.
- Kan ikke køre mens en anden render er i gang.
