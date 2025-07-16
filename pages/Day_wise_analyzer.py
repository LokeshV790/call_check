import streamlit as st
import requests
import base64
import pandas as pd
import datetime
import re
import time
import random
import logging
import google.generativeai as genai
import concurrent.futures

# --- Setup Gemini ---
GEMINI_API_KEY = st.secrets["GEMINI_API_KEY"]
genai.configure(api_key=GEMINI_API_KEY)
gemini_model = genai.GenerativeModel("gemini-1.5-flash-8b")

# --- Logging ---
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# --- Auth helper ---
def get_auth_header(user, token):
    auth_str = f"{user}:{token}"
    auth_bytes = auth_str.encode("utf-8")
    auth_b64 = base64.b64encode(auth_bytes).decode("utf-8")
    return {"Authorization": f"Basic {auth_b64}"}

# --- Fetch calls (incoming & outgoing) ---
def fetch_calls_all_types(date_from, date_to, user, token):
    results = []
    for direction in ["incoming", "outgoing"]:
        headers = get_auth_header(user, token)
        page = 1
        while True:
            url = (
                f"https://my.cloudtalk.io/api/calls/index.json?"
                f"date_from={date_from} 00:00:00&date_to={date_to} 23:59:59"
                f"&type={direction}&status=answered"
                f"&limit=1000&page={page}"
            )
            resp = requests.get(url, headers=headers)
            resp.raise_for_status()
            data = resp.json().get("responseData", {})
            batch = data.get("data", [])
            results.extend(batch)
            if page >= data.get("pageCount", 1):
                break
            page += 1
    return results

# --- Extract incident number from notes ---
def extract_incident(notes):
    if not notes:
        return "Not Found"
    for note_obj in notes:
        match = re.search(r"(INC\w+)", note_obj.get("note", ""))
        if match:
            return match.group(1)
    return "Not Found"

# --- Fetch transcript ---
def fetch_transcript(call_id, user, token):
    url = f"https://api.cloudtalk.io/v1/ai/calls/{call_id}/transcription"
    headers = get_auth_header(user, token)
    response = requests.get(url, headers=headers)
    response.raise_for_status()
    segments = response.json()["data"]["segments"]
    transcript = "\n".join([f"{s['caller']}: {s['text']}" for s in segments])
    return transcript

# --- Generate Gemini summary ---
def generate_summary(transcript, max_retries=5):
    retries = 0
    while retries < max_retries:
        try:
            prompt = (
                "Summarize the following customer support call in a structured format.\n"
                "Include:\n1. Problem\n2. Actions Taken\n3. Resolution or Next Steps\n\nTranscript:\n" + transcript
            )
            response = gemini_model.generate_content(prompt)
            return response.text.strip()
        except Exception as e:
            wait = min(2 ** retries + random.random(), 30)
            logging.warning(f"Retrying Gemini in {wait:.1f}s due to: {e}")
            time.sleep(wait)
            retries += 1
    return "Error: Gemini failed to generate summary."

# --- Fetch sentiment ---
def fetch_sentiment(call_id, user, token):
    url = f"https://api.cloudtalk.io/v1/ai/calls/{call_id}/overall-sentiment"
    headers = get_auth_header(user, token)
    try:
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        return response.json().get("overallSentiment", "Unknown").capitalize()
    except:
        return "Not Found"

# --- Streamlit UI ---
st.set_page_config(page_title="CloudTalk Call Analyzer", layout="wide")
st.title("CloudTalk Call Analyzer")

api_user = st.secrets["CT_API_ID"]
api_token = st.secrets["CT_API_KEY"]

col1, col2 = st.columns(2)
with col1:
    date_from = st.date_input("Start Date", datetime.date.today() - datetime.timedelta(days=1))
with col2:
    date_to = st.date_input("End Date", datetime.date.today())

if st.button("Fetch & Analyze Calls"):
    try:
        st.info("Fetching calls...")

        calls = fetch_calls_all_types(date_from, date_to, api_user, api_token)

        if not calls:
            st.warning("No calls found for this date range.")
        else:
            st.markdown("## ✅ Raw Calls Fetched from CloudTalk")
            raw_data = []
            for call in calls:
                cdr = call.get("Cdr", {})
                agent = call.get("Agent", {})
                call_id = cdr.get("id")
                started_at = cdr.get("started_at", "N/A")
                direction = cdr.get("type", "Unknown")
                agent_name = agent.get("fullname", "Unknown")
                raw_data.append({
                    "Call ID": call_id,
                    "Date": started_at,
                    "Agent Name": agent_name,
                    "Direction": direction
                })
            st.dataframe(pd.DataFrame(raw_data), use_container_width=True)

            st.info("Now processing each call for summaries and transcripts (parallel)...")

            rows = []

            # --- Define worker function for parallel execution ---
            def process_call(call):
                cdr = call.get("Cdr", {})
                agent = call.get("Agent", {})
                notes = call.get("Notes", [])

                call_id = cdr.get("id")
                started_at = cdr.get("started_at", "")
                call_date = started_at.split("T")[0] if started_at else "N/A"
                direction = cdr.get("type", "Unknown")
                agent_name = agent.get("fullname", "Unknown")

                incident_number = extract_incident(notes)
                sentiment = fetch_sentiment(call_id, api_user, api_token)

                try:
                    transcript = fetch_transcript(call_id, api_user, api_token)
                except:
                    transcript = "Transcript not available."

                summary = generate_summary(transcript) if transcript != "Transcript not available." else "Summary not available."

                return {
                    "Call ID": call_id,
                    "Date": call_date,
                    "Agent Name": agent_name,
                    "Direction": direction,
                    "Incident Number": incident_number,
                    "Sentiment": sentiment,
                    "Summary": summary,
                    "Transcript": transcript
                }

            # --- Process in parallel using ThreadPoolExecutor ---
            with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
                future_to_call = {executor.submit(process_call, call): call for call in calls}
                for future in concurrent.futures.as_completed(future_to_call):
                    rows.append(future.result())

            df = pd.DataFrame(rows)
            st.success("✅ Calls analyzed successfully!")

            for row in rows:
                with st.expander(f"📞 Call ID: {row['Call ID']} | Agent: {row['Agent Name']} | Date: {row['Date']} | Direction: {row['Direction']}"):
                    st.markdown(f"**Incident Number:** {row['Incident Number']}")
                    st.markdown(f"**Sentiment:** {row['Sentiment']}")
                    st.markdown("### 📝 Summary")
                    st.write(row["Summary"])
                    st.markdown("### 📄 Transcript")
                    st.text_area("Transcript", row["Transcript"], height=300, key=f"transcript_{row['Call ID']}")

            csv = df.to_csv(index=False).encode("utf-8")
            st.download_button("Download All Calls as CSV", csv, f"cloudtalk_calls_{date_from}_to_{date_to}.csv", "text/csv")

    except Exception as e:
        st.error(f"Error: {e}")
