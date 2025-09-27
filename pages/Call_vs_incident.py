import re
import pandas as pd
import streamlit as st
import requests
import base64
import tempfile
import os
import time
import numpy as np
import google.generativeai as genai
from sklearn.metrics.pairwise import cosine_similarity

# ---------------- Streamlit Config ----------------
st.set_page_config(page_title="Incident vs Call Checker", layout="wide")
st.title("📞 Incident vs Call Comparison")

# ---------------- Secrets ----------------
GEMINI_API_KEY = st.secrets["GEMINI_API_KEY"]
SNOW_BASE_URL = st.secrets["SNOW_BASE_URL"]
CT_USER = st.secrets["CT_API_ID"]
CT_TOKEN = st.secrets["CT_API_KEY"]

# ---------------- Gemini Setup ----------------
genai.configure(api_key=GEMINI_API_KEY)
gemini_model = genai.GenerativeModel("gemini-2.0-flash-lite")

# ---------------- Helpers ----------------
def summarize_large_text(text, chunk_size=8000):
    """Break long text into chunks and summarize each to avoid Gemini hanging."""
    chunks = [text[i:i+chunk_size] for i in range(0, len(text), chunk_size)]
    summaries = []
    for idx, chunk in enumerate(chunks, start=1):
        with st.spinner(f"Summarizing chunk {idx}/{len(chunks)}..."):
            try:
                resp = gemini_model.generate_content(
                    f"Summarize this conversation into short bullet points (max 1 sentence each):\n{chunk}"
                )
                if resp and resp.text:
                    summaries.append(resp.text.strip())
            except Exception as e:
                st.warning(f"Gemini chunk {idx} failed: {e}")
    return "\n".join(summaries)

def embed_text_list(text_list):
    """Generate embeddings for a list of texts using Gemini."""
    embeddings = []
    for t in text_list:
        try:
            r = genai.embed_content(
                model="models/text-embedding-004",
                content=t
            )
            embeddings.append(r["embedding"])
        except Exception as e:
            st.warning(f"Embedding failed for: {t[:30]}... — {e}")
            embeddings.append([0]*768)
    return np.array(embeddings)

def compare_with_embeddings(work_points, call_points, threshold=0.75):
    """Compare points semantically using cosine similarity of embeddings."""
    work_emb = embed_text_list(work_points)
    call_emb = embed_text_list(call_points)

    matches, extras, missing = [], [], []

    sim_matrix = cosine_similarity(work_emb, call_emb)

    for i, w_point in enumerate(work_points):
        if sim_matrix[i].max() >= threshold:
            matches.append(w_point)
        else:
            extras.append(w_point)

    for j, c_point in enumerate(call_points):
        if sim_matrix[:, j].max() < threshold:
            missing.append(c_point)

    score = (len(matches) / max(len(call_points), 1)) * 100
    return matches, extras, missing, round(score, 1)

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
    for _ in range(20):
        response = session.get(poll_url)
        if response.status_code == 200:
            match = re.search(r'"sys_id":"([a-f0-9]+)"', response.text)
            if match:
                return match.group(1)
        time.sleep(2)
    raise Exception("Failed to extract sys_id.")

def download_csv(session, sys_id):
    download_url = f"{SNOW_BASE_URL}/sys_report_template.do?CSV&jvar_report_id={sys_id}"
    response = session.get(download_url)
    if response.status_code == 200 and response.text.strip():
        return response.text
    raise Exception("CSV download failed or returned empty.")

def get_auth_header(user, token):
    auth_str = f"{user}:{token}"
    auth_b64 = base64.b64encode(auth_str.encode("utf-8")).decode("utf-8")
    return {"Authorization": f"Basic {auth_b64}"}

def fetch_transcript(call_id, user, token):
    headers = get_auth_header(user, token)
    url = f"https://api.cloudtalk.io/v1/ai/calls/{call_id}/transcription"
    response = requests.get(url, headers=headers)
    response.raise_for_status()
    data = response.json()
    if "data" in data and "segments" in data["data"]:
        segments = data["data"]["segments"]
        return "\n".join([f"{s['caller']}: {s['text']}" for s in segments])
    raise Exception("Transcript format unexpected.")

def split_worknotes_by_timestamp(work_notes_text):
    pattern = r"(?=\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} - )"
    entries = re.split(pattern, work_notes_text)
    return [e.strip() for e in entries if e.strip()]

# ---------------- UI ----------------
incident_number = st.text_input("Incident Number", placeholder="e.g., INC1234567").strip()
cookies_file = st.file_uploader("Upload cookies.txt from SNOW login", type="txt")

if incident_number and cookies_file:
    try:
        cookies = parse_cookies(cookies_file)
        session = requests.Session()
        session.cookies.update(cookies)

        export_url = f"{SNOW_BASE_URL}/incident_list.do?CSV"
        payload = {"CSV": "", "sysparm_query": f"number={incident_number}", "sysparm_target": "incident"}

        response = session.post(export_url, data=payload, allow_redirects=False)
        if response.status_code == 302 and "poll_redirect" in response.headers.get("Location", ""):
            poll_url = SNOW_BASE_URL + response.headers["Location"]
            sys_id = poll_export_status(session, poll_url)
            csv_data = download_csv(session, sys_id)
        elif response.status_code == 200 and "text/csv" in response.headers.get("Content-Type", ""):
            csv_data = response.text
        else:
            st.error("Unexpected response from SNOW.")
            st.stop()

        with tempfile.NamedTemporaryFile(delete=False, suffix=".csv") as f:
            f.write(csv_data.encode("utf-8"))
            temp_csv_path = f.name
        df = pd.read_csv(temp_csv_path)
        os.remove(temp_csv_path)

        if df.empty:
            st.warning("No incident found.")
            st.stop()

        possible_cols = ["work_notes", "Work notes", "Work Notes", "Notes", "comments", "Additional comments"]
        work_notes_text = ""
        for col in possible_cols:
            if col in df.columns:
                work_notes_text = str(df.iloc[0][col])
                break

        if not work_notes_text or work_notes_text.lower() == "nan":
            st.warning("No work notes text found in the CSV.")
            st.stop()

        call_ids = list(set(re.findall(r"\b\d{6,}\b", work_notes_text)))
        if not call_ids:
            st.warning("No Call IDs found in work notes.")
            st.stop()

        selected_call_id = st.selectbox("Select Call ID", call_ids)

        if selected_call_id:
            try:
                transcript = fetch_transcript(selected_call_id, CT_USER, CT_TOKEN)

                st.subheader("📜 Call Transcript")
                st.text_area("Transcript", transcript, height=300)

                chunks = split_worknotes_by_timestamp(work_notes_text)
                relevant_chunks = [c for c in chunks if selected_call_id in c]
                relevant_work_notes = "\n\n".join(relevant_chunks)

                if not relevant_chunks:
                    st.warning("No work notes found containing this Call ID.")
                    st.stop()

                st.subheader("📝 Work Notes for This Call")
                st.text_area("Work Notes", relevant_work_notes, height=300)

                # Summarize and split into points
                call_summary = summarize_large_text(transcript)
                call_points = [p.strip("-• ").strip() for p in call_summary.split("\n") if p.strip()]
                work_points = [line.strip() for line in relevant_work_notes.split("\n") if line.strip()]

                matches, extras, missing, score = compare_with_embeddings(work_points, call_points)

                st.markdown(f"**Match Score:** {score}%")
                col1, col2 = st.columns(2)
                with col1:
                    st.markdown("### ✅ Matches")
                    for m in matches: st.write(f"- {m}")
                    st.markdown("### ❌ Extra in Work Notes")
                    for e in extras: st.write(f"- {e}")
                with col2:
                    st.markdown("### ❌ Missing from Work Notes")
                    for m in missing: st.write(f"- {m}")

            except Exception as e:
                st.error(f"Error processing Call {selected_call_id}: {e}")

    except Exception as e:
        st.error(f"❌ {str(e)}")
