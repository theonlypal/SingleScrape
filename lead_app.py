import streamlit as st
import requests
import pandas as pd
import re
import socket
from datetime import datetime, timezone, timedelta
from slugify import slugify

# ---------------------------
# Config
# ---------------------------
USER_AGENT = 'streamlit-lead-finder'
OVERPASS_URL = "https://overpass-api.de/api/interpreter"
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
CACHE_TTL = 300  # seconds
MAX_LOOKBACK = 365
MAX_RADIUS = 100
TLDs = ['.com', '.net', '.biz', '.org', '.us']

# ---------------------------
# Caching Helpers
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
def fetch_osm_nodes(bbox):
    south, north, west, east = bbox
    area = f"({south},{west},{north},{east})"
    # fetch all new businesses by common tags
    clauses = [
        f"node{area}[shop];",
        f"node{area}[amenity];",
        f"node{area}[office];",
        f"node{area}[leisure];"
    ]
    query = f"""
[out:json][timeout:60];
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
def assemble_address(tags):
    parts = [tags.get('addr:housenumber',''), tags.get('addr:street',''), tags.get('addr:city',''), tags.get('addr:state',''), tags.get('addr:postcode','')]
    return ", ".join(p for p in parts if p)

@st.cache_data(ttl=CACHE_TTL)
def enrich_phone(name, city):
    try:
        params = {'search_terms': name, 'geo_location_terms': city}
        r = requests.get('https://www.yellowpages.com/search', params=params, headers={'User-Agent': USER_AGENT})
        r.raise_for_status()
        m = re.search(r"\(\d{3}\)\s*\d{3}-\d{4}", r.text)
        return m.group(0) if m else None
    except:
        return None

@st.cache_data(ttl=CACHE_TTL)
def has_website(name):
    domain_base = slugify(name)
    for tld in TLDs:
        domain = domain_base + tld
        # DNS check
        try:
            socket.gethostbyname(domain)
            # if DNS found, do HTTP HEAD
            r = requests.head(f"http://{domain}", timeout=5)
            if r.status_code < 400:
                return True
        except:
            pass
    return False

# ---------------------------
# Processing Logic
# ---------------------------

def process(nodes, blacklist, lookback_days):
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
        # address & city
        addr = assemble_address(tags)
        city = tags.get('addr:city','')
        # skip if likely has website
        if has_website(name):
            continue
        # phone/email
        phone = tags.get('phone') or tags.get('contact:phone')
        if not phone and city:
            phone = enrich_phone(name, city)
        if not phone:
            continue
        # score by freshness and address completeness
        freshness = (lookback_days - days) / lookback_days * 70
        addr_score = (1 if addr else 0) * 30
        score = round(min(100, freshness + addr_score))
        leads.append({'Name':name,'Contact':phone,'Address':addr,'Days':days,'Score':score,'lat':n['lat'],'lon':n['lon']})
    return sorted(leads, key=lambda x: x['Score'], reverse=True)

# ---------------------------
# Streamlit App
# ---------------------------
st.set_page_config(page_title="Lead Finder 2.0", layout="wide")
st.title("🚀 Lead Finder 2.0: Next-Gen Fresh Leads")

# Sidebar Controls
st.sidebar.header("Settings")
zip_code = st.sidebar.text_input("ZIP Code (5 digits)")
if st.sidebar.button("Lookup ZIP"):
    loc = geocode_zip(zip_code)
    if loc:
        st.session_state['geo'] = loc
        st.sidebar.success("Location found!")
    else:
        st.sidebar.error("Invalid ZIP code.")
if 'geo' not in st.session_state:
    st.info("Enter ZIP and click Lookup to start.")
    st.stop()
lat, lon, bbox = st.session_state['geo']
radius = st.sidebar.slider("Radius (miles)", 5, MAX_RADIUS, 25)
lookback = st.sidebar.slider("Look back (days)", 1, MAX_LOOKBACK, 30)
b_text = st.sidebar.text_area("Blacklist (one per line)", "starbucks\nmcDonald")
blacklist = [b.lower() for b in b_text.splitlines() if b.strip()]
if st.sidebar.button("Fetch Leads"):
    st.experimental_rerun()

# Fetch & Process
with st.spinner("🔍 Gathering raw businesses..."):
    nodes = fetch_osm_nodes(bbox)
leads = process(nodes, blacklist, lookback)

if not leads:
    st.error("No fresh unsaturated leads found. Try adjusting lookback or radius.")
    st.stop()

# Display
df = pd.DataFrame(leads)
st.header(f"{len(df)} leads found")
st.dataframe(df[['Name','Contact','Address','Days','Score']])
st.map(df.rename(columns={'lat':'latitude','lon':'longitude'}))
