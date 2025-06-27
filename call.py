import streamlit as st
import requests
import base64
import json
import time
import random
import logging
import google.generativeai as genai

# --- Setup Gemini ---
GEMINI_API_KEY = st.secrets["GEMINI_API_KEY"] 
genai.configure(api_key=GEMINI_API_KEY)
gemini_model = genai.GenerativeModel("gemini-1.5-flash-8b")

# Logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# --- Summary Function Using Gemini ---
def get_summary(prompt, max_retries=5):
    retries = 0
    while retries < max_retries:
        try:
            with st.spinner("Generating Summary (Gemini)... Please wait."):
                response = gemini_model.generate_content(prompt)
                return response.text.strip()
        except Exception as e:
            wait = 2 ** retries + random.random()
            logging.warning(f"Gemini error: {e}. Retrying in {wait:.2f} seconds...")
            time.sleep(wait)
            retries += 1
    return "Error: Gemini failed to generate summary."

# --- CloudTalk Transcript Fetcher ---
def fetch_transcript(call_id, api_user, api_token):
    url = f"https://api.cloudtalk.io/v1/ai/calls/{call_id}/transcription"
    auth_str = f"{api_user}:{api_token}"
    auth_bytes = auth_str.encode('utf-8')
    auth_header = base64.b64encode(auth_bytes).decode('utf-8')

    headers = {
        "Authorization": f"Basic {auth_header}"
    }

    try:
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        segments = response.json()["data"]["segments"]
        transcript = "\n".join([f"{s['caller']}: {s['text']}" for s in segments])
        return transcript
    except Exception as e:
        st.error(f"Error fetching transcript: {e}")
        return None

# --- Streamlit UI ---
st.set_page_config(page_title="Call Summary Generator", layout="wide")

# Streamlit UI
st.title("Call Summarizer")

call_id = st.text_input("Enter Call ID")
api_user = st.secrets["CT_API_ID"] 
api_token = st.secrets["CT_API_KEY"] 

if st.button("Fetch & Summarize Call"):
    if not call_id or not api_user or not api_token:
        st.warning("Please provide all required fields.")
    else:
        transcript = fetch_transcript(call_id, api_user, api_token)
        if transcript:
            st.subheader("Call Transcript")
            st.text_area("Full Transcript", value=transcript, height=300)

            prompt = f"Summarize the following customer support call in a structured format. Include:\n1. Problem\n2. Actions Taken\n3. Resolution or Next Steps\n\nTranscript:\n{transcript}"
            summary = get_summary(prompt)
            st.subheader("AI-Generated Summary")
            st.write(summary)


