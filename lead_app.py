import streamlit as st
import requests
import pandas as pd
import re
from bs4 import BeautifulSoup
from urllib.parse import quote_plus

st.set_page_config(layout="wide")
st.title("🚀 Lead Finder: Yelp Newest with No Website")

# --- Sidebar ---
st.sidebar.header("Search Settings")
zip_code = st.sidebar.text_input("ZIP Code (5-digit)", "")
if not re.fullmatch(r"\d{5}", zip_code):
    st.sidebar.error("Enter a valid 5-digit ZIP code.")
    st.stop()

pages = st.sidebar.slider("Pages of results (20 per page)", 1, 5, 3)
if st.sidebar.button("Fetch Leads"):
    st.session_state.fetch = True
else:
    st.session_state.fetch = st.session_state.get("fetch", False)

# --- Helper: scrape one Yelp page ---
@st.cache_data(ttl=300)
def scrape_yelp(zip_code: str, page: int):
    """Scrape Yelp 'Newest' for one page (20 items)."""
    offset = (page - 1) * 20
    url = (
        f"https://www.yelp.com/search?find_loc={quote_plus(zip_code)}"
        f"&sortby=date_desc&start={offset}"
    )
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; LeadFinder/1.0)"
    }
    r = requests.get(url, headers=headers, timeout=10)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    cards = soup.select("div.container__09f24__21w3G")  # each business card
    results = []
    for c in cards:
        # name
        name_tag = c.select_one("a.css-166la90")
        if not name_tag:
            continue
        name = name_tag.text.strip()
        # Yelp profile link
        link = name_tag["href"]
        # address
        addr = c.select_one("span.css-e81eai")
        address = addr.text.strip() if addr else ""
        # phone
        phone = ""
        ph = c.find("p", string=re.compile(r"\(\d{3}\)\s*\d{3}-\d{4}"))
        if ph:
            phone = ph.text.strip()
        # website link presence
        # some cards include a "Business website" link icon
        ws = bool(c.select_one("a[href*='biz_redir?url=']"))
        results.append({
            "Name": name,
            "Yelp Link": "https://yelp.com" + link,
            "Address": address,
            "Phone": phone,
            "Has Website": ws
        })
    return results

# --- Main fetch & process ---
if st.session_state.fetch:
    all_leads = []
    with st.spinner("Scraping Yelp..."):
        for pg in range(1, pages + 1):
            try:
                page_data = scrape_yelp(zip_code, pg)
                all_leads.extend(page_data)
            except Exception as e:
                st.warning(f"Page {pg} failed: {e}")
    # filter for Has Website == False
    df = pd.DataFrame(all_leads)
    if df.empty:
        st.error("No results returned from Yelp. Try expanding pages.")
        st.stop()
    df = df[df["Has Website"] == False].copy()
    if df.empty:
        st.warning("All recent businesses have websites listed on Yelp.")
        st.stop()

    df["Call Link"] = df["Phone"].apply(lambda p: f"tel:{p}" if p else "")
    df = df[["Name", "Address", "Phone", "Call Link", "Yelp Link"]]

    st.header(f"{len(df)} Fresh Leads without Websites")
    st.dataframe(df)

    st.markdown("### Next Steps")
    st.markdown(
        "- Review each lead's Yelp profile to confirm no website.\n"
        "- Cold-call via the `Call Link` column.\n"
        "- When they ask “Who is this?”, say “Your neighbors on Yelp said you’re new here—mind if I build you a free site?” 😉"
    )
