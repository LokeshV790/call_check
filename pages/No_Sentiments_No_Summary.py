import streamlit as st
import requests
import base64
import pandas as pd
import datetime
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
            for call in batch:
                call["direction"] = direction  # Tag direction for reference
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
        sentiment = response.json().get("overallSentiment", "")
        return sentiment.capitalize() if sentiment else None
    except:
        return None

# --- Fetch summary ---
def fetch_summary(call_id, user, token):
    url = f"https://api.cloudtalk.io/v1/ai/calls/{call_id}/summary"
    headers = get_auth_header(user, token)
    try:
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        summary = response.json().get("summary", "")
        return summary if summary else None
    except:
        return None

# --- Streamlit UI ---
st.set_page_config(page_title="CloudTalk Missing AI Data", layout="wide")
st.title("CloudTalk Calls Missing Sentiment or Summary")

api_user = st.secrets["CT_API_ID"]
api_token = st.secrets["CT_API_KEY"]

col1, col2 = st.columns(2)
with col1:
    date_from = st.date_input("Start Date", datetime.date.today() - datetime.timedelta(days=1))
with col2:
    date_to = st.date_input("End Date", datetime.date.today())

if st.button("Find Calls with Missing Sentiment or Summary"):
    try:
        st.info("Fetching call list...")
        calls = fetch_calls_all_types(date_from, date_to, api_user, api_token)
        st.success(f"✅ Total calls fetched: {len(calls)}")

        if not calls:
            st.warning("No calls found for the selected date range.")
        else:
            placeholder = st.empty()

            def process_call(call):
                cdr = call.get("Cdr", {})
                agent = call.get("Agent", {})
                call_id = cdr.get("id")
                agent_name = agent.get("fullname", "Unknown")
                direction = call.get("direction", "Unknown")

                sentiment = fetch_sentiment(call_id, api_user, api_token)
                summary = fetch_summary(call_id, api_user, api_token)

                # Include if either is missing or unavailable
                if not sentiment or sentiment.lower() in ["unknown", "not found"] or not summary or summary.lower() == "summary not available.":
                    return {
                        "Call ID": call_id,
                        "Agent Name": agent_name,
                        "Direction": direction,
                        "Sentiment Found": bool(sentiment),
                        "Summary Found": bool(summary)
                    }
                return None

            st.info("Checking for missing sentiment or summary...")

            results = []
            with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
                futures = [executor.submit(process_call, call) for call in calls]
                for idx, future in enumerate(concurrent.futures.as_completed(futures), 1):
                    result = future.result()
                    if result:
                        results.append(result)
                    placeholder.markdown(f"Processed calls: **{idx}/{len(calls)}**")

            placeholder.success("✅ Done processing all calls!")

            if results:
                df = pd.DataFrame(results)
                st.dataframe(df, use_container_width=True)
                csv = df.to_csv(index=False).encode("utf-8")
                st.download_button(
                    label="Download Missing Data CSV",
                    data=csv,
                    file_name=f"missing_sentiment_or_summary_{date_from}_to_{date_to}.csv",
                    mime="text/csv"
                )
                st.success(f"⚠️ Calls with missing sentiment or summary: {len(results)}")
            else:
                st.info("🎉 All calls have sentiment and summary data.")

    except Exception as e:
        st.error(f"Error: {e}")
