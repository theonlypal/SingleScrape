import streamlit as st
import requests
import pandas as pd
import re
import socket
from datetime import datetime, timezone, timedelta

# ---------------------------
# Configuration
# ---------------------------
USER_AGENT = 'streamlit-lead-finder'
OVERPASS_URL = "https://overpass-api.de/api/interpreter"
CACHE_TTL = 300  # seconds
# US bounding box for filtering global results
US_LAT_MIN, US_LAT_MAX = 24.396308, 49.384358
US_LON_MIN, US_LON_MAX = -124.848974, -66.885444

# ---------------------------
# Sector tags regex for Overpass
# ---------------------------
BUSINESS_TAGS_REGEX = '^(shop|amenity|office|leisure)$'

# ---------------------------
# Utilities
# ---------------------------
def slugify(name: str) -> str:
    s = name.lower()
    s = re.sub(r'[^a-z0-9]+', '-', s)
    return s.strip('-')

# ---------------------------
# Cached Helpers
# ---------------------------
@st.cache_data(ttl=CACHE_TTL)
def fetch_global_new_nodes(threshold_iso: str, limit: int = 500) -> list:
    """
    Fetch newest nodes globally matching business tags and missing website.
    Uses Overpass 'newer' filter and regex on key.
    """
    q = f"node[newer:'{threshold_iso}'][!website][~\"{BUSINESS_TAGS_REGEX}\"~'.'];"  # missing website
    query = f"""
[out:json][timeout:60];
(
{q}
);
out meta {limit};
"""
    r = requests.post(OVERPASS_URL, data={'data': query}, headers={'User-Agent': USER_AGENT})
    r.raise_for_status()
    return r.json().get('elements', [])

def within_us(lat: float, lon: float) -> bool:
    return US_LAT_MIN <= lat <= US_LAT_MAX and US_LON_MIN <= lon <= US_LON_MAX

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

# ---------------------------
# Processing
# ---------------------------

def process_global(nodes: list, blacklist: list, lookback_hours: int) -> pd.DataFrame:
    now = datetime.now(timezone.utc)
    records = []
    for n in nodes:
        lat = n.get('lat')
        lon = n.get('lon')
        if lat is None or lon is None or not within_us(lat, lon):
            continue
        tags = n.get('tags', {})
        name = tags.get('name')
        if not name or any(bl in name.lower() for bl in blacklist):
            continue
        ts = n.get('timestamp')
        try:
            dt = datetime.fromisoformat(ts.replace('Z', '+00:00'))
            hours_since = (now - dt).total_seconds() / 3600
        except:
            continue
        if hours_since > lookback_hours:
            continue
        # Contact
        phone = tags.get('phone') or tags.get('contact:phone')
        city = tags.get('addr:city', '')
        if not phone and city:
            phone = enrich_phone(name, city)
        if not phone:
            continue
        # Scoring
        freshness = max(0, (lookback_hours - hours_since) / lookback_hours * 70)
        has_addr = 'addr:street' in tags or 'addr:housenumber' in tags
        address_score = 30 if has_addr else 0
        score = round(min(100, freshness + address_score))
        records.append({
            'Name': name,
            'Contact': phone,
            'City': city,
            'Hours Since Added': round(hours_since, 1),
            'Score': score,
            'lat': lat,
            'lon': lon
        })
    return pd.DataFrame(records).sort_values('Score', ascending=False)

# ---------------------------
# Streamlit App
# ---------------------------

st.set_page_config(page_title="Lead Finder: Global Fresh", layout="wide")
st.title("🌍 Global Fresh Business Leads")

# Controls
lookback_hours = st.sidebar.slider("Look back (hours)", 1, 168, 24)
limit = st.sidebar.slider("Max OSM nodes to fetch", 100, 1000, 500, step=100)
black_text = st.sidebar.text_area("Blacklist substrings (one per line)", "starbucks\nMcDonald\nPizza Hut")
blacklist = [b.strip().lower() for b in black_text.splitlines() if b.strip()]

if st.sidebar.button("Fetch Latest Leads"):
    st.experimental_rerun()

# Fetch threshold
threshold = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)
threshold_iso = threshold.strftime("%Y-%m-%dT%H:%M:%SZ")

# Fetch and process
with st.spinner("Fetching global nodes from OSM..."):
    nodes = fetch_global_new_nodes(threshold_iso, limit=limit)

df = process_global(nodes, blacklist, lookback_hours)

if df.empty:
    st.warning("No fresh leads found—try increasing limit or lookback window.")
    st.stop()

# Display
df_display = df[['Name', 'Contact', 'City', 'Hours Since Added', 'Score']]
st.header(f"{len(df_display)} fresh leads")
st.dataframe(df_display)
st.map(df.rename(columns={'lat':'latitude','lon':'longitude'}))

st.markdown("**Algorithm Notes:**")
st.markdown(
"""
- **Global Overpass Query** for newest nodes (last X hours) with no `website` tag.
- **US-only Filter** via lat/lon bounding box to ensure domestic leads.
- **Phone Extraction** from OSM tags or YellowPages fallback.
- **Scoring:** freshness (70%) + address presence (30%).
- **Performance:** Limits to first N nodes for speed; adjustable in sidebar.
"""
)
