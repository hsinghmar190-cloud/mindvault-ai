# 🧠 MindVault AI 

**A Secure Personal Journal & AI Companion powered by Gemini 3.6-flash**
> **Built for the Gen AI Academy Cohort 3 Ideathon** | **Created by:** Himanshu Verma

---

## ⚠️ Important Note for Evaluators & Judges
This application is fully production-ready and its architecture is specifically designed for **Google Cloud Run**. However, as a 17-year-old student developer, I faced a deployment blocker: the inability to bypass the GCP billing verification step due to the lack of an accepted international credit/debit card. 

Rather than giving up, I have hosted a **100% functional live mirror on Render** so you can seamlessly evaluate the prototype, UI, and backend integration. 

**Live Demo URL:** [https://mindvault-ai-gpnx.onrender.com]

If billing were activated, the exact deployment command with the mandatory Ideathon label used would be:
```bash
gcloud run deploy mindvault-ai \
  --source . \
  --region asia-south1 \
  --allow-unauthenticated \
  --labels dev-tutorial=cloud-run-ai-challenge \
  --set-env-vars GEMINI_API_KEY="YOUR_API_KEY",FIREBASE_CREDENTIALS_PATH="/app/backend/serviceAccountKey.json"
🚀 Project Overview
MindVault AI is not just a standard chatbot; it is a secure, tenant-isolated reflection space. Users can securely log their thoughts, and the AI acts as an empathetic partner that categorizes emotional themes (Positive, Calm, Reflective, Stressed) and provides mindful feedback.

✨ Key Features
Empathetic AI Companion: Powered by the blazing-fast gemini-3.6-flash model, custom-prompted to provide supportive, memory-aware conversations.

Tenant-Isolated Security: Uses Firebase Auth & Firestore. Users can only access their own data via secure token verification.

Complete Privacy Control: A dedicated "Privacy Center" allows users to permanently wipe their entire journal and chat history with a single click.

Premium UI/UX: Features a seamless Dark/Light mode toggle, dynamic typing effects for AI responses, smooth auto-scrolling, and premium typography.

🛠️ Tech Stack
Backend: Python, FastAPI

AI Integration: Google Generative AI (gemini-3.6-flash)

Database & Auth: Google Firebase (Firestore & Firebase Authentication)

Frontend: HTML5, Tailwind CSS, Vanilla JavaScript

💻 Local Setup & Run
Clone the repository: git clone https://github.com/your-username/mindvault-ai.git

Navigate to the backend: cd mindvault-ai/backend

Install dependencies: pip install -r requirements.txt

Run the FastAPI server: uvicorn main:app --reload
