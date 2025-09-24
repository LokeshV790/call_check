import streamlit as st
import requests
import base64
import json
import time
import random
import logging
import re
import pandas as pd
import google.generativeai as genai

# --- Setup Gemini ---
GEMINI_API_KEY = st.secrets["GEMINI_API_KEY"]
genai.configure(api_key=GEMINI_API_KEY)
gemini_model = genai.GenerativeModel("gemini-1.5-flash-8b")

# --- Logging ---
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# --- Summary Function Using Gemini ---
def get_summary(prompt, max_retries=5):
    retries = 0
    while retries < max_retries:
        try:
            with st.spinner("Generating AI Summary..."):
                response = gemini_model.generate_content(prompt)
                return response.text.strip()
        except Exception as e:
            wait = 2 ** retries + random.random()
            logging.warning(f"Gemini error: {e}. Retrying in {wait:.2f} seconds...")
            time.sleep(wait)
            retries += 1
    return "Error: Gemini failed to generate summary after multiple attempts."

# --- Auth header helper ---
def get_auth_header(user, token):
    auth_str = f"{user}:{token}"
    auth_bytes = auth_str.encode("utf-8")
    auth_b64 = base64.b64encode(auth_bytes).decode("utf-8")
    return {"Authorization": f"Basic {auth_b64}"}

# --- Fetch Call Info ---
def get_call_info(call_id, user, token):
    headers = get_auth_header(user, token)
    url = f"https://analytics-api.cloudtalk.io/api/calls/{call_id}"
    response = requests.get(url, headers=headers)
    response.raise_for_status()
    data = response.json()

    agent_name = "Not found"
    for step in data.get("call_steps", []):
        for agent_call in step.get("agent_calls", []):
            if agent_call.get("status") == "answered":
                agent_name = agent_call.get("name", "Not found")
                break
        if agent_name != "Not found":
            break

    incident_number = None
    for note in data.get("notes", []):
        match = re.search(r"INC\d+", note)
        if match:
            incident_number = match.group(0)
            break

    call_date = data.get("date")
    return data, agent_name, incident_number, call_date

# --- Fetch Transcript ---
def fetch_transcript(call_id, user, token):
    headers = get_auth_header(user, token)
    url = f"https://api.cloudtalk.io/v1/ai/calls/{call_id}/transcription"
    response = requests.get(url, headers=headers)
    response.raise_for_status()
    segments = response.json().get("data", {}).get("segments", [])
    return "\n".join([f"{s['caller']}: {s['text']}" for s in segments])

# --- Fetch Sentiment ---
def get_sentiment(call_id, user, token):
    headers = get_auth_header(user, token)
    url = f"https://api.cloudtalk.io/v1/ai/calls/{call_id}/overall-sentiment"

    try:
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        return response.json().get("overallSentiment", "Unavailable")
    except Exception as e:
        logging.error(f"Error fetching sentiment: {e}")
        return "Error"

# --- Fetch CloudTalk Summary ---
def get_cloudtalk_summary(call_id, user, token):
    headers = get_auth_header(user, token)
    url = f"https://api.cloudtalk.io/v1/ai/calls/{call_id}/summary"
    response = requests.get(url, headers=headers)
    response.raise_for_status()
    return response.json().get("summary", "Unavailable")

# --- Streamlit UI ---
st.set_page_config(page_title="Multi-Call Analyzer", layout="wide")
st.markdown("<h1 style='text-align: center;'>Multi-Call Analyzer Dashboard</h1>", unsafe_allow_html=True)
st.markdown("<p style='text-align: center; color: gray;'>Analyze multiple CloudTalk calls, view AI & CloudTalk summaries, and export easily.</p>", unsafe_allow_html=True)

with st.container():
    st.markdown("### Call IDs Input")
    call_ids_input = st.text_area("Enter Call IDs (comma separated)", height=100, placeholder="Example: 123456, 234567, 345678")

api_user = st.secrets["CT_API_ID"]
api_token = st.secrets["CT_API_KEY"]

st.markdown("---")

if st.button("Analyze Calls"):
    if not call_ids_input or not api_user or not api_token:
        st.warning("Please provide call IDs and credentials.")
    else:
        call_ids = [c.strip() for c in call_ids_input.split(",") if c.strip()]
        results = []

        for call_id in call_ids:
            try:
                with st.spinner(f"Processing Call ID: {call_id}..."):
                    data, agent_name, incident_number, call_date = get_call_info(call_id, api_user, api_token)
                    transcript = fetch_transcript(call_id, api_user, api_token)
                    sentiment = get_sentiment(call_id, api_user, api_token)
                    ct_summary = get_cloudtalk_summary(call_id, api_user, api_token)

                    prompt = (
                        f"Summarize the following customer support call in a structured format. "
                        f"Include:\n1. Problem\n2. Actions Taken\n3. Resolution or Next Steps\n\nTranscript:\n{transcript}"
                    )
                    ai_summary = get_summary(prompt)

                    results.append({
                        "Call ID": call_id,
                        "Date": call_date.split("T")[0] if call_date else "N/A",
                        "Incident Number": incident_number or "Not found",
                        "Agent Name": agent_name or "Not found",
                        "Sentiment": sentiment.capitalize() if sentiment else "Unavailable",
                        "CloudTalk Summary": ct_summary,
                        "AI Summary": ai_summary,
                        "Transcript": transcript
                    })
            except Exception as e:
                st.error(f"Error processing Call ID {call_id}: {e}")

        if results:
            df = pd.DataFrame(results)

            st.markdown("## Summary Table")
            st.dataframe(df[["Call ID", "Date", "Incident Number", "Agent Name", "Sentiment", "CloudTalk Summary", "AI Summary"]])

            # Download CSV
            csv = df.to_csv(index=False).encode("utf-8")
            st.download_button("Download CSV", csv, "call_summaries.csv", "text/csv")

            # Optional: show full transcripts below
            # st.markdown("## Detailed Transcripts")
            # for res in results:
            #     with st.expander(f"Call ID: {res['Call ID']} - Transcript"):
            #         st.text_area("Transcript", value=res["Transcript"], height=300)

            st.caption("Note: Sentiment and summaries are indicative only and may vary.")
