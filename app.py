import streamlit as st
import pandas as pd
import gspread
import time
from streamlit_webrtc import webrtc_streamer, VideoTransformerBase
from pyzbar.pyzbar import decode
from PIL import Image
import numpy as np

# --- Configuration and Page Setup ---
st.set_page_config(
    page_title="Trip Attendance Tracker",
    page_icon="🎒",
    layout="wide",
    initial_sidebar_state="collapsed"
)

# --- CSS for Mobile-Friendly, Tappable Cards and Scanner Feedback ---
st.markdown("""
<style>
/* Ensure high visibility and tappability for buttons on mobile */
div.stButton > button {
    width: 100%;
    padding: 1rem;
    font-size: 1.1rem;
    font-weight: 700;
    margin: 5px 0;
    border-radius: 12px;
    box-shadow: 0 4px 6px rgba(0, 0, 0, 0.1);
    transition: all 0.2s ease;
}

/* Status Styling: Green for Present, Red for Absent/Remaining */
.present {
    background-color: #10B981 !important; /* Tailwind emerald-500 */
    color: white !important;
    border: 2px solid #059669 !important;
}

.absent {
    background-color: #F87171 !important; /* Tailwind red-400 */
    color: white !important;
    border: 2px solid #EF4444 !important;
}
</style>
""", unsafe_allow_html=True)


# --- Google Sheets Connection and Data Loading ---

@st.cache_resource(ttl=3600)
def get_gspread_client():
    """Authenticates and returns the spreadsheet object using Streamlit Secrets."""
    try:
        if "gcp_service_account" not in st.secrets or "SHEET_URL" not in st.secrets:
            st.error("🚨 Configuration Error: Please ensure `gcp_service_account` and `SHEET_URL` are correctly set in `.streamlit/secrets.toml`.")
            return None

        creds_dict = st.secrets["gcp_service_account"]
        gc = gspread.service_account_from_dict(creds_dict)
        sheet_url = st.secrets["SHEET_URL"]
        spreadsheet = gc.open_by_url(sheet_url)
        return spreadsheet
    except Exception as e:
        st.error(f"Error connecting to Google Sheets. Verify permissions and secrets: {e}")
        return None

# Caching with short TTL (1 second) to enable multi-user real-time refresh
@st.cache_data(ttl=1) 
def load_master_students(_spreadsheet): # FIXED: Added leading underscore to bypass hashing
    """Loads the master student list from the 'Students' sheet."""
    try:
        wks = _spreadsheet.worksheet("Students")
        df = pd.DataFrame(wks.get_all_records())
        if 'ID' not in df.columns or 'Name' not in df.columns:
            st.error("Sheet 'Students' is improperly configured. It must contain 'ID' and 'Name' columns.")
            return pd.DataFrame()
            
        df = df[['ID', 'Name']].astype(str).drop_duplicates(subset=['ID'])
        # Store a dictionary for quick name lookup
        st.session_state.id_to_name = dict(zip(df['ID'], df['Name']))
        return df
    except Exception as e:
        st.error(f"Error loading the 'Students' worksheet. Ensure the tab name is correct: {e}")
        return pd.DataFrame()

@st.cache_data(ttl=1)
def get_current_attendance_status(_spreadsheet, current_checkpoint, master_df): # FIXED: Added leading underscore to bypass hashing
    """
    Reads the AttendanceLog and determines the LATEST status for all students 
    for the active checkpoint. Refreshes every 1 second (ttl=1) for real-time concurrency.
    """
    if not current_checkpoint or master_df.empty:
        return pd.DataFrame({'ID': master_df['ID'].tolist(), 'Status': ['Absent'] * len(master_df)})

    try:
        wks = _spreadsheet.worksheet("AttendanceLog")
        df_log = pd.DataFrame(wks.get_all_records()) 
        
        # Initialize status for all students as Absent
        status_df = master_df[['ID', 'Name']].copy()
        status_df['Status'] = 'Absent' 

        if df_log.empty or 'Checkpoint' not in df_log.columns:
            return status_df

        # Filter the log for the current checkpoint
        checkpoint_log = df_log[df_log['Checkpoint'] == current_checkpoint]
        
        if checkpoint_log.empty:
            return status_df
        
        # Find the latest (most recent Timestamp) entry for each unique Student ID
        latest_status = checkpoint_log.sort_values(by='Timestamp', ascending=False).drop_duplicates(subset=['ID'])
        
        # Update status based on the latest entries
        for _, row in latest_status.iterrows():
            if row['Status'] in ['Present', 'Absent']:
                 status_df.loc[status_df['ID'] == row['ID'], 'Status'] = row['Status']

        return status_df
        
    except Exception as e:
        st.error(f"Error retrieving current attendance status from log. Ensure 'AttendanceLog' tab exists and has the correct headers: {e}")
        return pd.DataFrame({'ID': master_df['ID'].tolist(), 'Status': ['Absent'] * len(master_df)})


def save_attendance_entry(spreadsheet, student_id, status):
    """Appends a single, real-time attendance entry to the 'AttendanceLog' sheet."""
    try:
        wks = spreadsheet.worksheet("AttendanceLog")
        timestamp = pd.Timestamp.now().strftime('%Y-%m-%d %H:%M:%S')
        name = st.session_state.id_to_name.get(student_id, "Unknown Student")
        checkpoint = st.session_state.current_checkpoint
        
        # Row data must match the five column headers in AttendanceLog
        new_row = [timestamp, checkpoint, student_id, name, status]
        wks.append_row(new_row, value_input_option='USER_ENTERED')
        
        # Clear the cache for the status checker to force an immediate refresh on all devices
        get_current_attendance_status.clear()
        
        return True, name
    except Exception as e:
        st.error(f"Error saving real-time entry to Google Sheets: {e}")
        return False, "Unknown Student"


# --- QR Code Video Transformer (Handles Continuous Scanning) ---

class BarcodeDetector(VideoTransformerBase):
    """A VideoTransformer that detects barcodes/QR codes in the video stream."""
    def transform(self, frame):
        # Import cv2 dynamically for thread safety
        try:
            import cv2 
        except ImportError:
            # Fallback if cv2 is not available (though it's a requirement)
            return frame.to_ndarray(format="bgr24")

        img = frame.to_ndarray(format="bgr24")
        pil_img = Image.fromarray(img)
        decoded_objects = decode(pil_img)

        if decoded_objects:
            barcode = decoded_objects[0]
            student_id = barcode.data.decode('utf-8').strip()

            # Pass the scanned ID back to the Streamlit main thread using session state buffer
            st.session_state.scanned_id_buffer = student_id
            
            # Draw visual feedback (Green box around the detected code)
            points = barcode.polygon
            if points:
                rect = barcode.rect
                cv2.rectangle(img, (rect.left, rect.top), (rect.left + rect.width, rect.top + rect.height), (0, 255, 0), 2)
                cv2.putText(img, student_id, (rect.left, rect.top - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 2)
        
        return img


# --- Main Application Logic ---

def main():
    st.title("🎒 Quick Attendance Tracker")
    st.caption("Single-Page, Multi-User Real-Time Check-In")

    # 1. Initialize State and Check Connection
    if 'is_initialized' not in st.session_state:
        st.session_state.is_initialized = False
        st.session_state.scanned_id_buffer = None
        st.session_state.current_checkpoint = ""
        st.session_state.id_to_name = {}

    spreadsheet = get_gspread_client()
    if spreadsheet is None: return 

    master_df = load_master_students(spreadsheet)
    if master_df.empty:
        st.warning("Master student list could not be loaded or is empty. Please check the 'Students' sheet.")
        return

    # 2. Checkpoint Management Section
    col1, col2 = st.columns([5, 3])

    with col1:
        checkpoint = st.text_input(
            "📍 Current Checkpoint Name",
            key="checkpoint_input",
            placeholder="Enter Location/Activity Name (e.g., Temple Visit Check-In)",
            disabled=st.session_state.is_initialized
        )
        if st.session_state.is_initialized:
            st.session_state.current_checkpoint = checkpoint
        
    with col2:
        st.write(" ") # Spacer for alignment
        if not st.session_state.is_initialized:
            if st.button("Start New Checkpoint", type="primary", use_container_width=True, disabled=not checkpoint):
                st.session_state.is_initialized = True
                st.session_state.current_checkpoint = checkpoint
                get_current_attendance_status.clear()
                st.rerun()
        else:
            if st.button("End Checkpoint & Reset App", use_container_width=True, help="Resets the application interface for the next event. All data is securely saved in Google Sheets."):
                st.session_state.is_initialized = False
                st.session_state.current_checkpoint = ""
                st.session_state.scanned_id_buffer = None
                get_current_attendance_status.clear()
                st.rerun()

    st.markdown("---")
    
    # 3. Active Attendance Interface
    if st.session_state.is_initialized and st.session_state.current_checkpoint:
        
        # Fetch real-time status (Refreshes every 1s)
        status_df = get_current_attendance_status(spreadsheet, st.session_state.current_checkpoint, master_df)
        
        present_count = len(status_df[status_df['Status'] == 'Present'])
        total_count = len(master_df)

        # Real-Time Metric Display
        st.metric("Total Students Checked In (Real-Time)", f"{present_count} / {total_count}", delta=f"{total_count - present_count} Remaining", delta_color="inverse")
        st.markdown("---")
        
        
        # --- Scanner View ---
        st.subheader("🤳 Continuous Scan Mode")
        st.caption("Position the camera to scan student QR codes. No button presses are required between scans.")
        
        # Webrtc Streamer component to access the mobile camera (set to prefer environment/rear camera)
        webrtc_streamer(
            key="scanner_stream",
            video_processor_factory=BarcodeDetector,
            rtc_configuration={"iceServers": [{"urls": ["stun:stun.l.google.com:19302"]}]},
            media_stream_constraints={"video": {"facingMode": "environment"}}, 
            sendback_video=False 
        )
        
        # --- Continuous Scan Processing ---
        scanned_id = st.session_state.get('scanned_id_buffer')
        
        if scanned_id:
            if scanned_id in master_df['ID'].values:
                
                # Check the current status before writing
                current_status_row = status_df[status_df['ID'] == scanned_id]
                current_status = current_status_row['Status'].iloc[0] if not current_status_row.empty else 'Absent'
                
                if current_status != 'Present':
                    # Log the Present status instantly
                    success, name = save_attendance_entry(spreadsheet, scanned_id, 'Present')
                    if success:
                        st.toast(f"✅ CHECKED IN: {name}", icon='✅')
                    # If not successful, an error is already displayed in save_attendance_entry
                else:
                    name = st.session_state.id_to_name.get(scanned_id, "Student")
                    st.warning(f"⚠️ Already Checked In: {name}. Status saved by another device or previously.", icon='⚠️')
            else:
                st.error(f"❌ Invalid ID Scanned: {scanned_id}. ID not found in master list.", icon='❌')
                
            # CRITICAL: Clear the buffer to immediately allow the next scan from the camera feed
            st.session_state.scanned_id_buffer = None
            st.rerun() # Rerun to update the counter and list view

        st.markdown("---")
        
        # --- Real-Time Manual Override and Status List ---
        st.subheader("Manual Status Check (Tap to Override)")
        
        # Prepare the list for display, ensuring all students are included
        display_df = master_df.merge(status_df[['ID', 'Status']], on='ID', how='left').fillna({'Status': 'Absent'})

        # Use a responsive grid (3 columns is ideal for mobile viewing)
        cols = st.columns(3) 
        col_index = 0

        for index, row in display_df.iterrows():
            student_id = row['ID']
            name = row['Name']
            status = row['Status']
            css_class = 'present' if status == 'Present' else 'absent'
            
            with cols[col_index]:
                # Button displays name and serves as the manual override control
                new_status = 'Absent' if status == 'Present' else 'Present'
                
                if st.button(
                    f"{name}", 
                    key=f"manual_btn_{student_id}_{status}", 
                    use_container_width=True
                ):
                    # Manual override: Log the new status
                    save_attendance_entry(spreadsheet, student_id, new_status)
                    st.toast(f"Manual Override: {name} marked as {new_status}!", icon='🔄')

                # Inject style based on the real-time status
                st.markdown(f"""
                    <script>
                        var button = document.querySelector('[data-testid="stButton"] button[key="manual_btn_{student_id}_{status}"]');
                        if (button) {{
                            button.classList.remove('present', 'absent');
                            button.classList.add('{css_class}');
                        }}
                    </script>
                """, unsafe_allow_html=True)
                
            col_index = (col_index + 1) % len(cols)
    else:
        st.info("👆 Please enter a Checkpoint Name and click 'Start New Checkpoint' to activate the real-time tracking interface.")


if __name__ == "__main__":
    # Ensure critical dependencies are available before running
    try:
        import cv2
    except ImportError:
        st.error("🚨 Required dependency `opencv-python` is missing. Please ensure all libraries are installed.")
        st.stop()
        
    main()
