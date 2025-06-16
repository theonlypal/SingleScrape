import streamlit as st
import requests
import pandas as pd
from datetime import datetime, timedelta

# —— CONFIG ——
# Center (you can swap to dynamic ZIP → geocode later)
LAT, LON, RADIUS_M = 33.4255, -111.9400, 50_000  

# Niche tags to include: (OSM key, value)
NICHE_TAGS = [
    ("shop","fitness"),
    ("leisure","fitness_centre"),
    ("shop","hairdresser"),
    ("shop","beauty"),
    ("amenity","cafe"),
    # add more!
]
# —— END CONFIG ——

st.set_page_config(page_title="🔥 Hot Website Leads", layout="wide")
st.title("🔥 Hot Leads (No Website)")

# Sidebar: lookback & niche selection
hours = st.sidebar.slider("Look back (hours)", 1, 48, 24)
selected = st.sidebar.multiselect(
    "Niches",
    options=[f"{k}={v}" for k,v in NICHE_TAGS],
    default=[f"{k}={v}" for k,v in NICHE_TAGS]
)

@st.cache_data(ttl=300)
def fetch_leads(hours, selected_tags):
    # build Overpass filters
    since = datetime.utcnow() - timedelta(hours=hours)
    iso_since = since.strftime("%Y-%m-%dT%H:%M:%SZ")
    tag_filters = []
    for kv in selected_tags:
        k,v = kv.split("=",1)
        tag_filters.append(f'  node["{k}"="{v}"](around:{RADIUS_M},{LAT},{LON});')
    query = f"""
[out:json][timeout:25];
(
{chr(10).join(tag_filters)}
)->.niche;
(
  node.niche
    (if: t["timestamp"] > "{iso_since}");
  node.niche
    (if: !t["website"]);
);
out tags center;
"""
    resp = requests.post("https://overpass-api.de/api/interpreter", data={"data":query})
    resp.raise_for_status()
    elems = resp.json().get("elements", [])
    # normalize
    rows = []
    for e in elems:
        t = e.get("tags", {})
        rows.append({
            "Name": t.get("name","‹no name›"),
            "Lat": e.get("lat"),
            "Lon": e.get("lon"),
            "Tags": ";".join(f"{k}={v}" for k,v in t.items()),
            "Listed At": t.get("timestamp","")
        })
    return pd.DataFrame(rows)

# Fetch & display
with st.spinner("Fetching leads…"):
    df = fetch_leads(hours, selected)

st.markdown(f"**{len(df)} leads found (no website)**")

# Table + map
st.dataframe(df, use_container_width=True)

if not df.empty:
    st.subheader("Map view")
    st.map(df.rename(columns={"Lat":"latitude","Lon":"longitude"}))

# Next: quick export or integrate your dialer
st.markdown("---")
st.markdown("**👉** Call them now with your pitch: “I see you just opened [Name]—I can build you a live website in 3 hrs flat for a $1,000 flat fee. Interested?”")
