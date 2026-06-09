--[[
  Text+ Font Changer.lua
  Skifter skrifttype på alle Text+ klip i den aktuelle timeline.
  Kørsel: Workspace ▸ Scripts ▸ Comp ▸ Text+ Font Changer
]]


-- ── TTF/OTF font-info ────────────────────────────────────────────────────────
-- Læser nameID=1 (Family) og nameID=16 (Preferred Family) fra name-tabellen,
-- OG usWeightClass + fsSelection fra OS/2-tabellen.
-- OS/2-tallene er sproguafhængige: 400=Regular, 700=Bold uanset om fonten
-- hedder "Gras", "Fett" eller "Negrita" i name-tabellen.

local function u16(s, i) return s:byte(i) * 256 + s:byte(i + 1) end
local function u32(s, i)
    return ((s:byte(i)*256+s:byte(i+1))*256+s:byte(i+2))*256+s:byte(i+3)
end

local OS2_WEIGHT = {
    [100]="Thin", [200]="ExtraLight", [300]="Light", [400]="Regular",
    [500]="Medium", [600]="SemiBold", [700]="Bold", [800]="ExtraBold",
    [900]="Black",
}

-- Returnerer: fam16, fam1, os2_style, sub2, sub17, ps6
-- ps6 = nameID=6 PostScript-navn, unikt per variant ("AUPassata-Bold", "Verdana-Bold").
-- os2_style er engelsk uanset fontens sprog — bruges kun til UI-sortering.
-- Understøtter .ttc-filer (TrueType Collections) ved at parse hvert sub-font.
local function ttf_info(path)
    local f = io.open(path, "rb")
    if not f then return nil, nil, nil, nil, nil, nil end
    local names = {}
    local os2_style = nil

    local function parse_one_font(base)
        f:seek("set", base)
        f:read(4)  -- sfVersion, skip
        local n = u16(f:read(2), 1); f:read(6)
        local name_off, os2_off
        for _ = 1, n do
            local tag = f:read(4); f:read(4)
            local off = u32(f:read(4), 1); f:read(4)
            if tag == "name" then name_off = off
            elseif tag == "OS/2" then os2_off = off end
        end
        if os2_off then
            f:seek("set", os2_off + 4)
            local wc = u16(f:read(2), 1)
            f:seek("set", os2_off + 62)
            local fs   = u16(f:read(2), 1)
            local ital = (fs % 2) == 1
            local w    = math.max(100, math.min(900, math.floor((wc + 50) / 100) * 100))
            local base = OS2_WEIGHT[w] or (wc < 500 and "Regular" or "Bold")
            os2_style  = (ital and base ~= "Regular") and (base .. " Italic")
                      or (ital and "Italic")
                      or base
        end
        if not name_off then return end
        f:seek("set", name_off); f:read(2)
        local cnt  = u16(f:read(2), 1)
        local soff = u16(f:read(2), 1)
        for _ = 1, cnt do
            local rec = f:read(12)
            if not rec or #rec < 12 then break end
            local plat   = u16(rec, 1)
            local nid    = u16(rec, 7)
            local len    = u16(rec, 9)
            local stroff = u16(rec, 11)
            if nid == 1 or nid == 2 or nid == 6 or nid == 16 or nid == 17 then
                local pos = f:seek()
                f:seek("set", name_off + soff + stroff)
                local raw = f:read(len)
                f:seek("set", pos)
                if raw then
                    local s
                    if plat == 3 or plat == 0 then
                        -- plat=3 Windows UTF-16-BE, plat=0 Apple Unicode (også UTF-16-BE)
                        s = ""
                        for i = 1, #raw - 1, 2 do
                            local hi, lo = raw:byte(i), raw:byte(i + 1)
                            if hi == 0 and lo >= 32 then s = s .. string.char(lo) end
                        end
                    else s = raw end  -- plat=1 Mac Roman
                    s = s and s:match("^%s*(.-)%s*$")
                    if s and #s > 0 then
                        if not names[nid] then names[nid] = {} end
                        if not names[nid][plat] then names[nid][plat] = s end
                    end
                end
            end
        end
    end

    pcall(function()
        local magic = f:read(4)
        if magic == "ttcf" then
            f:read(4)  -- TTC version, skip
            local n = u32(f:read(4), 1)
            local offs = {}
            for i = 1, n do offs[i] = u32(f:read(4), 1) end
            for _, off in ipairs(offs) do
                parse_one_font(off)
                if names[16] or names[1] then break end
            end
        else
            f:seek("set", 0)
            parse_one_font(0)
        end
    end)
    f:close()
    -- Familie-navne: foretrækker plat=3 (Windows Unicode, bred tegnsæt-støtte)
    local function best(nid)  return names[nid] and (names[nid][3] or names[nid][1] or names[nid][0]) or nil end
    -- Style-navne: plat=1 (Mac Roman) → plat=0 (Apple Unicode) → plat=3 (Windows)
    -- CoreText/DaVinci på macOS bruger Mac-platform — AU Passata Bold: plat=1="Bold", plat=3="Gras"
    local function bests(nid) return names[nid] and (names[nid][1] or names[nid][0] or names[nid][3]) or nil end
    return best(16), best(1), os2_style, bests(2), bests(17), best(6)
end

-- Suffiksliste til at fjerne style-ordet fra nameID=1 og finde basis-familienavnet.
-- Inkluderer lokaliserede navne (FR/DE/NL) da de optræder i nameID=1 på alle platforme.
-- Længste suffiks først.
local STYLE_SUFFIXES = {
    " Bold Italic",       " BoldItalic",
    " Extra Bold Italic",  " ExtraBoldItalic",
    " Extra Bold",         " ExtraBold",
    " Extra Light Italic", " ExtraLightItalic",
    " Extra Light",        " ExtraLight",
    " Semi Bold Italic",   " SemiBoldItalic",
    " Semi Bold",          " SemiBold",
    " Demi Bold",          " DemiBold",
    " Light Italic",       " LightItalic",
    " Light",
    " Medium Italic",      " MediumItalic",
    " Medium",
    " Thin Italic",        " ThinItalic",
    " Thin",
    " Black Italic",       " BlackItalic",
    " Black",
    " Heavy Italic",       " HeavyItalic",
    " Heavy",
    " Bold",
    " Italic",
    " Regular",
    " Normal",             -- FR/alternativ for Regular
    " Roman",
    " Condensed",
    " Expanded",
    -- Lokaliserede style-ord der kan sidde i nameID=1 (FR/DE/NL/ES)
    " Gras Italique",      " GrasItalique",   -- FR: Bold Italic
    " Gras",                                  -- FR: Bold
    " Italique",                              -- FR: Italic
    " Maigre",                                -- FR: Light
    " Fett Kursiv",        " FettKursiv",     -- DE: Bold Italic
    " Fett",                                  -- DE: Bold
    " Kursiv",                                -- DE: Italic
    " Mager",                                 -- DE: Light
    " Vet",                                   -- NL: Bold
    " Negrita",                               -- ES: Bold
}

local function strip_style_suffix(name1)
    local lower = name1:lower()
    for _, suf in ipairs(STYLE_SUFFIXES) do
        if lower:sub(-#suf) == suf:lower() then
            return name1:sub(1, -#suf - 1)
        end
    end
    return name1
end

-- Rækkefølge for sortering af styles
local STYLE_ORDER = {
    Thin=1,  ["Thin Italic"]=2,
    ["Extra Light"]=3, ["Extra Light Italic"]=4,
    ExtraLight=3, ["ExtraLight Italic"]=4,
    Light=5, ["Light Italic"]=6,
    Regular=7, Roman=7, Italic=8,
    Medium=9, ["Medium Italic"]=10,
    ["Semi Bold"]=11, ["Semi Bold Italic"]=12,
    SemiBold=11, ["SemiBold Italic"]=12,
    Bold=13, ["Bold Italic"]=14,
    ["Extra Bold"]=15, ["Extra Bold Italic"]=16,
    ExtraBold=15, ["ExtraBold Italic"]=16,
    Black=17, ["Black Italic"]=18,
}

local function load_all_fonts()
    local dirs = {
        "/Library/Fonts",
        (os.getenv("HOME") or "") .. "/Library/Fonts",
        "/System/Library/Fonts",
        "/System/Library/Fonts/Supplemental",
        "/Library/Application Support/Adobe/Fonts",
        "/Library/Application Support/Blackmagic Design/DaVinci Resolve/Fusion/Fonts",
        (os.getenv("HOME") or "") ..
            "/Library/Application Support/Blackmagic Design/DaVinci Resolve/Fusion/Fonts",
    }

    -- Pas 1: indsaml rådata fra alle fontfiler
    local raw = {}          -- { fam16, fam1, os2_style, sub2, sub17, ps6 }
    local name1_set = {}    -- alle nameID=1 værdier der mangler nameID=16

    for _, dir in ipairs(dirs) do
        local h = io.popen(
            "find '" .. dir .. "' -maxdepth 3 \\( -iname '*.ttf' -o -iname '*.otf' -o -iname '*.ttc' \\) 2>/dev/null"
        )
        if h then
            for fp in h:lines() do
                local fam16, fam1, os2_sty, sub2, sub17, ps6 = ttf_info(fp)
                if fam16 or fam1 then
                    table.insert(raw, {fam16=fam16, fam1=fam1, sty=os2_sty or "Regular",
                                       sub2=sub2, sub17=sub17, ps6=ps6})
                    if fam1 and not fam16 then name1_set[fam1] = true end
                end
            end
            h:close()
        end
    end

    -- Pas 2: afgør visningsfamilienavn og gem DaVinci Style-nøgle pr. stil.
    --
    -- Font-input = display_family (nameID=16 eller afkortet nameID=1).
    -- Style-input = nameID=2/17 plat=1 (Mac Roman) — hvad CoreText/DaVinci returnerer på macOS.
    --   AU Passata Bold: nameID=2 plat=1 = "Bold" (plat=3 = "Gras" er forkert for DaVinci).
    -- Fallback: nameID=1-suffix (for fonte uden nameID=2/17 plat=1, fx Verdana).
    --
    -- font_map[display_family][os2_label] = {ps6=postscript, suffix=davinci_style}
    local font_map = {}

    local function add(display_family, display_label, ps6, suffix)
        display_label = (display_label ~= "") and display_label or "Regular"
        if not font_map[display_family] then font_map[display_family] = {} end
        if not font_map[display_family][display_label] then
            font_map[display_family][display_label] = {ps6=ps6, suffix=suffix or ""}
        end
    end

    local function derive_display_family(r)
        if r.fam16 then return r.fam16 end
        if not r.fam1 then return nil end
        -- Forsøg: fjern det sidste ord og se om resten er en nameID=1 uden fam16
        local words = {}
        for w in r.fam1:gmatch("%S+") do table.insert(words, w) end
        for rm = 1, math.min(3, #words - 1) do
            local candidate = table.concat(words, " ", 1, #words - rm)
            if name1_set[candidate] then return candidate end
        end
        -- Fallback: fjern kendte suffikser
        return strip_style_suffix(r.fam1)
    end

    -- Fallback: nameID=1-suffix bruges kun for fonte uden nameID=2/17.
    local function name1_suffix(family, name1)
        if not name1 or name1 == family then return "" end
        local prefix = family .. " "
        if name1:sub(1, #prefix) == prefix then return name1:sub(#prefix + 1) end
        return ""
    end

    for _, r in ipairs(raw) do
        local family = derive_display_family(r)
        if family and family ~= "" then
            -- sub17/sub2 er allerede plat=1-foretrukket (Mac) via bests() i ttf_info.
            -- "Bold" (plat=1) i stedet for "Gras" (plat=3) for AU Passata Bold.
            local dv_style = r.sub17 or r.sub2 or name1_suffix(family, r.fam1)
            add(family, r.sty, r.ps6, dv_style)
        end
    end

    local font_list  = {}
    -- styles_map[display_family] = { {label=os2_label, font_key=family, ps6=postscript, suffix=nameID1_suffix}, ... }
    local styles_map = {}
    for family, label_map in pairs(font_map) do
        table.insert(font_list, family)
        local styles = {}
        for label, info in pairs(label_map) do
            table.insert(styles, {label=label, font_key=family, ps6=info.ps6, suffix=info.suffix})
        end
        table.sort(styles, function(a, b)
            local oa = STYLE_ORDER[a.label] or 50
            local ob = STYLE_ORDER[b.label] or 50
            if oa ~= ob then return oa < ob end
            return a.label:lower() < b.label:lower()
        end)
        styles_map[family] = styles
    end

    table.sort(font_list, function(a, b) return a:lower() < b:lower() end)
    return font_list, styles_map
end


-- ── Resolve helpers ──────────────────────────────────────────────────────────

local resolve = resolve or bmd.scriptapp("Resolve")

-- Scaner alle Text+-klip og returnerer unikke {display, font, style} par.
-- "display" bruger præcis hvad DaVinci har gemt — garanteret at virke med SetInput.
local function scan_tl_exact(tl)
    local found, seen = {}, {}
    for ti = 1, tl:GetTrackCount("video") do
        for _, item in ipairs(tl:GetItemListInTrack("video", ti) or {}) do
            for ci = 1, item:GetFusionCompCount() do
                local comp = item:GetFusionCompByIndex(ci)
                if comp then
                    for _, t in pairs(comp:GetToolList(false, "TextPlus")) do
                        local f = tostring(t:GetInput("Font")  or "")
                        local s = tostring(t:GetInput("Style") or "")
                        if f ~= "" then
                            local disp = "● " .. f .. (s ~= "" and " ["..s.."]" or " [Regular]")
                            if not seen[disp] then
                                seen[disp] = true
                                table.insert(found, {display=disp, font=f, style=s})
                            end
                        end
                    end
                end
            end
        end
    end
    table.sort(found, function(a,b) return a.display:lower() < b.display:lower() end)
    return found
end

-- track_idx: 0 = alle tracks, ellers specifik track-indeks
local function fonts_in_timeline(tl, track_idx)
    local found, seen = {}, {}
    local from_ti = (track_idx and track_idx > 0) and track_idx or 1
    local to_ti   = (track_idx and track_idx > 0) and track_idx or tl:GetTrackCount("video")
    for ti = from_ti, to_ti do
        for _, item in ipairs(tl:GetItemListInTrack("video", ti) or {}) do
            for ci = 1, item:GetFusionCompCount() do
                local comp = item:GetFusionCompByIndex(ci)
                if comp then
                    for _, t in pairs(comp:GetToolList(false, "TextPlus")) do
                        local fv = t:GetInput("Font")
                        local fs = fv and tostring(fv) or nil
                        if fs and fs ~= "" and not seen[fs] then
                            seen[fs] = true
                            table.insert(found, fs)
                        end
                    end
                end
            end
        end
    end
    table.sort(found)
    return found
end

local function iter_text_tools(tl, filter_font, track_idx)
    local results = {}
    for ti = 1, tl:GetTrackCount("video") do
        if track_idx == 0 or ti == track_idx then
            for _, item in ipairs(tl:GetItemListInTrack("video", ti) or {}) do
                for ci = 1, item:GetFusionCompCount() do
                    local comp = item:GetFusionCompByIndex(ci)
                    if comp then
                        for _, t in pairs(comp:GetToolList(false, "TextPlus")) do
                            local cur = tostring(t:GetInput("Font") or "")
                            if filter_font == "" or cur == filter_font then
                                -- comp med i resultatet til Lock/Unlock
                                table.insert(results, {
                                    ti   = ti,
                                    item = item,
                                    comp = comp,
                                    tool = t,
                                    cur  = cur,
                                })
                            end
                        end
                    end
                end
            end
        end
    end
    return results
end

local function apply_change(matches, new_font, style_key, style_label, _ps6)
    local changed, errors, lines = 0, 0, {}

    for _, m in ipairs(matches) do
        local ok, err = pcall(function()
            m.tool:SetInput("Style", style_key)
            m.tool:SetInput("Font",  new_font)
            if app then app:Sleep(0.02) end
            changed = changed + 1
            table.insert(lines, string.format(
                "[Track %d] %q → Font=%q Style=%q",
                m.ti, m.item:GetName(), new_font, style_key))
        end)
        if not ok then
            errors = errors + 1
            table.insert(lines, "FEJL " .. m.item:GetName() .. ": " .. tostring(err))
        end
    end

    return changed, errors, lines
end


-- ── GUI ──────────────────────────────────────────────────────────────────────

local project  = resolve:GetProjectManager():GetCurrentProject()
local timeline = project and project:GetCurrentTimeline()

print("Text+ Font Changer: henter fonte og styles via NSFontManager…")
local all_fonts, styles_map = load_all_fonts()
print(string.format("Fandt %d fonte.", #all_fonts))

local track_names = {}
if timeline then
    for ti = 1, timeline:GetTrackCount("video") do
        local name = timeline:GetTrackName("video", ti)
        table.insert(track_names, string.format("V%d – %s", ti, name or ""))
    end
end
local track_items = {"(Alle tracks)"}
for _, n in ipairs(track_names) do table.insert(track_items, n) end

local current_tl_fonts = timeline and fonts_in_timeline(timeline, 0) or {}

local fusion = fusion or bmd.scriptapp("Fusion")
local ui     = fusion.UIManager
local disp   = bmd.UIDispatcher(ui)

local tl_name = timeline and timeline:GetName() or "ingen timeline"

local win = disp:AddWindow({
    ID          = "FC",
    WindowTitle = "Text+ Font Changer  [" .. tl_name .. "]",
    Geometry    = {300, 150, 500, 650},
}, {
    ui:VGroup({Spacing = 8, Weight = 1}, {

        ui:HGroup({Weight = 0, Spacing = 6}, {
            ui:Label({Text = "Søg:", Weight = 0}),
            ui:LineEdit({
                ID              = "Search",
                PlaceholderText = "Skriv for at filtrere fontlisten…",
                Weight          = 1,
            }),
            ui:Button({
                ID      = "Scan",
                Text    = "⟳ Scan timeline",
                Weight  = 0,
                ToolTip = "Læs Font+Style fra alle klip i timelineen (● = fra DaVinci, garanteret korrekte værdier)",
            }),
        }),

        ui:Tree({
            ID                   = "FontList",
            Weight               = 1,
            SortingEnabled       = false,
            RootIsDecorated      = false,
            AlternatingRowColors = true,
            SelectionMode        = "SingleSelection",
        }),

        ui:HGroup({Weight = 0, Spacing = 8}, {
            ui:Label({Text = "Udtryk (style):", Weight = 0}),
            ui:ComboBox({ID = "StyleCombo", Weight = 1}),
        }),

        ui:HGroup({Weight = 0, Spacing = 8}, {
            ui:Label({Text = "Track:",            Weight = 0}),
            ui:ComboBox({ID = "TrackCombo",        Weight = 1}),
        }),

        ui:HGroup({Weight = 0, Spacing = 8}, {
            ui:Label({Text = "Kun klip med font:", Weight = 0}),
            ui:ComboBox({ID = "FilterCombo",       Weight = 1}),
        }),

        ui:HGroup({Weight = 0, Spacing = 8}, {
            ui:Button({ID = "Test",  Text = "Test på ét klip", Weight = 1}),
            ui:Button({ID = "Apply", Text = "Anvend på alle",  Weight = 1}),
            ui:Button({ID = "Close", Text = "Luk",             Weight = 0}),
        }),

        ui:TextEdit({
            ID              = "Log",
            ReadOnly        = true,
            Weight          = 0,
            MinimumSize     = {0, 110},
            PlaceholderText = "Resultater vises her…",
        }),
    }),
})

local tree        = win:Find("FontList")
local style_combo = win:Find("StyleCombo")
local track_combo = win:Find("TrackCombo")
local filt        = win:Find("FilterCombo")
local log_box     = win:Find("Log")

tree:SetHeaderLabels({"Skrifttype"})

-- tl_exact_map: display_name → {font, style} med præcise DaVinci-værdier fra GetInput.
-- "● AU Passata [Bold]" → {font="AU Passata", style="<hvad DaVinci faktisk bruger>"}
local tl_exact_map = {}

local function fill_tree(fonts)
    tree:Clear()
    -- Øverst: timeline-scannede (● prefix) — garanteret korrekte værdier
    local exact_keys = {}
    for k in pairs(tl_exact_map) do table.insert(exact_keys, k) end
    table.sort(exact_keys)
    for _, k in ipairs(exact_keys) do
        local item = tree:NewItem()
        item.Text[0] = k
        tree:AddTopLevelItem(item)
    end
    -- Derefter: fil-scannede
    for _, name in ipairs(fonts) do
        local item   = tree:NewItem()
        item.Text[0] = name
        tree:AddTopLevelItem(item)
    end
end

local function update_styles(font_name)
    if font_name and tl_exact_map[font_name] then
        -- Timeline-scannet: fast style fra DaVinci
        local s = tl_exact_map[font_name].style
        style_combo:Clear()
        style_combo:AddItems({s ~= "" and s or "Regular"})
        style_combo.CurrentIndex = 0
        return
    end
    local entries = (font_name and styles_map[font_name]) or {{label="Regular", font_key=font_name, ps6=nil, suffix=""}}
    style_combo:Clear()
    local labels = {}
    for _, e in ipairs(entries) do table.insert(labels, e.label) end
    style_combo:AddItems(labels)
    for i, e in ipairs(entries) do
        if e.label:lower() == "regular" then
            style_combo.CurrentIndex = i - 1
            return
        end
    end
end

local function do_scan()
    tl_exact_map = {}
    if not timeline then log_box.PlainText = "Ingen aktiv timeline."; return end
    local scanned = scan_tl_exact(timeline)
    for _, e in ipairs(scanned) do tl_exact_map[e.display] = e end
    local q = (win:Find("Search").Text or ""):lower()
    local filtered = {}
    for _, f in ipairs(all_fonts) do
        if q == "" or f:lower():find(q, 1, true) then table.insert(filtered, f) end
    end
    fill_tree(filtered)
    log_box.PlainText = string.format(
        "Scannet %d fonte fra timeline (● = brug disse til AU Passata o.lign.).\n" ..
        "Fil-scannede fonte vises nedenunder.",
        #scanned)
end

local function refresh_font_filter(track_idx)
    current_tl_fonts = timeline and fonts_in_timeline(timeline, track_idx) or {}
    filt:Clear()
    local items = {"(Ændr alle fonts)"}
    for _, f in ipairs(current_tl_fonts) do table.insert(items, f) end
    filt:AddItems(items)
end

local function get_selection()
    if not timeline then return nil, nil, nil, nil, nil, nil, "FEJL: Ingen aktiv timeline." end
    local sel = tree:CurrentItem()
    if not sel     then return nil, nil, nil, nil, nil, nil, "Vælg en skrifttype i listen." end
    local display_font = sel.Text[0]
    local track_idx    = win:Find("TrackCombo").CurrentIndex
    local fi           = filt.CurrentIndex
    local filter_font  = (fi == 0) and "" or current_tl_fonts[fi]

    -- Timeline-scannet (● prefix): brug præcise værdier fra DaVinci — omgår alle navneproblem
    if tl_exact_map[display_font] then
        local ex = tl_exact_map[display_font]
        return ex.font, ex.style, track_idx, filter_font, ex.style, nil, nil
    end

    local si      = style_combo.CurrentIndex
    local entries = styles_map[display_font] or {{label="Regular", font_key=display_font, ps6=nil, suffix=""}}
    local entry   = entries[si + 1] or entries[1] or {label="Regular", font_key=display_font, ps6=nil, suffix=""}
    return entry.font_key, entry.suffix, track_idx, filter_font, entry.label, entry.ps6, nil
end

fill_tree(all_fonts)
update_styles(nil)
track_combo:AddItems(track_items)
refresh_font_filter(0)

-- ── Events ───────────────────────────────────────────────────────────────────

win.On.FC.Close      = function() disp:ExitLoop() end
win.On.Close.Clicked = function() disp:ExitLoop() end
win.On.Scan.Clicked  = function() do_scan() end

win.On.Search.TextChanged = function()
    local q = (win:Find("Search").Text or ""):lower()
    if q == "" then
        visible_fonts = all_fonts
    else
        visible_fonts = {}
        for _, f in ipairs(all_fonts) do
            if f:lower():find(q, 1, true) then table.insert(visible_fonts, f) end
        end
    end
    fill_tree(visible_fonts)
end

local function on_font_selected()
    local sel = tree:CurrentItem()
    if sel then update_styles(sel.Text[0]) end
end

win.On.FontList.CurrentItemChanged = on_font_selected
win.On.FontList.ItemClicked        = on_font_selected

win.On.TrackCombo.CurrentIndexChanged = function()
    refresh_font_filter(win:Find("TrackCombo").CurrentIndex)
end

win.On.Test.Clicked = function()
    local new_font, style_key, track_idx, filter_font, style_label, ps6, err = get_selection()
    if err then log_box.PlainText = err; return end
    local matches = iter_text_tools(timeline, filter_font, track_idx)
    if #matches == 0 then log_box.PlainText = "Ingen Text+-klip matcher filteret."; return end
    local changed, errors, lines = apply_change({matches[1]}, new_font, style_key, style_label, ps6)
    local s = string.format("Test på ét klip.  Ændret: %d", changed)
    if errors > 0 then s = s .. string.format("   Fejl: %d", errors) end
    s = s .. "\nKontrollér i Resolve — klik 'Anvend på alle' hvis det ser rigtigt ud."
    log_box.PlainText = s .. "\n\n" .. table.concat(lines, "\n")
end

win.On.Apply.Clicked = function()
    local new_font, style_key, track_idx, filter_font, style_label, ps6, err = get_selection()
    if err then log_box.PlainText = err; return end
    local matches = iter_text_tools(timeline, filter_font, track_idx)
    if #matches == 0 then log_box.PlainText = "Ingen Text+-klip matcher filteret."; return end
    local changed, errors, lines = apply_change(matches, new_font, style_key, style_label, ps6)
    local s = string.format("Færdig.  Ændret: %d   Fejl: %d", changed, errors)
    log_box.PlainText = s .. "\n\n" .. table.concat(lines, "\n")
end

win:Show()
disp:RunLoop()
win:Hide()
