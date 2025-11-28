import streamlit as st
import pandas as pd
import gspread
import time
from streamlit_webrtc import webrtc_streamer, VideoTransformerBase
from pyzbar.pyzbar import decode
from PIL import Image
import numpy as np
import io

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
    padding: 0.8rem 0.5rem; /* Slightly reduced padding for better fit */
    font-size: 1.0rem; 
    font-weight: 700;
    margin: 5px 0;
    border-radius: 8px; /* Slightly smaller radius */
    box-shadow: 0 2px 4px rgba(0, 0, 0, 0.1);
    transition: all 0.2s ease;
    text-align: center;
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

/* Ensure student name on the left is bold and vertically aligned */
.student-name {
    font-weight: 600;
    font-size: 1.1rem;
    padding: 0.8rem 0; /* Align with button padding */
    display: flex;
    align-items: center;
    min-height: 100%; /* Ensure full height alignment */
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
def load_master_students(_spreadsheet):
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
def get_current_attendance_status(_spreadsheet, current_checkpoint, master_df):
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


@st.cache_data(ttl=1)
def get_active_checkpoint_name(_spreadsheet):
    """
    Reads a reserved row in AttendanceLog (using 'CONFIG_STATE' as ID) to find
    the current active checkpoint name for global state synchronization.
    """
    try:
        wks = _spreadsheet.worksheet("AttendanceLog")
        df_log = pd.DataFrame(wks.get_all_records())
        
        # Look for the row that holds the active checkpoint name (sentinel ID)
        config_row = df_log[df_log['ID'] == 'CONFIG_STATE']
        
        if config_row.empty:
            return ""
        
        # The Checkpoint column holds the active checkpoint name. Return the latest one.
        # This will be "" if the app was globally reset.
        return config_row.sort_values(by='Timestamp', ascending=False)['Checkpoint'].iloc[0]
        
    except Exception:
        # If the sheet is empty or columns are wrong, return empty string
        return ""

def set_active_checkpoint_name(spreadsheet, checkpoint_name):
    """Writes the new active checkpoint name to the sheet for global sync."""
    try:
        wks = spreadsheet.worksheet("AttendanceLog")
        timestamp = pd.Timestamp.now().strftime('%Y-%m-%d %H:%M:%S')
        
        # Sentinel ID 'CONFIG_STATE' used for global app state management
        # Checkpoint column holds the active name, Status column is not used here ('N/A')
        new_row = [timestamp, checkpoint_name, 'CONFIG_STATE', 'N/A', 'N/A']
        wks.append_row(new_row, value_input_option='USER_ENTERED')
        
        # Force cache clear on all sync-related functions
        get_active_checkpoint_name.clear()
        get_current_attendance_status.clear()
        return True
    except Exception as e:
        st.error(f"Error synchronizing checkpoint state globally: {e}")
        return False


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

def process_uploaded_image(uploaded_file, spreadsheet, master_df, status_df):
    """Processes an uploaded image to find and log a QR code."""
    if uploaded_file is None:
        return
        
    try:
        # Read file as bytes and open with PIL
        image = Image.open(io.BytesIO(uploaded_file.read()))
        
        # Decode the image for barcodes/QR codes
        decoded_objects = decode(image)

        if decoded_objects:
            student_id = decoded_objects[0].data.decode('utf-8').strip()
            
            if student_id in master_df['ID'].values:
                # Check the current status before writing
                current_status_row = status_df[status_df['ID'] == student_id]
                current_status = current_status_row['Status'].iloc[0] if not current_status_row.empty else 'Absent'
                
                if current_status != 'Present':
                    success, name = save_attendance_entry(spreadsheet, student_id, 'Present')
                    if success:
                        st.success(f"✅ IMAGE SCANNED: {name} successfully checked in!")
                else:
                    name = st.session_state.id_to_name.get(student_id, "Student")
                    st.warning(f"⚠️ Already Checked In: {name}", icon='⚠️')
            else:
                st.error(f"❌ Invalid ID Scanned from Image: {student_id}. ID not found in master list.", icon='❌')
        else:
            st.error("🔍 No QR or barcode found in the uploaded image. Try taking a closer, clearer picture.")
            
        # Re-run to refresh status list and clear the file uploader
        st.session_state.uploaded_file = None
        st.rerun()

    except Exception as e:
        st.error(f"An error occurred while processing the image: {e}")


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
        if 'manual_id_input' not in st.session_state:
            st.session_state.manual_id_input = ""
        # Initialize for image upload
        if 'uploaded_file' not in st.session_state:
            st.session_state.uploaded_file = None


    spreadsheet = get_gspread_client()
    if spreadsheet is None: return 

    master_df = load_master_students(spreadsheet)
    if master_df.empty:
        st.warning("Master student list could not be loaded or is empty. Please check the 'Students' sheet.")
        return

    # --- Global State Synchronization ---
    global_checkpoint = get_active_checkpoint_name(spreadsheet)
    
    # Override local state if a global checkpoint is active
    if global_checkpoint:
        st.session_state.is_initialized = True
        st.session_state.current_checkpoint = global_checkpoint
    else:
        # If the global checkpoint is empty (reset), ensure local state is also reset
        st.session_state.is_initialized = False
        st.session_state.current_checkpoint = ""
    # -----------------------------------


    # 2. Checkpoint Management Section
    col1, col2 = st.columns([5, 3])

    with col1:
        # Checkpoint input field now uses the globally synchronized value
        checkpoint = st.text_input(
            "📍 Current Checkpoint Name",
            value=st.session_state.current_checkpoint,
            key="checkpoint_input",
            placeholder="Enter Location/Activity Name (e.g., Temple Visit Check-In)",
            # Disable if initialized globally OR if input field is pre-filled from global state
            disabled=st.session_state.is_initialized 
        )
        # This update ensures the current_checkpoint is always the displayed value
        st.session_state.current_checkpoint = checkpoint
        
    with col2:
        st.write(" ") # Spacer for alignment
        if not st.session_state.is_initialized:
            if st.button("Start New Checkpoint", type="primary", use_container_width=True, disabled=not checkpoint):
                # Global Start: Write to the sheet
                if set_active_checkpoint_name(spreadsheet, checkpoint):
                    st.session_state.is_initialized = True
                    st.session_state.current_checkpoint = checkpoint
                    st.toast(f"Checkpoint '{checkpoint}' started globally!", icon='🚀')
                    st.rerun()
        else:
            if st.button("End Checkpoint & Reset App (Global)", use_container_width=True, help="Resets the application interface for the next event on ALL connected devices."):
                # Global Reset: Write an empty name to the sheet
                if set_active_checkpoint_name(spreadsheet, ""):
                    st.session_state.is_initialized = False
                    st.session_state.current_checkpoint = ""
                    st.session_state.scanned_id_buffer = None
                    st.toast("Checkpoint ended and app reset globally!", icon='🛑')
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
        st.subheader("🤳 Continuous Scan Mode (May be unstable)")
        st.caption("This uses WebRTC and may fail on some networks/devices. If it fails, please use the manual entry or image upload below.")
        
        is_processing_scan = st.session_state.get('scanned_id_buffer') is not None
        
        if not is_processing_scan:
            # Webrtc Streamer component with expanded STUN list for stability
            webrtc_streamer(
                key="scanner_stream",
                video_processor_factory=BarcodeDetector,
                rtc_configuration={
                    "iceServers": [
                        {"urls": ["stun:stun.l.google.com:19302"]},
                        {"urls": ["stun:stun1.l.google.com:19302"]},
                        {"urls": ["stun:stun2.l.google.com:19302"]},
                        {"urls": ["stun:stun3.l.google.com:19302"]},
                        {"urls": ["stun:stun4.l.google.com:19302"]},
                        {"urls": ["stun:global.stun.twilio.com:3478"]},
                        {"urls": ["stun:stun.nextcloud.com:443"]},
                        {"urls": ["stun:stun.voipbuster.com"]},
                        {"urls": ["stun:stun.schlund.de"]},
                    ]
                },
                media_stream_constraints={"video": {"facingMode": "environment"}}, 
                sendback_video=False 
            )
        else:
            # Show a message while processing the scan
            st.info("Processing scan... Please wait.")


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
                else:
                    name = st.session_state.id_to_name.get(scanned_id, "Student")
                    st.warning(f"⚠️ Already Checked In: {name}", icon='⚠️')
            else:
                st.error(f"❌ Invalid ID Scanned: {scanned_id}. ID not found in master list.", icon='❌')
                
            # CRITICAL: Clear the buffer to immediately allow the next scan/reenable camera
            st.session_state.scanned_id_buffer = None
            st.rerun() 

        st.markdown("---")
        
        # --- Image Upload Scan (Reliable Alternative 2) ---
        st.subheader("🖼️ Image Upload Scan (Alternative)")
        uploaded_file = st.file_uploader(
            "Take a picture of the QR code and upload the image file here:", 
            type=['png', 'jpg', 'jpeg'],
            key="uploaded_file"
        )
        
        if uploaded_file is not None:
            # Pass the file object to the processor function
            process_uploaded_image(uploaded_file, spreadsheet, master_df, status_df)
            
        st.markdown("---")

        # --- Manual ID Entry (Reliable Alternative 1) ---
        st.subheader("⌨️ Manual ID/Code Entry (Reliable Fallback)")
        
        manual_id = st.text_input("Enter Student ID/Code", key="manual_id_input", placeholder="ID number or text from the QR code")
        
        if st.button("Check In Manually", disabled=not manual_id, type="secondary", use_container_width=True):
            student_id = st.session_state.manual_id_input.strip()
            
            if student_id in master_df['ID'].values:
                
                # Check the current status before writing
                current_status_row = status_df[status_df['ID'] == student_id]
                current_status = current_status_row['Status'].iloc[0] if not current_status_row.empty else 'Absent'
                
                if current_status != 'Present':
                    # Log the Present status instantly
                    success, name = save_attendance_entry(spreadsheet, student_id, 'Present')
                    if success:
                        st.toast(f"✅ MANUAL CHECKED IN: {name}", icon='✅')
                else:
                    name = st.session_state.id_to_name.get(student_id, "Student")
                    st.warning(f"⚠️ Already Checked In: {name}", icon='⚠️')
            else:
                st.error(f"❌ Invalid ID Entered: {student_id}. ID not found in master list.", icon='❌')

            # Clear input and rerun to refresh the list and clear the input box
            st.session_state.manual_id_input = ""
            st.rerun() 

        st.markdown("---")
        
        # --- Real-Time Manual Override and Status List ---
        st.subheader("Manual Status Check (Tap to Toggle Status)")
        
        # Prepare the list for display, ensuring all students are included
        display_df = master_df.merge(status_df[['ID', 'Status']], on='ID', how='left').fillna({'Status': 'Absent'})
        
        st.markdown("---") # Separator before the list starts

        # Custom two-column layout for each student to clearly separate name and status button
        for index, row in display_df.iterrows():
            student_id = row['ID']
            name = row['Name']
            status = row['Status']
            css_class = 'present' if status == 'Present' else 'absent'
            
            # Use a two-column row for Name (2 parts wide) and Status Button (1 part wide)
            col_name, col_status = st.columns([2, 1])
            
            with col_name:
                # Student name on the left
                st.markdown(f'<div class="student-name">{name}</div>', unsafe_allow_html=True)
                
            with col_status:
                # Status button on the right (shows current status and acts as toggle)
                new_status = 'Absent' if status == 'Present' else 'Present'
                
                if st.button(
                    status, # Display the current status on the button
                    key=f"manual_btn_{student_id}", 
                    use_container_width=True
                ):
                    # Manual override: Log the new status
                    save_attendance_entry(spreadsheet, student_id, new_status)
                    st.toast(f"Manual Override: {name} marked as {new_status}!", icon='🔄')
                
                # Inject style based on the real-time status
                st.markdown(f"""
                    <script>
                        var button = document.querySelector('[data-testid="stButton"] button[key="manual_btn_{student_id}"]');
                        if (button) {{
                            button.classList.remove('present', 'absent');
                            button.classList.add('{css_class}');
                        }}
                    </script>
                """, unsafe_allow_html=True)
                
            st.markdown("---") # Separator between student entries

    else:
        # Inform users when the checkpoint is globally reset
        if global_checkpoint == "" and st.session_state.is_initialized:
            st.warning("🛑 The checkpoint has been ended by the primary teacher. Please wait for a new checkpoint to be started.")
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
