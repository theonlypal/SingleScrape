import streamlit as st
import requests
import pandas as pd
from datetime import datetime, timedelta, timezone

# ---------------------------
# Config
# ---------------------------
OVERPASS_URL = "https://overpass-api.de/api/interpreter"
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
CACHE_TTL = 300  # cache API responses for 5 minutes

# ---------------------------
# Cached functions
# ---------------------------
@st.cache_data(ttl=CACHE_TTL)
def geocode_zip(zip_code):
    """Geocode a ZIP code to (lat, lon) via Nominatim."""
    params = {
        'postalcode': zip_code,
        'country': 'United States',
        'format': 'json',
        'limit': 1
    }
    resp = requests.get(NOMINATIM_URL, params=params, headers={'User-Agent': 'streamlit-lead-app'})
    resp.raise_for_status()
    data = resp.json()
    if not data:
        return None
    return float(data[0]['lat']), float(data[0]['lon'])

@st.cache_data(ttl=CACHE_TTL)
def fetch_leads(lat, lon, radius_m, threshold_iso, niches):
    """Fetch raw lead nodes from Overpass API using both phone and contact:phone tags."""
    clauses = []
    # Determine base filters: niches or all
    niche_filters = niches if niches else [(None, None)]
    for key, val in niche_filters:
        # nodes with phone
        q = f"node(around:{radius_m},{lat},{lon})"
        q += f"[newer:\"{threshold_iso}\"][!website]"
        if key and val:
            q += f"[{key}={val}]"
        q += "[phone];"
        clauses.append(q)
        # nodes with contact:phone
        q2 = f"node(around:{radius_m},{lat},{lon})"
        q2 += f"[newer:\"{threshold_iso}\"][!website]"
        if key and val:
            q2 += f"[{key}={val}]"
        q2 += "[contact:phone];"
        clauses.append(q2)

    query = (
        "[out:json][timeout:25];(\n" + "\n".join(clauses) + "\n);out body;"
    )
    resp = requests.post(OVERPASS_URL, data={'data': query}, headers={'User-Agent': 'streamlit-lead-app'})
    resp.raise_for_status()
    return resp.json().get('elements', [])

# ---------------------------
# Helper functions
# ---------------------------
def assemble_address(tags):
    parts = [tags.get('addr:housenumber', ''), tags.get('addr:street', ''),
             tags.get('addr:city', ''), tags.get('addr:state', ''), tags.get('addr:postcode', '')]
    return ", ".join([p for p in parts if p])

# ---------------------------
# Streamlit UI
# ---------------------------
st.set_page_config(page_title="Lead Finder", layout="wide")
st.title("📇 Lead Finder")

# Sidebar
st.sidebar.header("🔍 Search Parameters")
zip_code = st.sidebar.text_input("ZIP Code (5 digits)")
if st.sidebar.button("Lookup ZIP"):
    coords = None
    try:
        coords = geocode_zip(zip_code)
        if coords:
            st.session_state['coords'] = coords
            st.sidebar.success(f"Location found: {coords[0]:.4f}, {coords[1]:.4f}")
        else:
            st.sidebar.error("Invalid ZIP code.")
    except Exception as e:
        st.sidebar.error(f"Geocoding error: {e}")

coords = st.session_state.get('coords')
if not coords:
    st.info("Enter a valid U.S. ZIP code and click 'Lookup ZIP' to begin.")
    st.stop()
lat, lon = coords

radius = st.sidebar.slider("Radius (miles)", 5, 100, 25)
lookback = st.sidebar.slider("Look back (days)", 0, 60, 7)

# Compute ISO timestamp threshold for Overpass
now = datetime.now(timezone.utc)
threshold = now - timedelta(days=lookback)
threshold_iso = threshold.strftime("%Y-%m-%dT%H:%M:%SZ")

# Niche selection
i18n = {
    "Fitness Shop": ("shop", "fitness"),
    "Fitness Centre": ("leisure", "fitness_centre"),
    "Café": ("amenity", "cafe"),
    "Beauty Shop": ("shop", "beauty")
}
selected = st.sidebar.multiselect("Niches", list(i18n.keys()))
niches = [i18n[name] for name in selected]

# Blacklist
default = ["Starbucks", "McDonald", "Planet Fitness"]
black_text = st.sidebar.text_area("Blacklist Chains (one per line)", "\n".join(default))
blacklist = [b.lower() for b in black_text.splitlines() if b.strip()]

if st.sidebar.button("Refresh Leads"):
    st.experimental_rerun()

# Fetch and process leads
radius_m = radius * 1609.34
with st.spinner("Fetching leads..."):
    try:
        raw = fetch_leads(lat, lon, radius_m, threshold_iso, niches)
    except Exception as e:
        st.error(f"Overpass error: {e}")
        st.stop()

records = []
for node in raw:
    tags = node.get('tags', {})
    # extract phone (phone OR contact:phone)
    phone = tags.get('phone') or tags.get('contact:phone')
    name = tags.get('name')
    if not name or not phone:
        continue
    # blacklist
    if any(bl in name.lower() for bl in blacklist):
        continue
    # compute days since listed
    ts = node.get('timestamp')
    try:
        ts_dt = datetime.fromisoformat(ts.replace('Z', '+00:00'))
        days_since = (now - ts_dt).days
    except:
        days_since = None
    if days_since is None or days_since > lookback:
        continue
    address = assemble_address(tags)
    # scoring
    freshness = ((lookback - days_since) / lookback * 50) if lookback > 0 else 50
    spec = 30 if any((key,val) in niches for key,val in tags.items()) else 10
    parts = sum(bool(tags.get(p)) for p in [
        'addr:housenumber','addr:street','addr:city','addr:state','addr:postcode'
    ])
    addr_score = parts / 5 * 20
    score = min(100, round(freshness + spec + addr_score))
    records.append({
        'Name': name,
        'Phone': f"tel:{phone}",
        'Address': address,
        'Days Since Listed': days_since,
        'Score': score,
        'lat': node.get('lat'),
        'lon': node.get('lon')
    })

if not records:
    st.warning("No leads found for these parameters.")
    st.stop()

# DataFrame and display
df = pd.DataFrame(records).sort_values('Score', ascending=False)
st.header(f"{len(df)} leads found")
st.dataframe(df[['Name','Phone','Address','Days Since Listed','Score']])

# Map view
st.map(df.rename(columns={'lat':'latitude','lon':'longitude'}))
