import os
import sys
import json
import datetime
import discord
from discord.ext import commands
import firebase_admin
from firebase_admin import credentials, firestore

# Load dotenv helper
def load_dotenv():
    env_path = os.path.join(os.path.dirname(__file__), "..", ".env")
    if os.path.exists(env_path):
        with open(env_path, "r") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, value = line.split("=", 1)
                    os.environ[key.strip()] = value.strip().strip('"').strip("'")

load_dotenv()

# Firebase Config & Admin Auth initialization
USER_UID = os.environ.get("USER_UID")
FIREBASE_SERVICE_ACCOUNT = os.environ.get("FIREBASE_SERVICE_ACCOUNT")
DISCORD_BOT_TOKEN = os.environ.get("DISCORD_BOT_TOKEN")

if not USER_UID:
    print("Error: USER_UID is missing from environment. Cannot initialize bot.")
    sys.exit(1)

# Check fallback service account file
firebase_key_path = os.path.join(os.path.dirname(__file__), "..", "firebase_key.json")

try:
    if firebase_admin._apps:
        firebase_app = firebase_admin.get_app()
    else:
        if FIREBASE_SERVICE_ACCOUNT:
            print("Initializing Firebase Admin SDK using Service Account from Environment...")
            cred_dict = json.loads(FIREBASE_SERVICE_ACCOUNT)
            cred = credentials.Certificate(cred_dict)
        elif os.path.exists(firebase_key_path):
            print(f"Initializing Firebase Admin SDK using local key file: {firebase_key_path}...")
            cred = credentials.Certificate(firebase_key_path)
        else:
            raise ValueError("No service account configuration found.")
        firebase_app = firebase_admin.initialize_app(cred)
    firestore_db = firestore.client()
    print("Firebase Admin Client successfully initialized in Discord Bot.")
except Exception as e:
    print(f"CRITICAL: Failed to initialize Firebase SDK: {e}")
    sys.exit(1)

# Initialize Discord client commands
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    print(f"--- SCOUT DISCORD BOT ACTIVE ---")
    print(f"Logged in as: {bot.user.name} (ID: {bot.user.id})")
    print(f"Listening for commands...")

@bot.group(name="scout", invoke_without_command=True)
async def scout_group(ctx):
    """Base !scout command. Shows quick help."""
    help_msg = (
        "💻 **Scout AI Assistant Bot**\n"
        "Here are the operations you can trigger:\n"
        "• `!scout status` - Fetch weekly activity check and checklist tasks.\n"
        "• `!scout add \"<company>\" \"<role>\" \"<ctc>\"` - Add a placement drive opportunity.\n"
        "• `!scout done \"<task title>\"` - Mark a checklist task as completed in Firestore.\n"
        "• `!scout help` - Display help menu."
    )
    await ctx.send(help_msg)

@scout_group.command(name="help")
async def scout_help(ctx):
    """Detailed help command."""
    await scout_group(ctx)

@scout_group.command(name="status")
async def scout_status(ctx):
    """Display goals progress and pending tasks."""
    await ctx.send("🔍 Fetching your latest semester plan data from Firestore...")
    try:
        doc_ref = firestore_db.collection("users").document(USER_UID)
        doc_snap = doc_ref.get()
        if not doc_snap.exists:
            await ctx.send("❌ Error: No semester data found in Firestore for your UID.")
            return

        data = doc_snap.to_dict()
        
        # Calculate stats
        active_commitments = len([c for c in data.get("commitments", []) if c.get("category") in ["keep", "watch"]])
        pending_execs = [e for e in data.get("executables", []) if not e.get("done")]
        completed_execs_count = len([e for e in data.get("executables", []) if e.get("done")])
        
        user_config = data.get("config", {})
        leetcode_goal = user_config.get("leetcode_solved_goal", 3)
        github_goal = user_config.get("github_commit_goal", 5)
        
        leetcode_solved = data.get("leetcode_solved_this_week", 0)
        github_commits = data.get("github_commits_this_week", 0)
        
        msg = "📈 **Scout Sync Status & Report**\n"
        msg += f"• **Active Commitments:** {active_commitments}\n"
        msg += f"• **Tasks Done / Total:** {completed_execs_count} / {completed_execs_count + len(pending_execs)}\n"
        
        msg += "\n💻 **Weekly Placement Targets:**\n"
        msg += f"• LeetCode: {leetcode_solved} / {leetcode_goal} problems solved\n"
        msg += f"• GitHub: {github_commits} / {github_goal} commits pushed\n"
        
        if pending_execs:
            msg += "\n📋 **Top Pending Tasks:**\n"
            for e in pending_execs[:3]:
                msg += f"  - *{e.get('title')}* ({e.get('meta')})\n"
        else:
            msg += "\n✅ All clear! No pending executables."
            
        await ctx.send(msg)
    except Exception as e:
        await ctx.send(f"❌ Error loading status: {e}")

@scout_group.command(name="add")
async def scout_add(ctx, company: str, role: str, ctc: str):
    """Add a new placement opportunity drive to Firestore.
    Example: !scout add "Google" "SWE Intern" "40 LPA"
    """
    await ctx.send(f"💾 Adding opportunity: **{company}** ({role}) - {ctc}...")
    try:
        doc_ref = firestore_db.collection("users").document(USER_UID)
        doc_snap = doc_ref.get()
        if not doc_snap.exists:
            await ctx.send("❌ Error: No user document found in Firestore.")
            return

        data = doc_snap.to_dict()
        placements = data.get("placements", [])
        
        new_entry = {
            "id": f"company_{int(datetime.datetime.now().timestamp())}",
            "name": company,
            "title": role,
            "type": "off-campus",
            "ctc": ctc,
            "status": "applied",
            "jd": "",
            "notes": ""
        }
        placements.append(new_entry)
        data["placements"] = placements
        
        doc_ref.set(data)
        await ctx.send(f"✅ Successfully synced! Added **{company}** off-campus drive to your placements board.")
    except Exception as e:
        await ctx.send(f"❌ Error adding placement entry: {e}")

@scout_group.command(name="done")
async def scout_done(ctx, task_title: str):
    """Mark an executable task as completed in Firestore.
    Example: !scout done "ECell exit"
    """
    await ctx.send(f"✓ Updating task status for: '{task_title}'...")
    try:
        doc_ref = firestore_db.collection("users").document(USER_UID)
        doc_snap = doc_ref.get()
        if not doc_snap.exists:
            await ctx.send("❌ Error: No user document found in Firestore.")
            return

        data = doc_snap.to_dict()
        executables = data.get("executables", [])
        
        updated = False
        matched_title = ""
        
        for e in executables:
            # Case insensitive prefix check
            if e.get("title", "").lower().startswith(task_title.lower()):
                e["done"] = True
                updated = True
                matched_title = e.get("title")
                break
                
        if updated:
            data["executables"] = executables
            doc_ref.set(data)
            await ctx.send(f"✅ Task **'{matched_title}'** has been marked as completed in Firestore!")
        else:
            await ctx.send(f"❓ Could not find a pending task starting with '{task_title}'.")
    except Exception as e:
        await ctx.send(f"❌ Error updating task: {e}")

if __name__ == "__main__":
    if not DISCORD_BOT_TOKEN:
        print("Warning: DISCORD_BOT_TOKEN environment variable not set. Please add it to .env to run the bot.")
        sys.exit(1)
    bot.run(DISCORD_BOT_TOKEN)
