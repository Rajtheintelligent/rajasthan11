import streamlit as st
import pandas as pd
from datetime import datetime
# Import the specialized Streamlit GSheets Connection class
from streamlit_gsheets_connection import GSheetsConnection 

# --- Configuration ---
# The names of the tabs in your Google Sheet (Spreadsheet ID is configured in secrets.toml)
ROSTER_SHEET_NAME = "Roster"
ATTENDANCE_LOG_SHEET_NAME = "Form Responses 1" 

# The column names used in the sheets (Ensure these match your sheet exactly!)
ROSTER_ID_COL = "Student ID" # Assumed column in Roster tab
LOG_ID_COL = "ID"            # Assumed column in Form Responses 1 tab (from QR scanner)
TIMESTAMP_COL = "Timestamp"
STATUS_COL = "Attendance Status"

# Set Streamlit page configuration
st.set_page_config(
    page_title="Real-Time Attendance Dashboard",
    layout="wide",
    initial_sidebar_state="collapsed"
)

# --- Data Loading Function with Caching ---
# Caching ensures data is fetched securely and efficiently, and the TTL=10 guarantees
# a refresh every 10 seconds for real-time tracking across all users.
@st.cache_data(ttl=10)
def load_and_process_data():
    """Loads the Roster and Attendance Log and calculates the current status."""
    try:
        # Use the established Streamlit Connection method
        # This securely uses the credentials in your .streamlit/secrets.toml
        conn = st.connection("gcp_sheets", type=GSheetsConnection)
        
        # 1. Load the Master Roster (Student List)
        df_roster = conn.read(worksheet=ROSTER_SHEET_NAME, ttl=5).dropna(subset=[ROSTER_ID_COL])
        df_roster[ROSTER_ID_COL] = df_roster[ROSTER_ID_COL].astype(str)
        df_roster = df_roster.set_index(ROSTER_ID_COL)
        
        # 2. Load the Raw Attendance Log (QR Scanner Submissions)
        # This log contains all scans, including duplicates, since the beginning of time.
        df_log = conn.read(worksheet=ATTENDANCE_LOG_SHEET_NAME, ttl=5).dropna(subset=[LOG_ID_COL])
        df_log[LOG_ID_COL] = df_log[LOG_ID_COL].astype(str)
        
        # 3. Calculate Attendance Status: Find all unique IDs that have scanned
        present_ids = set(df_log[LOG_ID_COL].unique())
        
        # 4. Merge DataFrames and Determine Status
        def check_status(row_id):
            return "PRESENT" if row_id in present_ids else "ABSENT"

        df_roster[STATUS_COL] = df_roster.index.to_series().apply(check_status)
        
        # 5. Get Last Scan Time (for dashboard info)
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
        # NOTE: This error now specifically points to issues with GSheets authentication or columns/tabs.
        st.error(f"Error loading or processing data. Please check your Google Sheet tabs (`{ROSTER_SHEET_NAME}` / `{ATTENDANCE_LOG_SHEET_NAME}`) and column names (`{ROSTER_ID_COL}` / `{LOG_ID_COL}`).")
        st.exception(e)
        return pd.DataFrame(), "Failed to load", set()

# --- Main Dashboard Layout ---

st.title("🚌 Live Trip Attendance Tracker")

st.markdown("""
<style>
    .st-emotion-cache-1r65ghv { padding-top: 1rem; } /* Reduce space below title */
</style>
""", unsafe_allow_html=True)


# Load and process data
df_attendance, last_update, present_ids = load_and_process_data()

# Button to manually refresh the cache (helpful for immediate feedback after a scan)
if st.button("🔄 Manual Refresh Data (Updates every 10s automatically)", help="Click to force an immediate update from Google Sheets."):
    st.cache_data.clear()
    st.success("Data cache cleared and refreshing now...")

st.markdown("---")

if not df_attendance.empty:
    
    total_students = len(df_attendance)
    present_count = len(present_ids)
    absent_count = total_students - present_count

    # --- Metrics Section ---
    col1, col2, col3, col4 = st.columns(4)

    col1.metric("Total Students", total_students)
    col2.metric("✅ PRESENT", present_count)
    col3.metric("❌ ABSENT", absent_count)
    col4.metric("Last Scan Time", last_update)
    
    st.markdown("---")
    
    # --- Filter and Display ---
    
    # Use tabs for clean filtering between Present and Absent
    tab_present, tab_absent = st.tabs([
        f"✅ PRESENT ({present_count})", 
        f"❌ ABSENT ({absent_count})"
    ])

    # Function to apply styling to the DataFrame
    def style_dataframe(df):
        return df.style.applymap(
            lambda x: 'background-color: #d1fae5; color: #065f46; font-weight: bold;' if x == 'PRESENT' else 'background-color: #fee2e2; color: #991b1b;',
            subset=[STATUS_COL]
        )

    with tab_present:
        st.subheader("Currently Checked-In Students (Live Data)")
        df_present = df_attendance[df_attendance[STATUS_COL] == "PRESENT"]
        
        # Display the DataFrame
        st.dataframe(
            style_dataframe(df_present.reset_index()), 
            use_container_width=True,
            column_order=[ROSTER_ID_COL, 'Student Name', STATUS_COL],
            hide_index=True
        )

    with tab_absent:
        st.subheader("Students Not Yet Checked In (Live Data)")
        df_absent = df_attendance[df_attendance[STATUS_COL] == "ABSENT"]
        
        # Display the DataFrame
        st.dataframe(
            style_dataframe(df_absent.reset_index()), 
            use_container_width=True,
            column_order=[ROSTER_ID_COL, 'Student Name', STATUS_COL],
            hide_index=True
        )
else:
    # Display a warning if the roster could not be loaded
    st.warning("Roster data could not be loaded. Please ensure the 'Roster' tab exists and connection is successful.")
