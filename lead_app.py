import streamlit as st
import requests
import pandas as pd
import re
from datetime import datetime, timedelta, timezone

# ---------------------------
# Config Constants
# ---------------------------
OVERPASS_URL = "https://overpass-api.de/api/interpreter"
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
CACHE_TTL = 300
MAX_LOOKBACK = 365
MAX_RADIUS = 100
USER_AGENT = 'streamlit-lead-finder'

# ---------------------------
# Cached Helpers
# ---------------------------
@st.cache_data(ttl=CACHE_TTL)
def geocode_zip(zip_code):
    params = {'postalcode': zip_code, 'country': 'United States', 'format': 'json', 'limit': 1}
    r = requests.get(NOMINATIM_URL, params=params, headers={'User-Agent': USER_AGENT})
    r.raise_for_status()
    data = r.json()
    if not data:
        return None
    e = data[0]
    lat, lon = float(e['lat']), float(e['lon'])
    bbox = list(map(float, e['boundingbox']))
    return lat, lon, bbox

@st.cache_data(ttl=CACHE_TTL)
def fetch_nodes(bbox, niches):
    south, north, west, east = bbox
    area = f"({south},{west},{north},{east})"
    clauses = []
    if niches:
        for k, v in niches:
            clauses.append(f"node{area}[{k}={v}];")
    else:
        clauses.append(f"node{area}[~"^(shop|amenity|office|leisure)$"~".*"];" )
    query = f"""
[out:json][timeout:60];
(
{chr(10).join(clauses)}
);
out meta;
"""
    resp = requests.post(OVERPASS_URL, data={'data': query}, headers={'User-Agent': USER_AGENT})
    resp.raise_for_status()
    return resp.json().get('elements', [])

# ---------------------------
# Phone Enrichment
# ---------------------------
def enrich_phone(name, city):
    """Scrape YellowPages for first matching phone number."""
    try:
        params = {'search_terms': name, 'geo_location_terms': city}
        r = requests.get('https://www.yellowpages.com/search', params=params, headers={'User-Agent': USER_AGENT})
        r.raise_for_status()
        m = re.search(r"\(\d{3}\)\s*\d{3}-\d{4}", r.text)
        return m.group(0) if m else None
    except:
        return None

# ---------------------------
# Utility
# ---------------------------
def assemble_address(tags):
    parts = [tags.get('addr:housenumber',''), tags.get('addr:street',''), tags.get('addr:city',''), tags.get('addr:state',''), tags.get('addr:postcode','')]
    return ", ".join(p for p in parts if p)

# ---------------------------
# Main App
# ---------------------------
st.set_page_config(page_title="Lead Finder", layout="wide")
st.title("📇 Enhanced Lead Finder")

# Sidebar
st.sidebar.header("Controls")
zip_code = st.sidebar.text_input("ZIP Code")
if st.sidebar.button("Lookup"):
    loc = geocode_zip(zip_code)
    if loc:
        st.session_state['geo'] = loc
        st.sidebar.success("Found location.")
    else:
        st.sidebar.error("Invalid ZIP.")
if 'geo' not in st.session_state:
    st.info("Enter ZIP and click Lookup.")
    st.stop()
lat, lon, bbox = st.session_state['geo']
radius = st.sidebar.slider("Radius (mi)", 5, MAX_RADIUS, 25)
lookback = st.sidebar.slider("Look back (days)", 1, MAX_LOOKBACK, 30)

# Niches & Blacklist
i18n = {"Fitness Shop":("shop","fitness"),"Cafe":("amenity","cafe")}
sel = st.sidebar.multiselect("Niche", list(i18n.keys()))
niches = [i18n[k] for k in sel]
b_text = st.sidebar.text_area("Blacklist (one per line)", "Starbucks\nMcDonald")
blacklist = [b.lower() for b in b_text.splitlines() if b]

if st.sidebar.button("Fetch Leads"):
    st.experimental_rerun()

# Fetch & Process
with st.spinner("Querying OSM..."):
    nodes = fetch_nodes(bbox, niches)

now = datetime.now(timezone.utc)
results = []
for n in nodes:
    tags = n.get('tags',{})
    name = tags.get('name')
    if not name or any(bl in name.lower() for bl in blacklist): continue
    # timestamp
    ts = n.get('timestamp')
    try:
        dt = datetime.fromisoformat(ts.replace('Z','+00:00'))
        days = (now-dt).days
    except:
        continue
    if days>lookback: continue
    addr = assemble_address(tags)
    phone = tags.get('phone') or tags.get('contact:phone')
    if not phone and 'addr:city' in tags:
        phone = enrich_phone(name, tags['addr:city'])
    if not phone: continue
    score = round((max(0,lookback-days)/lookback*50) + (20 if addr else 0))
    results.append({'Name':name,'Phone':phone,'Address':addr,'Days':days,'Score':score,'lat':n['lat'],'lon':n['lon']})

if not results:
    st.error("No leads found—OSM data lag likely. Consider other data sources.")
    st.stop()

# Display
df = pd.DataFrame(results).sort_values('Score',ascending=False)
st.header(f"{len(df)} leads")
st.dataframe(df)
st.map(df.rename(columns={'lat':'latitude','lon':'longitude'}))
