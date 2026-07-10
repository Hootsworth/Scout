import os
import json
import sys
import datetime
import smtplib
import requests
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import firebase_admin
from firebase_admin import credentials
from firebase_admin import firestore
import markdown

# Helper to load local .env if present
def load_local_env():
    if os.path.exists(".env"):
        print("Loading environment variables from local .env file...")
        with open(".env", "r") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, val = line.split("=", 1)
                    os.environ[key.strip()] = val.strip().strip('"').strip("'")

# Run env loader before anything else
load_local_env()

# Load Environment Secrets
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
BREVO_SMTP_LOGIN = os.environ.get("BREVO_SMTP_LOGIN")      # afdac9001@smtp-brevo.com
BREVO_SMTP_PASSWORD = os.environ.get("BREVO_SMTP_PASSWORD") # Your Brevo SMTP key/password
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET")
GOOGLE_REFRESH_TOKEN = os.environ.get("GOOGLE_REFRESH_TOKEN")
USER_EMAIL = os.environ.get("USER_EMAIL")
USER_UID = os.environ.get("USER_UID")
FIREBASE_SERVICE_ACCOUNT = os.environ.get("FIREBASE_SERVICE_ACCOUNT")

# If FIREBASE_SERVICE_ACCOUNT is missing, but firebase_key.json is available, load it
if not FIREBASE_SERVICE_ACCOUNT and os.path.exists("firebase_key.json"):
    print("FIREBASE_SERVICE_ACCOUNT is empty. Loading credentials from local firebase_key.json...")
    try:
        with open("firebase_key.json", "r") as f:
            FIREBASE_SERVICE_ACCOUNT = f.read().strip()
            os.environ["FIREBASE_SERVICE_ACCOUNT"] = FIREBASE_SERVICE_ACCOUNT
    except Exception as e:
        print(f"Failed to load firebase_key.json: {e}")

# Check required parameters
if not all([GEMINI_API_KEY, BREVO_SMTP_LOGIN, BREVO_SMTP_PASSWORD, GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, GOOGLE_REFRESH_TOKEN, USER_EMAIL, USER_UID, FIREBASE_SERVICE_ACCOUNT]):
    print("Error: Missing required environment variables. Please check GitHub Secrets or local .env file.")
    print("Required: GEMINI_API_KEY, BREVO_SMTP_LOGIN, BREVO_SMTP_PASSWORD, GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, GOOGLE_REFRESH_TOKEN, USER_EMAIL, USER_UID, FIREBASE_SERVICE_ACCOUNT")
    sys.exit(1)

# Initialize Firebase Admin SDK
try:
    service_account_info = json.loads(FIREBASE_SERVICE_ACCOUNT)
    cred = credentials.Certificate(service_account_info)
    firebase_admin.initialize_app(cred)
    firestore_db = firestore.client()
    print("Firebase Admin SDK initialized successfully.")
except Exception as e:
    print(f"Failed to initialize Firebase Admin SDK: {e}")
    sys.exit(1)

# Helper: Refresh Google OAuth Token
def get_google_access_token():
    print("Refreshing Google OAuth access token...")
    url = "https://oauth2.googleapis.com/token"
    payload = {
        "client_id": GOOGLE_CLIENT_ID,
        "client_secret": GOOGLE_CLIENT_SECRET,
        "refresh_token": GOOGLE_REFRESH_TOKEN,
        "grant_type": "refresh_token"
    }
    res = requests.post(url, data=payload)
    if res.status_code != 200:
        print(f"Error refreshing Google Token: {res.text}")
        sys.exit(1)
    return res.json().get("access_token")

# Helper: Fetch recent Gmail messages (last 24 hours or 2 days)
def fetch_gmail_messages(access_token, query="classroom", days=2):
    print(f"Searching Gmail for messages matching '{query}' over the last {days} days...")
    search_url = "https://www.googleapis.com/gmail/v1/users/me/messages"
    headers = {"Authorization": f"Bearer {access_token}"}
    params = {
        "q": f"{query} newer_than:{days}d",
        "maxResults": 15
    }
    res = requests.get(search_url, headers=headers, params=params)
    if res.status_code != 200:
        print(f"Gmail search failed: {res.text}")
        return []
    
    data = res.json()
    messages = data.get("messages", [])
    print(f"Found {len(messages)} matching email(s).")
    
    detailed_messages = []
    for msg in messages:
        detail_url = f"https://www.googleapis.com/gmail/v1/users/me/messages/{msg['id']}?format=minimal"
        detail_res = requests.get(detail_url, headers=headers)
        if detail_res.status_code == 200:
            m_data = detail_res.json()
            detailed_messages.append({
                "id": m_data.get("id"),
                "snippet": m_data.get("snippet", "")
            })
    return detailed_messages

# Helper: Analyze emails with Gemini and extract structured calendar events
def analyze_emails_with_gemini(emails):
    if not emails:
        return []
    
    print("Analyzing email snippets with Gemini AI...")
    model = "gemini-2.5-flash"
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={GEMINI_API_KEY}"
    
    prompt = f"""
You are an automated academic scanner. Analyze the following list of email snippets to identify any new academic deadlines, class announcements, CIE (Internal Assessment) exam schedules, project presentation meetings, or homework submissions.

Emails:
{json.dumps(emails, indent=2)}

For each academic deadline or event, extract the details and return a JSON array containing:
- "title": Event name (e.g., 'NLP Project Presentation')
- "description": Event details (e.g., 'Google Classroom submission deadline')
- "start_time": Estimated ISO start datetime in UTC (e.g., '2026-07-12T15:00:00Z')
- "end_time": Estimated ISO end datetime in UTC (e.g., '2026-07-12T16:00:00Z')

If no academic deadlines or events are found in these emails, return an empty array [].
Your output must be strict JSON inside the response (no markdown syntax, just the JSON string).
"""
    
    headers = {"Content-Type": "application/json"}
    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "temperature": 0.1
        }
    }
    
    res = requests.post(url, headers=headers, json=body)
    if res.status_code != 200:
        print(f"Gemini API analysis failed: {res.text}")
        return []
    
    try:
        content_text = res.json()["candidates"][0]["content"]["parts"][0]["text"]
        events = json.loads(content_text)
        print(f"Gemini extracted {len(events)} event(s) from emails.")
        return events
    except Exception as e:
        print(f"Failed to parse Gemini response: {e}")
        return []

# Helper: Check if event exists on Calendar
def check_event_exists(access_token, title, start_time):
    url = "https://www.googleapis.com/calendar/v3/calendars/primary/events"
    headers = {"Authorization": f"Bearer {access_token}"}
    
    dt = datetime.datetime.fromisoformat(start_time.replace("Z", "+00:00"))
    start_day = (dt - datetime.timedelta(hours=12)).isoformat()
    end_day = (dt + datetime.timedelta(hours=12)).isoformat()
    
    params = {
        "timeMin": start_day,
        "timeMax": end_day,
        "singleEvents": True
    }
    res = requests.get(url, headers=headers, params=params)
    if res.status_code == 200:
        events = res.json().get("items", [])
        for e in events:
            if e.get("summary", "").lower() == title.lower():
                return True
    return False

# Helper: Create Google Calendar Event
def create_calendar_event(access_token, event):
    url = "https://www.googleapis.com/calendar/v3/calendars/primary/events"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json"
    }
    body = {
        "summary": event["title"],
        "description": event["description"],
        "start": {"dateTime": event["start_time"]},
        "end": {"dateTime": event["end_time"]}
    }
    res = requests.post(url, headers=headers, json=body)
    if res.status_code == 200:
        print(f"Successfully added event: '{event['title']}' to Google Calendar.")
        return True
    else:
        print(f"Failed to add event: {res.text}")
        return False

# Helper: Send Email via Brevo SMTP
def send_brevo_email(subject, html_content):
    print("Sending update email via Brevo SMTP...")
    try:
        msg = MIMEMultipart('alternative')
        msg['Subject'] = subject
        msg['From'] = "Scout <noreply@singulr.tech>"
        msg['To'] = USER_EMAIL
        msg.attach(MIMEText(html_content, 'html'))

        with smtplib.SMTP('smtp-relay.brevo.com', 587) as server:
            server.ehlo()
            server.starttls()
            server.login(BREVO_SMTP_LOGIN, BREVO_SMTP_PASSWORD)
            server.sendmail('noreply@singulr.tech', USER_EMAIL, msg.as_string())
        print("Weekly status email sent successfully!")
    except Exception as e:
        print(f"Failed to send email via SMTP: {e}")

# Generate Weekly Review HTML
def generate_weekly_report(semester_data, new_events):
    print("Generating weekly review report text...")
    
    pre_sem_weeks = semester_data.get("pre_semester", [])
    upcoming_tasks = []
    completed_tasks = []
    
    for w in pre_sem_weeks:
        for t in w.get("tasks", []):
            if t.get("done"):
                completed_tasks.append(t["text"])
            else:
                upcoming_tasks.append(f"{w['title']}: {t['text']}")
                
    executables = semester_data.get("executables", [])
    pending_execs = [e["title"] for e in executables if not e.get("done")]
    done_execs = [e["title"] for e in executables if e.get("done")]

    # Prompt Gemini for report summary
    model = "gemini-2.5-flash"
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={GEMINI_API_KEY}"
    
    ai_prompt = f"""
Generate a supportive, direct, and structured academic coaching review for the upcoming week based on this database state:

Completed executables:
{json.dumps(done_execs, indent=2)}

Pending executables:
{json.dumps(pending_execs, indent=2)}

Upcoming tasks in pre-semester schedule:
{json.dumps(upcoming_tasks, indent=2)}

Newly parsed classroom/calendar events:
{json.dumps(new_events, indent=2)}

Keep it professional, action-oriented, and write in the style of their operations plan: direct, realistic, focusing on reducing switching costs and building baseline habits. You can use tables, lists, headers, blockquotes, code blocks, and bold text. Output your response in clear Markdown.
"""
    
    ai_summary = "No AI analysis could be generated."
    headers = {"Content-Type": "application/json"}
    body = {
        "contents": [{"parts": [{"text": ai_prompt}]}]
    }
    res = requests.post(url, headers=headers, json=body)
    if res.status_code == 200:
        try:
            ai_summary = res.json()["candidates"][0]["content"]["parts"][0]["text"]
        except Exception as e:
            print("Failed to extract AI summary text:", e)
            
    # Convert ai_summary from Markdown to HTML
    ai_summary_html = "<p>No AI analysis could be generated.</p>"
    if ai_summary:
        try:
            ai_summary_html = markdown.markdown(ai_summary, extensions=['tables', 'fenced_code'])
        except Exception as e:
            print("Failed to convert markdown to html:", e)
            ai_summary_html = ai_summary.replace("\n", "<br>").replace("**", "<strong>")

    # Compile Lists
    new_events_html = ""
    if new_events:
        new_events_html = "<ul>" + "".join([f"<li><b>{e['title']}</b>: {e['description']} (Start: {e['start_time']})</li>" for e in new_events]) + "</ul>"
    else:
        new_events_html = "<p>No new academic events synced from Gmail this week.</p>"

    pending_execs_html = ""
    if pending_execs:
        pending_execs_html = "<ul>" + "".join([f"<li>{e}</li>" for e in pending_execs]) + "</ul>"
    else:
        pending_execs_html = "<p>All key executables checked off! Great job.</p>"

    done_execs_html = ""
    if done_execs:
        done_execs_html = "<ul>" + "".join([f"<li>{e}</li>" for e in done_execs]) + "</ul>"
    else:
        done_execs_html = "<p>Get started on some key actions to build momentum.</p>"

    html = f"""
    <html>
    <head>
      <style>
        body {{ font-family: sans-serif; background-color: #f5f2eb; color: #3d3a33; padding: 20px; }}
        .container {{ background-color: #faf8f3; border: 1px solid rgba(26,24,20,0.1); padding: 30px; max-width: 600px; margin: 0 auto; }}
        h1 {{ font-family: 'Syne', sans-serif; color: #c13d2e; text-transform: uppercase; border-bottom: 2px solid #c13d2e; padding-bottom: 10px; }}
        h2 {{ color: #2a6b5e; margin-top: 25px; }}
        .badge {{ background: #ede9e0; padding: 4px 8px; font-size: 11px; font-family: monospace; border: 1px solid #e5e0d4; display: inline-block; margin-bottom: 8px; }}
        ul {{ padding-left: 20px; }}
        li {{ margin-bottom: 8px; line-height: 1.5; }}
        .ai-coaching {{ border-left: 3px solid #c47d1a; padding-left: 15px; margin: 20px 0; font-style: italic; background: rgba(196,125,26,0.03); padding-top: 10px; padding-bottom: 10px; }}
        
        /* Markdown rendering inside email styling */
        .ai-coaching h1, .ai-coaching h2, .ai-coaching h3, .ai-coaching h4 {{ font-style: normal; color: #1a1814; font-family: sans-serif; margin-top: 16px; margin-bottom: 8px; }}
        .ai-coaching h3 {{ font-size: 16px; }}
        .ai-coaching h4 {{ font-size: 14px; }}
        .ai-coaching p {{ margin-bottom: 12px; }}
        .ai-coaching table {{ border-collapse: collapse; width: 100%; margin: 16px 0; font-size: 13px; font-style: normal; }}
        .ai-coaching th, .ai-coaching td {{ border: 1px solid rgba(26,24,20,0.15); padding: 8px 12px; text-align: left; }}
        .ai-coaching th {{ background-color: rgba(26,24,20,0.05); font-weight: bold; }}
        .ai-coaching tr:nth-child(even) {{ background-color: rgba(26,24,20,0.02); }}
        .ai-coaching pre {{ background: #ede9e0; padding: 10px; border-radius: 4px; overflow-x: auto; font-style: normal; }}
        .ai-coaching code {{ font-family: monospace; font-size: 12px; background: #ede9e0; padding: 2px 4px; border-radius: 2px; font-style: normal; }}
        .ai-coaching pre code {{ padding: 0; background: transparent; }}
        .ai-coaching blockquote {{ border-left: 3px solid #c13d2e; padding-left: 12px; margin: 12px 0; color: #7a7670; font-style: italic; }}
      </style>
    </head>
    <body>
      <div class="container">
        <h1>Scout Weekly Review</h1>
        <div class="badge">TIMESTAMP: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</div>
        
        <div class="ai-coaching">
          <h2>AI Planner Recommendation</h2>
          {ai_summary_html}
        </div>
        
        <h2>Newly Synced Calendar Deadlines</h2>
        {new_events_html}

        <h2>Pending Executables Checklist</h2>
        {pending_execs_html}

        <h2>Completed Tasks</h2>
        {done_execs_html}
        
        <hr style="border: 0; border-top: 1px solid rgba(26,24,20,0.1); margin-top: 30px;">
        <p style="font-size: 11px; color: #7a7670; text-align: center;">Sent by Scout via Brevo SMTP.</p>
      </div>
    </body>
    </html>
    """
    return html

def main():
    print("--- STARTING SCOUT BATCH CRON (FIRESTORE) ---")
    
    # 1. Fetch User Data Document from Firestore
    print(f"Fetching Firestore plan document for UID: {USER_UID}...")
    doc_ref = firestore_db.collection("users").document(USER_UID)
    doc_snap = doc_ref.get()
    
    if not doc_snap.exists:
        print(f"Error: User plan document for UID '{USER_UID}' does not exist in Firestore. Please initialize it on the dashboard first.")
        sys.exit(1)
        
    semester_data = doc_snap.to_dict()
    print("Firestore plan document successfully loaded.")
        
    # 2. Get Access Token
    google_token = get_google_access_token()
    
    # 3. Fetch recent emails (past 2 days)
    emails = fetch_gmail_messages(google_token, query="classroom", days=2)
    emails += fetch_gmail_messages(google_token, query="exam OR presentation OR CIE", days=2)
    
    # Deduplicate emails by ID
    unique_emails = {e["id"]: e for e in emails}.values()
    
    # 4. Use Gemini to parse
    extracted_events = analyze_emails_with_gemini(list(unique_emails))
    
    # 5. Insert events into Google Calendar
    newly_added = []
    for event in extracted_events:
        try:
            exists = check_event_exists(google_token, event["title"], event["start_time"])
            if not exists:
                success = create_calendar_event(google_token, event)
                if success:
                    newly_added.append(event)
            else:
                print(f"Event '{event['title']}' already exists on calendar. Skipping.")
        except Exception as ex:
            print(f"Error processing event {event.get('title')}: {ex}")

    # 6. Generate weekly planning email report
    report_html = generate_weekly_report(semester_data, newly_added)
    send_brevo_email("Scout: Plan Update & Synced Deadlines", report_html)
    
    # 7. Write run log back to Firestore
    semester_data["last_cron_run"] = datetime.datetime.now().isoformat()
    doc_ref.set(semester_data)
    print("Firestore user document updated with run timestamps.")
    print("--- CRON RUN COMPLETED SUCCESSFULLY ---")

if __name__ == "__main__":
    main()
