import streamlit as st
import os
from waypoint_modifier import modify_waypoints

st.set_page_config(page_title="Waypoint Modifier", layout="centered")

st.title("📍 KMZ Waypoint Modifier")
st.write("Upload `.kmz` files to modify gimbal angles across waypoints and download the result.")
st.page_link("pages/KMZ_Extraction_Guide.py", label="Guide to extract/upload KMZ files from DJI Flight Hub")

uploaded_files = st.file_uploader("Drag and drop KMZ files here", type=["kmz"], accept_multiple_files=True)

if uploaded_files:
    for i, uploaded_file in enumerate(uploaded_files):
        with open(f"input_{i}.kmz", "wb") as f:
            f.write(uploaded_file.read())

    st.success("Files uploaded successfully!")

    if st.button("Process Files"):
        for i, uploaded_file in enumerate(uploaded_files):
            progress = st.progress(0)
            for pct in range(1, 101):
                progress.progress(pct)
            st.write(f"Processing file {i+1}: {uploaded_file.name}")
            if modify_waypoints(f"input_{i}.kmz"):
                st.success(f"{uploaded_file.name} processed successfully.")
