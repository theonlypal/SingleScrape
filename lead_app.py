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
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
OPENCORP_SEARCH = "https://api.opencorporates.com/v0.4/companies/search"
CACHE_TTL = 300  # seconds

# ---------------------------
# Helpers
# ---------------------------
@st.cache_data(ttl=CACHE_TTL)
def geocode(address: str):
    params = {'q': address, 'format': 'json', 'limit': 1}
    r = requests.get(NOMINATIM_URL, params=params, headers={'User-Agent': USER_AGENT}, timeout=10)
    r.raise_for_status()
    data = r.json()
    if not data:
        return None, None
    return float(data[0]['lat']), float(data[0]['lon'])

@st.cache_data(ttl=CACHE_TTL)
def check_website(name: str):
    base = re.sub(r'[^a-zA-Z0-9]+', '', name).lower()
    tlds = ['.com', '.net', '.biz', '.org', '.us']
    for tld in tlds:
        domain = base + tld
        try:
            socket.gethostbyname(domain)
            r = requests.head(f"http://{domain}", timeout=5)
            if r.status_code < 400:
                return True
        except:
            continue
    return False

@st.cache_data(ttl=CACHE_TTL)
def enrich_phone(name: str, city: str):
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
# Main Fetch Incorporations
# ---------------------------
def fetch_incorporations(days: int, per_page: int = 50):
    today = datetime.now(timezone.utc).date()
    past = today - timedelta(days=days)
    params = {
        'jurisdiction_code': 'us',
        'incorporation_date_from': past.isoformat(),
        'incorporation_date_to': today.isoformat(),
        'per_page': per_page
    }
    r = requests.get(OPENCORP_SEARCH, params=params, headers={'User-Agent': USER_AGENT}, timeout=10)
    r.raise_for_status()
    return r.json()['results']['companies']

# ---------------------------
# Processing Logic
# ---------------------------

def process_incorp(companies, blacklist):
    leads = []
    now = datetime.now(timezone.utc)
    for c in companies:
        data = c['company']
        name = data.get('name')
        addr = data.get('registered_address_in_full', '')
        if not name or any(bl in name.lower() for bl in blacklist):
            continue
        # check website
        if check_website(name):
            continue
        # geocode
        lat, lon = geocode(addr)
        if lat is None:
            continue
        # enrich phone
        city = data.get('registered_address_in_full', '').split(',')[-2].strip() if ',' in addr else ''
        phone = enrich_phone(name, city)
        if not phone:
            continue
        # days since incorporation
        date_str = data.get('incorporation_date')
        try:
            inc_date = datetime.fromisoformat(date_str).date()
            days = (now.date() - inc_date).days
        except:
            days = None
        score = max(0, 100 - (days if days is not None else 0))
        leads.append({'Name': name, 'Contact': phone, 'Address': addr,
                      'Days Since Incorp': days, 'Score': score, 'lat': lat, 'lon': lon})
    return pd.DataFrame(leads).sort_values('Score', ascending=False)

# ---------------------------
# Streamlit App
# ---------------------------
st.set_page_config(page_title='Lead Finder v3', layout='wide')
st.title('🚀 Lead Finder v3: State Filings Powered')

# Sidebar
days = st.sidebar.slider('Days Since Incorporation', 1, 30, 7)
per_page = st.sidebar.slider('Companies to Retrieve', 10, 100, 50, step=10)
black_text = st.sidebar.text_area('Blacklist substrings (one per line)', 'starbucks\nmcDonald')
blacklist = [b.strip().lower() for b in black_text.splitlines() if b.strip()]
if st.sidebar.button('Fetch New Incorporations'):
    st.experimental_rerun()

# Fetch & Process
with st.spinner('Fetching new state incorporations...'):
    companies = fetch_incorporations(days, per_page)
    df = process_incorp(companies, blacklist)

if df.empty:
    st.warning('No leads found—consider expanding days or blacklist.')
    st.stop()

st.header(f'{len(df)} new incorporations')
st.dataframe(df[['Name','Contact','Address','Days Since Incorp','Score']])
st.map(df.rename(columns={'lat':'latitude','lon':'longitude'}))

st.markdown('**Approach:** Retrieved daily company filings via OpenCorporates, geocoded their addresses, filtered out any with existing domains, enriched phone via YellowPages, and scored by newest first.')
