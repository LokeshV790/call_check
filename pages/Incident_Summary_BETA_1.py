import streamlit as st
import pandas as pd
import json
import logging
import time
import google.generativeai as genai

# --- Configure Logging ---
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# --- Configure Gemini ---
GEMINI_API_KEY = st.secrets["GEMINI_API_KEY"]
genai.configure(api_key=GEMINI_API_KEY)
gemini_model = genai.GenerativeModel("gemini-1.5-flash-8b")

# --- Summary Generator Function ---
def get_summary(prompt):
    try:
        with st.spinner("Generating Summary with Gemini... Please wait."):
            response = gemini_model.generate_content(prompt)
            return response.text.strip()
    except Exception as e:
        logging.error(f"Gemini Error: {str(e)}")
        return f"Gemini Error: {str(e)}"

# --- Streamlit App ---
def main():
    st.set_page_config(page_title="ServiceNow Incident Summarizer", layout="wide")
    st.title("Incident Summary Generator")
    st.write("Upload your ServiceNow ticket dataset (CSV file) below.")
    st.warning(
        'Make sure header row contains: "Number", "Short description", "Description", '
        '"Additional comments", "Work notes", "Start time"',
        icon="⚠️"
    )

    uploaded_file = st.file_uploader("Choose a CSV file", type="csv")

    if uploaded_file is not None:
        df = pd.read_csv(uploaded_file)
        st.write("### 🗂 Original Data")
        st.dataframe(df)

        if st.button("Generate Timeline Summaries"):
            total_tickets = len(df)
            summaries = []
            progress_bar = st.progress(0)
            summary_table_placeholder = st.empty()
            processed_tickets = []

            with st.spinner("Processing tickets..."):
                for idx, row in df.iterrows():
                    logging.info(f"Processing ticket {idx+1} of {total_tickets}")

                    prompt = (
                        "You are a professional incident analyst helping a support team understand ServiceNow tickets. "

                        "Follow this exact structure:\n\n"

                        "1️⃣ **Issue:** \n\n"

                        "2️⃣ **Troubleshooting Steps Performed:**  this should be detailed and in points, make bullet points of actions, also make sure you capture every small detail\n\n"

                        "3️⃣ **Most Recent Update:** , you get this from most recent work notes and additional comments as per the latest timestamp.\n\n"

                        "4️⃣ **Actions to be taken or resolution:**  \n\n"

                        "Keep the summary informative. Your tone should be professional and clear.\n\n"
                        "### Incident Data:\n"
                        f"- **Ticket Number:** {row.get('number', 'N/A')}\n"
                        f"- **Short Description:** {row.get('short_description', 'N/A')}\n"
                        f"- **Description:** {row.get('description', 'N/A')}\n"
                        f"**Work Notes:** {row.get('work_notes', 'N/A')}\n"
                        f"**Additional Comments:** {row.get('comments', 'N/A')}\n"
                        f"**State:** {row.get('state', 'N/A')}\n"
                    )

                    summary = get_summary(prompt)
                    summaries.append(summary)
                    processed_tickets.append({"Summary": summary})

                    summary_table_placeholder.table(pd.DataFrame(processed_tickets))
                    progress_bar.progress((idx + 1) / total_tickets)
                    time.sleep(0.5)

            # Add the summary column
            df['Summary'] = summaries

            # Sort by start time if available
            if "Start time" in df.columns:
                try:
                    df['Start time'] = pd.to_datetime(df['Start time'])
                    df.sort_values(by="Start time", ascending=True, inplace=True)
                except Exception as e:
                    logging.error(f"Sorting error: {e}")

            st.write("### ✅ Final Summaries (Chronological Order)")
            st.dataframe(df)

            # Download button
            csv = df.to_csv(index=False).encode('utf-8')
            st.download_button(
                label="📥 Download Final CSV",
                data=csv,
                file_name="ServiceNow_Ticket_Summaries.csv",
                mime="text/csv"
            )

if __name__ == "__main__":
    main()
