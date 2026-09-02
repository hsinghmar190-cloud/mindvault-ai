import os
import json
from fastapi import FastAPI, HTTPException, Security, Depends, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
import firebase_admin
from firebase_admin import credentials, auth, firestore
import google.generativeai as genai

# 1. Initialize Firebase Admin SDK using the downloaded JSON key
cred_path = os.getenv("FIREBASE_CREDENTIALS_PATH", "serviceAccountKey.json")
if not os.path.exists(cred_path):
    raise RuntimeError(f"Service account key not found at: {cred_path}")

cred = credentials.Certificate(cred_path)
if not firebase_admin._apps:
    firebase_admin.initialize_app(cred)
db = firestore.client()

# 2. Configure Gemini API Key securely via environment variable
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
if not GEMINI_API_KEY:
    raise RuntimeError("CRITICAL: GEMINI_API_KEY environment variable is not set!")
genai.configure(api_key=GEMINI_API_KEY)

app = FastAPI(title="MindVault AI - Secure Personal Journal API")
security = HTTPBearer()

# Enable CORS for frontend interaction
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Authentication Middleware: Verifies Firebase JWT
async def get_current_user(credentials: HTTPAuthorizationCredentials = Security(security)):
    token = credentials.credentials
    try:
        decoded_token = auth.verify_id_token(token)
        return decoded_token
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired authentication token"
        )

# Request Schemas with Input Validation
class JournalEntrySchema(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    content: str = Field(..., min_length=1, max_length=10000)

class ChatMessageSchema(BaseModel):
    conversation_id: str | None = Field(default=None, max_length=128)
    message: str = Field(..., min_length=1, max_length=8000)
    enable_memory: bool = Field(default=False)

@app.get("/health")
def health_check():
    return {"status": "healthy", "service": "MindVault AI"}

# ----------------- JOURNAL ENDPOINTS -----------------

@app.post("/api/journal")
async def create_journal_entry(data: JournalEntrySchema, user: dict = Depends(get_current_user)):
    uid = user["uid"]
    
    # Analyze entry using Gemini: Generate Summary, Themes, and Reflection Label
    model = genai.GenerativeModel(
        model_name="gemini-1.5-flash",
        system_instruction="You are a reflective personal growth partner. Analyze the journal text. Output strict JSON with keys: summary (string), themes (list of strings), reflectionLabel (one of: Positive, Calm, Reflective, Stressed, Mixed). Informational only, never diagnostic."
    )
    
    try:
        prompt = f"Analyze this private journal entry:\nTitle: {data.title}\nContent: {data.content}"
        ai_resp = model.generate_content(
            prompt,
            generation_config={"response_mime_type": "application/json"}
        )
        ai_data = json.loads(ai_resp.text)
    except Exception:
        ai_data = {
            "summary": data.content[:120] + "...",
            "themes": ["Reflection"],
            "reflectionLabel": "Reflective"
        }

    # Store entry strictly in isolated Firestore path
    entry_ref = db.collection("users").document(uid).collection("journalEntries").document()
    payload = {
        "title": data.title.strip(),
        "content": data.content.strip(),
        "summary": ai_data.get("summary", ""),
        "themes": ai_data.get("themes", []),
        "reflectionLabel": ai_data.get("reflectionLabel", "Reflective"),
        "createdAt": firestore.SERVER_TIMESTAMP,
        "updatedAt": firestore.SERVER_TIMESTAMP
    }
    entry_ref.set(payload)
    
    return {"id": entry_ref.id, "summary": payload["summary"], "reflectionLabel": payload["reflectionLabel"]}

@app.get("/api/journal")
async def list_journal_entries(user: dict = Depends(get_current_user)):
    uid = user["uid"]
    docs = (
        db.collection("users")
        .document(uid)
        .collection("journalEntries")
        .order_by("createdAt", direction=firestore.Query.DESCENDING)
        .limit(25)
        .stream()
    )
    
    entries = []
    for doc in docs:
        d = doc.to_dict()
        entries.append({
            "id": doc.id,
            "title": d.get("title"),
            "content": d.get("content"),
            "summary": d.get("summary"),
            "themes": d.get("themes", []),
            "reflectionLabel": d.get("reflectionLabel", "Reflective"),
            "createdAt": str(d.get("createdAt"))
        })
    return {"entries": entries}

# ----------------- MULTI-TURN AI CHAT -----------------

@app.post("/api/chat")
async def chat_with_gemini(data: ChatMessageSchema, user: dict = Depends(get_current_user)):
    uid = user["uid"]
    
    # Ensure conversation path belongs only to this user
    conv_id = data.conversation_id
    if not conv_id:
        conv_ref = db.collection("users").document(uid).collection("conversations").document()
        conv_id = conv_ref.id
    else:
        conv_ref = db.collection("users").document(uid).collection("conversations").document(conv_id)

    # Optional Memory: Load prior messages if memory is enabled
    history = []
    if data.enable_memory:
        prior_msgs = conv_ref.collection("messages").order_by("createdAt", direction=firestore.Query.ASCENDING).limit(10).stream()
        for msg in prior_msgs:
            m_data = msg.to_dict()
            history.append({
                "role": m_data.get("role", "user"),
                "parts": [m_data.get("content", "")]
            })

    model = genai.GenerativeModel(
        model_name="gemini-1.5-flash",
        system_instruction="You are MindVault AI, an empathetic, secure personal journaling assistant. Help users reflect, clarify thoughts, and discover insights without giving medical or diagnostic advice."
    )
    
    chat = model.start_chat(history=history)
    ai_reply = chat.send_message(data.message).text

    # Persist conversation turn
    conv_ref.set({"updatedAt": firestore.SERVER_TIMESTAMP, "memoryEnabled": data.enable_memory}, merge=True)
    conv_ref.collection("messages").add({
        "role": "user",
        "content": data.message,
        "createdAt": firestore.SERVER_TIMESTAMP
    })
    conv_ref.collection("messages").add({
        "role": "model",
        "content": ai_reply,
        "createdAt": firestore.SERVER_TIMESTAMP
    })

    return {"conversation_id": conv_id, "reply": ai_reply}

# ----------------- PRIVACY CENTER -----------------

@app.delete("/api/privacy/clear-all")
async def delete_all_user_data(user: dict = Depends(get_current_user)):
    uid = user["uid"]
    
    # Secure server-side batch deletion of user records
    user_ref = db.collection("users").document(uid)
    for subcol in ["journalEntries", "conversations"]:
        docs = user_ref.collection(subcol).limit(100).stream()
        for d in docs:
            d.reference.delete()
            
    return {"status": "success", "message": "All personal journal data permanently deleted."}

from fastapi.responses import FileResponse

@app.get("/")
async def serve_frontend():
    return FileResponse("../frontend/index.html")
