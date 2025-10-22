import streamlit as st
import os
from pathlib import Path
import shutil
from waypoint_modifier import modify_waypoints
import zipfile
import glob


st.set_page_config(
    page_title="Waypoint Modifier", 
    layout="centered",
    # page_icon="📍"
)

# Set dark theme
st.markdown("""
    <style>
        .stApp {
            background-color: #0E1117;
            color: #FAFAFA;
        }
        .stButton>button {
            background-color: #1E88E5;
            color: white;
        }
        .stDownloadButton>button {
            background-color: #1E88E5;
            color: white;
        }
    </style>
""", unsafe_allow_html=True)

def main():
    st.title("KMZ Waypoint Modifier")
    st.write("Upload `.kmz` files to modify gimbal angles across waypoints and download the result.")
    st.page_link("pages/KMZ_Extraction_Guide.py", 
                label="Guide to extract/upload KMZ files from DJI Flight Hub")

    uploaded_files = st.file_uploader("Drag and drop KMZ files here", 
                                    type=["kmz"], 
                                    accept_multiple_files=True)

    if not uploaded_files:
        return

    # Create temp directory for processing
    temp_dir = "temp_processing"
    os.makedirs(temp_dir, exist_ok=True)

    # Save uploaded files
    input_files = []
    for uploaded_file in uploaded_files:
        input_path = os.path.join(temp_dir, uploaded_file.name)
        with open(input_path, "wb") as f:
            f.write(uploaded_file.getbuffer())
        input_files.append((input_path, uploaded_file.name))

    if st.button("Process All Files", type="primary"):
        # Create output directory
        output_dir = "processed_files"
        os.makedirs(output_dir, exist_ok=True)
        
        # Process files sequentially
        progress_bar = st.progress(0)
        status_container = st.container()
        results = []
        
        for i, (input_path, original_name) in enumerate(input_files):
            with status_container:
                status = st.status(f"Processing {original_name}...")
                try:
                    output_filename = original_name.split('.kmz')[0] + "_MODIFIED.kmz"
                    output_path = os.path.join(output_dir, output_filename)
                    
                    # Process the file
                    if modify_waypoints(input_path):
                        # The file is created in the same directory as input with a timestamp
                        base_name = os.path.splitext(original_name)[0]
                        modified_path = os.path.join(os.path.dirname(input_path), f"{base_name}-MODIFIED-*.kmz")
                        modified_files = glob.glob(modified_path)
                        if modified_files:
                            # Get the most recent file (in case there are multiple matches)
                            modified_path = max(modified_files, key=os.path.getmtime)
                        if os.path.exists(modified_path):
                            # Ensure the output directory exists
                            os.makedirs(os.path.dirname(output_path), exist_ok=True)
                            # Copy the file to the output directory
                            shutil.copy2(modified_path, output_path)
                            # Remove the temporary file
                            os.remove(modified_path)
                            results.append((True, original_name, output_path))
                            status.update(label=f"✅ {original_name} - Processed successfully", 
                                        state="complete")
                        else:
                            # Try to find any .kmz file that might have been created
                            all_kmz_files = glob.glob(os.path.join(os.path.dirname(input_path), "*.kmz"))
                            if all_kmz_files:
                                modified_path = max(all_kmz_files, key=os.path.getmtime)
                                if modified_path != input_path:  # Make sure it's not the input file
                                    shutil.copy2(modified_path, output_path)
                                    os.remove(modified_path)
                                    results.append((True, original_name, output_path))
                                    status.update(label=f"✅ {original_name} - Processed successfully (recovered output)", 
                                                state="complete")
                                    continue
                            
                            results.append((False, original_name, f"Output file not found. Checked: {modified_path}"))
                            status.update(label=f"❌ {original_name} - Output file not found", 
                                        state="error")
                    else:
                        results.append((False, original_name, "Processing failed"))
                        status.update(label=f"❌ {original_name} - Processing failed", 
                                    state="error")
                        
                except Exception as e:
                    results.append((False, original_name, f"Error: {str(e)}"))
                    status.update(label=f"❌ {original_name} - Error: {str(e)}", 
                                state="error")
                
                # Update progress
                progress = (i + 1) / len(input_files)
                progress_bar.progress(progress)
        
        # Show status for each file
        st.divider()
        st.subheader("Processing Results")
        for success, filename, output_path in results:
            if success:
                st.success(f"✓ Successfully processed: {filename}")
            else:
                st.error(f"✗ Failed to process {filename}: {output_path}")

        # Create a zip file containing all successfully processed files
        if any(success for success, _, _ in results):
            zip_filename = os.path.join(output_dir, "processed_files.zip")
            with zipfile.ZipFile(zip_filename, 'w') as zipf:
                for success, filename, output_path in results:
                    if success and os.path.exists(output_path):
                        zipf.write(output_path, os.path.basename(output_path))
            
            # Provide download link for the zip file
            with open(zip_filename, 'rb') as f:
                st.download_button(
                    label="Download All Processed Files",
                    type="primary",
                    data=f,
                    file_name="processed_waypoints.zip",
                    mime="application/zip"
                )
            
            # Clean up individual files
            for _, _, output_path in results:
                if os.path.exists(output_path):
                    os.remove(output_path)
            
            # Remove the zip file after download is complete
            if os.path.exists(zip_filename):
                os.remove(zip_filename)
        
        # Clean up
        shutil.rmtree(temp_dir, ignore_errors=True)

if __name__ == "__main__":
    main()