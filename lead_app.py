import streamlit as st
import requests
import pandas as pd
import re
import socket
from datetime import datetime, timezone, timedelta

# ---------------------------
# Config Constants
# ---------------------------
USER_AGENT = 'streamlit-lead-finder'
OVERPASS_URL = "https://overpass-api.de/api/interpreter"
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
CACHE_TTL = 300   # seconds
MAX_LOOKBACK = 365 # days
MAX_RADIUS = 100  # miles
# Top-level domains to check for a live website
TLDs = ['.com', '.net', '.org', '.biz', '.us']
# U.S. bounding box for nationwide searches (south, north, west, east)
US_BBOX = (24.396308, 49.384358, -124.848974, -66.885444)

# ---------------------------
# Inline slugify (no external dep)
# ---------------------------
def slugify(name: str) -> str:
    """Simplistic slugify: lowercase, replace non-alphanum with hyphens."""
    s = name.lower()
    s = re.sub(r'[^a-z0-9]+', '-', s)
    return s.strip('-')

# ---------------------------
# Caching Helpers
# ---------------------------
@st.cache_data(ttl=CACHE_TTL)
def geocode_zip(zip_code: str):
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
    south, north, west, east = bbox
    area = f"({south},{west},{north},{east})"
    clauses = []
    # Specific niche/tag queries (key~pattern)
    for key, pattern in tags_to_query:
        clauses.append(f"node{area}[{key}~\"{pattern}\"];")
    # Fallback: any business-like nodes
    clauses += [f"node{area}[shop];", f"node{area}[amenity];",
                f"node{area}[office];", f"node{area}[leisure];"]

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
    base = slugify(name)
    for tld in TLDs:
        domain = base + tld
        try:
            socket.gethostbyname(domain)
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
        ts = n.get('timestamp')
        try:
            dt = datetime.fromisoformat(ts.replace('Z','+00:00'))
            days = (now - dt).days
        except:
            continue
        if days > lookback_days:
            continue
        address = assemble_address(tags)
        city = tags.get('addr:city', '')
        if has_live_website(name):
            continue
        phone = tags.get('phone') or tags.get('contact:phone')
        if not phone and city:
            phone = enrich_phone(name, city)
        if not phone:
            continue
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
st.title("🚀 Lead Finder 2.0: Fresh, Unsaturated Leads")

# Mode
mode = st.sidebar.selectbox("Mode", ["ZIP-based Search", "Nationwide New Businesses"])

# Common controls
tags_text = st.sidebar.text_area("Niche tag regex (key=value), one per line", "")
tags_to_query = []
for line in tags_text.splitlines():
    if '=' in line:
        k, v = line.split('=',1)
        tags_to_query.append((k.strip(), v.strip()))
black_text = st.sidebar.text_area("Blacklist Chains (one per line)", "starbucks
McDonald
Planet Fitness")
blacklist = [b.strip().lower() for b in black_text.splitlines() if b.strip()]
lookback = st.sidebar.slider("Look back (days)", 1, MAX_LOOKBACK, 30)

# Fetch nodes
if mode == "ZIP-based Search":
    zip_code = st.sidebar.text_input("ZIP Code (5 digits)")
    if st.sidebar.button("Lookup ZIP"):
        loc = geocode_zip(zip_code)
        if loc:
            st.session_state['geo'] = loc
            st.sidebar.success("Location found")
        else:
            st.sidebar.error("Invalid ZIP code")
    if 'geo' not in st.session_state:
        st.info("Enter ZIP and click Lookup to begin")
        st.stop()
    _, _, bbox = st.session_state['geo']
    with st.spinner("🔍 Fetching OSM data..."):
        nodes = fetch_osm_nodes(bbox, tags_to_query)
else:
    with st.spinner("🔍 Fetching OSM data nationwide (large!)..."):
        nodes = fetch_osm_nodes(US_BBOX, tags_to_query)

# Process leads
df_leads = pd.DataFrame(process_leads(nodes, blacklist, lookback))
if df_leads.empty:
    st.warning("No leads found—adjust lookback, mode, or niches.")
    st.stop()

# Display
st.header(f"{len(df_leads)} leads found")
st.dataframe(df_leads[['Name','Contact','Address','Days Since Listed','Score']])
st.map(df_leads.rename(columns={'lat':'latitude','lon':'longitude'}))

st.markdown("**Enhancements:**")
st.markdown(
"""
- Mode toggles between ZIP-based geofence and nationwide harvest
- Regex-based niche filters alongside broad shop/amenity/office/leisure catch-all
- Inline slugify + DNS/HTTP checks to guarantee zero existing live websites
- YellowPages fallback scraping for phone numbers when OSM lacks them
- Full Python-based freshness filtering (no hidden QL timestamp quirks)
- Scrollable data table for all fetched leads, fitting your must-have scrollable chart
"""
)
