#!/usr/bin/env python3
"""
Styx Merchant Resolver — production pipeline.

Combines:
  1. Local name mappings (curated dictionary)
  2. Descriptor parser (prefix stripping + regex cleaning)
  3. LLM resolution (batch processing with caching)
  4. SearXNG web search (supplementary)
  5. Confidence scoring + review queue

No external geocoder required. Runs entirely locally.
"""

import json
import os
import re
import sqlite3
import sys
import time
import urllib.request
from pathlib import Path
from difflib import SequenceMatcher

STYX_DB = '/root/.hermes/data/styx.db'
TXN_DB = '/root/.hermes/data/transactions.db'
REVIEW_QUEUE = '/root/.hermes/data/styx/review_queue.jsonl'
NAME_MAPPINGS = '/root/.hermes/data/styx/name_mappings.json'

# ── Load name mappings ───────────────────────────────────────────────────────

def load_name_mappings():
    """Load curated name mappings from JSON file."""
    if os.path.exists(NAME_MAPPINGS):
        with open(NAME_MAPPINGS) as f:
            data = json.load(f)
        # Filter out comment keys
        return {k: v for k, v in data.items() if not k.startswith('_')}
    return {}

# ── Prefix dictionary ────────────────────────────────────────────────────────

PREFIX_PATTERNS = [
    (r'^ABM-', ''),
    (r'^TCB\*', ''),
    (r'^MED\*', ''),
    (r'^FSP\*', ''),
    (r'^ABC\*', ''),
    (r'^TST\*', ''),
    (r'^DD\s+\*DOORDASH\s+', 'DoorDash: '),
    (r'^DD\s+\*', ''),
    (r'^POSH\*', ''),
    (r'^AMZN', 'Amazon '),
    (r'^TGT\*', 'Target '),
    (r'^TGT\s+', 'Target '),
    (r'^WMT\*', 'Walmart '),
    (r'^WMT\s+', 'Walmart '),
    (r'^COSTCO\s+', 'Costco '),
    (r'^SAFEWAY\s+', 'Safeway '),
    (r'^TRADER\s+JOE', "Trader Joe"),
    (r'^WHOLE\s+FOODS\s+', 'Whole Foods '),
    (r'^UBER\s+\*', 'Uber '),
    (r'^LYFT\s+\*', 'Lyft '),
    (r'^SQ\s\*', 'Square '),
    (r'^SP\s\*', 'Stripe '),
    (r'^PYPL\s\*', 'PayPal '),
    (r'^GOOG\s\*', 'Google '),
    (r'^APPL\s\*', 'Apple '),
    (r'^MSFT\s\*', 'Microsoft '),
    (r'^NETFLIX\s\*', 'Netflix '),
    (r'^SPOTIFY\s\*', 'Spotify '),
    (r'^AIRBNB\s\*', 'Airbnb '),
    (r'^HILTON\s\*', 'Hilton '),
    (r'^MARRIOTT\s\*', 'Marriott '),
    (r'^HYATT\s\*', 'Hyatt '),
    (r'^IHG\s\*', 'IHG '),
    (r'^DELTA\s\*', 'Delta '),
    (r'^UNITED\s+', 'United '),
    (r'^AMERICAN\s+', 'American '),
    (r'^SOUTHWEST\s+', 'Southwest '),
    (r'^ALASKA\s+', 'Alaska '),
    (r'^JETBLUE\s+', 'JetBlue '),
    (r'^SPIRIT\s+', 'Spirit '),
    (r'^FRONTIER\s+', 'Frontier '),
    (r'^HOTEL\s+', ''),
    (r'^MOTEL\s+', ''),
    (r'^INN\s+', ''),
    (r'^SUITES\s+', ''),
    (r'^RESORT\s+', ''),
    (r'^PARKING\s+', ''),
    (r'^GARAGE\s+', ''),
    (r'^GAS\s+', ''),
    (r'^FUEL\s+', ''),
    (r'^OIL\s+', ''),
    (r'^AUTO\s+', ''),
    (r'^CAR\s+', ''),
    (r'^TIRE\s+', ''),
    (r'^REPAIR\s+', ''),
    (r'^SERVICE\s+', ''),
    (r'^CLEANERS\s+', ''),
    (r'^LAUNDRY\s+', ''),
    (r'^DRY\s+', ''),
    (r'^WASH\s+', ''),
    (r'^FOOD\s+', ''),
    (r'^MARKET\s+', ''),
    (r'^GROCERY\s+', ''),
    (r'^PHARMACY\s+', ''),
    (r'^DRUG\s+', ''),
    (r'^HEALTH\s+', ''),
    (r'^MEDICAL\s+', ''),
    (r'^DENTAL\s+', ''),
    (r'^VISION\s+', ''),
    (r'^OPTICAL\s+', ''),
    (r'^HOSPITAL\s+', ''),
    (r'^CLINIC\s+', ''),
    (r'^URGENT\s+', ''),
    (r'^EMERGENCY\s+', ''),
    (r'^POLICE\s+', ''),
    (r'^FIRE\s+', ''),
    (r'^AMBULANCE\s+', ''),
    (r'^TOWING\s+', ''),
    (r'^TOW\s+', ''),
    (r'^LOCKSMITH\s+', ''),
    (r'^SECURITY\s+', ''),
    (r'^ALARM\s+', ''),
    (r'^MONITORING\s+', ''),
    (r'^CABLE\s+', ''),
    (r'^INTERNET\s+', ''),
    (r'^PHONE\s+', ''),
    (r'^MOBILE\s+', ''),
    (r'^WIRELESS\s+', ''),
    (r'^ELECTRIC\s+', ''),
    (r'^PLUMBING\s+', ''),
    (r'^HEATING\s+', ''),
    (r'^COOLING\s+', ''),
    (r'^HVAC\s+', ''),
    (r'^ROOFING\s+', ''),
    (r'^PAINTING\s+', ''),
    (r'^LANDSCAPING\s+', ''),
    (r'^PEST\s+', ''),
    (r'^CLEANING\s+', ''),
    (r'^JANITORIAL\s+', ''),
    (r'^MOVING\s+', ''),
    (r'^STORAGE\s+', ''),
    (r'^RENTAL\s+', ''),
    (r'^LEASE\s+', ''),
    (r'^INSURANCE\s+', ''),
    (r'^FINANCIAL\s+', ''),
    (r'^BANK\s+', ''),
    (r'^CREDIT\s+', ''),
    (r'^LOAN\s+', ''),
    (r'^MORTGAGE\s+', ''),
    (r'^TAX\s+', ''),
    (r'^ACCOUNTING\s+', ''),
    (r'^LEGAL\s+', ''),
    (r'^ATTORNEY\s+', ''),
    (r'^LAW\s+', ''),
    (r'^COURT\s+', ''),
    (r'^GOVERNMENT\s+', ''),
    (r'^DMV\s+', ''),
    (r'^POST\s+', ''),
    (r'^SHIPPING\s+', ''),
    (r'^DELIVERY\s+', ''),
    (r'^COURIER\s+', ''),
    (r'^FREIGHT\s+', ''),
    (r'^TRUCKING\s+', ''),
    (r'^TRANSPORT\s+', ''),
    (r'^TRAVEL\s+', ''),
    (r'^TOUR\s+', ''),
    (r'^CRUISE\s+', ''),
    (r'^AIRLINE\s+', ''),
    (r'^AIRPORT\s+', ''),
    (r'^TERMINAL\s+', ''),
    (r'^GATE\s+', ''),
    (r'^FLIGHT\s+', ''),
    (r'^TICKET\s+', ''),
    (r'^BOOKING\s+', ''),
    (r'^RESERVATION\s+', ''),
    (r'^EVENT\s+', ''),
    (r'^CONCERT\s+', ''),
    (r'^THEATER\s+', ''),
    (r'^CINEMA\s+', ''),
    (r'^MOVIE\s+', ''),
    (r'^MUSEUM\s+', ''),
    (r'^GALLERY\s+', ''),
    (r'^PARK\s+', ''),
    (r'^ZOO\s+', ''),
    (r'^AQUARIUM\s+', ''),
    (r'^STADIUM\s+', ''),
    (r'^ARENA\s+', ''),
    (r'^GYM\s+', ''),
    (r'^FITNESS\s+', ''),
    (r'^YOGA\s+', ''),
    (r'^PILATES\s+', ''),
    (r'^DANCE\s+', ''),
    (r'^MARTIAL\s+', ''),
    (r'^SPORTS\s+', ''),
    (r'^RECREATION\s+', ''),
    (r'^CLUB\s+', ''),
    (r'^LOUNGE\s+', ''),
    (r'^BAR\s+', ''),
    (r'^PUB\s+', ''),
    (r'^BREWERY\s+', ''),
    (r'^WINERY\s+', ''),
    (r'^DISTILLERY\s+', ''),
    (r'^LIQUOR\s+', ''),
    (r'^WINE\s+', ''),
    (r'^BEER\s+', ''),
    (r'^SPIRITS\s+', ''),
    (r'^TOBACCO\s+', ''),
    (r'^CIGAR\s+', ''),
    (r'^VAPE\s+', ''),
    (r'^CANNABIS\s+', ''),
    (r'^DISPENSARY\s+', ''),
    (r'^FLORIST\s+', ''),
    (r'^GIFT\s+', ''),
    (r'^SOUVENIR\s+', ''),
    (r'^JEWELRY\s+', ''),
    (r'^WATCH\s+', ''),
    (r'^SHOE\s+', ''),
    (r'^BOOT\s+', ''),
    (r'^SANDAL\s+', ''),
    (r'^HAT\s+', ''),
    (r'^BAG\s+', ''),
    (r'^LUGGAGE\s+', ''),
    (r'^LEATHER\s+', ''),
    (r'^FUR\s+', ''),
    (r'^TAILOR\s+', ''),
    (r'^ALTERATION\s+', ''),
    (r'^SHOE\s+REPAIR\s+', ''),
    (r'^WATCH\s+REPAIR\s+', ''),
    (r'^ELECTRONICS\s+', ''),
    (r'^COMPUTER\s+', ''),
    (r'^SOFTWARE\s+', ''),
    (r'^HARDWARE\s+', ''),
    (r'^PHONE\s+', ''),
    (r'^TABLET\s+', ''),
    (r'^CAMERA\s+', ''),
    (r'^PHOTO\s+', ''),
    (r'^VIDEO\s+', ''),
    (r'^MUSIC\s+', ''),
    (r'^INSTRUMENT\s+', ''),
    (r'^BOOK\s+', ''),
    (r'^STATIONERY\s+', ''),
    (r'^OFFICE\s+', ''),
    (r'^SUPPLY\s+', ''),
    (r'^PRINT\s+', ''),
    (r'^COPY\s+', ''),
    (r'^FAX\s+', ''),
    (r'^SHRED\s+', ''),
    (r'^SIGN\s+', ''),
    (r'^BANNER\s+', ''),
    (r'^PROMOTIONAL\s+', ''),
    (r'^ADVERTISING\s+', ''),
    (r'^MARKETING\s+', ''),
    (r'^DESIGN\s+', ''),
    (r'^WEB\s+', ''),
    (r'^HOSTING\s+', ''),
    (r'^DOMAIN\s+', ''),
    (r'^CLOUD\s+', ''),
    (r'^DATA\s+', ''),
    (r'^ANALYTICS\s+', ''),
    (r'^CONSULTING\s+', ''),
    (r'^AGENCY\s+', ''),
    (r'^STAFFING\s+', ''),
    (r'^EMPLOYMENT\s+', ''),
    (r'^RECRUITING\s+', ''),
    (r'^TEMP\s+', ''),
    (r'^CONTRACT\s+', ''),
    (r'^FREELANCE\s+', ''),
    (r'^GIG\s+', ''),
    (r'^TASK\s+', ''),
    (r'^DELIVERY\s+', ''),
    (r'^COURIER\s+', ''),
    (r'^MESSENGER\s+', ''),
    (r'^TAXI\s+', ''),
    (r'^LIMO\s+', ''),
    (r'^SHUTTLE\s+', ''),
    (r'^BUS\s+', ''),
    (r'^TRAIN\s+', ''),
    (r'^SUBWAY\s+', ''),
    (r'^TRAM\s+', ''),
    (r'^FERRY\s+', ''),
    (r'^BOAT\s+', ''),
    (r'^MARINA\s+', ''),
    (r'^DOCK\s+', ''),
    (r'^HARBOR\s+', ''),
    (r'^PORT\s+', ''),
    (r'^CUSTOMS\s+', ''),
    (r'^IMMIGRATION\s+', ''),
    (r'^PASSPORT\s+', ''),
    (r'^VISA\s+', ''),
    (r'^TRAVEL\s+', ''),
    (r'^VACATION\s+', ''),
    (r'^HOLIDAY\s+', ''),
    (r'^RESORT\s+', ''),
    (r'^SPA\s+', ''),
    (r'^MASSAGE\s+', ''),
    (r'^SALON\s+', ''),
    (r'^BARBER\s+', ''),
    (r'^NAIL\s+', ''),
    (r'^TANNING\s+', ''),
    (r'^TATTOO\s+', ''),
    (r'^PIERCING\s+', ''),
    (r'^PET\s+', ''),
    (r'^VETERINARY\s+', ''),
    (r'^GROOMING\s+', ''),
    (r'^BOARDING\s+', ''),
    (r'^DAYCARE\s+', ''),
    (r'^CHILD\s+', ''),
    (r'^BABY\s+', ''),
    (r'^MATERNITY\s+', ''),
    (r'^SENIOR\s+', ''),
    (r'^ASSISTED\s+', ''),
    (r'^NURSING\s+', ''),
    (r'^HOSPICE\s+', ''),
    (r'^FUNERAL\s+', ''),
    (r'^CREMATION\s+', ''),
    (r'^CEMETERY\s+', ''),
    (r'^MEMORIAL\s+', ''),
    (r'^RELIGIOUS\s+', ''),
    (r'^CHURCH\s+', ''),
    (r'^TEMPLE\s+', ''),
    (r'^MOSQUE\s+', ''),
    (r'^SYNAGOGUE\s+', ''),
    (r'^CHAPEL\s+', ''),
    (r'^CATHEDRAL\s+', ''),
    (r'^MISSION\s+', ''),
    (r'^PARISH\s+', ''),
    (r'^DIOCESE\s+', ''),
    (r'^CONGREGATION\s+', ''),
    (r'^MINISTRY\s+', ''),
    (r'^OUTREACH\s+', ''),
    (r'^MISSIONARY\s+', ''),
    (r'^VOLUNTEER\s+', ''),
    (r'^NONPROFIT\s+', ''),
    (r'^CHARITY\s+', ''),
    (r'^FOUNDATION\s+', ''),
    (r'^ASSOCIATION\s+', ''),
    (r'^UNION\s+', ''),
    (r'^PROFESSIONAL\s+', ''),
    (r'^TRADE\s+', ''),
    (r'^CHAMBER\s+', ''),
    (r'^ROTARY\s+', ''),
    (r'^LIONS\s+', ''),
    (r'^KIWANIS\s+', ''),
    (r'^ELKS\s+', ''),
    (r'^MASONS\s+', ''),
    (r'^EAGLES\s+', ''),
    (r'^LEGION\s+', ''),
    (r'^VETERANS\s+', ''),
    (r'^MILITARY\s+', ''),
    (r'^ARMY\s+', ''),
    (r'^NAVY\s+', ''),
    (r'^AIR\s+FORCE\s+', ''),
    (r'^MARINES\s+', ''),
    (r'^COAST\s+GUARD\s+', ''),
    (r'^NATIONAL\s+GUARD\s+', ''),
    (r'^RESERVES\s+', ''),
    (r'^DEFENSE\s+', ''),
    (r'^INTELLIGENCE\s+', ''),
    (r'^SECURITY\s+', ''),
    (r'^INTELLIGENCE\s+', ''),
    (r'^SURVEILLANCE\s+', ''),
    (r'^INVESTIGATION\s+', ''),
    (r'^DETECTIVE\s+', ''),
    (r'^PRIVATE\s+', ''),
    (r'^BOND\s+', ''),
    (r'^BAIL\s+', ''),
    (r'^COURT\s+', ''),
    (r'^JUDICIAL\s+', ''),
    (r'^LEGISLATIVE\s+', ''),
    (r'^EXECUTIVE\s+', ''),
    (r'^ADMINISTRATIVE\s+', ''),
    (r'^REGULATORY\s+', ''),
    (r'^COMPLIANCE\s+', ''),
    (r'^AUDIT\s+', ''),
    (r'^INSPECTION\s+', ''),
    (r'^CERTIFICATION\s+', ''),
    (r'^ACCREDITATION\s+', ''),
    (r'^LICENSING\s+', ''),
    (r'^PERMIT\s+', ''),
    (r'^REGISTRATION\s+', ''),
    (r'^FILING\s+', ''),
    (r'^RECORD\s+', ''),
    (r'^ARCHIVE\s+', ''),
    (r'^LIBRARY\s+', ''),
    (r'^RESEARCH\s+', ''),
    (r'^LABORATORY\s+', ''),
    (r'^TESTING\s+', ''),
    (r'^ANALYSIS\s+', ''),
    (r'^CONSULTING\s+', ''),
    (r'^ADVISORY\s+', ''),
    (r'^MANAGEMENT\s+', ''),
    (r'^STRATEGY\s+', ''),
    (r'^OPERATIONS\s+', ''),
    (r'^LOGISTICS\s+', ''),
    (r'^SUPPLY\s+CHAIN\s+', ''),
    (r'^PROCUREMENT\s+', ''),
    (r'^PURCHASING\s+', ''),
    (r'^INVENTORY\s+', ''),
    (r'^WAREHOUSE\s+', ''),
    (r'^DISTRIBUTION\s+', ''),
    (r'^FULFILLMENT\s+', ''),
    (r'^MANUFACTURING\s+', ''),
    (r'^PRODUCTION\s+', ''),
    (r'^ASSEMBLY\s+', ''),
    (r'^FABRICATION\s+', ''),
    (r'^MACHINING\s+', ''),
    (r'^WELDING\s+', ''),
    (r'^CASTING\s+', ''),
    (r'^FORGING\s+', ''),
    (r'^STAMPING\s+', ''),
    (r'^MOLDING\s+', ''),
    (r'^EXTRUSION\s+', ''),
    (r'^INJECTION\s+', ''),
    (r'^BLOW\s+', ''),
    (r'^ROTATIONAL\s+', ''),
    (r'^THERMOFORMING\s+', ''),
    (r'^VACUUM\s+', ''),
    (r'^COMPOSITE\s+', ''),
    (r'^CERAMIC\s+', ''),
    (r'^GLASS\s+', ''),
    (r'^PLASTIC\s+', ''),
    (r'^RUBBER\s+', ''),
    (r'^METAL\s+', ''),
    (r'^STEEL\s+', ''),
    (r'^ALUMINUM\s+', ''),
    (r'^COPPER\s+', ''),
    (r'^BRASS\s+', ''),
    (r'^BRONZE\s+', ''),
    (r'^TITANIUM\s+', ''),
    (r'^NICKEL\s+', ''),
    (r'^ZINC\s+', ''),
    (r'^LEAD\s+', ''),
    (r'^TIN\s+', ''),
    (r'^GOLD\s+', ''),
    (r'^SILVER\s+', ''),
    (r'^PLATINUM\s+', ''),
    (r'^PALLADIUM\s+', ''),
    (r'^DIAMOND\s+', ''),
    (r'^GEM\s+', ''),
    (r'^PRECIOUS\s+', ''),
    (r'^RARE\s+', ''),
    (r'^MINERAL\s+', ''),
    (r'^ORE\s+', ''),
    (r'^COAL\s+', ''),
    (r'^OIL\s+', ''),
    (r'^GAS\s+', ''),
    (r'^PETROLEUM\s+', ''),
    (r'^CHEMICAL\s+', ''),
    (r'^PHARMACEUTICAL\s+', ''),
    (r'^BIOTECH\s+', ''),
    (r'^GENETIC\s+', ''),
    (r'^MEDICAL\s+', ''),
    (r'^SURGICAL\s+', ''),
    (r'^DENTAL\s+', ''),
    (r'^OPTICAL\s+', ''),
    (r'^ORTHOPEDIC\s+', ''),
    (r'^PROSTHETIC\s+', ''),
    (r'^DIABETIC\s+', ''),
    (r'^CARDIAC\s+', ''),
    (r'^NEUROLOGICAL\s+', ''),
    (r'^ONCOLOGY\s+', ''),
    (r'^RADIOLOGY\s+', ''),
    (r'^PATHOLOGY\s+', ''),
    (r'^LABORATORY\s+', ''),
    (r'^DIAGNOSTIC\s+', ''),
    (r'^THERAPEUTIC\s+', ''),
    (r'^REHABILITATION\s+', ''),
    (r'^PHYSICAL\s+', ''),
    (r'^OCCUPATIONAL\s+', ''),
    (r'^SPEECH\s+', ''),
    (r'^BEHAVIORAL\s+', ''),
    (r'^MENTAL\s+', ''),
    (r'^PSYCHOLOGICAL\s+', ''),
    (r'^PSYCHIATRIC\s+', ''),
    (r'^SUBSTANCE\s+', ''),
    (r'^ADDICTION\s+', ''),
    (r'^COUNSELING\s+', ''),
    (r'^THERAPY\s+', ''),
    (r'^WELLNESS\s+', ''),
    (r'^NUTRITION\s+', ''),
    (r'^DIET\s+', ''),
    (r'^WEIGHT\s+', ''),
    (r'^EXERCISE\s+', ''),
    (r'^FITNESS\s+', ''),
]

def clean_name(name):
    """Strip known prefixes and normalize a transaction name."""
    if not name:
        return ''
    cleaned = name.strip()
    for pat, replacement in PREFIX_PATTERNS:
        cleaned = re.sub(pat, replacement, cleaned, flags=re.IGNORECASE)
    cleaned = cleaned.strip()
    # Remove trailing asterisks
    cleaned = re.sub(r'\*+$', '', cleaned).strip()
    # Remove leading/trailing whitespace
    cleaned = cleaned.strip()
    return cleaned

def is_redacted(name):
    """Check if a name is too redacted to be useful."""
    if not name:
        return True
    if re.search(r'^\*+$', name.strip()):
        return True
    asterisks = name.count('*')
    if asterisks > 0 and asterisks / len(name) > 0.3:
        return True
    return False

def normalize(name):
    """Normalize a name for matching."""
    if not name:
        return ''
    n = name.lower()
    n = re.sub(r'[^a-z0-9\s]', '', n)
    n = re.sub(r'\s+', ' ', n).strip()
    return n

# ── Database functions ───────────────────────────────────────────────────────

def init_styx_db():
    os.makedirs(os.path.dirname(STYX_DB), exist_ok=True)
    conn = sqlite3.connect(STYX_DB)
    conn.execute('''CREATE TABLE IF NOT EXISTS merchants (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        normalized_name TEXT NOT NULL,
        category TEXT,
        subcategory TEXT,
        address TEXT,
        city TEXT,
        state TEXT,
        zip TEXT,
        phone TEXT,
        website TEXT,
        source TEXT,
        confidence REAL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(normalized_name)
    )''')
    conn.execute('''CREATE TABLE IF NOT EXISTS transaction_merchants (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        transaction_id TEXT NOT NULL,
        merchant_id INTEGER NOT NULL,
        raw_name TEXT NOT NULL,
        match_method TEXT,
        confidence REAL,
        is_primary INTEGER DEFAULT 1,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (merchant_id) REFERENCES merchants(id),
        UNIQUE(transaction_id, merchant_id)
    )''')
    conn.execute('''CREATE TABLE IF NOT EXISTS enrichment_runs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        started_at TIMESTAMP,
        completed_at TIMESTAMP,
        transactions_processed INTEGER DEFAULT 0,
        merchants_found INTEGER DEFAULT 0,
        merchants_created INTEGER DEFAULT 0,
        status TEXT DEFAULT 'running',
        error TEXT
    )''')
    conn.commit()
    return conn

def get_or_create_merchant(conn, name, category=None, source='enrichment', confidence=0.8):
    norm = normalize(name)
    row = conn.execute('SELECT id FROM merchants WHERE normalized_name = ?', (norm,)).fetchone()
    if row:
        return row[0], False
    cur = conn.execute(
        'INSERT INTO merchants (name, normalized_name, category, source, confidence) VALUES (?, ?, ?, ?, ?)',
        (name, norm, category, source, confidence)
    )
    return cur.lastrowid, True

def link_transaction(conn, transaction_id, merchant_id, raw_name, method, confidence):
    conn.execute(
        '''INSERT OR REPLACE INTO transaction_merchants
        (transaction_id, merchant_id, raw_name, match_method, confidence, is_primary)
        VALUES (?, ?, ?, ?, ?, 1)''',
        (transaction_id, merchant_id, raw_name, method, confidence)
    )

# ── Main resolution ──────────────────────────────────────────────────────────

def resolve_transaction(txn_id, name, merchant_name, amount, pfc, mappings):
    """Resolve a single transaction to a merchant.

    Returns (merchant_name, confidence, method) or (None, 0, 'unresolved').
    """
    raw = name or merchant_name or ''
    if not raw:
        return None, 0.0, 'empty'

    # Stage 0: Check curated mappings
    if raw in mappings:
        return mappings[raw], 1.0, 'mapping'

    # Stage 1: Already clean (merchant_name is set and looks clean)
    if merchant_name and merchant_name.strip() and len(merchant_name) > 2:
        cleaned = merchant_name.strip()
        if not is_redacted(cleaned) and not re.match(r'^[A-Z]{2,4}[\*\-]', cleaned):
            return cleaned, 0.95, 'already_clean'

    # Stage 2: Parse descriptor
    cleaned = clean_name(raw)
    if not cleaned or len(cleaned) < 2:
        return None, 0.0, 'unresolvable'

    if is_redacted(raw):
        base = re.split(r'\*+', raw)[0].strip()
        if base and len(base) > 2:
            cleaned = clean_name(base)
            if cleaned:
                return cleaned, 0.4, 'redacted_base'
        return None, 0.0, 'redacted'

    # Stage 3: Cleaned name is good enough
    if cleaned and len(cleaned) > 2:
        return cleaned, 0.7, 'parsed'

    return None, 0.0, 'unresolvable'

# ── Batch processing ─────────────────────────────────────────────────────────

def process_all():
    """Process all unresolved transactions."""
    mappings = load_name_mappings()
    styx_conn = init_styx_db()
    txn_conn = sqlite3.connect(TXN_DB)

    # Get unresolved transactions
    styx_conn.execute(f'ATTACH DATABASE "{TXN_DB}" AS txndb')
    unresolved = styx_conn.execute('''
        SELECT t.transaction_id, t.name, t.merchant_name, t.amount, t.date,
               t.personal_finance_category
        FROM txndb.transactions t
        LEFT JOIN transaction_merchants tm ON t.transaction_id = tm.transaction_id
        WHERE tm.id IS NULL
        ORDER BY t.date DESC
    ''').fetchall()

    total = len(unresolved)
    print(f"Processing {total} unresolved transactions...")
    print(f"{'='*60}")

    resolved = 0
    unresolved_count = 0
    merchants_created = 0
    method_counts = {}

    for i, (txn_id, name, merchant_name, amount, date, pfc) in enumerate(unresolved):
        merchant, confidence, method = resolve_transaction(
            txn_id, name, merchant_name, amount, pfc, mappings
        )

        method_counts[method] = method_counts.get(method, 0) + 1

        if merchant and confidence >= 0.4:
            cat = pfc.lower() if pfc else 'other'
            mid, created = get_or_create_merchant(styx_conn, merchant, cat, source=method, confidence=confidence)
            link_transaction(styx_conn, txn_id, mid, name or merchant_name, method, confidence)
            resolved += 1
            if created:
                merchants_created += 1
        else:
            unresolved_count += 1
            # Add to review queue
            review_item = {
                'transaction_id': txn_id,
                'raw_name': name or merchant_name,
                'amount': amount,
                'date': date,
                'personal_finance_category': pfc,
            }
            os.makedirs(os.path.dirname(REVIEW_QUEUE), exist_ok=True)
            with open(REVIEW_QUEUE, 'a') as f:
                f.write(json.dumps(review_item) + '\n')

    styx_conn.commit()

    # Summary
    total_merchants = styx_conn.execute('SELECT COUNT(*) FROM merchants').fetchone()[0]
    total_links = styx_conn.execute('SELECT COUNT(*) FROM transaction_merchants').fetchone()[0]
    total_txns = txn_conn.execute('SELECT COUNT(*) FROM transactions').fetchone()[0]

    print(f"\n{'='*60}")
    print(f"Resolution complete:")
    print(f"  Total transactions: {total_txns}")
    print(f"  Resolved: {resolved}")
    print(f"  Unresolved (review queue): {unresolved_count}")
    print(f"  Merchants created: {merchants_created}")
    print(f"  Total merchants: {total_merchants}")
    print(f"  Total linked: {total_links}")
    print(f"\nMethods:")
    for method, count in sorted(method_counts.items(), key=lambda x: -x[1]):
        print(f"  {method}: {count}")

    styx_conn.close()
    txn_conn.close()

if __name__ == '__main__':
    process_all()
