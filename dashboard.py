#!/usr/bin/env python3
"""
Cyprus power market dashboard.

Reads the scraped CSVs under data/series/ directly — no database, no build
step. Run it from the repo root:

    streamlit run dashboard.py

Everything it shows comes from three sources scraped off tsoc.org.cy:
day-ahead prices and volumes (30-minute), demand and generation mix
(15-minute), and wind/solar detail (15-minute). See DATA_DICTIONARY.md for
what the underlying fields mean and tsoc_data.py for how they are normalised.
"""

from __future__ import annotations

from datetime import timedelta

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

import tsoc_data as T

st.set_page_config(
    page_title="Cyprus power market",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded",
)

# --------------------------------------------------------------------------
# access
# --------------------------------------------------------------------------

def gate() -> None:
    """
    Require a Google sign-in, but only where one has been configured.

    Streamlit Community Cloud publishes every app at a public URL — there is no
    private tier — so the login has to live inside the app rather than in front
    of it. The gate switches itself on only when an `[auth]` block exists in
    secrets, which means a local `streamlit run` stays open and unauthenticated
    and needs no credentials to develop against.

    `allowed_emails` is the part that actually makes it private: without it,
    anyone with a Google account could sign in.
    """
    try:
        configured = "auth" in st.secrets
    except Exception:
        configured = False          # no secrets file at all — local dev
    if not configured:
        return

    user = getattr(st, "user", None)
    if user is None:                # Streamlit older than 1.42
        return

    if not user.is_logged_in:
        st.title("Cyprus power market")
        st.caption("Day-ahead, system and balancing data from the Cyprus TSO.")
        st.write("This dashboard is private. Sign in to continue.")
        if st.button("Sign in with Google", type="primary"):
            st.login("google")
        st.stop()

    allowed = [e.strip().lower() for e in st.secrets.get("allowed_emails", [])]
    email = (user.email or "").lower()
    if allowed and email not in allowed:
        st.error(f"`{user.email}` is not on the access list for this dashboard.")
        if st.button("Sign out"):
            st.logout()
        st.stop()


gate()


# --------------------------------------------------------------------------
# theme & palette
# --------------------------------------------------------------------------
# Hues are assigned to *entities*, not to positions in a legend, so a filter
# that drops a series never repaints the survivors. Both columns are selected
# for their own surface rather than being an automatic flip of each other.

PALETTE = {
    "light": {
        "surface": "#fcfcfb",
        "ink": "#0b0b0b",
        "ink2": "#52514e",
        "muted": "#898781",
        "grid": "#e1e0d9",
        "axis": "#c3c2b7",
        "price": "#2a78d6",
        "conventional": "#eb6834",
        "wind": "#1baf7a",
        "solar": "#eda100",
        "netload": "#e87ba4",
        "res": "#008300",
        "volume": "#4a3aa7",
        "demand": "#0b0b0b",
        "warn": "#d03b3b",
        "bm_up": "#e34948",
        "bm_down": "#1baf7a",
        "prod_1": "#2a78d6", "prod_2": "#eb6834",
        "prod_3": "#1baf7a", "prod_4": "#eda100",
    },
    "dark": {
        "surface": "#1a1a19",
        "ink": "#ffffff",
        "ink2": "#c3c2b7",
        "muted": "#898781",
        "grid": "#2c2c2a",
        "axis": "#383835",
        "price": "#3987e5",
        "conventional": "#d95926",
        "wind": "#199e70",
        "solar": "#c98500",
        "netload": "#d55181",
        "res": "#008300",
        "volume": "#9085e9",
        "demand": "#ffffff",
        "warn": "#d03b3b",
        "bm_up": "#e66767",
        "bm_down": "#199e70",
        "prod_1": "#3987e5", "prod_2": "#d95926",
        "prod_3": "#199e70", "prod_4": "#c98500",
    },
}

# Sequential blue ramp, light -> dark. Used where the categories are ordered
# (hour of day, month) and hue would imply an identity the data doesn't have.
BLUE_RAMP = ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#2a78d6",
             "#256abf", "#1c5cab", "#184f95", "#104281", "#0d366b"]

# Diverging scale for correlations: two poles that read as opposites, with a
# neutral grey — not a third hue — at zero, so "no relationship" looks like
# nothing rather than like a category of its own.
DIVERGING = {
    "light": [(0.0, "#104281"), (0.25, "#6da7ec"), (0.5, "#f0efec"),
              (0.75, "#e88a89"), (1.0, "#a52f2e")],
    "dark": [(0.0, "#9ec5f4"), (0.25, "#3987e5"), (0.5, "#383835"),
             (0.75, "#e66767"), (1.0, "#f2a6a6")],
}


def is_dark() -> bool:
    try:
        t = getattr(st.context, "theme", None)
        if t is not None and getattr(t, "type", None):
            return t.type == "dark"
    except Exception:
        pass
    try:
        return st.get_option("theme.base") == "dark"
    except Exception:
        return False


DARK = is_dark()
C = PALETTE["dark" if DARK else "light"]
DIVERGING_SCALE = DIVERGING["dark" if DARK else "light"]


def ramp(n: int) -> list[str]:
    """n evenly spaced steps of the sequential ramp."""
    if n <= 1:
        return [BLUE_RAMP[len(BLUE_RAMP) // 2]]
    lo, hi = 1, len(BLUE_RAMP) - 1
    return [BLUE_RAMP[round(lo + (hi - lo) * i / (n - 1))] for i in range(n)]


def style(fig: go.Figure, *, height: int = 340, ylab: str = "",
          legend: bool = True, xlab: str = "") -> go.Figure:
    """Recessive chrome, unified crosshair hover, legend above the plot."""
    fig.update_layout(
        height=height,
        margin=dict(l=8, r=8, t=28 if legend else 12, b=8),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(family="system-ui, -apple-system, 'Segoe UI', sans-serif",
                  size=12, color=C["ink2"]),
        hovermode="x unified",
        hoverlabel=dict(bgcolor=C["surface"], font_size=12,
                        bordercolor=C["axis"]),
        showlegend=legend,
        legend=dict(orientation="h", yanchor="bottom", y=1.0, x=0,
                    bgcolor="rgba(0,0,0,0)", font=dict(color=C["ink2"])),
    )
    fig.update_xaxes(showgrid=False, zeroline=False, linecolor=C["axis"],
                     tickcolor=C["axis"], tickfont=dict(color=C["muted"]))
    fig.update_yaxes(gridcolor=C["grid"], zeroline=False,
                     linecolor="rgba(0,0,0,0)", tickfont=dict(color=C["muted"]))
    # Only stamp axis titles when one was asked for — subplots set their own
    # per-row titles before this runs, and a blanket update would erase them.
    axis_title = dict(font=dict(color=C["muted"], size=11))
    if xlab:
        fig.update_xaxes(title=dict(text=xlab, **axis_title))
    if ylab:
        fig.update_yaxes(title=dict(text=ylab, **axis_title))
    else:
        for ax in fig.select_yaxes():
            if ax.title and ax.title.text:
                ax.title.font = axis_title["font"]
    return fig


# Streamlit renamed `use_container_width` to `width="stretch"`. Support both so
# the app runs on whatever version happens to be installed.
try:
    import inspect as _inspect

    _WIDE = (
        {"width": "stretch"}
        if "width" in _inspect.signature(st.dataframe).parameters
        else {"use_container_width": True}
    )
except Exception:
    _WIDE = {"use_container_width": True}


def table(df, **kw):
    st.dataframe(df, **_WIDE, **kw)


def show(fig: go.Figure) -> None:
    st.plotly_chart(fig, **_WIDE,
                    config={"displaylogo": False,
                            "modeBarButtonsToRemove": ["lasso2d", "select2d"]})


# --------------------------------------------------------------------------
# data
# --------------------------------------------------------------------------

@st.cache_data(show_spinner=False)
def cached_series(key: str, _sig: tuple) -> pd.DataFrame:
    return T.load_series(key)


def get(key: str) -> pd.DataFrame:
    return cached_series(key, T.file_signature(key))


@st.cache_data(show_spinner=False)
def cached_bm(kind: str, _sig: tuple) -> pd.DataFrame:
    return T.load_bm(kind)


def get_bm(kind: str) -> pd.DataFrame:
    if kind not in T.bm_available():
        return pd.DataFrame()
    return cached_bm(kind, T.bm_signature(kind))


available = T.available_series()
if not available:
    st.error(
        "No series CSVs found under `data/series/`.\n\n"
        "Scrape some first, e.g.\n\n"
        "```\npython tsoc_scrape.py series --only dam_prices_volumes "
        "penetration_rates wind_solar_generation --start 2025-09-01\n```"
    )
    st.stop()

market = get("dam_prices_volumes") if "dam_prices_volumes" in available else pd.DataFrame()
system = get("penetration_rates") if "penetration_rates" in available else pd.DataFrame()
windsol = get("wind_solar_generation") if "wind_solar_generation" in available else pd.DataFrame()

bm_energy = get_bm("energy")
bm_system = get_bm("system")
bm_bsp = get_bm("bsp")
bm_dev = get_bm("deviation")
HAS_BM = not bm_energy.empty or not bm_system.empty

spans = [df.index for df in (market, system, windsol, bm_energy, bm_system)
         if not df.empty]
DATA_MIN = min(s.min() for s in spans).date()
DATA_MAX = max(s.max() for s in spans).date()


# --------------------------------------------------------------------------
# sidebar
# --------------------------------------------------------------------------

st.sidebar.markdown("### Cyprus power market")
st.sidebar.caption(f"TSOC data · {DATA_MIN:%d %b %Y} → {DATA_MAX:%d %b %Y}")

PRESETS = {
    "Last 7 days": 7,
    "Last 30 days": 30,
    "Last 90 days": 90,
    "Last 12 months": 365,
    "All": None,
    "Custom…": -1,
}
choice = st.sidebar.radio("Date range", list(PRESETS), index=1,
                          label_visibility="collapsed")

if PRESETS[choice] is None:
    start, end = DATA_MIN, DATA_MAX
elif PRESETS[choice] == -1:
    picked = st.sidebar.date_input(
        "Custom range", value=(max(DATA_MIN, DATA_MAX - timedelta(days=30)), DATA_MAX),
        min_value=DATA_MIN, max_value=DATA_MAX,
    )
    if isinstance(picked, tuple) and len(picked) == 2:
        start, end = picked
    else:
        start, end = DATA_MIN, DATA_MAX
else:
    end = DATA_MAX
    start = max(DATA_MIN, end - timedelta(days=PRESETS[choice] - 1))

freq_label = st.sidebar.selectbox("Resolution", list(T.FREQ_CHOICES), index=0)
FREQ = T.FREQ_CHOICES[freq_label]

st.sidebar.divider()
stale = (pd.Timestamp.today().normalize() - pd.Timestamp(DATA_MAX)).days
if stale > 2:
    st.sidebar.warning(
        f"Data ends {DATA_MAX:%d %b}, {stale} days ago.\n\n"
        "Refresh with:\n\n"
        f"`python tsoc_scrape.py series --start {DATA_MAX:%Y-%m-%d}`"
    )
if st.sidebar.button("Reload data"):
    st.cache_data.clear()
    st.rerun()

_user = getattr(st, "user", None)
if _user is not None and getattr(_user, "is_logged_in", False):
    st.sidebar.caption(f"Signed in as {_user.email}")
    if st.sidebar.button("Sign out"):
        st.logout()

st.sidebar.caption(
    "Prices and volumes are half-hourly; system data is 15-minute. "
    "Resampling averages prices and MW, and sums MWh. Shares are recomputed "
    "from MW, never averaged."
)

# range-limited frames
mkt = T.slice_range(market, start, end) if not market.empty else market
sysd = T.slice_range(system, start, end) if not system.empty else system
ws = T.slice_range(windsol, start, end) if not windsol.empty else windsol

mkt_r = T.resample(mkt, FREQ)
sys_r = T.resample(sysd, FREQ)


def fmt(v, unit="", dp=0):
    if v is None or pd.isna(v):
        return "—"
    return f"{v:,.{dp}f}{(' ' + unit) if unit else ''}"


# --------------------------------------------------------------------------
# header
# --------------------------------------------------------------------------

st.title("Cyprus power market")
st.caption(
    f"Day-ahead prices, demand and generation mix from the Cyprus TSO "
    f"(tsoc.org.cy) · showing {start:%d %b %Y} to {end:%d %b %Y}"
    f" · {freq_label.lower()} resolution"
)

(tab_overview, tab_prices, tab_system, tab_bm, tab_fund, tab_data) = st.tabs(
    ["Overview", "Prices", "System & mix", "Balancing market",
     "Price vs fundamentals", "Data & quality"]
)


# --------------------------------------------------------------------------
# Overview
# --------------------------------------------------------------------------
with tab_overview:
    k = st.columns(5)

    if not mkt.empty:
        last_price = mkt["dam_price"].iloc[-1]
        prev_day = mkt.loc[mkt.index < mkt.index.max().normalize(), "dam_price"]
        vw = T.vwap(mkt["dam_price"], mkt["cleared_volume"])
        k[0].metric("Latest price", fmt(last_price, "€/MWh", 1),
                    help=f"Settlement period beginning {mkt.index.max():%d %b %H:%M}")
        k[1].metric("Volume-weighted price", fmt(vw, "€/MWh", 1),
                    help="Over the selected range. The honest average for a "
                         "price series — a simple mean over-weights low-volume "
                         "night periods.")
        k[2].metric("Cleared volume", fmt(mkt["cleared_volume"].sum() / 1000, "GWh", 1))
    if not sysd.empty:
        k[3].metric("Peak demand", fmt(sysd["demand"].max(), "MW"),
                    help=f"Reached {sysd['demand'].idxmax():%d %b %H:%M}")
        k[4].metric("Max RES share", fmt(sysd["res_pct"].max(), "%", 1),
                    help=f"Reached {sysd['res_pct'].idxmax():%d %b %H:%M}")

    st.divider()

    left, right = st.columns([3, 2])

    with left:
        st.markdown("**Day-ahead clearing price**")
        if mkt_r.empty:
            st.info("No price data in this range.")
        else:
            g = T.with_gaps(mkt_r)
            fig = go.Figure()
            fig.add_trace(go.Scatter(
                x=g.index, y=g["dam_price"], name="Clearing price",
                mode="lines", line=dict(color=C["price"], width=2),
                hovertemplate="%{y:.1f} €/MWh<extra></extra>",
            ))
            show(style(fig, ylab="EUR/MWh", legend=False, height=300))

        st.markdown("**Generation mix**")
        if sys_r.empty:
            st.info("No system data in this range.")
        else:
            # Stack order puts aqua between orange and yellow: those are the
            # validated adjacent pairs, and orange-beside-yellow is not.
            g = T.with_gaps(sys_r)
            fig = go.Figure()
            for col, label in (("conventional", "Conventional"),
                               ("wind", "Wind"),
                               ("distributed_pv", "Distributed PV & biomass")):
                if col not in sys_r:
                    continue
                fig.add_trace(go.Scatter(
                    x=g.index, y=g[col], name=label, mode="lines",
                    stackgroup="mix", line=dict(width=0.5, color=C["surface"]),
                    fillcolor=C[{"conventional": "conventional",
                                 "wind": "wind",
                                 "distributed_pv": "solar"}[col]],
                    hovertemplate="%{y:,.0f} MW<extra></extra>",
                ))
            fig.add_trace(go.Scatter(
                x=g.index, y=g["demand"], name="Total demand",
                mode="lines", line=dict(color=C["demand"], width=1.5, dash="dot"),
                hovertemplate="%{y:,.0f} MW<extra></extra>",
            ))
            show(style(fig, ylab="MW", height=320))

    with right:
        st.markdown("**Daily RES share of demand**")
        if sysd.empty:
            st.info("No system data in this range.")
        else:
            daily = T.resample(sysd, "D")
            fig = go.Figure()
            fig.add_trace(go.Scatter(
                x=daily.index, y=daily["res_pct"], name="Daily average",
                mode="lines", line=dict(color=C["res"], width=2),
                hovertemplate="%{y:.1f}%<extra></extra>",
            ))
            peak = sysd["res_pct"].resample("D").max()
            fig.add_trace(go.Scatter(
                x=peak.index, y=peak, name="Daily peak", mode="lines",
                line=dict(color=C["res"], width=1, dash="dot"),
                hovertemplate="%{y:.1f}%<extra></extra>",
            ))
            show(style(fig, ylab="% of demand", height=300))

        st.markdown("**Energy by source over the range**")
        if not sysd.empty:
            energy = (sysd[["conventional", "wind", "distributed_pv"]].mean()
                      * len(sysd) * 0.25 / 1000)
            share = energy / energy.sum() * 100
            tbl = pd.DataFrame({
                "Energy (GWh)": energy.round(1),
                "Share (%)": share.round(1),
            })
            tbl.index = ["Conventional", "Wind", "Distributed PV & biomass"]
            table(tbl)
            st.caption(
                "Energy integrated from 15-minute MW readings. Distributed PV "
                "is TSOC's estimate, not a metered value."
            )


# --------------------------------------------------------------------------
# Prices
# --------------------------------------------------------------------------
with tab_prices:
    if mkt.empty:
        st.info("No price data in this range.")
    else:
        st.markdown("**Price and cleared volume**")
        st.caption(
            "Two panels rather than two y-axes — a shared axis would make the "
            "crossing points of the two lines look meaningful when they are an "
            "artefact of the scaling."
        )
        fig = make_subplots(rows=2, cols=1, shared_xaxes=True,
                            vertical_spacing=0.06, row_heights=[0.62, 0.38])
        g = T.with_gaps(mkt_r)
        fig.add_trace(go.Scatter(
            x=g.index, y=g["dam_price"], name="Clearing price",
            mode="lines", line=dict(color=C["price"], width=2),
            hovertemplate="%{y:.1f} €/MWh<extra></extra>"), row=1, col=1)
        fig.add_trace(go.Scatter(
            x=g.index, y=g["cleared_volume"], name="Cleared volume",
            mode="lines", line=dict(color=C["volume"], width=1.5),
            hovertemplate="%{y:,.1f} MWh<extra></extra>"), row=2, col=1)
        fig.update_yaxes(title_text="EUR/MWh", row=1, col=1)
        fig.update_yaxes(title_text="MWh", row=2, col=1)
        show(style(fig, height=460))

        c1, c2 = st.columns(2)

        with c1:
            st.markdown("**Price duration curve**")
            st.caption("Every settlement period in the range, sorted high to low.")
            s = mkt["dam_price"].dropna().sort_values(ascending=False)
            pct = pd.Series(range(1, len(s) + 1), dtype=float) / len(s) * 100
            fig = go.Figure()
            fig.add_trace(go.Scatter(
                x=pct, y=s.values, mode="lines",
                line=dict(color=C["price"], width=2), name="Price",
                hovertemplate="%{y:.1f} €/MWh at %{x:.0f}% of periods<extra></extra>"))
            for q, lbl in ((10, "P10"), (50, "median"), (90, "P90")):
                v = s.quantile(1 - q / 100)
                fig.add_hline(y=v, line=dict(color=C["muted"], width=1, dash="dot"),
                              annotation_text=f"{lbl} {v:,.0f}",
                              annotation_font=dict(color=C["muted"], size=11),
                              annotation_position="top left")
            show(style(fig, ylab="EUR/MWh", xlab="% of periods at or above",
                       legend=False, height=320))

        with c2:
            st.markdown("**Average intraday shape**")
            st.caption("Mean price by half-hour, one line per month in range.")
            d = mkt.copy()
            d["tod"] = d.index.hour + d.index.minute / 60
            d["month"] = d.index.to_period("M").astype(str)
            months = sorted(d["month"].unique())
            colours = ramp(len(months))
            fig = go.Figure()
            for m, col in zip(months, colours):
                g = d[d["month"] == m].groupby("tod")["dam_price"].mean()
                fig.add_trace(go.Scatter(
                    x=g.index, y=g.values, name=m, mode="lines",
                    line=dict(color=col, width=2),
                    hovertemplate=f"{m}: %{{y:.1f}} €/MWh<extra></extra>"))
            fig.update_xaxes(tickvals=list(range(0, 25, 3)),
                             ticktext=[f"{h:02d}:00" for h in range(0, 25, 3)])
            show(style(fig, ylab="EUR/MWh", xlab="Hour of day", height=320))

        st.markdown("**Monthly statistics**")
        d = mkt.copy()
        d["month"] = d.index.to_period("M").astype(str)
        rows = []
        for m, g in d.groupby("month"):
            rows.append({
                "Month": m,
                "Mean (€/MWh)": g["dam_price"].mean(),
                "VWAP (€/MWh)": T.vwap(g["dam_price"], g["cleared_volume"]),
                "Min": g["dam_price"].min(),
                "P10": g["dam_price"].quantile(0.10),
                "Median": g["dam_price"].median(),
                "P90": g["dam_price"].quantile(0.90),
                "Max": g["dam_price"].max(),
                "Volume (GWh)": g["cleared_volume"].sum() / 1000,
                "Periods": len(g),
            })
        table(pd.DataFrame(rows).set_index("Month").round(1))
        st.caption(
            "VWAP below the mean means the expensive periods were also the thin "
            "ones. A partial first or last month is not comparable to a full one."
        )


# --------------------------------------------------------------------------
# System & mix
# --------------------------------------------------------------------------
with tab_system:
    if sysd.empty:
        st.info("No system data in this range.")
    else:
        c1, c2 = st.columns(2)

        with c1:
            st.markdown("**Average day: demand and net load**")
            st.caption(
                "Net load is demand minus wind and estimated distributed PV — "
                "the shape conventional plant actually has to follow. The gap "
                "between the two lines is the solar belly."
            )
            d = sysd.copy()
            d["tod"] = d.index.hour + d.index.minute / 60
            prof = d.groupby("tod")[["demand", "net_load"]].mean()
            fig = go.Figure()
            fig.add_trace(go.Scatter(
                x=prof.index, y=prof["demand"], name="Total demand",
                mode="lines", line=dict(color=C["demand"], width=2),
                hovertemplate="%{y:,.0f} MW<extra></extra>"))
            fig.add_trace(go.Scatter(
                x=prof.index, y=prof["net_load"], name="Net load",
                mode="lines", line=dict(color=C["netload"], width=2),
                fill="tonexty", fillcolor="rgba(232,123,164,0.12)",
                hovertemplate="%{y:,.0f} MW<extra></extra>"))
            fig.update_xaxes(tickvals=list(range(0, 25, 3)),
                             ticktext=[f"{h:02d}:00" for h in range(0, 25, 3)])
            show(style(fig, ylab="MW", xlab="Hour of day", height=340))

        with c2:
            st.markdown("**RES share of demand**")
            st.caption("Distribution of the 15-minute RES penetration figure.")
            fig = go.Figure()
            fig.add_trace(go.Histogram(
                x=sysd["res_pct"].dropna(), nbinsx=40,
                marker=dict(color=C["res"], line=dict(width=1, color=C["surface"])),
                name="Intervals",
                hovertemplate="%{y:,} intervals at %{x}%<extra></extra>"))
            show(style(fig, ylab="15-minute intervals", xlab="RES share of demand (%)",
                       legend=False, height=340))

        st.markdown("**Monthly energy mix**")
        d = sysd.copy()
        d["month"] = d.index.to_period("M").astype(str)
        mix = d.groupby("month")[["conventional", "wind", "distributed_pv"]].sum() * 0.25 / 1000
        fig = go.Figure()
        for col, label, key in (("conventional", "Conventional", "conventional"),
                                ("wind", "Wind", "wind"),
                                ("distributed_pv", "Distributed PV & biomass", "solar")):
            fig.add_trace(go.Bar(
                x=mix.index, y=mix[col], name=label,
                marker=dict(color=C[key], line=dict(width=2, color=C["surface"])),
                hovertemplate="%{y:,.1f} GWh<extra></extra>"))
        fig.update_layout(barmode="stack",
                          bargap=0.7 if len(mix) < 4 else 0.35)
        show(style(fig, ylab="GWh", height=330))
        st.caption(
            "15-minute MW readings integrated to energy. Months at the edges of "
            "the selected range may be partial."
        )

        if not ws.empty:
            st.markdown("**Wind and solar, cross-checked**")
            st.caption(
                "`wind_solar_generation` publishes transmission-connected wind "
                "and estimated distributed solar separately. Its wind figure "
                "should track the mix table's — a divergence means one of the "
                "two pages changed shape."
            )
            a = T.resample(ws, FREQ or "h")
            b = T.resample(sysd[["wind"]], FREQ or "h")
            fig = go.Figure()
            fig.add_trace(go.Scatter(
                x=a.index, y=a["wind_tx"], name="Wind (wind_solar page)",
                mode="lines", line=dict(color=C["wind"], width=2),
                hovertemplate="%{y:,.0f} MW<extra></extra>"))
            fig.add_trace(go.Scatter(
                x=b.index, y=b["wind"], name="Wind (penetration page)",
                mode="lines", line=dict(color=C["netload"], width=1.5, dash="dot"),
                hovertemplate="%{y:,.0f} MW<extra></extra>"))
            fig.add_trace(go.Scatter(
                x=a.index, y=a["distributed_solar"], name="Distributed solar (est.)",
                mode="lines", line=dict(color=C["solar"], width=2),
                hovertemplate="%{y:,.0f} MW<extra></extra>"))
            show(style(fig, ylab="MW", height=320))


# --------------------------------------------------------------------------
# Balancing market
# --------------------------------------------------------------------------
with tab_bm:
    if not HAS_BM:
        st.info(
            "No balancing-market data yet. Download and parse it with:\n\n"
            "```\n"
            "python tsoc_scrape.py files --only bm_daily_activity_en isp_balancing_bdl\n"
            "python tsoc_bm.py parse\n"
            "```\n\n"
            "`REP_TSO-001` is a transposed report — time across the columns — so "
            "it needs `tsoc_bm.py` rather than the generic `tsoc_parse.py`."
        )
    else:
        bm_e = T.slice_range(bm_energy, start, end) if not bm_energy.empty else bm_energy
        bm_s = T.slice_range(bm_system, start, end) if not bm_system.empty else bm_system
        bm_b = T.slice_range(bm_bsp, start, end) if not bm_bsp.empty else bm_bsp
        bm_d = T.slice_range(bm_dev, start, end) if not bm_dev.empty else bm_dev
        joined_bm = T.bm_vs_dam(bm_e, mkt)

        k = st.columns(5)
        if not bm_e.empty:
            k[0].metric("Balancing price, up", fmt(bm_e["price_up"].mean(), "€/MWh", 1),
                        help="Mean over priced intervals in range. Up = the system "
                             "was short and had to buy energy.")
            k[1].metric("Balancing price, down", fmt(bm_e["price_down"].mean(), "€/MWh", 1),
                        help="Down = the system was long and paid to reduce output.")
            # MW read at 5-minute resolution -> MWh over the interval.
            up_mwh = bm_e["activated_up"].sum() * 5 / 60
            k[2].metric("Energy activated, up", fmt(up_mwh / 1000, "GWh", 2),
                        help="Activated balancing energy, integrated from the "
                             "5-minute figures. The unit is inferred as MW — TSOC "
                             "publishes no unit for this row.")
            share = T.sentinel_share(bm_e)
            worst = max(share.values()) if share else 0
            k[3].metric("Intervals with no price", fmt(worst * 100, "%", 1),
                        help="Published as 999999 or 25000 — markers, not prices. "
                             "Excluded from every average on this tab.")
        if not joined_bm.empty and "spread_up" in joined_bm:
            k[4].metric("Up spread vs day-ahead",
                        fmt(joined_bm["spread_up"].mean(), "€/MWh", 1),
                        help="Mean of (balancing up − day-ahead) over the "
                             "settlement periods where both exist.")

        st.divider()

        st.markdown("**Balancing price against day-ahead**")
        st.caption(
            "The balancing price is averaged up to the 30-minute settlement "
            "period — the day-ahead price is one number per period by "
            "definition, so that is the finest resolution at which the two are "
            "comparable. Down is dashed as well as differently coloured, so the "
            "two balancing series stay separable without relying on hue."
        )
        if joined_bm.empty:
            st.info(
                "No overlap between the balancing and day-ahead series in this "
                "range. Note the two archives end on different days — balancing "
                "reports run ahead of the scraped day-ahead series."
            )
        else:
            jr = T.with_gaps(T.resample(joined_bm, FREQ))
            fig = go.Figure()
            fig.add_trace(go.Scatter(
                x=jr.index, y=jr["dam_price"], name="Day-ahead", mode="lines",
                line=dict(color=C["price"], width=2),
                hovertemplate="%{y:.1f} €/MWh<extra></extra>"))
            fig.add_trace(go.Scatter(
                x=jr.index, y=jr["price_up"], name="Balancing, up", mode="lines",
                line=dict(color=C["bm_up"], width=2),
                hovertemplate="%{y:.1f} €/MWh<extra></extra>"))
            fig.add_trace(go.Scatter(
                x=jr.index, y=jr["price_down"], name="Balancing, down", mode="lines",
                line=dict(color=C["bm_down"], width=2, dash="dash"),
                hovertemplate="%{y:.1f} €/MWh<extra></extra>"))
            show(style(fig, ylab="EUR/MWh", height=340))

            c1, c2 = st.columns(2)
            with c1:
                st.markdown("**How far balancing sits from day-ahead**")
                st.caption(
                    "Positive means being short cost more than the day-ahead "
                    "price; negative means the balancing market was the cheaper "
                    "place to be."
                )
                fig = go.Figure()
                for col, label, colour, dash in (
                    ("spread_up", "Up − day-ahead", C["bm_up"], None),
                    ("spread_down", "Down − day-ahead", C["bm_down"], "dash"),
                ):
                    if col not in joined_bm:
                        continue
                    s = joined_bm[col].dropna()
                    if s.empty:
                        continue
                    fig.add_trace(go.Histogram(
                        x=s, name=label, nbinsx=50, opacity=0.6,
                        marker=dict(color=colour,
                                    line=dict(width=1, color=C["surface"])),
                        hovertemplate=f"{label}: %{{y:,}} periods near "
                                      "%{x:.0f} €/MWh<extra></extra>"))
                fig.add_vline(x=0, line=dict(color=C["muted"], width=1, dash="dot"))
                fig.update_layout(barmode="overlay")
                show(style(fig, ylab="Settlement periods",
                           xlab="Spread (EUR/MWh)", height=320))

            with c2:
                st.markdown("**Spread by hour of day**")
                st.caption("Mean spread in each half-hour of the day.")
                d = joined_bm.copy()
                d["tod"] = d.index.hour + d.index.minute / 60
                prof = d.groupby("tod")[[c for c in ("spread_up", "spread_down")
                                         if c in d]].mean()
                fig = go.Figure()
                for col, label, colour, dash in (
                    ("spread_up", "Up − day-ahead", C["bm_up"], None),
                    ("spread_down", "Down − day-ahead", C["bm_down"], "dash"),
                ):
                    if col in prof:
                        fig.add_trace(go.Scatter(
                            x=prof.index, y=prof[col], name=label, mode="lines",
                            line=dict(color=colour, width=2, dash=dash),
                            hovertemplate="%{y:+.1f} €/MWh<extra></extra>"))
                fig.add_hline(y=0, line=dict(color=C["muted"], width=1, dash="dot"))
                fig.update_xaxes(tickvals=list(range(0, 25, 3)),
                                 ticktext=[f"{h:02d}:00" for h in range(0, 25, 3)])
                show(style(fig, ylab="EUR/MWh", xlab="Hour of day", height=320))

        if not bm_e.empty:
            st.markdown("**Activated balancing energy**")
            st.caption(
                "How much the system operator actually had to move, at "
                "5-minute resolution. Up and down are both published as "
                "positive numbers."
            )
            er = T.with_gaps(T.resample(bm_e, FREQ or "30min"))
            fig = go.Figure()
            for col, label, colour, dash in (
                ("activated_up", "Activated up", C["bm_up"], None),
                ("activated_down", "Activated down", C["bm_down"], "dash"),
            ):
                if col in er:
                    fig.add_trace(go.Scatter(
                        x=er.index, y=er[col], name=label, mode="lines",
                        line=dict(color=colour, width=2, dash=dash),
                        hovertemplate="%{y:,.1f} MW<extra></extra>"))
            show(style(fig, ylab="MW (inferred)", height=300))

        if not bm_s.empty:
            st.markdown("**Reserve marginal prices by product**")
            st.caption(
                "Four separate products, four separate panels — one shared "
                "y-axis would let the biggest product flatten the rest. These "
                "are **capacity** prices in EUR/MW: what the system pays to "
                "have a resource standing by. The energy actually activated is "
                "paid separately, at the 5-minute balancing energy price above. "
                "The acronyms are TSOC's own English labelling in this report, "
                "unlike the inferred Greek mapping in DATA_DICTIONARY §5a."
            )
            with st.expander("What each product is"):
                st.markdown(
                    "Cyprus procures the standard European balancing stack, "
                    "defined in the EU Electricity Balancing Guideline "
                    "(Regulation 2017/2195). Each tier hands over to the next "
                    "as the response gets slower and more deliberate:\n\n"
                    "| Product | Also called | Responds in | How | What it does |\n"
                    "|---|---|---|---|---|\n"
                    "| **FCR** | Primary control | ~30 seconds | Automatic, "
                    "local | Arrests a frequency deviation. Units react to the "
                    "measured frequency themselves — no instruction is sent. "
                    "Stops the fall; does not correct it. |\n"
                    "| **aFRR** | Secondary control | ~5 minutes | Automatic, "
                    "TSO signal | Pulls frequency back to 50 Hz and releases "
                    "the FCR so it is armed again. Continuously modulated by a "
                    "central controller. |\n"
                    "| **mFRR** | Tertiary control | ~12.5 minutes | Manual | "
                    "Dispatcher-instructed. Handles the larger or longer "
                    "imbalance and frees up the aFRR band. |\n"
                    "| **RR** | Replacement reserve | ~30 minutes+ | Manual | "
                    "The slowest tier — restores the others for the next event "
                    "when an imbalance persists. Not every European TSO "
                    "procures it. |\n\n"
                    "**Up** means the system was short and needs more energy "
                    "or less demand; **down** means it was long. They are "
                    "separate products with separate prices — a resource can "
                    "be awarded one and not the other.\n\n"
                    "Two prices exist for every service, and conflating them "
                    "is the classic mistake: **capacity** (EUR/MW, paid for "
                    "availability whether or not it is used — the panels "
                    "below) and **energy** (EUR/MWh, paid only for what is "
                    "actually delivered — the chart further up). In Cyprus "
                    "the capacity prices clear per half-hourly settlement "
                    "period, while balancing energy clears every 5 minutes."
                )
            products = [("fcr", "FCR"), ("afrr", "aFRR"),
                        ("mfrr", "mFRR"), ("rr", "RR")]
            have = [(p, lbl) for p, lbl in products
                    if f"{p}_price_up" in bm_s.columns]
            if have:
                sr = T.with_gaps(T.resample(bm_s, FREQ))
                fig = make_subplots(
                    rows=2, cols=2, shared_xaxes=True,
                    subplot_titles=[lbl for _, lbl in have],
                    vertical_spacing=0.12, horizontal_spacing=0.07)
                for n, (p, lbl) in enumerate(have):
                    r, c = n // 2 + 1, n % 2 + 1
                    for side, colour, dash in (("up", C["bm_up"], None),
                                               ("down", C["bm_down"], "dash")):
                        col = f"{p}_price_{side}"
                        if col not in sr:
                            continue
                        fig.add_trace(go.Scatter(
                            x=sr.index, y=sr[col], name=side.title(),
                            mode="lines", legendgroup=side,
                            showlegend=(n == 0),
                            line=dict(color=colour, width=2, dash=dash),
                            hovertemplate="%{y:.2f} €/MW<extra></extra>"),
                            row=r, col=c)
                fig.update_annotations(font=dict(size=12, color=C["ink2"]))
                show(style(fig, ylab="", height=440)
                     .update_yaxes(title=dict(text="EUR/MW",
                                              font=dict(color=C["muted"], size=11)),
                                   col=1))

        if not bm_b.empty:
            st.markdown("**Who provides the reserve**")
            st.caption(
                "Mean awarded capacity per unit over the selected range, split "
                "by product. Units are TSOC's resource-object codes — DHEK, "
                "MONI and VASS are the three conventional stations."
            )
            agg = (bm_b.groupby(["unit", "product"], observed=False)["mw"]
                   .mean().unstack(fill_value=0))
            order = agg.sum(axis=1).sort_values(ascending=False)
            agg = agg.loc[order[order > 0].index]
            if not agg.empty:
                agg = agg.head(18).iloc[::-1]
                # Stack order follows the validated adjacent sequence.
                prod_order = [p for p in ("FCR", "aFRR", "mFRR", "RR")
                              if p in agg.columns]
                fig = go.Figure()
                for n, p in enumerate(prod_order):
                    fig.add_trace(go.Bar(
                        y=agg.index, x=agg[p], name=p, orientation="h",
                        marker=dict(color=C[f"prod_{n + 1}"],
                                    line=dict(width=2, color=C["surface"])),
                        hovertemplate=f"{p}: %{{x:,.2f}} MW<extra></extra>"))
                fig.update_layout(barmode="stack", bargap=0.3)
                show(style(fig, xlab="Mean awarded capacity (MW)",
                           height=max(320, 26 * len(agg) + 90)))

        if not bm_d.empty:
            st.markdown("**System balance deviation**")
            st.caption(
                "From `REP_TSO-009-BDL`. Positive means the system was long "
                "against schedule, negative short. Published per settlement "
                "period with no timestamp — the time axis here is reconstructed "
                "as trade date + (period − 1) × 30 minutes."
            )
            c1, c2 = st.columns(2)
            with c1:
                fig = go.Figure()
                fig.add_trace(go.Histogram(
                    x=bm_d["value"].dropna(), nbinsx=50,
                    marker=dict(color=C["price"],
                                line=dict(width=1, color=C["surface"])),
                    name="Settlement periods",
                    hovertemplate="%{y:,} periods near %{x:,.0f} MW<extra></extra>"))
                fig.add_vline(x=0, line=dict(color=C["muted"], width=1, dash="dot"))
                show(style(fig, ylab="Settlement periods",
                           xlab="System balance deviation (MW)",
                           legend=False, height=310))
            with c2:
                dr = bm_d[["value"]].rename(columns={"value": "deviation"})
                pair = dr.join(
                    bm_e[["price_up", "price_down"]].resample("30min").mean()
                    .dropna(how="all"), how="inner").dropna(how="all")
                if pair.empty or len(pair) < 5:
                    st.info("Not enough overlap to compare deviation with price "
                            "in this range.")
                else:
                    fig = go.Figure()
                    for col, label, colour in (("price_up", "Up", C["bm_up"]),
                                               ("price_down", "Down", C["bm_down"])):
                        if col not in pair:
                            continue
                        p = pair.dropna(subset=[col])
                        fig.add_trace(go.Scattergl(
                            x=p["deviation"], y=p[col], mode="markers",
                            name=f"Balancing {label.lower()}",
                            marker=dict(size=5, color=colour, opacity=0.55,
                                        line=dict(width=1, color=C["surface"])),
                            hovertemplate="%{y:.1f} €/MWh at %{x:,.0f} MW"
                                          "<extra></extra>"))
                    fig.add_vline(x=0, line=dict(color=C["muted"], width=1,
                                                 dash="dot"))
                    show(style(fig, ylab="EUR/MWh",
                               xlab="System balance deviation (MW)", height=310))
                    st.caption(
                        "A short system (left of zero) paying more for up "
                        "regulation is the expected shape."
                    )


# --------------------------------------------------------------------------
# Price vs fundamentals
# --------------------------------------------------------------------------
with tab_fund:
    joined = T.join_market_system(mkt, sysd)
    if joined.empty:
        st.info("Need both price and system data in the range for this view.")
    else:
        st.caption(
            "System data is averaged up to the 30-minute settlement period "
            "rather than the price being interpolated down — the clearing "
            "price is one number per period, and inventing values inside it "
            "would be fiction."
        )

        c1, c2 = st.columns(2)
        with c1:
            st.markdown("**Price against net load**")
            j = joined.dropna(subset=["dam_price", "net_load"])
            hours = j.index.hour
            fig = go.Figure()
            fig.add_trace(go.Scattergl(
                x=j["net_load"], y=j["dam_price"], mode="markers",
                marker=dict(size=4, color=hours, colorscale=[[i / 9, c] for i, c
                                                             in enumerate(BLUE_RAMP)],
                            colorbar=dict(title=dict(text="Hour", font=dict(size=11)),
                                          tickfont=dict(size=10), thickness=10),
                            opacity=0.55),
                name="Settlement periods",
                customdata=hours,
                hovertemplate="%{y:.1f} €/MWh at %{x:,.0f} MW net load "
                              "(hour %{customdata})<extra></extra>"))
            show(style(fig, ylab="EUR/MWh", xlab="Net load (MW)",
                       legend=False, height=380))
            corr = j["dam_price"].corr(j["net_load"])
            st.caption(f"Correlation: **{corr:+.2f}**. Hour of day is on a "
                       "sequential ramp — it is an ordered quantity, not an identity.")

        with c2:
            st.markdown("**Price against RES share**")
            j2 = joined.dropna(subset=["dam_price", "res_pct"])
            fig = go.Figure()
            fig.add_trace(go.Scattergl(
                x=j2["res_pct"], y=j2["dam_price"], mode="markers",
                marker=dict(size=4, color=C["res"], opacity=0.4),
                name="Settlement periods",
                hovertemplate="%{y:.1f} €/MWh at %{x:.1f}% RES<extra></extra>"))
            binned = j2.groupby(pd.cut(j2["res_pct"], bins=20),
                                observed=False)["dam_price"].mean()
            centres = [iv.mid for iv in binned.index]
            fig.add_trace(go.Scatter(
                x=centres, y=binned.values, mode="lines+markers",
                line=dict(color=C["price"], width=2),
                marker=dict(size=8, color=C["price"],
                            line=dict(width=2, color=C["surface"])),
                name="Mean price in bin",
                hovertemplate="%{y:.1f} €/MWh<extra></extra>"))
            show(style(fig, ylab="EUR/MWh", xlab="RES share of demand (%)",
                       height=380))
            corr2 = j2["dam_price"].corr(j2["res_pct"])
            st.caption(f"Correlation: **{corr2:+.2f}**. A negative figure is the "
                       "merit-order effect: renewables displace the marginal unit.")

        st.markdown("**Correlations**")
        # `conventional` is omitted deliberately: TSOC derives it as demand
        # minus RES, so it is identical to net load to the last decimal and
        # would show up here as a spurious 1.00.
        cols = [c for c in ["dam_price", "cleared_volume", "demand", "net_load",
                            "wind", "distributed_pv", "res_pct"]
                if c in joined.columns]
        cm = joined[cols].corr()
        labels = [T.LABELS.get(c, c) for c in cols]
        z = cm.values

        fig = go.Figure(go.Heatmap(
            z=z, x=labels, y=labels, zmin=-1, zmax=1,
            colorscale=DIVERGING_SCALE, xgap=2, ygap=2,
            colorbar=dict(thickness=10, tickfont=dict(size=10),
                          tickvals=[-1, -0.5, 0, 0.5, 1],
                          title=dict(text="r", font=dict(size=11))),
            hovertemplate="%{y} ↔ %{x}<br>r = %{z:+.2f}<extra></extra>",
        ))
        # Every cell is labelled, so the value never depends on reading a hue.
        for i, rlab in enumerate(labels):
            for j, clab in enumerate(labels):
                v = z[i, j]
                fig.add_annotation(
                    x=clab, y=rlab, text=f"{v:+.2f}", showarrow=False,
                    font=dict(size=11, family="system-ui, sans-serif",
                              color=C["surface"] if abs(v) > 0.55 else C["ink"]),
                )
        fig.update_yaxes(autorange="reversed", showgrid=False)
        fig.update_xaxes(showgrid=False, tickangle=-30)
        show(style(fig, legend=False, height=120 + 46 * len(labels))
             .update_layout(hovermode="closest",
                            margin=dict(l=8, r=8, t=8, b=8)))
        st.caption(
            "Pearson correlation over the selected range, at 30-minute "
            "resolution. These are contemporaneous associations, not causes — "
            "demand, solar output and price all move with the time of day. "
            "Conventional generation is left out: TSOC derives it as demand "
            "minus RES, so it is the same series as net load."
        )


# --------------------------------------------------------------------------
# Data & quality
# --------------------------------------------------------------------------
with tab_data:
    st.markdown("**Archive coverage**")
    rows = []
    for key in available:
        spec = T.SERIES_SPECS[key]
        df = get(key)
        cov = T.coverage(df, spec.freq_minutes)
        rows.append({
            "Series": spec.title,
            "Key": key,
            "Resolution": f"{spec.freq_minutes} min",
            "From": cov["first"],
            "To": cov["last"],
            "Rows": cov["rows"],
            "Complete (%)": round(cov["pct"], 2),
        })
    st.dataframe(pd.DataFrame(rows).set_index("Series"))

    if HAS_BM:
        st.markdown("**Balancing market archive**")
        rows = []
        for kind in T.bm_available():
            df = get_bm(kind)
            if df.empty:
                continue
            rows.append({
                "Dataset": T.BM_FILES[kind],
                "Rows": len(df),
                "Columns": df.shape[1],
                "From": df.index.min() if df.index.name == "timestamp" else None,
                "To": df.index.max() if df.index.name == "timestamp" else None,
            })
        if rows:
            table(pd.DataFrame(rows).set_index("Dataset"))
        if not bm_energy.empty:
            share = T.sentinel_share(bm_energy)
            st.caption(
                "Balancing prices published as `999999` or `25000` are markers, "
                "not prices — "
                + ", ".join(f"**{v * 100:.1f}%** of intervals on the {k} side"
                            for k, v in share.items())
                + ". They are excluded from `price_up` / `price_down` and kept "
                "verbatim in the `_raw` columns, so nothing is lost and no "
                "average is distorted. Unlike the scraped HTML series, these "
                "files publish all 50 periods on the October DST day — the "
                "`period` and `interval` columns disambiguate the repeated "
                "wall-clock timestamps."
            )

    st.markdown("**Days that are not the length they should be**")
    st.caption(
        "Cyprus clocks change on the last Sunday of March and October. A "
        "correct half-hourly archive holds 46 periods on the March day and 50 "
        "on the October one. The October day is the dangerous one: the "
        "repeated wall-clock hour de-duplicates against itself, so an hour of "
        "history disappears without any gap appearing in the index."
    )
    any_anom = False
    for key in available:
        spec = T.SERIES_SPECS[key]
        anom = T.day_anomalies(get(key), spec.freq_minutes)
        if anom.empty:
            continue
        any_anom = True
        st.markdown(f"`{key}`")
        table(anom.set_index("day"))
    if not any_anom:
        st.success("Every day holds the number of intervals it should.")
    else:
        st.caption(
            "A negative `missing` on the March day means the page published "
            "more intervals than the clock has — the 15-minute system pages "
            "appear to render a fixed 96-slot day rather than true local time. "
            "Worth knowing before you join them to anything clock-accurate."
        )

    st.markdown("**Largest gaps**")
    for key in available:
        spec = T.SERIES_SPECS[key]
        g = T.gaps(get(key), spec.freq_minutes, top=8)
        if g.empty:
            continue
        st.markdown(f"`{key}`")
        table(g, hide_index=True)

    st.divider()
    st.markdown("**Browse and export**")
    pick = st.selectbox("Series", available,
                        format_func=lambda k: T.SERIES_SPECS[k].title)
    spec = T.SERIES_SPECS[pick]
    st.caption(spec.note)

    df = T.resample(T.slice_range(get(pick), start, end), FREQ)
    cols = st.multiselect("Columns", list(df.columns), default=list(df.columns),
                          format_func=lambda c: f"{T.LABELS.get(c, c)}"
                                                f"{' (' + T.UNITS[c] + ')' if c in T.UNITS else ''}")
    view = df[cols] if cols else df
    st.dataframe(view.tail(500))
    st.caption(f"{len(view):,} rows in range; showing the last 500.")
    st.download_button(
        "Download this view as CSV",
        view.to_csv().encode("utf-8-sig"),
        file_name=f"{pick}_{start:%Y%m%d}_{end:%Y%m%d}_{freq_label.lower()}.csv",
        mime="text/csv",
    )

    st.divider()
    st.caption(
        "Source: Cyprus Transmission System Operator (tsoc.org.cy), scraped by "
        "`tsoc_scrape.py`. Timestamps are naive Cyprus local time (EET/EEST). "
        "Distributed PV is TSOC's estimate — rooftop solar is not individually "
        "metered. Full field definitions in DATA_DICTIONARY.md."
    )
