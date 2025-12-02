import streamlit as st
import gspread
from google.oauth2.service_account import Credentials
import pandas as pd
from datetime import datetime

st.set_page_config(page_title="Attendance Dashboard", layout="wide")

# ----------------------------- #
# Load Google Sheets Client
# ----------------------------- #
@st.cache_resource
def get_gsheet_client():
    # Load all secrets from Streamlit secrets.toml
    secrets = st.secrets["gcp_service_account"]

    creds = Credentials.from_service_account_info(
        secrets,
        scopes=["https://www.googleapis.com/auth/spreadsheets"]
    )

    client = gspread.authorize(creds)
    return client


# ----------------------------- #
# Load data from Google Sheet
# ----------------------------- #
def load_data():
    try:
        client = get_gsheet_client()

        sheet = client.open_by_url(st.secrets["gsheet_url"])

        roster_ws = sheet.worksheet("Roster")
        form_ws = sheet.worksheet("Form Responses")

        roster_df = pd.DataFrame(roster_ws.get_all_records())
        form_df = pd.DataFrame(form_ws.get_all_records())

        return roster_df, form_df

    except Exception as e:
        st.error(f"Error loading Google Sheet data.\n\n{e}")
        return None, None


# ----------------------------- #
# Page Title
# ----------------------------- #
st.title("📋 Attendance Dashboard")

# Button → Open QR scanner page
st.markdown("""
<a href="https://rajasthan11.vercel.app" target="_blank">
    <button style="padding: 10px 20px; font-size: 16px;">
        Open QR Scanner Website
    </button>
</a>
""", unsafe_allow_html=True)

st.write("---")

# Load sheets
roster_df, form_df = load_data()

if roster_df is None:
    st.stop()

# ----------------------------- #
# Process Data
# ----------------------------- #
# Convert timestamp
form_df["Timestamp"] = pd.to_datetime(form_df["Timestamp"], errors="coerce")

# Ensure ID is text
form_df["ID"] = form_df["ID"].astype(str)
roster_df["ID"] = roster_df["ID"].astype(str)

# Merge → attach student names to scan records
merged_df = form_df.merge(roster_df, on="ID", how="left")

st.subheader("Today's Attendance")

today = datetime.now().date()
today_df = merged_df[merged_df["Timestamp"].dt.date == today]

st.dataframe(today_df)

st.write("---")

st.subheader("Full Scan Log")
st.dataframe(merged_df)
