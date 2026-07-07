import asyncio
import copy
import itertools
import json
import os
import random
import time
import traceback
from datetime import datetime, timedelta

import discord
from discord.ext import commands, tasks

try:
    import psycopg2
    import psycopg2.extras
    HAS_PG = True
except ImportError:
    HAS_PG = False

_raw_db_url = os.getenv("DATABASE_URL")

# If DATABASE_URL is not set, try constructing from Railway's individual PG variables
if not _raw_db_url:
    pg_host = os.getenv("PGHOST") or os.getenv("PGHOSTADDR")
    pg_port = os.getenv("PGPORT", "5432")
    pg_user = os.getenv("PGUSER")
    pg_pass = os.getenv("PGPASSWORD")
    pg_db = os.getenv("PGDATABASE")
    if pg_host and pg_user and pg_pass and pg_db:
        from urllib.parse import quote_plus
        _raw_db_url = f"postgresql://{quote_plus(pg_user)}:{quote_plus(pg_pass)}@{pg_host}:{pg_port}/{pg_db}"

DATABASE_URL = _raw_db_url
_PG_CONN = None

def _pg_connect():
    global _PG_CONN
    if not DATABASE_URL or not HAS_PG:
        print(f"[PG] Skipping PG: DATABASE_URL={'set' if DATABASE_URL else 'NOT SET'}, HAS_PG={HAS_PG}")
        return None
    if _PG_CONN and _PG_CONN.closed == 0:
        return _PG_CONN
    masked_url = DATABASE_URL[:DATABASE_URL.rfind("@")+1] + "***" if "@" in DATABASE_URL else "no @ found"
    print(f"[PG] Attempting connection to: {masked_url}")
    try:
        _PG_CONN = psycopg2.connect(DATABASE_URL, connect_timeout=5)
        _PG_CONN.autocommit = True
        cur = _PG_CONN.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS bot_data (
                key TEXT PRIMARY KEY,
                value JSONB NOT NULL
            )
        """)
        cur.close()
        print("[PG] Connected and table ready")
        return _PG_CONN
    except Exception as e:
        print(f"[PG] Connection failed: {e}")
        print(f"[PG] DATABASE_URL prefix: {masked_url}")
        _PG_CONN = None
        return None

def _load_pg(key: str) -> dict:
    conn = _pg_connect()
    if not conn:
        return None
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT value FROM bot_data WHERE key = %s", (key,))
        row = cur.fetchone()
        cur.close()
        return row["value"] if row else {}
    except Exception:
        return None

def _save_pg(key: str, data: dict) -> None:
    conn = _pg_connect()
    if not conn:
        return
    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO bot_data (key, value) VALUES (%s, %s::jsonb) ON CONFLICT (key) DO UPDATE SET value = %s::jsonb",
            (key, json.dumps(data), json.dumps(data))
        )
        cur.close()
    except Exception:
        pass

# =============================================================================
# CONFIG
# =============================================================================
TOKEN = os.getenv("DISCORD_TOKEN")
BOT_OWNER_ID = 1406336844882383049
DATA_FILE = "data.json"
AUCTION_FILE = "auctions.json"

ROLL_ANIMATION_DELAY = 1.7
MAX_SPINS = 50
KEY_DROP_CHANCE = 0.08
KEY_DROP_MIN = 1
KEY_DROP_MAX = 4
BONUS_BELI_CHANCE = 0.05
BONUS_BELI_MAX = 200_000
DAILY_COOLDOWN_HOURS = 24
DAILY_BONUS_BERRIES = 20_000
DUPLICATE_CONVERT_RATE = 0.5
DEFAULT_TEAM_SIZE = 4
MAX_TEAM_SIZE_CAP = 6
PITY_THRESHOLD = 200
MAX_BERRIES = 10_000_000

BRAND_COLOR = 0xD32F2F
FOOTER_TEXT = "OP Bot • One Piece Collector"

SHOP_LUCK_COST = 2_000_000
SHOP_LUCK_MINUTES = 10
SHOP_LUCK_MULTIPLIER = 2
SHOP_KEY_COST = 1_000_000
SHOP_REFILL_COST = 500_000
SHOP_TEAMSLOT_COST = 5_000_000
SHOP_FASTSPINS_COST = 1_000_000
SHOP_FASTSPINS_COUNT = 10
SHOP_AUTOROLL_COST = 1_500_000
SHOP_AUTOROLL_MINUTES = 10
SHOP_AUTOROLL_MAX = 30
SHOP_AUTOROLL_BREAK = 40
SPIN_CONSUME_AUTOROLL = 8

# =============================================================================
# ASSET IMAGES (PNG URLs for icons used throughout the bot)
# =============================================================================


# =============================================================================
# RACES (randomized per pull, small stat modifiers)
# =============================================================================
RACES = {
    "Human":     {"power": 1.00, "health": 1.00, "speed": 1.00, "emoji": "\U0001f9d1", "desc": "Balanced", "value": 1.00},
    "Fishman":   {"power": 1.10, "health": 1.05, "speed": 1.00, "emoji": "\U0001f41f", "desc": "+10% Power, +5% Health", "value": 1.25},
    "Mink":      {"power": 1.00, "health": 1.00, "speed": 1.15, "emoji": "\U0001f43e", "desc": "+15% Speed", "value": 1.10},
    "Merfolk":   {"power": 0.95, "health": 1.15, "speed": 1.05, "emoji": "\U0001f9dc", "desc": "+15% Health, +5% Speed", "value": 1.10},
    "Giant":     {"power": 1.15, "health": 1.20, "speed": 0.80, "emoji": "\U0001f98d", "desc": "+15% Power, +20% Health, -20% Speed", "value": 1.15},
    "Lunarian":  {"power": 1.10, "health": 1.10, "speed": 1.10, "emoji": "\U0001f525", "desc": "+10% All Stats", "value": 1.50},
    "Skypiean":  {"power": 1.00, "health": 0.95, "speed": 1.10, "emoji": "\u2601\ufe0f", "desc": "+10% Speed, -5% Health", "value": 0.95},
    "Longarm":   {"power": 1.08, "health": 1.00, "speed": 1.02, "emoji": "\U0001f4aa", "desc": "+8% Power", "value": 1.10},
    "Longleg":   {"power": 1.02, "health": 1.00, "speed": 1.08, "emoji": "\U0001f9b5", "desc": "+8% Speed", "value": 1.10},
    "Dwarf":     {"power": 0.90, "health": 0.85, "speed": 1.20, "emoji": "\U0001f4cf", "desc": "+20% Speed, -15% Health, -10% Power", "value": 0.90},
    "Buccaneer": {"power": 1.12, "health": 1.15, "speed": 0.90, "emoji": "\u2620\ufe0f", "desc": "+12% Power, +15% Health, -10% Speed", "value": 1.30},
    "Kuja":      {"power": 1.05, "health": 0.90, "speed": 1.10, "emoji": "\U0001f3f9", "desc": "+5% Power, +10% Speed, -10% Health", "value": 1.00},
    "Cyborg":    {"power": 1.10, "health": 1.10, "speed": 0.95, "emoji": "\u2699\ufe0f", "desc": "+10% Power, +10% Health, -5% Speed", "value": 1.15},
}

RACE_NAMES = list(RACES.keys())

RACE_TIERS = {
    "Human": 1, "Dwarf": 1, "Skypiean": 1, "Kuja": 1,
    "Longarm": 2, "Longleg": 2, "Giant": 2, "Mink": 2, "Merfolk": 2, "Cyborg": 2,
    "Fishman": 3, "Buccaneer": 3, "Lunarian": 3,
}

RARITY_RACE_WEIGHTS = {
    "E": [0.70, 0.25, 0.05],
    "D": [0.50, 0.40, 0.10],
    "C": [0.25, 0.55, 0.20],
    "B": [0.15, 0.55, 0.30],
    "A": [0.05, 0.45, 0.50],
    "S": [0.02, 0.28, 0.70],
    "SS": [0.01, 0.19, 0.80],
    "HDYGT": [0.00, 0.10, 0.90],
}

RACE_TIER_POOLS = {1: [], 2: [], 3: []}
for rn, tier in RACE_TIERS.items():
    RACE_TIER_POOLS[tier].append(rn)

# =============================================================================
# DEVIL FRUITS (non-canon pool, flat stat bonuses)
# =============================================================================
FRUIT_DROP_CHANCE = 0.20
FRUIT_RARITIES = {
    "Common":    {"weight": 45, "color": 0xB0BEC5, "emoji": "\U0001f34e", "value": 1.00},
    "Uncommon":  {"weight": 28, "color": 0x4CAF50, "emoji": "\U0001f34b", "value": 1.10},
    "Rare":      {"weight": 18, "color": 0x2196F3, "emoji": "\U0001f34a", "value": 1.25},
    "Legendary": {"weight":  7, "color": 0xFFC107, "emoji": "\U0001f34c", "value": 1.50},
    "Mythical":  {"weight":  2, "color": 0xE040FB, "emoji": "\U0001f32a\ufe0f", "value": 2.00},
}

FRUITS = [
    # Common Paramecia
    {"name": "Bara Bara no Mi", "type": "Paramecia", "rarity": "Common", "power": 50, "health": 0, "speed": 30},
    {"name": "Sube Sube no Mi", "type": "Paramecia", "rarity": "Common", "power": 0, "health": 80, "speed": 40},
    {"name": "Kilo Kilo no Mi", "type": "Paramecia", "rarity": "Common", "power": 70, "health": 40, "speed": 0},
    {"name": "Bomu Bomu no Mi", "type": "Paramecia", "rarity": "Common", "power": 80, "health": 0, "speed": 20},
    {"name": "Horu Horu no Mi", "type": "Paramecia", "rarity": "Common", "power": 30, "health": 60, "speed": 10},
    {"name": "Beri Beri no Mi", "type": "Paramecia", "rarity": "Common", "power": 40, "health": 70, "speed": 0},
    # Uncommon Paramecia
    {"name": "Hana Hana no Mi", "type": "Paramecia", "rarity": "Uncommon", "power": 100, "health": 50, "speed": 40},
    {"name": "Doku Doku no Mi", "type": "Paramecia", "rarity": "Uncommon", "power": 140, "health": 70, "speed": 0},
    {"name": "Supaa Supaa no Mi", "type": "Paramecia", "rarity": "Uncommon", "power": 120, "health": 80, "speed": 20},
    {"name": "Doa Doa no Mi", "type": "Paramecia", "rarity": "Uncommon", "power": 60, "health": 120, "speed": 30},
    {"name": "Jiki Jiki no Mi", "type": "Paramecia", "rarity": "Uncommon", "power": 150, "health": 50, "speed": 30},
    {"name": "Hira Hira no Mi", "type": "Paramecia", "rarity": "Uncommon", "power": 90, "health": 60, "speed": 60},
    # Rare
    {"name": "Gomu Gomu no Mi", "type": "Paramecia", "rarity": "Rare", "power": 200, "health": 150, "speed": 100},
    {"name": "Moku Moku no Mi", "type": "Logia", "rarity": "Rare", "power": 180, "health": 200, "speed": 40},
    {"name": "Suna Suna no Mi", "type": "Logia", "rarity": "Rare", "power": 220, "health": 150, "speed": 50},
    {"name": "Buki Buki no Mi", "type": "Paramecia", "rarity": "Rare", "power": 250, "health": 80, "speed": 60},
    {"name": "Noro Noro no Mi", "type": "Paramecia", "rarity": "Rare", "power": 100, "health": 100, "speed": 200},
    {"name": "Pika Pika no Mi", "type": "Logia", "rarity": "Rare", "power": 200, "health": 100, "speed": 150},
    # Legendary
    {"name": "Mera Mera no Mi", "type": "Logia", "rarity": "Legendary", "power": 350, "health": 150, "speed": 100},
    {"name": "Yami Yami no Mi", "type": "Logia", "rarity": "Legendary", "power": 400, "health": 200, "speed": 50},
    {"name": "Gura Gura no Mi", "type": "Paramecia", "rarity": "Legendary", "power": 450, "health": 100, "speed": 50},
    {"name": "Ope Ope no Mi", "type": "Paramecia", "rarity": "Legendary", "power": 300, "health": 200, "speed": 150},
    {"name": "Goro Goro no Mi", "type": "Logia", "rarity": "Legendary", "power": 380, "health": 120, "speed": 130},
    {"name": "Magu Magu no Mi", "type": "Logia", "rarity": "Legendary", "power": 420, "health": 180, "speed": 60},
    # Mythical
    {"name": "Hito Hito no Mi, Model: Nika", "type": "Mythical Zoan", "rarity": "Mythical", "power": 600, "health": 300, "speed": 200},
    {"name": "Uo Uo no Mi, Model: Seiryu", "type": "Mythical Zoan", "rarity": "Mythical", "power": 500, "health": 500, "speed": 100},
    {"name": "Soru Soru no Mi", "type": "Paramecia", "rarity": "Mythical", "power": 400, "health": 400, "speed": 150},
    {"name": "Tori Tori no Mi, Model: Phoenix", "type": "Mythical Zoan", "rarity": "Mythical", "power": 450, "health": 350, "speed": 180},
    {"name": "Inu Inu no Mi, Model: Kyubi", "type": "Mythical Zoan", "rarity": "Mythical", "power": 400, "health": 300, "speed": 250},
]

# =============================================================================
# RARITIES / CHARACTERS (mostly unchanged)
# =============================================================================
# Clean 1-in-X odds: weight = 1M / denominator
RARITY_ODDS = {
    "E":     2,        # 1 in 2
    "D":     4,        # 1 in 4
    "C":     10,       # 1 in 10
    "B":     25,       # 1 in 25
    "A":     100,      # 1 in 100
    "S":     500,      # 1 in 500
    "SS":    2_000,    # 1 in 2,000
    "HDYGT": 1_000_000, # 1 in 1,000,000
}

RARITIES = {
    "E":     {"weight": 500_000,  "color": 0xB0BEC5, "emoji": "\u26aa", "value": 5_000},
    "D":     {"weight": 250_000,  "color": 0x4CAF50, "emoji": "\U0001f7e2", "value": 15_000},
    "C":     {"weight": 100_000,  "color": 0x2196F3, "emoji": "\U0001f535", "value": 40_000},
    "B":     {"weight": 40_000,   "color": 0x9C27B0, "emoji": "\U0001f7e3", "value": 120_000},
    "A":     {"weight": 10_000,   "color": 0xFFC107, "emoji": "\U0001f7e1", "value": 400_000},
    "S":     {"weight": 2_000,    "color": 0xFF1744, "emoji": "\U0001f534", "value": 1_200_000},
    "SS":    {"weight": 500,      "color": 0x00E5FF, "emoji": "\u2694\ufe0f", "value": 3_000_000},
    "HDYGT": {"weight": 1,        "color": 0xFFFFFF, "emoji": "\u2753", "value": 100_000_000},
}

CHARACTERS = [
    {"name": "Coby", "rarity": "E", "image": "https://static.wikia.nocookie.net/onepiece/images/b/b8/Koby_Anime_Post_Timeskip_Infobox.png/revision/latest?cb=20241114130518"},
    {"name": "Helmeppo", "rarity": "E", "image": "https://static.wikia.nocookie.net/onepiece/images/a/a3/Helmeppo_Anime_Post_Timeskip_Infobox.png/revision/latest?cb=20230723204723"},
    {"name": "Alvida", "rarity": "E", "image": "https://static.wikia.nocookie.net/onepiece/images/c/cd/Alvida_Anime_Infobox.png/revision/latest?cb=20221116234952"},
    {"name": "Genzo", "rarity": "E", "image": "https://static.wikia.nocookie.net/onepiece/images/f/fe/Genzo_Anime_Post_Timeskip_Infobox.png/revision/latest?cb=20251109161031"},
    {"name": "Wapol", "rarity": "E", "image": "https://static.wikia.nocookie.net/onepiece/images/3/33/Wapol_Anime_Post_Timeskip_Infobox.png/revision/latest?cb=20190519090502"},
    {"name": "Bellamy", "rarity": "E", "image": "https://static.wikia.nocookie.net/onepiece/images/2/27/Bellamy_Anime_Post_Timeskip_Infobox.png/revision/latest?cb=20230116235201"},
    {"name": "Foxy", "rarity": "E", "image": "https://static.wikia.nocookie.net/onepiece/images/7/7d/Foxy_Anime_Post_Timeskip_Infobox.png/revision/latest?cb=20251102162706"},
    {"name": "Absalom", "rarity": "E", "image": "https://static.wikia.nocookie.net/onepiece/images/5/56/Absalom_Anime_Infobox.png/revision/latest?cb=20230101154942"},
    {"name": "Chess", "rarity": "E", "image": "https://static.wikia.nocookie.net/onepiece/images/6/66/Chess_Anime_Infobox.png/revision/latest?cb=20221003172343"},
    {"name": "Kuroobi", "rarity": "E", "image": "https://static.wikia.nocookie.net/onepiece/images/1/17/Kuroobi_Anime_Infobox.png/revision/latest?cb=20121220133817"},
    {"name": "Bepo", "rarity": "E", "image": "https://static.wikia.nocookie.net/onepiece/images/5/5f/Bepo_Anime_Infobox.png/revision/latest?cb=20231210123641"},
    {"name": "Shachi", "rarity": "E", "image": "https://static.wikia.nocookie.net/onepiece/images/5/5f/Shachi_Anime_Infobox.png/revision/latest?cb=20130425024908"},
    {"name": "Penguin", "rarity": "E", "image": "https://static.wikia.nocookie.net/onepiece/images/8/8a/Penguin_Anime_Infobox.png/revision/latest?cb=20240128193140"},
    {"name": "Nojiko", "rarity": "E", "image": "https://static.wikia.nocookie.net/onepiece/images/2/2d/Nojiko_Anime_Post_Timeskip_Infobox.png/revision/latest?cb=20251109161125"},
    {"name": "Caribou", "rarity": "E", "image": "https://static.wikia.nocookie.net/onepiece/images/7/76/Caribou_Anime_Infobox.png/revision/latest?cb=20221124030700"},
    {"name": "Squard", "rarity": "E", "image": "https://static.wikia.nocookie.net/onepiece/images/b/b8/Squard_Anime_Infobox.png/revision/latest?cb=20140921221031"},
    {"name": "Otama", "rarity": "E", "image": "https://static.wikia.nocookie.net/onepiece/images/6/6d/Kurozumi_Tama_Anime_Infobox.png/revision/latest?cb=20210210051806"},
    {"name": "Tamago", "rarity": "E", "image": "https://static.wikia.nocookie.net/onepiece/images/4/49/Tamago_Anime_Infobox.png/revision/latest?cb=20171126235414"},
    {"name": "Wire", "rarity": "E", "image": "https://static.wikia.nocookie.net/onepiece/images/3/3d/Wire_Anime_Infobox.png/revision/latest?cb=20240731170205"},
    {"name": "Bobbin", "rarity": "E", "image": "https://static.wikia.nocookie.net/onepiece/images/1/1c/Bobbin_Anime_Infobox.png/revision/latest?cb=20171112035640"},
    {"name": "Pappag", "rarity": "E", "image": "https://static.wikia.nocookie.net/onepiece/images/3/32/Pappag_Anime_Post_Timeskip_Infobox.png/revision/latest?cb=20140413064635"},
    {"name": "Camie", "rarity": "E", "image": "https://static.wikia.nocookie.net/onepiece/images/a/af/Camie_Anime_Post_Timeskip_Infobox.png/revision/latest?cb=20130912213617"},

    {"name": "Usopp", "rarity": "D", "image": "https://static.wikia.nocookie.net/onepiece/images/3/35/Usopp_Anime_Post_Timeskip_Infobox.png/revision/latest?cb=20221127233827"},
    {"name": "Nami", "rarity": "D", "image": "https://static.wikia.nocookie.net/onepiece/images/6/68/Nami_Anime_Post_Timeskip_Infobox.png/revision/latest?cb=20260315214841"},
    {"name": "Tony Tony Chopper", "rarity": "D", "image": "https://static.wikia.nocookie.net/onepiece/images/a/af/Tony_Tony_Chopper_Anime_Post_Timeskip_Infobox.png/revision/latest?cb=20240720150824"},
    {"name": "Franky", "rarity": "D", "image": "https://static.wikia.nocookie.net/onepiece/images/8/8c/Franky_Anime_Post_Timeskip_Infobox.png/revision/latest?cb=20241110020715"},
    {"name": "Brook", "rarity": "D", "image": "https://static.wikia.nocookie.net/onepiece/images/4/41/Brook_Anime_Post_Timeskip_Infobox.png/revision/latest?cb=20161016160925"},
    {"name": "Buggy", "rarity": "D", "image": "https://static.wikia.nocookie.net/onepiece/images/f/f7/Buggy_Anime_Post_Timeskip_Infobox.png/revision/latest?cb=20240813025900"},
    {"name": "Krieg", "rarity": "D", "image": "https://static.wikia.nocookie.net/onepiece/images/b/bb/Krieg_Anime_Infobox.png/revision/latest?cb=20230123170612"},
    {"name": "Arlong", "rarity": "D", "image": "https://static.wikia.nocookie.net/onepiece/images/0/01/Arlong_Anime_Infobox.png/revision/latest?cb=20230403145629"},
    {"name": "Vinsmoke Reiju", "rarity": "D", "image": "https://static.wikia.nocookie.net/onepiece/images/a/a3/Vinsmoke_Reiju_Anime_Infobox.png/revision/latest?cb=20231211104854"},
    {"name": "Smoker", "rarity": "D", "image": "https://static.wikia.nocookie.net/onepiece/images/c/c4/Smoker_Anime_Post_Timeskip_Infobox.png/revision/latest?cb=20221101011905"},
    {"name": "Tashigi", "rarity": "D", "image": "https://static.wikia.nocookie.net/onepiece/images/1/1e/Tashigi_Anime_Post_Timeskip_Infobox.png/revision/latest?cb=20251127120726"},
    {"name": "Nefertari Vivi", "rarity": "D", "image": "https://static.wikia.nocookie.net/onepiece/images/0/09/Nefertari_Vivi_Anime_Post_Timeskip_Infobox.png/revision/latest?cb=20190505023647"},
    {"name": "Carrot", "rarity": "D", "image": "https://static.wikia.nocookie.net/onepiece/images/e/e2/Carrot_Anime_Infobox.png/revision/latest?cb=20180826142459"},
    {"name": "Kalifa", "rarity": "D", "image": "https://static.wikia.nocookie.net/onepiece/images/b/b3/Kalifa_Anime_Post_Timeskip_Infobox.png/revision/latest?cb=20240915021516"},
    {"name": "Perona", "rarity": "D", "image": "https://static.wikia.nocookie.net/onepiece/images/4/4a/Perona_Anime_Post_Timeskip_Infobox.png/revision/latest?cb=20221124200121"},
    {"name": "Bartolomeo", "rarity": "D", "image": "https://static.wikia.nocookie.net/onepiece/images/e/eb/Bartolomeo_Anime_Infobox.png/revision/latest?cb=20221027202808"},
    {"name": "Urouge", "rarity": "D", "image": "https://static.wikia.nocookie.net/onepiece/images/f/fb/Urouge_Anime_Infobox.png/revision/latest?cb=20230126223235"},
    {"name": "Scratchmen Apoo", "rarity": "D", "image": "https://static.wikia.nocookie.net/onepiece/images/d/d0/Scratchmen_Apoo_Anime_Post_Timeskip_Infobox.png/revision/latest?cb=20210426143015"},
    {"name": "X Drake", "rarity": "D", "image": "https://static.wikia.nocookie.net/onepiece/images/0/04/X_Drake_Anime_Post_Timeskip_Infobox.png/revision/latest?cb=20200209080003"},
    {"name": "Capone Bege", "rarity": "D", "image": "https://static.wikia.nocookie.net/onepiece/images/9/99/Capone_Bege_Anime_Post_Timeskip_Infobox.png/revision/latest?cb=20160911163015"},
    {"name": "Kinemon", "rarity": "D", "image": "https://static.wikia.nocookie.net/onepiece/images/e/ec/Kin%27emon_Anime_Infobox.png/revision/latest?cb=20191124100115"},
    {"name": "Raizo", "rarity": "D", "image": "https://static.wikia.nocookie.net/onepiece/images/2/23/Raizo_Anime_Infobox.png/revision/latest?cb=20161218152812"},
    {"name": "Kanjuro", "rarity": "D", "image": "https://static.wikia.nocookie.net/onepiece/images/a/ad/Kurozumi_Kanjuro_Anime_Infobox.png/revision/latest?cb=20150503144042"},
    {"name": "Hatchan", "rarity": "D", "image": "https://static.wikia.nocookie.net/onepiece/images/3/3d/Hatchan_Anime_Infobox.png/revision/latest?cb=20221003174139"},
    {"name": "Mr. 3 (Galdino)", "rarity": "D", "image": "https://static.wikia.nocookie.net/onepiece/images/1/13/Galdino_Anime_Infobox.png/revision/latest?cb=20221003165116"},
    {"name": "Miss Goldenweek", "rarity": "D", "image": "https://static.wikia.nocookie.net/onepiece/images/b/b4/Marianne_Anime_Infobox.png/revision/latest?cb=20250115010211"},
    {"name": "Shirahoshi", "rarity": "D", "image": "https://static.wikia.nocookie.net/onepiece/images/c/c1/Shirahoshi_Anime_Infobox.png/revision/latest?cb=20240814220909"},

    {"name": "Roronoa Zoro", "rarity": "C", "image": "https://static.wikia.nocookie.net/onepiece/images/5/52/Roronoa_Zoro_Anime_Post_Timeskip_Infobox.png/revision/latest?cb=20241029161719"},
    {"name": "Sanji", "rarity": "C", "image": "https://static.wikia.nocookie.net/onepiece/images/b/b6/Sanji_Anime_Post_Timeskip_Infobox.png/revision/latest?cb=20240122012744"},
    {"name": "Nico Robin", "rarity": "C", "image": "https://static.wikia.nocookie.net/onepiece/images/b/bc/Nico_Robin_Anime_Post_Timeskip_Infobox.png/revision/latest?cb=20260610121757"},
    {"name": "Eustass Kid", "rarity": "C", "image": "https://static.wikia.nocookie.net/onepiece/images/4/47/Eustass_Kid_Anime_Post_Timeskip_Infobox.png/revision/latest?cb=20240505021859"},
    {"name": "Killer", "rarity": "C", "image": "https://static.wikia.nocookie.net/onepiece/images/7/70/Killer_Anime_Post_Timeskip_Infobox.png/revision/latest?cb=20210815025653"},
    {"name": "Basil Hawkins", "rarity": "C", "image": "https://static.wikia.nocookie.net/onepiece/images/f/f8/Basil_Hawkins_Anime_Post_Timeskip_Infobox.png/revision/latest?cb=20230906163534"},
    {"name": "Jewelry Bonney", "rarity": "C", "image": "https://static.wikia.nocookie.net/onepiece/images/6/62/Jewelry_Bonney_Anime_Post_Timeskip_Infobox.png/revision/latest?cb=20230123001318"},
    {"name": "Yamato", "rarity": "C", "image": "https://static.wikia.nocookie.net/onepiece/images/b/bd/Yamato_Anime_Infobox.png/revision/latest?cb=20260126165014"},
    {"name": "Charlotte Smoothie", "rarity": "C", "image": "https://static.wikia.nocookie.net/onepiece/images/c/c5/Charlotte_Smoothie_Anime_Infobox.png/revision/latest?cb=20180423150946"},
    {"name": "Charlotte Perospero", "rarity": "C", "image": "https://static.wikia.nocookie.net/onepiece/images/7/7e/Charlotte_Perospero_Anime_Infobox.png/revision/latest?cb=20211101122146"},
    {"name": "Charlotte Oven", "rarity": "C", "image": "https://static.wikia.nocookie.net/onepiece/images/f/f0/Charlotte_Oven_Anime_Infobox.png/revision/latest?cb=20181028111159"},
    {"name": "Charlotte Cracker", "rarity": "C", "image": "https://static.wikia.nocookie.net/onepiece/images/6/64/Charlotte_Cracker_Anime_Infobox.png/revision/latest?cb=20170730021804"},
    {"name": "Nekomamushi", "rarity": "C", "image": "https://static.wikia.nocookie.net/onepiece/images/a/a2/Nekomamushi_Anime_Infobox.png/revision/latest?cb=20220419121421"},
    {"name": "Inuarashi", "rarity": "C", "image": "https://static.wikia.nocookie.net/onepiece/images/9/9f/Inuarashi_Anime_Infobox.png/revision/latest?cb=20250516022630"},
    {"name": "Pedro", "rarity": "C", "image": "https://static.wikia.nocookie.net/onepiece/images/c/c8/Pedro_Anime_Infobox.png/revision/latest?cb=20170423080015"},
    {"name": "Vinsmoke Ichiji", "rarity": "C", "image": "https://static.wikia.nocookie.net/onepiece/images/7/7c/Vinsmoke_Ichiji_Anime_Infobox.png/revision/latest?cb=20180625103724"},
    {"name": "Vinsmoke Niji", "rarity": "C", "image": "https://static.wikia.nocookie.net/onepiece/images/d/d6/Vinsmoke_Niji_Anime_Infobox.png/revision/latest?cb=20180618054009"},
    {"name": "Vinsmoke Yonji", "rarity": "C", "image": "https://static.wikia.nocookie.net/onepiece/images/e/e7/Vinsmoke_Yonji_Anime_Infobox.png/revision/latest?cb=20170416175856"},
    {"name": "Vinsmoke Judge", "rarity": "C", "image": "https://static.wikia.nocookie.net/onepiece/images/6/6f/Vinsmoke_Judge_Anime_Infobox.png/revision/latest?cb=20170626124958"},
    {"name": "King Neptune", "rarity": "C", "image": "https://static.wikia.nocookie.net/onepiece/images/4/40/Neptune_Anime_Infobox.png/revision/latest?cb=20131206042454"},

    {"name": "Trafalgar Law", "rarity": "B", "image": "https://static.wikia.nocookie.net/onepiece/images/6/6d/Trafalgar_D_Water_Law_Anime_Post_Timeskip_Infobox.png/revision/latest?cb=20230129022336"},
    {"name": "Donquixote Doflamingo", "rarity": "B", "image": "https://static.wikia.nocookie.net/onepiece/images/7/7e/Donquixote_Doflamingo_Anime_Infobox.png/revision/latest?cb=20231017082245"},
    {"name": "Crocodile", "rarity": "B", "image": "https://static.wikia.nocookie.net/onepiece/images/f/fd/Crocodile_Anime_Infobox.png/revision/latest?cb=20230125235528"},
    {"name": "Boa Hancock", "rarity": "B", "image": "https://static.wikia.nocookie.net/onepiece/images/f/f0/Boa_Hancock_Anime_Infobox.png/revision/latest?cb=20230126022456"},
    {"name": "Bartholomew Kuma", "rarity": "B", "image": "https://static.wikia.nocookie.net/onepiece/images/8/8d/Bartholomew_Kuma_Anime_Infobox.png/revision/latest?cb=20221012030835"},
    {"name": "Jinbe", "rarity": "B", "image": "https://static.wikia.nocookie.net/onepiece/images/8/81/Jinbe_Anime_Infobox.png/revision/latest?cb=20170521201349"},
    {"name": "Gecko Moria", "rarity": "B", "image": "https://static.wikia.nocookie.net/onepiece/images/b/be/Gecko_Moria_Anime_Infobox.png/revision/latest?cb=20181127062446"},
    {"name": "Rob Lucci", "rarity": "B", "image": "https://static.wikia.nocookie.net/onepiece/images/d/d7/Rob_Lucci_Anime_Post_Timeskip_Infobox.png/revision/latest?cb=20230102052113"},
    {"name": "Marco", "rarity": "B", "image": "https://static.wikia.nocookie.net/onepiece/images/4/4a/Polo_Marco_Anime_Post_Timeskip_Infobox.png/revision/latest?cb=20221010015200"},
    {"name": "Portgas D. Ace", "rarity": "B", "image": "https://static.wikia.nocookie.net/onepiece/images/4/4f/Portgas_D._Ace_Anime_Infobox.png/revision/latest?cb=20240629132600"},
    {"name": "Sabo", "rarity": "B", "image": "https://static.wikia.nocookie.net/onepiece/images/c/c2/Sabo_Anime_Infobox.png/revision/latest?cb=20230804035141"},
    {"name": "Sengoku", "rarity": "B", "image": "https://static.wikia.nocookie.net/onepiece/images/2/24/Sengoku_Anime_Post_Timeskip_Infobox.png/revision/latest?cb=20210208064630"},

    {"name": "Kaido", "rarity": "A", "image": "https://static.wikia.nocookie.net/onepiece/images/2/2d/Kaidou_Anime_Infobox.png/revision/latest?cb=20231102015517"},
    {"name": "Whitebeard", "rarity": "A", "image": "https://static.wikia.nocookie.net/onepiece/images/b/b7/Edward_Newgate_Anime_Infobox.png/revision/latest?cb=20220926165737"},
    {"name": "Blackbeard", "rarity": "A", "image": "https://static.wikia.nocookie.net/onepiece/images/f/ff/Marshall_D._Teach_Anime_Post_Timeskip_Infobox.png/revision/latest?cb=20240128044952"},
    {"name": "Big Mom", "rarity": "A", "image": "https://static.wikia.nocookie.net/onepiece/images/d/d8/Charlotte_Linlin_Anime_Infobox.png/revision/latest?cb=20180423150804"},
    {"name": "Shanks", "rarity": "A", "image": "https://static.wikia.nocookie.net/onepiece/images/6/66/Shanks_Anime_Infobox.png/revision/latest?cb=20240829145447"},
    {"name": "Kuzan (Aokiji)", "rarity": "A", "image": "https://static.wikia.nocookie.net/onepiece/images/d/d6/Kuzan_Anime_Post_Timeskip_Infobox.png/revision/latest?cb=20240811021341"},
    {"name": "Sakazuki (Akainu)", "rarity": "A", "image": "https://static.wikia.nocookie.net/onepiece/images/d/d7/Sakazuki_Anime_Post_Timeskip_Infobox.png/revision/latest?cb=20220829052511"},
    {"name": "Borsalino (Kizaru)", "rarity": "A", "image": "https://static.wikia.nocookie.net/onepiece/images/1/14/Borsalino_Anime_Infobox.png/revision/latest?cb=20190603023753"},
    {"name": "Fujitora", "rarity": "A", "image": "https://static.wikia.nocookie.net/onepiece/images/e/e8/Issho_Anime_Infobox.png/revision/latest?cb=20220718140829"},
    {"name": "Dracule Mihawk", "rarity": "A", "image": "https://static.wikia.nocookie.net/onepiece/images/b/bf/Dracule_Mihawk_Anime_Infobox.png/revision/latest?cb=20151222105910"},
    {"name": "Monkey D. Garp", "rarity": "A", "image": "https://static.wikia.nocookie.net/onepiece/images/e/e1/Monkey_D._Garp_Anime_Infobox.png/revision/latest?cb=20230207160645"},
    {"name": "Katakuri", "rarity": "A", "image": "https://static.wikia.nocookie.net/onepiece/images/2/2e/Charlotte_Katakuri_Anime_Infobox.png/revision/latest?cb=20230204155539"},

    {"name": "Gol D. Roger", "rarity": "S", "image": "https://static.wikia.nocookie.net/onepiece/images/2/24/Gol_D._Roger_Anime_Infobox.png/revision/latest?cb=20230612100153"},

    {"name": "Joy Boy", "rarity": "SS", "image": "https://static.wikia.nocookie.net/onepiece/images/5/5a/Joy_Boy_Anime_Infobox.png/revision/latest?cb=20251221044647"},

    {"name": "Monkey D. Luffy (Gear 5)", "rarity": "HDYGT", "image": "https://imgs.search.brave.com/aSCGcJvC0eb-Zomx7ly_QaZn5a6OptUOuKwGFzbRcDk/rs:fit:860:0:0:0/g:ce/aHR0cHM6Ly93YWxs/cGFwZXJjYXZlLmNv/bS93cC93cDEyOTI1/NTc1LmpwZw"},
    {"name": "Imu", "rarity": "HDYGT", "image": "https://static.wikia.nocookie.net/onepiece/images/d/d4/Nerona_Imu_Manga_Infobox.png/revision/latest?cb=20260419150637"},
]

QUEST_POOL = [
    {"id": "roll3", "desc": "Spin 3 times", "type": "roll_count", "target": 3, "reward": 4_000},
    {"id": "roll6", "desc": "Spin 6 times", "type": "roll_count", "target": 6, "reward": 9_000},
    {"id": "rare_plus", "desc": "Pull a D-tier or better", "type": "rarity_at_least", "target": "D", "reward": 6_000},
    {"id": "epic_plus", "desc": "Pull a C-tier or better", "type": "rarity_at_least", "target": "C", "reward": 12_000},
    {"id": "sell1", "desc": "Sell any character", "type": "sell_count", "target": 1, "reward": 4_000},
    {"id": "duel1", "desc": "Win a duel", "type": "duel_win", "target": 1, "reward": 12_000},
]
QUESTS_PER_DAY = 3

RARITY_ORDER = list(RARITIES.keys())

SHOP_ITEMS = {
    "luck": {
        "label": f"2x Luck ({SHOP_LUCK_MINUTES} min)",
        "desc": f"Doubles the odds of every rarity above E for {SHOP_LUCK_MINUTES} minutes.",
        "cost": SHOP_LUCK_COST,
    },
    "key": {
        "label": "1 Key",
        "desc": "Instantly get 1 Key (use with `op refreshspins`).",
        "cost": SHOP_KEY_COST,
    },
    "refill": {
        "label": "Spin Refill",
        "desc": f"Instantly refill your spins to {MAX_SPINS}.",
        "cost": SHOP_REFILL_COST,
    },
    "teamslot": {
        "label": "Extra Team Slot",
        "desc": f"Permanently +1 duel team slot (max {MAX_TEAM_SIZE_CAP}).",
        "cost": SHOP_TEAMSLOT_COST,
    },
    "fastspins": {
        "label": f"Fast Spins ({SHOP_FASTSPINS_COUNT})",
        "desc": f"Your next {SHOP_FASTSPINS_COUNT} spins skip the roll animation — instant pulls!",
        "cost": SHOP_FASTSPINS_COST,
    },
    "autoroll": {
        "label": f"Auto Roll ({SHOP_AUTOROLL_MINUTES} min)",
        "desc": f"Spins auto-roll for {SHOP_AUTOROLL_MINUTES} minutes (no animation, no cooldown). Max {SHOP_AUTOROLL_MAX} min then {SHOP_AUTOROLL_BREAK} min break.",
        "cost": SHOP_AUTOROLL_COST,
    },
}

STAT_BANDS = {
    "E":     {"power": (80, 180),    "health": (160, 360),    "speed": (30, 55)},
    "D":     {"power": (220, 380),   "health": (440, 760),    "speed": (60, 85)},
    "C":     {"power": (420, 620),   "health": (840, 1240),   "speed": (90, 115)},
    "B":     {"power": (660, 920),   "health": (1320, 1840),  "speed": (120, 145)},
    "A":     {"power": (960, 1280),  "health": (1920, 2560),  "speed": (150, 175)},
    "S":     {"power": (1320, 1700), "health": (2640, 3400),  "speed": (180, 205)},
    "SS":    {"power": (1740, 2200), "health": (3480, 4400),  "speed": (210, 235)},
    "HDYGT": {"power": (2300, 3000), "health": (4600, 6000),  "speed": (240, 270)},
}

# =============================================================================
# STORAGE
# =============================================================================
def _load(path: str) -> dict:
    pg_key = os.path.basename(path).replace(".json", "")
    if DATABASE_URL and HAS_PG:
        result = _load_pg(pg_key)
        if result is not None:
            return result
        print(f"[PG] _load_pg returned None for {pg_key} — falling back to file")
    try:
        if not os.path.exists(path):
            return {}
        with open(path, "r", encoding="utf-8") as f:
            raw = f.read()
            if not raw.strip():
                return {}
            return json.loads(raw)
    except (json.JSONDecodeError, OSError, ValueError):
        return {}

def _save(path: str, data: dict) -> None:
    pg_key = os.path.basename(path).replace(".json", "")
    # Always save to PG if available
    if DATABASE_URL and HAS_PG:
        _save_pg(pg_key, data)
    # Always also save to file as backup
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(tmp, path)
    except OSError:
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except OSError:
            pass

def load_data() -> dict:
    return _load(DATA_FILE)

def save_data(data: dict) -> None:
    for k, v in data.items():
        if isinstance(v, dict) and "berries" in v:
            v["berries"] = min(MAX_BERRIES, max(0, int(v.get("berries", 0))))
    _save(DATA_FILE, data)

def cap_berries(user: dict) -> None:
    user["berries"] = min(MAX_BERRIES, max(0, int(user.get("berries", 0))))

def load_auctions() -> dict:
    return _load(AUCTION_FILE)

def save_auctions(data: dict) -> None:
    _save(AUCTION_FILE, data)

def default_user() -> dict:
    return {
        "collection": [],
        "spins": MAX_SPINS,
        "berries": 20_000,
        "keys": 0,
        "team": [],
        "team_slots": DEFAULT_TEAM_SIZE,
        "pity_counter": 0,
        "luck_until_utc": 0,
        "last_daily_utc": 0,
        "quest_date": None,
        "quests": [],
        "signed_up": False,
        "spins_used": 0,
        "reroll_tokens": 0,
        "fruit_ticket": False,
        "fast_spins": 0,
        "autoroll_remaining": 0,
        "autoroll_break_until": 0,
        "luck_date": "",
        "luck_seconds_today": 0,
        "_next_inst_id": 1,
    }

def _migrate_user(user: dict) -> dict:
    if user.get("signed_up") is None:
        user["signed_up"] = True
    if "_next_inst_id" not in user:
        user["_next_inst_id"] = 1

    old_collection = user.get("collection", [])
    if old_collection and isinstance(old_collection[0], str):
        new_collection = []
        inst_id = user["_next_inst_id"]
        for name in old_collection:
            char = character_lookup(name)
            if not char:
                continue
            race_name = random.choice(RACE_NAMES)
            race_mod = RACES[race_name]
            rng = random.Random(name)
            band = STAT_BANDS[char["rarity"]]
            base_power = rng.randint(band["power"][0], band["power"][1])
            base_health = rng.randint(band["health"][0], band["health"][1])
            base_speed = rng.randint(band["speed"][0], band["speed"][1])
            power = round(base_power * race_mod["power"])
            health = round(base_health * race_mod["health"])
            speed = round(base_speed * race_mod["speed"])
            new_collection.append({
                "inst_id": inst_id,
                "character": name,
                "rarity": char["rarity"],
                "race": race_name,
                "fruit": None,
                "power": power,
                "health": health,
                "speed": speed,
            })
            inst_id += 1
        user["collection"] = new_collection
        user["_next_inst_id"] = inst_id

    old_team = user.get("team", [])
    if old_team and isinstance(old_team, list) and old_team and isinstance(old_team[0], str):
        new_team = []
        for name in old_team:
            for inst in user["collection"]:
                if inst["character"] == name and inst["inst_id"] not in new_team:
                    new_team.append(inst["inst_id"])
                    break
        user["team"] = new_team

    return user

def get_user(data: dict, user_id: str) -> dict:
    if user_id not in data:
        data[user_id] = default_user()
    user = data[user_id]
    for key, val in default_user().items():
        if key not in user:
            user[key] = val
    _migrate_user(user)
    return user

# =============================================================================
# HELPERS
# =============================================================================
def rarity_icon(rarity: str) -> str:
    return RARITIES[rarity]["emoji"]

def rarity_at_least(rarity: str, minimum: str) -> bool:
    return RARITY_ORDER.index(rarity) >= RARITY_ORDER.index(minimum)

def roll_race(rarity: str = "C") -> str:
    tier_weights = RARITY_RACE_WEIGHTS.get(rarity, [0.33, 0.34, 0.33])
    chosen_tier = random.choices([1, 2, 3], weights=tier_weights, k=1)[0]
    return random.choice(RACE_TIER_POOLS[chosen_tier])

FRUIT_RARITY_BIAS = {
    "E": {"Common": 0.55, "Uncommon": 0.28, "Rare": 0.12, "Legendary": 0.04, "Mythical": 0.01},
    "D": {"Common": 0.45, "Uncommon": 0.30, "Rare": 0.17, "Legendary": 0.07, "Mythical": 0.01},
    "C": {"Common": 0.35, "Uncommon": 0.30, "Rare": 0.22, "Legendary": 0.10, "Mythical": 0.03},
    "B": {"Common": 0.25, "Uncommon": 0.28, "Rare": 0.27, "Legendary": 0.15, "Mythical": 0.05},
    "A": {"Common": 0.15, "Uncommon": 0.22, "Rare": 0.30, "Legendary": 0.23, "Mythical": 0.10},
    "S": {"Common": 0.08, "Uncommon": 0.15, "Rare": 0.28, "Legendary": 0.30, "Mythical": 0.19},
    "SS": {"Common": 0.03, "Uncommon": 0.10, "Rare": 0.22, "Legendary": 0.30, "Mythical": 0.35},
    "HDYGT": {"Common": 0.01, "Uncommon": 0.04, "Rare": 0.10, "Legendary": 0.25, "Mythical": 0.60},
}

FRUIT_RARITY_ORDER = ["Common", "Uncommon", "Rare", "Legendary", "Mythical"]

def roll_fruit(rarity: str = "C") -> dict:
    if random.random() >= FRUIT_DROP_CHANCE:
        return None
    bias = FRUIT_RARITY_BIAS.get(rarity, FRUIT_RARITY_BIAS["C"])
    fruit_rarity = random.choices(FRUIT_RARITY_ORDER, weights=[bias[r] for r in FRUIT_RARITY_ORDER], k=1)[0]
    pool = [f for f in FRUITS if f["rarity"] == fruit_rarity]
    return random.choice(pool) if pool else random.choice(FRUITS)

RARITY_FRUIT_SCALE = {
    "E": 0.15, "D": 0.30, "C": 0.45, "B": 0.60,
    "A": 0.75, "S": 1.00, "SS": 1.25, "HDYGT": 1.50,
}

def calculate_instance_stats(character: dict, race_name: str, fruit: dict = None) -> dict:
    rarity = character["rarity"]
    band = STAT_BANDS[rarity]
    base_power = random.randint(band["power"][0], band["power"][1])
    base_health = random.randint(band["health"][0], band["health"][1])
    base_speed = random.randint(band["speed"][0], band["speed"][1])
    race_mod = RACES[race_name]
    power = round(base_power * race_mod["power"])
    health = round(base_health * race_mod["health"])
    speed = round(base_speed * race_mod["speed"])
    if fruit:
        scale = RARITY_FRUIT_SCALE[rarity]
        power += round(fruit["power"] * scale)
        health += round(fruit["health"] * scale)
        speed += round(fruit["speed"] * scale)
    return {"power": power, "health": health, "speed": speed}

def roll_character(luck_active: bool = False) -> dict:
    def weight_for(rarity: str) -> float:
        base = RARITIES[rarity]["weight"]
        return base * SHOP_LUCK_MULTIPLIER if (luck_active and rarity != "E") else base
    weights = [weight_for(c["rarity"]) for c in CHARACTERS]
    return random.choices(CHARACTERS, weights=weights, k=1)[0]

def roll_pity_character() -> dict:
    pool = [c for c in CHARACTERS if RARITY_ORDER.index(c["rarity"]) >= RARITY_ORDER.index("A")]
    return random.choice(pool)

def create_instance(character: dict, race_name: str = None, fruit: dict = None) -> dict:
    rarity = character["rarity"]
    race_name = race_name or roll_race(rarity)
    fruit = fruit if fruit is not None else roll_fruit(rarity)
    stats = calculate_instance_stats(character, race_name, fruit)
    return {
        "character": character["name"],
        "rarity": character["rarity"],
        "race": race_name,
        "fruit": fruit,
        "power": stats["power"],
        "health": stats["health"],
        "speed": stats["speed"],
    }

def roll_key_drop() -> int:
    if random.random() < KEY_DROP_CHANCE:
        return random.randint(KEY_DROP_MIN, KEY_DROP_MAX)
    return 0

NICKNAMES = {
    "luffy": "Monkey D. Luffy (Gear 5)",
    "gear 5": "Monkey D. Luffy (Gear 5)",
    "gear5": "Monkey D. Luffy (Gear 5)",
    "zoro": "Roronoa Zoro",
    "sanji": "Sanji",
    "nami": "Nami",
    "ussop": "Usopp",
    "usopp": "Usopp",
    "chopper": "Tony Tony Chopper",
    "robin": "Nico Robin",
    "franky": "Franky",
    "brook": "Brook",
    "jinbe": "Jinbe",
    "jinbei": "Jinbe",
    "law": "Trafalgar Law",
    "trafalgar": "Trafalgar Law",
    "ace": "Portgas D. Ace",
    "sabo": "Sabo",
    "katakuri": "Katakuri",
    "doffy": "Donquixote Doflamingo",
    "doflamingo": "Donquixote Doflamingo",
    "mihawk": "Dracule Mihawk",
    "shanks": "Shanks",
    "buggy": "Buggy",
    "crocodile": "Crocodile",
    "kizaru": "Borsalino (Kizaru)",
    "akainu": "Sakazuki (Akainu)",
    "aokiji": "Kuzan (Aokiji)",
    "fujitora": "Fujitora",
    "garp": "Monkey D. Garp",
    "kaido": "Kaido",
    "big mom": "Big Mom",
    "linlin": "Big Mom",
    "whitebeard": "Whitebeard",
    "blackbeard": "Blackbeard",
    "kuma": "Bartholomew Kuma",
    "lucci": "Rob Lucci",
    "hancock": "Boa Hancock",
    "boa": "Boa Hancock",
    "marco": "Marco",
    "kidd": "Eustass Kid",
    "kid": "Eustass Kid",
    "bege": "Capone Bege",
    "hawkins": "Basil Hawkins",
    "apoo": "Scratchmen Apoo",
    "drake": "X Drake",
    "x drake": "X Drake",
    "smoker": "Smoker",
    "tashigi": "Tashigi",
    "sengoku": "Sengoku",
    "koby": "Coby",
    "coby": "Coby",
    "helmeppo": "Helmeppo",
    "reiju": "Vinsmoke Reiju",
    "ichiji": "Vinsmoke Ichiji",
    "niji": "Vinsmoke Niji",
    "yonji": "Vinsmoke Yonji",
    "judge": "Vinsmoke Judge",
    "carrot": "Carrot",
    "pedro": "Pedro",
    "neptune": "King Neptune",
    "king neptune": "King Neptune",
    "arlong": "Arlong",
    "hody": "Arlong",
    "vivi": "Nefertari Vivi",
    "nefertari": "Nefertari Vivi",
    "yamato": "Yamato",
    "oden": "Kinemon",
    "kinemon": "Kinemon",
    "tama": "Otama",
    "otama": "Otama",
    "perona": "Perona",
    "bonney": "Jewelry Bonney",
    "jewelry": "Jewelry Bonney",
    "killer": "Killer",
    "urouge": "Urouge",
    "beppo": "Bepo",
    "bepo": "Bepo",
    "shirahoshi": "Shirahoshi",
    "goldenweek": "Miss Goldenweek",
    "golden week": "Miss Goldenweek",
    "miss goldenweek": "Miss Goldenweek",
    "galdino": "Mr. 3 (Galdino)",
    "mr 3": "Mr. 3 (Galdino)",
    "mr 3 (galdino)": "Mr. 3 (Galdino)",
    "raizo": "Raizo",
    "kanjuro": "Kanjuro",
    "inuarashi": "Inuarashi",
    "nekomamushi": "Nekomamushi",
    "cracker": "Charlotte Cracker",
    "smoothie": "Charlotte Smoothie",
    "oven": "Charlotte Oven",
    "perospero": "Charlotte Perospero",
    "tamago": "Tamago",
    "bobbin": "Bobbin",
    "kuma": "Bartholomew Kuma",
    "bellamy": "Bellamy",
    "foxy": "Foxy",
    "wapol": "Wapol",
    "krieg": "Krieg",
    "alvida": "Alvida",
    "kuroobi": "Kuroobi",
    "hatchan": "Hatchan",
    "camie": "Camie",
    "pappag": "Pappag",
    "nojiko": "Nojiko",
    "genzo": "Genzo",
    "squard": "Squard",
    "caribou": "Caribou",
    "absalom": "Absalom",
    "moria": "Gecko Moria",
    "gecko": "Gecko Moria",
    "kaku": "Rob Lucci",
    "kalifa": "Kalifa",
    "wire": "Wire",
    "penguin": "Penguin",
    "shachi": "Shachi",
    "chesson": "Chess",
    "chess": "Chess",
    "gomu": "Joy Boy",
    "joyboy": "Joy Boy",
    "roger": "Gol D. Roger",
    "gold roger": "Gol D. Roger",
    "gol d roger": "Gol D. Roger",
    "imu": "Imu",
    "imu sama": "Imu",
    "im": "Imu",
    "barto": "Bartolomeo",
    "bartolomeo": "Bartolomeo",
    "cavendish": "Urouge",
    "kyros": "Kinemon",
    "rebecca": "Nami",
}

def resolve_character_name(query: str):
    """Find a character by exact name, nickname, or partial match. Returns full name or list if ambiguous, or None."""
    q = query.lower().strip()
    # 1. Check nickname map — verify the target exists in CHARACTERS
    if q in NICKNAMES:
        target = NICKNAMES[q]
        if any(c["name"].lower() == target.lower() for c in CHARACTERS):
            return target
    # 2. Exact match
    for c in CHARACTERS:
        if c["name"].lower() == q:
            return c["name"]
    # 3. Partial match
    matches = [c["name"] for c in CHARACTERS if q in c["name"].lower()]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        return matches
    return None

def match_character_name(input_name: str):
    """Resolve user input to a proper character name. Returns (resolved_name, warning_message_or_None)."""
    resolved = resolve_character_name(input_name)
    if resolved is None:
        return input_name, None
    if isinstance(resolved, list):
        return input_name, f"Multiple characters match \"{input_name}\": {', '.join(resolved[:5])}"
    return resolved, None

def character_lookup(name: str):
    name_lower = name.lower()
    for c in CHARACTERS:
        if c["name"].lower() == name_lower:
            return c
    return None

def collection_search(user: dict, query: str):
    """Find matching card instances in user's collection using fuzzy name matching. Returns (instances, resolved_name)."""
    q = query.lower().strip()
    exact = [inst for inst in user["collection"] if inst["character"].lower() == q]
    if exact:
        return exact, exact[0]["character"]
    resolved = resolve_character_name(query)
    if isinstance(resolved, list):
        return [], f"Multiple characters match \"{query}\": {', '.join(resolved[:5])}"
    if resolved:
        fuzzy = [inst for inst in user["collection"] if inst["character"].lower() == resolved.lower()]
        if fuzzy:
            return fuzzy, resolved
    return [], None

TYPE_OVERRIDES = {
    "Coby": "Marine", "Helmeppo": "Marine", "Smoker": "Marine", "Tashigi": "Marine",
    "Sengoku": "Marine", "Monkey D. Garp": "Marine", "Fujitora": "Marine",
    "Borsalino (Kizaru)": "Marine", "Sakazuki (Akainu)": "Marine", "Kuzan (Aokiji)": "Marine",
    "Sabo": "Revolutionary",
    "Roronoa Zoro": "Swordsman", "Dracule Mihawk": "Swordsman", "Killer": "Swordsman",
}

def ensure_todays_quests(user: dict) -> None:
    today = datetime.utcnow().date().isoformat()
    if user["quest_date"] != today:
        user["quest_date"] = today
        picks = random.sample(QUEST_POOL, k=min(QUESTS_PER_DAY, len(QUEST_POOL)))
        user["quests"] = [{"id": q["id"], "progress": 0, "claimed": False} for q in picks]

def find_active_quest(user: dict, quest_id: str):
    for q in user["quests"]:
        if q["id"] == quest_id:
            return q
    return None

def bump_quest_progress(user: dict, qtype: str, value=None) -> None:
    ensure_todays_quests(user)
    for entry in user["quests"]:
        if entry["claimed"]:
            continue
        template = next((q for q in QUEST_POOL if q["id"] == entry["id"]), None)
        if template is None or template["type"] != qtype:
            continue
        if qtype == "rarity_at_least":
            if rarity_at_least(value, template["target"]):
                entry["progress"] = 1
        else:
            entry["progress"] = min(template["target"], entry["progress"] + 1)

def fmt_duration(seconds: float) -> str:
    seconds = max(0, int(seconds))
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m {secs}s"
    return f"{secs}s"

def branded_embed(title: str, description: str = "", color: int = BRAND_COLOR) -> discord.Embed:
    embed = discord.Embed(
        title=title,
        description=description,
        color=color,
    )
    embed.set_footer(text=FOOTER_TEXT)
    return embed

def instance_total_stat(inst: dict) -> int:
    return inst.get("power", 0) + inst.get("health", 0) + inst.get("speed", 0)

def instance_payout(inst: dict, rarity: str = None) -> int:
    if rarity is None:
        rarity = inst.get("rarity", "C")
    base = RARITIES[rarity]["value"]
    race = RACES.get(inst.get("race", "Human"), RACES["Human"]).get("value", 1.0)
    fruit = inst.get("fruit")
    fruit_val = FRUIT_RARITIES.get(fruit.get("rarity", "Common"), {}).get("value", 0.75) if fruit else 0.75
    return round(base * DUPLICATE_CONVERT_RATE * race * fruit_val)

# ── Max stat reference per rarity for stat bars ──
_STAT_MAX = {
    r: {s: bands[s][1] for s in ("power", "health", "speed")}
    for r, bands in STAT_BANDS.items()
}

def _stat_bar(val: int, max_val: int, length: int = 10) -> str:
    filled = round((val / max(max_val, 1)) * length)
    filled = max(0, min(length, filled))
    return "▰" * filled + "▱" * (length - filled)

def _compact_num(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(n)

def _compact_stat_line(inst: dict) -> str:
    rar = inst.get("rarity", "C")
    race_data = RACES.get(inst.get("race", "Human"), RACES["Human"])
    fruit = inst.get("fruit")
    fruit_emoji = FRUIT_RARITIES.get(fruit["rarity"], {}).get("emoji", "") if fruit else ""
    maxes = _STAT_MAX.get(rar, _STAT_MAX["C"])
    p, h, s = inst.get("power", 0), inst.get("health", 0), inst.get("speed", 0)
    total = p + h + s
    bar5 = lambda v, m: _stat_bar(v, m, 5)
    return (
        f"`#{inst.get('inst_id', 0):>3}` {race_data['emoji']} {fruit_emoji}  "
        f"⚔{bar5(p, maxes['power'])}  ❤{bar5(h, maxes['health'])}  "
        f"💨{bar5(s, maxes['speed'])}  📊{_compact_num(total)}"
    )


def build_card_embed(inst: dict, ctx_or_author, extra: dict = None) -> discord.Embed:
    if isinstance(ctx_or_author, commands.Context):
        display_name = ctx_or_author.author.display_name
    else:
        display_name = getattr(ctx_or_author, "display_name", "Unknown")

    char = character_lookup(inst["character"])
    rarity = inst["rarity"]
    rarity_info = RARITIES[rarity]
    race_data = RACES.get(inst["race"], RACES["Human"])
    fruit = inst.get("fruit")
    total = instance_total_stat(inst)

    embed = discord.Embed(
        title=f"{rarity_info['emoji']}  {inst['character']}",
        color=rarity_info["color"],
    )

    embed.set_author(name=f"{rarity}  —  {inst['race']}")

    if char and char.get("image"):
        embed.set_image(url=char["image"])

    # ── Card body ──
    card = ""

    # Race / Fruit line
    card += f"{race_data['emoji']}  {inst['race']}"
    if fruit:
        fru = FRUIT_RARITIES[fruit["rarity"]]
        card += f"   ┆   {fru['emoji']}  {fruit['name']}"
        card += f"\n`{fruit['type']}`"
    else:
        card += "\n`No Devil Fruit`"

    card += "\n\n"

    # Stat bars
    maxes = _STAT_MAX.get(rarity, _STAT_MAX["C"])
    bars = [
        f"⚔ **Power**  `{inst['power']:>5,}`  {_stat_bar(inst['power'], maxes['power'])}",
        f"❤ **Health** `{inst['health']:>5,}`  {_stat_bar(inst['health'], maxes['health'])}",
        f"💨 **Speed**  `{inst['speed']:>5,}`  {_stat_bar(inst['speed'], maxes['speed'])}",
    ]
    card += "\n".join(bars)
    card += f"\n━━━━━━━━━━━━━━━━━━━━━━━━━"
    card += f"\n📊 **TOTAL**  `{total:>6,}`  {_stat_bar(total, sum(maxes.values()))}"

    # Race bonus footnote
    card += f"\n\n*{race_data['emoji']} {inst['race']}: {race_data['desc']}*"
    if fruit:
        scale = RARITY_FRUIT_SCALE[rarity]
        fr = FRUIT_RARITIES.get(fruit.get("rarity", "Common"), {})
        card += f"\n*{fr.get('emoji', '')} Fruit effect scaled x{scale:.2f} by {rarity} tier*"

    embed.description = card

    # ── Extra event fields ──
    if extra:
        if extra.get("pity_triggered"):
            embed.add_field(name="\u26a1 Pity Activated", value=f"**{PITY_THRESHOLD}** spins without an A+ — guaranteed pull!", inline=False)
        if extra.get("luck_active"):
            embed.add_field(name="\U0001f340 2x Luck Active", value="Better odds are in effect!", inline=False)
        if extra.get("hdygt"):
            embed.add_field(name="\U0001f451 GODLY PULL", value="**1 in 1,000,000** \u2014 worth **100,000,000 Beli** \u2014 screenshot this.", inline=False)
        if extra.get("duplicate"):
            embed.add_field(name="\u267b\ufe0f Duplicate", value=f"Keep the card or convert for **{extra.get('payout', 0):,} Beli**?", inline=False)
        if extra.get("keys_found"):
            embed.add_field(name="\U0001f511 Key Drop", value=f"+{extra['keys_found']} Key (**{extra['keys']}** total)", inline=False)
        if extra.get("bonus_beli"):
            embed.add_field(name="\U0001f4b0 Bonus Beli", value=f"+{extra['bonus_beli']:,}", inline=False)

    footer_text = FOOTER_TEXT
    if extra:
        footer_text = (
            f"{display_name}  \u2022  "
            f"Spins {extra.get('spins', '?')}/{MAX_SPINS}  \u2022  "
            f"Pity {extra.get('pity', 0)}/{PITY_THRESHOLD}"
        )
    embed.set_footer(text=footer_text)
    return embed

# =============================================================================
# DUEL SYSTEM (challenge -> pick -> battle)
# =============================================================================
active_duels = {}
DUEL_CHALLENGE_TIMEOUT = 60
DUEL_BOT_REWARD_MULTIPLIER = 0.5
DUEL_MAX_WAGER = 10_000_000


def hp_bar(current: int, max_hp: int, length: int = 8) -> str:
    filled = round((current / max(1, max_hp)) * length)
    filled = max(0, min(length, filled))
    return "\u2588" * filled + "\u2592" * (length - filled)


class DuelState:
    # State for challenge -> pick -> battle flow
    def __init__(self, ctx, opponent, wager):
        self.ctx = ctx
        self.opponent = opponent
        self.wager = wager
        self.channel = ctx.channel
        self.finished = False
        self.is_bot = False
        self.challenger_char = None
        self.opponent_char = None
        self.attacker_id = None
        self.winner_id = None
        self.message = None


class DuelChallengeView(discord.ui.View):
    def __init__(self, ctx, opponent, wager):
        super().__init__(timeout=DUEL_CHALLENGE_TIMEOUT)
        self.ctx = ctx
        self.opponent = opponent
        self.wager = wager
        self.accepted = False

    @discord.ui.button(label="\u2705 Accept", style=discord.ButtonStyle.success)
    async def accept_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.opponent.id:
            await interaction.response.send_message("Only the challenged player can accept.", ephemeral=True)
            return
        self.accepted = True
        self.stop()

    @discord.ui.button(label="\u274c Decline", style=discord.ButtonStyle.danger)
    async def decline_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.opponent.id:
            await interaction.response.send_message("Only the challenged player can decline.", ephemeral=True)
            return
        self.stop()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id not in (self.ctx.author.id, self.opponent.id):
            await interaction.response.send_message("Not your duel!", ephemeral=True)
            return False
        return True

    async def on_timeout(self):
        self.stop()


class BotOptionView(discord.ui.View):
    def __init__(self, ctx, opponent):
        super().__init__(timeout=30)
        self.ctx = ctx
        self.opponent = opponent
        self.bot_mode = False

    @discord.ui.button(label="\U0001f916 Fight Bot", style=discord.ButtonStyle.secondary)
    async def bot_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.ctx.author.id:
            await interaction.response.send_message("Not your match!", ephemeral=True)
            return
        self.bot_mode = True
        self.stop()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.ctx.author.id:
            await interaction.response.send_message("Not your match!", ephemeral=True)
            return False
        return True

    async def on_timeout(self):
        self.stop()


class TeamPickerView(discord.ui.View):
    def __init__(self, state, challenger_team, opp_team, is_bot=False):
        super().__init__(timeout=30)
        self.state = state
        self.is_bot = is_bot
        self.challenger_pick = None
        self.opponent_pick = None

        opts = []
        for inst in challenger_team:
            char = character_lookup(inst["character"])
            r = char["rarity"] if char else "C"
            total = instance_total_stat(inst)
            opts.append(discord.SelectOption(
                label=f"{inst['character']}  [{total:,}]",
                value=str(inst["inst_id"]),
                emoji=RARITIES[r]["emoji"],
                description=f"{inst['race']} HP:{inst['health']:,} PW:{inst['power']:,}",
            ))
        if opts:
            s = discord.ui.Select(placeholder="Pick your fighter...", options=opts, row=0)
            async def _chall_cb(i: discord.Interaction):
                if i.user.id != self.state.ctx.author.id:
                    await i.response.send_message("Not your pick!", ephemeral=True)
                    return
                self.challenger_pick = int(s.values[0])
                s.disabled = True
                await i.response.defer()
                self._check_done()
            s.callback = _chall_cb
            self.add_item(s)

        if not is_bot:
            opts2 = []
            for inst in opp_team:
                char = character_lookup(inst["character"])
                r = char["rarity"] if char else "C"
                total = instance_total_stat(inst)
                opts2.append(discord.SelectOption(
                    label=f"{inst['character']}  [{total:,}]",
                    value=str(inst["inst_id"]),
                    emoji=RARITIES[r]["emoji"],
                    description=f"{inst['race']} HP:{inst['health']:,} PW:{inst['power']:,}",
                ))
            if opts2:
                s2 = discord.ui.Select(placeholder="Pick your fighter...", options=opts2, row=1)
                async def _opp_cb(i: discord.Interaction):
                    if not self.is_bot and i.user.id != self.state.opponent.id:
                        await i.response.send_message("Not your pick!", ephemeral=True)
                        return
                    self.opponent_pick = int(s2.values[0])
                    s2.disabled = True
                    await i.response.defer()
                    self._check_done()
                s2.callback = _opp_cb
                self.add_item(s2)

    def _check_done(self):
        if self.challenger_pick is not None and (self.is_bot or self.opponent_pick is not None):
            self.stop()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if self.is_bot and interaction.user.id != self.state.ctx.author.id:
            await interaction.response.send_message("Not your match!", ephemeral=True)
            return False
        if interaction.user.id not in (self.state.ctx.author.id, self.state.opponent.id):
            await interaction.response.send_message("Not your match!", ephemeral=True)
            return False
        return True

    async def on_timeout(self):
        self.stop()


def get_duel_team(user: dict) -> list:
    ids = user["team"] if user["team"] else []
    if ids:
        result = []
        for inst_id in ids:
            for inst in user["collection"]:
                if inst["inst_id"] == inst_id:
                    result.append(inst)
                    break
        return result[:user.get("team_slots", DEFAULT_TEAM_SIZE)]
    sorted_insts = sorted(
        user["collection"],
        key=lambda i: instance_total_stat(i),
        reverse=True,
    )
    return sorted_insts[:user.get("team_slots", DEFAULT_TEAM_SIZE)]

# =============================================================================
# BOT
# =============================================================================
intents = discord.Intents.default()
intents.message_content = True

def get_prefix(bot_: commands.Bot, message: discord.Message):
    return commands.when_mentioned_or("op ", "OP ", "Op ")(bot_, message)

bot = commands.Bot(command_prefix=get_prefix, intents=intents, help_command=None, case_insensitive=True)

_auction_id_counter = itertools.count(1)

# -----------------------------------------------------------------------
# Signup check (runs before every command)
# -----------------------------------------------------------------------
def repair_user_data(user: dict) -> list:
    """Fix common data corruption in a user dict in-place. Returns list of fixes."""
    fixes = []
    if not isinstance(user, dict):
        return fixes

    # 1. ensure all top-level fields exist with valid types
    EXPECTED_FIELDS = {
        "spins": ((int, float), MAX_SPINS),
        "berries": ((int, float), 0),
        "keys": ((int, float), 0),
        "pity_counter": ((int, float), 0),
        "spins_used": ((int, float), 0),
        "reroll_tokens": ((int, float), 0),
        "fast_spins": ((int, float), 0),
        "autoroll_remaining": ((int, float), 0),
        "autoroll_break_until": ((int, float), 0),
        "fruit_ticket": (bool, False),
        "team_slots": ((int, float), DEFAULT_TEAM_SIZE),
        "luck_until_utc": ((int, float), 0),
        "last_daily_utc": ((int, float), 0),
        "luck_date": (str, ""),
        "luck_seconds_today": ((int, float), 0),
        "_next_inst_id": ((int, float), 1),
        "signed_up": (bool, True),
    }
    for field, (typ, default) in EXPECTED_FIELDS.items():
        if field not in user or user[field] is None:
            user[field] = default
            fixes.append(f"added missing '{field}'")
        elif not isinstance(user[field], typ):
            try:
                if isinstance(typ, tuple):
                    for t in typ:
                        if isinstance(user[field], t):
                            break
                    else:
                        user[field] = default
                else:
                    user[field] = typ(user[field])
                fixes.append(f"fixed '{field}' type ({type(user[field]).__name__})")
            except (ValueError, TypeError):
                user[field] = default
                fixes.append(f"reset '{field}' (unconvertable {type(user[field]).__name__})")

    # 2. ensure collection/team are lists (handled separately to avoid type coercion)
    for coll_key in ("collection", "team"):
        val = user.get(coll_key)
        if not isinstance(val, list):
            if isinstance(val, (list, tuple)):
                user[coll_key] = list(val)
            else:
                user[coll_key] = []
            fixes.append(f"fixed '{coll_key}' (was {type(val).__name__})")

    # 3. validate every card
    valid_races = set(RACES.keys())
    valid_collection = []
    valid_ids = set()
    next_id = user.get("_next_inst_id", 1)

    for idx, inst in enumerate(user["collection"]):
        card_fixes = []

        if not isinstance(inst, dict):
            fixes.append(f"removed card #{idx} (was {type(inst).__name__})")
            continue

        name = inst.get("character", f"#{idx}")
        cfix = []

        # character
        if "character" not in inst or inst["character"] is None:
            inst["character"] = "Unknown"
            cfix.append("character")
        # rarity
        if "rarity" not in inst or inst["rarity"] is None or inst["rarity"] not in RARITIES:
            inst["rarity"] = "C"
            cfix.append("rarity")
        # race
        if "race" not in inst or inst["race"] is None or inst["race"] not in valid_races:
            inst["race"] = "Human"
            cfix.append("race")
        # fruit
        if "fruit" not in inst or inst["fruit"] is None:
            inst["fruit"] = None
        elif isinstance(inst["fruit"], dict):
            if "rarity" not in inst["fruit"] or inst["fruit"]["rarity"] not in FRUIT_RARITIES:
                inst["fruit"]["rarity"] = "Common"
                cfix.append("fruit.rarity")
            if "name" not in inst["fruit"]:
                inst["fruit"]["name"] = "Unknown Fruit"
                cfix.append("fruit.name")
        else:
            inst["fruit"] = None
            cfix.append("fruit (was not dict)")
        # stats
        for stat in ("power", "health", "speed"):
            val = inst.get(stat)
            if val is None or not isinstance(val, (int, float)):
                inst[stat] = 0
                cfix.append(stat)
        # inst_id
        iid = inst.get("inst_id")
        if iid is None or not isinstance(iid, int):
            inst["inst_id"] = next_id
            next_id += 1
            cfix.append("inst_id")

        valid_collection.append(inst)
        valid_ids.add(inst["inst_id"])
        if cfix:
            fixes.append(f"fixed '{name}': {', '.join(cfix)}")

    user["collection"] = valid_collection
    if next_id > user.get("_next_inst_id", 1):
        user["_next_inst_id"] = next_id

    # 4. prune team refs
    user["team"] = [tid for tid in user.get("team", []) if tid in valid_ids]

    # 5. clamp balances
    user["berries"] = max(0, int(user.get("berries", 0)))
    user["spins"] = max(0, min(MAX_SPINS, int(user.get("spins", 0))))
    user["keys"] = max(0, int(user.get("keys", 0)))
    user["pity_counter"] = max(0, int(user.get("pity_counter", 0)))
    user["spins_used"] = max(0, int(user.get("spins_used", 0)))
    user["reroll_tokens"] = max(0, int(user.get("reroll_tokens", 0)))
    user["fast_spins"] = max(0, int(user.get("fast_spins", 0)))
    user["autoroll_remaining"] = max(0, int(user.get("autoroll_remaining", 0)))
    user["autoroll_break_until"] = max(0, float(user.get("autoroll_break_until", 0)))
    user["team_slots"] = max(1, min(MAX_TEAM_SIZE_CAP, int(user.get("team_slots", DEFAULT_TEAM_SIZE))))
    user["luck_seconds_today"] = max(0, min(7200, int(user.get("luck_seconds_today", 0))))
    nid = user.get("_next_inst_id", 1)
    user["_next_inst_id"] = max(1, int(nid)) if isinstance(nid, (int, float)) else 1

    return fixes

def _sanitize_user(user: dict) -> None:
    """Guard against data corruption — clamp all balances, prune invalid refs."""
    repair_user_data(user)

class SignupView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=60)

    @discord.ui.button(label="Sign Up", style=discord.ButtonStyle.success, emoji="\U0001f3f4\u200d\u2620\ufe0f")
    async def signup_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        data = load_data()
        user = get_user(data, str(interaction.user.id))
        if user["signed_up"]:
            await interaction.response.send_message("You're already signed up!", ephemeral=True)
            return
        user["signed_up"] = True
        user["spins"] = MAX_SPINS
        user["berries"] = 20_000
        save_data(data)
        embed = discord.Embed(
            title="\U0001f3f4\u200d\u2620\ufe0f Welcome to the Grand Line!",
            description=(
                f"{interaction.user.mention}, you're now a pirate!\n\n"
                f"\U0001f3b2 **{MAX_SPINS} free spins**\n"
                f"\U0001f4b0 **20,000 Beli**\n\n"
                "Hit **\U0001f3b2 Spin** below to pull your first character!"
            ),
            color=0xFFD700,
        )
        embed.set_footer(text=FOOTER_TEXT)
        view = WelcomeView()
        await interaction.response.send_message(embed=embed, view=view)

class WelcomeView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=60)

    @discord.ui.button(label="\U0001f3b2 Spin Now", style=discord.ButtonStyle.primary, emoji="\U0001f3b2")
    async def spin_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        ctx = await bot.get_context(interaction.message)
        ctx.author = interaction.user
        await spin(ctx)

    @discord.ui.button(label="\U0001f4dc Commands", style=discord.ButtonStyle.secondary, emoji="\U0001f4dc")
    async def help_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        ctx = await bot.get_context(interaction.message)
        ctx.author = interaction.user
        await help_command(ctx)

@bot.before_invoke
async def ensure_signed_up(ctx: commands.Context):
    if ctx.command.name in ("signup", "help", "invite", "status", "odds", "fixdb", "codes", "shop", "characters", "fixmycards", "resetuser", "restart", "save", "promocode"):
        return
    data = load_data()
    user = data.get(str(ctx.author.id))
    if not user or not user.get("signed_up"):
        embed = discord.Embed(
            title="\u26a0\ufe0f Not Signed Up",
            description="You need to sign up before using commands!\n\nTap the button below or type **`op signup`** to start your adventure \u2014 free spins & Beli await.",
            color=0xFF5722,
        )
        embed.set_footer(text=FOOTER_TEXT)
        await ctx.send(embed=embed, view=SignupView())
        raise commands.CommandError("User not signed up")
    try:
        _sanitize_user(user)
    except Exception as e:
        print(f"[SANITIZE ERROR] {ctx.author.id}: {e}")

# -----------------------------------------------------------------------
# on_ready
# -----------------------------------------------------------------------
@bot.event
async def on_ready():
    data = load_auctions()
    if data.get("_next_id"):
        global _auction_id_counter
        _auction_id_counter = itertools.count(data["_next_id"])
    if not auction_watcher.is_running():
        auction_watcher.start()
    if not auto_save.is_running():
        auto_save.start()
    pg_status = "[PostgreSQL ACTIVE]" if DATABASE_URL and HAS_PG and _pg_connect() else "[File-based storage]"
    print(f"Logged in as {bot.user} \u2014 ready to roll! {pg_status}")

@bot.event
async def on_command_error(ctx: commands.Context, error: commands.CommandError):
    if isinstance(error, commands.CommandOnCooldown):
        await ctx.send(f"\u23f3 Slow down! Try again in {error.retry_after:.1f}s.")
        return
    if isinstance(error, commands.CommandNotFound):
        return
    if isinstance(error, commands.MemberNotFound):
        await ctx.send("\u26a0\ufe0f Member not found.")
        return
    if isinstance(error, commands.BadArgument):
        await ctx.send("\u26a0\ufe0f Bad argument. Check `op help` for correct usage.")
        return
    msg = str(error)
    if "not signed up" in msg.lower():
        return
    tb = "".join(traceback.format_exception(type(error), error, error.__traceback__))
    short = tb[-1800:] if len(tb) > 1800 else tb
    print(f"[ERROR] {ctx.author} ({ctx.author.id}): {ctx.message.content}")
    print(tb)
    try:
        await ctx.send(embed=branded_embed(
            "\u26a0\ufe0f Command Error",
            f"```\n{short[:1900]}\n```\nSend this to the bot owner to fix it!",
            color=0xFF5722,
        ))
    except Exception:
        pass

# -----------------------------------------------------------------------
# op signup
# -----------------------------------------------------------------------
@bot.command(name="signup")
@commands.cooldown(1, 4, commands.BucketType.user)
async def signup(ctx: commands.Context):
    """Sign up to start playing!"""
    data = load_data()
    user = get_user(data, str(ctx.author.id))
    if user["signed_up"]:
        embed = discord.Embed(
            title="\u2705 Already Signed Up",
            description=f"{ctx.author.mention}, you're already in the crew!",
            color=0x4CAF50,
        )
        embed.set_footer(text=FOOTER_TEXT)
        await ctx.send(embed=embed, view=WelcomeView())
        return
    embed = discord.Embed(
        title="\U0001f3f4\u200d\u2620\ufe0f Join Your Crew",
        description=(
            "Welcome to **OP Bot** \u2014 the ultimate One Piece gacha experience!\n\n"
            "Collect your favourite characters, unlock Devil Fruits, master rare races, "
            "and duel other players to become the Pirate King.\n\n"
            "Tap the button below or type **`op signup`** to get started!"
        ),
        color=0xFFD700,
    )
    embed.add_field(
        name="\U0001f3c6 What You Get",
        value=f"\U0001f3b2 **{MAX_SPINS}** Free Spins\n\U0001f4b0 **20,000** Beli\n\U0001f3af Instant Access to All Commands",
        inline=False,
    )
    embed.set_footer(text=FOOTER_TEXT)
    await ctx.send(embed=embed, view=SignupView())

# -----------------------------------------------------------------------
# op invite
# -----------------------------------------------------------------------
@bot.command(name="invite")
@commands.cooldown(1, 4, commands.BucketType.user)
async def invite(ctx: commands.Context):
    embed = discord.Embed(
        title="\U0001f4ec Invite OP Bot",
        description=(
            "Invite this bot to your own server:\n"
            "https://discord.com/api/oauth2/authorize?client_id=1329796524888293376&permissions=274877991936&scope=bot%20applications.commands"
        ),
        color=BRAND_COLOR,
    )
    embed.set_footer(text=FOOTER_TEXT)
    await ctx.send(embed=embed)

# -----------------------------------------------------------------------
# op promocode — owner-only GUI to create promo codes
# -----------------------------------------------------------------------
REWARD_TYPES = {
    "spins": {"label": "Spins", "emoji": "\U0001f3b2", "field": "spins", "default": 10},
    "berries": {"label": "Beli", "emoji": "\U0001f4b0", "field": "berries", "default": 50000},
    "keys": {"label": "Keys", "emoji": "\U0001f511", "field": "keys", "default": 3},
}

class PromoModal(discord.ui.Modal, title="Create Promo Code"):
    def __init__(self, reward_type: str, scope: str):
        super().__init__()
        self.reward_type = reward_type
        self.scope = scope
        rt = REWARD_TYPES[reward_type]
        self.code_name = discord.ui.TextInput(label="Code Name", placeholder="e.g. summer2026", max_length=30)
        self.reward_amount = discord.ui.TextInput(
            label=f"Amount ({rt['label']})",
            placeholder=f"e.g. {rt['default']}",
            max_length=7,
        )
        self.add_item(self.code_name)
        self.add_item(self.reward_amount)

    async def on_submit(self, interaction: discord.Interaction):
        name = self.code_name.value.lower().strip()
        if not name:
            await interaction.response.send_message("\u26a0\ufe0f Code can't be empty.", ephemeral=True)
            return
        try:
            amount = int(self.reward_amount.value.strip())
            if amount < 1 or amount > 999999:
                raise ValueError
        except ValueError:
            await interaction.response.send_message("\u26a0\ufe0f Amount must be a number between 1-999999.", ephemeral=True)
            return
        data = load_data()
        promos = data.setdefault("_promo_codes", {})
        promos[name] = {"type": self.reward_type, "amount": amount, "scope": self.scope}
        data["_promo_codes"] = promos
        save_data(data)
        rt = REWARD_TYPES[self.reward_type]
        scope_label = "Public (everyone once)" if self.scope == "public" else "1 Player (first claimer)"
        await interaction.response.send_message(embed=branded_embed(
            "\u2705 Promo Code Created",
            f"**{name}** \u2014 {rt['emoji']} **{amount:,}** {rt['label']}  \u2022  {scope_label}\nTap the **\U0001f3b5 Redeem** button to claim it!",
            color=0x4CAF50,
        ))

class PromoCreateView(discord.ui.View):
    def __init__(self, owner_id: int):
        super().__init__(timeout=120)
        self.owner_id = owner_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("Only the bot owner can use this.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Create Promo Code", style=discord.ButtonStyle.success, emoji="\U0001f3b5")
    async def create_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        opts = [
            discord.SelectOption(label="Spins", value="spins", emoji="\U0001f3b2", description="Free spins for redeemer"),
            discord.SelectOption(label="Beli", value="berries", emoji="\U0001f4b0", description="In-game currency"),
            discord.SelectOption(label="Keys", value="keys", emoji="\U0001f511", description="Key items"),
        ]
        select = discord.ui.Select(placeholder="Choose reward type...", options=opts)
        async def rt_callback(sel_interaction: discord.Interaction):
            if sel_interaction.user.id != self.owner_id:
                await sel_interaction.response.send_message("Not your panel.", ephemeral=True)
                return
            scope_opts = [
                discord.SelectOption(label="Public (everyone once)", value="public", emoji="\U0001f30d", description="Every player can use it once"),
                discord.SelectOption(label="1 Player (first claimer)", value="single", emoji="\U0001f464", description="Only the first person to claim it gets it"),
            ]
            scope_select = discord.ui.Select(placeholder="Choose code scope...", options=scope_opts)
            async def scope_callback(sc_interaction: discord.Interaction):
                if sc_interaction.user.id != self.owner_id:
                    await sc_interaction.response.send_message("Not your panel.", ephemeral=True)
                    return
                modal = PromoModal(select.values[0], scope_select.values[0])
                await sc_interaction.response.send_modal(modal)
            scope_select.callback = scope_callback
            scope_view = discord.ui.View(timeout=60)
            scope_view.add_item(scope_select)
            await sel_interaction.response.edit_original_response(content="Pick who can use this code:", view=scope_view)
        select.callback = rt_callback
        view = discord.ui.View(timeout=60)
        view.add_item(select)
        await interaction.response.send_message("Pick what the code gives:", view=view, ephemeral=True)

    @discord.ui.button(label="Delete Code", style=discord.ButtonStyle.danger, emoji="\u274c")
    async def delete_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        data = load_data()
        promos = data.get("_promo_codes", {})
        if not promos:
            await interaction.response.send_message("No promo codes available to delete.", ephemeral=True)
            return
        options = []
        for k, v in promos.items():
            rt = REWARD_TYPES.get(v.get("type", "spins"))
            scope_label = "PUB" if v.get("scope") == "public" else "1P"
            label = f"{k} [{scope_label}] \u2014 {v.get('amount', 0)} {rt['label'] if rt else ''}"
            options.append(discord.SelectOption(label=label[:100], value=k))
        select = discord.ui.Select(placeholder="Pick a code to delete...", options=options[:25])
        async def del_callback(sel_interaction: discord.Interaction):
            if sel_interaction.user.id != self.owner_id:
                await sel_interaction.response.send_message("Not your panel.", ephemeral=True)
                return
            code = select.values[0]
            data = load_data()
            if code in data.get("_promo_codes", {}):
                del data["_promo_codes"][code]
                save_data(data)
            await sel_interaction.response.send_message(f"\u2705 Deleted promo code **{code}**.", ephemeral=True)
        select.callback = del_callback
        view = discord.ui.View(timeout=60)
        view.add_item(select)
        await interaction.response.send_message("Select a promo code to delete:", view=view, ephemeral=True)

@bot.command(name="promocode")
async def promocode_cmd(ctx: commands.Context):
    owner = BOT_OWNER_ID or getattr(bot, "owner_id", None)
    if not owner or ctx.author.id != owner:
        await ctx.send("\u26a0\ufe0f Only the bot owner can use this.")
        return
    data = load_data()
    promos = data.get("_promo_codes", {})
    lines = []
    for k, v in promos.items():
        rt = REWARD_TYPES.get(v.get("type", "spins"))
        emoji = rt["emoji"] if rt else "\U0001f3b5"
        label = rt["label"] if rt else v.get("type", "?")
        scope_label = "\U0001f30d Public" if v.get("scope") == "public" else "\U0001f464 Single"
        lines.append(f"`{k}` {scope_label} \u2014 {emoji} **{v.get('amount', 0):,}** {label}")
    desc = "\n".join(lines) if lines else "No promo codes yet."
    embed = branded_embed("\U0001f3b5 Promo Codes", desc, color=0x9C27B0)
    await ctx.send(embed=embed, view=PromoCreateView(ctx.author.id))

# -----------------------------------------------------------------------
# op redeem — claim a promo code (button + modal)
# -----------------------------------------------------------------------
class RedeemModal(discord.ui.Modal, title="Redeem Promo Code"):
    def __init__(self, default_code: str = ""):
        super().__init__()
        self.code_input = discord.ui.TextInput(
            label="Enter your code",
            placeholder="e.g. summer2026",
            max_length=30,
            default=default_code or None,
        )
        self.add_item(self.code_input)

    async def on_submit(self, interaction: discord.Interaction):
        code = self.code_input.value.lower().strip()
        data = load_data()
        promos = data.get("_promo_codes", {})
        if code not in promos:
            await interaction.response.send_message("\u26a0\ufe0f Invalid promo code.", ephemeral=True)
            return
        user = get_user(data, str(interaction.user.id))
        info = promos[code]
        scope = info.get("scope", "single")

        if scope == "public":
            redeemed_by = data.setdefault("_redeemed_by_user", {})
            users_for_code = redeemed_by.setdefault(code, [])
            if str(interaction.user.id) in users_for_code:
                await interaction.response.send_message("\u26a0\ufe0f You've already used this code!", ephemeral=True)
                return
        else:
            global_redeemed = data.setdefault("_redeemed_codes", [])
            if code in global_redeemed:
                await interaction.response.send_message("\u26a0\ufe0f This code has already been claimed!", ephemeral=True)
                return
            global_redeemed.append(code)
            data["_redeemed_codes"] = global_redeemed

        rtype = info.get("type", "spins")
        amount = info.get("amount", 0)
        if rtype == "spins":
            user["spins"] = min(MAX_SPINS, user["spins"] + amount)
        elif rtype == "berries":
            user["berries"] += amount
        elif rtype == "keys":
            user["keys"] += amount
        else:
            user["spins"] = min(MAX_SPINS, user["spins"] + amount)

        if scope == "public":
            redeemed_by = data.setdefault("_redeemed_by_user", {})
            redeemed_by.setdefault(code, []).append(str(interaction.user.id))
            data["_redeemed_by_user"] = redeemed_by

        save_data(data)
        rt = REWARD_TYPES.get(rtype)
        emoji = rt["emoji"] if rt else "\U0001f3b5"
        label = rt["label"] if rt else rtype
        await interaction.response.send_message(embed=branded_embed(
            "\U0001f3b5 Code Redeemed!",
            f"{emoji} **+{amount:,}** {label} added!",
            color=0x4CAF50,
        ))

class RedeemView(discord.ui.View):
    def __init__(self, default_code: str = ""):
        super().__init__(timeout=60)
        self.default_code = default_code

    @discord.ui.button(label="Redeem Code", style=discord.ButtonStyle.primary, emoji="\U0001f3b5")
    async def redeem_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(RedeemModal(self.default_code))

@bot.command(name="redeem")
@commands.cooldown(1, 4, commands.BucketType.user)
async def redeem(ctx: commands.Context, code: str = None):
    embed = discord.Embed(
        title="\U0001f3b5 Redeem a Code",
        description="Tap the button below and type in your promo code!",
        color=0xFFD700,
    )
    embed.set_footer(text=FOOTER_TEXT)
    await ctx.send(embed=embed, view=RedeemView(default_code=code or ""))

# -----------------------------------------------------------------------
# op codes — view available promo codes
# -----------------------------------------------------------------------
@bot.command(name="codes")
@commands.cooldown(1, 4, commands.BucketType.user)
async def codes_cmd(ctx: commands.Context):
    """View all available promo codes."""
    data = load_data()
    promos = data.get("_promo_codes", {})
    global_redeemed = set(data.get("_redeemed_codes", []))
    redeemed_by_user = data.get("_redeemed_by_user", {})
    uid = str(ctx.author.id)

    available = {}
    for k, v in promos.items():
        scope = v.get("scope", "single")
        if scope == "public":
            if uid in redeemed_by_user.get(k, []):
                continue
        elif k in global_redeemed:
            continue
        available[k] = v

    if not available:
        embed = discord.Embed(
            title="\U0001f3b5 Promo Codes",
            description="No active promo codes right now. Stay tuned!",
            color=0xFFD700,
        )
        embed.set_footer(text=FOOTER_TEXT)
        await ctx.send(embed=embed, view=RedeemView())
        return
    desc_lines = []
    for k, v in available.items():
        rt = REWARD_TYPES.get(v.get("type", "spins"))
        emoji = rt["emoji"] if rt else "\U0001f3b5"
        label = rt["label"] if rt else v.get("type", "?")
        scope_label = "\U0001f30d" if v.get("scope") == "public" else "\U0001f464"
        desc_lines.append(f"{emoji} **{k}** {scope_label} \u2014 {v.get('amount', 0):,} {label}")
    embed = discord.Embed(
        title="\U0001f3b5 Available Promo Codes",
        description="\n".join(desc_lines) + "\n\nTap **Redeem Code** below to claim one!",
        color=0xFFD700,
    )
    embed.set_footer(text=FOOTER_TEXT)
    await ctx.send(embed=embed, view=RedeemView())

# -----------------------------------------------------------------------
# op restart — owner-only, kills process (process manager auto-restarts)
# -----------------------------------------------------------------------
@bot.command(name="restart")
async def restart_bot(ctx: commands.Context):
    owner = BOT_OWNER_ID or (bot.owner_id if hasattr(bot, "owner_id") and bot.owner_id else None)
    if not owner or ctx.author.id != owner:
        await ctx.send("\u26a0\ufe0f Only the bot owner can use this command.")
        return
    save_data(load_data())
    await ctx.send(embed=branded_embed(
        "\U0001f504 Restarting",
        "Data saved. Restarting... See you in a sec!",
        color=0x2196F3,
    ))
    await bot.close()
    os._exit(0)

# -----------------------------------------------------------------------
# op save — force-save all game data
# -----------------------------------------------------------------------
@bot.command(name="save")
async def save_cmd(ctx: commands.Context):
    """Force-save all game data to disk & database."""
    owner = BOT_OWNER_ID or (bot.owner_id if hasattr(bot, "owner_id") and bot.owner_id else None)
    if not owner or ctx.author.id != owner:
        await ctx.send("\u26a0\ufe0f Only the bot owner can use this command.")
        return
    save_data(load_data())
    await ctx.send(embed=branded_embed(
        "\u2705 Data Saved",
        "All game data has been force-saved to disk & database.",
        color=0x4CAF50,
    ))

# -----------------------------------------------------------------------
# op status — check bot status
# -----------------------------------------------------------------------
@bot.command(name="status")
@commands.cooldown(1, 4, commands.BucketType.user)
async def status_cmd(ctx: commands.Context):
    """Check bot status and database connection."""
    pg_status_text = "\u274c Not configured" if not DATABASE_URL else "\u274c psycopg2 not installed" if not HAS_PG else "\u274c Connection failed"
    pg_color = 0xFF5722
    if DATABASE_URL and HAS_PG:
        conn = _pg_connect()
        if conn:
            pg_status_text = "\u2705 Connected"
            pg_color = 0x4CAF50
        else:
            pg_status_text = "\u274c Connection failed (check Railway PG service is linked)"
    storage = "File-based only (data resets on Railway!)" if not (DATABASE_URL and HAS_PG and _pg_connect()) else "PostgreSQL + File backup"
    total_users = len([k for k in load_data().keys() if not k.startswith('_')])
    embed = discord.Embed(
        title="\U0001f916 OP Bot Status",
        color=pg_color if "❌" in pg_status_text else 0x4CAF50,
    )
    embed.add_field(name="\U0001f4e1 PostgreSQL", value=pg_status_text, inline=True)
    embed.add_field(name="\U0001f4be Storage Mode", value=storage, inline=True)
    embed.add_field(name="\U0001f4dd Total Users", value=str(total_users), inline=True)
    if DATABASE_URL and HAS_PG and not _pg_connect():
        masked = DATABASE_URL[:DATABASE_URL.rfind("@")+1] + "***" if "@" in DATABASE_URL else "unknown"
        embed.add_field(name="\u26a0\ufe0f DB URL", value=f"`{masked}`", inline=False)
    embed.set_footer(text=FOOTER_TEXT)
    await ctx.send(embed=embed)

# -----------------------------------------------------------------------
# op fixdb — force reconnect to PostgreSQL
# -----------------------------------------------------------------------
@bot.command(name="fixdb")
async def fixdb_cmd(ctx: commands.Context):
    """Force re-connect to PostgreSQL and migrate data."""
    owner = BOT_OWNER_ID or (bot.owner_id if hasattr(bot, "owner_id") and bot.owner_id else None)
    if not owner or ctx.author.id != owner:
        await ctx.send("\u26a0\ufe0f Only the bot owner can use this command.")
        return
    global _PG_CONN
    _PG_CONN = None
    if not DATABASE_URL:
        await ctx.send("\u274c `DATABASE_URL` is not set. Add PostgreSQL on Railway first.")
        return
    if not HAS_PG:
        await ctx.send("\u274c `psycopg2` is not installed. Check `requirements.txt`.")
        return
    conn = _pg_connect()
    if conn:
        data = load_data()
        _save_pg("data", data)
        _save_pg("auctions", load_auctions())
        await ctx.send("\u2705 PostgreSQL reconnected & data migrated! Run `op status` to confirm.")
    else:
        await ctx.send("\u274c Still can't connect. Check Railway logs for the error message.")

# -----------------------------------------------------------------------
# op resetuser — owner-only: wipe a player's data back to defaults
# -----------------------------------------------------------------------
@bot.command(name="resetuser")
async def resetuser_cmd(ctx: commands.Context, *, target: str = None):
    """Reset a player's data (collection, team, progress) as if they just signed up."""
    owner = BOT_OWNER_ID or (bot.owner_id if hasattr(bot, "owner_id") and bot.owner_id else None)
    if not owner or ctx.author.id != owner:
        await ctx.send("\u26a0\ufe0f Only the bot owner can use this command.")
        return
    if not target:
        await ctx.send("\u26a0\ufe0f Usage: `op resetuser @mention` or `op resetuser 123456789`")
        return
    uid = None
    if target.startswith("<@") and target.endswith(">"):
        uid = target.strip("<@!>")
    else:
        uid = target.strip()
    if not uid.isdigit():
        await ctx.send("\u26a0\ufe0f Provide a valid user mention or numeric user ID.")
        return
    data = load_data()
    if uid not in data:
        await ctx.send(f"\u26a0\ufe0f No player found with ID `{uid}`.")
        return
    fresh = default_user()
    fresh["signed_up"] = True
    data[uid] = fresh
    save_data(data)
    await ctx.send(embed=branded_embed(
        "\U0001f504 Player Reset",
        f"Player `<@{uid}>` has been reset to a fresh state.\nTheir collection, team, spins, berries, and progress have all been wiped.",
        color=0xFF9800,
    ))

# -----------------------------------------------------------------------
# op odds — show pull rates
# -----------------------------------------------------------------------
@bot.command(name="odds")
@commands.cooldown(1, 4, commands.BucketType.user)
async def odds_cmd(ctx: commands.Context):
    """View pull rates and chances."""
    embed = discord.Embed(
        title="\U0001f3b2 Pull Rates & Odds",
        description="Every spin pulls a random character with a rarity. The rarer, the stronger!",
        color=0xFFD700,
    )
    embed.add_field(
        name="\U0001f3af Rarity Chances (1-in-X)",
        value=(
            "E  \u2014 1 in 2  (50%)\n"
            "D  \u2014 1 in 4  (25%)\n"
            "C  \u2014 1 in 10 (10%)\n"
            "B  \u2014 1 in 25 (4%)\n"
            "A  \u2014 1 in 100 (1%)\n"
            "S  \u2014 1 in 500 (0.2%)\n"
            "SS \u2014 1 in 2,000 (0.05%)\n"
            "\U0001f451 HDYGT \u2014 1 in 1,000,000 (0.0001%)"
        ),
        inline=True,
    )
    embed.add_field(
        name="\U0001f9ea Bonus Drops",
        value=(
            "\U0001f511 **Key** \u2014 8% chance per spin\n"
            "\U0001f4b0 **Beli** \u2014 5% chance (up to 200k)\n"
            "\U0001f4b0 **Duplicate** \u2014 sells for 50% of stat value\n"
            "\nUse **2x Luck** from the shop to double odds!"
        ),
        inline=True,
    )
    embed.set_footer(text=FOOTER_TEXT)
    await ctx.send(embed=embed)

# -----------------------------------------------------------------------
# op help
# -----------------------------------------------------------------------
HELP_CATEGORIES = {
    "overview": {
        "emoji": "\U0001f3f4\u200d\u2620\ufe0f",
        "label": "Overview",
        "title": "Overview",
        "desc": "Welcome to OP Bot! Select a category below to explore commands.",
        "commands": [],
    },
    "spinning": {
        "emoji": "\U0001f3b2",
        "label": "Spinning",
        "title": "Spinning",
        "desc": "Each spin gives a unique character with random race & Devil Fruit. Use `op odds` to check rarity weights.",
        "commands": [
            ("op spin", "Pull a random character"),
            ("op spins", "Check remaining spins"),
            ("op daily", "Claim daily bonus spins & berries"),
            ("op odds", "View pull rates & rarity chances"),
            ("op refreshspins", "Refill spins (costs 1 Key)"),
        ],
    },
    "collection": {
        "emoji": "\U0001f4d2",
        "label": "Collection",
        "title": "Collection",
        "desc": "Browse, inspect, sell, and manage your card collection.",
        "commands": [
            ("op inv / op inventory", "View your card collection"),
            ("op card <name>", "View a card's full stats"),
            ("op p <name>", "Preview a character card (simulated pull)"),
            ("op dex / op characters", "Browse the character pool"),
            ("op sell <name>", "Sell a duplicate card"),
            ("op fixmycards", "Repair corrupted card data"),
        ],
    },
    "shop": {
        "emoji": "\U0001f6cd\ufe0f",
        "label": "Shop & Economy",
        "title": "Shop & Economy",
        "desc": "Spend berries on items and climb the global rankings.",
        "commands": [
            ("op shop", "Open the item shop (button menu)"),
            ("op buy <item>", "Buy an item by name"),
            ("op leaderboard", "View global berry rankings"),
        ],
    },
    "duels": {
        "emoji": "\u2694\ufe0f",
        "label": "Duels",
        "title": "Duels",
        "desc": "Challenge other players to 1v1 character battles with wagers.",
        "commands": [
            ("op duel @user [bet]", "Challenge someone to a duel"),
            ("op team", "View your duel team"),
            ("op team+ <name>", "Add a character to your team"),
            ("op team- <name>", "Remove a character from your team"),
        ],
    },
    "quests": {
        "emoji": "\U0001f4dc",
        "label": "Quests",
        "title": "Quests",
        "desc": "Complete daily quests for bonus berries and rewards.",
        "commands": [
            ("op quests", "View active daily quests"),
            ("op claim <id>", "Claim a completed quest reward"),
        ],
    },
    "auction": {
        "emoji": "\U0001f528",
        "label": "Auction",
        "title": "Auction",
        "desc": "Trade cards with other players through public auctions.",
        "commands": [
            ("op auction start <c> <b> <min>", "Create a new auction"),
            ("op auction bid <id> <amt>", "Place a bid on an auction"),
            ("op auction cancel <id>", "Cancel your own auction"),
            ("op auction list", "View all active auctions"),
        ],
    },
    "promos": {
        "emoji": "\U0001f3b5",
        "label": "Promos & Info",
        "title": "Promos & Info",
        "desc": "Promo codes, bot info, and getting started.",
        "commands": [
            ("op codes", "View available promo codes"),
            ("op redeem", "Redeem a promo code"),
            ("op invite", "Add this bot to another server"),
            ("op signup", "Show this help menu"),
        ],
    },
    "owner": {
        "emoji": "\U0001f511",
        "label": "Owner",
        "title": "Owner",
        "desc": "Bot owner utilities and administration.",
        "commands": [
            ("op promocode", "Create or delete promo codes"),
            ("op restart", "Restart the bot"),
            ("op save", "Force-save all data"),
            ("op status", "Check database connection status"),
            ("op fixdb", "Reconnect to PostgreSQL"),
            ("op resetuser", "Reset a player's data"),
        ],
    },
}

_SEP = "\u2500" * 42

def _help_build_category(key: str) -> str:
    cat = HELP_CATEGORIES[key]
    if not cat["commands"]:
        return f"{_SEP}\n{cat['desc']}\n{_SEP}"
    lines = [cat["title"], _SEP]
    for cmd, desc in cat["commands"]:
        display = f"`{cmd}`"
        dots = "\u00b7" * max(2, 38 - len(display))
        lines.append(f"{display} {dots} {desc}")
    lines.append(_SEP)
    lines.append(cat["desc"])
    return "\n".join(lines)


class HelpSelect(discord.ui.Select):
    def __init__(self, ctx: commands.Context):
        self.ctx = ctx
        options = []
        for key, cat in HELP_CATEGORIES.items():
            if key == "owner" and ctx.author.id != BOT_OWNER_ID:
                continue
            options.append(
                discord.SelectOption(label=cat["label"], emoji=cat["emoji"], value=key)
            )
        super().__init__(placeholder="Choose a category...", options=options)

    async def callback(self, interaction: discord.Interaction):
        key = self.values[0]
        embed = discord.Embed(
            title="\U0001f3f4\u200d\u2620\ufe0f  OP Bot \u2014 Pirate's Handbook",
            description=_help_build_category(key),
            color=0xFFD700,
        )
        embed.set_footer(text=FOOTER_TEXT)
        await interaction.response.edit_message(embed=embed)


class HelpView(discord.ui.View):
    def __init__(self, ctx: commands.Context):
        super().__init__(timeout=120)
        self.add_item(HelpSelect(ctx))


@bot.command(name="help")
@commands.cooldown(1, 4, commands.BucketType.user)
async def help_command(ctx: commands.Context):
    embed = discord.Embed(
        title="\U0001f3f4\u200d\u2620\ufe0f  OP Bot \u2014 Pirate's Handbook",
        description=_help_build_category("overview"),
        color=0xFFD700,
    )
    embed.set_footer(text=FOOTER_TEXT)
    await ctx.send(embed=embed, view=HelpView(ctx))

# in-memory spam tracker: user_id -> [timestamp, ...]
_spam_tracker: dict = {}
_spam_cooldown: dict = {}
_spin_locks: set = set()

# -----------------------------------------------------------------------
# op spin
# -----------------------------------------------------------------------
class DuplicateChoiceView(discord.ui.View):
    def __init__(self, author_id: int, rarity: str = "C"):
        super().__init__(timeout=30)
        self.author_id = author_id
        self.rarity = rarity
        self.choice = None
        self.message = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("This isn't your pull!", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Keep Duplicate", style=discord.ButtonStyle.secondary, emoji="\U0001f4e6")
    async def keep_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.choice = "keep"
        await interaction.response.defer()
        self.stop()

    @discord.ui.button(label="Convert to Beli", style=discord.ButtonStyle.success, emoji="\U0001f4b0")
    async def convert_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.choice = "convert"
        await interaction.response.defer()
        self.stop()

    async def on_timeout(self):
        if self.choice is None:
            if RARITY_ORDER.index(self.rarity) >= RARITY_ORDER.index("A"):
                self.choice = "keep"
            else:
                self.choice = "convert"
        self.stop()
        if self.message:
            try:
                await self.message.edit(view=None)
            except Exception:
                pass

@bot.command(name="spin", aliases=["roll"])
async def spin(ctx: commands.Context):
    """Spin for a random One Piece character. Costs 1 spin."""
    now = time.time()
    now_ts = datetime.utcnow().timestamp()

    # anti-spam: 10s cooldown after 6+ spins in a 2s window
    cooldown_until = _spam_cooldown.get(ctx.author.id, 0)
    if now < cooldown_until:
        await ctx.send(embed=branded_embed(
            "\u26a0\ufe0f Spam Detected",
            f"{ctx.author.mention}, you're spinning too fast! Please wait **{int(cooldown_until - now)}s**.",
            color=0xFF9800,
        ))
        return
    spam = _spam_tracker.get(ctx.author.id, [])
    spam = [t for t in spam if now - t < 2]
    if len(spam) >= 5:
        _spam_cooldown[ctx.author.id] = now + 10
        await ctx.send(embed=branded_embed(
            "\u26a0\ufe0f Spam Detected",
            f"{ctx.author.mention}, you're spinning too fast! Please wait **10 seconds**.",
            color=0xFF9800,
        ))
        return
    spam.append(now)
    _spam_tracker[ctx.author.id] = spam

    uid = ctx.author.id
    if uid in _spin_locks:
        _spin_locks.discard(uid)
        await ctx.send(embed=branded_embed(
            "\u26a0\ufe0f Already Spinning",
            f"{ctx.author.mention}, you already have a spin in progress! Wait for it to finish.",
            color=0xFF9800,
        ))
        return
    _spin_locks.add(uid)

    data = load_data()
    user = get_user(data, str(ctx.author.id))

    if user["spins"] <= 0:
        await ctx.send(embed=branded_embed(
            "\U0001f6ab Out of spins",
            f"{ctx.author.mention}, you're out of spins! Try `op daily` for a free refill, "
            f"or use `op refreshspins` if you've collected Keys.",
            color=0x757575,
        ))
        _spin_locks.discard(uid)
        return

    # check autoroll — skip animation, consume timer & bypass spam
    fast = user.get("fast_spins", 0)
    auto = user.get("autoroll_remaining", 0)
    break_until = user.get("autoroll_break_until", 0)
    autoroll_active = auto > 0 and break_until < now_ts

    if autoroll_active:
        consume = SPIN_CONSUME_AUTOROLL
        auto_after = max(0, auto - consume)
        user["autoroll_remaining"] = auto_after
        if auto_after <= 0:
            user["autoroll_break_until"] = now_ts + SHOP_AUTOROLL_BREAK * 60
        suspense = await ctx.send("\U0001f3b2 Auto-rolling...")
        await asyncio.sleep(0.3)
    elif fast > 0:
        user["fast_spins"] = fast - 1
        suspense = await ctx.send("\U0001f3b2 Fast spin...")
        await asyncio.sleep(0.3)
    else:
        suspense = await ctx.send("\U0001f3b2 Spinning...")
        delays = [0.1, 0.12, 0.13, 0.15]
        for d in delays:
            c = random.choice(CHARACTERS)
            ri = RARITIES[c["rarity"]]
            try:
                await suspense.edit(embed=discord.Embed(
                    title=f"{ri['emoji']}  {c['name']}",
                    color=ri["color"],
                ))
            except Exception:
                pass
            await asyncio.sleep(d)

    now_ts = datetime.utcnow().timestamp()
    luck_active = now_ts < user.get("luck_until_utc", 0)

    pity_triggered = user["pity_counter"] >= PITY_THRESHOLD
    if pity_triggered:
        character = roll_pity_character()
    else:
        character = roll_character(luck_active=luck_active)

    rarity = character["rarity"]
    name = character["name"]

    user["spins"] -= 1
    user["spins_used"] = user.get("spins_used", 0) + 1
    bump_quest_progress(user, "roll_count")
    bump_quest_progress(user, "rarity_at_least", rarity)

    if pity_triggered:
        user["pity_counter"] = 0
    else:
        user["pity_counter"] += 1

    use_fruit_ticket = user.get("fruit_ticket", False)
    if use_fruit_ticket:
        user["fruit_ticket"] = False
        rare_pool = [f for f in FRUITS if f["rarity"] in ("Rare", "Legendary", "Mythical")]
        fruit = random.choice(rare_pool) if rare_pool else roll_fruit(rarity)
        r = random.random()
        if r < 0.10:
            myth = [f for f in FRUITS if f["rarity"] == "Mythical"]
            if myth:
                fruit = random.choice(myth)
        elif r < 0.35:
            leg = [f for f in FRUITS if f["rarity"] == "Legendary"]
            if leg:
                fruit = random.choice(leg)
    else:
        fruit = roll_fruit(rarity)
    race_name = roll_race(rarity)
    inst = create_instance(character, race_name, fruit)

    keys_found = roll_key_drop()
    if keys_found:
        user["keys"] += keys_found

    bonus_beli = 0
    if random.random() < BONUS_BELI_CHANCE:
        bonus_beli = random.randint(1000, BONUS_BELI_MAX)
        user["berries"] += bonus_beli

    is_duplicate = any(i["character"] == name for i in user["collection"])
    duplicate_payout = instance_payout(inst, rarity) if is_duplicate else 0

    if not is_duplicate:
        inst["inst_id"] = user["_next_inst_id"]
        user["_next_inst_id"] += 1
        user["collection"].append(inst)

    save_data(data)

    extra = {
        "pity_triggered": pity_triggered,
        "luck_active": luck_active and not pity_triggered,
        "hdygt": rarity == "HDYGT" and not is_duplicate,
        "keys_found": keys_found,
        "keys": user["keys"],
        "bonus_beli": bonus_beli,
        "spins": user["spins"],
        "pity": user["pity_counter"],
    }

    # ── HDYGT: Godly Flex Reveal ──
    if rarity == "HDYGT" and not is_duplicate:
        steps = [
            ("\U0001f30d  The world trembles...", 1.5),
            ("\U0001f4ab  A rift opens in the sky...", 1.5),
            ("\u2728  Divine light pours forth...", 1.5),
            ("\U0001f451  **THE GODS HAVE SPOKEN**", 2.0),
        ]
        for text, delay in steps:
            try:
                await suspense.edit(content=text, embed=None)
            except Exception:
                pass
            await asyncio.sleep(delay)

        announcement = discord.Embed(
            title="\U0001f451  \u2728  THE UNIVERSE HAS SPOKEN  \u2728  \U0001f451",
            description=(
                "\u2501" * 36 + "\n"
                f"\U0001f31f {ctx.author.mention} \U0001f31f\n\n"
                "You have been blessed by the divine.\n"
                "The **1 in 1,000,000** has chosen you.\n"
                "\u2501" * 36
            ),
            color=0xFFD700,
        )
        if character.get("image"):
            announcement.set_image(url=character["image"])
        announcement.set_footer(text="\u2728  A legend is born...")
        try:
            await suspense.edit(content=None, embed=announcement)
        except Exception:
            pass
        await asyncio.sleep(2.5)

        race_data = RACES.get(inst.get("race", "Human"), RACES["Human"])
        fruit = inst.get("fruit")
        total = inst.get("power", 0) + inst.get("health", 0) + inst.get("speed", 0)
        maxes = _STAT_MAX.get("HDYGT", _STAT_MAX["C"])
        bar = lambda v, m: _stat_bar(v, m)

        card_desc = (
            f"\u2501" * 36 + "\n"
            f"\U0001f451  **{name}**  \U0001f451\n"
            f"\u2753  HDYGT  \u2014  1 in 1,000,000\n"
            f"\u2501" * 36 + "\n\n"
            f"{race_data['emoji']}  {inst['race']}"
        )
        if fruit:
            fru = FRUIT_RARITIES.get(fruit.get("rarity", "Common"), {})
            card_desc += f"   \u2502   {fru.get('emoji', '')}  {fruit['name']}\n`{fruit.get('type', '')}`"
        else:
            card_desc += "\n`No Devil Fruit`"
        card_desc += "\n\n"
        card_desc += (
            f"\u2694  **Power**  `{inst['power']:>5,}`  {bar(inst['power'], maxes['power'])}\n"
            f"\u2764  **Health** `{inst['health']:>5,}`  {bar(inst['health'], maxes['health'])}\n"
            f"\U0001f4a8  **Speed**  `{inst['speed']:>5,}`  {bar(inst['speed'], maxes['speed'])}\n"
            f"\u2501" * 36 + "\n"
            f"\U0001f4ca  **TOTAL**  `{total:>6,}`  {bar(total, sum(maxes.values()))}\n\n"
            f"*{race_data['emoji']} {inst['race']}: {race_data['desc']}*"
        )
        if fruit:
            scale = RARITY_FRUIT_SCALE.get("HDYGT", 1.5)
            fr = FRUIT_RARITIES.get(fruit.get("rarity", "Common"), {})
            card_desc += f"\n*{fr.get('emoji', '')} Fruit effect scaled x{scale:.2f} by HDYGT tier*"
        card_desc += f"\n\u2501" * 36

        embed = discord.Embed(
            title="\U0001f451  \u2728  GODLY PULL  \u2728  \U0001f451",
            description=card_desc,
            color=0xFFFFFF,
        )
        embed.set_author(name="\u2753  HDYGT  \u2014  1 in 1,000,000")
        if character.get("image"):
            embed.set_image(url=character["image"])
        embed.add_field(name="\U0001f4b0  VALUE", value=f"**{RARITIES['HDYGT']['value']:,} Beli**", inline=True)
        embed.add_field(name="\U0001f3c6  TIER", value="**HDYGT** \u2014 Impossible", inline=True)
        embed.add_field(name="\U0001f4dc  CLAIM", value="Screenshot this. You earned it.", inline=True)
        embed.set_footer(text=f"{ctx.author.display_name}  \u2728  THE GODS HAVE SPOKEN  \u2728  Spins {user['spins']}/{MAX_SPINS}")
        await ctx.send(embed=embed)
        _spin_locks.discard(uid)
        return

    if not is_duplicate:
        embed = build_card_embed(inst, ctx, extra)
        await suspense.edit(content=None, embed=embed)
        _spin_locks.discard(uid)
        return

    # autoroll / fast spin — skip buttons, auto-handle
    if autoroll_active or fast > 0:
        embed = build_card_embed(inst, ctx, {**extra, "duplicate": True, "payout": duplicate_payout})
        await suspense.edit(content=None, embed=embed)
        await asyncio.sleep(0.3)
        if RARITY_ORDER.index(rarity) >= RARITY_ORDER.index("A"):
            view_choice = "keep"
        else:
            view_choice = "convert"
    else:
        embed = build_card_embed(inst, ctx, {**extra, "duplicate": True, "payout": duplicate_payout})
        view = DuplicateChoiceView(author_id=ctx.author.id, rarity=rarity)
        await suspense.edit(content=None, embed=embed, view=view)
        view.message = suspense
        await view.wait()
        view_choice = view.choice

    dup_embed = build_card_embed(inst, ctx, {**extra})
    if view_choice == "keep":
        inst["inst_id"] = user["_next_inst_id"]
        user["_next_inst_id"] += 1
        user["collection"].append(inst)
        dup_embed.add_field(name="\U0001f4e6 Kept", value="Added as a unique card in your inventory.", inline=False)
    else:
        user["berries"] += duplicate_payout
        dup_embed.add_field(name="\U0001f4b0 Converted", value=f"Converted to **{duplicate_payout:,} Beli**.", inline=False)

    save_data(data)
    await suspense.edit(embed=dup_embed, view=None)
    _spin_locks.discard(uid)

@spin.error
async def spin_error(ctx: commands.Context, error: Exception):
    _spin_locks.discard(ctx.author.id)
    raise error

# -----------------------------------------------------------------------
# op refreshspins
# -----------------------------------------------------------------------
@bot.command(name="refreshspins", aliases=["chest"])
async def refreshspins(ctx: commands.Context):
    """Spend 1 Key to refill your spins to max."""
    data = load_data()
    user = get_user(data, str(ctx.author.id))
    if user["keys"] <= 0:
        await ctx.send(embed=branded_embed(
            "\U0001f512 No Keys",
            f"{ctx.author.mention}, you don't have any Keys yet. Keep spinning for a chance to find one!",
            color=0x757575,
        ))
        return
    user["keys"] -= 1
    user["spins"] = MAX_SPINS
    save_data(data)
    await ctx.send(embed=branded_embed(
        "\U0001f504 Spins Refreshed!",
        f"Spins refilled to **{MAX_SPINS}**! You have **{user['keys']}** key(s) left.",
        color=0x4CAF50,
    ))

# -----------------------------------------------------------------------
# op spins
# -----------------------------------------------------------------------
@bot.command(name="spins")
async def spins(ctx: commands.Context):
    data = load_data()
    user = get_user(data, str(ctx.author.id))
    save_data(data)
    await ctx.send(embed=branded_embed(
        "\U0001f3af Spins",
        f"{ctx.author.mention}, you have **{user['spins']}/{MAX_SPINS}** spins left.",
    ))

# -----------------------------------------------------------------------
# op daily
# -----------------------------------------------------------------------
@bot.command(name="daily")
async def daily(ctx: commands.Context):
    data = load_data()
    user = get_user(data, str(ctx.author.id))
    now_ts = datetime.utcnow().timestamp()
    elapsed = now_ts - user["last_daily_utc"]
    cooldown_seconds = DAILY_COOLDOWN_HOURS * 3600
    if elapsed < cooldown_seconds:
        wait = cooldown_seconds - elapsed
        await ctx.send(embed=branded_embed(
            "\u23f3 Already claimed today",
            f"Come back in **{fmt_duration(wait)}**.",
            color=0x757575,
        ))
        return
    user["spins"] = MAX_SPINS
    user["berries"] += DAILY_BONUS_BERRIES
    user["last_daily_utc"] = now_ts
    save_data(data)
    await ctx.send(embed=branded_embed(
        "\U0001f381 Daily Claimed!",
        f"Spins refilled to **{MAX_SPINS}** and you received **{DAILY_BONUS_BERRIES:,} Beli**.\n"
        f"Next daily in **{fmt_duration(cooldown_seconds)}**.",
        color=0x4CAF50,
    ))

# -----------------------------------------------------------------------
# -----------------------------------------------------------------------
# op inventory / op inv \u2014 premium paginated collection
# -----------------------------------------------------------------------
INV_PER_PAGE = 12


class CharacterDetailView(discord.ui.View):
    def __init__(self, char_name: str, instances: list, display_name: str, user_id: int, inv_kwargs: dict):
        super().__init__(timeout=120)
        self.char_name = char_name
        self.instances = instances
        self.display_name = display_name
        self.user_id = user_id
        self.inv_kwargs = inv_kwargs

    def _embed(self) -> discord.Embed:
        char = character_lookup(self.char_name)
        if not char:
            return branded_embed("\u26a0\ufe0f Unknown Character", color=0xFF5722)
        r = char["rarity"]
        e = discord.Embed(
            title=f"{RARITIES[r]['emoji']}  \u2605  {self.char_name}  \u2014  {len(self.instances)} card{'s' if len(self.instances) != 1 else ''}",
            color=RARITIES[r]["color"],
        )
        if char.get("image"):
            e.set_thumbnail(url=char["image"])
        e.set_footer(text=f"{self.display_name}'s collection")

        max_show = 6
        shown = self.instances[:max_show]
        remaining = len(self.instances) - max_show

        sections = []
        for inst in shown:
            race_data = RACES.get(inst.get("race", "Human"), RACES["Human"])
            fruit = inst.get("fruit")
            if fruit:
                fru = FRUIT_RARITIES[fruit["rarity"]]
                fruit_line = f"{fru['emoji']} {fruit['name']} (`{fruit['type']}`)"
            else:
                fruit_line = "*No Fruit*"
            ir = inst.get("rarity", "C")
            maxes = _STAT_MAX.get(ir, _STAT_MAX["C"])
            p, h, s = inst.get("power", 0), inst.get("health", 0), inst.get("speed", 0)
            total = p + h + s

            section = (
                f"**Instance #{inst.get('inst_id', '?')}**\n"
                f"{race_data['emoji']} {inst['race']}  \u00b7  {fruit_line}\n"
                f"\u2694 {_stat_bar(p, maxes['power'])}  `{p:,}`  \u00b7  \u2764 {_stat_bar(h, maxes['health'])}  `{h:,}`\n"
                f"\U0001f4a8 {_stat_bar(s, maxes['speed'])}  `{s:,}`  \u00b7  \U0001f4ca **{total:,}** TOTAL"
            )
            sections.append(section)

        desc = "\n\n\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n\n".join(sections)
        if remaining > 0:
            desc += f"\n\n*+{remaining} more \u2014 use the main inventory to see all*"
        e.description = desc
        return e

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Not your inventory!", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="\u25c0 Back to Inventory", style=discord.ButtonStyle.secondary)
    async def back_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        inv_view = InventoryView(**self.inv_kwargs)
        await interaction.response.edit_message(embed=inv_view._embed(), view=inv_view)


class InventoryView(discord.ui.View):
    def __init__(self, pages: list, total_cards: int, display_name: str, user_id: int,
                 stats_line: str, page_images: list, page_colors: list, sorted_chars: list):
        super().__init__(timeout=120)
        self.pages = pages
        self.total_cards = total_cards
        self.display_name = display_name
        self.user_id = user_id
        self.stats_line = stats_line
        self.page_images = page_images
        self.page_colors = page_colors
        self.sorted_chars = sorted_chars
        self.page = 0

        # Character jump select (up to 25 options)
        options = []
        for name, instances in sorted_chars:
            char = character_lookup(name)
            if not char:
                continue
            r = char["rarity"]
            label = f"{name}  \u2014  {len(instances)}x"
            if len(label) > 100:
                label = label[:97] + "..."
            options.append(discord.SelectOption(
                label=label, value=name, emoji=RARITIES[r]["emoji"],
            ))
        if options:
            self.char_select = discord.ui.Select(
                placeholder="\U0001f50d Jump to a character...",
                options=options[:25], row=0,
            )
            select = self.char_select
            async def _char_cb(i: discord.Interaction):
                char_name = select.values[0]
                instances = []
                for name, insts in self.sorted_chars:
                    if name == char_name:
                        instances = insts
                        break
                inv_kwargs = {
                    "pages": self.pages,
                    "total_cards": self.total_cards,
                    "display_name": self.display_name,
                    "user_id": self.user_id,
                    "stats_line": self.stats_line,
                    "page_images": self.page_images,
                    "page_colors": self.page_colors,
                    "sorted_chars": self.sorted_chars,
                }
                detail_view = CharacterDetailView(
                    char_name=char_name,
                    instances=instances,
                    display_name=self.display_name,
                    user_id=self.user_id,
                    inv_kwargs=inv_kwargs,
                )
                await i.response.edit_message(
                    embed=detail_view._embed(),
                    view=detail_view,
                )
            self.char_select.callback = _char_cb
            self.add_item(self.char_select)

        self._update_buttons()

    def _embed(self) -> discord.Embed:
        e = discord.Embed(
            title=f"\U0001f4d2  {self.display_name}'s Collection  \u2014  {self.total_cards} cards",
            color=self.page_colors[self.page],
        )
        e.description = self.stats_line
        thumb = self.page_images[self.page]
        if thumb:
            e.set_thumbnail(url=thumb)
        for field in self.pages[self.page]:
            e.add_field(name=field["name"], value=field["value"], inline=False)
        total_pages = len(self.pages)
        if total_pages > 1:
            e.set_footer(text=f"Page {self.page + 1}/{total_pages}")
        return e

    def _update_buttons(self):
        self.prev_btn.disabled = self.page == 0
        self.next_btn.disabled = self.page == len(self.pages) - 1

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Not your inventory!", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="\u25c0  Page", style=discord.ButtonStyle.primary, row=1)
    async def prev_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page -= 1
        self._update_buttons()
        await interaction.response.edit_message(embed=self._embed(), view=self)

    @discord.ui.button(label="Page  \u25b6", style=discord.ButtonStyle.primary, row=1)
    async def next_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page += 1
        self._update_buttons()
        await interaction.response.edit_message(embed=self._embed(), view=self)

@bot.command(name="inventory", aliases=["inv", "collection"])
@commands.cooldown(1, 4, commands.BucketType.user)
async def inventory(ctx: commands.Context):
    try:
        data = load_data()
        user = get_user(data, str(ctx.author.id))

        if not user.get("collection"):
            await ctx.send(f"{ctx.author.mention}, you haven't pulled anyone yet! Try `op spin`.")
            return

        by_char = {}
        for inst in user["collection"]:
            try:
                key = inst.get("character", "Unknown")
                if key not in by_char:
                    by_char[key] = []
                by_char[key].append(inst)
            except Exception:
                continue

        def _sort_key(item):
            c = character_lookup(item[0])
            return RARITY_ORDER.index(c["rarity"]) if c else 99
        sorted_chars = sorted(by_char.items(), key=_sort_key)

        def _safe(val, default=0):
            try:
                return int(val) if val is not None else default
            except (TypeError, ValueError):
                return default

        berries = _safe(user.get("berries"))
        spins = _safe(user.get("spins"))
        keys = _safe(user.get("keys"))
        fast = _safe(user.get("fast_spins"))
        auto_val = _safe(user.get("autoroll_remaining"))
        break_ts = user.get("autoroll_break_until", 0)
        pity = _safe(user.get("pity_counter"))

        # --- premium stats line ---
        auto_str = f"{auto_val // 60}m" if auto_val else "0m"
        break_str = ""
        try:
            if break_ts and break_ts > datetime.utcnow().timestamp():
                left = int((break_ts - datetime.utcnow().timestamp()) // 60)
                break_str = f" (break {left}m)"
        except Exception:
            pass
        stats_line = (
            f"\U0001f4b0 **{berries:,}**  \u00b7  \U0001f3af **{spins}/{MAX_SPINS}**  \u00b7  "
            f"\U0001f511 **{keys}**  \u00b7  \u26a1 **{fast}**\n"
            f"\U0001f504 {auto_str}{break_str}  \u00b7  \u26a1 **{pity}/{PITY_THRESHOLD}**"
        )

        # Build field dicts grouped into pages with rich metadata
        all_fields = []
        page_images = []
        page_colors = []
        max_lines = 4

        for char_name, instances in sorted_chars:
            char = character_lookup(char_name)
            if not char:
                continue
            r = char["rarity"]
            instance_lines = []
            for inst in instances:
                try:
                    instance_lines.append(_compact_stat_line(inst))
                except Exception:
                    continue
            if not instance_lines:
                continue
            shown = instance_lines[:max_lines]
            val = "\n".join(shown)
            remaining = len(instance_lines) - max_lines
            if remaining > 0:
                val += f"\n*+{remaining} more*"
            all_fields.append({
                "name": f"{rarity_icon(r)}  \u2605  {char_name}  \u2014  {len(instances)}\u00d7",
                "value": val,
                "rarity": r,
                "image": char.get("image", ""),
            })

        pages = [all_fields[i:i + INV_PER_PAGE] for i in range(0, len(all_fields), INV_PER_PAGE)]

        # Build per-page thumbnail and color arrays
        rarity_idx = {r: i for i, r in enumerate(RARITY_ORDER)}
        for page_fields in pages:
            if not page_fields:
                continue
            best_r = min((pf["rarity"] for pf in page_fields), key=lambda x: rarity_idx.get(x, 99))
            page_images.append(page_fields[0].get("image", ""))
            page_colors.append(RARITIES[best_r]["color"])

        view = InventoryView(
            pages=pages,
            total_cards=len(user["collection"]),
            display_name=ctx.author.display_name,
            user_id=ctx.author.id,
            stats_line=stats_line,
            page_images=page_images,
            page_colors=page_colors,
            sorted_chars=sorted_chars,
        )
        await ctx.send(embed=view._embed(), view=view)
    except Exception as e:
        tb = "".join(traceback.format_exception(type(e), e, e.__traceback__))
        print(f"[INVENTORY ERROR] {ctx.author.id}:")
        print(tb)
        try:
            data = load_data()
            user = get_user(data, str(ctx.author.id))
            fixes = repair_user_data(user)
            save_data(data)
            if fixes:
                await ctx.send(embed=branded_embed(
                    "\u2705 Auto-Repaired",
                    f"Found and fixed **{len(fixes)} issue(s)** in your data. Try `op inv` again!\n"
                    + "\n".join(f"\u2022 {f}" for f in fixes[:10])
                    + ("\n..." if len(fixes) > 10 else ""),
                    color=0x4CAF50,
                ))
                return
        except Exception as repair_err:
            print(f"[REPAIR ERROR] {ctx.author.id}: {repair_err}")
        await ctx.send(embed=branded_embed(
            "\u26a0\ufe0f Inventory Error",
            "Your collection has an unexpected issue I couldn't auto-fix.\n"
            f"```\n{tb[-1800:]}\n```\nTry `op fixmycards` for a deeper scan, or send this to the bot owner.",
            color=0xFF5722,
        ))


# -----------------------------------------------------------------------
# op fixmycards — force-repair a user's data
# -----------------------------------------------------------------------
@bot.command(name="fixmycards")
@commands.cooldown(1, 1, commands.BucketType.user)
async def fixmycards(ctx: commands.Context):
    data = load_data()
    user = get_user(data, str(ctx.author.id))
    fixes = repair_user_data(user)
    save_data(data)
    desc = "Your collection has been scanned and repaired." if fixes else "No issues found — your data looks clean!"
    if fixes:
        desc = f"**{len(fixes)} fix(es) applied:**\n" + "\n".join(f"• {f}" for f in fixes[:20])
        if len(fixes) > 20:
            desc += f"\n...and {len(fixes) - 20} more"
    await ctx.send(embed=branded_embed(
        "\u2705 Cards Repaired",
        desc,
        color=0x4CAF50,
    ))

# -----------------------------------------------------------------------
# op card — view a specific instance's full card
# -----------------------------------------------------------------------
@bot.command(name="card")
@commands.cooldown(1, 4, commands.BucketType.user)
async def card(ctx: commands.Context, *, query: str = None):
    if not query:
        await ctx.send("\u26a0\ufe0f Usage: `op card <character name or #id>`")
        return

    data = load_data()
    user = get_user(data, str(ctx.author.id))

    target_inst = None

    if query.startswith("#"):
        try:
            inst_id = int(query[1:])
        except ValueError:
            await ctx.send("\u26a0\ufe0f Invalid card ID. Use `op inv` to see IDs.")
            return
        for inst in user["collection"]:
            if inst["inst_id"] == inst_id:
                target_inst = inst
                break
        if not target_inst:
            await ctx.send(f"\u26a0\ufe0f No card found with ID **#{inst_id}**.")
            return
    else:
        matches, resolved = collection_search(user, query)
        if isinstance(resolved, str) and not matches:
            await ctx.send(resolved)
            return
        if not matches:
            await ctx.send(f"\u26a0\ufe0f You don't own **{query}**. Check `op inv`.")
            return
        if len(matches) == 1:
            target_inst = matches[0]
        else:
            lines = []
            for inst in matches:
                race_data = RACES.get(inst["race"], RACES["Human"])
                fruit_str = f" \u2022 {FRUIT_RARITIES[inst['fruit']['rarity']]['emoji']} {inst['fruit']['name']}" if inst.get("fruit") else ""
                lines.append(f"`#{inst['inst_id']}` {race_data['emoji']} {inst['race']}{fruit_str}")
            embed = branded_embed(
                f"\U0001f392 {query} — Multiple Cards",
                "You have multiple cards of this character. Use `op card #<id>` to view a specific one:\n" + "\n".join(lines),
                color=0x00BCD4,
            )
            await ctx.send(embed=embed)
            return

    extra = {"spins": user["spins"], "pity": user["pity_counter"]}
    embed = build_card_embed(target_inst, ctx, extra)
    await ctx.send(embed=embed)

# -----------------------------------------------------------------------
# op p — card preview (simulate a pull)
# -----------------------------------------------------------------------
@bot.command(name="p", aliases=["preview", "pr"])
@commands.cooldown(1, 2, commands.BucketType.user)
async def preview(ctx: commands.Context, *, name: str = None):
    if not name:
        await ctx.send("\u26a0\ufe0f Usage: `op p <character name>`")
        return

    resolved = resolve_character_name(name)
    if resolved is None:
        await ctx.send(f"\u26a0\ufe0f No character found matching **{name}**. Check `op dex`.")
        return
    if isinstance(resolved, list):
        await ctx.send(f"\u26a0\ufe0f Multiple characters match \"{name}\": {', '.join(resolved[:5])}")
        return

    char = character_lookup(resolved)
    if not char:
        await ctx.send(f"\u26a0\ufe0f Could not load **{resolved}**.")
        return

    inst = create_instance(char)
    rarity = char["rarity"]
    race_data = RACES.get(inst.get("race", "Human"), RACES["Human"])
    fruit = inst.get("fruit")
    total = inst.get("power", 0) + inst.get("health", 0) + inst.get("speed", 0)
    maxes = _STAT_MAX.get(rarity, _STAT_MAX["C"])
    bar = lambda v, m: _stat_bar(v, m)

    desc = (
        f"\u2501" * 32 + "\n"
        f"\u25c6  **{name}**  \u25c6\n"
        f"{RARITIES[rarity]['emoji']}  {rarity}  \u2014  {inst['race']}\n"
        f"\u2501" * 32 + "\n\n"
        f"{race_data['emoji']}  {inst['race']}"
    )
    if fruit:
        fru = FRUIT_RARITIES.get(fruit.get("rarity", "Common"), {})
        desc += f"   \u2502   {fru.get('emoji', '')}  {fruit['name']}\n`{fruit.get('type', '')}`"
    else:
        desc += "\n`No Devil Fruit`"
    desc += "\n\n"
    desc += (
        f"\u26a1  **PWR** `{inst['power']:>5,}`  {bar(inst['power'], maxes['power'])}\n"
        f"\u2764  **HP**  `{inst['health']:>5,}`  {bar(inst['health'], maxes['health'])}\n"
        f"\U0001f4a8  **SPD** `{inst['speed']:>5,}`  {bar(inst['speed'], maxes['speed'])}\n"
        f"\u2501" * 32 + "\n"
        f"\U0001f4ca  **TOTAL** `{total:>6,}`  {bar(total, sum(maxes.values()))}"
    )

    embed = discord.Embed(
        title="\u26a1  HOLO-PREVIEW  \u26a1",
        description=desc,
        color=0xFFFFFF,
    )
    embed.set_author(name=f"\u2728  {rarity}  \u2014  {inst['race']}")
    if char.get("image"):
        embed.set_image(url=char["image"])

    footnote = f"*{race_data['emoji']} {inst['race']}: {race_data['desc']}*"
    if fruit:
        scale = RARITY_FRUIT_SCALE.get(rarity, 1.0)
        fr = FRUIT_RARITIES.get(fruit.get("rarity", "Common"), {})
        footnote += f"\n*{fr.get('emoji', '')} Fruit effect scaled x{scale:.2f} by {rarity} tier*"
    embed.add_field(name="\U0001f4c4  SPECS", value=footnote, inline=False)

    embed.set_footer(text=f"PREVIEW  \u2022  {FOOTER_TEXT}")
    await ctx.send(embed=embed)

# -----------------------------------------------------------------------
# op characters / op dex
# -----------------------------------------------------------------------
@bot.command(name="characters", aliases=["dex"])
async def characters(ctx: commands.Context):
    embed = branded_embed("\U0001f4d6 One Piece Character Pool", color=0x4CAF50)
    for rarity in RARITIES:
        names = [c["name"] for c in CHARACTERS if c["rarity"] == rarity]
        if names:
            embed.add_field(
                name=f"{rarity_icon(rarity)} {rarity} (drop {RARITIES[rarity]['weight']}%-weighted)",
                value="\n".join(names),
                inline=False,
            )
    await ctx.send(embed=embed)

# -----------------------------------------------------------------------
# op sell
# -----------------------------------------------------------------------
class InstanceSelectView(discord.ui.View):
    def __init__(self, author_id: int, instances: list):
        super().__init__(timeout=60)
        self.author_id = author_id
        self.selected = None
        options = []
        for inst in instances:
            race_data = RACES.get(inst["race"], RACES["Human"])
            fruit_str = f" [{inst['fruit']['name']}]" if inst.get("fruit") else ""
            label = f"#{inst['inst_id']} {inst['character']} — {race_data['emoji']} {inst['race']}{fruit_str}"
            if len(label) > 100:
                label = label[:97] + "..."
            options.append(discord.SelectOption(label=label, value=str(inst["inst_id"])))
        if len(options) == 1:
            self.selected = instances[0]
            self.stop()
            return
        select = discord.ui.Select(placeholder="Choose a card to sell...", options=options[:25])
        async def callback(interaction: discord.Interaction):
            if interaction.user.id != self.author_id:
                await interaction.response.send_message("Not your sale!", ephemeral=True)
                return
            inst_id = int(select.values[0])
            for inst in instances:
                if inst["inst_id"] == inst_id:
                    self.selected = inst
                    break
            await interaction.response.defer()
            self.stop()
        select.callback = callback
        self.add_item(select)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("Not your sale!", ephemeral=True)
            return False
        return True

@bot.command(name="sell")
@commands.cooldown(1, 2, commands.BucketType.user)
async def sell(ctx: commands.Context, *, character_name: str = None):
    if not character_name:
        await ctx.send("\u26a0\ufe0f Usage: `op sell <character name>`")
        return

    data = load_data()
    user = get_user(data, str(ctx.author.id))

    matches, resolved = collection_search(user, character_name)
    if not matches:
        msg = resolved if isinstance(resolved, str) else f"\u26a0\ufe0f You don't own **{character_name}**. Check `op inv`."
        await ctx.send(msg)
        return

    target = None
    if len(matches) == 1:
        target = matches[0]
    else:
        view = InstanceSelectView(ctx.author.id, matches)
        msg = await ctx.send("\u26a0\ufe0f You have multiple cards of this character. Select which one to sell:", view=view)
        await view.wait()
        try:
            await msg.delete()
        except Exception:
            pass
        target = view.selected
        if not target:
            return

    if not target:
        return

    char = character_lookup(target["character"])
    value = instance_payout(target, char["rarity"])

    user["collection"] = [inst for inst in user["collection"] if inst["inst_id"] != target["inst_id"]]
    if target["inst_id"] in user["team"]:
        user["team"].remove(target["inst_id"])
    user["berries"] += value
    bump_quest_progress(user, "sell_count")
    save_data(data)

    race_data = RACES.get(target.get("race", "Human"), RACES["Human"])
    fruit_name = target.get("fruit", {}).get("name", "") if target.get("fruit") else ""
    fruit_label = f" + {target.get('fruit', {}).get('rarity', 'Common')} Fruit" if fruit_name else " (No Fruit)"
    race_label = f"x{race_data['value']:.2f}" if race_data['value'] != 1.0 else ""
    await ctx.send(embed=branded_embed(
        "\U0001f4b0 Sold!",
        f"You sold **{target['character']}** — {target['race']}{race_label}{fruit_label}\n"
        f"Value: **{value:,} Beli**\n"
        f"New balance: {user['berries']:,} Beli.",
        color=0x4CAF50,
    ))

# -----------------------------------------------------------------------
# op duel (challenge -> pick -> battle)
# -----------------------------------------------------------------------
@bot.command(name="duel")
@commands.cooldown(1, 5, commands.BucketType.channel)
async def duel(ctx: commands.Context, opponent: discord.Member, wager: int = 0):
    if opponent.id == ctx.author.id:
        await ctx.send("\u26a0\ufe0f You can't duel yourself.")
        return
    if opponent.bot:
        await ctx.send("\u26a0\ufe0f You can't duel a bot.")
        return
    if ctx.channel.id in active_duels:
        await ctx.send("\u26a0\ufe0f A duel is already active in this channel.")
        return
    if wager < 0:
        await ctx.send("\u26a0\ufe0f Wager can't be negative.")
        return
    if wager > DUEL_MAX_WAGER:
        await ctx.send(f"\u26a0\ufe0f Max wager is **{DUEL_MAX_WAGER:,} Beli**.")
        return

    data = load_data()
    challenger = get_user(data, str(ctx.author.id))
    opponent_user = get_user(data, str(opponent.id))

    if wager > 0 and (challenger["berries"] < wager or opponent_user["berries"] < wager):
        await ctx.send("\u26a0\ufe0f Both need enough berries to cover the wager.")
        return

    chall_team = get_duel_team(challenger)
    opp_team = get_duel_team(opponent_user)
    if not chall_team or not opp_team:
        await ctx.send("\u26a0\ufe0f Both players need at least one character to duel.")
        return

    state = DuelState(ctx, opponent, wager)

    # Phase 1: Send challenge
    challenge_view = DuelChallengeView(ctx, opponent, wager)

    desc = f"**{ctx.author.display_name}** challenges **{opponent.display_name}** to a duel!"
    if wager:
        desc += f"\n\nWager: **{wager:,} Beli**"
    challenge_embed = discord.Embed(
        title="\u2694\ufe0f Duel Challenge!",
        description=desc,
        color=0xFF5722,
    )
    challenge_embed.set_footer(text=f"Expires in {DUEL_CHALLENGE_TIMEOUT}s")

    msg = await ctx.send(embed=challenge_embed, view=challenge_view)
    await challenge_view.wait()

    if challenge_view.accepted:
        # Phase 2: Pick characters
        pick_view = TeamPickerView(state, chall_team, opp_team, is_bot=False)

        if wager > 0:
            challenger["berries"] -= wager
            opponent_user["berries"] -= wager
            save_data(data)

        active_duels[ctx.channel.id] = state

        pick_embed = discord.Embed(
            title="\u2694\ufe0f Select Your Fighter",
            description=f"**{ctx.author.display_name}** vs **{opponent.display_name}**\nPick one character from your team!",
            color=0xFF5722,
        )
        await msg.edit(embed=pick_embed, view=pick_view)
        await pick_view.wait()

        # Resolve picks (auto-pick highest stat on timeout)
        if pick_view.challenger_pick is None:
            pick_view.challenger_pick = max(chall_team, key=instance_total_stat)["inst_id"]
        if pick_view.opponent_pick is None:
            pick_view.opponent_pick = max(opp_team, key=instance_total_stat)["inst_id"]

        state.challenger_char = next(i for i in chall_team if i["inst_id"] == pick_view.challenger_pick)
        state.opponent_char = next(i for i in opp_team if i["inst_id"] == pick_view.opponent_pick)
        state.attacker_id = random.choice([ctx.author.id, opponent.id])
        state.message = msg

        # Phase 3: Battle
        await _run_battle(state)

    else:
        # Bot fallback
        bot_view = BotOptionView(ctx, opponent)

        bot_embed = discord.Embed(
            title="\u23f0 No Response",
            description=f"**{opponent.display_name}** didn't accept.\nYou can fight a bot using their team - reward is **50%** of normal.",
            color=0x9E9E9E,
        )
        await msg.edit(embed=bot_embed, view=bot_view)
        await bot_view.wait()

        if bot_view.bot_mode:
            state.is_bot = True
            state.challenger_char = max(chall_team, key=instance_total_stat)
            state.opponent_char = max(opp_team, key=instance_total_stat)
            state.attacker_id = ctx.author.id
            state.message = msg

            active_duels[ctx.channel.id] = state
            await _run_battle(state)
        else:
            await msg.edit(embed=discord.Embed(
                title="\U0001f44b Duel Cancelled",
                description="Maybe next time!",
                color=0x9E9E9E,
            ), view=None)


async def _run_battle(state: DuelState):
    # Auto-battle between two selected characters until one dies.
    p1 = copy.deepcopy(state.challenger_char)
    p2 = copy.deepcopy(state.opponent_char)
    p1["current_hp"] = p1["health"]
    p2["current_hp"] = p2["health"]

    attacker_char = p1 if state.attacker_id == state.ctx.author.id else p2
    defender_char = p2 if state.attacker_id == state.ctx.author.id else p1

    if state.is_bot:
        p1_name = state.ctx.author.display_name
        p2_name = f"{state.opponent.display_name} (Bot)"
    else:
        p1_name = state.ctx.author.display_name
        p2_name = state.opponent.display_name

    if id(attacker_char) == id(p2):
        p1_name, p2_name = p2_name, p1_name

    rounds = []
    r = 1

    while True:
        dmg = max(1, round(
            max(attacker_char["power"] // 10, attacker_char["power"] - defender_char["speed"] // 3)
            * random.uniform(0.85, 1.0)
        ))
        defender_char["current_hp"] -= dmg
        if defender_char["current_hp"] < 0:
            defender_char["current_hp"] = 0

        rounds.append(
            f"**R{r}** {RARITIES[attacker_char['rarity']]['emoji']} **{attacker_char['name']}**"
            f" \u279c {RARITIES[defender_char['rarity']]['emoji']} **{defender_char['name']}**"
            f" \u2014 **{dmg:,}** {hp_bar(defender_char['current_hp'], defender_char['health'])}"
            f" ({defender_char['current_hp']:,}/{defender_char['health']:,})"
        )

        if defender_char["current_hp"] <= 0:
            state.winner_id = state.attacker_id
            break

        attacker_char, defender_char = defender_char, attacker_char
        r += 1
        if r > 100:
            break

        if r % 3 == 1:
            progress = discord.Embed(
                title="\u2694\ufe0f Battle in Progress",
                description="\n".join(rounds[-3:]),
                color=0xFF5722,
            )
            await state.message.edit(embed=progress, view=None)
            await asyncio.sleep(1.5)

    winner_name = p1_name if state.winner_id == state.ctx.author.id else p2_name

    final = discord.Embed(
        title="\u2694\ufe0f Battle Over!",
        description="\n".join(rounds) + f"\n\n**{winner_name}** wins! \U0001f3c6",
        color=0x4CAF50,
    )
    await state.message.edit(embed=final, view=None)
    await asyncio.sleep(1.5)

    await _finish_duel(state)


async def _finish_duel(state: DuelState):
    state.finished = True
    active_duels.pop(state.channel.id, None)

    if state.winner_id is None:
        return

    data = load_data()
    winner_user = get_user(data, str(state.winner_id))

    winner_name = state.ctx.author.display_name if state.winner_id == state.ctx.author.id else state.opponent.display_name

    if state.is_bot:
        loser_char = state.opponent_char
        base = RARITIES[loser_char["rarity"]]["value"] // 10
        reward = int(base * DUEL_BOT_REWARD_MULTIPLIER)
        winner_user["berries"] += reward
        reward_text = f"**{reward:,} Beli** (bot match \u2014 50%)"
    else:
        pot = state.wager * 2 if state.wager > 0 else 0
        loser_char = state.opponent_char if state.winner_id == state.ctx.author.id else state.challenger_char
        bonus = RARITIES[loser_char["rarity"]]["value"] // 10
        total_reward = pot + bonus
        winner_user["berries"] += total_reward
        if state.wager > 0:
            reward_text = f"**{total_reward:,} Beli** (pot: {pot:,} + bonus: {bonus:,})"
        else:
            reward_text = f"**{bonus:,} Beli**"

    bump_quest_progress(winner_user, "duel_win")
    save_data(data)

    embed = discord.Embed(
        title="\U0001f3c6 Duel Won!",
        description=f"**{winner_name}** takes the victory!",
        color=0x4CAF50,
    )
    embed.add_field(name="\U0001f4b0 Reward", value=reward_text, inline=False)
    await state.channel.send(embed=embed)


@duel.error
async def duel_error(ctx: commands.Context, error: commands.CommandError):
    active_duels.pop(ctx.channel.id, None)
    if isinstance(error, commands.MemberNotFound):
        await ctx.send("\u26a0\ufe0f Usage: `op duel @user [wager]`")
    elif isinstance(error, commands.BadArgument):
        await ctx.send("\u26a0\ufe0f Wager must be a whole number of berries.")
    else:
        raise error


# -----------------------------------------------------------------------
# op team / op team+ / op team-
# -----------------------------------------------------------------------
@bot.command(name="team")
@commands.cooldown(1, 4, commands.BucketType.user)
async def team_view(ctx: commands.Context):
    data = load_data()
    user = get_user(data, str(ctx.author.id))
    save_data(data)

    if not user["team"]:
        await ctx.send(embed=branded_embed(
            "\u2694\ufe0f Your Duel Team",
            f"No team set yet ({user['team_slots']} slots). Add cards with "
            f"`op team+ <character name>`. Until you set one, duels auto-pick "
            f"your best cards.",
            color=0x757575,
        ))
        return

    lines = []
    for inst_id in user["team"]:
        for inst in user["collection"]:
            if inst["inst_id"] == inst_id:
                race_data = RACES.get(inst["race"], RACES["Human"])
                fruit_str = f" {FRUIT_RARITIES[inst['fruit']['rarity']]['emoji']}" if inst.get("fruit") else ""
                total = instance_total_stat(inst)
                lines.append(f"#{inst_id} {RARITIES[inst['rarity']]['emoji']} **{inst['character']}** {race_data['emoji']}{fruit_str} \u2014 \u2694{total:,}")
                break

    embed = branded_embed(
        f"\u2694\ufe0f {ctx.author.display_name}'s Duel Team ({len(user['team'])}/{user['team_slots']})",
        "\n".join(lines),
        color=0xFF5722,
    )

    def team_total(user: dict) -> int:
        total = 0
        for inst_id in user["team"]:
            for inst in user["collection"]:
                if inst["inst_id"] == inst_id:
                    total += instance_total_stat(inst)
                    break
        return total

    embed.add_field(name="Total Power", value=str(team_total(user)), inline=False)
    await ctx.send(embed=embed)

@bot.command(name="team+")
@commands.cooldown(1, 4, commands.BucketType.user)
async def team_add(ctx: commands.Context, *, character_name: str = None):
    if not character_name:
        await ctx.send("\u26a0\ufe0f Usage: `op team+ <character name>`")
        return

    data = load_data()
    user = get_user(data, str(ctx.author.id))

    team_ids = set(user["team"])
    q = character_name.lower().strip()
    resolved = resolve_character_name(character_name)
    ch_name = (resolved if isinstance(resolved, str) else q) if resolved else q
    matches = [inst for inst in user["collection"] if inst["character"].lower() == ch_name.lower() and inst["inst_id"] not in team_ids]
    if not matches:
        owned = any(inst["character"].lower() == ch_name.lower() for inst in user["collection"])
        if not owned:
            await ctx.send(f"\u26a0\ufe0f You don't own **{character_name}** ({ch_name}). Check `op inv`.")
        else:
            await ctx.send(f"\u26a0\ufe0f All your **{ch_name}** cards are already on your team.")
        return
    if len(user["team"]) >= user["team_slots"]:
        await ctx.send(
            f"\u26a0\ufe0f Your team is full ({user['team_slots']}/{user['team_slots']}). "
            f"Remove someone first with `op team- <character name>`."
        )
        return

    target = matches[0]
    user["team"].append(target["inst_id"])
    save_data(data)
    await ctx.send(embed=branded_embed(
        "\u2705 Added to Team",
        f"**{target['character']}** (race: {target['race']}, #{target['inst_id']}) joined your duel team ({len(user['team'])}/{user['team_slots']}).",
        color=0x4CAF50,
    ))

@bot.command(name="team-")
@commands.cooldown(1, 4, commands.BucketType.user)
async def team_remove(ctx: commands.Context, *, character_name: str = None):
    if not character_name:
        await ctx.send("\u26a0\ufe0f Usage: `op team- <character name>`")
        return

    data = load_data()
    user = get_user(data, str(ctx.author.id))

    q = character_name.lower().strip()
    resolved = resolve_character_name(character_name)
    ch_name = (resolved if isinstance(resolved, str) else q) if resolved else q
    for inst_id in user["team"]:
        for inst in user["collection"]:
            if inst["inst_id"] == inst_id and inst["character"].lower() == ch_name.lower():
                user["team"].remove(inst_id)
                save_data(data)
                await ctx.send(embed=branded_embed(
                    "\u2705 Removed from Team",
                    f"**{inst['character']}** (race: {inst['race']}, #{inst_id}) left your duel team ({len(user['team'])}/{user['team_slots']}).",
                    color=0x4CAF50,
                ))
                return

    await ctx.send(f"\u26a0\ufe0f No **{character_name}** card found on your team.")

# -----------------------------------------------------------------------
# op quests / op claim
# -----------------------------------------------------------------------
def quest_target_value(template: dict) -> int:
    return 1 if template["type"] == "rarity_at_least" else template["target"]

@bot.command(name="quests")
async def quests(ctx: commands.Context):
    data = load_data()
    user = get_user(data, str(ctx.author.id))
    ensure_todays_quests(user)
    save_data(data)

    embed = branded_embed(f"\U0001f4dc {ctx.author.display_name}'s Daily Quests", color=0x795548)
    for entry in user["quests"]:
        template = next((q for q in QUEST_POOL if q["id"] == entry["id"]), None)
        if template is None:
            continue
        target_value = quest_target_value(template)
        if entry["claimed"]:
            status = "\u2705 Claimed"
        elif entry["progress"] >= target_value:
            status = "\U0001f7e2 Ready to claim!"
        elif template["type"] == "rarity_at_least":
            status = "Not yet"
        else:
            status = f"{entry['progress']}/{target_value}"
        embed.add_field(
            name=f"`{template['id']}` \u2014 {template['desc']}",
            value=f"Reward: {template['reward']:,} Beli \u2022 Status: {status}",
            inline=False,
        )
    embed.description = "Use `op claim <quest id>` once a quest is ready."
    await ctx.send(embed=embed)

@bot.command(name="claim")
async def claim(ctx: commands.Context, quest_id: str = None):
    if not quest_id:
        await ctx.send("\u26a0\ufe0f Usage: `op claim <quest id>` \u2014 see IDs with `op quests`.")
        return

    data = load_data()
    user = get_user(data, str(ctx.author.id))
    ensure_todays_quests(user)

    entry = find_active_quest(user, quest_id)
    if not entry:
        await ctx.send("\u26a0\ufe0f That quest isn't in your list today. Check `op quests`.")
        return
    if entry["claimed"]:
        await ctx.send("\u26a0\ufe0f You already claimed that one today.")
        return

    template = next((q for q in QUEST_POOL if q["id"] == entry["id"]), None)
    if template is None:
        await ctx.send("\u26a0\ufe0f That quest is no longer available.")
        return
    target_value = quest_target_value(template)
    if entry["progress"] < target_value:
        detail = "Not finished yet." if template["type"] == "rarity_at_least" else f"Not finished yet: {entry['progress']}/{target_value}."
        await ctx.send(f"\u26a0\ufe0f {detail}")
        return

    entry["claimed"] = True
    user["berries"] += template["reward"]
    save_data(data)

    await ctx.send(embed=branded_embed(
        "\u2705 Quest Claimed",
        f"**{template['desc']}** complete! You earned **{template['reward']:,} Beli**.",
        color=0x4CAF50,
    ))

# -----------------------------------------------------------------------
# op shop / op buy
# -----------------------------------------------------------------------
SHOP_EMOJIS = {
    "luck": "\U0001f3b0",
    "key": "\U0001f511",
    "refill": "\U0001f504",
    "teamslot": "\u2795",
    "fastspins": "\u26a1",
    "autoroll": "\U0001f504",
}

def _build_shop_embed(user: dict = None) -> discord.Embed:
    embed = branded_embed("\U0001f6cd\ufe0f OP Shop", color=0x00BFA5)
    lines = []
    if user:
        lines.append(f"\U0001f4b0 Balance: **{user['berries']:,} Beli**\n")
    for key, item in SHOP_ITEMS.items():
        emoji = SHOP_EMOJIS.get(key, "\u2705")
        lines.append(f"{emoji}  **{item['label']}**  \u2014  `{item['cost']:,} Beli`")
        lines.append(f"\u3000\u3000{item['desc']}")
    embed.description = "\n".join(lines)
    embed.set_footer(text=FOOTER_TEXT)
    return embed

class ShopView(discord.ui.View):
    def __init__(self, user_id: int):
        super().__init__(timeout=120)
        self.user_id = user_id

        for idx, (key, item) in enumerate(SHOP_ITEMS.items()):
            emoji = SHOP_EMOJIS.get(key, "\u2705")
            if key in ("luck", "key", "refill"):
                style = discord.ButtonStyle.primary
            elif key == "teamslot":
                style = discord.ButtonStyle.secondary
            elif key == "autoroll":
                style = discord.ButtonStyle.success
            else:
                style = discord.ButtonStyle.primary
            btn = discord.ui.Button(label=f"{item['label']}  \u2014  {item['cost']:,}", style=style, emoji=emoji, row=idx // 3)
            async def callback(interaction: discord.Interaction, _key=key, _item=item):
                if interaction.user.id != self.user_id:
                    await interaction.response.send_message("This isn't your shop!", ephemeral=True)
                    return
                await _process_shop_buy(interaction, _key, _item)
            btn.callback = callback
            self.add_item(btn)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This isn't your shop!", ephemeral=True)
            return False
        return True

async def _process_shop_buy(interaction: discord.Interaction, item_key: str, item: dict):
    uid = interaction.user.id
    if uid in _spin_locks:
        await interaction.response.send_message("\u26a0\ufe0f You're currently spinning! Wait for it to finish before buying.", ephemeral=True)
        return
    data = load_data()
    user = get_user(data, str(interaction.user.id))

    if item_key == "teamslot" and user["team_slots"] >= MAX_TEAM_SIZE_CAP:
        await interaction.response.send_message(
            f"\u26a0\ufe0f You're already at the max team size ({MAX_TEAM_SIZE_CAP}).", ephemeral=True)
        return

    if user["berries"] < item["cost"]:
        await interaction.response.send_message(
            f"\u26a0\ufe0f Not enough Beli. **{item['label']}** costs **{item['cost']:,}**, "
            f"you have **{user['berries']:,}**.", ephemeral=True)
        return

    user["berries"] -= item["cost"]

    if item_key == "luck":
        today = datetime.utcnow().date().isoformat()
        if user.get("luck_date") != today:
            user["luck_date"] = today
            user["luck_seconds_today"] = 0
        seconds = user.get("luck_seconds_today", 0)
        added = SHOP_LUCK_MINUTES * 60
        remaining_today = max(0, 7200 - seconds)
        if added > remaining_today:
            user["berries"] += item["cost"]
            save_data(data)
            await interaction.response.send_message(
                f"\u26a0\ufe0f 2x Luck is capped at **2 hours** per day. You have **{remaining_today // 60} min** left.", ephemeral=True)
            return
        user["luck_seconds_today"] = seconds + added
        now_ts = datetime.utcnow().timestamp()
        base = max(now_ts, user.get("luck_until_utc", 0))
        user["luck_until_utc"] = base + added
        mins_left = (7200 - (seconds + added)) // 60
        result = f"2x Luck active for {SHOP_LUCK_MINUTES} minutes! ({mins_left} min remaining today)"
    elif item_key == "key":
        user["keys"] += 1
        result = f"You now have **{user['keys']}** Key(s)."
    elif item_key == "refill":
        user["spins"] = MAX_SPINS
        result = f"Spins refilled to **{MAX_SPINS}**."
    elif item_key == "teamslot":
        user["team_slots"] += 1
        result = f"Team size is now **{user['team_slots']}**."
    elif item_key == "fastspins":
        user["fast_spins"] = user.get("fast_spins", 0) + SHOP_FASTSPINS_COUNT
        result = f"You now have **{user['fast_spins']}** fast spin(s) queued."
    elif item_key == "autoroll":
        now_ts = datetime.utcnow().timestamp()
        break_until = user.get("autoroll_break_until", 0)
        if break_until > now_ts:
            mins_left = int((break_until - now_ts) // 60)
            await interaction.response.send_message(
                f"\u26a0\ufe0f Auto Roll is on break for another **{mins_left} minutes**. Wait before buying more.",
                ephemeral=True)
            return
        remaining = user.get("autoroll_remaining", 0)
        added = SHOP_AUTOROLL_MINUTES * 60
        new_total = min(remaining + added, SHOP_AUTOROLL_MAX * 60)
        user["autoroll_remaining"] = new_total
        result = f"Auto Roll extended! You have **{new_total // 60} min** stored (max {SHOP_AUTOROLL_MAX} min)."

    save_data(data)
    await interaction.response.send_message(embed=branded_embed(
        f"\u2705 Purchased: {item['label']}",
        f"{result}\nRemaining balance: **{user['berries']:,} Beli**.",
        color=0x4CAF50,
    ), ephemeral=True)

@bot.command(name="shop")
@commands.cooldown(1, 4, commands.BucketType.user)
async def shop(ctx: commands.Context):
    data = load_data()
    user = get_user(data, str(ctx.author.id))
    embed = _build_shop_embed(user)
    await ctx.send(embed=embed, view=ShopView(ctx.author.id))

@bot.command(name="buy")
@commands.cooldown(1, 4, commands.BucketType.user)
async def buy(ctx: commands.Context, item_key: str = None):
    if not item_key or item_key.lower() not in SHOP_ITEMS:
        await ctx.send("\u26a0\ufe0f Usage: `op buy <item>` \u2014 see items with `op shop`.")
        return

    item_key = item_key.lower()
    item = SHOP_ITEMS[item_key]

    uid = ctx.author.id
    if uid in _spin_locks:
        await ctx.send("\u26a0\ufe0f You're currently spinning! Wait for it to finish before buying.")
        return

    data = load_data()
    user = get_user(data, str(ctx.author.id))

    if item_key == "teamslot" and user["team_slots"] >= MAX_TEAM_SIZE_CAP:
        await ctx.send(f"\u26a0\ufe0f You're already at the max team size ({MAX_TEAM_SIZE_CAP}).")
        return

    if user["berries"] < item["cost"]:
        await ctx.send(
            f"\u26a0\ufe0f Not enough Beli. **{item['label']}** costs **{item['cost']:,}**, "
            f"you have **{user['berries']:,}**."
        )
        return

    user["berries"] -= item["cost"]

    if item_key == "luck":
        today = datetime.utcnow().date().isoformat()
        if user.get("luck_date") != today:
            user["luck_date"] = today
            user["luck_seconds_today"] = 0
        seconds = user.get("luck_seconds_today", 0)
        max_seconds = 7200
        added = SHOP_LUCK_MINUTES * 60
        remaining_today = max(0, max_seconds - seconds)
        if added > remaining_today:
            user["berries"] += item["cost"]
            save_data(data)
            await ctx.send(embed=branded_embed(
                "\u26a0\ufe0f Luck Cap Reached",
                f"2x Luck is capped at **2 hours** per day. You have **{remaining_today // 60} min** left today.",
                color=0xFF9800,
            ))
            return
        user["luck_seconds_today"] = seconds + added
        now_ts = datetime.utcnow().timestamp()
        base = max(now_ts, user.get("luck_until_utc", 0))
        user["luck_until_utc"] = base + added
        mins_left = (max_seconds - (seconds + added)) // 60
        result = f"2x Luck active for the next {SHOP_LUCK_MINUTES} minutes! ({mins_left} min remaining today)"
    elif item_key == "key":
        user["keys"] += 1
        result = f"You now have **{user['keys']}** Key(s)."
    elif item_key == "refill":
        user["spins"] = MAX_SPINS
        result = f"Spins refilled to **{MAX_SPINS}**."
    elif item_key == "teamslot":
        user["team_slots"] += 1
        result = f"Team size is now **{user['team_slots']}**."
    elif item_key == "fastspins":
        user["fast_spins"] = user.get("fast_spins", 0) + SHOP_FASTSPINS_COUNT
        result = f"You now have **{user['fast_spins']}** fast spin(s) queued."
    elif item_key == "autoroll":
        now_ts = datetime.utcnow().timestamp()
        break_until = user.get("autoroll_break_until", 0)
        if break_until > now_ts:
            mins_left = int((break_until - now_ts) // 60)
            await ctx.send(f"\u26a0\ufe0f Auto Roll is on break for another **{mins_left} minutes**. Wait before buying more.")
            return
        remaining = user.get("autoroll_remaining", 0)
        added = SHOP_AUTOROLL_MINUTES * 60
        new_total = min(remaining + added, SHOP_AUTOROLL_MAX * 60)
        user["autoroll_remaining"] = new_total
        result = f"Auto Roll extended! You have **{new_total // 60} min** stored (max {SHOP_AUTOROLL_MAX} min)."

    save_data(data)
    await ctx.send(embed=branded_embed(
        f"\u2705 Purchased: {item['label']}",
        f"{result}\nRemaining balance: **{user['berries']:,} Beli**.",
        color=0x4CAF50,
    ))

# -----------------------------------------------------------------------
# op reroll — use a reroll token to randomize a card's race
# -----------------------------------------------------------------------
@bot.command(name="reroll")
@commands.cooldown(1, 4, commands.BucketType.user)
async def reroll_card(ctx: commands.Context, *, card_id: str = None):
    if not card_id:
        await ctx.send("\u26a0\ufe0f Usage: `op reroll <#id>` \u2014 find card IDs with `op inv`.")
        return
    q = card_id.strip()
    if q.startswith("#"):
        q = q[1:]
    try:
        cid = int(q)
    except ValueError:
        await ctx.send("\u26a0\ufe0f Invalid card ID. Use `op inv` to see IDs.")
        return
    data = load_data()
    user = get_user(data, str(ctx.author.id))
    if user.get("reroll_tokens", 0) <= 0:
        await ctx.send("\u26a0\ufe0f You don't have any Race Re-roll tokens! These were available in a previous shop version.")
        return
    target = None
    for inst in user["collection"]:
        if inst.get("inst_id") == cid:
            target = inst
            break
    if not target:
        await ctx.send(f"\u26a0\ufe0f No card found with ID **#{cid}**.")
        return
    new_race = roll_race(target["rarity"])
    old_race = target["race"]
    target["race"] = new_race
    char = character_lookup(target["character"])
    if char:
        new_stats = calculate_instance_stats(char, new_race, target.get("fruit"))
        target["power"] = new_stats["power"]
        target["health"] = new_stats["health"]
        target["speed"] = new_stats["speed"]
    user["reroll_tokens"] -= 1
    save_data(data)
    await ctx.send(embed=branded_embed(
        "\U0001f500 Race Re-rolled",
        f"**{target['character']}** (#**{card_id}**)\n{old_race} \u2192 {new_race}\nStats recalculated!",
        color=0x9C27B0,
    ))

# -----------------------------------------------------------------------
# op auction
# -----------------------------------------------------------------------
@bot.group(name="auction", invoke_without_command=True)
async def auction(ctx: commands.Context):
    await ctx.send(embed=branded_embed(
        "\U0001f528 Auction House",
        "`op auction start <character> <starting bid> <minutes>` \u2014 no quotes needed, e.g.\n"
        "`op auction start Roronoa Zoro 100000 10`\n\n"
        "`op auction bid <id> <amount>` \u2014 bid on an active auction\n"
        "`op auction list` \u2014 see what's active\n"
        "`op auction cancel <id>` \u2014 cancel your own auction (only if no bids yet)",
        color=0x2196F3,
    ))

@auction.command(name="start")
@commands.cooldown(1, 3, commands.BucketType.user)
async def auction_start(ctx: commands.Context, *, args: str = None):
    usage = (
        "\u26a0\ufe0f Usage: `op auction start <character> <starting bid> <minutes>`\n"
        "Example: `op auction start Roronoa Zoro 100000 10` (no quotes needed)."
    )
    if not args:
        await ctx.send(usage)
        return

    parts = args.rsplit(maxsplit=2)
    if len(parts) != 3:
        await ctx.send(usage)
        return
    character_name, bid_str, minutes_str = parts
    try:
        starting_bid = int(bid_str)
        minutes = int(minutes_str)
    except ValueError:
        await ctx.send(usage + "\n(Starting bid and minutes both need to be whole numbers.)")
        return

    if starting_bid <= 0 or minutes <= 0:
        await ctx.send("\u26a0\ufe0f Starting bid and minutes must both be positive.")
        return
    if minutes > 1440:
        await ctx.send("\u26a0\ufe0f Max auction length is 1440 minutes (24 hours).")
        return

    data = load_data()
    user = get_user(data, str(ctx.author.id))

    matches, resolved = collection_search(user, character_name)
    if not matches:
        msg = resolved if isinstance(resolved, str) else f"\u26a0\ufe0f You don't own **{character_name}**. Check `op inv` for exact spelling."
        await ctx.send(msg)
        return

    target = matches[0]
    user["collection"] = [inst for inst in user["collection"] if inst["inst_id"] != target["inst_id"]]
    if target["inst_id"] in user["team"]:
        user["team"].remove(target["inst_id"])
    save_data(data)

    auctions = load_auctions()
    aid = next(_auction_id_counter)
    auctions["_next_id"] = aid + 1
    auctions[str(aid)] = {
        "id": aid,
        "seller_id": str(ctx.author.id),
        "character": target["character"],
        "inst_data": target,
        "current_bid": starting_bid,
        "current_bidder_id": None,
        "channel_id": ctx.channel.id,
        "end_utc": (datetime.utcnow() + timedelta(minutes=minutes)).timestamp(),
        "closed": False,
    }
    save_auctions(auctions)

    char = character_lookup(target["character"])
    embed = branded_embed(
        f"\U0001f528 Auction #{aid} Started",
        f"{rarity_icon(char['rarity'])} **{target['character']}** ({char['rarity']})\n"
        f"Race: {RACES[target['race']]['emoji']} {target['race']}\n"
        f"{'Fruit: ' + target['fruit']['name'] if target.get('fruit') else ''}\n"
        f"Starting bid: **{starting_bid:,} Beli**\nEnds in **{minutes} minutes**.\n\n"
        f"Made a mistake? `op auction cancel {aid}` (only works before anyone bids).",
        color=RARITIES[char["rarity"]]["color"],
    )
    await ctx.send(embed=embed)

@auction.command(name="bid")
@commands.cooldown(1, 2, commands.BucketType.user)
async def auction_bid(ctx: commands.Context, auction_id: int, amount: int):
    auctions = load_auctions()
    entry = auctions.get(str(auction_id))
    if not entry or entry["closed"]:
        await ctx.send("\u26a0\ufe0f That auction doesn't exist or already ended. Check `op auction list`.")
        return
    if str(ctx.author.id) == entry["seller_id"]:
        await ctx.send("\u26a0\ufe0f You can't bid on your own auction.")
        return
    if amount <= entry["current_bid"]:
        await ctx.send(f"\u26a0\ufe0f Bid must be higher than the current **{entry['current_bid']:,} Beli**.")
        return

    data = load_data()
    bidder = get_user(data, str(ctx.author.id))
    if bidder["berries"] < amount:
        await ctx.send(f"\u26a0\ufe0f You need **{amount:,} Beli** for that bid, you have **{bidder['berries']:,}**.")
        return

    bidder["berries"] -= amount
    if entry["current_bidder_id"]:
        previous_bidder = get_user(data, entry["current_bidder_id"])
        previous_bidder["berries"] += entry["current_bid"]
    save_data(data)

    entry["current_bid"] = amount
    entry["current_bidder_id"] = str(ctx.author.id)
    save_auctions(auctions)

    await ctx.send(embed=branded_embed(
        "\U0001f4b8 New Highest Bid",
        f"{ctx.author.mention} bid **{amount:,} Beli** on auction #{auction_id} (**{entry['character']}**).",
        color=0x2196F3,
    ))

@auction.command(name="cancel")
async def auction_cancel(ctx: commands.Context, auction_id: int):
    auctions = load_auctions()
    entry = auctions.get(str(auction_id))
    if not entry or entry["closed"]:
        await ctx.send("\u26a0\ufe0f That auction doesn't exist or already ended.")
        return
    if str(ctx.author.id) != entry["seller_id"]:
        await ctx.send("\u26a0\ufe0f Only the seller can cancel this auction.")
        return
    if entry["current_bidder_id"]:
        await ctx.send("\u26a0\ufe0f Can't cancel \u2014 someone's already bid. Let it run out or wait for it to close.")
        return

    entry["closed"] = True
    save_auctions(auctions)

    data = load_data()
    seller = get_user(data, str(ctx.author.id))
    inst = entry.get("inst_data")
    if inst:
        inst["inst_id"] = seller["_next_inst_id"]
        seller["_next_inst_id"] += 1
        seller["collection"].append(inst)
    save_data(data)

    await ctx.send(embed=branded_embed(
        "\u2705 Auction Cancelled",
        f"**{entry['character']}** was returned to your inventory.",
        color=0x4CAF50,
    ))

@auction.command(name="list")
async def auction_list(ctx: commands.Context):
    auctions = load_auctions()
    active = [a for k, a in auctions.items() if k != "_next_id" and not a["closed"]]
    if not active:
        await ctx.send("No active auctions right now. Start one with `op auction start <character> <bid> <minutes>`.")
        return

    embed = branded_embed("\U0001f528 Active Auctions", color=0x2196F3)
    now = datetime.utcnow().timestamp()
    for a in sorted(active, key=lambda x: x["end_utc"]):
        remaining = fmt_duration(a["end_utc"] - now)
        bidder = f"<@{a['current_bidder_id']}>" if a["current_bidder_id"] else "No bids yet"
        embed.add_field(
            name=f"#{a['id']} \u2014 {a['character']}",
            value=f"Current bid: **{a['current_bid']:,} Beli** ({bidder})\nEnds in: {remaining}",
            inline=False,
        )
    await ctx.send(embed=embed)

@tasks.loop(seconds=30)
async def auction_watcher():
    auctions = load_auctions()
    now = datetime.utcnow().timestamp()
    changed = False

    for key, entry in list(auctions.items()):
        if key == "_next_id" or entry["closed"]:
            continue
        if entry["end_utc"] > now:
            continue

        entry["closed"] = True
        changed = True
        channel = bot.get_channel(entry["channel_id"])
        data = load_data()

        if entry["current_bidder_id"]:
            winner = get_user(data, entry["current_bidder_id"])
            seller = get_user(data, entry["seller_id"])
            inst = entry.get("inst_data")
            if inst:
                inst["inst_id"] = winner["_next_inst_id"]
                winner["_next_inst_id"] += 1
                winner["collection"].append(inst)
            seller["berries"] += entry["current_bid"]
            save_data(data)
            if channel:
                await channel.send(embed=branded_embed(
                    f"\U0001f528 Auction #{entry['id']} Closed",
                    f"<@{entry['current_bidder_id']}> won **{entry['character']}** for "
                    f"**{entry['current_bid']:,} Beli**!",
                    color=0x4CAF50,
                ))
        else:
            seller = get_user(data, entry["seller_id"])
            inst = entry.get("inst_data")
            if inst:
                inst["inst_id"] = seller["_next_inst_id"]
                seller["_next_inst_id"] += 1
                seller["collection"].append(inst)
            save_data(data)
            if channel:
                await channel.send(embed=branded_embed(
                    f"\U0001f528 Auction #{entry['id']} Closed",
                    f"No bids came in \u2014 **{entry['character']}** was returned to <@{entry['seller_id']}>.",
                    color=0x757575,
                ))

    if changed:
        save_auctions(auctions)

# -----------------------------------------------------------------------
# op leaderboard — global + server
# -----------------------------------------------------------------------
@bot.group(name="leaderboard", aliases=["lb"], invoke_without_command=True)
@commands.cooldown(1, 4, commands.BucketType.user)
async def leaderboard_cmd(ctx: commands.Context):
    """Global leaderboard — top collectors by cards, power, berries, and spins."""
    await _show_leaderboard(ctx, server_only=False)

@leaderboard_cmd.command(name="server", aliases=["guild", "local"])
@commands.cooldown(1, 4, commands.BucketType.user)
async def leaderboard_server(ctx: commands.Context):
    """Server-only leaderboard."""
    await _show_leaderboard(ctx, server_only=True)

def _lb_user_total_power(user: dict) -> int:
    return sum(instance_total_stat(i) for i in user.get("collection", []))

def _lb_user_best_card(user: dict) -> int:
    return max((instance_total_stat(i) for i in user.get("collection", [])), default=0)

LEADERBOARD_CATEGORIES = [
    ("cards",  "📦 Most Cards", lambda u: len(u.get("collection", [])), "{:,} cards"),
    ("power",  "⚔️  Highest Total Power", _lb_user_total_power, "{:,} total power"),
    ("best",   "🏆 Best Single Card", _lb_user_best_card, "{:,} best card"),
    ("berries","💰 Richest", lambda u: u.get("berries", 0), "{:,} Beli"),
    ("spins",  "🎰 Most Spins Used", lambda u: u.get("spins_used", 0), "{:,} spins"),
]

async def _show_leaderboard(ctx: commands.Context, server_only: bool):
    data = load_data()
    scope = "this server" if server_only else "global"

    members = None
    if server_only and ctx.guild:
        members = {str(m.id) for m in ctx.guild.members}

    embed = discord.Embed(
        title=f"\U0001f3c6 Leaderboard \u2014 {scope.title()}",
        color=0xFFD700,
    )
    embed.set_footer(text=FOOTER_TEXT)

    for key, label, stat_fn, fmt in LEADERBOARD_CATEGORIES:
        entries = []
        for uid, user in data.items():
            if uid.startswith("_") or not isinstance(user, dict) or "collection" not in user:
                continue
            if members is not None and uid not in members:
                continue
            val = stat_fn(user)
            if val > 0:
                entries.append((uid, val))
        entries.sort(key=lambda x: x[1], reverse=True)
        top = entries[:10]
        medals = ["\U0001f947", "\U0001f948", "\U0001f949"]
        lines = []
        for rank, (uid, val) in enumerate(top, 1):
            prefix = medals[rank - 1] if rank <= 3 else f"`#{rank:<2}`"
            lines.append(f"{prefix} <@{uid}>  \u2014  {fmt.format(val)}")
        if not lines:
            lines.append("No data yet.")
        embed.add_field(
            name=label,
            value="\n".join(lines),
            inline=False,
        )

    await ctx.send(embed=embed)

@tasks.loop(seconds=120)
async def auto_save():
    """Periodically save data to ensure persistence."""
    data = load_data()
    save_data(data)
    save_auctions(load_auctions())

# -----------------------------------------------------------------------
# RUN
# -----------------------------------------------------------------------
if __name__ == "__main__":
    if not TOKEN:
        raise RuntimeError(
            "Set your bot token as an environment variable: set DISCORD_TOKEN=your_token_here"
        )
    bot.run(TOKEN)
