"""
Sorare JPL Scouting Dashboard — Streamlit app

WHAT THIS IS
A real, hosted webpage with dropdowns/sliders/buttons for scouting Sorare
players, instead of editing Python code by hand. Same underlying logic as
the earlier Colab script, just wrapped in a proper UI.

HOW TO RUN IT LOCALLY (to test before deploying)
1. pip install streamlit requests
2. streamlit run sorare_dashboard.py
3. It opens in your browser at localhost:8501

HOW TO DEPLOY IT FOR FREE (so you get a real URL, not just local)
1. Create a free GitHub account if you don't have one: github.com
2. Create a new repository, upload this file to it (also add a
   requirements.txt file containing just: streamlit / requests)
3. Go to share.streamlit.io, sign in with GitHub, click "New app"
4. Point it at your repo and this file (sorare_dashboard.py)
5. Deploy — you'll get a real https://yourapp.streamlit.app URL you can
   open from your phone, bookmark, whatever.

IMPORTANT CAVEAT (same as before)
This uses Sorare's own Automatic Persisted Query hash, captured from a
real browser session. If Sorare updates their frontend, this hash can go
stale and you'll see a "PersistedQueryNotFound" error in the app. If that
happens: browse sorare.com again, export a fresh HAR (Network tab, filter
"graphql", right-click -> Save all as HAR), and send it back to Claude to
get an updated OPERATION_ID.
"""

import streamlit as st
import requests
import json
import statistics

SORARE_URL = "https://api.sorare.com/graphql"
HEADERS = {"Content-Type": "application/json"}
OPERATION_ID = "React/2912d064f8122563ada54fd1f56de1d8ba48ece1e85aec9fc369cf9b8e9d5459"

COMPETITIONS = {
    "Jupiler Pro League": "jupiler-pro-league",
    # Add more here once confirmed working — see caveat in chat history.
    # "Premier League": "premier-league",
}

POSITIONS = ["Any", "Goalkeeper", "Defender", "Midfielder", "Forward"]
RARITIES = ["limited", "rare", "super_rare", "unique"]


@st.cache_data(ttl=300)  # cache for 5 minutes so we don't hammer the API
def _raw_search(advanced_filters, rarity, page, page_size, sort_field, sort_dir):
    variables = {
        "skipTeamSlugs": False,
        "query": "",
        "page": page,
        "pageSize": page_size,
        "advancedFilters": advanced_filters,
        "sorts": [{"field": sort_field, "direction": sort_dir}],
        "refinements": [
            {
                "field": "player.playing_status",
                "operator": "EQUAL",
                "values": [{"stringValue": "starter"}, {"stringValue": "regular"}],
            }
        ],
        "facets": [],
        "rarity": rarity,
        "inSeason": True,
        "onlyPrimary": False,
        "onlyFollowed": False,
        "averageLimit": "LAST_10",
        "withoutSo5Fixture": False,
        "teamMode": "CLUB",
    }
    payload = {
        "operationName": "AdvancedPlayersSearchQuery",
        "variables": variables,
        "extensions": {"operationId": OPERATION_ID},
    }
    resp = requests.post(SORARE_URL, headers=HEADERS, json=payload, timeout=15)
    if resp.status_code != 200:
        return None, f"HTTP Error {resp.status_code}: {resp.text[:300]}"
    data = resp.json()
    if "errors" in data:
        return None, json.dumps(data["errors"], indent=2)
    hits = data.get("data", {}).get("searchPlayers", {}).get("commonPlayerHits", [])
    return hits, None


def search(competition_slug, rarity, position, min_apps_l15, max_price_eur, min_price_eur, pages, page_size):
    filters = f"sport:football AND (active_competitions:{competition_slug})"
    if position and position != "Any":
        filters += f" AND position:{position}"

    all_players = []
    for page in range(1, pages + 1):
        hits, err = _raw_search(filters, rarity, page, page_size,
                                 "so5.club.last_fifteen_so5_average_score", "DESC")
        if err:
            st.error(f"API error: {err}")
            break
        if not hits:
            break
        for hit in hits:
            p = hit["anyPlayer"]
            price_eur = None
            lowest_card = p.get("lowestPriceAnyCard")
            if lowest_card:
                offer = lowest_card.get("livePrimaryOffer") or lowest_card.get("liveSingleSaleOffer")
                if offer and offer.get("price"):
                    price_eur = offer["price"]["eurCents"] / 100

            row = {
                "Player": p.get("displayName"),
                "Club": (p.get("activeClub") or {}).get("slug", "?"),
                "Position": ", ".join(p.get("cardPositions", [])),
                "L5": p.get("lastFiveSo5AverageScore"),
                "L10": p.get("lastTenPlayedSo5AverageScore"),
                "L15": p.get("lastFifteenSo5AverageScore"),
                "Apps (L15)": p.get("lastFifteenSo5Appearances") or 0,
                "Price (EUR)": price_eur,
            }
            if row["Apps (L15)"] < min_apps_l15:
                continue
            if max_price_eur is not None and (row["Price (EUR)"] is None or row["Price (EUR)"] > max_price_eur):
                continue
            if min_price_eur is not None and (row["Price (EUR)"] is None or row["Price (EUR)"] < min_price_eur):
                continue
            all_players.append(row)

    return all_players


def find_value_picks(rows, min_apps=8, discount_threshold=0.6):
    priced = [r for r in rows if r["Price (EUR)"] and r["L15"] and r["Apps (L15)"] >= min_apps]
    if len(priced) < 5:
        return []

    by_position = {}
    for r in priced:
        by_position.setdefault(r["Position"], []).append(r)

    flagged = []
    for pos, players in by_position.items():
        ratios = [(r["Price (EUR)"] / r["L15"]) for r in players]
        if len(ratios) < 3:
            continue
        median_ratio = statistics.median(ratios)
        for r in players:
            r_ratio = r["Price (EUR)"] / r["L15"]
            if r_ratio <= median_ratio * discount_threshold:
                flagged.append({**r, "Price/Point": round(r_ratio, 2), "Position Median": round(median_ratio, 2)})

    flagged.sort(key=lambda r: r["Price/Point"] / r["Position Median"])
    return flagged


# ---------------- UI ----------------

st.set_page_config(page_title="Sorare Scout", layout="wide")
st.title("⚽ Sorare Scouting Dashboard")
st.caption("Real live data from Sorare's own API. Not financial advice — always double-check prices in the actual Sorare app before buying.")

with st.sidebar:
    st.header("Filters")
    competition_name = st.selectbox("Competition", list(COMPETITIONS.keys()))
    rarity = st.selectbox("Rarity", RARITIES, index=0)
    position = st.selectbox("Position", POSITIONS)
    min_apps = st.slider("Min appearances (last 15 games)", 0, 15, 8)
    price_range = st.slider("Price range (EUR)", 0, 500, (0, 100))
    pages = st.slider("Pages to fetch (20 players/page)", 1, 5, 1)
    run_button = st.button("🔍 Search", type="primary")

if run_button:
    comp_slug = COMPETITIONS[competition_name]
    with st.spinner("Querying Sorare..."):
        rows = search(
            competition_slug=comp_slug,
            rarity=rarity,
            position=position,
            min_apps_l15=min_apps,
            min_price_eur=price_range[0],
            max_price_eur=price_range[1],
            pages=pages,
            page_size=20,
        )

    if not rows:
        st.warning("No players matched — try widening your filters.")
    else:
        st.subheader(f"Results ({len(rows)} players)")
        st.dataframe(rows, use_container_width=True, hide_index=True)

        st.subheader("💡 Potential value picks")
        st.caption("Priced well below what their score would predict, compared to positional peers in THIS result set. Not historical price tracking — a relative comparison only.")
        value_picks = find_value_picks(rows)
        if value_picks:
            st.dataframe(value_picks, use_container_width=True, hide_index=True)
        else:
            st.info("No standout value picks in this result set — try widening filters for a bigger comparison pool.")
else:
    st.info("Set your filters on the left and hit Search.")
