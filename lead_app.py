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
# US bounding box to filter global data
US_LAT_MIN, US_LAT_MAX = 24.396308, 49.384358
US_LON_MIN, US_LON_MAX = -124.848974, -66.885444

# ---------------------------
# Utilities
# ---------------------------
def slugify(name: str) -> str:
    s = name.lower()
    return re.sub(r'[^a-z0-9]+', '-', s).strip('-')

@st.cache_data(ttl=CACHE_TTL)
def fetch_new_osm_nodes(hours: int, limit: int):
    """
    Fetch up to `limit` OSM nodes globally edited within last `hours` that lack a website tag.
    """
    since = (datetime.now(timezone.utc) - timedelta(hours=hours)).strftime('%Y-%m-%dT%H:%M:%SZ')
    # Overpass QL query
    q = f"node[newer:'{since}'][!website];"
    query = f"""
[out:json][timeout:60];
(
{q}
);
out meta {limit};
"""
    try:
        r = requests.post(OVERPASS_URL, data={'data': query}, headers={'User-Agent': USER_AGENT}, timeout=60)
        r.raise_for_status()
        return r.json().get('elements', [])
    except Exception:
        return []

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

def process_nodes(nodes, blacklist, hours):
    now = datetime.now(timezone.utc)
    records = []
    for n in nodes:
        lat = n.get('lat'); lon = n.get('lon')
        if lat is None or lon is None:
            continue
        # US-only filter
        if not (US_LAT_MIN <= lat <= US_LAT_MAX and US_LON_MIN <= lon <= US_LON_MAX):
            continue
        tags = n.get('tags', {})
        name = tags.get('name')
        if not name or any(bl in name.lower() for bl in blacklist):
            continue
        # recency
        ts = n.get('timestamp')
        try:
            dt = datetime.fromisoformat(ts.replace('Z','+00:00'))
            hours_since = (now - dt).total_seconds() / 3600
        except:
            continue
        if hours_since > hours:
            continue
        # contact
        phone = tags.get('phone') or tags.get('contact:phone')
        city = tags.get('addr:city','')
        if not phone and city:
            phone = enrich_phone(name, city)
        if not phone:
            continue
        # scoring
        freshness = max(0, (hours - hours_since) / hours * 70)
        address_score = 30 if tags.get('addr:street') else 0
        score = round(min(100, freshness + address_score))
        records.append({
            'Name': name,
            'Phone': phone,
            'City': city,
            'Hours Since Edit': round(hours_since,1),
            'Score': score,
            'lat': lat,
            'lon': lon
        })
    return pd.DataFrame(records).sort_values('Score', ascending=False)

# ---------------------------
# Streamlit UI
# ---------------------------

st.set_page_config(page_title="Lead Finder 3.0", layout="wide")
st.title("🚀 Lead Finder 3.0 by Rayan Pal")

# Sidebar controls
hours = st.sidebar.slider("Look back (hours)", 1, 168, 24)
limit = st.sidebar.slider("Max nodes to fetch", 100, 2000, 500, step=100)
black_text = st.sidebar.text_area("Blacklist substrings (one per line)", "starbucks\nMcDonald\nPizza Hut")
blacklist = [b.strip().lower() for b in black_text.splitlines() if b.strip()]
if st.sidebar.button("Fetch Leads"):
    st.experimental_rerun()

# Fetch
with st.spinner("Fetching latest OSM nodes..."):
    nodes = fetch_new_osm_nodes(hours, limit)

# Process
df = process_nodes(nodes, blacklist, hours)
if df.empty:
    st.warning("No leads found. Try increasing the look-back window or node limit.")
    st.stop()

# Display
df_display = df[['Name','Phone','City','Hours Since Edit','Score']]
st.header(f"{len(df_display)} leads found")
st.dataframe(df_display)
st.map(df.rename(columns={'lat':'latitude','lon':'longitude'}))

st.markdown("**Notes:**")
st.markdown(
"""
- Queries the newest OSM nodes (no website tag) from the last X hours.
- Filters to US bounding box for localized results.
- Enriches missing phone via YellowPages scrape.
- Scores by recency (70%) and address presence (30%).
- Fully handles HTTP errors silently, so the app won’t crash.
- Adjustable look-back and node-limit ensure fast, relevant results.
"""
)
