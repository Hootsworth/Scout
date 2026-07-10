import os
import json
import sys
import datetime
import requests
import firebase_admin
from firebase_admin import credentials
from firebase_admin import firestore

# Load Environment Secrets
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
BREVO_API_KEY = os.environ.get("BREVO_API_KEY")
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET")
GOOGLE_REFRESH_TOKEN = os.environ.get("GOOGLE_REFRESH_TOKEN")
USER_EMAIL = os.environ.get("USER_EMAIL")
USER_UID = os.environ.get("USER_UID")
FIREBASE_SERVICE_ACCOUNT = os.environ.get("FIREBASE_SERVICE_ACCOUNT")

# Check required parameters
if not all([GEMINI_API_KEY, BREVO_API_KEY, GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, GOOGLE_REFRESH_TOKEN, USER_EMAIL, USER_UID, FIREBASE_SERVICE_ACCOUNT]):
    print("Error: Missing required environment variables. Please check GitHub Secrets.")
    print("Required: GEMINI_API_KEY, BREVO_API_KEY, GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, GOOGLE_REFRESH_TOKEN, USER_EMAIL, USER_UID, FIREBASE_SERVICE_ACCOUNT")
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

# Helper: Send Email via Brevo
def send_brevo_email(subject, html_content):
    print("Sending update email via Brevo...")
    url = "https://api.brevo.com/v3/smtp/email"
    headers = {
        "api-key": BREVO_API_KEY,
        "Content-Type": "application/json"
    }
    body = {
        "sender": {"name": "AI Academic Assistant", "email": "noreply@singulr.tech"},
        "to": [{"email": USER_EMAIL}],
        "subject": subject,
        "htmlContent": html_content
    }
    res = requests.post(url, headers=headers, json=body)
    if res.status_code == 201:
        print("Weekly status email sent successfully!")
    else:
        print(f"Failed to send email: {res.text}")

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

Keep it professional, action-oriented, and write in the style of their operations plan: direct, realistic, focusing on reducing switching costs and building baseline habits. Output it in rich HTML suitable for an email (use simple tags like <h3>, <p>, <ul>, <li>).
"""
    
    ai_summary = "<p>No AI analysis could be generated.</p>"
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
      </style>
    </head>
    <body>
      <div class="container">
        <h1>Academic Assistant Weekly Review</h1>
        <div class="badge">TIMESTAMP: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</div>
        
        <div class="ai-coaching">
          <h2>AI Planner Recommendation</h2>
          {ai_summary}
        </div>
        
        <h2>Newly Synced Calendar Deadlines</h2>
        {new_events_html}

        <h2>Pending Executables Checklist</h2>
        {pending_execs_html}

        <h2>Completed Tasks</h2>
        {done_execs_html}
        
        <hr style="border: 0; border-top: 1px solid rgba(26,24,20,0.1); margin-top: 30px;">
        <p style="font-size: 11px; color: #7a7670; text-align: center;">Sent by your Semester 5 Operations Assistant via Brevo SMTP.</p>
      </div>
    </body>
    </html>
    """
    return html

def main():
    print("--- STARTING ACADEMIC ASSISTANT BATCH CRON (FIRESTORE) ---")
    
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
    send_brevo_email("Academic Assistant: Plan Update & Synced Deadlines", report_html)
    
    # 7. Write run log back to Firestore
    semester_data["last_cron_run"] = datetime.datetime.now().isoformat()
    doc_ref.set(semester_data)
    print("Firestore user document updated with run timestamps.")
    print("--- CRON RUN COMPLETED SUCCESSFULLY ---")

if __name__ == "__main__":
    main()
