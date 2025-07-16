import streamlit as st
import requests
import base64
import pandas as pd
import datetime
import re
import logging
import concurrent.futures

# --- Logging setup ---
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

# --- Fetch sentiment ---
def fetch_sentiment(call_id, user, token):
    url = f"https://api.cloudtalk.io/v1/ai/calls/{call_id}/overall-sentiment"
    headers = get_auth_header(user, token)
    try:
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        return response.json().get("overallSentiment", "Unknown").capitalize()
    except:
        return "Not Found"

# --- Extract incident number from notes ---
def extract_incident(notes):
    if not notes:
        return "Not Found"
    for note_obj in notes:
        match = re.search(r"(INC\w+)", note_obj.get("note", ""))
        if match:
            return match.group(1)
    return "Not Found"

# --- Fetch CloudTalk summary ---
def fetch_summary(call_id, user, token):
    url = f"https://api.cloudtalk.io/v1/ai/calls/{call_id}/summary"
    headers = get_auth_header(user, token)
    try:
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        return response.json().get("summary", "Summary not available.")
    except:
        return "Summary not available."

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
        st.success(f"✅ Total calls fetched: {len(calls)}")

        if not calls:
            st.warning("No calls found.")
        else:
            placeholder = st.empty()

            # --- Parallel fetch sentiments ---
            def get_sentiment_for_call(call):
                cdr = call.get("Cdr", {})
                call_id = cdr.get("id")
                sentiment = fetch_sentiment(call_id, api_user, api_token)
                return call, sentiment

            st.info("Fetching sentiments in parallel...")

            sentiments_results = []
            with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
                futures = [executor.submit(get_sentiment_for_call, call) for call in calls]
                for idx, future in enumerate(concurrent.futures.as_completed(futures), 1):
                    call, sentiment = future.result()
                    sentiments_results.append((call, sentiment))
                    placeholder.markdown(f"Processed sentiments: **{idx}/{len(calls)}**")
                    logging.info(f"Call ID: {call.get('Cdr', {}).get('id')} — Sentiment: {sentiment}")

            # --- Filter negative sentiment calls ---
            negative_calls = [(call, sentiment) for call, sentiment in sentiments_results if sentiment == "Negative"]

            st.success(f"✅ Negative calls found: {len(negative_calls)}")

            # --- Parallel fetch incident & summary for negatives ---
            def process_negative_call(call, sentiment):
                cdr = call.get("Cdr", {})
                agent = call.get("Agent", {})
                notes = call.get("Notes", [])

                call_id = cdr.get("id")
                started_at = cdr.get("started_at", "N/A")
                direction = cdr.get("type", "Unknown")
                agent_name = agent.get("fullname", "Unknown")

                incident_number = extract_incident(notes)
                summary = fetch_summary(call_id, api_user, api_token)

                return {
                    "Call ID": call_id,
                    "Date": started_at,
                    "Agent Name": agent_name,
                    "Direction": direction,
                    "Sentiment": sentiment,
                    "Incident Number": incident_number,
                    "Summary": summary
                }

            st.info("Fetching incident numbers & summaries for negative calls...")

            negative_results = []
            with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
                futures = [executor.submit(process_negative_call, call, sentiment) for call, sentiment in negative_calls]
                for idx, future in enumerate(concurrent.futures.as_completed(futures), 1):
                    negative_results.append(future.result())
                    placeholder.markdown(f"Processed negative calls: **{idx}/{len(negative_calls)}**")

            placeholder.success("✅ Done processing all negative calls!")

            if negative_results:
                df = pd.DataFrame(negative_results)
                st.dataframe(df, use_container_width=True)

                csv = df.to_csv(index=False).encode("utf-8")
                st.download_button("Download Negative Calls CSV", csv, f"negative_calls_{date_from}_to_{date_to}.csv", "text/csv")
            else:
                st.info("No negative sentiment calls found.")

    except Exception as e:
        st.error(f"Error: {e}")
