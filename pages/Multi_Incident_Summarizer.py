import streamlit as st
import requests
import time
import re
import pandas as pd
import tempfile
import os
import google.generativeai as genai

# --- Configure Gemini ---
GEMINI_API_KEY = st.secrets["GEMINI_API_KEY"]
genai.configure(api_key=GEMINI_API_KEY)
gemini_model = genai.GenerativeModel("gemini-1.5-flash-8b")

# --- SNOW Base URL from secrets ---
BASE_URL = st.secrets["SNOW_BASE_URL"]

def summarize_with_gemini(prompt):
    try:
        response = gemini_model.generate_content(prompt)
        return response.text.strip()
    except Exception as e:
        return f"Error: {str(e)}"

def parse_cookies(uploaded_file):
    cookies = {}
    for line in uploaded_file.getvalue().decode().splitlines():
        if not line.startswith("#") and line.strip():
            parts = line.strip().split("\t")
            if len(parts) == 7:
                _, _, _, _, _, name, value = parts
                cookies[name] = value
    return cookies

def poll_export_status(session, poll_url):
    for attempt in range(20):
        response = session.get(poll_url)
        if response.status_code == 200:
            match = re.search(r'"sys_id":"([a-f0-9]+)"', response.text)
            if match:
                return match.group(1)
        time.sleep(2)
    raise Exception("Failed to extract sys_id.")

def download_csv(session, sys_id):
    download_url = f"{BASE_URL}/sys_report_template.do?CSV&jvar_report_id={sys_id}"
    response = session.get(download_url)
    if response.status_code == 200 and response.text.strip():
        return response.text
    raise Exception("CSV download failed or returned empty.")

# --- Streamlit UI ---
st.set_page_config(page_title="SNOW Incident Summarizer", layout="wide")
st.title("SNOW Incident Summarizer")

with st.expander("📘 How to Use"):
    st.markdown("""
    1. 🔑 Install [Get cookies.txt extension](https://chromewebstore.google.com/detail/get-cookiestxt-locally/cclelndahbckbenkjhflpdbgdldlbecc)
    2. 📥 Login to SNOW and export `cookies.txt`
    3. 📤 Upload `cookies.txt` here
    4. 🔍 Enter one or more incident numbers like `INC1234567, INC1234568`
    """)

st.warning("Make sure following columns are added in your SNOW (Number, Short Description, Description, Additional Comments, Work Notes, Resolution Summary, State, Reporting Subcategory)")

incident_input = st.text_input("Incident Number(s)", placeholder="e.g., INC1234567, INC1234568").strip()
incident_numbers = [x.strip() for x in incident_input.split(",") if x.strip()]
cookies_file = st.file_uploader("Upload cookies.txt", type="txt")

if incident_numbers and cookies_file:
    try:
        cookies = parse_cookies(cookies_file)
        session = requests.Session()
        session.cookies.update(cookies)

        for inc in incident_numbers:
            st.markdown(f"## 🔍 {inc}")
            try:
                with st.spinner(f"Fetching {inc}..."):
                    export_url = f"{BASE_URL}/incident_list.do?CSV"
                    payload = {
                        "CSV": "",
                        "sysparm_query": f"number={inc}",
                        "sysparm_target": "incident",
                        "sysparm_force_update": "true",
                    }

                    response = session.post(export_url, data=payload, allow_redirects=False)

                    if response.status_code == 302 and "poll_redirect" in response.headers.get("Location", ""):
                        poll_url = BASE_URL + response.headers["Location"]
                        sys_id = poll_export_status(session, poll_url)
                        csv_data = download_csv(session, sys_id)
                    elif response.status_code == 200 and "text/csv" in response.headers.get("Content-Type", ""):
                        csv_data = response.text
                    else:
                        st.error(f"❌ Failed to fetch {inc}. Check cookies or incident number.")
                        continue

                    with tempfile.NamedTemporaryFile(delete=False, suffix=".csv") as f:
                        f.write(csv_data.encode("utf-8"))
                        temp_csv_path = f.name

                    df = pd.read_csv(temp_csv_path)
                    os.remove(temp_csv_path)

                    if df.empty:
                        st.warning(f"No data for {inc}.")
                        continue

                    row = df.iloc[0]
                    number = row.get("number", "")
                    short_desc = row.get("short_description", "")
                    description = row.get("description", "")
                    comments = row.get("comments", "")
                    work_notes = row.get("work_notes", "")
                    resolution = row.get("u_resolution_summary", "")
                    subcategory = row.get("u_reporting_subcategory", "")
                    state = row.get("state", "")

                    single_row_df = pd.DataFrame([{
                        "Number": number,
                        "Short Description": short_desc,
                        "Description": description,
                        "Comments": comments,
                        "Work Notes": work_notes,
                        "Resolution": resolution,
                        "Subcategory": subcategory,
                        "State": state
                    }])

                    st.dataframe(single_row_df)

                    # --- Prompt for Gemini ---
                    prompt = (
                        "You are a professional incident analyst helping a support team understand ServiceNow tickets. "

                        "Follow this exact structure:\n\n"

                        "1️⃣ **Issue:** \n\n"

                        "2️⃣ **Troubleshooting Steps Performed:**  this should be detailed and in points\n\n"

                        "3️⃣ **Most Recent Update:** , you get this from most recent work notes and additional comments as per the latest timestamp.\n\n"

                        "4️⃣ **Actions to be taken or resolution:**  \n\n"

                        "Keep the summary informative. Your tone should be professional and clear.\n\n"

                        "### Incident Data:\n"
                        f"Incident Number: {number}\n"
                        f"Short Description: {short_desc}\n"
                        f"Description: {description}\n"
                        f"Additional Comments: {comments}\n"
                        f"Work Notes: {work_notes}\n"
                        f"Resolution Summary: {resolution}\n"
                        f"Reporting Subcategory: {subcategory}\n"
                        f"State: {state}\n\n"
                        f"Do not assume or add anything not in the text."
                    )

                    summary = summarize_with_gemini(prompt)

                    st.subheader("📄 Summary")
                    st.markdown(summary)
                    st.divider()

            except Exception as e:
                st.error(f"Error while processing {inc}: {str(e)}")
                st.divider()

    except Exception as e:
        st.error(f"❌ {str(e)}")
