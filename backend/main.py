import os
import json
import base64
import textwrap
from fastapi import FastAPI, HTTPException, Security, Depends, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
import firebase_admin
from firebase_admin import credentials, auth, firestore
import google.generativeai as genai

# 1. READ CREDENTIALS
base64_cred = os.getenv("FIREBASE_CREDENTIALS_BASE64")
cred_path = os.getenv("FIREBASE_CREDENTIALS_PATH", "/etc/secrets/serviceAccountKey.json")

cred_dict = None
if base64_cred:
    try:
        cred_dict = json.loads(base64.b64decode(base64_cred).decode('utf-8'))
    except Exception:
        pass

if not cred_dict and os.path.exists(cred_path):
    with open(cred_path, 'r') as f:
        cred_dict = json.load(f)
        
if not cred_dict and os.path.exists("serviceAccountKey.json"):
    with open("serviceAccountKey.json", 'r') as f:
        cred_dict = json.load(f)

if not cred_dict:
    raise RuntimeError("CRITICAL: Firebase credentials not found!")

# 🚨 MAGIC FIX: AUTO-HEAL JWT SIGNATURE 🚨
# यह हिस्सा किसी भी टूटी हुई चाबी को रिपेयर करके परफेक्ट PEM फॉर्मेट में बदल देगा
pk = cred_dict.get("private_key", "")
pk = pk.replace("\\n", "\n").replace("-----BEGIN PRIVATE KEY-----", "").replace("-----END PRIVATE KEY-----", "").replace(" ", "").replace("\n", "")
if pk:
    cred_dict["private_key"] = "-----BEGIN PRIVATE KEY-----\n" + "\n".join(textwrap.wrap(pk, 64)) + "\n-----END PRIVATE KEY-----\n"

cred = credentials.Certificate(cred_dict)
if not firebase_admin._apps:
    firebase_admin.initialize_app(cred)
db = firestore.client()

# 2. Configure Gemini API Key
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
genai.configure(api_key=GEMINI_API_KEY)

app = FastAPI(title="MindVault AI")
security = HTTPBearer()

app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

async def get_current_user(credentials: HTTPAuthorizationCredentials = Security(security)):
    try:
        return auth.verify_id_token(credentials.credentials)
    except Exception:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

class JournalEntrySchema(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    content: str = Field(..., min_length=1, max_length=10000)

class ChatMessageSchema(BaseModel):
    conversation_id: str | None = Field(default=None, max_length=128)
    message: str = Field(..., min_length=1, max_length=8000)
    enable_memory: bool = Field(default=False)

@app.get("/")
async def serve_frontend():
    return FileResponse("../frontend/index.html")

# ----------------- JOURNAL ENDPOINTS -----------------
@app.post("/api/journal")
async def create_journal_entry(data: JournalEntrySchema, user: dict = Depends(get_current_user)):
    uid = user["uid"]
    model = genai.GenerativeModel("gemini-1.5-flash")
    system_prompt = (
        "You are a reflective personal growth partner. Analyze the journal text.\n"
        "Return ONLY a JSON object with keys:\n"
        '\"summary\": a short summary string,\n'
        '\"themes\": list of topic strings,\n'
        '\"reflectionLabel\": one of [\"Positive\", \"Calm\", \"Reflective\", \"Stressed\", \"Mixed\"].\n'
    )
    try:
        full_prompt = f"{system_prompt}Journal Title: {data.title}\nContent: {data.content}"
        resp = model.generate_content(full_prompt).text.strip()
        if resp.startswith("```json"): resp = resp[7:-3].strip()
        elif resp.startswith("```"): resp = resp[3:-3].strip()
        ai_data = json.loads(resp)
    except Exception:
        ai_data = {"summary": data.content[:120] + "...", "themes": ["Reflection"], "reflectionLabel": "Reflective"}

    entry_ref = db.collection("users").document(uid).collection("journalEntries").document()
    payload = {
        "title": data.title.strip(), "content": data.content.strip(),
        "summary": ai_data.get("summary", ""), "themes": ai_data.get("themes", []),
        "reflectionLabel": ai_data.get("reflectionLabel", "Reflective"),
        "createdAt": firestore.SERVER_TIMESTAMP, "updatedAt": firestore.SERVER_TIMESTAMP
    }
    entry_ref.set(payload)
    return {"id": entry_ref.id, "summary": payload["summary"], "reflectionLabel": payload["reflectionLabel"]}

@app.get("/api/journal")
async def list_journal_entries(user: dict = Depends(get_current_user)):
    uid = user["uid"]
    docs = db.collection("users").document(uid).collection("journalEntries").order_by("createdAt", direction=firestore.Query.DESCENDING).limit(25).stream()
    entries = []
    for doc in docs:
        d = doc.to_dict()
        entries.append({
            "id": doc.id, "title": d.get("title"), "content": d.get("content"),
            "summary": d.get("summary"), "themes": d.get("themes", []),
            "reflectionLabel": d.get("reflectionLabel", "Reflective"), "createdAt": str(d.get("createdAt"))
        })
    return {"entries": entries}

# ----------------- CHAT ENDPOINTS -----------------
@app.post("/api/chat")
async def chat_with_gemini(data: ChatMessageSchema, user: dict = Depends(get_current_user)):
    uid = user["uid"]
    conv_id = data.conversation_id
    if not conv_id:
        conv_ref = db.collection("users").document(uid).collection("conversations").document()
        conv_id = conv_ref.id
    else:
        conv_ref = db.collection("users").document(uid).collection("conversations").document(conv_id)

    history = []
    if data.enable_memory:
        prior_msgs = conv_ref.collection("messages").order_by("createdAt", direction=firestore.Query.ASCENDING).limit(10).stream()
        for msg in prior_msgs:
            m_data = msg.to_dict()
            history.append({"role": m_data.get("role", "user"), "parts": [m_data.get("content", "")]})

    model = genai.GenerativeModel("gemini-1.5-flash")
    chat = model.start_chat(history=history)
    ai_reply = chat.send_message(f"Assistant Persona: You are MindVault AI.\nUser: {data.message}").text

    conv_ref.set({"updatedAt": firestore.SERVER_TIMESTAMP, "memoryEnabled": data.enable_memory}, merge=True)
    conv_ref.collection("messages").add({"role": "user", "content": data.message, "createdAt": firestore.SERVER_TIMESTAMP})
    conv_ref.collection("messages").add({"role": "model", "content": ai_reply, "createdAt": firestore.SERVER_TIMESTAMP})

    return {"conversation_id": conv_id, "reply": ai_reply}

# ----------------- PRIVACY CENTER -----------------
@app.delete("/api/privacy/clear-all")
async def delete_all_user_data(user: dict = Depends(get_current_user)):
    user_ref = db.collection("users").document(user["uid"])
    for subcol in ["journalEntries", "conversations"]:
        for d in user_ref.collection(subcol).limit(100).stream():
            d.reference.delete()
    return {"status": "success", "message": "All data deleted."}
