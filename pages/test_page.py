import streamlit as st
import requests
import base64
import json
import time
import random
import logging
import re
import google.generativeai as genai

# --- Setup Gemini ---
GEMINI_API_KEY = st.secrets["GEMINI_API_KEY"]
genai.configure(api_key=GEMINI_API_KEY)
gemini_model = genai.GenerativeModel("gemini-2.0-flash-lite")

# Logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# --- Summary Function Using Gemini ---
def get_summary(prompt, max_retries=5):
    retries = 0
    while retries < max_retries:
        try:
            with st.spinner("Generating Call Summary...Please wait."):
                response = gemini_model.generate_content(prompt)
                return response.text.strip()
        except Exception as e:
            wait = 2 ** retries + random.random()
            logging.warning(f"Gemini error: {e}. Retrying in {wait:.2f} seconds...")
            time.sleep(wait)
            retries += 1
    return "Error: Gemini failed to generate summary."

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

    agent_id = None
    for step in data.get("call_steps", []):
        for agent in step.get("agent_calls", []):
            if agent.get("status") == "answered":
                agent_id = agent.get("id")
                break
        if agent_id:
            break

    incident_number = None
    for note in data.get("notes", []):
        match = re.search(r"INC\d+", note)
        if match:
            incident_number = match.group(0)
            break

    call_date = data.get("date")
    return data, agent_id, incident_number, call_date

# --- Fetch Agent Name ---
def get_agent_name(agent_id, user, token):
    headers = get_auth_header(user, token)
    url = "https://my.cloudtalk.io/api/agents/index.json"

    response = requests.get(url, headers=headers)
    response.raise_for_status()
    agents = response.json().get("responseData", {}).get("data", [])

    for agent_entry in agents:
        agent = agent_entry.get("Agent", {})
        if str(agent.get("id")) == str(agent_id):
            name = f"{agent.get('firstname', '')} {agent.get('lastname', '')}".strip()
            return name or agent.get("name")
    return "Not found"

# --- Fetch Transcript ---
def fetch_transcript(call_id, user, token):
    headers = get_auth_header(user, token)
    url = f"https://api.cloudtalk.io/v1/ai/calls/{call_id}/transcription"

    response = requests.get(url, headers=headers)
    response.raise_for_status()
    segments = response.json()["data"]["segments"]
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

# --- Fetch Talk-Listen Ratio ---
def get_talk_listen_ratio(call_id, user, token):
    headers = get_auth_header(user, token)
    url = f"https://api.cloudtalk.io/v1/ai/calls/{call_id}/talk-listen-ratio"

    response = requests.get(url, headers=headers)
    response.raise_for_status()
    return response.json().get("talkListenRatio", [])

# --- Streamlit UI ---
st.set_page_config(page_title="Call Summary Generator", layout="wide")
st.title("Call Analyzer")

call_id = st.text_input("Enter Call ID")
api_user = st.secrets["CT_API_ID"]
api_token = st.secrets["CT_API_KEY"]

if st.button("Fetch & Summarize Call"):
    if not call_id or not api_user or not api_token:
        st.warning("Please provide all required fields.")
    else:
        try:
            with st.spinner("Fetching Call Info..."):
                data, agent_id, incident_number, call_date = get_call_info(call_id, api_user, api_token)

            with st.spinner("Fetching Agent Name..."):
                agent_name = get_agent_name(agent_id, api_user, api_token)

            with st.spinner("Fetching Transcript..."):
                transcript = fetch_transcript(call_id, api_user, api_token)

            with st.spinner("Generating AI Summary..."):
                prompt = (
                    f"review the below call and give me a feedback for the agent that handled the call and give me the strengths vs weakness or improvement areas"
                    f"Transcript:\n{transcript}"
                )
                summary = get_summary(prompt)

            # with st.spinner("Fetching Sentiment..."):
            #     sentiment = get_sentiment(call_id, api_user, api_token)

            # with st.spinner("Fetching Talk-Listen Ratio..."):
            #     ratios = get_talk_listen_ratio(call_id, api_user, api_token)

            # --- Display Info Cards ---
            st.subheader("Call Details")
            col1, col2 = st.columns(2)

            with col1:
                st.metric("Call ID", call_id)
                st.text_area("Analysis", value=summary, height=400)

            with col2:
                st.subheader("Call Transcript")
                st.text_area("Transcript", value=transcript, height=400)

            # with col3:
            #     st.metric("Sentiment", sentiment.capitalize() if sentiment else "Unavailable")
            #     st.caption("⚠️ Sentiments are not 100% accurate — we are continuously working to improve this.")

            # with col1:
            #     st.metric("Call ID", call_id)
            #     st.metric("Date", call_date.split("T")[0] if call_date else "N/A")

            # with col2:
            #     st.metric("Incident Number", incident_number or "Not found")
            #     st.metric("Agent Name", agent_name or "Not found")

            # with col3:
            #     st.metric("Sentiment", sentiment.capitalize() if sentiment else "Unavailable")
            #     st.caption("⚠️ Sentiments are not 100% accurate — we are continuously working to improve this.")

            # st.divider()

            # st.subheader("Call Summary")
            # st.write(summary)

            # st.divider()

            # st.subheader("Talk-Listen Ratio")
            # for item in ratios:
            #     st.write(f"**{item.get('caller')}** — Talking Time: {item.get('talkingTime')} sec, Ratio: {item.get('ratio')}%")

            # st.divider()

            # st.subheader("Call Transcript")
            # st.text_area("Transcript", value=transcript, height=200)

        except Exception as e:
            st.error(f"❌ An error occurred: {e}")