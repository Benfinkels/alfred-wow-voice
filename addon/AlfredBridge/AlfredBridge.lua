-- AlfredBridge.lua
-- Live WoW Forever telemetry, WaypointUI 3D arrows, and Spoken audio pause/resume for Alfred Discord Voice

AlfredBridgeDB = AlfredBridgeDB or { moments = false }

BINDING_HEADER_ALFREDBRIDGE = "AlfredBridge"
BINDING_NAME_ALFREDBRIDGE_LOAD_PIN = "Load Next Waypoint Pin"
BINDING_NAME_ALFREDBRIDGE_DUCK_ON = "Duck Spoken TTS (Pause)"
BINDING_NAME_ALFREDBRIDGE_DUCK_OFF = "Unduck Spoken TTS (Resume)"
BINDING_NAME_ALFREDBRIDGE_COPY_STATE = "Copy State to Clipboard"

AlfredBridge = AlfredBridge or {}
AlfredBridge.nextPinSlot = 1
AlfredBridge.lastPinSeq = 0
AlfredBridge.pausedSpoken = false
AlfredBridge.zoneToMap = {}
AlfredBridge.activeMoment = nil
AlfredBridge.momentTimer = 0
AlfredBridge.deadTimestamps = {}
AlfredBridge.lastX = -1
AlfredBridge.lastY = -1
AlfredBridge.lastStateHash = ""
AlfredBridge.hideTimer = 0
AlfredBridge.heartbeatTimer = 0
AlfredBridge.screenshotSuppressTimer = 0
AlfredBridge.lastJson = ""

-- CRC-16/CCITT-FALSE (poly=0x1021, init=0xFFFF, no final xor)
local function crc16_ccitt(str)
    local crc = 0xFFFF
    for i = 1, #str do
        local b = string.byte(str, i)
        crc = bit.bxor(crc, bit.lshift(b, 8))
        for _ = 1, 8 do
            if bit.band(crc, 0x8000) ~= 0 then
                crc = bit.bxor(bit.lshift(crc, 1), 0x1021)
            else
                crc = bit.lshift(crc, 1)
            end
            crc = bit.band(crc, 0xFFFF)
        end
    end
    return crc
end

-- WoW 12.x (Midnight / Forever) secret-value guards: dungeon/combat unit APIs return opaque secret values
local function isSecret(v)
    return (issecretvalue and issecretvalue(v)) or false
end

local function cleanStr(v, fallback)
    if v == nil or isSecret(v) or type(v) ~= "string" then
        return fallback or ""
    end
    return v
end

local function cleanNum(v, fallback)
    if v == nil or isSecret(v) or type(v) ~= "number" then
        return fallback or 0
    end
    return v
end

local function escapeJson(s)
    if not s or isSecret(s) then return '""' end
    local str = tostring(s)
    str = str:gsub('\\', '\\\\')
    str = str:gsub('"', '\\"')
    str = str:gsub('\n', ' ')
    str = str:gsub('\r', '')
    str = str:gsub('\t', ' ')
    return '"' .. str .. '"'
end

local function getTargetNpcId()
    local guid = UnitGUID("target")
    if not guid or isSecret(guid) or type(guid) ~= "string" then return "null" end
    -- Format: Creature-0-server-instance-zone-npcID-spawnUID
    local parts = { strsplit("-", guid) }
    if (parts[1] == "Creature" or parts[1] == "Vehicle") and parts[6] then
        local npcId = tonumber(parts[6])
        if npcId then return tostring(npcId) end
    end
    return "null"
end

-- Stealth Hairline Optical Strip v3: 898x1 single-row hairline of true 2x2 physical pixels (1796x2 px).
-- Uses GetPhysicalScreenSize() for exact 1:1 physical pixels on 1080p/1440p/4K and subtle dark charcoal
-- levels (0.08 vs 0.20) with a 2-cell self-calibrating preamble so there is NO bright rainbow artifact.
local STRIP_DATA_CELLS = 896
local STRIP_TOTAL_CELLS = 898
local STRIP_MAX_PAYLOAD = 329
local TONE_LOW = 0.08
local TONE_HIGH = 0.20
local stripFrame = CreateFrame("Frame", "AlfredBridgeStripFrame", UIParent)
stripFrame:SetFrameStrata("TOOLTIP")
stripFrame:EnableMouse(false)
stripFrame:SetPoint("TOPLEFT", UIParent, "TOPLEFT", 0, 0)
stripFrame:Hide()

local cells = {}

local function setCellTone(tex, r, g, b)
    if not tex then return end
    if tex.SetColorTexture then
        tex:SetColorTexture(r, g, b, 1.0)
    else
        tex:SetTexture(r, g, b, 1.0)
    end
end

local function updateStripLayout()
    local _, ph = GetPhysicalScreenSize()
    local scale = UIParent:GetEffectiveScale() or 1
    if scale <= 0 then scale = 1 end
    local onePx = 768 / ((ph or 768) * scale)
    local cellW = 2 * onePx
    local cellH = 2 * onePx
    stripFrame:SetSize(STRIP_TOTAL_CELLS * cellW, cellH)
    for col = 0, STRIP_TOTAL_CELLS - 1 do
        local idx = col + 1
        if not cells[idx] then
            cells[idx] = stripFrame:CreateTexture(nil, "OVERLAY")
        end
        cells[idx]:ClearAllPoints()
        cells[idx]:SetSize(cellW, cellH)
        cells[idx]:SetPoint("TOPLEFT", stripFrame, "TOPLEFT", col * cellW, 0)
    end
end

local function renderStrip(jsonPayload)
    if #cells < STRIP_TOTAL_CELLS then
        updateStripLayout()
    end
    local L = #jsonPayload
    if L > STRIP_MAX_PAYLOAD then
        jsonPayload = string.sub(jsonPayload, 1, STRIP_MAX_PAYLOAD)
        L = STRIP_MAX_PAYLOAD
    end
    local crc = crc16_ccitt(jsonPayload)

    -- Binary buffer v3: 336 bytes (7-byte header + up to 329-byte payload)
    local buf = {}
    buf[1] = 0xA5
    buf[2] = 0x5A
    buf[3] = 0x03
    buf[4] = bit.band(bit.rshift(L, 8), 0xFF)
    buf[5] = bit.band(L, 0xFF)
    buf[6] = bit.band(bit.rshift(crc, 8), 0xFF)
    buf[7] = bit.band(crc, 0xFF)

    for i = 1, L do
        buf[7 + i] = string.byte(jsonPayload, i)
    end
    for i = 8 + L, 336 do
        buf[i] = 0
    end

    -- 2-cell self-calibrating dark charcoal preamble
    setCellTone(cells[1], TONE_LOW, TONE_HIGH, TONE_LOW)
    setCellTone(cells[2], TONE_HIGH, TONE_LOW, TONE_HIGH)

    -- 640 data cells in subtle dark charcoal (3 bits per cell, MSB-first)
    for k = 0, STRIP_DATA_CELLS - 1 do
        local tones = {}
        for ch = 0, 2 do
            local bitIdx = k * 3 + ch
            local byteVal = buf[math.floor(bitIdx / 8) + 1] or 0
            local shift = 7 - (bitIdx % 8)
            local bitVal = bit.band(bit.rshift(byteVal, shift), 1)
            tones[ch + 1] = (bitVal == 1) and TONE_HIGH or TONE_LOW
        end
        setCellTone(cells[k + 3], tones[1], tones[2], tones[3])
    end
end

local FOREVER_ZONE_MAP = {
    ["alterac mountains"] = 1416, ["alterac valley"] = 1459, ["arathi basin"] = 1461, ["arathi highlands"] = 1417,
    ["ashenvale"] = 1440, ["azshara"] = 1447, ["badlands"] = 1418, ["blasted lands"] = 1419, ["burning steppes"] = 1428,
    ["darkshore"] = 1439, ["darkspear islands"] = 2524, ["darnassus"] = 1457, ["deadwind pass"] = 1430, ["desolace"] = 1443,
    ["dun morogh"] = 1426, ["durotar"] = 1411, ["duskwood"] = 1431, ["dustwallow marsh"] = 1445, ["eastern plaguelands"] = 1423,
    ["elwynn forest"] = 1429, ["felwood"] = 1448, ["feralas"] = 1444, ["hillsbrad foothills"] = 1424, ["ironforge"] = 1455,
    ["loch modan"] = 1432, ["moonglade"] = 1450, ["mount hyjal"] = 2482, ["mulgore"] = 1412, ["orgrimmar"] = 1454,
    ["redridge mountains"] = 1433, ["riverglades"] = 2548, ["searing gorge"] = 1427, ["shen'dralas"] = 2652, ["silithus"] = 1451,
    ["silverpine forest"] = 1421, ["stonetalon mountains"] = 1442, ["stormwind city"] = 1453, ["stranglethorn vale"] = 1434,
    ["swamp of sorrows"] = 1435, ["tanaris"] = 1446, ["teldrassil"] = 1438, ["the barrens"] = 1413, ["the hinterlands"] = 1425,
    ["thousand needles"] = 1441, ["thunder bluff"] = 1456, ["tirisfal glades"] = 1420, ["un'goro crater"] = 1449,
    ["undercity"] = 1458, ["warsong gulch"] = 1460, ["western plaguelands"] = 1422, ["westfall"] = 1436, ["wetlands"] = 1437,
    ["winterspring"] = 1452, ["zephras isle"] = 2521,
}

local function buildZoneMap()
    for k, v in pairs(FOREVER_ZONE_MAP) do
        AlfredBridge.zoneToMap[k] = v
    end
    if C_Map and C_Map.GetMapChildrenInfo then
        for _, rootMap in ipairs({ 946, 947, 1414, 1415 }) do
            local ok, list = pcall(C_Map.GetMapChildrenInfo, rootMap, nil, true)
            if ok and type(list) == "table" then
                for _, info in ipairs(list) do
                    if info.name and info.mapID then
                        AlfredBridge.zoneToMap[string.lower(info.name)] = info.mapID
                    end
                end
            end
        end
    end
end

local function registerDefaultBindings()
    local binds = {
        { cmd = "ALFREDBRIDGE_LOAD_PIN", key = "CTRL-ALT-F10" },
        { cmd = "ALFREDBRIDGE_DUCK_ON",  key = "CTRL-ALT-F11" },
        { cmd = "ALFREDBRIDGE_DUCK_OFF", key = "CTRL-ALT-F12" },
    }
    local changed = false
    for _, b in ipairs(binds) do
        local curKey = GetBindingKey(b.cmd)
        local curAction = GetBindingAction(b.key)
        if (not curKey or curKey == "") and (not curAction or curAction == "") then
            SetBinding(b.key, b.cmd)
            changed = true
        end
    end
    if changed and SaveBindings and GetCurrentBindingSet then
        pcall(SaveBindings, GetCurrentBindingSet())
    end
end

local function getQuestsList()
    local quests = {}
    local ok = pcall(function()
        if C_QuestLog and C_QuestLog.GetNumQuestWatches then
            local numWatches = cleanNum(C_QuestLog.GetNumQuestWatches(), 0)
            for i = 1, math.min(numWatches, 3) do
                local qID = C_QuestLog.GetQuestIDForQuestWatchIndex(i)
                if qID and not isSecret(qID) then
                    local title = cleanStr(C_QuestLog.GetTitleForQuestID(qID), "")
                    if #title > 0 then
                        table.insert(quests, title)
                    end
                end
            end
        elseif GetNumQuestWatches then
            local numWatches = cleanNum(GetNumQuestWatches(), 0)
            for i = 1, math.min(numWatches, 3) do
                local qIndex = GetQuestIndexForWatch(i)
                if qIndex and not isSecret(qIndex) then
                    local title = cleanStr(GetQuestLogTitle(qIndex), "")
                    if #title > 0 then
                        table.insert(quests, title)
                    end
                end
            end
        end

        if #quests == 0 and C_QuestLog and C_QuestLog.GetNumQuestLogEntries then
            local numEntries = cleanNum(C_QuestLog.GetNumQuestLogEntries(), 0)
            for i = 1, numEntries do
                local info = C_QuestLog.GetInfo(i)
                if info and not isSecret(info) and not info.isHeader and not info.isHidden then
                    local title = cleanStr(info.title, "")
                    if #title > 0 then
                        table.insert(quests, title)
                        if #quests >= 3 then break end
                    end
                end
            end
        elseif #quests == 0 and GetNumQuestLogEntries then
            local numEntries = cleanNum(GetNumQuestLogEntries(), 0)
            for i = 1, numEntries do
                local title, _, _, isHeader = GetQuestLogTitle(i)
                local cleanTitle = cleanStr(title, "")
                if #cleanTitle > 0 and not isHeader then
                    table.insert(quests, cleanTitle)
                    if #quests >= 3 then break end
                end
            end
        end
    end)
    if not ok then return {} end
    return quests
end

local function getPartyList()
    local party = {}
    for i = 1, 4 do
        local unit = "party" .. i
        local exists = UnitExists(unit)
        if not isSecret(exists) and exists then
            local pName = cleanStr(UnitName(unit), "Party" .. i)
            if pName == "" then pName = "Party" .. i end
            local pClass = cleanStr(select(2, UnitClass(unit)), "")
            local pLvl = cleanNum(UnitLevel(unit), 0)
            local pZone = ""
            if C_Map and C_Map.GetBestMapForUnit then
                local ok, pMap = pcall(C_Map.GetBestMapForUnit, unit)
                if ok and pMap and not isSecret(pMap) and C_Map.GetMapInfo then
                    local okInfo, info = pcall(C_Map.GetMapInfo, pMap)
                    if okInfo and info and not isSecret(info) then
                        pZone = cleanStr(info.name, "")
                    end
                end
            end
            table.insert(party, { n = pName, c = pClass, l = pLvl, z = pZone })
        end
    end
    return party
end

function AlfredBridge:CollectTelemetry()
    local playerName = cleanStr(UnitName("player"), "")
    local realmName = cleanStr(GetRealmName(), "")
    local classFile = cleanStr(select(2, UnitClass("player")), "")
    local level = cleanNum(UnitLevel("player"), 1)
    local mapID = 0
    if C_Map and C_Map.GetBestMapForUnit then
        local okMap, m = pcall(C_Map.GetBestMapForUnit, "player")
        if okMap then mapID = cleanNum(m, 0) end
    end
    local zone = cleanStr(GetRealZoneText(), "")
    if zone == "" then zone = cleanStr(GetZoneText(), "") end
    local subzone = cleanStr(GetSubZoneText(), "")

    local x, y = 0.0, 0.0
    if mapID > 0 and C_Map and C_Map.GetPlayerMapPosition then
        local okPos, pos = pcall(C_Map.GetPlayerMapPosition, mapID, "player")
        if okPos and pos and not isSecret(pos) then
            local px = cleanNum(pos.x, nil)
            local py = cleanNum(pos.y, nil)
            if px and py then
                x = math.floor(px * 10000 + 0.5) / 100.0
                y = math.floor(py * 10000 + 0.5) / 100.0
            end
        end
    end

    local inCombat = UnitAffectingCombat("player")
    local combat = (not isSecret(inCombat) and inCombat) and 1 or 0
    local rawTarget = UnitName("target")
    local target = isSecret(rawTarget) and "" or cleanStr(rawTarget, "")
    local targetNpcId = getTargetNpcId()
    local quests = getQuestsList()
    local party = getPartyList()
    local slot = self.nextPinSlot or 1
    local ev = self.activeMoment or ""

    return {
        playerName = playerName,
        realmName = realmName,
        classFile = classFile,
        level = level,
        mapID = mapID,
        zone = zone,
        subzone = subzone,
        x = x,
        y = y,
        combat = combat,
        target = target,
        targetNpcId = targetNpcId,
        quests = quests,
        party = party,
        slot = slot,
        ev = ev
    }
end

local function serializeJson(t, includeParty, includeQuests, compactParty)
    local qItems = {}
    if includeQuests then
        for _, q in ipairs(t.quests) do
            table.insert(qItems, escapeJson(q))
        end
    end

    local pItems = {}
    if includeParty then
        for _, m in ipairs(t.party) do
            local sameZone = (not m.z) or (m.z == "") or (m.z == t.zone)
            local pStr
            if compactParty or sameZone then
                local cls = compactParty and string.sub(m.c or "", 1, 4) or (m.c or "")
                pStr = string.format('{"n":%s,"c":%s,"l":%d}', escapeJson(m.n), escapeJson(cls), m.l)
            else
                pStr = string.format('{"n":%s,"c":%s,"l":%d,"z":%s}', escapeJson(m.n), escapeJson(m.c), m.l, escapeJson(m.z))
            end
            table.insert(pItems, pStr)
        end
    end

    return string.format(
        '{"p":%s,"c":%s,"l":%d,"m":%d,"z":%s,"x":%.1f,"y":%.1f,"cb":%d,"t":%s,"q":[%s],"pty":[%s],"s":%d,"ev":%s}',
        escapeJson(t.playerName),
        escapeJson(t.classFile),
        t.level,
        t.mapID,
        escapeJson(t.zone),
        t.x,
        t.y,
        t.combat,
        escapeJson(t.target),
        table.concat(qItems, ","),
        table.concat(pItems, ","),
        t.slot,
        escapeJson(t.ev)
    )
end

function AlfredBridge:UpdatePayload(force)
    local t = self:CollectTelemetry()

    local dx = math.abs(t.x - (self.lastX or -1))
    local dy = math.abs(t.y - (self.lastY or -1))
    local moved = (dx >= 2.5 or dy >= 2.5)

    local ptyNames = {}
    for _, m in ipairs(t.party) do
        table.insert(ptyNames, (m.n or "") .. ":" .. tostring(m.l or 0) .. ":" .. (m.z or ""))
    end

    -- Build signature to check for state change
    local sig = string.format("%s|%s|%d|%d|%s|%s|%s|%s|%d|%s",
        t.zone, t.subzone, t.level, t.combat, t.target, tostring(t.targetNpcId), table.concat(ptyNames, ","), t.ev, t.slot, table.concat(t.quests, ";"))

    local changed = (moved or sig ~= self.lastStateHash or force)

    if not changed then
        return
    end

    self.lastX = t.x
    self.lastY = t.y
    self.lastStateHash = sig

    -- Fit compactly into v3 255-byte budget, prioritizing player + party + zone
    local jsonStr = serializeJson(t, true, true, false)
    if #jsonStr > STRIP_MAX_PAYLOAD then
        jsonStr = serializeJson(t, true, false, false)
    end
    if #jsonStr > STRIP_MAX_PAYLOAD then
        jsonStr = serializeJson(t, true, false, true)
    end
    if #jsonStr > STRIP_MAX_PAYLOAD then
        t.target = ""
        jsonStr = serializeJson(t, true, false, true)
    end
    if #jsonStr > STRIP_MAX_PAYLOAD then
        jsonStr = serializeJson(t, false, false, false)
    end

    self.lastJson = jsonStr
    renderStrip(jsonStr)

    -- Show 2px stealth hairline for 0.65s then completely Hide()
    if (self.screenshotSuppressTimer or 0) <= 0 then
        stripFrame:Show()
        self.hideTimer = 0.65
    end
end

-- Keybind Handlers
function AlfredBridge_OnDuck()
    if _G.Spoken and Spoken.IsPlaying and Spoken:IsPlaying() and (not Spoken.IsPaused or not Spoken:IsPaused()) then
        AlfredBridge.pausedSpoken = true
        Spoken:Pause()
    end
end

function AlfredBridge_OnUnduck()
    if AlfredBridge.pausedSpoken and _G.Spoken and Spoken.Resume then
        if not Spoken.IsPaused or Spoken:IsPaused() then
            Spoken:Resume()
        end
        AlfredBridge.pausedSpoken = false
    end
end

function AlfredBridge_OnLoadPin()
    local slot = AlfredBridge.nextPinSlot or 1
    local addonName = "AlfredBridge_Pin" .. slot
    local loadFn = (C_AddOns and C_AddOns.LoadAddOn) or LoadAddOn
    if loadFn then
        pcall(loadFn, addonName)
    end

    local pending = _G.AlfredBridgePendingPin
    if pending and pending.seq and pending.seq > (AlfredBridge.lastPinSeq or 0) then
        AlfredBridge.lastPinSeq = pending.seq
        if AlfredBridge.nextPinSlot < 21 then
            AlfredBridge.nextPinSlot = AlfredBridge.nextPinSlot + 1
        end

        local mapID = pending.mapID
        if (not mapID or mapID == 0) and pending.zone and AlfredBridge.zoneToMap then
            mapID = AlfredBridge.zoneToMap[string.lower(pending.zone)]
        end
        if (not mapID or mapID == 0) and C_Map and C_Map.GetBestMapForUnit then
            mapID = C_Map.GetBestMapForUnit("player")
        end

        if _G.WaypointUIAPI and WaypointUIAPI.Navigation and WaypointUIAPI.Navigation.NewUserNavigation then
            WaypointUIAPI.Navigation.NewUserNavigation({
                name = pending.name or "Alfred Waypoint",
                mapID = mapID,
                x = pending.x,
                y = pending.y,
                flags = "AlfredBridge_Waypoint",
                requestRecolor = true
            })
        end
        AlfredBridge:UpdatePayload(true)
    end
end

function AlfredBridge_OnCopyState()
    print("|cff00ff00[AlfredBridge]|r State: " .. (AlfredBridge.lastJson or "(empty)"))
end

-- Event handling
local eventFrame = CreateFrame("Frame", "AlfredBridgeEventFrame")
eventFrame:RegisterEvent("PLAYER_LOGIN")
eventFrame:RegisterEvent("PLAYER_ENTERING_WORLD")
eventFrame:RegisterEvent("UI_SCALE_CHANGED")
eventFrame:RegisterEvent("DISPLAY_SIZE_CHANGED")
eventFrame:RegisterEvent("SCREENSHOT_STARTED")
eventFrame:RegisterEvent("ZONE_CHANGED_NEW_AREA")
eventFrame:RegisterEvent("ZONE_CHANGED")
eventFrame:RegisterEvent("ZONE_CHANGED_INDOORS")
eventFrame:RegisterEvent("PLAYER_TARGET_CHANGED")
eventFrame:RegisterEvent("PLAYER_REGEN_DISABLED")
eventFrame:RegisterEvent("PLAYER_REGEN_ENABLED")
eventFrame:RegisterEvent("GROUP_ROSTER_UPDATE")
eventFrame:RegisterEvent("QUEST_LOG_UPDATE")
eventFrame:RegisterEvent("PLAYER_LEVEL_UP")
eventFrame:RegisterEvent("BOSS_KILL")
eventFrame:RegisterEvent("PLAYER_DEAD")

local function safeUpdatePayload(force)
    pcall(AlfredBridge.UpdatePayload, AlfredBridge, force)
end

eventFrame:SetScript("OnEvent", function(self, event, ...)
    if event == "PLAYER_LOGIN" or event == "PLAYER_ENTERING_WORLD" then
        pcall(buildZoneMap)
        pcall(registerDefaultBindings)
        pcall(updateStripLayout)
        safeUpdatePayload(true)
    elseif event == "UI_SCALE_CHANGED" or event == "DISPLAY_SIZE_CHANGED" then
        pcall(updateStripLayout)
        safeUpdatePayload(true)
    elseif event == "SCREENSHOT_STARTED" then
        stripFrame:Hide()
        AlfredBridge.screenshotSuppressTimer = 2.0
    elseif event == "PLAYER_LEVEL_UP" then
        local lvl = cleanNum((...), 0)
        if AlfredBridgeDB.moments and lvl > 0 then
            AlfredBridge.activeMoment = "lvl:" .. tostring(lvl)
            AlfredBridge.momentTimer = 4.0
        end
        safeUpdatePayload(true)
    elseif event == "BOSS_KILL" then
        local encounterID, encounterName = ...
        if AlfredBridgeDB.moments then
            local bName = cleanStr(encounterName, "")
            if bName == "" then bName = tostring(cleanNum(encounterID, 0)) end
            AlfredBridge.activeMoment = "boss:" .. bName
            AlfredBridge.momentTimer = 4.0
        end
        safeUpdatePayload(true)
    elseif event == "PLAYER_DEAD" then
        if AlfredBridgeDB.moments then
            local now = GetTime()
            table.insert(AlfredBridge.deadTimestamps, now)
            -- Keep deaths within last 900 seconds
            local cutoff = now - 900
            local filtered = {}
            for _, t in ipairs(AlfredBridge.deadTimestamps) do
                if t >= cutoff then
                    table.insert(filtered, t)
                end
            end
            AlfredBridge.deadTimestamps = filtered
            if #filtered >= 3 then
                AlfredBridge.activeMoment = "wipe:3"
                AlfredBridge.momentTimer = 4.0
            end
        end
        safeUpdatePayload(true)
    else
        safeUpdatePayload(false)
    end
end)

-- Main loop ticker (bound to eventFrame so stripFrame can stay completely Hidden when idle)
local elapsedSinceCheck = 0
eventFrame:SetScript("OnUpdate", function(self, elapsed)
    if AlfredBridge.screenshotSuppressTimer and AlfredBridge.screenshotSuppressTimer > 0 then
        AlfredBridge.screenshotSuppressTimer = AlfredBridge.screenshotSuppressTimer - elapsed
        stripFrame:Hide()
        return
    end

    if AlfredBridge.momentTimer and AlfredBridge.momentTimer > 0 then
        AlfredBridge.momentTimer = AlfredBridge.momentTimer - elapsed
        if AlfredBridge.momentTimer <= 0 then
            AlfredBridge.activeMoment = nil
        end
    end

    if AlfredBridge.hideTimer and AlfredBridge.hideTimer > 0 then
        AlfredBridge.hideTimer = AlfredBridge.hideTimer - elapsed
        if AlfredBridge.hideTimer <= 0 then
            stripFrame:Hide()
        end
    end

    elapsedSinceCheck = elapsedSinceCheck + elapsed
    AlfredBridge.heartbeatTimer = (AlfredBridge.heartbeatTimer or 0) + elapsed

    -- Periodic check every 0.5s for movement / state change
    if elapsedSinceCheck >= 0.5 then
        elapsedSinceCheck = 0
        local forceHeartbeat = false
        if AlfredBridge.heartbeatTimer >= 15.0 then
            AlfredBridge.heartbeatTimer = 0
            forceHeartbeat = true
        end
        safeUpdatePayload(forceHeartbeat)
    end
end)

-- Slash Commands
SLASH_ALFREDBRIDGE1 = "/alfred"
SLASH_ALFREDBRIDGE2 = "/ab"
SlashCmdList["ALFREDBRIDGE"] = function(msg)
    local cmd = string.lower(strtrim(msg or ""))
    if cmd == "status" then
        local spokenStatus = (_G.Spoken and "detected") or "not found"
        local wpStatus = (_G.WaypointUIAPI and "detected") or "not found"
        local momentsStatus = AlfredBridgeDB.moments and "ON" or "OFF"
        print(string.format("|cff00ff00[AlfredBridge]|r Spoken: %s, WaypointUI: %s, Moments: %s, Next Pin Slot: %d",
            spokenStatus, wpStatus, momentsStatus, AlfredBridge.nextPinSlot or 1))
    elseif cmd == "moments" then
        AlfredBridgeDB.moments = not AlfredBridgeDB.moments
        print(string.format("|cff00ff00[AlfredBridge]|r Moments telemetry is now %s.",
            AlfredBridgeDB.moments and "|cff00ff00ENABLED|r" or "|cffff0000DISABLED|r"))
    elseif cmd == "testpin" then
        if _G.WaypointUIAPI and WaypointUIAPI.Navigation and WaypointUIAPI.Navigation.NewUserNavigation then
            local mapID = (C_Map and C_Map.GetBestMapForUnit and C_Map.GetBestMapForUnit("player")) or 0
            local x, y = 50.0, 50.0
            if mapID and mapID > 0 and C_Map and C_Map.GetPlayerMapPosition then
                local pos = C_Map.GetPlayerMapPosition(mapID, "player")
                if pos and pos.x and pos.y then
                    x = math.floor(pos.x * 10000 + 0.5) / 100.0 + 1.0
                    y = math.floor(pos.y * 10000 + 0.5) / 100.0 + 1.0
                end
            end
            WaypointUIAPI.Navigation.NewUserNavigation({
                name = "Alfred Test Waypoint",
                mapID = mapID,
                x = x,
                y = y,
                flags = "AlfredBridge_Waypoint",
                requestRecolor = true
            })
            print(string.format("|cff00ff00[AlfredBridge]|r Dropped test waypoint at (%.1f, %.1f)", x, y))
        else
            print("|cffff0000[AlfredBridge]|r WaypointUI not detected.")
        end
    elseif cmd == "resetpins" then
        print("|cff00ff00[AlfredBridge]|r Type /reload to reload the UI and reset pin addon slots.")
    else
        print("|cff00ff00[AlfredBridge]|r Commands: /ab status | /ab moments | /ab testpin | /ab resetpins")
    end
end
