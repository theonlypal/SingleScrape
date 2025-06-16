# lead_app.py

import streamlit as st
import requests
import pandas as pd
from datetime import datetime, timedelta

# —— CONFIG ——
# Geolocation center & radius (meters)
LAT, LON, RADIUS_M = 33.4255, -111.9400, 50_000

# Default niches (OSM key=value)
NICHE_TAGS = [
    ("shop","fitness"),
    ("leisure","fitness_centre"),
    ("shop","hairdresser"),
    ("shop","beauty"),
    ("amenity","cafe"),
    # add more as needed
]

# Default blacklist chains
DEFAULT_BLACKLIST = [
    "Starbucks","McDonald's","Planet Fitness","Walmart",
    "Target","CVS","7-Eleven","Walgreens"
]

# Specificity weights for scoring
SPECIFICITY_WEIGHTS = {
    "leisure=fitness_centre": 1.0,
    "amenity=cafe": 0.8,
    "shop=fitness": 0.6,
    "shop=hairdresser": 0.6,
    "shop=beauty": 0.6,
}
# —— END CONFIG ——


st.set_page_config(page_title="🔥 Hot Leads Dashboard", layout="wide")
st.title("🔥 Real-Time Website-Less Leads")

# Sidebar controls
st.sidebar.header("Parameters")

days = st.sidebar.slider("Look back (days)", 0, 60, 7)
selected = st.sidebar.multiselect(
    "Niches to include",
    options=[f"{k}={v}" for k, v in NICHE_TAGS],
    default=[f"{k}={v}" for k, v in NICHE_TAGS],
)
blacklist_input = st.sidebar.text_area(
    "Blacklist chains (one per line)", 
    value="\n".join(DEFAULT_BLACKLIST),
    help="Any business whose name contains one of these will be excluded."
)
if st.sidebar.button("🔄 Refresh"):
    st.experimental_rerun()

# parse blacklist
BLACKLIST = [line.strip() for line in blacklist_input.splitlines() if line.strip()]

@st.cache_data(ttl=300)
def fetch_leads(days, selected_tags):
    since = datetime.utcnow() - timedelta(days=days)
    iso_since = since.strftime("%Y-%m-%dT%H:%M:%SZ")

    # build Overpass filters
    tag_filters = []
    for kv in selected_tags:
        k, v = kv.split("=", 1)
        tag_filters.append(f'  node["{k}"="{v}"](around:{RADIUS_M},{LAT},{LON});')

    query = f"""
[out:json][timeout:30];
(
{chr(10).join(tag_filters)}
)->.niche;
(
  node.niche
    (if: t["timestamp"] > "{iso_since}")
    (if: !t["website"])
    (if: t["phone"]);
);
out tags center;
"""
    resp = requests.post("https://overpass-api.de/api/interpreter", data={"data": query})
    resp.raise_for_status()
    elems = resp.json().get("elements", [])

    rows = []
    now = datetime.utcnow()
    for e in elems:
        tags = e.get("tags", {})
        name = tags.get("name", "‹no name›")
        # blacklist filter
        if any(bl.lower() in name.lower() for bl in BLACKLIST):
            continue

        phone = tags.get("phone", "‹no phone›")
        # assemble address
        addr_parts = []
        for part in ("addr:housenumber","addr:street","addr:city","addr:state","addr:postcode"):
            if tags.get(part):
                addr_parts.append(tags.get(part))
        address = ", ".join(addr_parts) if addr_parts else "‹no address›"

        # days since listed
        ts_str = tags.get("timestamp", "")
        try:
            ts = datetime.fromisoformat(ts_str.replace("Z","+00:00"))
            days_listed = (now - ts).days
        except:
            days_listed = None

        # specificity score
        tag_strs = [f"{k}={v}" for k,v in tags.items() if f"{k}={v}" in SPECIFICITY_WEIGHTS]
        spec_score = max((SPECIFICITY_WEIGHTS.get(t,0) for t in tag_strs), default=0.5)

        # days score (fresher → higher)
        max_days = max(days, 1)
        days_score = max(0, (max_days - (days_listed or max_days)) / max_days)

        # address completeness score
        addr_score = min(len(addr_parts) / 5, 1)

        # composite score
        score = (days_score * 0.5 + spec_score * 0.3 + addr_score * 0.2) * 100

        rows.append({
            "Name": name,
            "Phone": phone,
            "Address": address,
            "Days Since Listed": days_listed,
            "Score": round(score, 1),
            "Lat": e.get("lat"),
            "Lon": e.get("lon"),
        })

    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.sort_values("Score", ascending=False).reset_index(drop=True)
    return df

# fetch & display
with st.spinner("Fetching leads…"):
    df = fetch_leads(days, selected)

st.markdown(f"**Found {len(df)} call-ready leads**")

if df.empty:
    st.info("No leads found for these parameters.")
else:
    # main table
    st.subheader("Leads (sorted by Score)")
    st.dataframe(
        df[["Name","Phone","Address","Days Since Listed","Score"]],
        use_container_width=True,
    )

    # map view
    st.subheader("📍 Map View")
    st.map(df.rename(columns={"Lat":"latitude","Lon":"longitude"})[["latitude","longitude"]])

    # optional: click-to-call hint
    st.markdown(
        "**Next:** Click the phone number in your CRM or dial directly:\n\n"
        "> 📞 `tel:` links supported on mobile browsers"
    )
