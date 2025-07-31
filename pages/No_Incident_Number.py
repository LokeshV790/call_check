import streamlit as st
import requests
import datetime
import base64
import logging
import re
import pandas as pd
from concurrent.futures import ThreadPoolExecutor, as_completed

# --- Configure Logging ---
logging.basicConfig(level=logging.INFO)

# --- Helper: Get Authorization Header ---
def get_auth_header(user, token):
    auth_str = f"{user}:{token}"
    auth_bytes = auth_str.encode("utf-8")
    auth_b64 = base64.b64encode(auth_bytes).decode("utf-8")
    return {"Authorization": f"Basic {auth_b64}"}

# --- Fetch AI Summary ---
def fetch_summary(call_id, user, token):
    url = f"https://api.cloudtalk.io/v1/ai/calls/{call_id}/summary"
    headers = get_auth_header(user, token)
    try:
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        return response.json().get("summary", "Summary not available.")
    except Exception as e:
        logging.warning(f"Failed to fetch summary for Call ID {call_id}: {e}")
        return "Summary not available."

# --- Fetch Calls (incoming & outgoing) ---
def fetch_calls_all_types(date_from, date_to, user, token):
    results = []
    for direction in ["incoming", "outgoing"]:
        headers = get_auth_header(user, token)
        page = 1
        while True:
            url = (
                f"https://my.cloudtalk.io/api/calls/index.json?"
                f"date_from={date_from} 00:00:00&date_to={date_to} 23:59:59"
                f"&type={direction}&status=answered&limit=1000&page={page}"
            )
            logging.info(f"Fetching calls from URL: {url}")
            resp = requests.get(url, headers=headers)
            resp.raise_for_status()
            data = resp.json().get("responseData", {})
            batch = data.get("data", [])
            results.extend(batch)
            if page >= data.get("pageCount", 1):
                break
            page += 1
    return results

# --- Streamlit UI ---
st.set_page_config(page_title="CloudTalk Calls Without INC", layout="wide")
st.title("📞 CloudTalk Calls Without Incident Number")

# --- API credentials from secrets ---
api_user = st.secrets["CT_API_ID"]
api_token = st.secrets["CT_API_KEY"]

# --- Date selectors ---
col1, col2 = st.columns(2)
with col1:
    date_from = st.date_input("Start Date", datetime.date.today() - datetime.timedelta(days=1))
with col2:
    date_to = st.date_input("End Date", datetime.date.today())

# --- Trigger processing ---
if st.button("🔍 Fetch Calls Without INC"):
    with st.spinner("Fetching and processing calls..."):
        raw_calls = fetch_calls_all_types(date_from, date_to, api_user, api_token)
        filtered_results = []

        # --- First filter calls without INC ---
        for call in raw_calls:
            cdr = call.get("Cdr", {})
            call_id = cdr.get("id")
            agent_name = call.get("Agent", {}).get("fullname", "Unknown")
            started_at = cdr.get("started_at", "")
            date = started_at[:10] if started_at else "Unknown"

            notes = call.get("Notes", [])
            incident_found = any(re.search(r"INC\d+", note.get("note", "")) for note in notes)

            if not incident_found:
                filtered_results.append({
                    "Call ID": call_id,
                    "Agent Name": agent_name,
                    "Date": date
                })

        st.info(f"{len(filtered_results)} calls without incident numbers. Fetching summaries in parallel...")

        # --- Parallel fetch summaries ---
        final_results = []
        with ThreadPoolExecutor(max_workers=10) as executor:
            future_to_call = {
                executor.submit(fetch_summary, item["Call ID"], api_user, api_token): item
                for item in filtered_results
            }
            for future in as_completed(future_to_call):
                item = future_to_call[future]
                summary = future.result()
                item["Summary"] = summary
                final_results.append(item)

        # --- Display results ---
        if final_results:
            df = pd.DataFrame(final_results)
            st.success("✅ Completed fetching summaries.")
            st.dataframe(df, use_container_width=True)

            # Optional: CSV download
            csv = df.to_csv(index=False).encode("utf-8")
            st.download_button("📥 Download CSV", data=csv, file_name="calls_without_inc.csv", mime="text/csv")
        else:
            st.info("No calls found without incident numbers.")
