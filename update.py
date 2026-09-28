"""Weekly update: re-read every school page on schoolkeuze020.nl and rebuild schools.js.

Exam results, Inspectie ratings, map positions and translations change rarely, so they come
from data/static.json. Only the dates, photos and school facts are refreshed.

Run with:  python3 update.py
"""
import datetime
import html
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).parent
BASE = "https://schoolkeuze020.nl"
MONTHS = {m: i for i, m in enumerate("januari februari maart april mei juni juli augustus september oktober november december".split(), 1)}


def fetch(url):
    """Download a page as text."""
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (school-map weekly update)"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8")


def clean(s):
    """Strip HTML tags and extra whitespace."""
    return html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", s))).strip()


def parse_school(slug, page):
    """Pull name, photo, website, highlights, facts and open days out of one school page."""
    s = {"slug": slug, "url": f"{BASE}/scholen/{slug}/"}
    first = lambda pattern: (m.group(1) if (m := re.search(pattern, page, re.S)) else None)
    s["name"] = clean(first(r"<h1[^>]*>(.*?)</h1>") or "")
    s["image"] = first(r'og:image" content="([^"]+)"')
    s["website"] = first(r'class="school-button">\s*<a href="([^"]+)"')
    s["opendays_url"] = first(r'class="school-top">.*?<a href="([^"]+)"')
    usps = first(r'class="school-usps">(.*?)</ul>')
    s["usps"] = [clean(x) for x in re.findall(r"<li>(.*?)</li>", usps or "", re.S)]
    for label, body in re.findall(r"<strong>(.*?)</strong>\s*<p>(.*?)</p>", page, re.S):
        s[clean(label)] = clean(body)
    s["days"] = []
    for item in re.findall(r'<div class="school-item">(.*?)</div>\s*</div>\s*(?=<div class="school-item">|</div>)', page, re.S):
        get = lambda cls: clean(m.group(1)) if (m := re.search(cls + r'[^"]*">(.*?)(?:</div>|$)', item, re.S)) else ""
        dm = re.match(r"(\d{1,2}) (\w+) (\d{4})$", get("school-date"))
        if not dm or dm.group(2) not in MONTHS:
            raise ValueError(f"{slug}: unreadable date {get('school-date')!r}")
        s["days"].append({"date": f"{dm.group(3)}-{MONTHS[dm.group(2)]:02d}-{int(dm.group(1)):02d}",
                          "time": get("school-time"), "info": get("school-info")})
    return s


def label(info):
    """Turn the school's Dutch description into a short English event type."""
    i = re.split(r"[.,]", info.lower())[0]  # first phrase only: "Open Dag. ... aansluitend lesjes" is an open day
    if re.search(r"lesjes|lesmiddag|minilessen|meeloop|xperience", i): return "Trial lessons"
    if "kijk- en doe" in i or "workshop" in i: return "Taster afternoon"
    if "tour" in i: return "School tour"
    if re.search(r"informatie|voorlichting|infomiddag", i):
        return "Info session + trial lessons" if "lesjes" in info.lower() else "Info session"
    if "open avond" in i: return "Open evening"
    return "Open day"


def signup(info):
    """Say whether visitors need to register, if the school mentions it."""
    i = info.lower()
    if re.search(r"niet nodig|hoeft .*niet|niet vooraf|niet van tevoren|niet in te schrijven", i): return "No sign-up needed"
    if "basisschool" in i: return "Sign up via your primary school"
    if re.search(r"aanmeld|inschrij|geef je op|verplicht", i): return "Sign-up needed"
    return None


def fmt_time(t):
    """'18.00 tot 21.00 uur' -> '18:00–21:00'."""
    t = re.sub(r"(\d{1,2})\.(\d{2})", r"\1:\2", t.replace(" uur", ""))
    t = t.replace(" tot ", "–").replace(" en ", " & ").replace("-", "–")
    return re.sub(r"\s*–\s*", "–", t).strip()


def en_text(v):
    """Translate the short Dutch lesson-length / start-time strings, e.g. '45 of 90 minuten' -> '45 or 90 min'."""
    if not v or v.lower() in ("nvt", "n.v.t."): return None
    v = re.sub(r"\b0?(\d{1,2})[.:](\d{2})\b", r"\1:\2", v)
    for nl, en in [("minuten", "min"), ("min.", "min"), (" of ", " or "), (" en ", " & "), ("1 klokuur", "60 min"),
                   ("dinsdag", "Tuesday"), ("(startmoment)", ""), (" uur", "")]:
        v = v.replace(nl, en)
    v = v.strip()
    return v + " min" if v.isdigit() else v


def geocode(address):
    """Find a new school's position with PDOK (the Dutch government address service)."""
    m = re.match(r"(.+?)\s+(\d+)", address)
    city = "Weesp" if "Weesp" in address else "Amstelveen" if "Amstelveen" in address else "Amsterdam"
    params = [("q", f"{m.group(1)} {m.group(2)}"), ("rows", 1), ("fq", "type:adres"),
              ("fq", f"woonplaatsnaam:{city}"), ("fq", f"huisnummer:{m.group(2)}")]
    doc = json.loads(fetch("https://api.pdok.nl/bzk/locatieserver/search/v3_1/free?" + urllib.parse.urlencode(params)))["response"]["docs"][0]
    lng, lat = map(float, re.match(r"POINT\(([\d.]+) ([\d.]+)\)", doc["centroide_ll"]).groups())
    return round(lat, 6), round(lng, 6)


def level_group(level):
    """Map a level like 'vmbo-tl' to the filter group shown on the site."""
    if level.startswith("vmbo"): return "VMBO"
    return {"havo": "HAVO", "vwo": "VWO", "praktijkonderwijs": "Praktijk", "vso": "VSO"}.get(level)


def build(static, pages):
    """Combine freshly scraped school pages with the saved static data."""
    out, warnings = [], []
    for slug, page in pages.items():
        if slug in static["skip"]:
            continue
        s = parse_school(slug, page)
        area, _, raw_addr = s["Stadsdeel"].partition("(")
        known = static["schools"].get(slug)
        if known:
            address, lat, lng = known["address"], known["lat"], known["lng"]
        else:
            address = re.sub(r",?\s*\d{4}\s?[A-Z]{2}.*$", "", raw_addr.rstrip(") ")).strip()
            try:
                lat, lng = geocode(address)
            except Exception:
                warnings.append(f"NEW school {s['name']} skipped: could not find '{address}' on the map")
                continue
            warnings.append(f"NEW school added: {s['name']} ({address}); no exam or inspection data yet")
        untranslated = [u for u in s["usps"] if u not in static["highlights_en"]]
        if untranslated:
            warnings.append(f"{s['name']}: highlights shown in Dutch (no translation yet): {untranslated}")
        levels = [{"kleinschalig ondersteunend onderwijs (kovo)": "kovo"}.get(l.strip(), l.strip()) for l in s["Schoolsoorten"].split(",")]
        days = [{"type": label(d["info"]), "date": d["date"], "time": fmt_time(d["time"]),
                 **({"signup": signup(d["info"])} if signup(d["info"]) else {})} for d in s["days"]]
        out.append({
            "name": s["name"], "area": area.strip(), "address": address, "lat": lat, "lng": lng,
            "levels": levels, "groups": sorted({level_group(l) for l in levels} - {None}),
            "pupils": int(s["Aantal leerlingen"]) if s.get("Aantal leerlingen", "").isdigit() else None,
            "lesson_length": en_text(s.get("Duur lessen")), "start_time": en_text(s.get("Starttijd eerste les")),
            "website": s["website"], "dates_url": s["opendays_url"] or s["website"], "profile_url": s["url"],
            "image": s["image"], "image_credit": "schoolkeuze020.nl",
            "open_days": sorted(days, key=lambda d: (d["date"], d["time"])),
            "highlights": [static["highlights_en"].get(u, u) for u in s["usps"]],
            "pros": known["pros"] if known else [], "cons": known["cons"] if known else [],
            "facts": known["facts"] if known else ["No exam or inspection record on file yet"],
            "sources": [{"label": "schoolkeuze020.nl", "url": s["url"]}] + (known["extra_sources"] if known else []),
        })
    # nudge pins that share an exact spot so both stay clickable
    seen = {}
    for o in out:
        k = (o["lat"], o["lng"])
        o["lng"] = round(o["lng"] + 0.00025 * seen.get(k, 0), 6)
        seen[k] = seen.get(k, 0) + 1
    return out, warnings


def main():
    static = json.loads((HERE / "data/static.json").read_text(encoding="utf-8"))
    slugs = sorted(set(re.findall(rf"{BASE}/scholen/([a-z0-9-]+)/", fetch(f"{BASE}/scholen/"))) - {"feed", "page"})
    pages = {}
    for slug in slugs:
        pages[slug] = fetch(f"{BASE}/scholen/{slug}/")
        time.sleep(0.3)
    schools, warnings = build(static, pages)
    for w in warnings:
        print("WARNING:", w)
    # Fail loudly rather than publish a broken site (e.g. if schoolkeuze020 changes its layout)
    events = sum(len(s["open_days"]) for s in schools)
    if len(schools) < 70 or not all(s["name"] and s["image"] for s in schools):
        sys.exit(f"ERROR: only {len(schools)} complete schools found – the page layout may have changed. Site NOT updated.")
    today = datetime.date.today().isoformat()
    header = ("// Generated by update.py from schoolkeuze020.nl (dates, photos, school info), DUO open data (exam results),\n"
              "// Inspectie van het Onderwijs open data (ratings, 1 Sept 2026) and PDOK (map positions).\n")
    (HERE / "school-map" / "schools.js").write_text(
        header + f'window.LAST_CHECKED = "{today}";\n'
        + "window.SCHOOLS = " + json.dumps(schools, ensure_ascii=False, indent=1) + ";\n", encoding="utf-8")
    print(f"Updated schools.js: {len(schools)} schools, {sum(1 for s in schools if s['open_days'])} with dates, {events} events.")


if __name__ == "__main__":
    main()
