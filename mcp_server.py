import os
import sys
import json
import datetime
import subprocess
import re
import time
import firebase_admin
from firebase_admin import credentials, firestore
from mcp.server.fastmcp import FastMCP

# Initialize FastMCP Server
mcp = FastMCP("Scout")

db_client = None

def load_local_env():
    """Load local .env if it exists in the workspace root."""
    if os.path.exists(".env"):
        print("Loading environment variables from local .env file...")
        with open(".env", "r") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, val = line.split("=", 1)
                    os.environ[key.strip()] = val.strip().strip('"').strip("'")

def init_firebase():
    """Initialize Firebase Firestore client using local key or environment variables."""
    global db_client
    if db_client is not None:
        return db_client

    load_local_env()

    # Determine credentials source
    firebase_sa = os.environ.get("FIREBASE_SERVICE_ACCOUNT")
    
    # If not set, look for local firebase_key.json
    if not firebase_sa and os.path.exists("firebase_key.json"):
        print("FIREBASE_SERVICE_ACCOUNT not set. Loading from firebase_key.json...")
        try:
            with open("firebase_key.json", "r") as f:
                firebase_sa = f.read().strip()
                os.environ["FIREBASE_SERVICE_ACCOUNT"] = firebase_sa
        except Exception as e:
            print(f"Failed to read local firebase_key.json: {e}")

    if not firebase_sa:
        raise Exception(
            "Firebase credentials missing. Please set the FIREBASE_SERVICE_ACCOUNT environment variable "
            "or place the 'firebase_key.json' credentials file in the project folder."
        )

    try:
        service_account_info = json.loads(firebase_sa)
        cred = credentials.Certificate(service_account_info)
        firebase_admin.initialize_app(cred)
        db_client = firestore.client()
        print("Firebase Admin SDK initialized successfully for MCP Server.")
        return db_client
    except Exception as e:
        raise Exception(f"Failed to initialize Firebase Admin SDK: {e}")

# --- TOOLS ---

@mcp.tool()
def get_student_plan() -> str:
    """Fetch the student's entire academic operations plan, including commitments, academics, executables, and pre-semester schedule, from Firestore."""
    try:
        db = init_firebase()
        uid = os.environ.get("USER_UID")
        if not uid:
            return "Error: USER_UID environment variable is not set. Please set it in your local .env file."

        print(f"MCP Tool: Fetching plan document for UID '{uid}'...")
        doc_ref = db.collection("users").document(uid)
        doc_snap = doc_ref.get()

        if not doc_snap.exists:
            return f"Error: No user document found for UID '{uid}' in Firestore. Please initialize it on the Scout dashboard first."

        return json.dumps(doc_snap.to_dict(), indent=2)
    except Exception as e:
        return f"Error occurred while fetching user plan: {str(e)}"

@mcp.tool()
def update_student_plan(plan_json: str) -> str:
    """Overwrite the full student plan document in Firestore with a new JSON string representation."""
    try:
        db = init_firebase()
        uid = os.environ.get("USER_UID")
        if not uid:
            return "Error: USER_UID environment variable is not set."

        try:
            new_data = json.loads(plan_json)
        except Exception as e:
            return f"Error: Invalid JSON representation: {e}"

        print(f"MCP Tool: Overwriting plan document for UID '{uid}'...")
        doc_ref = db.collection("users").document(uid)
        new_data["last_updated"] = datetime.datetime.now().isoformat()
        doc_ref.set(new_data)
        return "Successfully updated student plan in Firestore."
    except Exception as e:
        return f"Error updating student plan: {str(e)}"

@mcp.tool()
def toggle_task_done(task_id: str, done: bool) -> str:
    """Toggle a task as completed (done=True) or pending (done=False) in Firestore. This applies to both pre_semester tasks and executables."""
    try:
        db = init_firebase()
        uid = os.environ.get("USER_UID")
        if not uid:
            return "Error: USER_UID environment variable is not set."

        doc_ref = db.collection("users").document(uid)
        doc_snap = doc_ref.get()
        if not doc_snap.exists:
            return "Error: User plan document does not exist."

        data = doc_snap.to_dict()
        updated = False

        # 1. Look in executables
        for item in data.get("executables", []):
            if item.get("id") == task_id:
                item["done"] = done
                updated = True
                print(f"MCP Tool: Toggled executable task '{task_id}' done={done}")
                break

        # 2. Look in pre_semester schedules
        if not updated:
            for week in data.get("pre_semester", []):
                for task in week.get("tasks", []):
                    if task.get("id") == task_id:
                        task["done"] = done
                        updated = True
                        print(f"MCP Tool: Toggled pre_semester task '{task_id}' done={done}")
                        break
                if updated:
                    break

        if updated:
            data["last_updated"] = datetime.datetime.now().isoformat()
            doc_ref.set(data)
            return f"Task '{task_id}' updated successfully to done={done}."
        else:
            return f"Error: Task with ID '{task_id}' could not be found in executables or pre-semester tasks."
    except Exception as e:
        return f"Error toggling task: {str(e)}"

@mcp.tool()
def add_commitment(title: str, meta: str, category: str, description: str) -> str:
    """Add a new commitment (e.g. clubs like Poethra, AWS, FOSS, or work/internships like Library, CARA) to the student's plan in Firestore."""
    try:
        db = init_firebase()
        uid = os.environ.get("USER_UID")
        if not uid:
            return "Error: USER_UID environment variable is not set."

        doc_ref = db.collection("users").document(uid)
        doc_snap = doc_ref.get()
        if not doc_snap.exists:
            return "Error: User plan document does not exist."

        data = doc_snap.to_dict()
        commitments = data.get("commitments", [])

        # Generate custom id
        clean_id = re.sub(r'[^a-z0-9]', '_', title.lower().strip())
        if any(c.get("id") == clean_id for c in commitments):
            clean_id = f"{clean_id}_{int(time.time())}"

        new_commitment = {
            "id": clean_id,
            "title": title,
            "meta": meta,
            "category": category,
            "description": description
        }

        commitments.append(new_commitment)
        data["commitments"] = commitments
        data["last_updated"] = datetime.datetime.now().isoformat()
        
        doc_ref.set(data)
        print(f"MCP Tool: Added commitment '{title}' with ID '{clean_id}'")
        return f"Successfully added commitment '{title}' (ID: {clean_id})."
    except Exception as e:
        return f"Error adding commitment: {str(e)}"

@mcp.tool()
def sync_academic_assistant() -> str:
    """Run the Scout synchronization cron job to check Gmail for classroom/exam announcements, extract calendar events using Gemini, add them to Google Calendar, and send a summary email report via Brevo SMTP."""
    try:
        load_local_env()
        print("MCP Tool: Spawning academic_cron.py subprocess...")
        # Execute the python sync script
        result = subprocess.run(
            [sys.executable, "scripts/academic_cron.py"],
            capture_output=True,
            text=True
        )
        if result.returncode == 0:
            return f"Sync run completed successfully!\n\nOutput Log:\n{result.stdout}"
        else:
            return f"Sync failed (Exit Code {result.returncode}).\n\nStderr:\n{result.stderr}\n\nStdout:\n{result.stdout}"
    except Exception as e:
        return f"Exception occurred while launching sync job: {str(e)}"

@mcp.tool()
def get_classroom_assignments() -> str:
    """Fetch the list of Google Classroom courses, assignments, statuses, and grades from Firestore."""
    try:
        db = init_firebase()
        uid = os.environ.get("USER_UID")
        if not uid:
            return "Error: USER_UID environment variable is not set."
            
        doc_ref = db.collection("users").document(uid)
        doc_snap = doc_ref.get()
        if not doc_snap.exists:
            return "Error: User plan document does not exist."
            
        data = doc_snap.to_dict()
        assignments = data.get("classroom_assignments", [])
        return json.dumps(assignments, indent=2)
    except Exception as e:
        return f"Error fetching Classroom assignments: {str(e)}"

@mcp.tool()
def get_placements() -> str:
    """Fetch the list of on-campus and off-campus placements, applications, statuses, CTC, and notes."""
    try:
        db = init_firebase()
        uid = os.environ.get("USER_UID")
        if not uid:
            return "Error: USER_UID environment variable is not set."
            
        doc_ref = db.collection("users").document(uid)
        doc_snap = doc_ref.get()
        if not doc_snap.exists:
            return "Error: User plan document does not exist."
            
        data = doc_snap.to_dict()
        placements = data.get("placements", [])
        return json.dumps(placements, indent=2)
    except Exception as e:
        return f"Error fetching placements: {str(e)}"

@mcp.tool()
def add_placement_opportunity(company: str, role: str, ctc: str, status: str = "applied", jd: str = "", notes: str = "") -> str:
    """Add a new placement opportunity or job application (on-campus or off-campus) to the tracker in Firestore."""
    try:
        db = init_firebase()
        uid = os.environ.get("USER_UID")
        if not uid:
            return "Error: USER_UID environment variable is not set."
            
        doc_ref = db.collection("users").document(uid)
        doc_snap = doc_ref.get()
        if not doc_snap.exists:
            return "Error: User plan document does not exist."
            
        data = doc_snap.to_dict()
        placements = data.get("placements", [])
        
        new_entry = {
            "id": f"company_{int(time.time())}",
            "name": company,
            "title": role,
            "type": "off-campus",
            "ctc": ctc,
            "status": status,
            "jd": jd,
            "notes": notes
        }
        placements.append(new_entry)
        data["placements"] = placements
        data["last_updated"] = datetime.datetime.now().isoformat()
        
        doc_ref.set(data)
        print(f"MCP Tool: Added placement opportunity '{company}'")
        return f"Successfully added placement opportunity for {company} ({role}) with CTC {ctc}."
    except Exception as e:
        return f"Error adding placement opportunity: {str(e)}"

@mcp.tool()
def update_placement_status(company_id: str, new_status: str) -> str:
    """Update the status of a placement application in Firestore (e.g. to technical, hr, offer, rejected)."""
    try:
        db = init_firebase()
        uid = os.environ.get("USER_UID")
        if not uid:
            return "Error: USER_UID environment variable is not set."
            
        doc_ref = db.collection("users").document(uid)
        doc_snap = doc_ref.get()
        if not doc_snap.exists:
            return "Error: User plan document does not exist."
            
        data = doc_snap.to_dict()
        placements = data.get("placements", [])
        
        updated = False
        for p in placements:
            if p.get("id") == company_id:
                p["status"] = new_status
                updated = True
                break
                
        if updated:
            data["placements"] = placements
            data["last_updated"] = datetime.datetime.now().isoformat()
            doc_ref.set(data)
            return f"Successfully updated application '{company_id}' status to '{new_status}'."
        else:
            return f"Error: Application with ID '{company_id}' could not be found."
    except Exception as e:
        return f"Error updating placement status: {str(e)}"

if __name__ == "__main__":
    # Standard MCP initialization through stdin/stdout transport
    mcp.run()
