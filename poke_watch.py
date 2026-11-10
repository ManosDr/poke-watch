#!/usr/bin/env python3
"""
poke_watch.py: checks Greek retailers for English Pokémon TCG "30th Celebration" sealed
products at retail price and pushes an alert to your phone (ntfy and/or Telegram) when
one is buyable under your price cap.

  python poke_watch.py --notify-test   send a test push to your phone
  python poke_watch.py --test          dry run: show what every shop returns, send nothing
  python poke_watch.py                 one real check (this is what the scheduler runs)
  python poke_watch.py --every 60      stay running and check every 60 minutes
  python poke_watch.py --shop "Public,Kaissa"        only these shops (--skip-shop: all but these)

Needs:  pip install playwright    then    python -m playwright install chromium
Secrets come from environment variables or a poke_watch.env file next to this script:
  NTFY_TOPIC=some-long-random-topic-name
  TELEGRAM_BOT_TOKEN=...  and  TELEGRAM_CHAT_ID=...   (optional)
"""
from __future__ import annotations

import argparse
import json
import os
import random
import re
import sys
import time
import unicodedata
import urllib.parse
import urllib.request
from urllib.parse import urlparse
from datetime import datetime
from pathlib import Path

# =============================== SETTINGS ===============================

SHOPS = [
    # "search" is the site's search URL with {q} where the words go. If it stops working, the
    # script types into the site's own search box instead and remembers the URL that worked.
    # "product_link" is what this shop's product URLs look like (keeps out books, figures, etc.).
    # "watch" lists product pages to open on every run, even when the shop's search hides them.
    {
        "name": "Public",
        "home": "https://www.public.gr/",
        "search": "https://www.public.gr/search?q={q}",
        "product_link": r"/product/kids-and-toys/",
        "skip_link": r"/MRK\d+",           # Public Partner / marketplace listings (third-party sellers)
        "seller": r"public",               # if a page names a seller, it has to be Public itself
        # Public's own 30th Celebration pages. Its search hides sold-out products, so these are
        # opened directly. The number after each is Public's price on 7 Nov 2026.
        "watch": [
            "https://www.public.gr/product/kids-and-toys/trading-collectable-cards/pokemon-tcg-30th-celebration-elite-trainer-box/2159065",  # 89.90
            "https://www.public.gr/product/kids-and-toys/trading-collectable-cards/pokemon-tcg-30th-celebration-ultra-premium-collection--tuxaia-epilogi-1-temaxio/2159079",  # 299
            "https://www.public.gr/product/kids-and-toys/trading-collectable-cards/pokemon-tcg-30th-celebration-ditto-premium-collection-1-temaxio/2159078",  # 74.90
            "https://www.public.gr/product/kids-and-toys/trading-collectable-cards/pokemon-tcg-binder-collection--30th-celebration/2159063",  # 69.90
            "https://www.public.gr/product/kids-and-toys/trading-collectable-cards/asmodee-pokmon-bst-bundle-30th-20-min-card-game-collectible/2159070",  # 44.90 Booster Bundle
            "https://www.public.gr/product/kids-and-toys/trading-collectable-cards/pokemon-tcg-30th-celebration-ex-box/2159066",  # 39.90
            "https://www.public.gr/product/kids-and-toys/trading-collectable-cards/pokmon-tcg-30th-celebration--ex-tin-sylveongreninja/2159067",  # 39.90
            "https://www.public.gr/product/kids-and-toys/trading-collectable-cards/pokmon-tcg-30th-celebration--poster-collection/2159064",  # 34.89
            "https://www.public.gr/product/kids-and-toys/trading-collectable-cards/asmodee-pokmon-tech-sticker-coll-30th-2-20-min-card-game-collectible/2159062",  # 34.89
            "https://www.public.gr/product/kids-and-toys/trading-collectable-cards/pokmon-30th-celebration-mini-tin--tuxaia-epilogi/2159069",  # 24.90
            "https://www.public.gr/product/kids-and-toys/trading-collectable-cards/pokemon-tcg-30th-celebration-2-pack-blister/2159068",  # 19.99
        ],
    },
    {
        "name": "Kaissa",                  # the Greek Pokémon TCG distributor's own shop
        "home": "https://kaissagames.com/b2c_gr/",
        "search": "https://kaissagames.com/b2c_gr/catalogsearch/result/?q={q}",
        "extra_pages": ["https://kaissagames.com/b2c_gr/paichnidia-me-sullektikes-kartes/pokemon.html"],
        "product_link": r"/b2c_gr/.+\.html",
    },
    {
        "name": "Nerdom",
        "home": "https://www.nerdom.gr/",
        "search": "https://www.nerdom.gr/el/search?keyword={q}",
        "product_link": r"/el/[^/]+\.html$",
    },
    {
        "name": "efantasy",
        "home": "https://www.efantasy.gr/",
        "search": "https://www.efantasy.gr/en/products/search={q}/sort=id-desc/c-651-pokemon-tcg",
        "extra_pages": ["https://www.efantasy.gr/en/products/pokemon-tcg/sc-3510-30th-celebration"],
        "product_link": r"/products/pokemon-tcg/\d+-",
    },
    {
        "name": "iConnect",
        "home": "https://iconnectstore.gr/",
        "search": "https://iconnectstore.gr/index.php?route=product/search&search={q}",
        "product_link": r"-\d{4,}(?:[?#]|$)|route=product/product",
    },
    {
        "name": "Moustakas Toys",
        "home": "https://www.moustakastoys.gr/",
        "search": "https://www.moustakastoys.gr/apotelesmata-anazitisis/?profile=Default&section=Products&q={q}",
        "product_link": r"/paixnidia-me-kartes/|pokemon",
    },
    {
        # Price comparison listing: covers dozens of Greek shops at once (Roll n Trade, Toys24,
        # Gamescom, OtakuStore, Toys4u, PerfecToys...). The price cap decides what's "retail".
        # Sorted cheapest first; its data can lag the shops by an hour or so.
        "name": "BestPrice",
        "type": "bestprice",
        "home": "https://www.bestprice.gr/",
        "search": "https://www.bestprice.gr/cat/3024/kartes.html?q={q}&o=2",
        "pages": 2,
        "new_listings": False,             # too many shops for "new listing" pings
    },
]

QUERIES = ["30th celebration", "30th anniversary"]

# Other product names to watch as well, e.g. ["pokemon day 2026", "first partner illustration"]
EXTRA_PRODUCTS: list[str] = []

# Highest price (EUR) that still counts as retail, per product type. First match wins.
# Retail prices on 7 Nov 2026, from Public's own 30th Celebration pages: ETB 89.90,
# Ultra-Premium Collection 299, Ditto Premium Collection 74.90, Binder Collection 69.90,
# Booster Bundle 44.90, ex Box and ex Tin 39.90, Poster and Tech Sticker Collection 34.89,
# Mini Tin 24.90, 2-Pack Blister 19.99. Kaissa (the distributor): ETB 90, Ditto 65, Figure
# Collection 50, Battle Deck 35. Caps sit 5-10% above those. Resellers ask 1.5x to 2x (ETB 150+).
PRICE_CAPS = [
    (r"ultra[\s-]*premium|\bupc\b",            "Ultra-Premium Collection", 320),  # Public 299
    (r"elite\s*trainer|\betb\b",               "Elite Trainer Box",         95),  # Public 89.90, Kaissa 90
    (r"booster\s*bundle|\bbundle\b",           "Booster Bundle",            50),  # Public 44.90
    (r"binder",                                "Binder Collection",         75),  # Public 69.90
    (r"figure",                                "Figure Collection",         55),  # Kaissa 50
    (r"premium\s*collection|ditto",            "Premium Collection",        80),  # Public 74.90, Kaissa 65
    (r"poster",                                "Poster Collection",         38),  # Public 34.89
    (r"sticker",                               "Tech Sticker Collection",   38),  # Public 34.89
    (r"knock\s*-?\s*out",                      "Knock Out Collection",      27),  # estimate, 2 packs
    (r"mini\s*tin",                            "Mini Tin",                  27),  # Public 24.90
    (r"\btin\b",                               "ex Tin",                    44),  # Public 39.90
    (r"blister|\b[23][\s-]*pack",              "Blister pack",              22),  # Public 2-Pack 19.99
    (r"battle\s*deck|\bdeck\b",                "Battle Deck",               38),  # Kaissa 35
    (r"\bex\s*box\b|sylveon|greninja|\bbox\b", "ex Box",                    44),  # Public 39.90
    (r"booster|φακελακ|\bpack\b",              "Booster Pack",               9),  # estimate (normal packs 5.99)
]
DEFAULT_CAP = 60

# English cards only: product names in other languages (or saying they're Japanese, Chinese...)
# are skipped, and so are pages that say their language is one of these.
NOT_ENGLISH = (r"japan|jpn|\bjp\b|ιαπων|chinese|κινεζ|\bcn\b|korean|κορεα|\bkr\b|german|deutsch|γερμαν|"
               r"french|francais|γαλλ|italian|ιταλ|spanish|espanol|ισπαν|portugu|πορτογαλ|dutch|nederlands|"
               r"ολλανδ|polish|polski|πολων|\bthai\b|indonesia|"
               r"[(\[](?:de|ger|fr|fra|it|ita|es|spa|pt|nl|pl|jp|jpn|cn|sc|tc|kr|kor|th|id)[)\]]|"
               r"\s[-–]\s?(?:de|ger|fr|it|es|jp|cn|kr)$")

# Never alert on these either: cases of 10, store displays, empty or opened boxes, single cards
# (names like "116/128", rarities, card condition, graded slabs), Pokémon Center exclusives (in
# Greece only resellers carry those) and other card games' own 30th anniversary products.
SKIP_NAMES = (r"\bcase\b|κιβωτιο|(?:booster|mini\s*tin|tin)s?\s*display|display\s*(?:box|\(\d+)|\bempty\b|"
              r"αδει[οα]|opened|ανοιγμεν|pokemon center|"
              r"\b\d{1,3}/\d{2,3}\b|single card|near mint|(?:lightly|moderately|heavily) played|\bpsa\s?\d|"
              r"\bcgc\b|\bbgs\b|graded|(?:illustration|hyper|secret|ultra|double|art) rare|full art|reverse holo|"
              r"\bmagic\b|\bmtg\b|yu-?gi-?oh|lorcana|one piece|digimon|dragon ?ball|flesh (?:and|&) blood|"
              r"vanguard|wei(?:ss|ß)|union arena|star wars|gundam|battle spirits|riftbound|metazoo")

ALERT_ON = {"in_stock", "preorder"}  # drop "preorder" if you only want things that ship now; add
                                     # "store_only" to also hear about stock sold only in their shops
ALERT_IF_PRICE_UNKNOWN = True        # in stock but the price couldn't be read: still tell me
NOTIFY_NEW_LISTINGS = True           # quiet ping when a shop lists a new 30th product (any stock)
REMIND_HOURS = 0                     # 0 = one alert per restock; 6 = remind every 6h while in stock
MAX_SEPARATE_ALERTS = 3              # more than this in one run: one summary push instead
HEALTH_ALERT_AFTER = 6               # failed runs in a row before a "shop check failing" ping
MAX_PRODUCTS_PER_SHOP = 25
PAUSE = (0.8, 2.0)                   # seconds to wait between page loads (be polite)

# ============================ END OF SETTINGS ============================

HERE = Path(__file__).resolve().parent
STATE_FILE = Path(os.environ.get("POKE_STATE") or HERE / "state.json")
LOG_FILE = HERE / "poke_watch.log"
ENV_FILE = HERE / "poke_watch.env"
DEBUG_DIR = HERE / "debug"
BROWSERS_DIR = HERE / "browsers"     # setup_pc.py installs Chromium here, so uninstalling is just deleting the folder
if BROWSERS_DIR.is_dir():
    os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(BROWSERS_DIR))

# Third-party hosts the browser never talks to: analytics, ads, social widgets, payment widgets and
# sign-in scripts. Product pages are read from the shop's own HTML, so none of these are needed.
BLOCKED_HOSTS = (
    "googletagmanager.com", "google-analytics.com", "analytics.google.com", "doubleclick.net",
    "googleadservices.com", "googlesyndication.com", "facebook.net", "facebook.com", "fbcdn.net",
    "connect.facebook.net", "hotjar.com", "clarity.ms", "cookiebot.com", "klarna.com", "klarnaservices.com",
    "appleid.cdn-apple.com", "accounts.google.com", "tiktok.com", "snapchat.com", "twitter.com", "ads-twitter.com",
    "linkedin.com", "pinterest.com", "criteo.com", "criteo.net", "taboola.com", "outbrain.com",
    "scorecardresearch.com", "sentry.io", "newrelic.com", "nr-data.net", "segment.io", "segment.com",
    "mixpanel.com", "hubspot.com", "intercom.io", "tawk.to", "smartsupp.com",
)


def norm(text) -> str:
    """Lowercase, drop accents and squash spaces, so 'Χρόνια' and 'χρονια' compare equal."""
    text = unicodedata.normalize("NFKD", str(text or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", text.replace("°", "o")).lower().strip()


# English names plus the Greek ways shops describe them ("30 Χρόνια", "30ής Επετείου")
_30TH = r"30\s*(?:th|o|ο|ης|η|ου)?[\s:.\-_]*(?:celebration|anniversary|anniv\b|χρονια|επετει)"
PRODUCT_RE = re.compile("|".join([_30TH] + [re.escape(norm(p)) for p in EXTRA_PRODUCTS]))
CELEBRATION_RE = re.compile(r"30\s*(?:th)?[\s:.\-_]*celebration")  # the set's own name: always Pokémon
POKEMON_RE = re.compile(r"poke?mon|ποκεμον")                         # Public's URLs drop the é: "pokmon"
NOT_ENGLISH_RE = re.compile(NOT_ENGLISH)
SKIP_RE = re.compile(NOT_ENGLISH + "|" + SKIP_NAMES)
TCG_HINT = re.compile(r"tcg|trading card|card game|καρτ|booster|elite trainer|\betb\b|bundle|\btin\b|collection|"
                      r"collectible|\bcoll\b|κολεξιον|blister|\bdeck\b|\bpack\b|\bbox\b|κουτι|φακελακ|συλλογ")
OOS_RE = re.compile(r"εξαντλ|μη διαθεσιμ|δεν (?:ειναι )?διαθεσιμ|προσωρινα μη|χωρις αποθεμα|out of stock|"
                    r"sold out|unavailable|not available|ειδοποιησε με|ενημερωσε με|ενημερωση διαθεσιμοτητας|"
                    r"notify me|αναμενεται|coming soon")
PRE_RE = re.compile(r"προπαραγγελ|pre-?order|προκρατηση|κατοπιν παραγγελιας")
IN_RE = re.compile(r"αμεσα διαθεσιμ|σε αποθεμα|in stock|διαθεσιμο(?!τ)|παραδοση (?:σε|αυριο|εντος)|"
                   r"αποστολη (?:σε|αυριο|εντος)")
# Sold only over the counter in the shop's physical stores (iConnect: "Διαθέσιμο στα Φυσικά Καταστήματά μας")
STORE_ONLY_RE = re.compile(r"μονο (?:απο |σε |στο |στα )?(?:τα |το )?(?:φυσικ\w* )?καταστημ|(?<!και )(?:στα|στο) "
                           r"φυσικ\w* καταστημ|in[\s-]?store only|only in (?:our )?stores?")
# "Not available for pickup at store X" says nothing about buying online
PICKUP_NO_RE = re.compile(r"(?:δεν (?:ειναι )?|μη )διαθεσιμ\w*(?: για)? (?:παραλαβ\w*|στο καταστημα\w*|"
                          r"στα καταστηματα\w*)|(?:παραλαβ\w*|pick ?up)[^.]{0,30}?(?:δεν (?:ειναι )?|μη )διαθεσιμ\w*")
# "Διαθεσιμότητα: ...", "Διαθ/τα: ...", "Availability: ..." labels say it most precisely
AVAIL_LABEL_RE = re.compile(r"(?:διαθεσιμοτητα|διαθ/τα|availability|stock)\s*:\s*([^:]{2,40})")
LANGUAGE_RE = re.compile(r"(?:γλωσσ[αε]ς?|language)\s*:?\s*([a-zα-ω]{4,})")
# "This is a search results page" (with or without hits), in the wording these shops use
NO_RESULTS_RE = re.compile(r"δεν βρεθηκ|κανενα αποτελεσμα|\b0 (?:αποτελεσματα|προιοντα)|δεν υπαρχουν (?:σχετικα )?"
                           r"προιοντα|δεν επεστρεψε|αποτελεσματα αναζητησης|αναζητηση για|αποτελεσματα για|"
                           r"no results|no products|nothing found|did not match|search results|results for|δεν ταιριαζ")
BLOCKED_RE = re.compile(r"access denied|attention required|just a moment|verify you are human|"
                        r"are you a robot|request blocked|captcha|forbidden|too many requests")
PRICE_RE = re.compile(r"(?<![\d.,])(\d{1,3}(?:\.\d{3})+|\d+)(?:\s*[,.]\s*(\d{2}))?\s*€"
                      r"|€\s*(\d{1,3}(?:\.\d{3})+|\d+)(?:[,.](\d{2}))?")
NOT_THE_PRICE = re.compile(r"δοσ|μηνα|ανω των|μεταφορικ|κερδισ|εξοικονομ|αρχικη|προηγουμεν|εως|ατοκ|επιτοκ|"
                           r"save|was|shipping|over")
STATUS_TEXT = {"in_stock": "in stock", "preorder": "pre-order open", "out_of_stock": "sold out",
               "store_only": "in their shops only", "unknown": "status unknown"}
WATCH_LABEL = "Pokémon TCG 30th Celebration (watch list)"


class ShopError(Exception):
    """The shop couldn't be checked this run (blocked, layout changed, search not found...)."""


# ------------------------------------------------------------- small helpers

SECRET_ENV = ("NTFY_TOPIC", "NTFY_TOKEN", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID")
TOKEN_SHAPES = re.compile(r"bot\d{5,}:[A-Za-z0-9_-]{20,}|Bearer\s+\S+|tk_[A-Za-z0-9]{20,}")


def scrub(text: str) -> str:
    """Hide the notification topic, tokens and chat id if they ever end up in a log line."""
    text = TOKEN_SHAPES.sub("<hidden>", text)
    for name in SECRET_ENV:
        value = os.environ.get(name, "").strip()
        if len(value) >= 6:
            text = text.replace(value, "<hidden>")
    return text


def log(msg: str) -> None:
    line = scrub(f"{datetime.now():%Y-%m-%d %H:%M:%S} {msg}")
    try:
        print(line, flush=True)
    except Exception:
        pass
    try:
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def short(err) -> str:
    text = str(err).strip().splitlines()
    return (text[0] if text else type(err).__name__)[:200]


def money(value) -> str:
    return f"€{value:.2f}" if value is not None else "€?"


def to_float(value):
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).replace("€", "").replace("\xa0", "").replace(" ", "").strip()
    if re.fullmatch(r"\d{1,3}(?:\.\d{3})+(?:,\d+)?", s):
        s = s.replace(".", "").replace(",", ".")
    elif re.fullmatch(r"\d+,\d+", s):
        s = s.replace(",", ".")
    elif re.fullmatch(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?", s):
        s = s.replace(",", "")
    try:
        return float(s)
    except ValueError:
        return price_from_text(f"{value} €")


def price_from_text(text: str):
    """First euro amount in the text that isn't an instalment, saving or shipping threshold."""
    text = text or ""
    for m in PRICE_RE.finditer(text):
        before = text[max(0, m.start() - 30):m.start()].rsplit("€", 1)[-1]
        if NOT_THE_PRICE.search(norm(before)):
            continue
        whole = (m.group(1) or m.group(3) or "").replace(".", "")
        cents = m.group(2) or m.group(4) or "00"
        try:
            value = float(f"{whole}.{cents}")
        except ValueError:
            continue
        if 1 <= value <= 5000:
            return value
    return None


def availability_from_schema(value):
    v = norm(value).replace(" ", "").replace("_", "").replace("-", "")
    if not v:
        return None
    if any(k in v for k in ("outofstock", "soldout", "discontinued")) or v == "oos":
        return "out_of_stock"
    if any(k in v for k in ("preorder", "presale", "backorder")):
        return "preorder"
    if any(k in v for k in ("instock", "limitedavailability", "onlineonly", "instoreonly")) \
            or v in ("available", "availablefororder"):
        return "in_stock"
    return None


def classify(name_norm: str):
    for pattern, label, cap in PRICE_CAPS:
        if re.search(pattern, name_norm):
            return label, cap
    return "Other", DEFAULT_CAP


def as_list(value):
    return value if isinstance(value, list) else [value]


def walk(obj):
    """Every dict inside parsed JSON-LD (handles @graph and product variants)."""
    if isinstance(obj, list):
        for x in obj:
            yield from walk(x)
    elif isinstance(obj, dict):
        yield obj
        for key in ("@graph", "mainEntity", "hasVariant"):
            if key in obj:
                yield from walk(obj[key])


def offers_of(obj):
    if isinstance(obj, list):
        for x in obj:
            yield from offers_of(x)
    elif isinstance(obj, dict):
        yield obj
        if "offers" in obj:
            yield from offers_of(obj["offers"])


def site_of(url: str) -> str:
    host = urllib.parse.urlsplit(url).netloc.lower()
    return host[4:] if host.startswith("www.") else host


def link_kind(url: str, shop: dict) -> str:
    """'product', 'skip' (marketplace etc.) or 'other'."""
    host = urllib.parse.urlsplit(url).netloc.lower()
    base = site_of(shop["home"])
    if not (host == base or host.endswith("." + base)):
        return "other"
    path = urllib.parse.unquote(url)
    if shop.get("skip_link") and re.search(shop["skip_link"], path, re.I):
        return "skip"
    if shop.get("product_link") and not re.search(shop["product_link"], path, re.I):
        return "other"
    return "product"


def is_product_page(url: str, shop: dict) -> bool:
    """Did a search land straight on a product page? Only knowable when the shop has a product_link."""
    return bool(shop.get("product_link")) and link_kind(url, shop) == "product"


def clean_url(url: str) -> str:
    """Product URL without tracking or search-term parameters, so one product is always one URL."""
    parts = urllib.parse.urlsplit(url)
    pairs = [(k, v) for k, v in urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
             if not re.match(r"utm_|gclid|fbclid|srsltid|ref$|search$|description$|tag$|sub_category$", k)]
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, parts.path, urllib.parse.urlencode(pairs), ""))


def mentions_30th(*texts) -> bool:
    for t in texts:
        n = norm(urllib.parse.unquote(t or ""))
        # "30th Celebration" etc., or a name like "Asmodee Pokémon BST Bundle 30th" (Public)
        if n and (PRODUCT_RE.search(n) or (re.search(r"(?<!\d)30\s*th\b", n) and POKEMON_RE.search(n))):
            return True
    return False


def not_english(language) -> bool:
    return bool(language) and bool(NOT_ENGLISH_RE.search(norm(language)))


def learn_template(url: str, query: str):
    """Turn the results URL the site gave us back into a reusable template with {q}."""
    parts = urllib.parse.urlsplit(url)
    pairs = urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
    nq = norm(query)
    if any(norm(v) == nq for _, v in pairs):
        qs = "&".join(urllib.parse.quote(k, safe="") + "=" +
                      ("{q}" if norm(v) == nq else urllib.parse.quote(v, safe="")) for k, v in pairs)
        return urllib.parse.urlunsplit((parts.scheme, parts.netloc, parts.path, qs, ""))
    path = urllib.parse.unquote(parts.path)
    i = path.lower().find(query.lower())
    if i >= 0:
        templ = urllib.parse.quote(path[:i] + "{q}" + path[i + len(query):], safe="/{}")
        return urllib.parse.urlunsplit((parts.scheme, parts.netloc, templ, parts.query, ""))
    return None


# ----------------------------------------------------------- notifications

def _post(url: str, data: bytes, headers: dict) -> bool:
    for attempt in (1, 2):  # one retry: a network blip shouldn't cost you an alert
        try:
            req = urllib.request.Request(url, data=data, headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=20) as resp:
                if 200 <= resp.status < 300:
                    return True
                raise RuntimeError(f"HTTP {resp.status}")
        except Exception as e:
            if attempt == 2:
                log(f"  ! couldn't send notification via {urlparse(url).hostname}: {short(e)}")
            else:
                time.sleep(3)
    return False


def notify(title: str, message: str, url: str | None = None, priority: int = 4, tags=None,
           actions=None, telegram_text: str | None = None) -> bool:
    sent = False
    topic = os.environ.get("NTFY_TOPIC", "").strip()
    if topic:
        server = os.environ.get("NTFY_SERVER", "https://ntfy.sh").strip().rstrip("/")
        body = {"topic": topic, "title": title, "message": message, "priority": priority, "tags": tags or []}
        if url:
            body["click"] = url
            body["actions"] = actions or [{"action": "view", "label": "Open shop page", "url": url}]
        headers = {"Content-Type": "application/json"}
        if os.environ.get("NTFY_TOKEN"):
            headers["Authorization"] = "Bearer " + os.environ["NTFY_TOKEN"].strip()
        sent = _post(server + "/", json.dumps(body).encode("utf-8"), headers) or sent
    tg_token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    tg_chat = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if tg_token and tg_chat:
        text = f"{title}\n{telegram_text or message}" + (f"\n{url}" if url and not telegram_text else "")
        data = urllib.parse.urlencode({"chat_id": tg_chat, "text": text}).encode("utf-8")
        sent = _post(f"https://api.telegram.org/bot{tg_token}/sendMessage", data,
                     {"Content-Type": "application/x-www-form-urlencoded"}) or sent
    if not topic and not (tg_token and tg_chat):
        log("  (no NTFY_TOPIC or Telegram settings, so this alert only went to the log)")
    return sent


def send_alert(item: dict) -> bool:
    title = f"{STATUS_TEXT[item['status']].capitalize()}: {item['kind']} at {item['shop']}"
    price = money(item["price"]) if item["price"] is not None else "price not shown"
    message = f"{item['name']}\n{price} (your cap €{item['cap']})"
    return notify(title, message, item["url"], priority=5 if item["status"] == "in_stock" else 4,
                  tags=["rotating_light"])


def send_alerts(alerts: list) -> list:
    """One push per item, or a single summary when a run finds a lot at once.
    Returns the items whose push didn't go out, so the next run can try again."""
    if len(alerts) <= MAX_SEPARATE_ALERTS:
        return [item for item in alerts if not send_alert(item)]
    alerts = sorted(alerts, key=lambda i: (i["status"] != "in_stock", -(i["price"] or 0)))
    groups = {}  # e.g. ten Mini Tin designs from one shop at one price become one line
    for i in alerts:
        groups.setdefault((i["kind"], i["price"], i["shop"], i["status"]), []).append(i)
    lines, tg_lines = [], []
    for (kind, price, shop, status), items in list(groups.items())[:15]:
        line = f"{kind} {money(price)} at {shop}" + (f" x{len(items)}" if len(items) > 1 else "") \
            + (" (pre-order)" if status == "preorder" else "")
        lines.append(line)
        tg_lines.append(f"{line}\n{items[0]['url']}")
    if len(groups) > 15:
        lines.append(f"...and {len(groups) - 15} more")
    firsts = [items[0] for items in groups.values()]
    actions = [{"action": "view", "label": f"{i['kind'][:16]} {money(i['price'])}", "url": i["url"]} for i in firsts[:3]]
    sent = notify(f"{len(alerts)} Pokémon 30th items at retail price", "\n".join(lines), firsts[0]["url"],
                  priority=5, tags=["rotating_light"], actions=actions, telegram_text="\n".join(tg_lines))
    return [] if sent else list(alerts)


def send_new_listing(item: dict) -> None:
    notify(f"New 30th listing at {item['shop']}",
           f"{item['name']}\n{STATUS_TEXT[item['status']]}, {money(item['price'])}",
           item["url"], priority=2, tags=["eyes"])


# ------------------------------------------------------------------ state

def load_state() -> dict:
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    data.setdefault("items", {})
    data.setdefault("shops", {})
    return data


def save_state(state: dict) -> None:
    cutoff = time.time() - 60 * 86400  # forget products not seen for 60 days
    state["items"] = {u: r for u, r in state["items"].items() if r.get("last_seen", 0) >= cutoff}
    tmp = STATE_FILE.with_name(STATE_FILE.name + ".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")
    os.replace(tmp, STATE_FILE)


def decide(item: dict, prev: dict | None, now: float):
    """'alert', 'new' or None."""
    if item.get("skip"):
        return None
    if item["good"]:
        if not prev or not prev.get("alerted"):
            return "alert"
        last = prev.get("alerted_price")
        if item["price"] is not None and last is not None and item["price"] < last - 0.5:
            return "alert"  # cheaper than when we last told you
        if REMIND_HOURS and now - prev.get("alerted_at", 0) >= REMIND_HOURS * 3600:
            return "alert"
        return None
    if prev is None and NOTIFY_NEW_LISTINGS:
        return "new"
    return None


def remember(item: dict, prev: dict | None, kind, now: float) -> dict:
    rec = dict(prev or {})
    rec.setdefault("first_seen", int(now))
    # last_seen is rounded to the day so state.json only changes when something real changes
    rec.update(shop=item["shop"], src=item.get("src", item["shop"]), name=item["name"], status=item["status"],
               price=item["price"], last_seen=int(now // 86400 * 86400), missing=0)
    if kind == "alert":
        rec.update(alerted=True, alerted_at=int(now), alerted_price=item["price"])
    elif (item["status"] in ALERT_ON and not item["good"]) or item["status"] not in ALERT_ON | {"unknown"}:
        rec["alerted"] = False  # sold out or over the cap: next time it's buyable under the cap you hear about it
    return rec


# ---------------------------------------------------------------- browser

LINKS_JS = r"""() => [...document.querySelectorAll('a[href]')].map(a => {
  const img = a.querySelector('img[alt]');
  let label = [a.innerText, a.getAttribute('title'), a.getAttribute('aria-label'), img && img.getAttribute('alt')]
    .filter(Boolean).join(' ').replace(/\s+/g, ' ').trim().slice(0, 300);
  if (!/[a-zα-ω]{3}/i.test(label)) {
    let el = a;
    for (let i = 0; i < 3 && el.parentElement; i++) {
      el = el.parentElement;
      const t = (el.innerText || '').replace(/\s+/g, ' ').trim();
      if (t.length > 10 && t.length < 400) { label = t; break; }
    }
  }
  return [a.href, label];
}).filter(x => /^https?:/i.test(x[0]))"""

PRODUCT_READY_JS = r"""() => {
  const ld = [...document.querySelectorAll('script[type="application/ld+json"]')].some(s => /"offers"/.test(s.textContent));
  return ld || !!document.querySelector('[itemprop="price"], meta[property="product:price:amount"], meta[property="og:price:amount"]');
}"""

PRODUCT_JS = r"""() => {
  const r = {jsonld: [], meta: {}, micro: {}, h1: '', text: '', box: false, cart: null, title: document.title || ''};
  for (const s of document.querySelectorAll('script[type="application/ld+json"]')) {
    try { r.jsonld.push(JSON.parse(s.textContent)); } catch (e) {}
  }
  for (const m of document.querySelectorAll('meta')) {
    const k = (m.getAttribute('property') || m.getAttribute('name') || m.getAttribute('itemprop') || '').toLowerCase();
    if (k && /price|availability|og:title/.test(k)) r.meta[k] = m.getAttribute('content') || '';
  }
  const ip = document.querySelector('[itemprop="price"]') || document.querySelector('[data-price-type="finalPrice"][data-price-amount]');
  if (ip) r.micro.price = ip.getAttribute('content') || ip.getAttribute('data-price-amount') || ip.textContent || '';
  const ia = document.querySelector('[itemprop="availability"]');
  if (ia) r.micro.availability = ia.getAttribute('href') || ia.getAttribute('content') || ia.textContent || '';
  const h1 = document.querySelector('h1');
  let box = null;
  if (h1) {
    r.h1 = (h1.innerText || '').trim();
    let el = h1;
    for (let i = 0; i < 10 && el.parentElement; i++) {
      el = el.parentElement;
      const t = el.innerText || '';
      if (/\d\s*€|€\s*\d/.test(t) && t.length > 60) { box = el; break; }
    }
  }
  r.box = !!box;
  r.text = ((box || document.body).innerText || '').slice(0, 8000);
  const cartRe = /στο καλάθι|add to (cart|basket|bag)|buy now|αγορά τώρα|^\s*αγορά\s*$|προπαραγγελία|αγ[οό]ρασ[εέ]\s*το/i;
  const btns = [...(box || document).querySelectorAll('button, input[type="submit"], [role="button"]')]
    .filter(b => cartRe.test((b.innerText || b.value || '').trim()));
  if (btns.length) r.cart = btns.some(b => !b.disabled && b.getAttribute('aria-disabled') !== 'true'
    && !/(^|\s)disabled(\s|$)/i.test(b.getAttribute('class') || ''));
  return r;
}"""

SEARCH_INPUTS = ", ".join([
    'input[type="search"]', 'input[name="q"]', 'input[name="query"]', 'input[name="text"]',
    'input[name="search"]', 'input[name="s"]', 'input[name="term"]', 'input[name="keyword"]',
    'input[name="keywords"]', 'input[name="searchTerm"]', 'input[placeholder*="ναζήτ"]',
    'input[placeholder*="ΝΑΖΉΤ"]', 'input[placeholder*="search" i]', 'input[aria-label*="search" i]',
    'input[aria-label*="ναζήτ"]',
])
SEARCH_TOGGLES = ", ".join([
    'button[aria-label*="search" i]', 'button[aria-label*="ναζήτ"]', 'button[title*="ναζήτ"]',
    'button[class*="search" i]', 'a[class*="search" i]', '[class*="search" i] button',
    '[data-testid*="search" i]', '[class*="search-toggle" i]', '[class*="search-icon" i]',
])


def user_agent(version: str) -> str:
    major = (version or "130").split(".")[0]
    if sys.platform.startswith("win"):
        plat = "Windows NT 10.0; Win64; x64"
    elif sys.platform == "darwin":
        plat = "Macintosh; Intel Mac OS X 10_15_7"
    else:
        plat = "X11; Linux x86_64"
    return f"Mozilla/5.0 ({plat}) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{major}.0.0.0 Safari/537.36"


def launch_browser(pw, headed: bool = False):
    errors = []
    for channel in (None, "chrome", "msedge"):  # bundled Chromium, else installed Chrome or Edge
        try:
            options = {"headless": not headed}
            if channel:
                options["channel"] = channel
            return pw.chromium.launch(**options)
        except Exception as e:
            errors.append(f"{channel or 'chromium'}: {short(e)}")
    raise SystemExit("Couldn't start a browser. Run:  python -m playwright install chromium\n  "
                     + "\n  ".join(errors))


def host_blocked(host: str) -> bool:
    return any(host == h or host.endswith("." + h) for h in BLOCKED_HOSTS)


def make_router(seen: dict):
    """Blocks images, fonts, media and tracker hosts; records every host the page asked for in `seen`."""
    def router(route):
        req = route.request
        host = (urlparse(req.url).hostname or "").lower()
        blocked = req.resource_type in ("image", "media", "font") or host_blocked(host)
        if host:
            seen[host] = "blocked" if blocked else "loaded"
        if blocked:
            route.abort()
        else:
            route.continue_()
    return router


def settle(page, idle_ms: int = 8000) -> None:
    try:
        page.wait_for_load_state("networkidle", timeout=idle_ms)
    except Exception:
        pass
    page.wait_for_timeout(800)


def safe_eval(page, js: str, default=None, arg=None):
    """page.evaluate that survives a page still navigating or re-rendering."""
    for attempt in range(3):
        try:
            return page.evaluate(js) if arg is None else page.evaluate(js, arg)
        except Exception:
            if attempt == 2:
                return default
            page.wait_for_timeout(1000)


def check_blocked(page, status: int = 0) -> None:
    try:
        title = page.title() or ""
    except Exception:
        title = ""
    body = safe_eval(page, "() => (document.body && document.body.innerText || '').slice(0, 3000)", "")
    if status in (401, 403, 429, 503) or BLOCKED_RE.search(norm(title)) \
            or (len(body) < 1500 and BLOCKED_RE.search(norm(body))):
        raise ShopError(f"blocked by the site (HTTP {status or '?'}, page title '{title[:60]}')")


def open_page(page, url: str, idle_ms: int = 8000) -> int:
    resp = page.goto(url, wait_until="domcontentloaded", timeout=45000)
    settle(page, idle_ms)
    status = resp.status if resp else 0
    check_blocked(page, status)
    return status


def page_links(page) -> list:
    return safe_eval(page, LINKS_JS, []) or []


def looks_like_results(page, shop: dict, query: str, links: list) -> bool:
    if any(link_kind(h, shop) == "product" for h, _ in links):
        return True
    text = norm(safe_eval(page, "() => document.body ? document.body.innerText.slice(0, 20000) : ''", ""))
    if NO_RESULTS_RE.search(text):
        return True
    values = safe_eval(page, "() => [...document.querySelectorAll('input')].map(i => i.value || '')", []) or []
    return any(norm(v) == norm(query) for v in values)


def _visible(page, selector: str):
    for el in page.query_selector_all(selector):
        try:
            if el.is_visible() and el.is_enabled():
                return el
        except Exception:
            continue
    return None


def type_search(page, query: str) -> bool:
    """Use the site's own search box like a person would."""
    box = _visible(page, SEARCH_INPUTS)
    if not box:
        for toggle in page.query_selector_all(SEARCH_TOGGLES)[:6]:
            try:
                toggle.dispatch_event("click")
                page.wait_for_timeout(700)
            except Exception:
                continue
            box = _visible(page, SEARCH_INPUTS)
            if box:
                break
    if not box:
        return False
    start = page.url
    try:
        box.fill(query)
        box.press("Enter")
    except Exception:
        return False
    try:
        page.wait_for_url(lambda u: u != start, timeout=8000)
    except Exception:
        try:  # some sites only submit through the form
            box.evaluate("el => el.form && (el.form.requestSubmit ? el.form.requestSubmit() : el.form.submit())")
            page.wait_for_url(lambda u: u != start, timeout=6000)
        except Exception:
            pass
    settle(page)
    check_blocked(page)
    return True


def run_search(page, shop: dict, sstate: dict, query: str):
    """Open the results page for one query. Returns (url, links)."""
    tried = []
    for template in dict.fromkeys(t for t in (sstate.get("template"), shop.get("search")) if t):
        url = template.replace("{q}", urllib.parse.quote(query, safe=""))
        status = open_page(page, url)
        links = page_links(page)
        if status < 400 and is_product_page(page.url, shop):
            return page.url, links  # single hit: the shop jumped straight to the product page
        query_in_url = norm(query).split()[0] in norm(urllib.parse.unquote(page.url))
        if status < 400 and query_in_url and looks_like_results(page, shop, query, links):
            sstate["template"] = template
            return page.url, links
        tried.append(url)
    open_page(page, shop["home"])
    if not type_search(page, query):
        raise ShopError("couldn't find the site's search box" + (f" (and {tried[-1]} didn't work)" if tried else ""))
    links = page_links(page)
    if is_product_page(page.url, shop):
        return page.url, links
    if not looks_like_results(page, shop, query, links):
        raise ShopError(f"searching for '{query}' didn't lead to a results page (ended on {page.url})")
    learned = learn_template(page.url, query)
    if learned:
        sstate["template"] = learned
    return page.url, links


def read_product(raw: dict, fallback_name: str) -> dict:
    """Name, price and stock status from a product page, structured data first."""
    h1 = raw.get("h1") or ""
    products = [n for n in walk(raw.get("jsonld") or [])
                if any(re.split(r"[/:#]", str(t))[-1] in ("Product", "ProductGroup", "IndividualProduct")
                       for t in as_list(n.get("@type")))]
    node = None
    if products:
        node = products[0]
        for p in products:
            pn, nh = norm(p.get("name")), norm(h1)
            if pn and nh and (pn == nh or pn in nh or nh in pn):
                node = p
                break
    name, price, status, seller, source = (str(node.get("name") or "").strip() if node else ""), None, None, None, ""
    if node:
        best = None
        for off in offers_of(node.get("offers")):
            spec = off.get("priceSpecification")
            if isinstance(spec, list):
                spec = next((x for x in spec if isinstance(x, dict) and x.get("price") not in (None, "")), {})
            raw_price = next((v for v in (off.get("price"), off.get("lowPrice"), (spec or {}).get("price"))
                              if v not in (None, "")), None)
            pr, st = to_float(raw_price), availability_from_schema(off.get("availability"))
            if pr is None and st is None:
                continue
            sel = off.get("seller")
            sel = sel.get("name") if isinstance(sel, dict) else sel if isinstance(sel, str) else None
            rank = (0 if st in ALERT_ON else 1, pr if pr is not None else 1e9)
            if best is None or rank < best[0]:
                best = (rank, pr, st, sel)
        if best:
            _, price, status, seller = best
            source = "page data"
    meta, micro = raw.get("meta") or {}, raw.get("micro") or {}
    if price is None:
        price = to_float(meta.get("product:price:amount") or meta.get("og:price:amount") or micro.get("price"))
    if status is None:
        status = availability_from_schema(meta.get("product:availability") or meta.get("og:availability")
                                          or micro.get("availability"))
        source = "page data" if status else source
    text, cart = raw.get("text") or "", raw.get("cart")
    ntext = norm(text)
    if price is None and raw.get("box"):
        price = price_from_text(text)
    if status is None:
        if raw.get("box"):
            label = AVAIL_LABEL_RE.search(ntext)
            status = (status_from_text(label.group(1)) if label else None) or status_from_text(ntext, cart) \
                or ("in_stock" if cart else "unknown")
        else:
            status = "in_stock" if cart else "out_of_stock" if cart is False else "unknown"
        source = "page text"
    # Card language, when the product box states it ("Γλώσσα: English", "Γλώσσες: Αγγλικά")
    lang = LANGUAGE_RE.search(ntext) if raw.get("box") else None
    name = name or (meta.get("og:title") or "").strip() or h1.strip() or raw.get("title", "").strip() or fallback_name
    return {"name": re.sub(r"\s+", " ", name)[:200], "price": price, "status": status,
            "seller": seller, "source": source, "language": lang.group(1) if lang else None}


def status_from_text(text: str, cart=None):
    """Stock status from normalized page text, or None if the text doesn't say."""
    text = PICKUP_NO_RE.sub(" ", text)
    if PRE_RE.search(text) and cart is not False:
        return "preorder"
    if STORE_ONLY_RE.search(text):
        return "store_only"
    if OOS_RE.search(text):
        return "out_of_stock"
    if IN_RE.search(text):
        return "in_stock"
    return None


def judge(item: dict, shop: dict) -> dict:
    """Is this a 30th product we care about, and is it buyable at retail price?"""
    n = norm(item["name"])
    context = norm(urllib.parse.unquote(" ".join(t for t in (item["name"], item["url"], item.get("label")) if t)))
    item["kind"], item["cap"] = classify(n)
    if not mentions_30th(item["name"], item["url"], item.get("label")):
        item["skip"] = "not a 30th product"
    elif NOT_ENGLISH_RE.search(n) or not_english(item.get("language")):
        item["skip"] = "not English" + (f" (page says {item['language']})" if not_english(item.get("language")) else "")
    elif SKIP_RE.search(n):
        item["skip"] = "single card, case, display, Pokémon Center item or another card game"
    elif not TCG_HINT.search(n):
        item["skip"] = "not a sealed card product"
    elif not (POKEMON_RE.search(context) or CELEBRATION_RE.search(context)):
        item["skip"] = "not a Pokémon product"
    elif item.get("seller") and shop.get("seller") and not re.search(shop["seller"], norm(item["seller"])):
        item["skip"] = f"sold by {item['seller']}, not the shop itself"
    price = item["price"]
    price_ok = (price is not None and price <= item["cap"]) or (price is None and ALERT_IF_PRICE_UNKNOWN)
    item["good"] = not item.get("skip") and item["status"] in ALERT_ON and price_ok
    return item


def snapshot(page, shop: dict, label: str) -> None:
    DEBUG_DIR.mkdir(exist_ok=True)
    base = re.sub(r"\W+", "_", f"{shop['name']}_{label}")[:60]
    try:
        page.screenshot(path=str(DEBUG_DIR / f"{base}.png"))
        (DEBUG_DIR / f"{base}.html").write_text(page.content(), encoding="utf-8")
    except Exception:
        pass


BESTPRICE_JS = r"""() => [...document.querySelectorAll('div.p[data-id]')].map(c => {
  const a = c.querySelector('.p__title a') || c.querySelector('a[href*="/to/"]');
  const av = c.querySelector('.p__availability');
  const m = c.querySelector('.p__merchant-link') || c.querySelector('.p__merchant');
  const pr = c.querySelector('.p__price--current');
  return {url: a ? a.href : '', name: a ? (a.getAttribute('title') || a.innerText || '').trim() : '',
          cents: c.getAttribute('data-price') || '', priceText: pr ? pr.innerText : '',
          merchant: m ? m.innerText.trim() : '', avail: av ? av.innerText.trim() : '', availClass: av ? av.className : ''};
}).filter(x => x.url)"""


def bestprice_status(text: str, css: str) -> str:
    t = norm(text)
    if "in-stock" in (css or "") or "αμεσα διαθεσιμ" in t:
        return "in_stock"
    if PRE_RE.search(t) or "κατοπιν παραγγελιας" in t:
        return "preorder"
    if re.search(r"σε \d+\s*-\s*\d+ ημερ|εως \d+ ημερ", t):
        return "in_stock"  # orderable now, ships in a few days
    if OOS_RE.search(t):
        return "out_of_stock"
    return "unknown"


def scan_bestprice(page, shop: dict, sstate: dict, debug: bool = False):
    """Read offers straight off BestPrice listings: name, price, shop and availability per card."""
    items, pages, seen = [], [], set()
    for query in QUERIES:
        for n in range(1, shop.get("pages", 1) + 1):
            url = shop["search"].replace("{q}", urllib.parse.quote(query, safe="")) + (f"&pg={n}" if n > 1 else "")
            open_page(page, url)
            pages.append(url)
            if debug:
                snapshot(page, shop, f"{query}_{n}")
            offers = safe_eval(page, BESTPRICE_JS, []) or []
            if not offers:
                text = norm(safe_eval(page, "() => document.body ? document.body.innerText.slice(0, 20000) : ''", ""))
                if n == 1 and not re.search(r"δεν βρεθηκ|\b0 προιοντα|κανενα αποτελεσμα", text):
                    raise ShopError("no offers found on BestPrice (layout changed?)")
                break
            for o in offers:
                u = clean_url(o["url"])
                if u in seen:
                    continue
                seen.add(u)
                price = to_float(o["cents"]) / 100 if str(o["cents"]).isdigit() else to_float(o["priceText"])
                item = {"name": o["name"], "price": price, "status": bestprice_status(o["avail"], o["availClass"]),
                        "seller": None, "source": "BestPrice listing", "url": u, "label": o["name"],
                        "shop": f"{o['merchant'] or 'a shop'} (via BestPrice)", "src": shop["name"]}
                items.append(judge(item, shop))
            time.sleep(random.uniform(*PAUSE))
    return items, {"pages": pages, "marketplace": 0, "candidates": len(items)}


def scan_shop(page, shop: dict, sstate: dict, debug: bool = False):
    if shop.get("type") == "bestprice":
        return scan_bestprice(page, shop, sstate, debug)
    found, fallback, marketplace, pages, passed = {}, {}, set(), [], set()

    def consider(href: str, label: str) -> None:
        u = clean_url(href)
        if label and len(label) < 250 and SKIP_RE.search(norm(label)):
            passed.add(u)  # e.g. "(Illustration Rare) English - Near Mint" or "Japanese": no need to open it
        else:
            found[u] = found.get(u) or label

    for query in QUERIES:
        url, links = run_search(page, shop, sstate, query)
        pages.append(url)
        if debug:
            snapshot(page, shop, query)
        if is_product_page(page.url, shop) and mentions_30th(page.url, safe_eval(page, "() => document.title", "")):
            consider(page.url, safe_eval(page, "() => document.title", ""))
        for href, label in links:
            kind = link_kind(href, shop)
            if kind == "skip" and mentions_30th(label, href):
                marketplace.add(clean_url(href))
            elif kind == "product":
                if mentions_30th(label, clean_url(href)):
                    consider(href, label)
                elif len(fallback) < 5 and (TCG_HINT.search(norm(label)) or POKEMON_RE.search(norm(label))):
                    fallback.setdefault(clean_url(href), label)
        time.sleep(random.uniform(*PAUSE))
    for extra in shop.get("extra_pages", []):
        try:
            open_page(page, extra)
            pages.append(extra)
            for href, label in page_links(page):
                if link_kind(href, shop) == "product" and mentions_30th(label, href) and clean_url(href) not in found:
                    consider(href, label)
        except ShopError:
            raise
        except Exception as e:
            log(f"  ! {shop['name']}: couldn't open {extra}: {short(e)}")
    # Watched pages first, then what the search found. If the search found nothing obviously
    # "30th", open its top hits instead: some shops only show the full name on the product page.
    watched = [clean_url(w) for w in shop.get("watch", [])]
    candidates = dict.fromkeys(watched, WATCH_LABEL)
    for u, label in (found or ({} if passed else fallback)).items():
        candidates.setdefault(u, label)
    items, opened = [], set()
    for u, label in list(candidates.items())[:MAX_PRODUCTS_PER_SHOP]:
        time.sleep(random.uniform(*PAUSE))
        try:
            if open_page(page, u, idle_ms=4000) >= 400:
                log(f"  ! {shop['name']}: {u} is gone (page not found)")
                continue
            final = clean_url(page.url)
            if final != u and link_kind(final, shop) != "product":
                log(f"  ! {shop['name']}: {u} now leads to {final}, which isn't a product page")
                continue
            if final in opened:
                continue  # the same product, reached through another link
            opened.update((u, final))
            try:
                page.wait_for_function(PRODUCT_READY_JS, timeout=6000)
            except Exception:
                pass
            raw = safe_eval(page, PRODUCT_JS)
            if not raw:
                raise RuntimeError("page didn't finish loading")
        except ShopError:
            raise
        except Exception as e:
            log(f"  ! {shop['name']}: couldn't read {u}: {short(e)}")
            continue
        item = read_product(raw, label)
        # Keyed by where the page really is, so a product reached by two links is one product
        item.update(url=final, label=label, shop=shop["name"], src=shop["name"], watched=u in watched)
        items.append(judge(item, shop))
    return items, {"pages": pages, "marketplace": len(marketplace), "candidates": len(candidates),
                   "passed": len(passed)}


def report(item: dict, kind, test: bool) -> None:
    if item.get("skip"):
        if test:
            log(f"   skip  {item['name'][:80]}  ({item['skip']})")
        return
    over = " OVER CAP" if item["price"] is not None and item["price"] > item["cap"] else ""
    note = {"alert": " -> would alert" if test else " -> ALERT SENT",
            "new": " -> would ping: new listing" if test else " -> new listing ping"}.get(kind, "")
    log(f"  [{'BUY' if item['good'] else ' - '}] {STATUS_TEXT[item['status']]:<15}{money(item['price']):>9} "
        f"(cap €{item['cap']}{over})  {item['kind']}: {item['name'][:80]}{note}")
    if test:
        log(f"          {item['url']}  [read from {item['source']}]")


# ------------------------------------------------------------------- runs

def pick_shops(only=None, skip=None) -> list:
    """Shops to check. Both take comma-separated names; part of a name is enough ("public,kaissa")."""
    def names(text):
        return [n.strip().lower() for n in (text or "").split(",") if n.strip()]
    want, drop = names(only), names(skip)
    return [s for s in SHOPS if (not want or any(w in s["name"].lower() for w in want))
            and not any(d in s["name"].lower() for d in drop)]


def run_once(args) -> int:
    from playwright.sync_api import sync_playwright

    state, now = load_state(), time.time()
    shops = pick_shops(args.shop, getattr(args, "skip_shop", None))
    if not shops:
        raise SystemExit(f"No shop matches '{args.shop}'. Shops: {', '.join(s['name'] for s in SHOPS)}")
    log(f"Checking {', '.join(s['name'] for s in shops)}" + (" (test run: nothing is sent or saved)" if args.test else ""))
    alerts = []
    with sync_playwright() as pw:
        browser = launch_browser(pw, args.headed)
        ua = user_agent(browser.version)
        for shop in shops:
            sstate = state["shops"].setdefault(shop["name"], {})
            seeded = sstate.get("seeded", False)
            ctx = browser.new_context(user_agent=ua, locale="el-GR", timezone_id="Europe/Athens",
                                      viewport={"width": 1366, "height": 900})
            seen = {}
            ctx.route("**/*", make_router(seen))
            page = ctx.new_page()
            try:
                items, info = scan_shop(page, shop, sstate, args.debug)
            except Exception as e:
                if args.debug:
                    snapshot(page, shop, "failed")
                shop_failed(shop, sstate, e, args.test)
                continue
            finally:
                ctx.close()
                if getattr(args, "hosts", False):
                    loaded = sorted(h for h, v in seen.items() if v == "loaded")
                    blocked = sorted(h for h, v in seen.items() if v == "blocked")
                    log(f"[{shop['name']}] hosts contacted: {', '.join(loaded) or '-'}")
                    log(f"[{shop['name']}] hosts blocked: {', '.join(blocked) or '-'}")
            extra = f", {info['marketplace']} marketplace listing(s) ignored" if info["marketplace"] else ""
            if info.get("passed"):
                extra += f", {info['passed']} skipped by name (singles, imports, other languages...)"
            what = "offer(s) read" if shop.get("type") == "bestprice" else "product page(s) checked"
            log(f"[{shop['name']}] {len(items)} {what}{extra}")
            if args.test:
                for p in info["pages"]:
                    log(f"   searched: {p}")
            elif sstate.get("warned"):
                notify(f"Poke watch: {shop['name']} is working again", "Checks are back to normal.",
                       priority=2, tags=["white_check_mark"])
            sstate.update(fails=0, warned=False)
            sstate.pop("last_error", None)
            for item in items:
                prev = state["items"].get(item["url"])
                kind = decide(item, prev, now)
                if kind == "new" and (not seeded or shop.get("new_listings") is False or item.get("watched")):
                    kind = None  # first run for this shop (just learn what's listed), or a page you already watch
                report(item, kind, args.test)
                if args.test or item.get("skip"):
                    continue
                if kind == "alert":
                    alerts.append(item)
                elif kind == "new":
                    send_new_listing(item)
                state["items"][item["url"]] = remember(item, prev, kind, now)
            if not args.test:
                # Many shops hide sold-out products from search. If something we alerted on is
                # gone for two good runs in a row, re-arm it so its comeback triggers an alert.
                seen = {i["url"] for i in items}
                for url, rec in state["items"].items():
                    if rec.get("src", rec.get("shop")) == shop["name"] and url not in seen and rec.get("missing", 0) < 2:
                        rec["missing"] = rec.get("missing", 0) + 1
                        if rec["missing"] >= 2:
                            rec["alerted"] = False
                sstate["seeded"] = True
        browser.close()
    failed = []
    if not args.test:
        failed = send_alerts(alerts)
        for item in failed:  # the push didn't reach you: don't count it as told, try again next run
            if item["url"] in state["items"]:
                state["items"][item["url"]]["alerted"] = False
        save_state(state)
    if args.test:
        log("Test run finished.")
    else:
        log(f"Done: {len(alerts) - len(failed)} alert(s) sent."
            + (f" {len(failed)} couldn't be sent and will be retried next run." if failed else ""))
    return 0


def shop_failed(shop: dict, sstate: dict, err: Exception, test: bool) -> None:
    reason = short(err)
    log(f"[{shop['name']}] check FAILED: {reason}")
    if test:
        return
    sstate["fails"] = min(sstate.get("fails", 0) + 1, HEALTH_ALERT_AFTER)
    sstate["last_error"] = reason
    if sstate["fails"] >= HEALTH_ALERT_AFTER and not sstate.get("warned"):
        notify(f"Poke watch: {shop['name']} check failing",
               f"{sstate['fails']} runs in a row failed: {reason}\n"
               f"Run: python poke_watch.py --test --shop \"{shop['name']}\" to see what the site returns.",
               priority=3, tags=["warning"])
        sstate["warned"] = True


def load_env_file() -> None:
    if not ENV_FILE.exists():
        return
    for line in ENV_FILE.read_text(encoding="utf-8-sig").splitlines():  # -sig: Notepad may add a BOM
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def main() -> int:
    for name in ("stdout", "stderr"):  # pythonw.exe (Windows scheduled task) has no console at all
        if getattr(sys, name) is None:
            setattr(sys, name, open(os.devnull, "w", encoding="utf-8"))
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    try:
        if LOG_FILE.stat().st_size > 2_000_000:
            LOG_FILE.write_bytes(LOG_FILE.read_bytes()[-1_000_000:])
    except OSError:
        pass
    ap = argparse.ArgumentParser(description="Pokémon TCG 30th Celebration retail stock checker")
    ap.add_argument("--test", action="store_true", help="dry run: show what each shop returns, send and save nothing")
    ap.add_argument("--notify-test", action="store_true", help="send a test notification and exit")
    ap.add_argument("--every", type=float, metavar="MINUTES", help="keep running, checking every N minutes")
    ap.add_argument("--shop", help="only check these shops, e.g. \"Public,Kaissa,Nerdom\" (part of a name is enough)")
    ap.add_argument("--skip-shop", help="check every shop except these, e.g. \"Public,Kaissa,Nerdom\"")
    ap.add_argument("--headed", action="store_true", help="show the browser window")
    ap.add_argument("--hosts", action="store_true", help="list every website each shop check contacted (loaded or blocked)")
    ap.add_argument("--debug", action="store_true", help="save screenshots and HTML of search pages to ./debug")
    args = ap.parse_args()
    load_env_file()
    if args.notify_test:
        ok = notify("Poke watch test", "If you can read this on your phone, alerts work.", priority=3, tags=["tada"])
        log("Test notification sent." if ok else "Test notification NOT sent: check NTFY_TOPIC / Telegram settings.")
        return 0 if ok else 1
    if not args.every:
        return run_once(args)
    while True:
        try:
            run_once(args)
        except SystemExit:
            raise
        except Exception as e:
            log(f"Run crashed: {short(e)}")
        wait = max(5.0, args.every) * 60 + random.uniform(-90, 90)
        log(f"Next check in about {wait / 60:.0f} min")
        time.sleep(wait)


if __name__ == "__main__":
    sys.exit(main())
