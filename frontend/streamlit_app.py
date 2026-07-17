"""
Streamlit frontend for the Booking CSV Upload API.

Run with:
    streamlit run streamlit_app.py

Talks to the FastAPI backend over HTTP — API URL set via API_BASE_URL in
.env (defaults to http://localhost:8000).

Error handling: ApiError/AuthExpiredError is a small exception hierarchy
raised by api_request()/download_file() (the only two functions that call
`requests` directly). @handle_api_errors is the shared handler — decorate
any function that calls one of those and it catches everything: expired
sessions bounce to login, other errors show a clean message, nothing ever
shows a raw traceback or a raw Pydantic error dict.
"""
import functools
import io
import os
from datetime import datetime

import matplotlib
matplotlib.use("Agg")  # headless backend — no display available in a server process
import matplotlib.pyplot as plt
import pandas as pd
import requests
import streamlit as st
from dotenv import load_dotenv
from matplotlib.backends.backend_pdf import PdfPages

load_dotenv()

st.set_page_config(page_title="Booking Manifest \u00b7 Upload Desk", page_icon="\U0001F6EB", layout="centered")

# Small CSS polish on top of the config.toml theme — monospace for
# ledger-style data, a stamp-like callout for the result, brass accents.
st.markdown(
    """
    <style>
      .stamp-cleared, .stamp-flagged {
        display: inline-flex; flex-direction: column; align-items: center; justify-content: center;
        width: 110px; height: 110px; border-radius: 50%; border: 3px solid;
        font-family: 'Courier New', monospace; text-transform: uppercase;
        transform: rotate(-7deg);
      }
      .stamp-cleared { border-color: #2F6D4F; color: #2F6D4F; }
      .stamp-flagged { border-color: #A83B2C; color: #A83B2C; }
      .stamp-word { font-size: 20px; font-weight: 700; letter-spacing: 0.03em; }
      .stamp-date { font-size: 9px; letter-spacing: 0.12em; margin-top: 2px; }
      .eyebrow {
        font-family: 'Courier New', monospace; font-size: 12px; letter-spacing: 0.18em;
        text-transform: uppercase; color: #C9A24B; margin-bottom: 4px;
      }
      div[data-testid="stDataFrame"] * { font-family: 'Courier New', monospace !important; }
    </style>
    """,
    unsafe_allow_html=True,
)

if "api_base" not in st.session_state:
    st.session_state.api_base = os.environ.get("API_BASE_URL", "http://localhost:8000")
if "result" not in st.session_state:
    st.session_state.result = None
if "totals" not in st.session_state:
    st.session_state.totals = None
if "token" not in st.session_state:
    st.session_state.token = None
if "username" not in st.session_state:
    st.session_state.username = None


# =============================================================================
# Exception hierarchy
# =============================================================================

class ApiError(Exception):
    """Base for anything that can go wrong talking to the API. `.message`
    is always a clean, display-ready string — never a raw dict/list."""

    def __init__(self, message: str, status_code: int = None, detail=None):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.detail = detail


class AuthExpiredError(ApiError):
    """Specifically a 401 — token missing, expired, or invalid. Handled
    separately from other ApiErrors because it needs to clear the session
    and bounce back to the login gate, not just display a message."""


# =============================================================================
# The only two functions that call `requests` directly
# =============================================================================

def auth_headers():
    return {"Authorization": f"Bearer {st.session_state.token}"} if st.session_state.token else {}


def humanize_error_detail(detail) -> str:
    """
    Turn an API "detail" field into a readable string. Handles a plain
    string, FastAPI/Pydantic's 422 shape (a list of {"loc", "msg", ...}
    dicts, each becoming "Field: message"), or anything else via str().
    """
    if detail is None:
        return "Something went wrong."
    if isinstance(detail, str):
        return detail
    if isinstance(detail, list):
        lines = []
        for err in detail:
            if isinstance(err, dict) and "msg" in err:
                loc = err.get("loc") or []
                field = loc[-1] if loc else "input"  # loc is ["body", "<field>"]
                field_label = str(field).replace("_", " ").capitalize()
                lines.append(f"{field_label}: {err['msg']}")
            else:
                lines.append(str(err))
        return "\n\n".join(lines) if lines else "Invalid input."
    return str(detail)


def api_request(method: str, path: str, *, treat_401_as_session_expiry: bool = True, **kwargs):
    """
    Single call point for every JSON API request. Returns the parsed body
    on success; raises ApiError (humanized `.message`) on failure.

    A 401 is ambiguous: on a protected endpoint it means an expired/missing
    token (AuthExpiredError — clear session, bounce to login); on the
    login endpoint itself it means wrong credentials, so
    treat_401_as_session_expiry=False there keeps it a plain ApiError.
    """
    url = f"{st.session_state.api_base}{path}"
    kwargs.setdefault("timeout", 15)
    headers = {**auth_headers(), **kwargs.pop("headers", {})}

    try:
        resp = requests.request(method, url, headers=headers, **kwargs)
    except requests.exceptions.RequestException as exc:
        raise ApiError(f"Could not reach the API at {st.session_state.api_base}: {exc}") from exc

    content_type = resp.headers.get("content-type", "")
    body = resp.json() if content_type.startswith("application/json") else {}

    if resp.status_code == 401 and treat_401_as_session_expiry:
        raise AuthExpiredError(
            "Session expired \u2014 please log in again.", status_code=401, detail=body.get("detail")
        )

    if not resp.ok:
        raw_detail = body.get("detail", f"Request failed (HTTP {resp.status_code})")
        raise ApiError(humanize_error_detail(raw_detail), status_code=resp.status_code, detail=raw_detail)

    return body


def download_file(path: str) -> bytes:
    """Same contract as api_request(), but for endpoints that return raw
    bytes (the error-log CSV) instead of JSON."""
    url = f"{st.session_state.api_base}{path}"
    try:
        resp = requests.get(url, headers=auth_headers(), timeout=15)
    except requests.exceptions.RequestException as exc:
        raise ApiError(f"Could not reach the API at {st.session_state.api_base}: {exc}") from exc

    if resp.status_code == 401:
        raise AuthExpiredError("Session expired \u2014 please log in again.", status_code=401)

    if not resp.ok:
        content_type = resp.headers.get("content-type", "")
        body = resp.json() if content_type.startswith("application/json") else {}
        raw_detail = body.get("detail", f"Request failed (HTTP {resp.status_code})")
        raise ApiError(humanize_error_detail(raw_detail), status_code=resp.status_code, detail=raw_detail)

    return resp.content


# =============================================================================
# The global exception handler
# =============================================================================

def handle_api_errors(func):
    """
    Decorate any function calling api_request()/download_file() — this is
    the one place that decides what happens on failure, so no call site
    needs its own try/except. Returns func's return value on success, or
    None on failure (after showing/handling the error).
    """
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except AuthExpiredError:
            st.session_state.token = None
            st.warning("Session expired \u2014 please log in again.")
            st.rerun()
        except ApiError as exc:
            st.error(exc.message)
        except Exception as exc:  # noqa: BLE001 — deliberate catch-all, see docstring
            st.error(f"Something unexpected went wrong: {exc}")
        return None
    return wrapper


# =============================================================================
# Actions — each one is a plain function that calls api_request()/
# download_file() and lets errors propagate; @handle_api_errors is the only
# error handling any of them need.
# =============================================================================

@handle_api_errors
def do_login(identifier: str, password: str):
    data = api_request(
        "POST", "/auth/login",
        data={"username": identifier, "password": password},
        treat_401_as_session_expiry=False,
    )
    st.session_state.token = data["access_token"]
    st.session_state.username = identifier
    st.rerun()


@handle_api_errors
def do_signup(username: str, email: str, password: str) -> bool:
    api_request("POST", "/auth/signup", json={"username": username, "email": email, "password": password})
    st.success("Account created \u2014 you can log in now.")
    return True


@handle_api_errors
def do_upload(uploaded_file):
    files = {"file": (uploaded_file.name, uploaded_file.getvalue(), "text/csv")}
    result = api_request("POST", "/upload-csv", files=files, timeout=120)
    st.session_state.result = result
    # Totals are a bonus on top of the upload result — if this secondary
    # call fails, don't let it block showing the upload result itself.
    try:
        st.session_state.totals = api_request("GET", "/analytics/summary")
    except ApiError:
        st.session_state.totals = None


@handle_api_errors
def get_error_log_bytes(url: str) -> bytes:
    return download_file(url)


@handle_api_errors
def load_analytics():
    st.session_state._analytics_cache = {
        "summary": api_request("GET", "/analytics/summary"),
        "by_country": api_request("GET", "/analytics/revenue-by-country")["revenue_by_country"],
        "by_agent": api_request("GET", "/analytics/bookings-by-agent")["bookings_by_agent"],
        "trend": api_request("GET", "/analytics/monthly-revenue-trend")["monthly_revenue_trend"],
    }


# Palette matching the rest of the app (see .streamlit/config.toml).
_PDF_INK = "#0F1D33"
_PDF_BRASS = "#C9A24B"
_PDF_PARCHMENT = "#F3EEDD"


def build_pdf_report(cache: dict) -> bytes:
    """
    Renders the current analytics into a multi-page PDF: a title/summary
    page, then one page per chart. Pure matplotlib — no dependency on
    Streamlit's own chart widgets, since those can't be exported directly.
    Returns the PDF as bytes, ready for st.download_button.
    """
    s = cache["summary"]
    buffer = io.BytesIO()

    with PdfPages(buffer) as pdf:
        # ---- Page 1: title + summary stats ----
        fig, ax = plt.subplots(figsize=(8.5, 11))
        ax.axis("off")
        fig.patch.set_facecolor(_PDF_PARCHMENT)

        ax.text(0.5, 0.93, "Booking Manifest", fontsize=26, weight="bold",
                ha="center", color=_PDF_INK, transform=ax.transAxes)
        ax.text(0.5, 0.885, "Analytics Report", fontsize=15, style="italic",
                ha="center", color=_PDF_BRASS, transform=ax.transAxes)
        ax.text(0.5, 0.85, datetime.now().strftime("Generated %Y-%m-%d %H:%M"),
                fontsize=9, ha="center", color="#666", transform=ax.transAxes)

        stats_lines = [
            f"Total bookings:        {s['total_bookings']:,}",
            f"Total revenue:         {s['total_revenue']:,.2f}",
            f"Average booking value: {s['average_booking_value']:,.2f}",
            f"Date range:            {s['earliest_booking_date']} \u2192 {s['latest_booking_date']}",
            "",
            "Status breakdown:",
        ]
        for status, count in s["status_breakdown"].items():
            stats_lines.append(f"  {status:<14} {count:,}")

        ax.text(0.5, 0.75, "\n".join(stats_lines), fontsize=12, ha="center", va="top",
                family="monospace", color=_PDF_INK, transform=ax.transAxes)
        pdf.savefig(fig)
        plt.close(fig)

        # ---- Page 2: revenue by country ----
        if cache["by_country"]:
            country_df = pd.DataFrame(cache["by_country"]).set_index("country")
            fig, ax = plt.subplots(figsize=(8.5, 6))
            ax.bar(country_df.index, country_df["revenue"], color=_PDF_BRASS)
            ax.set_title("Revenue by Country", fontsize=14, weight="bold", color=_PDF_INK)
            ax.set_ylabel("Revenue")
            plt.xticks(rotation=45, ha="right")
            fig.tight_layout()
            pdf.savefig(fig)
            plt.close(fig)

        # ---- Page 3: bookings by agent ----
        if cache["by_agent"]:
            agent_df = pd.DataFrame(cache["by_agent"]).set_index("agent")
            fig, ax = plt.subplots(figsize=(8.5, 6))
            ax.bar(agent_df.index, agent_df["bookings"], color=_PDF_INK)
            ax.set_title("Bookings by Agent", fontsize=14, weight="bold", color=_PDF_INK)
            ax.set_ylabel("Bookings")
            plt.xticks(rotation=45, ha="right")
            fig.tight_layout()
            pdf.savefig(fig)
            plt.close(fig)

        # ---- Page 4: monthly revenue trend ----
        if cache["trend"]:
            trend_df = pd.DataFrame(cache["trend"]).set_index("month")
            fig, ax = plt.subplots(figsize=(8.5, 6))
            ax.plot(trend_df.index, trend_df["revenue"], color=_PDF_BRASS, marker="o", linewidth=2)
            ax.set_title("Monthly Revenue Trend", fontsize=14, weight="bold", color=_PDF_INK)
            ax.set_ylabel("Revenue")
            plt.xticks(rotation=45, ha="right")
            ax.grid(True, alpha=0.3)
            fig.tight_layout()
            pdf.savefig(fig)
            plt.close(fig)

    return buffer.getvalue()


# =============================================================================
# UI
# =============================================================================

def show_login_gate():
    """Blocks the rest of the app until the user is signed in."""
    st.markdown('<div class="eyebrow">Booking Manifest \u00b7 Upload Desk</div>', unsafe_allow_html=True)
    st.title("Sign in to continue.")

    login_tab, signup_tab = st.tabs(["Log in", "Sign up"])

    # Streamlit forbids writing to a widget's session_state key once that
    # widget has rendered in this run (signup, below, needs to prefill the
    # login field after it's already rendered). Fix: never touch the
    # widget's own key — hold the value separately and cycle a suffix on
    # the key so `value=` applies to a fresh widget identity next run.
    if "login_prefill_nonce" not in st.session_state:
        st.session_state.login_prefill_nonce = 0

    with login_tab:
        with st.form("login_form"):
            username = st.text_input(
                "Username or email",
                value=st.session_state.pop("login_prefill_value", ""),
                key=f"login_username_{st.session_state.login_prefill_nonce}",
            )
            password = st.text_input("Password", type="password", key="login_password")
            submitted = st.form_submit_button("Log in", type="primary")
        if submitted:
            do_login(username, password)

    with signup_tab:
        with st.form("signup_form"):
            new_username = st.text_input("Choose a username", key="signup_username")
            new_email = st.text_input("Email", key="signup_email")
            new_password = st.text_input("Choose a password (min. 8 characters)", type="password", key="signup_password")
            signed_up = st.form_submit_button("Sign up", type="primary")
        if signed_up:
            if do_signup(new_username, new_email, new_password):
                st.session_state.login_prefill_value = new_username
                st.session_state.login_prefill_nonce += 1
                st.rerun()

    st.stop()


if not st.session_state.token:
    show_login_gate()

with st.sidebar:
    st.markdown(f"**Signed in as** `{st.session_state.username}`")
    if st.button("Log out"):
        st.session_state.token = None
        st.session_state.username = None
        st.session_state.result = None
        st.session_state.totals = None
        st.session_state.pop("_analytics_cache", None)
        st.rerun()

st.markdown('<div class="eyebrow">Booking Manifest \u00b7 Upload Desk</div>', unsafe_allow_html=True)
st.title("Get your booking data cleared, not just uploaded.")
st.write(
    "Drop a CSV of bookings below. Every row is checked for **mandatory fields**, "
    "**valid dates**, **duplicate booking numbers**, and **negative amounts** before "
    "anything reaches the database \u2014 rejects are logged, not lost."
)

tab_upload, tab_analytics = st.tabs(["\U0001F4E4 Upload", "\U0001F4CA Analytics"])

# ---------------------------------------------------------------- Upload tab
with tab_upload:
    uploaded_file = st.file_uploader(
        "Booking No \u00b7 Agent \u00b7 Country \u00b7 Tour Type \u00b7 Booking Date \u00b7 Amount \u00b7 Status",
        type=["csv"],
    )

    if uploaded_file is not None:
        st.caption(f"{uploaded_file.name} \u00b7 {uploaded_file.size / 1024:.1f} KB")

    if st.button("Stamp & Upload", type="primary", disabled=uploaded_file is None):
        with st.spinner("Validating and inserting rows\u2026"):
            do_upload(uploaded_file)

    result = st.session_state.result
    if result:
        st.divider()
        cleared = result["rows_rejected"] == 0
        stamp_class = "stamp-cleared" if cleared else "stamp-flagged"
        stamp_word = "Cleared" if cleared else "Flagged"

        col_stamp, col_stats = st.columns([1, 2])
        with col_stamp:
            st.markdown(
                f'<div class="{stamp_class}"><span class="stamp-word">{stamp_word}</span>'
                f'<span class="stamp-date">{pd.Timestamp.now().strftime("%m\u00b7%d\u00b7%y")}</span></div>',
                unsafe_allow_html=True,
            )
        with col_stats:
            m1, m2, m3 = st.columns(3)
            m1.metric("Processed", result["total_rows_processed"])
            m2.metric("Inserted", result["rows_inserted"])
            m3.metric("Rejected", result["rows_rejected"])

        if result["rows_rejected"] > 0 and result.get("errors_preview"):
            st.markdown("**Rejected rows (preview)**")
            errors_df = pd.DataFrame(result["errors_preview"])
            st.dataframe(errors_df, width="stretch", hide_index=True)

            if result["errors_truncated"]:
                st.caption(
                    f"Showing first {len(result['errors_preview'])} of {result['rows_rejected']} "
                    "rejected rows \u2014 download the full log below for the rest."
                )

            if result.get("error_log_download_url"):
                log_bytes = get_error_log_bytes(result["error_log_download_url"])
                if log_bytes is not None:
                    st.download_button(
                        "Download full error log (CSV)",
                        data=log_bytes,
                        file_name=result["error_log_file"] or "error_log.csv",
                        mime="text/csv",
                    )

        if st.session_state.totals:
            st.divider()
            t = st.session_state.totals
            c1, c2, c3 = st.columns(3)
            c1.metric("Total bookings", f"{t['total_bookings']:,}")
            c2.metric("Total revenue", f"{t['total_revenue']:,.0f}")
            c3.metric("Avg. booking value", f"{t['average_booking_value']:,.2f}")

# ------------------------------------------------------------- Analytics tab
with tab_analytics:
    st.caption("Pulled live from the API \u2014 click refresh any time.")
    if st.button("\U0001F504 Refresh analytics"):
        st.session_state.pop("_analytics_cache", None)

    if "_analytics_cache" not in st.session_state:
        load_analytics()

    cache = st.session_state.get("_analytics_cache")
    if cache and cache["summary"]["total_bookings"] == 0:
        st.info("No bookings in the database yet \u2014 upload a CSV first.")
    elif cache:
        s = cache["summary"]

        # Only regenerate when the data actually changed — Streamlit
        # reruns the whole script on any interaction anywhere in the app.
        if st.session_state.get("_analytics_pdf_cache_id") != id(cache):
            st.session_state._analytics_pdf_bytes = build_pdf_report(cache)
            st.session_state._analytics_pdf_cache_id = id(cache)

        st.download_button(
            "\U0001F4C4 Download PDF report",
            data=st.session_state._analytics_pdf_bytes,
            file_name=f"booking_analytics_{datetime.now().strftime('%Y%m%d')}.pdf",
            mime="application/pdf",
        )

        c1, c2, c3 = st.columns(3)
        c1.metric("Total bookings", f"{s['total_bookings']:,}")
        c2.metric("Total revenue", f"{s['total_revenue']:,.0f}")
        c3.metric("Avg. booking value", f"{s['average_booking_value']:,.2f}")
        st.caption(f"Bookings span {s['earliest_booking_date']} \u2192 {s['latest_booking_date']}")

        if s["status_breakdown"]:
            st.markdown("**Status breakdown**")
            st.bar_chart(pd.Series(s["status_breakdown"], name="bookings"))

        if cache["by_country"]:
            st.markdown("**Revenue by country**")
            country_df = pd.DataFrame(cache["by_country"]).set_index("country")
            st.bar_chart(country_df["revenue"])

        if cache["by_agent"]:
            st.markdown("**Bookings by agent**")
            agent_df = pd.DataFrame(cache["by_agent"]).set_index("agent")
            st.bar_chart(agent_df["bookings"])

        if cache["trend"]:
            st.markdown("**Monthly revenue trend**")
            trend_df = pd.DataFrame(cache["trend"]).set_index("month")
            st.line_chart(trend_df["revenue"])
    else:
        st.info("No analytics loaded yet \u2014 upload a file or click refresh.")
