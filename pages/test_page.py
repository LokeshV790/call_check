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
gemini_model = genai.GenerativeModel(
    "gemini-2.0-flash-lite",
    generation_config={
        "temperature": 0.2,   # lower = more deterministic, less variety
        "top_p": 0.8,        # nucleus sampling
    }
)


# --- SNOW Base URL from secrets ---
BASE_URL = st.secrets["SNOW_BASE_URL"]

def summarize_with_gemini(prompt):
    try:
        with st.spinner("🔄 Generating summary..."):
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
    4. 🔍 Enter an incident number like `INC1234567`
    """)

st.warning("Make sure following columns are added in your SNOW (Number, Short Description, Description, Additional Comments, Work Notes, Resolution Summary, State, Reporting Subcategory)")

incident_number = st.text_input("Incident Number", placeholder="e.g., INC1234567").strip()
cookies_file = st.file_uploader("Upload cookies.txt", type="txt")

if incident_number and cookies_file:
    try:
        with st.spinner("🔄 Fetching Incident..."):
            cookies = parse_cookies(cookies_file)
            session = requests.Session()
            session.cookies.update(cookies)

            export_url = f"{BASE_URL}/incident_list.do?CSV"
            payload = {
                "CSV": "",
                "sysparm_query": f"number={incident_number}",
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
                st.error("❌ Unexpected response. Check cookies and incident number.")
                st.stop()

            with tempfile.NamedTemporaryFile(delete=False, suffix=".csv") as f:
                f.write(csv_data.encode("utf-8"))
                temp_csv_path = f.name

            df = pd.read_csv(temp_csv_path)
            os.remove(temp_csv_path)

            if df.empty:
                st.warning("No incident found.")
                st.stop()

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
                                "Number": row.get("number", ""),
                                "Short Description": row.get("short_description", ""),
                                "Description": row.get("description", ""),
                                "Comments": row.get("comments", ""),
                                "Work Notes": row.get("work_notes", ""),
                                "Resolution": row.get("u_resolution_summary", ""),
                                "Subcategory": row.get("u_reporting_subcategory", ""),
                                "State": row.get("state", "")
                            }])

            st.dataframe(single_row_df)

            # --- Display Structured Info ---
            st.subheader("📝 Incident Details")

            col1, col2 = st.columns(2)
            with col1:
                st.markdown(f"**Incident Number:** {number}")
                st.markdown(f"**Short Description:** {short_desc or 'N/A'}")
            with col2:
                st.markdown(f"**Reporting Subcategory:** {subcategory or 'N/A'}")
                with st.expander("📃 Description"):
                    st.markdown(description or "_No description provided._")

            # --- Build prompt for Gemini ---
            prompt = (
                "You are a professional incident analyst helping a support team understand ServiceNow tickets.\n\n"
                "Follow this exact structure:\n\n"
                "1️⃣ **Issue:**\n"
                "- Clearly state the problem based only on Description and Short Description.\n\n"
                "2️⃣ **Troubleshooting Steps Performed:** -- this should be detailed bullet points\n"
                 "- Write each as a separate bullet point starting with '- '.\n"
                "- Group by timestamps"
                "- Extract every action, observation, or note from Work Notes and Additional Comments.\n"
                "- Capture the sequence of events as they occurred.\n"
                "- also capture the steps given by the support team in the additional comments and work notes\n"
                "- Ensure clarity and completeness in each bullet point.\n"
                "- Do not merge multiple steps into one line.\n"
                "- Capture even the smallest details, including who performed the action (if mentioned).\n\n"
                "3️⃣ **Most Recent Update:**\n"
                "- Identify the most recent entry from Work Notes or Additional Comments based on timestamps.\n"
                "- Present it in full detail.\n\n"
                "4️⃣ **Actions to be taken or resolution:**\n"
                "- State the final resolution if provided.\n"
                "- If unresolved, clearly mention pending actions.\n\n"
                "⚠️ Rules:\n"
                "- Do not assume or add anything not present in the incident data.\n"
                "- Dont skip any troubleshooting steps, capture everything.\n"
                "- Keep the tone professional and clear.\n"
                "- Ensure Troubleshooting Steps are always in bullet format.\n\n"
                "### Incident Data:\n"
                f"Incident Number: {number}\n"
                f"Short Description: {short_desc}\n"
                f"Description: {description}\n"
                f"Additional Comments: {comments}\n"
                f"Work Notes: {work_notes}\n"
                f"Resolution Summary: {resolution}\n"
                f"Reporting Subcategory: {subcategory}\n"
                f"State: {state}\n\n"

            )


            summary = summarize_with_gemini(prompt)
            
            # --- Display Summary ---
            st.divider()
            st.subheader("Incident Summary")
            st.markdown(summary)

    except Exception as e:
        st.error(f"❌ {str(e)}")
