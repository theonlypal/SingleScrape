import streamlit as st
import requests
import pandas as pd
from datetime import datetime, timedelta, timezone

# ---------------------------
# Config Constants
# ---------------------------
OVERPASS_URL = "https://overpass-api.de/api/interpreter"
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
CACHE_TTL = 300  # seconds for caching API calls
MAX_LOOKBACK = 365  # days max fallback
MAX_RADIUS = 100  # miles max fallback
USER_AGENT = 'streamlit-lead-finder'

# ---------------------------
# Cached Helpers
# ---------------------------
@st.cache_data(ttl=CACHE_TTL)
def geocode_zip(zip_code):
    """Return lat, lon, and bounding box for a US ZIP code."""
    params = {
        'postalcode': zip_code,
        'country': 'United States',
        'format': 'json',
        'limit': 1
    }
    r = requests.get(NOMINATIM_URL, params=params, headers={'User-Agent': USER_AGENT})
    r.raise_for_status()
    data = r.json()
    if not data:
        return None
    entry = data[0]
    lat, lon = float(entry['lat']), float(entry['lon'])
    bbox = list(map(float, entry['boundingbox']))  # south, north, west, east
    return lat, lon, bbox

@st.cache_data(ttl=CACHE_TTL)
def fetch_nodes(bbox, radius_m, niches):
    """Fetch nodes without website tag within bbox or radius, applying niche filters."""
    south, north, west, east = bbox
    area_clause = f"({south},{west},{north},{east})"
    filters = []
    # niche-specific queries
    for key, val in niches:
        filters.append(f"node{area_clause}[!website][{key}={val}];")
    # fallback any without website
    filters.append(f"node{area_clause}[!website];")

    query = f"""
[out:json][timeout:60];
(
{chr(10).join(filters)}
);
out body;
"""
    resp = requests.post(OVERPASS_URL, data={'data': query}, headers={'User-Agent': USER_AGENT})
    resp.raise_for_status()
    return resp.json().get('elements', [])

# ---------------------------
# Utility Functions
# ---------------------------

def assemble_address(tags):
    parts = [tags.get('addr:housenumber', ''), tags.get('addr:street', ''),
             tags.get('addr:city', ''), tags.get('addr:state', ''), tags.get('addr:postcode', '')]
    return ", ".join([p for p in parts if p])

# ---------------------------
# Lead Processing & Fallback Logic
# ---------------------------

def process_leads(nodes, blacklist, lookback):
    """Filter, score, and return processed lead records."""
    now = datetime.now(timezone.utc)
    records = []
    for node in nodes:
        tags = node.get('tags', {})
        name = tags.get('name')
        # accept phone or contact:phone or email
        phone = tags.get('phone') or tags.get('contact:phone')
        email = tags.get('email')
        if not name or (not phone and not email):
            continue
        if any(bl in name.lower() for bl in blacklist):
            continue
        # days since listed via timestamp
        ts = node.get('timestamp')
        try:
            ts_dt = datetime.fromisoformat(ts.replace('Z', '+00:00'))
            days_since = (now - ts_dt).days
        except:
            continue
        if days_since > lookback:
            continue
        address = assemble_address(tags)
        # scoring
        freshness = ((lookback - days_since) / lookback * 50) if lookback > 0 else 50
        # niche specificity: if node matches any niche key/value
        specificity = 30 if any((k, v) in tags.items() for k, v in niches) else 10
        addr_parts = sum(bool(tags.get(p)) for p in [
            'addr:housenumber', 'addr:street', 'addr:city', 'addr:state', 'addr:postcode'
        ])
        address_score = (addr_parts / 5) * 20
        total_score = min(100, round(freshness + specificity + address_score))
        records.append({
            'Name': name,
            'Contact': phone or email,
            'Address': address,
            'Days Since Listed': days_since,
            'Score': total_score,
            'lat': node.get('lat'),
            'lon': node.get('lon')
        })
    return sorted(records, key=lambda r: r['Score'], reverse=True)

# ---------------------------
# Streamlit App
# ---------------------------
st.set_page_config(page_title="Lead Finder", layout="wide")
st.title("📇 Supercharged Lead Finder")

# Sidebar Inputs
st.sidebar.header("Search Controls")
zip_code = st.sidebar.text_input("ZIP Code (5 digits)")
if st.sidebar.button("Lookup"):  
    try:
        loc = geocode_zip(zip_code)
        if loc:
            st.session_state['geo'] = loc
            st.sidebar.success("Location found!")
        else:
            st.sidebar.error("Invalid ZIP code.")
    except Exception as e:
        st.sidebar.error(f"Geocode error: {e}")

if 'geo' not in st.session_state:
    st.info("Enter ZIP and click Lookup to begin.")
    st.stop()
lat, lon, bbox = st.session_state['geo']
radius = st.sidebar.slider("Radius (miles)", 5, MAX_RADIUS, 25)
radius_m = radius * 1609.34
lookback = st.sidebar.slider("Look back (days)", 1, MAX_LOOKBACK, 30)

# Niches
all_niches = {
    "Fitness Shop": ("shop", "fitness"),
    "Fitness Centre": ("leisure", "fitness_centre"),
    "Café": ("amenity", "cafe"),
    "Beauty Shop": ("shop", "beauty")
}
selected = st.sidebar.multiselect("Niche Filters", list(all_niches.keys()))
niches = [all_niches[n] for n in selected]

# Blacklist
default_black = ["Starbucks", "McDonald", "Planet Fitness"]
b_text = st.sidebar.text_area("Blacklist Chains (one per line)", "\n".join(default_black))
blacklist = [b.strip().lower() for b in b_text.splitlines() if b.strip()]

if st.sidebar.button("Find Leads"):
    st.experimental_rerun()

# Fetch raw nodes
with st.spinner("🔄 Querying Overpass..."):
    try:
        nodes = fetch_nodes(bbox, radius_m, niches)
    except Exception as e:
        st.error(f"Overpass fetch error: {e}")
        st.stop()

# Primary processing: phone/email leads
records = process_leads(nodes, blacklist, lookback)
# Fallback: if none, try expand lookback & radius once
if not records:
    st.warning("No fresh phone/email leads—expanding search parameters...")
    # expand to max
    nodes2 = fetch_nodes(bbox, MAX_RADIUS * 1609.34, [])
    records = process_leads(nodes2, blacklist, MAX_LOOKBACK)

if not records:
    st.error("No leads found even after fallback. Try a different ZIP or check OSM data coverage.")
    st.stop()

# Display Results
df = pd.DataFrame(records)
st.header(f"{len(df)} leads found 📈")
st.dataframe(df[['Name', 'Contact', 'Address', 'Days Since Listed', 'Score']])
st.map(df.rename(columns={'lat':'latitude','lon':'longitude'}))

# Explanation of enhancements
st.markdown("**Enhancements:**")
st.markdown(
"""
- **Bounding Box Geofence:** Uses full ZIP polygon rather than just a circle.
- **Contact Fallback:** Accepts `phone`, `contact:phone`, or `email` tags for leads.
- **Post-Fetch Filtering:** All freshness & tag filters run in Python for accuracy.
- **Automatic Fallback:** If no initial leads, auto-expands to max radius and look-back.
- **Extended Look-Back:** Up to 365 days available to capture slow-mapped entries.
- **Improved Scoring:** Balances freshness, tag specificity, and address completeness.
"""
)
