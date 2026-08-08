"""
Sorare Scout — Streamlit dashboard, v2 (visual overhaul + fixtures)

SETUP: same as before.
  pip install streamlit requests
  streamlit run sorare_dashboard.py

DEPLOY: same as before, via share.streamlit.io (GitHub repo + this file +
requirements.txt containing "streamlit" and "requests").

WHAT'S NEW IN THIS VERSION
- Full visual redesign (see chat for the design rationale) — no more plain
  dataframe, players now render as styled "match ticket" cards.
- Fixture info (next opponent, home/away) via a second, ad-hoc API call
  per unique upcoming game. This part is NEW and UNTESTED — if fixtures
  don't show up or error out, that's expected on a first run; tell Claude
  what happened and we'll debug it the same way we did everything else.
- No fixture "difficulty" rating is shown — we don't have real opponent-
  strength data, and I'm not going to invent one.
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
}
POSITIONS = ["Any", "Goalkeeper", "Defender", "Midfielder", "Forward"]
RARITIES = ["limited", "rare", "super_rare", "unique"]


# ---------------- Data layer ----------------

@st.cache_data(ttl=300)
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


@st.cache_data(ttl=300)
def _fetch_game_details(game_id):
    """
    Ad-hoc (non-persisted) query for a single game's teams, using the
    directly-queryable `anyGame` root field. UNTESTED — first real run
    will tell us if the field names guessed here are correct.
    """
    query = """
    query GetGame($id: ID!) {
      anyGame(id: $id) {
        ... on Game {
          id
          homeTeam { ... on Club { slug code } }
          awayTeam { ... on Club { slug code } }
          so5Fixture { shortDisplayName slug }
        }
      }
    }
    """
    payload = {"query": query, "variables": {"id": game_id}}
    resp = requests.post(SORARE_URL, headers=HEADERS, json=payload, timeout=15)
    if resp.status_code != 200:
        return None
    data = resp.json()
    if "errors" in data:
        return None
    return data.get("data", {}).get("anyGame")


def search(competition_slug, rarity, position, min_apps_l15, max_price_eur, min_price_eur, pages, page_size):
    filters = f"sport:football AND (active_competitions:{competition_slug})"
    if position and position != "Any":
        filters += f" AND position:{position}"

    rows = []
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

            next_game_id = None
            if p.get("nextGame"):
                next_game_id = p["nextGame"].get("id")

            row = {
                "name": p.get("displayName"),
                "club": (p.get("activeClub") or {}).get("slug", "?"),
                "position": ", ".join(p.get("cardPositions", [])),
                "l5": p.get("lastFiveSo5AverageScore"),
                "l10": p.get("lastTenPlayedSo5AverageScore"),
                "l15": p.get("lastFifteenSo5AverageScore"),
                "apps_l15": p.get("lastFifteenSo5Appearances") or 0,
                "price_eur": price_eur,
                "next_game_id": next_game_id,
                "fixture": None,
                "club_code": None,
            }
            if row["apps_l15"] < min_apps_l15:
                continue
            if max_price_eur is not None and (row["price_eur"] is None or row["price_eur"] > max_price_eur):
                continue
            if min_price_eur is not None and (row["price_eur"] is None or row["price_eur"] < min_price_eur):
                continue
            rows.append(row)

    unique_game_ids = {r["next_game_id"] for r in rows if r["next_game_id"]}
    game_cache = {}
    for gid in unique_game_ids:
        game_cache[gid] = _fetch_game_details(gid)

    for r in rows:
        gid = r["next_game_id"]
        game = game_cache.get(gid) if gid else None
        if not game:
            continue
        home = (game.get("homeTeam") or {}).get("slug")
        away = (game.get("awayTeam") or {}).get("slug")
        home_code = (game.get("homeTeam") or {}).get("code", "???")
        away_code = (game.get("awayTeam") or {}).get("code", "???")
        gw = (game.get("so5Fixture") or {}).get("shortDisplayName", "")
        if r["club"] == home:
            r["fixture"] = f"vs {away_code} (H) · {gw}"
            r["club_code"] = home_code
        elif r["club"] == away:
            r["fixture"] = f"vs {home_code} (A) · {gw}"
            r["club_code"] = away_code

    return rows


def find_value_picks(rows, min_apps=8, discount_threshold=0.6):
    """
    Flags players with a low price-per-score-point relative to peers in
    the SAME POSITION within this result set.

    IMPORTANT CAVEAT — read before trusting this signal: this does NOT
    control for club reputation or market demand. Cards from smaller,
    less-followed clubs are systematically cheaper across the board,
    regardless of how well the player actually performs — lower name
    recognition means lower demand, not necessarily a pricing mistake.
    So this detector will naturally skew toward flagging players from
    less prominent clubs. That can still be genuinely useful (a cheap,
    reliable player is a cheap, reliable player), but it's closer to
    "efficient performance-per-euro" than "underpriced relative to true
    value" — and cards from bigger clubs tend to be easier to resell
    later if you want liquidity, which this ratio doesn't account for.
    """
    priced = [r for r in rows if r["price_eur"] and r["l15"] and r["apps_l15"] >= min_apps]
    if len(priced) < 5:
        return set()
    by_position = {}
    for r in priced:
        by_position.setdefault(r["position"], []).append(r)
    flagged_names = set()
    for pos, players in by_position.items():
        ratios = [(r["price_eur"] / r["l15"]) for r in players]
        if len(ratios) < 3:
            continue
        median_ratio = statistics.median(ratios)
        for r in players:
            if (r["price_eur"] / r["l15"]) <= median_ratio * discount_threshold:
                flagged_names.add(r["name"])
    return flagged_names


# ---------------- UI ----------------

st.set_page_config(page_title="Sorare Scout", layout="wide", initial_sidebar_state="expanded")

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&family=IBM+Plex+Mono:wght@400;500&display=swap');

:root {
    --bg-base: #050914;
    --bg-glow: radial-gradient(ellipse 900px 500px at 50% -10%, rgba(59,130,246,0.35), transparent 60%);
    --surface: rgba(30, 41, 71, 0.35);
    --surface-border: rgba(148, 178, 255, 0.14);
    --surface-border-hover: rgba(96, 165, 250, 0.5);
    --text: #F3F6FF;
    --text-dim: #93A0C4;
    --blue: #4C8DFF;
    --blue-soft: #7FB0FF;
    --cyan: #4FD9E8;
    --pill-bg: linear-gradient(135deg, #3B7BFF, #4FD9E8);
}

* { font-family: 'Plus Jakarta Sans', sans-serif; }

.stApp {
    background-color: var(--bg-base);
    background-image: var(--bg-glow);
    color: var(--text);
}

section[data-testid="stSidebar"] {
    background: rgba(8, 13, 28, 0.9);
    border-right: 1px solid var(--surface-border);
}
section[data-testid="stSidebar"] * { color: var(--text) !important; }

/* Pill-ify Streamlit's native controls to match the reference language */
.stButton > button {
    background: var(--pill-bg) !important;
    color: #06101F !important;
    font-weight: 700 !important;
    border: none !important;
    border-radius: 999px !important;
    padding: 10px 20px !important;
    box-shadow: 0 4px 20px rgba(76,141,255,0.35);
}
div[data-baseweb="select"] > div, .stSlider {
    border-radius: 14px !important;
}
div[data-baseweb="select"] > div {
    background: var(--surface) !important;
    border: 1px solid var(--surface-border) !important;
}

h1, h2, h3, h4 { font-weight: 800 !important; letter-spacing: -0.01em; }

.scout-header h1 {
    font-size: 2.6rem;
    font-weight: 800;
    margin: 0;
    background: linear-gradient(90deg, #FFFFFF, var(--blue-soft));
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
}
.scout-sub {
    color: var(--text-dim);
    font-size: 0.85rem;
    margin: 6px 0 28px 0;
}
.live-dot {
    width: 7px; height: 7px; border-radius: 50%;
    background: var(--cyan);
    display: inline-block;
    box-shadow: 0 0 10px var(--cyan);
}

.card-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(270px, 1fr));
    gap: 16px;
    margin-top: 8px;
}
.player-card {
    background: var(--surface);
    backdrop-filter: blur(18px);
    -webkit-backdrop-filter: blur(18px);
    border: 1px solid var(--surface-border);
    border-radius: 20px;
    padding: 18px 20px;
    position: relative;
    transition: border-color 0.2s ease, transform 0.2s ease;
}
.player-card:hover {
    border-color: var(--surface-border-hover);
    transform: translateY(-2px);
}
.player-card.value-pick {
    border-color: rgba(79, 217, 232, 0.55);
    box-shadow: 0 0 24px rgba(79,217,232,0.12);
}

.value-banner {
    display: inline-block;
    font-size: 0.65rem;
    font-weight: 700;
    letter-spacing: 0.04em;
    color: #06101F;
    background: var(--pill-bg);
    border-radius: 999px;
    padding: 3px 10px;
    text-transform: uppercase;
    margin-bottom: 10px;
}

.card-top { display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px; }
.club-chip {
    font-family: 'IBM Plex Mono', monospace;
    font-size: 0.68rem;
    font-weight: 500;
    color: var(--blue-soft);
    background: rgba(76,141,255,0.12);
    border-radius: 999px;
    padding: 3px 10px;
}
.pos-pill {
    font-size: 0.68rem;
    color: var(--text-dim);
    background: rgba(255,255,255,0.05);
    border-radius: 999px;
    padding: 3px 10px;
}

.player-name {
    font-size: 1.25rem;
    font-weight: 700;
    color: var(--text);
    margin: 2px 0 2px 0;
    line-height: 1.15;
}
.fixture-line {
    font-family: 'IBM Plex Mono', monospace;
    font-size: 0.72rem;
    color: var(--text-dim);
    margin: 2px 0 14px 0;
}

.stat-ticker { display: flex; gap: 8px; margin: 12px 0; }
.stat-block {
    text-align: center; flex: 1;
    background: rgba(255,255,255,0.03);
    border-radius: 12px;
    padding: 8px 4px;
}
.stat-value {
    font-family: 'IBM Plex Mono', monospace;
    font-size: 1rem;
    font-weight: 500;
    color: var(--text);
}
.stat-label {
    font-size: 0.6rem;
    color: var(--text-dim);
    text-transform: uppercase;
    letter-spacing: 0.05em;
}

.card-footer {
    display: flex; justify-content: space-between; align-items: center;
    border-top: 1px solid var(--surface-border);
    padding-top: 12px; margin-top: 10px;
}
.price-tag {
    font-family: 'IBM Plex Mono', monospace;
    font-weight: 600;
    color: var(--cyan);
    font-size: 0.95rem;
}
.price-tag.no-offer { color: var(--text-dim); font-size: 0.75rem; font-weight: 400; }
.apps-badge {
    font-size: 0.7rem;
    color: var(--text-dim);
}
</style>
""", unsafe_allow_html=True)

st.markdown("""
<div class="scout-header"><h1>Sorare Scout</h1></div>
<div class="scout-sub"><span class="live-dot"></span>&nbsp; Live data from Sorare's own API · not financial advice, always confirm prices in-app before buying</div>
""", unsafe_allow_html=True)

with st.sidebar:
    st.markdown("### Filters")
    competition_name = st.selectbox("Competition", list(COMPETITIONS.keys()))
    rarity = st.selectbox("Rarity", RARITIES, index=0)
    position = st.selectbox("Position", POSITIONS)
    min_apps = st.slider("Min appearances (last 15)", 0, 15, 8)
    price_range = st.slider("Price range (EUR)", 0, 500, (0, 100))
    pages = st.slider("Pages to fetch (20/page)", 1, 15, 1,
                       help="Each page is a separate API call at the same size Sorare's own site uses. More pages = more players, but slower.")
    run_button = st.button("Search", type="primary", use_container_width=True)

if run_button:
    comp_slug = COMPETITIONS[competition_name]
    with st.spinner("Querying Sorare..."):
        rows = search(
            competition_slug=comp_slug, rarity=rarity, position=position,
            min_apps_l15=min_apps, min_price_eur=price_range[0], max_price_eur=price_range[1],
            pages=pages, page_size=20,
        )

    if not rows:
        st.warning("No players matched — try widening your filters.")
    else:
        value_names = find_value_picks(rows)

        st.markdown(f"#### {len(rows)} players found")

        cards_html = '<div class="card-grid">'
        for r in rows:
            is_value = r["name"] in value_names
            fixture_html = f'<div class="fixture-line">{r["fixture"]}</div>' if r["fixture"] else '<div class="fixture-line">Fixture unavailable</div>'
            price_html = (f'<span class="price-tag">€{r["price_eur"]:.2f}</span>'
                          if r["price_eur"] is not None else '<span class="price-tag no-offer">no offer</span>')

            def fmt(x):
                return f"{x:.0f}" if isinstance(x, (int, float)) else "–"

            club_display = r["club_code"] or r["club"][:12].upper()
            card_html = f"""
            <div class="player-card {'value-pick' if is_value else ''}">
                {'<div class="value-banner">Efficient</div>' if is_value else ''}
                <div class="card-top">
                    <span class="club-chip">{club_display}</span>
                    <span class="pos-pill">{r['position']}</span>
                </div>
                <div class="player-name">{r['name']}</div>
                {fixture_html}
                <div class="stat-ticker">
                    <div class="stat-block"><div class="stat-value">{fmt(r['l5'])}</div><div class="stat-label">L5</div></div>
                    <div class="stat-block"><div class="stat-value">{fmt(r['l10'])}</div><div class="stat-label">L10</div></div>
                    <div class="stat-block"><div class="stat-value">{fmt(r['l15'])}</div><div class="stat-label">L15</div></div>
                </div>
                <div class="card-footer">
                    {price_html}
                    <span class="apps-badge">{r['apps_l15']}/15 apps</span>
                </div>
            </div>
            """
            # Strip leading whitespace on every line — otherwise Markdown
            # treats 4+ leading spaces as a preformatted code block and
            # renders the raw HTML as text instead of parsing it.
            card_html = "\n".join(line.lstrip() for line in card_html.split("\n"))
            cards_html += card_html
        cards_html += "</div>"
        st.markdown(cards_html, unsafe_allow_html=True)

        if value_names:
            st.caption(f"⚡ {len(value_names)} players flagged 'Efficient' — cheap relative to score, WITHIN their position. "
                       f"Read this as 'high score per euro,' not 'underpriced': it doesn't account for club reputation, "
                       f"and cards from smaller/less-followed clubs are cheaper across the board regardless of performance. "
                       f"That can still be a good pick, but bigger-club cards tend to resell more easily later.")
else:
    st.info("Set your filters on the left and hit Search.")
