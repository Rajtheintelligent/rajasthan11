import streamlit as st
import pandas as pd
import gspread
from google.oauth2.service_account import Credentials
from datetime import datetime

st.title("📋 Live Attendance Tracker")

# -----------------------------
# Google Sheet Connection
# -----------------------------
scope = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]

creds = Credentials.from_service_account_info(
    st.secrets["gcp_service_account"],
    scopes=scope,
)

client = gspread.authorize(creds)

# Load sheets
attendance_sheet = client.open("Attendance").worksheet("Attendance_Logs")
students_sheet = client.open("Attendance").worksheet("Students")
status_sheet = client.open("Attendance").worksheet("Status")

# -----------------------------
# Read Data
# -----------------------------
logs = pd.DataFrame(attendance_sheet.get_all_records())
students = pd.DataFrame(students_sheet.get_all_records())

status = status_sheet.get_all_records()
session_start_time = status[0]["value"]

# Convert to datetime
try:
    session_start_time = datetime.fromisoformat(session_start_time)
except:
    session_start_time = None

# -----------------------------
# Buttons
# -----------------------------
if st.button("▶️ Start New Attendance Session"):
    now = datetime.now().isoformat()
    status_sheet.update("B1", now)  # write session start_time
    st.success("New session started!")

if st.button("🔄 Reset Attendance"):
    status_sheet.update("B1", "")  # clear start_time
    st.warning("Attendance Reset!")

st.divider()

# -----------------------------
# Generate Live Attendance
# -----------------------------
if session_start_time:
    st.info(f"Session started at: {session_start_time}")

    # Filter logs after session start
    logs["timestamp"] = pd.to_datetime(logs["timestamp"])
    filtered_logs = logs[logs["timestamp"] > session_start_time]

    present_ids = filtered_logs["student_id"].unique()

    # Prepare final attendance table
    students["status"] = students["student_id"].apply(
        lambda x: "PRESENT" if x in present_ids else "ABSENT"
    )

    st.dataframe(students)
else:
    st.warning("Start a session to begin tracking live attendance.")
