import streamlit as st
import requests
import pandas as pd
from datetime import datetime, timezone
from time import sleep

# ---------------------------
# Config
# ---------------------------
OVERPASS_URL = "https://overpass-api.de/api/interpreter"
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
CACHE_TTL = 300  # seconds

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
    resp = requests.get(NOMINATIM_URL, params=params, headers={'User-Agent': 'streamlit-app'})
    resp.raise_for_status()
    data = resp.json()
    if not data:
        return None
    return float(data[0]['lat']), float(data[0]['lon'])

@st.cache_data(ttl=CACHE_TTL)
def fetch_leads(lat, lon, radius_m, niches):
    """Fetch raw lead nodes from Overpass API."""
    # Build Overpass QL query
    filters = []
    if niches:
        for key, val in niches:
            filters.append(f"node(around:{radius_m},{lat},{lon})[phone][!website][{key}={val}];")
    else:
        filters.append(f"node(around:{radius_m},{lat},{lon})[phone][!website];")
    query = """
[out:json][timeout:25];
(
%s
);
out body;
""" % "\n".join(filters)
    resp = requests.post(OVERPASS_URL, data={'data': query}, headers={'User-Agent': 'streamlit-app'})
    resp.raise_for_status()
    return resp.json().get('elements', [])

# ---------------------------
# Helper functions
# ---------------------------
def assemble_address(tags):
    parts = [tags.get('addr:housenumber', ''), tags.get('addr:street', ''),
             tags.get('addr:city', ''), tags.get('addr:state', ''), tags.get('addr:postcode', '')]
    return " ".join([p for p in parts if p])

# ---------------------------
# Streamlit UI
# ---------------------------
st.title("Lead Finder: New Businesses with No Website 📇")

# Sidebar inputs
st.sidebar.header("Search Parameters")
zip_code = st.sidebar.text_input("ZIP Code (5-digit)")
if st.sidebar.button("Lookup ZIP"):  # trigger lookup
    coords = None
    try:
        coords = geocode_zip(zip_code)
        if not coords:
            st.sidebar.error("Invalid ZIP code.")
        else:
            st.sidebar.success(f"Found: {coords[0]:.4f}, {coords[1]:.4f}")
    except Exception as e:
        st.sidebar.error(f"Geocode error: {e}")

# Maintain state of coords
coords = st.session_state.get('coords') if 'coords' in st.session_state else None
if 'coords' not in st.session_state and zip_code:
    try:
        c = geocode_zip(zip_code)
        if c:
            st.session_state['coords'] = c
            coords = c
    except:
        coords = None

radius = st.sidebar.slider("Radius (miles)", 5, 100, 25)
lookback = st.sidebar.slider("Look back (days)", 0, 60, 7)

# Define available niches (OSM tag filters)
niche_options = {
    "Fitness Shop": ("shop", "fitness"),
    "Fitness Centre": ("leisure", "fitness_centre"),
    "Café": ("amenity", "cafe"),
    "Beauty Shop": ("shop", "beauty")
}
selected = st.sidebar.multiselect("Niches", list(niche_options.keys()))
niches = [niche_options[n] for n in selected]

# Blacklist chains
default_chains = ["Starbucks", "McDonald", "Planet Fitness"]
blacklist_text = st.sidebar.text_area("Blacklist Chains (one per line)", "\n".join(default_chains))
blacklist = [b.strip().lower() for b in blacklist_text.splitlines() if b.strip()]

if st.sidebar.button("Refresh Leads"):
    st.experimental_rerun()

# Main
if not coords:
    st.info("Enter a valid U.S. ZIP code and click 'Lookup ZIP' to begin.")
    st.stop()

lat, lon = coords
radius_m = radius * 1609.34

# Fetch leads
with st.spinner("Fetching leads..."):
    try:
        raw = fetch_leads(lat, lon, radius_m, niches)
    except Exception as e:
        st.error(f"Overpass error: {e}")
        st.stop()

# Process leads into DataFrame
records = []
now = datetime.now(timezone.utc)
for node in raw:
    tags = node.get('tags', {})
    name = tags.get('name')
    phone = tags.get('phone')
    if not name or not phone:
        continue
    # Blacklist filter
    if any(bl in name.lower() for bl in blacklist):
        continue
    # Timestamp
    ts_str = node.get('timestamp')
    try:
        ts = datetime.fromisoformat(ts_str.replace('Z', '+00:00'))
        days_since = (now - ts).days
    except:
        days_since = None
    if days_since is None or days_since > lookback:
        continue
    # Address
    address = assemble_address(tags)
    # Score computation
    freshness_score = ((lookback - days_since) / lookback * 50) if lookback > 0 else 50
    specificity_score = 30 if any(k in tags and (k, tags[k]) in niches for k in tags) else 10
    addr_parts = sum(1 for part in ['addr:housenumber','addr:street','addr:city','addr:state','addr:postcode'] if tags.get(part))
    address_score = addr_parts / 5 * 20
    total_score = min(100, round(freshness_score + specificity_score + address_score))
    records.append({
        'Name': name,
        'Phone': phone,
        'Address': address,
        'Days Since Listed': days_since,
        'Score': total_score,
        'lat': node.get('lat'),
        'lon': node.get('lon')
    })

if not records:
    st.warning("No leads found for these parameters.")
    st.stop()

# Create DataFrame
df = pd.DataFrame(records)
# Sort by Score desc
df = df.sort_values('Score', ascending=False)

# Display results
st.header(f"{len(df)} leads found")
# Click-to-call formatting
df['Phone'] = df['Phone'].apply(lambda x: f"tel:{x}")
st.dataframe(df[['Name','Phone','Address','Days Since Listed','Score']])

# Map view
st.map(df.rename(columns={'lat': 'latitude', 'lon': 'longitude'}))

# End of app
