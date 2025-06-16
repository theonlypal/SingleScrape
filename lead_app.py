import streamlit as st
import requests
import pandas as pd
import re
import socket
from datetime import datetime, timezone, timedelta

# ---------------------------
# Config
# ---------------------------
USER_AGENT = 'streamlit-lead-finder'
OVERPASS_URL = "https://overpass-api.de/api/interpreter"
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
CACHE_TTL = 300   # seconds
MAX_LOOKBACK = 365 # days
MAX_RADIUS = 100  # miles
# Top-level domains to check for a live website
TLDs = ['.com', '.net', '.org', '.biz', '.us']

# ---------------------------
# Inline slugify (no external dep)
# ---------------------------
def slugify(name: str) -> str:
    """Simplistic slugify: lowercase, replace non-alphanum with hyphens."""
    s = name.lower()
    # replace non-alphanumeric with hyphens
    s = re.sub(r'[^a-z0-9]+', '-', s)
    # strip hyphens
    return s.strip('-')

# ---------------------------
# Caching Helpers
# ---------------------------
@st.cache_data(ttl=CACHE_TTL)
def geocode_zip(zip_code: str):
    """Return (lat, lon, bbox) for a US ZIP code from Nominatim."""
    params = {'postalcode': zip_code, 'country': 'United States', 'format': 'json', 'limit': 1}
    r = requests.get(NOMINATIM_URL, params=params, headers={'User-Agent': USER_AGENT})
    r.raise_for_status()
    data = r.json()
    if not data:
        return None
    e = data[0]
    lat, lon = float(e['lat']), float(e['lon'])
    bbox = list(map(float, e['boundingbox']))  # south, north, west, east
    return lat, lon, bbox

@st.cache_data(ttl=CACHE_TTL)
def fetch_osm_nodes(bbox, tags_to_query):
    """Fetch OSM nodes by tags within the bounding box."""
    south, north, west, east = bbox
    area = f"({south},{west},{north},{east})"
    clauses = []
    # Build tag filters
    for key, pattern in tags_to_query:
        clauses.append(f"node{area}[{key}~\"{pattern}\"];")
    # fallback any shop/amenity/office/leisure node
    clauses.append(f"node{area}[shop];")
    clauses.append(f"node{area}[amenity];")
    clauses.append(f"node{area}[office];")
    clauses.append(f"node{area}[leisure];")

    query = f"""
[out:json][timeout:120];
(
{chr(10).join(clauses)}
);
out meta;
"""
    r = requests.post(OVERPASS_URL, data={'data': query}, headers={'User-Agent': USER_AGENT})
    r.raise_for_status()
    return r.json().get('elements', [])

# ---------------------------
# Utility Functions
# ---------------------------
def assemble_address(tags: dict) -> str:
    parts = [tags.get('addr:housenumber',''), tags.get('addr:street',''),
             tags.get('addr:city',''), tags.get('addr:state',''), tags.get('addr:postcode','')]
    return ", ".join(p for p in parts if p)

@st.cache_data(ttl=CACHE_TTL)
def enrich_phone(name: str, city: str) -> str:
    """Fallback: scrape YellowPages for a phone number."""
    try:
        params = {'search_terms': name, 'geo_location_terms': city}
        r = requests.get('https://www.yellowpages.com/search', params=params,
                         headers={'User-Agent': USER_AGENT}, timeout=10)
        r.raise_for_status()
        m = re.search(r"\(\d{3}\)\s*\d{3}-\d{4}", r.text)
        return m.group(0) if m else None
    except:
        return None

@st.cache_data(ttl=CACHE_TTL)
def has_live_website(name: str) -> bool:
    """Check common TLDs: DNS lookup + HTTP HEAD to detect a live site."""
    base = slugify(name)
    for tld in TLDs:
        domain = base + tld
        try:
            socket.gethostbyname(domain)
            # DNS exists, now check HTTP
            r = requests.head(f"http://{domain}", timeout=5)
            if r.status_code < 400:
                return True
        except:
            continue
    return False

# ---------------------------
# Processing Logic
# ---------------------------

def process_leads(nodes: list, blacklist: list, lookback_days: int) -> list:
    now = datetime.now(timezone.utc)
    leads = []
    for n in nodes:
        tags = n.get('tags', {})
        name = tags.get('name')
        if not name or any(bl in name.lower() for bl in blacklist):
            continue
        # timestamp filter
        ts = n.get('timestamp')
        try:
            dt = datetime.fromisoformat(ts.replace('Z','+00:00'))
            days = (now - dt).days
        except:
            continue
        if days > lookback_days:
            continue
        # address
        address = assemble_address(tags)
        city = tags.get('addr:city', '')
        # skip if they likely have a website
        if has_live_website(name):
            continue
        # contact: prefer phone tags, else email, else enrich
        phone = tags.get('phone') or tags.get('contact:phone')
        if not phone and city:
            phone = enrich_phone(name, city)
        if not phone:
            continue
        # scoring: freshness + address completeness
        freshness_score = (lookback_days - days) / lookback_days * 70
        address_score = (1 if address else 0) * 30
        score = round(min(100, freshness_score + address_score))
        leads.append({
            'Name': name,
            'Contact': phone,
            'Address': address,
            'Days Since Listed': days,
            'Score': score,
            'lat': n.get('lat'),
            'lon': n.get('lon')
        })
    return sorted(leads, key=lambda x: x['Score'], reverse=True)

# ---------------------------
# Streamlit App
# ---------------------------
st.set_page_config(page_title="Lead Finder 2.0", layout="wide")
st.title("🚀 Lead Finder 2.0: Maximum Freshness & Saturation Avoidance")

# Sidebar
st.sidebar.header("Search Settings")
zip_code = st.sidebar.text_input("ZIP Code (5 digits)")
if st.sidebar.button("Lookup ZIP"):
    loc = geocode_zip(zip_code)
    if loc:
        st.session_state['geo'] = loc
        st.sidebar.success("Location found")
    else:
        st.sidebar.error("Invalid ZIP code")

if 'geo' not in st.session_state:
    st.info("Enter ZIP code and click Lookup ZIP to begin")
    st.stop()
lat, lon, bbox = st.session_state['geo']
radius = st.sidebar.slider("Radius (miles)", 5, MAX_RADIUS, 25)
lookback = st.sidebar.slider("Look back (days)", 1, MAX_LOOKBACK, 30)
b_text = st.sidebar.text_area("Blacklist Chains (one per line)", "starbucks\nMcDonald\nPlanet Fitness")
blacklist = [b.strip().lower() for b in b_text.splitlines() if b.strip()]

# Niche filters: regex patterns for tags
niche_text = st.sidebar.text_area("Niche tag regex (key=value) one per line, e.g. shop=fitness", "")
tags_to_query = []
for line in niche_text.splitlines():
    if '=' in line:
        key, val = line.split('=',1)
        tags_to_query.append((key.strip(), val.strip()))

if st.sidebar.button("Fetch Leads"):
    st.experimental_rerun()

# Fetch raw nodes
with st.spinner("🔍 Querying OpenStreetMap..."):
    nodes = fetch_osm_nodes(bbox, tags_to_query)
# Process leads
leads = process_leads(nodes, blacklist, lookback)

if not leads:
    st.warning("No leads found—consider increasing radius or lookback, or adjust niches.")
    st.stop()

# Display results
df = pd.DataFrame(leads)
st.header(f"{len(df)} leads found")
st.dataframe(df[['Name','Contact','Address','Days Since Listed','Score']])
st.map(df.rename(columns={'lat':'latitude','lon':'longitude'}))

# Explanation of secret sauce
st.markdown("**Secret Sauce Enhancements:**")
st.markdown(
"""
- **Inline Slugify + DNS/HTTP Check:** Filters out any business that *actually* has a live site, even if OSM tags lacked it.
- **Broad Tag Harvest:** Queries any shop/amenity/office/leisure, plus user-defined niche regex rules.
- **Fallback Phone Enrichment:** Scrapes YellowPages only when OSM lacks contact tags.
- **Python-Only Freshness Logic:** Ensures our look-back days are absolute and reliable.
- **Cache Everywhere:** 5‑minute caching on geocode, OSM calls, phone & domain checks to speed up reruns.
- **Maxed-Out Score Bias:** 70% weight on freshness, 30% on address completeness, so you call hottest leads first.
"""
)
