import streamlit as st
import os

st.set_page_config(page_title="KMZ Extraction Guide", layout="centered")
print(os.getcwd())

st.title("Extract and Upload DJI Flight Hub KMZ Files")
# st.write("""
# DJI Flight Hub allows you to export mission data as `.kmz` files. Here's how to do it:
# """)

st.header("Step 1: Download Flight Mapping Mission from DJI Flight Hub")
st.image("images/waypoint_download.png", caption="Flight Hub export interface")

st.header("Step 2: Process the KMZ file(s)")
st.write("Upload KMZ files to this app which will process and save as new files")

st.header("Step 3: Upload the modified KMZ file(s) to DJI Flight Hub")
st.image("images/waypoint_upload.png", caption="Flight Hub import interface")

# st.success("Once downloaded, you can upload the KMZ file on the Home page to modify it.")

