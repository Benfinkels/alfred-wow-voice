#!/usr/bin/env python3
"""
Builds wow_forever.sqlite from QuestieDB_Forever.toc + Forever support tables.
Zero external dependencies (pure-Python CBOR decoder + sqlite3 FTS5).
"""
import base64
import json
import os
import re
import sqlite3
import struct
import time
from collections import defaultdict

ADDON_ROOT = r"C:\Program Files (x86)\World of Warcraft\_forever_\Interface\AddOns"
ADDON_DIR = os.path.join(ADDON_ROOT, "QuestieDB")
TOC_PATH = os.path.join(ADDON_DIR, "QuestieDB_Forever.toc")
ZONES_LUA = os.path.join(ADDON_DIR, r"src\corrections\enum\zones.lua")
QUESTS_LUA = os.path.join(ADDON_DIR, r"src\corrections\enum\quests.lua")
SUBZONES_LUA = os.path.join(ADDON_DIR, r"support\Forever\Zones\subZoneToParentZone.lua")
DROPS_LUA = os.path.join(ADDON_DIR, r"support\Forever\DropTables\classicItemDrops.lua")
DROP_CORR_LUA = os.path.join(ADDON_DIR, r"support\Forever\DropTables\itemDropCorrections.lua")
XP_LUA = os.path.join(ADDON_DIR, r"support\Forever\QuestXP\xpDB-classic.lua")
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CLASSIC_ITEMS_JSON = os.environ.get("CLASSIC_ITEMS_JSON", os.path.join(BASE_DIR, "addon_data", "wow_classic_items", "data.json"))
ATLASLOOT_DROPRATE_LUA = os.environ.get("ATLASLOOT_DROPRATE_LUA", os.path.join(BASE_DIR, "addon_data", "AtlasLootClassic", "AtlasLootClassic_DungeonsAndRaids", "droprate.lua"))
OUT_DB = os.environ.get("WOW_DB_PATH", os.path.join(BASE_DIR, "wow_forever.sqlite"))

WEAPON_MASTER_SKILLS = {
    11865: ("Buliwyf Stonehand", "Ironforge (61.2, 89.5)", "One-Handed Axes, Two-Handed Axes, One-Handed Maces, Two-Handed Maces, Guns, Fist Weapons"),
    13084: ("Bixi Wobblebonk", "Ironforge (62.2, 89.6)", "Daggers, Thrown, Crossbows"),
    11867: ("Woo Ping", "Stormwind City (63.9, 69.1) [Classic: 57.1, 57.7]", "Polearms (Lvl 20), One-Handed Swords, Two-Handed Swords, Staves, Daggers, Crossbows"),
    11866: ("Ilyenia Moonfire", "Darnassus (57.7, 46.0)", "Staves, Bows, Daggers, Thrown, Fist Weapons"),
    2704: ("Hanashi", "Orgrimmar (81.5, 19.6)", "One-Handed Axes, Two-Handed Axes, Staves, Bows, Thrown"),
    11868: ("Sayoc", "Orgrimmar (81.7, 19.6)", "One-Handed Axes, Two-Handed Axes, Staves, Bows, Daggers, Thrown, Fist Weapons"),
    11869: ("Ansekhwa", "Thunder Bluff (40.0, 63.1)", "One-Handed Maces, Two-Handed Maces, Staves, Guns"),
    11870: ("Archibald", "Undercity (57.3, 32.8)", "Polearms (Lvl 20), One-Handed Swords, Two-Handed Swords, Daggers, Crossbows"),
}

PROFESSION_NAMES = {
    164: "Blacksmithing", 165: "Leatherworking", 171: "Alchemy", 197: "Tailoring",
    202: "Engineering", 333: "Enchanting", 755: "Jewelcrafting", 773: "Inscription",
    182: "Herbalism", 186: "Mining", 393: "Skinning", 129: "First Aid",
    185: "Cooking", 356: "Fishing", 762: "Riding",
}

NPC_RANK_NAMES = {1: "ELITE", 2: "RARE ELITE", 3: "BOSS", 4: "RARE"}

NPC_FLAG_TAGS = [
    (16384, "Repair Vendor"),
    (8, "Flight Master"),
    (128, "Innkeeper"),
    (8192, "Stable Master"),
    (256, "Banker"),
    (4096, "Auctioneer"),
    (16, "Trainer"),
    (4, "Vendor"),
]

CURATED_MECHANICS_LORE = [
    (
        "Fishing Skill Requirements by Zone",
        "fishing skill level fish getting away loch modan darkshore westfall redridge elwynn teldrassil",
        "In WoW Classic, fish escape when your Fishing skill is too low for the zone (lures add +25 to +100 skill to stop fish from getting away; fish are never character-level locked). Zone minimum/0%-getaway Fishing skills: Starter zones (Elwynn, Dun Morogh, Teldrassil, Durotar, Mulgore, Tirisfal): 1 min / 75 no-getaway. Level 10-20 zones (Loch Modan, Westfall, Darkshore, Barrens, Silverpine): 20 min / 100 no-getaway (aim for 50-75 Fishing + a +25/+50 lure). Level 15-25 zones (Redridge Mountains, Duskwood, Wetlands, Ashenvale, Stonetalon, Hillsbrad): 55 min / 150 no-getaway. Level 25-35 zones (Stranglethorn Vale, Desolace, Arathi, Thousand Needles): 130 min / 225 no-getaway. Train Journeyman Fishing (up to 150) at skill 50 (level 10), and buy Expert Fishing - The Bass and You from Old Man Heming in Booty Bay at skill 125 (level 20).",
    ),
    (
        "Alliance Travel Routes, Boats, Tram and Flight Paths",
        "boat zeppelin tram travel route teldrassil darkshore auberdine menethil stormwind ironforge westfall",
        "WoW Classic Alliance Travel Connections: (1) Teldrassil is an island tree; walk through Darnassus (36, 50) into the pink portal to Rut'theran Village (56.3, 92.4), then take the free Hippogryph (The Bounty of Teldrassil #6341 -> Flight to Auberdine #6342) or boat to Auberdine in Darkshore (36.8, 44.3). (2) From Auberdine (Darkshore) south dock (32.4, 43.8), take the boat to Menethil Harbor in Wetlands (4.6, 57.1). (3) From Menethil Harbor, run east through Wetlands and south through Loch Modan to Dun Morogh / Ironforge. (4) Take the Deeprun Tram in Ironforge (Tinker Town 77, 51) directly to Stormwind City (Dwarven District 63, 8), which connects to Elwynn Forest, Westfall (Sentinel Hill flight path at 56.5, 52.6), and Redridge Mountains (Lakeshire flight path at 30.6, 59.4). Note: Stormwind has NO harbor or boat in Classic 1.12.",
    ),
    (
        "Teldrassil to Darkshore Leveling and Quest Skip Guide",
        "skip teldrassil dolanaar darkshore level 6 level 10 leveling route",
        "In Teldrassil (levels 5-10 around Dolanaar), to reach Darkshore quickly at level 10: DO the fast East-Teldrassil loop: Dolanaar Delivery (#2159 at 55.6, 59.8), Zenn's Bidding (#488 at 60.4, 56.2), Denalan's Earth (#997 at 60.9, 68.5) + Timberling Seeds/Sprouts (#918/#919), Starbreeze Village (A Troubling Breeze #475 + The Emerald Dreamcatcher #2438 at 68.0, 59.6), and the Crown of the Earth moonwell chain (#928/#929/#933). SKIP the high-wipe caves and slow west-side grinds: skip Ban'ethil Barrow Den (The Relics of Wakening #483 and The Sleeping Druid #2541 at 44.9, 61.6), skip Twisted Hatred (#932 Lord Melenas cave at 54.6, 50.8), and once you hit level 10, skip the far-west Enchanted Glade/Oracle Tree/Ursal the Mauler quests and go straight through Darnassus to Rut'theran Village (56.3, 92.4) for The Bounty of Teldrassil (#6341) to fly to Auberdine in Darkshore.",
    ),
    (
        "The Defias Brotherhood and Deadmines Attunement Chain",
        "edwin vancleef staff of westfall chausses tunic of westfall deadmines quest chain gryan stoutmantle",
        "To get the Quest 'The Defias Brotherhood' (#166) which rewards the Staff of Westfall (#2042), Chausses of Westfall (#1569), or Tunic of Westfall (#2041) for killing Edwin VanCleef in The Deadmines: (1) Complete all 3 steps of 'The People's Militia' (#12, #13, #14) from Gryan Stoutmantle at Sentinel Hill in Westfall (56.3, 47.5). (2) Pick up 'The Defias Brotherhood' step 1 (#65) from Gryan Stoutmantle (56.3, 47.5) -> Wiley the Black in Lakeshire, Redridge Mountains (26.5, 45.3) -> (#132) Gryan Stoutmantle -> (#135) Master Mathias Shaw in Stormwind SI:7 (75.8, 59.8) -> (#141) Gryan Stoutmantle -> (#142) shoot the Defias Messenger on the Westfall road -> (#155) escort The Defias Traitor to the Deadmines entrance in Moonbrook (42.5, 71.7) -> (#166) kill Edwin VanCleef inside The Deadmines.",
    ),
    (
        "Weapon Skills and Weapon Masters by City and Class",
        "weapon master trainer train swords axes maces polearms staves bows guns daggers crossbows fist weapons thrown woo ping buliwyf bixi ilyenia hanashi sayoc ansekhwa archibald",
        "Weapon Master Trainers by Capital City (cost 10 silver, Polearms require Level 20): Alliance: (1) Ironforge: Buliwyf Stonehand (61.2, 89.5) teaches 1H Axes, 2H Axes, 1H Maces, 2H Maces, Guns, Fist Weapons; Bixi Wobblebonk (62.2, 89.6) teaches Daggers, Thrown, Crossbows. (2) Stormwind City: Woo Ping (63.9, 69.1 in Forever / 57.1, 57.7 Classic) teaches Polearms, 1H Swords, 2H Swords, Staves, Daggers, Crossbows. (3) Darnassus: Ilyenia Moonfire (57.7, 46.0) teaches Staves, Bows, Daggers, Thrown, Fist Weapons. Horde: (4) Orgrimmar: Hanashi & Sayoc (81.7, 19.6) teach 1H Axes, 2H Axes, Staves, Bows, Daggers, Thrown, Fist Weapons. (5) Thunder Bluff: Ansekhwa (40.0, 63.1) teaches 1H Maces, 2H Maces, Staves, Guns. (6) Undercity: Archibald (57.3, 32.8) teaches Polearms, 1H Swords, 2H Swords, Daggers, Crossbows. Class Eligibility (Forever): Warriors (all except Wands); Paladins (1H/2H Axes, 1H/2H Maces, 1H/2H Swords, Polearms); Hunters (Axes, Swords, Polearms, Staves, Daggers, Fist Weapons, Bows, Guns, Crossbows, Thrown); Rogues (Daggers, 1H Swords, 1H Maces, Fist Weapons, 1H Axes in Forever, Bows, Guns, Crossbows, Thrown); Priests (1H Maces, Daggers, Staves, Wands); Shamans (1H/2H Axes, 1H/2H Maces, Staves, Daggers, Fist Weapons); Druids (1H/2H Maces, Staves, Daggers, Fist Weapons); Mages & Warlocks (1H Swords, Daggers, Staves, Wands).",
    ),
]


def load_quest_xp():
    xp_map = {}
    if not os.path.exists(XP_LUA):
        return xp_map
    with open(XP_LUA, "r", encoding="utf-8") as f:
        for line in f:
            m = re.match(r"^\s*\[(\d+)\]\s*=\s*\{\s*-?\d+\s*,\s*(\d+)\s*\}", line)
            if m:
                xp_map[int(m.group(1))] = int(m.group(2))
    return xp_map


def load_atlasloot_droprates(drop_rates):
    """Merge AtlasLootClassic boss drop rates into drop_rates[itemId][npcId] and return boss_loot[npcId]."""
    boss_loot = defaultdict(list)
    if not os.path.exists(ATLASLOOT_DROPRATE_LUA):
        return boss_loot
    cur_npc = None
    with open(ATLASLOOT_DROPRATE_LUA, "r", encoding="utf-8") as f:
        for line in f:
            m_npc = re.match(r"^\s*\[(\d+)\]\s*=\s*\{", line)
            if m_npc:
                cur_npc = int(m_npc.group(1))
                continue
            if cur_npc is not None:
                if line.strip().startswith("}"):
                    cur_npc = None
                else:
                    m_it = re.match(r"^\s*\[(\d+)\]\s*=\s*([0-9.]+)", line)
                    if m_it:
                        iid, pct = int(m_it.group(1)), float(m_it.group(2))
                        drop_rates[iid][cur_npc] = pct
                        boss_loot[cur_npc].append((iid, pct))
    return boss_loot


def load_classic_item_tooltips():
    """Load item stats, slot, quality, and tooltip lines from wow-classic-items data.json."""
    info = {}
    if not os.path.exists(CLASSIC_ITEMS_JSON):
        return info
    with open(CLASSIC_ITEMS_JSON, "r", encoding="utf-8") as f:
        data = json.load(f)
    skip_prefixes = (
        "Phase ", "Item Level ", "Binds ", "Unique", "Durability ", "Requires Level ",
        "Sell Price", "Dropped by:", "Drop Chance:", "Classes:", "Races:",
    )
    for it in data:
        iid = it.get("itemId")
        if not iid:
            continue
        qual = it.get("quality") or ""
        slot = it.get("slot") or ""
        subc = it.get("subclass") or it.get("class") or ""
        name = it.get("name") or ""
        stat_lines = []
        for t in it.get("tooltip") or []:
            lbl = (t.get("label") or "").strip()
            if not lbl or lbl == name or lbl in (slot, subc):
                continue
            if any(lbl.startswith(p) for p in skip_prefixes):
                continue
            stat_lines.append(lbl)
        head = " ".join(x for x in [qual if qual not in ("Common", "Poor") else "", slot, subc] if x).strip()
        stats_s = ", ".join(stat_lines[:6])
        if head or stats_s:
            info[iid] = f"[{head}: {stats_s}]" if (head and stats_s) else f"[{head or stats_s}]"
    return info


def load_whatstraining_recipes(npcs, quests, npc_loc_str):
    """Parse WhatsTraining Camelot recipe files to map crafted items and recipe scrolls to profession/skill/source."""
    recipe_dir = os.path.join(ADDON_ROOT, r"WhatsTraining\Crafting\Recipes\Camelot")
    crafted_by = {}
    teaches_recipe = {}
    if not os.path.isdir(recipe_dir):
        return crafted_by, teaches_recipe
    for fn in sorted(os.listdir(recipe_dir)):
        if not fn.endswith(".lua"):
            continue
        prof_name = fn[:-4]
        if prof_name == "FirstAid":
            prof_name = "First Aid"
        fpath = os.path.join(recipe_dir, fn)
        with open(fpath, "r", encoding="utf-8") as f:
            for line in f:
                m_skill = re.search(r"skill\s*=\s*(\d+)", line)
                if not m_skill:
                    continue
                skill = int(m_skill.group(1))
                m_creates = re.search(r"creates\s*=\s*(\d+)", line)
                m_teach = re.search(r"teach\s*=\s*(\d+)", line)
                m_src = re.search(r"source\s*=\s*\{\s*([^}]+)\}", line)
                srcs = [s.strip().strip('"') for s in m_src.group(1).split(",")] if m_src else []
                v_nids = [int(x) for x in re.findall(r"npc\s*=\s*(\d+)", line)]
                q_ids = [int(x) for x in re.findall(r"\{\s*id\s*=\s*(\d+)", line)]
                src_parts = []
                if "trainer" in srcs:
                    src_parts.append("Trainer")
                if v_nids:
                    v_names = [npc_loc_str.get(nid, f"NPC #{nid}") for nid in v_nids[:3]]
                    src_parts.append("Recipe Vendor: " + "; ".join(v_names))
                if q_ids:
                    q_names = [f"{quests[qid].get(1, f'Quest #{qid}')} (#{qid})" for qid in q_ids[:2] if qid in quests]
                    if q_names:
                        src_parts.append("Quest: " + ", ".join(q_names))
                if "drop" in srcs and not src_parts:
                    src_parts.append("World/Mob Recipe Drop")
                src_str = " | ".join(src_parts) if src_parts else "Trainer/Auto"
                creates_id = int(m_creates.group(1)) if m_creates else None
                teach_id = int(m_teach.group(1)) if m_teach else None
                if creates_id:
                    crafted_by[creates_id] = (prof_name, skill, teach_id, src_str)
                if teach_id:
                    teaches_recipe[teach_id] = (prof_name, skill, creates_id, src_str)
    return crafted_by, teaches_recipe


def load_addon_lore_and_fish():
    """Load Spoken_Places, Spoken_Bosses, and GatherLite (ores, herbs, lockboxes, fishing catches) into lore + fish_zones."""
    extra_lore = []
    fish_zones = defaultdict(list)
    uimap_to_zone = {}

    # 1. Spoken_Places Zones.lua
    zones_lua = os.path.join(ADDON_ROOT, r"Spoken_Places\Data\enUS\Zones.lua")
    if os.path.exists(zones_lua):
        with open(zones_lua, "r", encoding="utf-8") as f:
            ztxt = f.read()
        for m in re.finditer(
            r"\[(\d+)\]\s*=\s*\{\s*name\s*=\s*\"([^\"]+)\",\s*short\s*=\s*\"([^\"]+)\",\s*full\s*=\s*\"((?:\\.|[^\"\\])*)\"",
            ztxt,
        ):
            umid, zname, zshort, zfull = int(m.group(1)), m.group(2), m.group(3), m.group(4).replace("\\n", " ").strip()
            uimap_to_zone[umid] = zname
            extra_lore.append((f"Zone Lore: {zname}", f"{zname.lower()} zone lore history overview", zfull[:700]))

    # 2. Spoken_Places Subzones.lua (group subzones by parent zone so FTS stays compact & high-signal)
    subzones_lua = os.path.join(ADDON_ROOT, r"Spoken_Places\Data\enUS\Subzones.lua")
    if os.path.exists(subzones_lua):
        with open(subzones_lua, "r", encoding="utf-8") as f:
            stxt = f.read()
        for m_blk in re.finditer(r"\[(\d+)\]\s*=\s*\{(.*?)\n\t\},", stxt, re.DOTALL):
            umid = int(m_blk.group(1))
            zname = uimap_to_zone.get(umid, f"Zone #{umid}")
            body = m_blk.group(2)
            sub_entries = []
            sub_aliases = [zname.lower()]
            for ms in re.finditer(r'\["([^"]+)"\]\s*=\s*\{\s*name\s*=\s*"([^"]+)",\s*short\s*=\s*"((?:\\.|[^"\\])*)"', body):
                skey, sname, sshort = ms.group(1), ms.group(2), ms.group(3)
                sub_aliases.append(skey)
                sub_entries.append(f"{sname}: {sshort}")
            if sub_entries:
                # Split into chunks of 10 subzones so each lore row stays <900 chars
                for idx in range(0, len(sub_entries), 10):
                    chunk = sub_entries[idx : idx + 10]
                    part_num = (idx // 10) + 1
                    title = f"Subzones of {zname} (Part {part_num})" if len(sub_entries) > 10 else f"Subzones of {zname}"
                    extra_lore.append((title, " ".join(sub_aliases), f"{zname} Subzones — " + " | ".join(chunk)))

    # 3. Spoken_Bosses Stories.lua
    stories_lua = os.path.join(ADDON_ROOT, r"Spoken_Bosses\Data\Stories.lua")
    if os.path.exists(stories_lua):
        with open(stories_lua, "r", encoding="utf-8") as f:
            for line in f:
                m = re.search(r'\["([^"]+)"\]\s*=\s*\{\s*"((?:\\.|[^"\\])*)"', line)
                if m:
                    bkey = m.group(1).title()
                    bstory = m.group(2).replace("\\n\\n", " ").replace("\\n", " ").strip()
                    extra_lore.append((f"Boss Lore: {bkey}", f"{m.group(1)} boss dungeon raid lore story", bstory[:650]))

    # 4. GatherLite nodes.lua + forever.lua + fishing/data.lua
    nodes_lua = os.path.join(ADDON_ROOT, r"GatherLite\scripts\nodes.lua")
    forever_lua = os.path.join(ADDON_ROOT, r"GatherLite\plugins\database\data\forever.lua")
    fish_lua = os.path.join(ADDON_ROOT, r"GatherLite\plugins\fishing\data.lua")

    obj_to_node = {}
    node_meta = {}
    if os.path.exists(nodes_lua):
        with open(nodes_lua, "r", encoding="utf-8") as f:
            ntxt = re.sub(r"\n\s+", " ", f.read())
        for m in re.finditer(r'InsertObject\("(\w+)",\s*\{([^}]+)\},\s*"([^"]+)",\s*"[^"]+",\s*(\{[^}]+\}|nil)', ntxt):
            ntype, ids_s, raw_name, lvls_s = m.groups()
            pretty = raw_name.replace("_", " ").title()
            ids = [int(x) for x in re.findall(r"\d+", ids_s)]
            lvls = [int(x) for x in re.findall(r"\d+", lvls_s)] if lvls_s != "nil" else []
            node_meta[pretty] = (ntype, lvls)
            for oid in ids:
                obj_to_node[oid] = pretty

    # Parse forever.lua: [objectID][uiMapID] = { x, y, ... }
    node_zone_coords = defaultdict(lambda: defaultdict(list))
    if os.path.exists(forever_lua):
        with open(forever_lua, "r", encoding="utf-8") as f:
            ftxt = f.read()
        for m_obj in re.finditer(r"^    \[(\d+)\]\s*=\s*\{(.*?)^    \},", ftxt, re.DOTALL | re.MULTILINE):
            oid = int(m_obj.group(1))
            nname = obj_to_node.get(oid)
            if not nname:
                continue
            for m_map in re.finditer(r"\[(\d+)\]\s*=\s*\{([^}]+)\}", m_obj.group(2)):
                umid = int(m_map.group(1))
                zname = uimap_to_zone.get(umid, f"Map #{umid}")
                nums = [float(x) for x in re.findall(r"\d+\.\d+", m_map.group(2))]
                pts = [(nums[i] * 100.0, nums[i + 1] * 100.0) for i in range(0, len(nums) - 1, 2)]
                if pts:
                    node_zone_coords[nname][zname].extend(pts)

    zone_resources = defaultdict(list)
    type_priority = {"ore": 0, "herb": 1, "fishing": 2, "container": 3}
    for nname, zmap in sorted(node_zone_coords.items(), key=lambda kv: (type_priority.get(node_meta.get(kv[0], ("", []))[0], 9), kv[0])):
        ntype, lvls = node_meta.get(nname, ("resource", []))
        skill_s = f" (Skill {lvls[0]})" if lvls else ""
        top_zones = sorted(zmap.items(), key=lambda kv: len(kv[1]), reverse=True)
        z_summaries = []
        for zname, pts in top_zones[:10]:
            sample = ", ".join(f"({x:.1f}, {y:.1f})" for x, y in pts[:2])
            z_summaries.append(f"{zname} [{len(pts)} spawns, e.g. {sample}]")
            zone_resources[zname].append(f"{nname}{skill_s} ({len(pts)} spawns, e.g. {sample})")
        cat_label = {"ore": "Mining Node", "herb": "Herb", "container": "Chest/Footlocker", "fishing": "Fishing Pool"}.get(ntype, "Node")
        extra_lore.append(
            (
                f"{cat_label}: {nname}{skill_s}",
                f"{nname.lower()} {ntype} gathering farming locations coordinates",
                f"{nname}{skill_s} ({cat_label}) top farming zones & coordinates: " + "; ".join(z_summaries),
            )
        )

    # Parse fishing/data.lua
    zone_fish = defaultdict(list)
    if os.path.exists(fish_lua):
        with open(fish_lua, "r", encoding="utf-8") as f:
            fltxt = f.read()
        for m_f in re.finditer(r'\{itemID=(\d+),\s*type="fish",\s*id=\{\},\s*name="([^"]+)",\s*icon=[^,]+,\s*maps=\{(.*?)\}\}', fltxt, re.DOTALL):
            fiid, fname, maps_body = int(m_f.group(1)), m_f.group(2), m_f.group(3)
            for m_m in re.finditer(r"\[(\d+)\]=\{([^}]*)\}", maps_body):
                umid, coords_s = int(m_m.group(1)), m_m.group(2)
                zname = uimap_to_zone.get(umid, f"Map #{umid}")
                nums = [float(x) for x in re.findall(r"\d+\.\d+", coords_s)]
                if len(nums) >= 2:
                    sample = f"({nums[0]*100:.1f}, {nums[1]*100:.1f})"
                    fish_zones[fiid].append(f"{zname} {sample}")
                    zone_fish[zname].append(f"{fname} {sample}")
                else:
                    fish_zones[fiid].append(zname)
                    zone_fish[zname].append(fname)

    for zname in sorted(set(zone_resources.keys()) | set(zone_fish.keys())):
        res_s = "; ".join(zone_resources.get(zname, [])[:10])
        f_s = ", ".join(zone_fish.get(zname, [])[:10])
        parts = []
        if res_s:
            parts.append(f"Ores/Herbs/Chests: {res_s}")
        if f_s:
            parts.append(f"Fish Caught: {f_s}")
        extra_lore.append(
            (
                f"Gathering & Fishing Guide: {zname}",
                f"{zname.lower()} mining herbs herbalism ore fishing fish lockpicking chests",
                f"{zname} Gathering & Fishing Hotspots — " + " | ".join(parts),
            )
        )

    return extra_lore, fish_zones


# --- Pure-Python RFC 8949 CBOR Decoder ---
def cbor_decode(data: bytes, offset: int = 0):
    b = data[offset]
    offset += 1
    major = b >> 5
    info = b & 0x1F

    if info < 24:
        val = info
    elif info == 24:
        val = data[offset]
        offset += 1
    elif info == 25:
        val = struct.unpack(">H", data[offset : offset + 2])[0]
        offset += 2
    elif info == 26:
        val = struct.unpack(">I", data[offset : offset + 4])[0]
        offset += 4
    elif info == 27:
        val = struct.unpack(">Q", data[offset : offset + 8])[0]
        offset += 8
    elif info == 31:
        # Indefinite length
        if major == 4:
            arr = []
            while data[offset] != 0xFF:
                item, offset = cbor_decode(data, offset)
                arr.append(item)
            return arr, offset + 1
        elif major == 5:
            mp = {}
            while data[offset] != 0xFF:
                k, offset = cbor_decode(data, offset)
                v, offset = cbor_decode(data, offset)
                mp[k] = v
            return mp, offset + 1
        raise ValueError(f"Unsupported indefinite major {major}")
    else:
        raise ValueError(f"Unsupported CBOR info {info}")

    if major == 0:
        return val, offset
    elif major == 1:
        return -1 - val, offset
    elif major == 2:
        raw = data[offset : offset + val]
        try:
            return raw.decode("utf-8"), offset + val
        except UnicodeDecodeError:
            return raw.decode("latin-1", errors="replace"), offset + val
    elif major == 3:
        raw = data[offset : offset + val]
        return raw.decode("utf-8", errors="replace"), offset + val
    elif major == 4:
        arr = []
        for _ in range(val):
            item, offset = cbor_decode(data, offset)
            arr.append(item)
        return arr, offset
    elif major == 5:
        mp = {}
        for _ in range(val):
            k, offset = cbor_decode(data, offset)
            v, offset = cbor_decode(data, offset)
            mp[k] = v
        return mp, offset
    elif major == 6:
        # Semantic tag
        return cbor_decode(data, offset)
    elif major == 7:
        if info == 20:
            return False, offset
        elif info == 21:
            return True, offset
        elif info in (22, 23):
            return None, offset
        elif info == 25:
            return struct.unpack(">e", data[offset - 2 : offset])[0], offset
        elif info == 26:
            return struct.unpack(">f", data[offset - 4 : offset])[0], offset
        elif info == 27:
            return struct.unpack(">d", data[offset - 8 : offset])[0], offset
        return val, offset


def clean_symbol_name(sym: str) -> str:
    sym = re.sub(r"_FOREVER$", "", sym)
    words = sym.replace("_", " ").title().split()
    small = {"Of", "The", "In", "And", "To", "For", "On", "At"}
    out = [w if i == 0 or w not in small else w.lower() for i, w in enumerate(words)]
    return " ".join(out).replace("Un Goro", "Un'Goro").replace("Zul ", "Zul'").replace("Ahn Qiraj", "Ahn'Qiraj")


def load_zone_and_sort_names():
    zones = {}
    # 1. Parse zones.lua
    with open(ZONES_LUA, "r", encoding="utf-8") as f:
        for line in f:
            m = re.match(r"^\s*([A-Z0-9_]+)\s*=\s*(-?\d+)\s*,", line)
            if m:
                sym, zid = m.group(1), int(m.group(2))
                if zid not in zones:
                    zones[zid] = clean_symbol_name(sym)

    # 2. Parse quests.lua sortKeys (negative zoneOrSort values)
    with open(QUESTS_LUA, "r", encoding="utf-8") as f:
        in_sort = False
        for line in f:
            if "constants.sortKeys" in line:
                in_sort = True
            elif in_sort and line.startswith("}"):
                in_sort = False
            elif in_sort:
                m = re.match(r"^\s*([A-Z0-9_]+)\s*=\s*(-\d+)\s*,", line)
                if m:
                    sym, sid = m.group(1), int(m.group(2))
                    zones[sid] = clean_symbol_name(sym)

    # 3. Parse subZoneToParentZone.lua comments for exact human names & parent mapping
    sub_to_parent = {}
    with open(SUBZONES_LUA, "r", encoding="utf-8") as f:
        for line in f:
            m = re.match(r"^\s*\[(\d+)\]\s*=\s*(\d+),\s*--\s*(.+?)(?:\s*->\s*(.+))?$", line)
            if m:
                sub_id = int(m.group(1))
                parent_id = int(m.group(2))
                sub_name = m.group(3).strip()
                parent_name = m.group(4).strip() if m.group(4) else None
                sub_to_parent[sub_id] = parent_id
                if parent_name and not parent_name.endswith("UNUSED"):
                    zones[parent_id] = parent_name
                if sub_name and not sub_name.endswith("UNUSED"):
                    zones[sub_id] = sub_name

    def resolve_zone(zid: int) -> str:
        if not zid:
            return "Unknown"
        name = zones.get(zid, f"Zone #{zid}")
        parent_id = sub_to_parent.get(zid)
        if parent_id and parent_id != zid:
            parent_name = zones.get(parent_id)
            if parent_name and parent_name != name:
                return f"{parent_name} ({name})"
        return name

    return resolve_zone


def load_drop_rates():
    # Parse classicItemDrops.lua and itemDropCorrections.lua: [itemId] = { [npcId] = rate, ... }
    drops = defaultdict(dict)
    for path in (DROPS_LUA, DROP_CORR_LUA):
        if not os.path.exists(path):
            continue
        cur_item = None
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                m_item = re.match(r"^\s*\[(\d+)\]\s*=\s*\{", line)
                if m_item:
                    cur_item = int(m_item.group(1))
                    continue
                if cur_item is not None:
                    if line.strip().startswith("}"):
                        cur_item = None
                    else:
                        m_npc = re.match(r"^\s*\[(\d+)\]\s*=\s*([0-9.]+)", line)
                        if m_npc:
                            drops[cur_item][int(m_npc.group(1))] = float(m_npc.group(2))
    return drops


def decode_faction(race_mask: int) -> str:
    if not race_mask or race_mask == 0:
        return "Both"
    # Forever Alliance bits: Human(1) + Dwarf(4) + NightElf(8) + Gnome(64) + Draenei(1024) + Worgen(2097152) + SkyborneA(4294967296)
    alliance_mask = 1 | 4 | 8 | 64 | 1024 | 2097152 | 16777216 | 4294967296
    # Forever Horde bits: Orc(2) + Undead(16) + Tauren(32) + Troll(128) + Goblin(256) + BloodElf(512) + SkyborneH(8589934592)
    horde_mask = 2 | 16 | 32 | 128 | 256 | 512 | 33554432 | 8589934592
    has_a = bool(race_mask & alliance_mask)
    has_h = bool(race_mask & horde_mask)
    if has_a and has_h:
        return "Both"
    if has_a:
        races = []
        for bit, rname in [(1, "Human"), (4, "Dwarf"), (8, "Night Elf"), (64, "Gnome"), (4294967296, "Skyborne")]:
            if race_mask & bit:
                races.append(rname)
        return "Alliance" if len(races) >= 4 else f"Alliance ({', '.join(races)})"
    if has_h:
        races = []
        for bit, rname in [(2, "Orc"), (16, "Undead"), (32, "Tauren"), (128, "Troll"), (256, "Goblin"), (8589934592, "Skyborne")]:
            if race_mask & bit:
                races.append(rname)
        return "Horde" if len(races) >= 4 else f"Horde ({', '.join(races)})"
    return "Both"


def decode_classes(class_mask: int) -> str:
    if not class_mask or class_mask in (0, 1503, 1535, 2047):
        return "All"
    cmap = [
        (1, "Warrior"), (2, "Paladin"), (4, "Hunter"), (8, "Rogue"),
        (16, "Priest"), (32, "Death Knight"), (64, "Shaman"), (128, "Mage"),
        (256, "Warlock"), (512, "Monk"), (1024, "Druid"),
    ]
    out = [name for bit, name in cmap if class_mask & bit]
    return ", ".join(out) if out else "All"


def parse_toc():
    t0 = time.time()
    raw_kv = {}
    pat = re.compile(r"^##\s+(X-(?:Quest|Npc|Item|Object)-\S+):\s*(\S+)\s*$")
    with open(TOC_PATH, "r", encoding="utf-8") as f:
        for line in f:
            if line.startswith("## X-"):
                m = pat.match(line)
                if m:
                    raw_kv[m.group(1)] = m.group(2)

    print(f"Loaded {len(raw_kv):,} raw metadata lines in {time.time() - t0:.2f}s")

    entities = {
        "Quest": defaultdict(dict),
        "Npc": defaultdict(dict),
        "Item": defaultdict(dict),
        "Object": defaultdict(dict),
    }

    key_pat = re.compile(r"^X-(Quest|Npc|Item|Object)-(\d+)-(S|\d+)$")
    chunk_pat = re.compile(r"^~(\d+)~$")

    for k, v in raw_kv.items():
        m = key_pat.match(k)
        if not m:
            continue
        kind, eid_str, field = m.group(1), m.group(2), m.group(3)
        eid = int(eid_str)

        cm = chunk_pat.match(v)
        if cm:
            n_chunks = int(cm.group(1))
            b64_str = "".join(raw_kv.get(f"{k}-{i}", "") for i in range(1, n_chunks + 1))
        else:
            b64_str = v

        try:
            decoded, _ = cbor_decode(base64.b64decode(b64_str))
        except Exception:
            continue

        if field == "S" and isinstance(decoded, dict):
            for sk, sv in decoded.items():
                if isinstance(sk, int):
                    entities[kind][eid][sk] = sv
        elif field != "S":
            entities[kind][eid][int(field)] = decoded

    print(
        f"Decoded entities -> Quests: {len(entities['Quest']):,}, "
        f"NPCs: {len(entities['Npc']):,}, Items: {len(entities['Item']):,}, "
        f"Objects: {len(entities['Object']):,}"
    )
    return entities


def lua_idx(c, idx1):
    """Access 1-based Lua table index whether CBOR decoded it as a list or a sparse dict."""
    if isinstance(c, list):
        return c[idx1 - 1] if 0 <= idx1 - 1 < len(c) else None
    if isinstance(c, dict):
        return c.get(idx1)
    return None


def lua_list(c):
    """Iterate values of a Lua list/table whether CBOR decoded it as a list or a dict."""
    if isinstance(c, list):
        return c
    if isinstance(c, dict):
        return [v for _, v in sorted(c.items(), key=lambda kv: kv[0] if isinstance(kv[0], int) else 0)]
    return []


def format_spawns(spawns_dict, fallback_zid, resolve_zone) -> tuple[str, str]:
    """Returns (primary_zone_str, coords_str)"""
    if isinstance(spawns_dict, list):
        spawns_dict = {i + 1: v for i, v in enumerate(spawns_dict) if v}
    if not isinstance(spawns_dict, dict) or not spawns_dict:
        z = resolve_zone(fallback_zid) if fallback_zid else "Unknown"
        return z, ""
    parts = []
    primary_zone = None
    for zid, coords in spawns_dict.items():
        if not isinstance(zid, int):
            continue
        zname = resolve_zone(zid)
        if primary_zone is None:
            primary_zone = zname
        clist = lua_list(coords)
        if clist:
            first = lua_list(clist[0])
            if len(first) >= 2 and isinstance(first[0], (int, float)) and first[0] != -1:
                parts.append(f"{zname} ({first[0]:.1f}, {first[1]:.1f})")
            else:
                parts.append(zname)
        else:
            parts.append(zname)
    return primary_zone or (resolve_zone(fallback_zid) if fallback_zid else "Unknown"), "; ".join(parts[:3])


def build_sqlite():
    resolve_zone = load_zone_and_sort_names()
    drop_rates = load_drop_rates()
    boss_loot = load_atlasloot_droprates(drop_rates)
    item_tooltips = load_classic_item_tooltips()
    extra_lore, fish_zones = load_addon_lore_and_fish()
    xp_map = load_quest_xp()
    entities = parse_toc()

    quests = entities["Quest"]
    npcs = entities["Npc"]
    items = entities["Item"]
    objects = entities["Object"]

    existing_lore = []
    if os.path.exists(OUT_DB):
        try:
            old_conn = sqlite3.connect(OUT_DB)
            existing_lore = old_conn.execute("SELECT title, aliases, summary FROM lore").fetchall()
            old_conn.close()
        except Exception:
            pass
        os.remove(OUT_DB)

    conn = sqlite3.connect(OUT_DB)
    cur = conn.cursor()
    cur.executescript(
        """
        PRAGMA journal_mode=WAL;
        PRAGMA synchronous=NORMAL;

        CREATE TABLE quests (
            id INTEGER PRIMARY KEY,
            name TEXT,
            zone TEXT,
            req_level INTEGER,
            quest_level INTEGER,
            faction TEXT,
            classes TEXT,
            started_by TEXT,
            finished_by TEXT,
            objectives TEXT,
            prereqs TEXT,
            next_quest TEXT,
            summary TEXT
        );

        CREATE TABLE npcs (
            id INTEGER PRIMARY KEY,
            name TEXT,
            subname TEXT,
            zone TEXT,
            min_level INTEGER,
            max_level INTEGER,
            faction TEXT,
            coords TEXT,
            starts_quests TEXT,
            ends_quests TEXT,
            summary TEXT
        );

        CREATE TABLE items (
            id INTEGER PRIMARY KEY,
            name TEXT,
            req_level INTEGER,
            item_level INTEGER,
            starts_quest TEXT,
            dropped_by TEXT,
            sold_by TEXT,
            summary TEXT
        );

        CREATE VIRTUAL TABLE wow_fts USING fts5(
            kind,
            entity_id UNINDEXED,
            name,
            zone,
            summary,
            tokenize='porter unicode61'
        );

        CREATE TABLE lore (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT UNIQUE,
            aliases TEXT,
            summary TEXT
        );

        CREATE VIRTUAL TABLE lore_fts USING fts5(
            title,
            aliases,
            summary,
            content='lore',
            content_rowid='id'
        );
        """
    )

    # Pre-format NPCs (including rank [ELITE/RARE], npcFlags, Weapon Master skills, and AtlasLoot Boss Drops)
    npc_loc_str = {}
    npc_is_elite = set()
    npc_rows = []
    fts_rows = []

    for nid, d in sorted(npcs.items()):
        name = d.get(1) or f"NPC #{nid}"
        raw_subname = d.get(14) or ""
        min_lvl = d.get(4) or 0
        max_lvl = d.get(5) or min_lvl
        rank = d.get(6) or 0
        rank_str = NPC_RANK_NAMES.get(rank, "")
        if rank in (1, 2, 3):
            npc_is_elite.add(nid)
        npc_flags = d.get(15) or 0
        flag_roles = [label for bit, label in NPC_FLAG_TAGS if isinstance(npc_flags, int) and (npc_flags & bit)]
        if "Repair Vendor" in flag_roles and "Vendor" in flag_roles:
            flag_roles.remove("Vendor")
        role_suffix = ", ".join(flag_roles[:2])
        subname = f"{raw_subname} [{role_suffix}]".strip() if role_suffix and role_suffix.lower() not in raw_subname.lower() else raw_subname

        faction = str(d.get(13) or "Both")
        primary_zone, coords_str = format_spawns(d.get(7), d.get(9), resolve_zone)
        if nid in WEAPON_MASTER_SKILLS:
            _, wm_loc, _ = WEAPON_MASTER_SKILLS[nid]
            coords_str = wm_loc
        elite_inline = f" [{rank_str}]" if rank_str else ""
        npc_loc_str[nid] = f"{name}{elite_inline} ({coords_str or primary_zone})"

        q_starts = [f"{quests[qid].get(1, f'Quest #{qid}')} (#{qid})" for qid in lua_list(d.get(10)) if qid in quests]
        q_ends = [f"{quests[qid].get(1, f'Quest #{qid}')} (#{qid})" for qid in lua_list(d.get(11)) if qid in quests]

        starts_s = ", ".join(q_starts[:8])
        ends_s = ", ".join(q_ends[:8])
        lvl_s = f"Lvl {min_lvl}" if min_lvl == max_lvl else f"Lvl {min_lvl}-{max_lvl}"
        if rank_str:
            lvl_s += f" [{rank_str}]"
        tag_s = f" <{subname}>" if subname else ""
        summary = f"NPC: {name}{tag_s} (#{nid}) | {lvl_s} | {coords_str or primary_zone}"
        if nid in WEAPON_MASTER_SKILLS:
            summary += f" | Teaches Weapon Skills: {WEAPON_MASTER_SKILLS[nid][2]}"
        if nid in boss_loot:
            loot_entries = sorted(boss_loot[nid], key=lambda x: x[1], reverse=True)
            loot_strs = [f"{items[iid].get(1, f'Item #{iid}')} (#{iid}, {pct:.1f}%)" for iid, pct in loot_entries[:8] if iid in items]
            if loot_strs:
                summary += f" | Notable Loot Drops: {', '.join(loot_strs)}"
        if starts_s:
            summary += f" | Starts Quests: {starts_s}"
        if ends_s:
            summary += f" | Ends Quests: {ends_s}"

        npc_rows.append((nid, name, subname, primary_zone, min_lvl, max_lvl, faction, coords_str, starts_s, ends_s, summary))
        fts_rows.append(("npc", nid, f"{name} {subname}".strip(), primary_zone, summary))

    cur.executemany("INSERT INTO npcs VALUES (?,?,?,?,?,?,?,?,?,?,?)", npc_rows)

    # Load WhatsTraining recipes now that npc_loc_str is populated
    crafted_by, teaches_recipe = load_whatstraining_recipes(npcs, quests, npc_loc_str)

    # Pre-format Objects
    obj_loc_str = {}
    for oid, d in objects.items():
        name = d.get(1) or f"Object #{oid}"
        primary_zone, coords_str = format_spawns(d.get(4), d.get(5), resolve_zone)
        obj_loc_str[oid] = f"Object '{name}' at {coords_str or primary_zone}"

    # Pre-format Items + build quest -> reward items map from field 6 (questRewards)
    item_source_str = {}
    quest_rewards_map = defaultdict(list)
    item_rows = []
    for iid, d in sorted(items.items()):
        name = d.get(1) or f"Item #{iid}"
        ilvl = d.get(9) or 0
        rlvl = d.get(10) or 0
        start_qid = d.get(5)
        start_q_str = f"{quests[start_qid].get(1, f'Quest #{start_qid}')} (#{start_qid})" if start_qid and start_qid in quests else ""
        q_zone = resolve_zone(quests[start_qid].get(17) or 0) if start_qid and start_qid in quests else ""

        reward_qids = [qid for qid in lua_list(d.get(6)) if qid in quests]
        for rqid in reward_qids:
            quest_rewards_map[rqid].append(f"{name} (#{iid})")
        reward_from_s = ", ".join(f"{quests[rqid].get(1, f'Quest #{rqid}')} (#{rqid})" for rqid in reward_qids[:4])

        # Resolve top NPC droppers (prioritizing quest zone + drop rate, including AtlasLoot boss drop rates)
        rates = drop_rates.get(iid, {})
        npc_drop_ids = list(dict.fromkeys(lua_list(d.get(2)) + list(rates.keys())))
        sorted_npcs = sorted(
            npc_drop_ids,
            key=lambda n: (1 if q_zone and q_zone in npc_loc_str.get(n, "") else 0, rates.get(n, 0.0)),
            reverse=True,
        )
        drop_parts = []
        fallback_pct = ""
        if start_q_str and npc_drop_ids and not rates:
            # shortcut: Questie omits % for quest-starter drops; use 100% for single-boss starters, ~2-4% for multi-mob world starters
            fallback_pct = " (~100% drop)" if len(npc_drop_ids) == 1 else " (~2-4% drop)"
        for nid in sorted_npcs[:6]:
            nname = npcs.get(nid, {}).get(1, f"NPC #{nid}")
            nloc = npc_loc_str.get(nid, nname)
            pct = rates.get(nid)
            drop_parts.append(f"{nloc} ({pct:.1f}%)" if pct else f"{nloc}{fallback_pct}")

        # Object drops
        for oid in lua_list(d.get(3))[:3]:
            drop_parts.append(obj_loc_str.get(oid, f"Object #{oid}"))

        dropped_s = "; ".join(drop_parts)
        vendors_s = "; ".join(npc_loc_str.get(nid, f"NPC #{nid}") for nid in lua_list(d.get(14))[:4])

        src_desc = f"Item Drop '{name}' (#{iid})"
        if dropped_s:
            src_desc += f" from {dropped_s}"
        elif vendors_s:
            src_desc += f" sold by {vendors_s}"
        item_source_str[iid] = src_desc

        tt_str = f" {item_tooltips[iid]}" if iid in item_tooltips else ""
        summary = f"Item: {name} (#{iid}, ReqLvl {rlvl}, iLvl {ilvl}){tt_str}"
        if start_q_str:
            summary += f" | Starts Quest: {start_q_str}"
        if reward_from_s:
            summary += f" | Reward from Quest: {reward_from_s}"
        if iid in crafted_by:
            prof, skill, teach_id, src_s = crafted_by[iid]
            t_name = f", Recipe: {items[teach_id].get(1, f'#{teach_id}')} (#{teach_id})" if teach_id and teach_id in items else ""
            summary += f" | Crafted by: {prof} ({skill}) [Learned via {src_s}{t_name}]"
        if iid in teaches_recipe:
            prof, skill, creates_id, src_s = teaches_recipe[iid]
            c_name = f" -> Crafts: {items[creates_id].get(1, f'#{creates_id}')} (#{creates_id})" if creates_id and creates_id in items else ""
            summary += f" | Recipe: {prof} ({skill}) [{src_s}{c_name}]"
        if iid in fish_zones:
            summary += f" | Caught Fishing in: {', '.join(fish_zones[iid][:6])}"
        if dropped_s:
            summary += f" | Dropped by: {dropped_s}"
        if vendors_s:
            summary += f" | Sold by: {vendors_s}"

        item_rows.append((iid, name, rlvl, ilvl, start_q_str, dropped_s, vendors_s, summary))
        fts_rows.append(("item", iid, name, "", summary))

    cur.executemany("INSERT INTO items VALUES (?,?,?,?,?,?,?,?)", item_rows)

    # Pre-format Quests
    quest_rows = []
    for qid, d in sorted(quests.items()):
        name = d.get(1) or f"Quest #{qid}"
        if "UNUSE" in name or name.startswith("<TXT>") or name.startswith("[DEPRECATED"):
            continue
        rlvl = d.get(4) or 0
        qlvl = d.get(5) or rlvl
        faction = decode_faction(d.get(6) or 0)
        classes = decode_classes(d.get(7) or 0)
        z_or_s = d.get(17) or 0
        zone = resolve_zone(z_or_s)

        # startedBy = [npcIds, objIds, itemIds]
        sb = d.get(2)
        sb_npcs = lua_list(lua_idx(sb, 1))
        sb_objs = lua_list(lua_idx(sb, 2))
        sb_items = lua_list(lua_idx(sb, 3))
        sb_parts = []
        for nid in sb_npcs[:4]:
            sb_parts.append(f"NPC {npc_loc_str.get(nid, f'#{nid}')}")
        for oid in sb_objs[:3]:
            sb_parts.append(obj_loc_str.get(oid, f"Object #{oid}"))
        for iid in sb_items[:3]:
            sb_parts.append(item_source_str.get(iid, f"Item #{iid}"))
        if not sb_parts and d.get(11):
            # Fallback to sourceItemId if startedBy was empty
            siid = d.get(11)
            if siid in item_source_str and items.get(siid, {}).get(5) == qid:
                sb_parts.append(item_source_str[siid])
        started_by = "; ".join(sb_parts) or "Auto/Chain"

        # If zone was negative sortKey or Unknown, enrich with starter NPC's zone if possible
        if (z_or_s <= 0 or zone.startswith("Zone #")) and sb_npcs:
            first_nid = sb_npcs[0]
            if first_nid in npcs:
                nz, _ = format_spawns(npcs[first_nid].get(7), npcs[first_nid].get(9), resolve_zone)
                if nz and nz != "Unknown" and nz != zone:
                    zone = f"{zone} ({nz})" if z_or_s < 0 else nz

        # finishedBy = [npcIds, objIds]
        fb = d.get(3)
        fb_npcs = lua_list(lua_idx(fb, 1))
        fb_objs = lua_list(lua_idx(fb, 2))
        fb_parts = []
        for nid in fb_npcs[:4]:
            fb_parts.append(f"NPC {npc_loc_str.get(nid, f'#{nid}')}")
        for oid in fb_objs[:3]:
            fb_parts.append(obj_loc_str.get(oid, f"Object #{oid}"))
        finished_by = "; ".join(fb_parts) or "None"

        # Objectives text + structured targets (creatures idx 1, objects idx 2, items idx 3, triggerEnd field 9, extraObjectives field 29)
        obj_texts = lua_list(d.get(8))
        obj_str = " ".join(t.strip() for t in obj_texts if isinstance(t, str))
        obj_struct = d.get(10)
        target_parts = []
        has_elite_target = False
        for entry in lua_list(lua_idx(obj_struct, 1))[:4]:
            elist = lua_list(entry)
            if elist:
                if elist[0] in npc_is_elite:
                    has_elite_target = True
                target_parts.append(npc_loc_str.get(elist[0], f"NPC #{elist[0]}"))
        for entry in lua_list(lua_idx(obj_struct, 2))[:3]:
            elist = lua_list(entry)
            if elist:
                target_parts.append(obj_loc_str.get(elist[0], f"Object #{elist[0]}"))
        for entry in lua_list(lua_idx(obj_struct, 3))[:4]:
            elist = lua_list(entry)
            if elist:
                target_parts.append(item_source_str.get(elist[0], f"Item #{elist[0]}"))

        # triggerEnd (field 9: [text, spawnsDict])
        trig = lua_list(d.get(9))
        if len(trig) >= 2 and trig[1]:
            _, trig_coords = format_spawns(trig[1], z_or_s if z_or_s > 0 else 0, resolve_zone)
            if trig_coords:
                t_label = trig[0] if isinstance(trig[0], str) and trig[0] else "Trigger"
                target_parts.append(f"{t_label} at {trig_coords}")

        # extraObjectives (field 29: [[spawnsDict, icon, text, ...], ...])
        for ex in lua_list(d.get(29))[:2]:
            ex_list = lua_list(ex)
            if ex_list and ex_list[0]:
                _, ex_coords = format_spawns(ex_list[0], z_or_s if z_or_s > 0 else 0, resolve_zone)
                ex_txt = ex_list[2] if len(ex_list) >= 3 and isinstance(ex_list[2], str) else "Use item"
                if ex_coords:
                    target_parts.append(f"{ex_txt} at {ex_coords}")

        if target_parts:
            obj_str = (obj_str + " [Targets: " + "; ".join(target_parts) + "]").strip()

        # Prereqs (field 12 preQuestGroup + field 13 preQuestSingle)
        pre_ids = lua_list(d.get(12)) + lua_list(d.get(13))
        prereqs = ", ".join(f"{quests[pid].get(1, f'#{pid}')} (#{pid})" for pid in pre_ids if pid in quests)

        # Next quest (field 22)
        next_qid = d.get(22)
        next_quest = f"{quests[next_qid].get(1, f'#{next_qid}')} (#{next_qid})" if next_qid and next_qid in quests else ""

        # Quest flags (field 23), requiredSkill (field 18), breadcrumb (field 27), XP, Rewards
        qflags = d.get(23) or 0
        qtags = []
        if (isinstance(qflags, int) and (qflags & 32)) or has_elite_target:
            qtags.append("ELITE")
        if isinstance(qflags, int) and (qflags & 64):
            qtags.append("RAID")
        if d.get(27):
            qtags.append("BREADCRUMB")
        qtag_s = f" [{'/'.join(qtags)}]" if qtags else ""

        req_skill = lua_list(d.get(18))
        skill_tag = ""
        if len(req_skill) >= 2 and req_skill[0] in PROFESSION_NAMES:
            skill_tag = f" | Requires: {PROFESSION_NAMES[req_skill[0]]} ({req_skill[1]})"

        xp_val = xp_map.get(qid)
        xp_tag = f" | XP: {xp_val}" if xp_val else ""
        rewards_list = quest_rewards_map.get(qid, [])
        rewards_tag = f" | Rewards: {', '.join(rewards_list[:4])}" if rewards_list else ""

        cls_tag = f" | Class: {classes}" if classes != "All" else ""
        summary = (
            f"Quest: {name} (#{qid}){qtag_s} | Zone: {zone} | ReqLvl {rlvl} (QuestLvl {qlvl}){xp_tag} | Faction: {faction}{cls_tag}{skill_tag} "
            f"| Start: {started_by} | Turn-in: {finished_by}"
        )
        if obj_str:
            summary += f" | Objective: {obj_str}"
        if rewards_tag:
            summary += rewards_tag
        if prereqs:
            summary += f" | Prereq: {prereqs}"
        if next_quest:
            summary += f" | Next: {next_quest}"

        quest_rows.append((qid, name, zone, rlvl, qlvl, faction, classes, started_by, finished_by, obj_str, prereqs, next_quest, summary))
        fts_rows.append(("quest", qid, name, zone, summary))

    cur.executemany("INSERT INTO quests VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", quest_rows)
    cur.executemany("INSERT INTO wow_fts VALUES (?,?,?,?,?)", fts_rows)

    # Restore existing lore + upsert addon lore & curated mechanics guides
    if existing_lore:
        cur.executemany("INSERT OR REPLACE INTO lore (title, aliases, summary) VALUES (?,?,?)", existing_lore)
    if extra_lore:
        cur.executemany("INSERT OR REPLACE INTO lore (title, aliases, summary) VALUES (?,?,?)", extra_lore)
    cur.executemany("INSERT OR REPLACE INTO lore (title, aliases, summary) VALUES (?,?,?)", CURATED_MECHANICS_LORE)
    cur.execute("INSERT INTO lore_fts(lore_fts) VALUES('rebuild');")

    cur.execute("CREATE INDEX idx_quests_zone_lvl ON quests(zone, req_level, quest_level);")
    cur.execute("CREATE INDEX idx_quests_name ON quests(name COLLATE NOCASE);")
    cur.execute("CREATE INDEX idx_npcs_name ON npcs(name COLLATE NOCASE);")
    cur.execute("CREATE INDEX idx_items_name ON items(name COLLATE NOCASE);")
    conn.commit()
    lore_cnt = cur.execute("SELECT count(*) FROM lore").fetchone()[0]
    conn.close()
    print(f"Built {OUT_DB} ({os.path.getsize(OUT_DB) / (1024*1024):.2f} MB) with {len(quest_rows):,} quests, {len(npc_rows):,} NPCs, {len(item_rows):,} items, {lore_cnt:,} lore/mechanics guides.")


STATE_FILE = os.path.join(BASE_DIR, ".wow_db_state.json")
SHELDON_TARGET = os.environ.get("WOW_REMOTE_SYNC_TARGET", "user@your-agent-host:~/.openclaw/workspace/wow_forever.sqlite")


def compute_source_fingerprint() -> dict:
    """Fast mtime+size fingerprint across all tracked addon data files on Hyperion."""
    watched = [
        TOC_PATH,
        ZONES_LUA,
        QUESTS_LUA,
        SUBZONES_LUA,
        DROPS_LUA,
        DROP_CORR_LUA,
        XP_LUA,
        CLASSIC_ITEMS_JSON,
        ATLASLOOT_DROPRATE_LUA,
        os.path.join(ADDON_ROOT, r"WhatsTraining\Classes\WeaponSkills.lua"),
        os.path.join(ADDON_ROOT, r"WhatsTraining\Classes\Camelot\WeaponSkills.lua"),
        os.path.join(ADDON_ROOT, r"GatherLite\scripts\nodes.lua"),
        os.path.join(ADDON_ROOT, r"GatherLite\plugins\database\data\forever.lua"),
        os.path.join(ADDON_ROOT, r"GatherLite\plugins\fishing\data.lua"),
        os.path.join(ADDON_ROOT, r"Spoken_Places\Data\enUS\Zones.lua"),
        os.path.join(ADDON_ROOT, r"Spoken_Places\Data\enUS\Subzones.lua"),
        os.path.join(ADDON_ROOT, r"Spoken_Bosses\Data\Stories.lua"),
        __file__,
    ]
    fp = {}
    for p in watched:
        if os.path.exists(p):
            st = os.stat(p)
            fp[p] = f"{int(st.st_mtime)}:{st.st_size}"
    return fp


def sync_if_changed(force: bool = False):
    import subprocess
    cur_fp = compute_source_fingerprint()
    old_fp = {}
    if os.path.exists(STATE_FILE) and not force:
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                old_fp = json.load(f)
        except Exception:
            pass
    if not force and cur_fp == old_fp and os.path.exists(OUT_DB):
        print("wow_forever.sqlite is already up to date with all AddOns (0 changes).")
        return
    print("Addon update detected -> rebuilding wow_forever.sqlite and syncing to Sheldon...")
    build_sqlite()
    res = subprocess.run(["scp", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", OUT_DB, SHELDON_TARGET])
    if res.returncode == 0:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(cur_fp, f, indent=2)
        print("Successfully synced updated wow_forever.sqlite to Sheldon!")
    else:
        print("Warning: Sheldon unreachable during SCP; state file left dirty so next run retries sync.")


if __name__ == "__main__":
    import sys
    if "--sync-if-changed" in sys.argv:
        sync_if_changed(force="--force" in sys.argv)
    else:
        build_sqlite()
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(compute_source_fingerprint(), f, indent=2)
