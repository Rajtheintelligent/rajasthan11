import streamlit as st
import pandas as pd
import gspread
import time 
from pyzbar.pyzbar import decode
from PIL import Image
import numpy as np
from datetime import datetime

# --- Configuration ---
# The names of the tabs in your Google Sheet (Spreadsheet ID is in secrets.toml)
ROSTER_SHEET_NAME = "Roster"
ATTENDANCE_LOG_SHEET_NAME = "FormResponses" 
# The column names used in the sheets
ROSTER_ID_COL = "Students"
LOG_ID_COL = "ID"
TIMESTAMP_COL = "Timestamp"
STATUS_COL = "Attendance Status" # New column for the final dashboard

# Set Streamlit page configuration
st.set_page_config(
    page_title="Real-Time Attendance Dashboard",
    layout="wide",
    initial_sidebar_state="collapsed"
)

# --- Data Loading Function with Caching ---
# We use caching to prevent reading the entire Google Sheet on every single interaction,
# but we set max_age to 10 seconds (10) so the data is refreshed frequently.
@st.cache_data(ttl=10)
def load_and_process_data():
    """Loads the Roster and Attendance Log and calculates the current status."""
    try:
        # 1. Load the Master Roster
        # The Streamlit st.connection('gcp_sheets') uses the credentials from secrets.toml
        # FIX 2: Change type to the imported class
        #conn = st.connection("gcp_sheets", type=GSheetsConnection)
        
        # Load Roster (main student list)
        df_roster = conn.read(worksheet=ROSTER_SHEET_NAME, usecols=[ROSTER_ID_COL, 'Students'], ttl=5).dropna(subset=[ROSTER_ID_COL])
        df_roster[ROSTER_ID_COL] = df_roster[ROSTER_ID_COL].astype(str)
        df_roster = df_roster.set_index(ROSTER_ID_COL)
        
        # 2. Load the Raw Attendance Log
        # This contains all the timestamps and IDs captured by the QR scanner
        df_log = conn.read(worksheet=ATTENDANCE_LOG_SHEET_NAME, usecols=[TIMESTAMP_COL, LOG_ID_COL], ttl=5).dropna(subset=[LOG_ID_COL])
        df_log[LOG_ID_COL] = df_log[LOG_ID_COL].astype(str)
        
        # 3. Calculate Attendance Status from the Log
        
        # Get the set of unique IDs that have scanned (Present students)
        present_ids = set(df_log[LOG_ID_COL].unique())
        
        # 4. Merge DataFrames and Determine Status
        
        # Create a status column in the roster based on the unique scanned IDs
        def check_status(row_id):
            return "Present" if row_id in present_ids else "Absent"

        df_roster[STATUS_COL] = df_roster.index.to_series().apply(check_status)
        
        # 5. Get Last Scan Time (Optional, for dashboard info)
        try:
            # Convert timestamp column to datetime objects
            df_log[TIMESTAMP_COL] = pd.to_datetime(df_log[TIMESTAMP_COL], errors='coerce')
            last_update = df_log[TIMESTAMP_COL].max()
            if pd.isna(last_update):
                 last_update = "N/A (No scans recorded yet)"
            else:
                 last_update = last_update.strftime("%Y-%m-%d %I:%M:%S %p")
        except Exception:
            last_update = "Error reading timestamp"
            
        return df_roster, last_update, present_ids

    except Exception as e:
        st.error(f"Error loading or processing data. Please check your Google Sheet ID, tab names, and column names (`{ROSTER_ID_COL}` / `{LOG_ID_COL}`).")
        st.exception(e)
        return pd.DataFrame(), "Failed to load", set()

# --- Main Dashboard Layout ---

st.title("🚌 Live Trip Attendance Tracker")

# Button to manually refresh the cache
if st.button("Manual Refresh Data", help="Click to force a refresh from Google Sheets."):
    st.cache_data.clear()
    st.success("Data cache cleared and refreshing now...")

# Load and process data
df_attendance, last_update, present_ids = load_and_process_data()

if not df_attendance.empty:
    
    total_students = len(df_attendance)
    present_count = len(present_ids)
    absent_count = total_students - present_count

    # --- Metrics Section ---
    col1, col2, col3, col4 = st.columns(4)

    col1.metric("Total Students", total_students)
    col2.metric("✅ Present", present_count)
    col3.metric("❌ Absent", absent_count)
    col4.metric("Last Scan Time", last_update)
    
    st.markdown("---")
    
    # --- Filter and Display ---
    
    # Use tabs for clean filtering between Present and Absent
    tab_present, tab_absent = st.tabs([
        f"✅ Present ({present_count})", 
        f"❌ Absent ({absent_count})"
    ])

    # Function to apply styling to the DataFrame
    def style_dataframe(df):
        return df.style.applymap(
            lambda x: 'background-color: #d1fae5' if x == 'Present' else 'background-color: #fee2e2',
            subset=[STATUS_COL]
        )

    with tab_present:
        st.subheader("Currently Checked-In Students")
        df_present = df_attendance[df_attendance[STATUS_COL] == "Present"]
        
        # Display the DataFrame, reset index so Student ID shows as a column
        st.dataframe(
            df_present.reset_index(), 
            use_container_width=True,
            column_order=[ROSTER_ID_COL, 'Student Name', STATUS_COL]
        )

    with tab_absent:
        st.subheader("Students Not Yet Checked In")
        df_absent = df_attendance[df_attendance[STATUS_COL] == "Absent"]
        
        # Display the DataFrame
        st.dataframe(
            df_absent.reset_index(), 
            use_container_width=True,
            column_order=[ROSTER_ID_COL, 'Student Name', STATUS_COL]
        )
