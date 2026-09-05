import os
import json
import base64
from fastapi import FastAPI, HTTPException, Security, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
import firebase_admin
from firebase_admin import credentials, auth, firestore
import google.generativeai as genai

# 1. Initialize Firebase
base64_cred = os.getenv("FIREBASE_CREDENTIALS_BASE64")
cred_path = os.getenv("FIREBASE_CREDENTIALS_PATH", "/etc/secrets/serviceAccountKey.json")

if base64_cred:
    cred_dict = json.loads(base64.b64decode(base64_cred).decode('utf-8'))
    cred = credentials.Certificate(cred_dict)
elif os.path.exists(cred_path):
    cred = credentials.Certificate(cred_path)
elif os.path.exists("serviceAccountKey.json"):
    cred = credentials.Certificate("serviceAccountKey.json")
else:
    raise RuntimeError("CRITICAL: Firebase credentials not found!")

if not firebase_admin._apps:
    firebase_admin.initialize_app(cred)
db = firestore.client()

# 2. Configure Gemini
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
genai.configure(api_key=GEMINI_API_KEY)

app = FastAPI(title="MindVault AI")
security = HTTPBearer()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

async def get_current_user(credentials: HTTPAuthorizationCredentials = Security(security)):
    try:
        return auth.verify_id_token(credentials.credentials)
    except:
        raise HTTPException(status_code=401, detail="Invalid token")

class JournalEntrySchema(BaseModel):
    title: str = Field(..., min_length=1)
    content: str = Field(..., min_length=1)

class ChatMessageSchema(BaseModel):
    conversation_id: str | None = Field(default=None, max_length=128)
    message: str = Field(..., min_length=1, max_length=8000)
    enable_memory: bool = Field(default=False)

@app.get("/")
async def serve_frontend():
    return FileResponse("../frontend/index.html")

# --- JOURNAL ENDPOINTS ---
@app.post("/api/journal")
async def create_journal_entry(data: JournalEntrySchema, user: dict = Depends(get_current_user)):
    uid = user["uid"]
    system_prompt = "Return JSON with keys: summary, themes, reflectionLabel (Positive, Calm, Reflective, Stressed, Mixed)."
    
    try:
        model = genai.GenerativeModel("gemini-3.6-flash")
        resp = model.generate_content(f"{system_prompt}\nTitle: {data.title}\nContent: {data.content}").text.strip()
        if resp.startswith("```json"): resp = resp[7:-3].strip()
        elif resp.startswith("```"): resp = resp[3:-3].strip()
        ai_data = json.loads(resp)
    except Exception as e:
        print(f"Journal AI Error: {e}")
        ai_data = {"summary": f"Fallback Saved. Error: {str(e)[:50]}", "themes": ["Reflection"], "reflectionLabel": "Reflective"}

    entry_ref = db.collection("users").document(uid).collection("journalEntries").document()
    payload = {"title": data.title, "content": data.content, "summary": ai_data.get("summary", ""), "reflectionLabel": ai_data.get("reflectionLabel", "Reflective"), "createdAt": firestore.SERVER_TIMESTAMP}
    entry_ref.set(payload)
    return {"id": entry_ref.id, "summary": payload["summary"], "reflectionLabel": payload["reflectionLabel"]}

@app.get("/api/journal")
async def list_journal_entries(user: dict = Depends(get_current_user)):
    docs = db.collection("users").document(user["uid"]).collection("journalEntries").order_by("createdAt", direction=firestore.Query.DESCENDING).limit(25).stream()
    return {"entries": [{"id": doc.id, **doc.to_dict(), "createdAt": str(doc.to_dict().get("createdAt"))} for doc in docs]}

# --- CHAT ENDPOINTS ---
@app.post("/api/chat")
async def chat_with_gemini(data: ChatMessageSchema, user: dict = Depends(get_current_user)):
    system_instruction = "You are MindVault AI, a very empathetic, supportive, and motivating journaling companion. You were proudly created by Himanshu Verma."
    try:
        model = genai.GenerativeModel("gemini-3.6-flash")
        ai_reply = model.generate_content(f"{system_instruction}\n\nUser says: {data.message}").text
    except Exception as e:
        ai_reply = f"API Error: {str(e)}"
    
    return {"reply": ai_reply}

# --- PRIVACY ENDPOINT ---
@app.delete("/api/privacy/clear-all")
async def delete_all_user_data(user: dict = Depends(get_current_user)):
    uid = user["uid"]
    user_ref = db.collection("users").document(uid)
    for subcol in ["journalEntries", "conversations"]:
        docs = user_ref.collection(subcol).limit(100).stream()
        for d in docs:
            d.reference.delete()
    return {"status": "success"}
