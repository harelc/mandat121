#!/usr/bin/env python3
"""
Fetch MK photos from multiple sources:
1. Hebrew Wikipedia direct page lookup (most articles are "FirstName LastName")
2. Hebrew Wikipedia search with variants
3. Wikidata API (P18 image property)
4. English Wikipedia search
Falls back to initials+color avatars if no photo found.
"""

import json
import re
import urllib.request
import urllib.parse
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

DATA_DIR = Path(__file__).parent / "data"
PHOTOS_DIR = DATA_DIR / "photos"
PHOTOS_DIR.mkdir(parents=True, exist_ok=True)

HE_WIKI_API = "https://he.wikipedia.org/w/api.php"
EN_WIKI_API = "https://en.wikipedia.org/w/api.php"
WIKIDATA_API = "https://www.wikidata.org/w/api.php"
UA = "BiapiaMKPhotos/1.0 (quiz app; https://github.com/harelc/biapia)"


def api_get(url, params):
    """Make a GET request to a MediaWiki API."""
    full_url = f"{url}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(full_url)
    req.add_header("User-Agent", UA)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None


def get_page_image(api_url, title, thumb_size=300):
    """Get the page image (thumbnail) for a specific page title."""
    data = api_get(api_url, {
        "action": "query",
        "format": "json",
        "titles": title,
        "prop": "pageimages",
        "pithumbsize": str(thumb_size),
    })
    if not data:
        return None
    pages = data.get("query", {}).get("pages", {})
    for page in pages.values():
        if page.get("pageid"):  # Page exists
            thumb = page.get("thumbnail", {}).get("source")
            if thumb:
                return thumb
    return None


def search_wiki_image(api_url, search_terms, key_parts=None):
    """Search Wikipedia for a person and get their page image."""
    for term in search_terms:
        data = api_get(api_url, {
            "action": "query",
            "format": "json",
            "list": "search",
            "srsearch": term,
            "srlimit": "5",
            "srnamespace": "0",
        })
        if not data:
            continue

        results = data.get("query", {}).get("search", [])
        if not results:
            continue

        # Pick best matching page
        page_title = None
        if key_parts:
            for r in results:
                title = r.get("title", "")
                if all(p in title for p in key_parts):
                    page_title = title
                    break
        if not page_title:
            # Only use first result if it looks like a person page
            page_title = results[0]["title"]

        thumb = get_page_image(api_url, page_title)
        if thumb:
            return thumb

    return None


def wikidata_get_image(search_name):
    """Search Wikidata for the person and get their P18 (image) property."""
    # Search Wikidata
    data = api_get(WIKIDATA_API, {
        "action": "wbsearchentities",
        "format": "json",
        "language": "he",
        "type": "item",
        "search": search_name,
        "limit": "5",
    })
    if not data:
        return None

    for result in data.get("search", []):
        entity_id = result.get("id")
        if not entity_id:
            continue

        # Get the entity's P18 (image) claim
        entity_data = api_get(WIKIDATA_API, {
            "action": "wbgetclaims",
            "format": "json",
            "entity": entity_id,
            "property": "P18",
        })
        if not entity_data:
            continue

        claims = entity_data.get("claims", {}).get("P18", [])
        if not claims:
            continue

        # Get the image filename
        image_name = claims[0].get("mainsnak", {}).get("datavalue", {}).get("value")
        if not image_name:
            continue

        # Construct Commons thumbnail URL
        image_name_encoded = urllib.parse.quote(image_name.replace(" ", "_"))
        # Use Special:FilePath to get a redirect to the actual thumbnail
        thumb_url = f"https://commons.wikimedia.org/wiki/Special:FilePath/{image_name_encoded}?width=300"
        return thumb_url

    return None


# Map of known Hebrew→English name transliterations for major MKs
ENGLISH_NAMES = {
    "לפיד יאיר": "Yair Lapid",
    "נתניהו בנימין": "Benjamin Netanyahu",
    "ליברמן אביגדור": "Avigdor Lieberman",
    "גנץ בני": "Benny Gantz",
    "בן גביר איתמר": "Itamar Ben-Gvir",
    "טיבי אחמד": "Ahmad Tibi",
    "דרעי אריה מכלוף": "Aryeh Deri",
    "מיכאלי מרב": "Merav Michaeli",
    "סמוטריץ' בצלאל": "Bezalel Smotrich",
    "דיכטר אבי": "Avi Dichter",
    "לוין יריב": "Yariv Levin",
    "מעוז אבי": "Avi Maoz",
    "רוטמן שמחה": "Simcha Rothman",
    "אלקין זאב": "Ze'ev Elkin",
    "פרקש הכהן אורית": "Orit Farkash-Hacohen",
    "גמליאל גילה": "Gila Gamliel",
    "כהן מירב": "Merav Cohen",
    "שקד איילת": "Ayelet Shaked",
    "עודה איימן": "Ayman Odeh",
    "עבאס מנסור": "Mansour Abbas",
    "קריב גלעד": "Gilad Kariv",
    "כץ ישראל": "Israel Katz",
    "מלכיאלי מיכאל": "Michael Malchieli",
    "גפני משה": "Moshe Gafni",
    "גולדקנופ יצחק": "Yitzhak Goldknopf",
    "פורר עודד": "Oded Forer",
    "רביבו אליהו": "Eliyahu Revivo",
    "סולומון משה": "Moshe Solomon",
    "כהן מאיר": "Meir Cohen",
    "שוסטר אלון": "Alon Schuster",
    "מרגי יעקב": "Yaakov Margi",
    "מקלב אורי": "Uri Maklev",
    "פינדרוס יצחק זאב": "Yitzhak Pindrus",
    "פרוש מאיר": "Meir Porush",
    "שטרן אלעזר": "Elazar Stern",
    "קרעי שלמה": "Shlomo Karhi",
    "עמאר חמד": "Waleed Taha",
    "טאהא ווליד": "Walid Taha",
    "כץ אופיר": "Ofir Katz",
    "לוי מיקי": "Miki Levy",
    "לוי ירון": "Yaron Levy",
    "סגלוביץ יואב": "Yoav Segalovitz",
    "תמנו פנינה": "Pnina Tamano-Shata",
    "קלנר אריאל": "Ariel Kallner",
    "קרויזר יצחק": "Yitzhak Kroizer",
    "שירי נאור": "Naor Shiri",
    "להב הרצנו יוראי": "Yorai Lahav Hertzanu",
    "פסל משה": "Moshe Passal",
    "טור פז משה": "Moshe Tur-Paz",
    "צרפתי הרכבי מטי": "Mati Sarfatti Harkabi",
    "שיר סגמן מיכל": "Michal Shir Segman",
    "רון בן משה יעל": "Yael Ron Ben Moshe",
    "רייטן מרום אפרת": "Efrat Rayten Marom",
    "ח'טיב יאסין אימאן": "Iman Khatib-Yasin",
    "חוג'יראת יאסר": "Yasser Hujirat",
    "עבד עפיף": "Afif Abd",
    "טופורובסקי בועז": "Boaz Toporovsky",
    "מישרקי יונתן": "Yonatan Mishraki",
    "פוגל צביקה": "Zvika Fogel",
    "מלינובסקי יוליה": "Yulia Malinovsky",
    "סעדה משה": "Moshe Saada",
    "עזוז עדי": "Adi Azuz",
    "טרופר חילי": "Hili Tropper",
    "טייב יוסף": "Yosef Tayeb",
    "לזימי נעמה": "Naama Lazimi",
    "דלל אלי": "Eli Dalal",
    "דנינו שלום": "Shalom Danino",
    "טל אוהד": "Ohad Tal",
    "טסלר יעקב": "Yaakov Tessler",
    "שטרית קטי קטרין": "Keti Katrin Shtrit",
    "מלביצקי חנוך דב": "Chanoch Dov Malbitzky",
    "סובה יבגני": "Evgeny Sova",
    "מזרסקי טטיאנה": "Tatiana Mazarsky",
    "עטייה חוה אתי": "Eti Atia",
    "שקלים אושר": "Osher Shekalim",
    "מירון שלי טל": "Shelly Tal Meron",
    "מלקו צגה צגנש": "Tsega Melaku",
}


def name_variants_he(mk_name):
    """Generate Hebrew search variants for an MK name."""
    clean = re.sub(r'\s+', ' ', mk_name).strip()
    parts = clean.split()
    variants = []

    # Try "FirstName LastName" (reversed) — this is how Wikipedia articles are titled
    if len(parts) == 2:
        variants.append(f"{parts[1]} {parts[0]}")
        variants.append(clean)
    elif len(parts) == 3:
        # Could be "Last First1 First2" or "Last1 Last2 First"
        variants.append(f"{parts[-1]} {' '.join(parts[:-1])}")  # Last word is first name
        variants.append(f"{parts[1]} {parts[2]} {parts[0]}")  # First word is last name
        variants.append(f"{parts[1]} {parts[0]}")
        variants.append(f"{parts[-1]} {parts[0]}")
        variants.append(clean)
    elif len(parts) >= 4:
        variants.append(f"{parts[-1]} {' '.join(parts[:-1])}")
        variants.append(f"{parts[-1]} {parts[0]}")
        variants.append(clean)

    return variants


def fetch_photo_for_mk(name, faction):
    """Try multiple sources to find a photo for one MK."""
    safe_name = re.sub(r'[^\w\s-]', '', name).strip().replace(' ', '_')

    # Strategy 1: Direct Hebrew Wikipedia page lookup with name variants
    he_variants = name_variants_he(name)
    for variant in he_variants:
        thumb = get_page_image(HE_WIKI_API, variant)
        if thumb:
            return _save_photo(name, faction, thumb, safe_name)

    # Strategy 2: Hebrew Wikipedia search
    key_parts = re.sub(r'\s+', ' ', name).strip().split()
    key_check = [key_parts[0], key_parts[-1]] if len(key_parts) >= 2 else key_parts
    search_terms = he_variants + [f"{v} פוליטיקאי" for v in he_variants[:3]]
    thumb = search_wiki_image(HE_WIKI_API, search_terms, key_check)
    if thumb:
        return _save_photo(name, faction, thumb, safe_name)

    # Strategy 3: Wikidata
    for variant in he_variants[:3]:
        thumb = wikidata_get_image(variant)
        if thumb:
            return _save_photo(name, faction, thumb, safe_name)

    # Strategy 4: English Wikipedia (for well-known MKs)
    en_name = ENGLISH_NAMES.get(name)
    if en_name:
        thumb = get_page_image(EN_WIKI_API, en_name)
        if thumb:
            return _save_photo(name, faction, thumb, safe_name)
        thumb = search_wiki_image(EN_WIKI_API, [en_name])
        if thumb:
            return _save_photo(name, faction, thumb, safe_name)

    return name, {"photo_url": None, "faction": faction}


def _save_photo(name, faction, thumb_url, safe_name):
    """Download and save a photo, return result tuple."""
    ext = ".jpg" if ".jpg" in thumb_url.lower() or ".jpeg" in thumb_url.lower() else ".png"
    local_path = PHOTOS_DIR / f"{safe_name}{ext}"

    if download_photo(thumb_url, local_path):
        rel_path = f"data/photos/{safe_name}{ext}"
        return name, {"photo_url": rel_path, "faction": faction}
    return name, {"photo_url": None, "faction": faction}


def download_photo(url, dest):
    """Download a photo to local path."""
    try:
        req = urllib.request.Request(url)
        req.add_header("User-Agent", UA)
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = resp.read()
        if len(data) > 500:
            dest.write_bytes(data)
            return True
    except Exception:
        pass
    return False


def main():
    # Read MK names from mk_photos.json (which has all 121 MKs)
    out_path = DATA_DIR / "mk_photos.json"
    if out_path.exists():
        existing = json.loads(out_path.read_text("utf-8"))
    else:
        # Fall back to bills.json
        bills = json.loads((DATA_DIR / "bills.json").read_text("utf-8"))
        existing = {}
        for bill in bills:
            for mv in bill.get("mk_votes", []):
                name = mv["mk_name"]
                if name not in existing:
                    existing[name] = {"photo_url": None, "faction": mv.get("faction", "")}

    already_have = {n for n, d in existing.items() if d.get("photo_url")}
    to_fetch = sorted(set(existing.keys()) - already_have)
    print(f"Total MKs: {len(existing)}, have photos: {len(already_have)}, to fetch: {len(to_fetch)}")

    mk_photos = dict(existing)
    photos_found = len(already_have)

    # Fetch in parallel (4 threads to be polite to Wikipedia)
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = {
            executor.submit(fetch_photo_for_mk, name, existing.get(name, {}).get("faction", "")): name
            for name in to_fetch
        }
        for i, future in enumerate(as_completed(futures)):
            name, result = future.result()
            mk_photos[name] = result
            if result["photo_url"]:
                photos_found += 1
                print(f"  [{i+1}/{len(to_fetch)}] PHOTO: {name}")
            else:
                print(f"  [{i+1}/{len(to_fetch)}] MISS:  {name}")
            if (i + 1) % 20 == 0:
                # Save periodically
                out_path.write_text(json.dumps(mk_photos, ensure_ascii=False, indent=2), "utf-8")
                print(f"  ... saved ({photos_found} photos)")

    out_path.write_text(json.dumps(mk_photos, ensure_ascii=False, indent=2), "utf-8")
    print(f"\nSaved {len(mk_photos)} MK entries to {out_path}")
    print(f"  Photos found: {photos_found}, Avatars needed: {len(mk_photos) - photos_found}")


if __name__ == "__main__":
    main()
