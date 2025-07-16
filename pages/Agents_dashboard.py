import streamlit as st
import requests
import base64
import pandas as pd
import datetime
from collections import defaultdict
import concurrent.futures

# --- Auth helper ---
def get_auth_header(user, token):
    auth_str = f"{user}:{token}"
    auth_bytes = auth_str.encode("utf-8")
    auth_b64 = base64.b64encode(auth_bytes).decode("utf-8")
    return {"Authorization": f"Basic {auth_b64}"}

# --- Fetch calls with pagination ---
def fetch_calls(date_from, date_to, user, token, status="answered"):
    headers = get_auth_header(user, token)
    all_calls = []
    page = 1
    limit = 1000

    while True:
        url = (
            f"https://my.cloudtalk.io/api/calls/index.json?"
            f"date_from={date_from}&date_to={date_to}&status={status}&limit={limit}&page={page}"
        )
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        data = response.json().get("responseData", {})
        calls = data.get("data", [])
        all_calls.extend(calls)

        page_count = data.get("pageCount", 1)
        if page >= page_count:
            break
        page += 1

    return all_calls

# --- Fetch sentiment safely ---
def get_sentiment(call_id, user, token):
    headers = get_auth_header(user, token)
    url = f"https://api.cloudtalk.io/v1/ai/calls/{call_id}/overall-sentiment"
    try:
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        return response.json().get("overallSentiment", "unknown"), "OK"
    except requests.exceptions.RequestException:
        return "unknown", "Not found"

# --- Parallel fetch sentiments ---
def fetch_all_sentiments(call_ids, user, token):
    results = {}

    def fetch_single(call_id):
        sentiment, status = get_sentiment(call_id, user, token)
        return call_id, sentiment, status

    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(fetch_single, cid) for cid in call_ids]
        for future in concurrent.futures.as_completed(futures):
            cid, sentiment, status = future.result()
            results[cid] = (sentiment, status)

    return results

# --- Streamlit UI ---
st.set_page_config(page_title="Agent Call Summary Dashboard", layout="wide")
st.title("Agent Call Summary Dashboard")

api_user = st.secrets["CT_API_ID"]
api_token = st.secrets["CT_API_KEY"]

col1, col2 = st.columns(2)
with col1:
    date_from = st.date_input("Start Date", datetime.date.today() - datetime.timedelta(days=7))
with col2:
    date_to = st.date_input("End Date", datetime.date.today())

if "generated" not in st.session_state:
    st.session_state.generated = False

if st.button("Generate Dashboard"):
    try:
        st.info("Fetching calls... Please wait, this may take some time if many calls exist.")
        calls = fetch_calls(date_from, date_to, api_user, api_token)

        if not calls:
            st.warning("No calls found for this range.")
        else:
            call_ids = []
            call_info_map = {}

            for call in calls:
                cdr = call.get("Cdr", {})
                agent = call.get("Agent", {})
                call_id = cdr.get("id")
                started_at = cdr.get("started_at")

                if not call_id or not agent or not started_at:
                    continue

                call_date = started_at.split("T")[0]
                agent_name = agent.get("fullname", "Unknown")

                call_ids.append(call_id)
                call_info_map[call_id] = {
                    "date": call_date,
                    "agent_name": agent_name
                }

            st.info("Fetching sentiments in parallel...")

            sentiment_results = fetch_all_sentiments(call_ids, api_user, api_token)

            daily_summary = defaultdict(lambda: defaultdict(lambda: {"total": 0, "positive": 0, "negative": 0, "neutral": 0, "statuses": []}))
            sentiment_not_found_calls = []

            for cid in call_ids:
                info = call_info_map[cid]
                call_date = info["date"]
                agent_name = info["agent_name"]

                sentiment, status = sentiment_results.get(cid, ("unknown", "Not found"))
                sentiment = sentiment.lower()

                daily_summary[call_date][agent_name]["total"] += 1
                daily_summary[call_date][agent_name]["statuses"].append(status)

                if status == "Not found":
                    sentiment_not_found_calls.append({
                        "Call ID": cid,
                        "Agent Name": agent_name,
                        "Date": call_date
                    })

                if sentiment == "positive":
                    daily_summary[call_date][agent_name]["positive"] += 1
                elif sentiment == "negative":
                    daily_summary[call_date][agent_name]["negative"] += 1
                else:
                    daily_summary[call_date][agent_name]["neutral"] += 1

            rows = []
            top_agents_per_day = {}

            for date, agents in daily_summary.items():
                agents_sorted = sorted(agents.items(), key=lambda x: x[1]["positive"], reverse=True)
                top_agent = agents_sorted[0][0] if agents_sorted else "None"
                top_agents_per_day[date] = top_agent

                for agent_name, data in agents.items():
                    statuses = ", ".join(set(data["statuses"]))
                    rows.append({
                        "Date": date,
                        "Agent Name": agent_name,
                        "Total Calls": data["total"],
                        "Positive": data["positive"],
                        "Negative": data["negative"],
                        "Neutral": data["neutral"],
                        # "Sentiment Status": statuses,
                        "Top Agent (this Day)": "Yes" if agent_name == top_agent else ""
                    })

            df = pd.DataFrame(rows)
            df = df.sort_values(by=["Date", "Positive"], ascending=[True, False])
            not_found_df = pd.DataFrame(sentiment_not_found_calls)
            top_agents_df = pd.DataFrame([{"Date": k, "Top Agent": v} for k, v in top_agents_per_day.items()])

            st.session_state.df = df
            st.session_state.not_found_df = not_found_df
            st.session_state.top_agents_df = top_agents_df
            st.session_state.generated = True

            st.success("Dashboard generated successfully!")

    except Exception as e:
        st.error(f"Error: {e}")

if st.session_state.generated:
    st.markdown("## Summary Table")
    st.dataframe(st.session_state.df, use_container_width=True)

    csv = st.session_state.df.to_csv(index=False).encode("utf-8")
    st.download_button("Download CSV", csv, "daily_agent_summary.csv", "text/csv")

    st.markdown("## Top Agent by Date")
    st.dataframe(st.session_state.top_agents_df, use_container_width=True)

    if not st.session_state.not_found_df.empty:
        st.markdown("## Calls with Sentiment Not Found")
        st.dataframe(st.session_state.not_found_df, use_container_width=True)

        csv_nf = st.session_state.not_found_df.to_csv(index=False).encode("utf-8")
        st.download_button("Download Missing Sentiments CSV", csv_nf, "sentiment_not_found_calls.csv", "text/csv")

    st.caption("Sentiment marked as 'Not found' means no sentiment data was available for that call.")
