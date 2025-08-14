import streamlit as st
import requests
import base64
import json
import time
import random
import logging
import re
import datetime
import google.generativeai as genai

# ------------------- CONFIG -------------------
st.set_page_config(page_title="📞 Call Analyzer", layout="wide")
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# --- Gemini Setup ---
GEMINI_API_KEY = st.secrets["GEMINI_API_KEY"]
genai.configure(api_key=GEMINI_API_KEY)
gemini_model = genai.GenerativeModel("gemini-1.5-flash-8b")

# --- Auth Helper ---
def get_auth_header(user, token):
    auth_str = f"{user}:{token}"
    auth_bytes = auth_str.encode("utf-8")
    auth_b64 = base64.b64encode(auth_bytes).decode("utf-8")
    return {"Authorization": f"Basic {auth_b64}"}

# ------------------- API HELPERS -------------------

def get_summary(prompt, max_retries=5):
    retries = 0
    while retries < max_retries:
        try:
            with st.spinner("🤖 Generating AI Summary..."):
                response = gemini_model.generate_content(prompt)
                return response.text.strip()
        except Exception as e:
            wait = 2 ** retries + random.random()
            logging.warning(f"Gemini error: {e}. Retrying in {wait:.2f}s...")
            time.sleep(wait)
            retries += 1
    return "⚠️ Error: Gemini failed to generate summary."

def get_call_info(call_id, user, token):
    headers = get_auth_header(user, token)
    url = f"https://analytics-api.cloudtalk.io/api/calls/{call_id}"
    response = requests.get(url, headers=headers)
    response.raise_for_status()
    data = response.json()

    agent_name = "Unknown"
    agent_id = None

    # Loop through call_steps to find first answered agent
    for step in data.get("call_steps", []):
        for agent_call in step.get("agent_calls", []):
            if agent_call.get("status") == "answered":
                agent_id = agent_call.get("id")
                # FIX: pull name directly instead of nested agent.fullname
                if agent_call.get("name"):
                    agent_name = agent_call["name"]
                break
        if agent_id:
            break

    # Extract incident number from notes
    incident_number = None
    for note in data.get("notes", []):
        match = re.search(r"INC\d+", note)
        if match:
            incident_number = match.group(0)
            break

    call_date = data.get("date")
    return data, agent_id, incident_number, call_date, agent_name

def fetch_calls_for_date(date_from, date_to, user, token):
    """Fetch all calls in date range to find agent name for given call_id"""
    headers = get_auth_header(user, token)
    all_calls = []
    page = 1
    limit = 1000
    while True:
        url = (
            f"https://my.cloudtalk.io/api/calls/index.json?"
            f"date_from={date_from}&date_to={date_to}&status=answered&limit={limit}&page={page}"
        )
        r = requests.get(url, headers=headers)
        r.raise_for_status()
        data = r.json().get("responseData", {})
        calls = data.get("data", [])
        all_calls.extend(calls)
        if page >= data.get("pageCount", 1):
            break
        page += 1
    return all_calls

def get_agent_name_by_callid(call_id, call_date, user, token):
    calls = fetch_calls_for_date(call_date, call_date, user, token)
    for call in calls:
        if str(call.get("Cdr", {}).get("id")) == str(call_id):
            return call.get("Agent", {}).get("fullname", "Unknown")
    return "Unknown"

def fetch_transcript(call_id, user, token):
    headers = get_auth_header(user, token)
    url = f"https://api.cloudtalk.io/v1/ai/calls/{call_id}/transcription"
    r = requests.get(url, headers=headers)
    r.raise_for_status()
    segments = r.json()["data"]["segments"]
    return "\n".join([f"{s['caller']}: {s['text']}" for s in segments])

def get_sentiment(call_id, user, token):
    headers = get_auth_header(user, token)
    url = f"https://api.cloudtalk.io/v1/ai/calls/{call_id}/overall-sentiment"
    r = requests.get(url, headers=headers)
    r.raise_for_status()
    return r.json().get("overallSentiment", "Unavailable")

def get_talk_listen_ratio(call_id, user, token):
    headers = get_auth_header(user, token)
    url = f"https://api.cloudtalk.io/v1/ai/calls/{call_id}/talk-listen-ratio"
    r = requests.get(url, headers=headers)
    r.raise_for_status()
    return r.json().get("talkListenRatio", [])

# ------------------- STREAMLIT UI -------------------

st.title("Sentiment Analyzer")
st.caption("Analyze calls from CloudTalk — fetch transcript, sentiment, talk/listen ratio, and AI-powered summary.")

call_id = st.text_input("Enter Call ID", placeholder="e.g., 932690427")
api_user = st.secrets["CT_API_ID"]
api_token = st.secrets["CT_API_KEY"]

if st.button("Fetch Call"):
    if not call_id:
        st.warning("Please enter a Call ID.")
    else:
        try:
            with st.spinner("📡 Fetching Call Info..."):
                data, agent_id, incident_number, call_date, agent_name_direct = get_call_info(call_id, api_user, api_token)
                formatted_date = call_date.split("T")[0] if call_date else None

            with st.spinner("👤 Getting Agent Name..."):
                agent_name = agent_name_direct
                if agent_name == "Unknown" and formatted_date:
                    agent_name = get_agent_name_by_callid(call_id, formatted_date, api_user, api_token)

            with st.spinner("📜 Fetching Transcript..."):
                transcript = fetch_transcript(call_id, api_user, api_token)

            with st.spinner("🤖 Generating AI Summary..."):
                prompt = f"""
                You are an expert call sentiment classifier.

                Analyze the emotional tone of both the **caller** and the **agent** in the conversation below.
                Classify each as **Positive** or **Negative** based on their overall attitude, cooperation, and politeness
                throughout the call — ignore neutral or irrelevant filler talk.

                Return your result in **exactly this format** with no extra words:
                caller: Positive/Negative
                agent: Positive/Negative

                Do not explain, summarize, or add anything beyond these two lines.

                Transcript:
                {transcript}
                """

                summary = get_summary(prompt)

            with st.spinner("😊 Fetching Sentiment..."):
                sentiment = get_sentiment(call_id, api_user, api_token)

            with st.spinner("🗣️ Fetching Talk-Listen Ratio..."):
                ratios = get_talk_listen_ratio(call_id, api_user, api_token)

            # --- Call Details ---
            st.subheader("📌 Call Details")
            col1, col2, col3 = st.columns(3)
            col1.metric("📞 Call ID", call_id)
            col1.metric("📅 Date", formatted_date or "N/A")
            col2.metric("📂 Incident", incident_number or "Not found")
            col2.metric("👤 Agent", agent_name)

            # --- AI Summary ---
            st.markdown("### 🤖 AI Sentiment Classification")

            caller_line, agent_line = summary.strip().split("\n") if "\n" in summary else (summary, "")

            def sentiment_badge(label, sentiment):
                color = "green" if sentiment.lower() == "positive" else "red"
                icon = "😊" if sentiment.lower() == "positive" else "😠"
                return f"<span style='background-color:{color};color:white;padding:4px 8px;border-radius:8px;'>{icon} {label}: {sentiment}</span>"

            st.markdown(
                f"{sentiment_badge('Caller', caller_line.split(':')[-1].strip())} &nbsp;&nbsp; "
                f"{sentiment_badge('Agent', agent_line.split(':')[-1].strip())}",
                unsafe_allow_html=True
            )

            # --- Talk Listen Ratio ---
            st.markdown("### 🗣️ Talk-Listen Ratio")
            if ratios:
                for item in ratios:
                    st.write(f"**{item.get('caller')}** — Talking Time: {item.get('talkingTime')} sec, Ratio: {item.get('ratio')}%")
            else:
                st.info("No talk-listen ratio data available.")

            # --- Transcript ---
            with st.expander("📜 View Transcript"):
                st.text_area("Transcript", value=transcript, height=300)

        except Exception as e:
            st.error(f"❌ An error occurred: {e}")
