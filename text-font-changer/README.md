# Text+ Font Changer

**Download:** [github.com/augustagerskov/Davinci-goodies](https://github.com/augustagerskov/Davinci-goodies)

Skifter skrifttype på alle Text+ klip i den aktuelle DaVinci Resolve timeline via en GUI-dialog. Kører som Lua-script direkte fra Resolve's script-menu.

## Installation

Kopiér `Text+ Font Changer.lua` til:

**macOS:**
```
~/Library/Application Support/Blackmagic Design/DaVinci Resolve/Fusion/Scripts/Comp/
```

Scriptet vises herefter under `Workspace ▸ Scripts ▸ Comp ▸ Text+ Font Changer`.

## Brug

### Standardfonte (Verdana, Arial, osv.)

1. Åbn scriptet fra Script-menuen
2. Søg eller rul til den ønskede skrifttype i listen
3. Vælg udtryk (Bold, Regular osv.) i dropdown'en
4. Klik **"Test på ét klip"** for at verificere resultatet
5. Klik **"Anvend på alle"** for at ændre alle klip

### Kommercielle fonte med ikke-standard navngivning (AU Passata o.lign.)

Nogle fonte kan ikke sættes direkte via font-filen alene — f.eks. AU Passata Bold, hvor fontfilens interne navn ikke inkluderer "Bold". Brug i stedet **Scan timeline**-metoden:

1. Sæt **ét enkelt klip** til den ønskede font manuelt via DaVinci's egne dropdowns
2. Klik **"⟳ Scan timeline"** i scriptet
3. Øverst i listen vises nu `● AU Passata [Bold]` (præcis hvad DaVinci gemt internt)
4. Vælg den og klik **"Anvend på alle"**

`●`-entries bruger de præcise værdier DaVinci selv bruger — ingen gætteri.

## Vinduet

```
┌──────────────────────────────────────────────┐
│  Text+ Font Changer  [Min Timeline]          │
├──────────────────────────────────────────────┤
│  Søg: [________________]  [⟳ Scan timeline] │
│                                              │
│  ┌────────────────────────────────────────┐  │
│  │ ● AU Passata [Bold]   ← fra timeline   │  │
│  │ ● Verdana [Bold]      ← fra timeline   │  │
│  │ ─────────────────────────────────────  │  │
│  │ Arial                                  │  │
│  │ AU Passata                             │  │
│  │ Verdana                ← fra fontfiler │  │
│  └────────────────────────────────────────┘  │
│                                              │
│  Udtryk (style): [ Bold              ▼ ]     │
│  Track:          [ (Alle tracks)     ▼ ]     │
│  Kun klip m. font:[ (Ændr alle fonts)▼ ]     │
│                                              │
│  [ Test på ét klip ]  [ Anvend på alle ]     │
│                                              │
│  Resultater vises her…                       │
└──────────────────────────────────────────────┘
```

| Element | Beskrivelse |
|---|---|
| **Søg** | Filtrerer fontlisten mens du skriver |
| **⟳ Scan timeline** | Læser Font+Style fra alle eksisterende klip |
| **● Font [Style]** | Præcise værdier fra DaVinci — brug disse til svære fonte |
| **Udtryk (style)** | Vælg Bold, Regular, Light osv. |
| **Track** | Begræns til én video-track eller alle |
| **Kun klip med font** | Ændr kun klip med en bestemt nuværende font |
| **Test på ét klip** | Afprøv på første matchende klip |
| **Anvend på alle** | Kør på alle matchende klip |

## Hvad scriptet gør

- Gennemgår alle video-tracks i den aktive timeline
- Finder klip med Fusion-kompositioner
- Finder alle **TextPlus**-noder
- Ændrer `Font` og `Style` — alle andre effekter forbliver uændrede
- Understøtter `.ttc`-filer (TrueType Collections)

## Bemærk

Hvis en Text+ har inline-formateret tekst (formateret direkte i tekstboksen), tilsidesætter det Font-parametret. Åbn klippet i Fusion og tjek Characters-tabben.
