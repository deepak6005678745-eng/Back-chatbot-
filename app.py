import os
import random
from flask import Flask, request, jsonify
from google import genai
from google.genai import types
from google.oauth2 import id_token
from google.auth.transport import requests as google_requests
from pymongo import MongoClient
from datetime import datetime
import pytz

app = Flask(__name__)

# --- CORS Headers Handle Karne Ke Liye ---
@app.after_request
def add_cors_headers(response):
    response.headers.add('Access-Control-Allow-Origin', '*')
    response.headers.add('Access-Control-Allow-Headers', 'Content-Type,Authorization')
    response.headers.add('Access-Control-Allow-Methods', 'GET,PUT,POST,DELETE,OPTIONS')
    return response

# --- 5x Gemini API Keys Rotation Logic ---
api_keys = [
    os.getenv("GEMINI_KEY_1"),
    os.getenv("GEMINI_KEY_2"),
    os.getenv("GEMINI_KEY_3"),
    os.getenv("GEMINI_KEY_4"),
    os.getenv("GEMINI_KEY_5"),
    os.getenv("GEMINI_API_KEY")
]
active_keys = [key for key in api_keys if key]

if not active_keys:
    raise ValueError("Bhai, ek bhi GEMINI API KEY environment variable me set nahi hai!")

def get_gemini_client():
    selected_key = random.choice(active_keys)
    return genai.Client(api_key=selected_key)

# --- Google Auth Client ID ---
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID") 

# --- MongoDB Database Connection Setup ---
BACKUP_MONGO_URI = "mongodb+srv://deepak6005678745_db_user:DiEcK4b7enZL1c7V@cluster0.yn1lfwq.mongodb.net/chatbot_db?retryWrites=true&w=majority&appName=Cluster0"
MONGO_URI = os.getenv("MONGO_URI", BACKUP_MONGO_URI)

try:
    db_client = MongoClient(MONGO_URI)
    db = db_client['chatbot_db']
    chats_collection = db['user_chats']
    users_collection = db['users'] 
    print("MongoDB se connection ekdum makkhan chal gaya!")
except Exception as e:
    print("Database connect karne me error aaya:", str(e))
    chats_collection = None
    users_collection = None


# =======================================================
# 1. GOOGLE AUTHENTICATION ENDPOINT
# =======================================================
@app.route('/api/auth/google', methods=['POST', 'OPTIONS'])
def google_auth():
    if request.method == 'OPTIONS':
        return jsonify({"status": "ok"}), 200
        
    try:
        data = request.get_json()
        token = data.get("token")

        if not token:
            return jsonify({"error": "Bhai, token missing hai!"}), 400

        if not GOOGLE_CLIENT_ID:
            return jsonify({"error": "Backend me GOOGLE_CLIENT_ID set nahi hai!"}), 500

        idinfo = id_token.verify_oauth2_token(token, google_requests.Request(), GOOGLE_CLIENT_ID)

        user_email = idinfo.get("email")
        user_name = idinfo.get("name")
        user_google_id = idinfo.get("sub") 

        if users_collection is not None:
            user = users_collection.find_one({"google_id": user_google_id})
            if not user:
                user_data = {
                    "google_id": user_google_id,
                    "name": user_name,
                    "email": user_email,
                    "joined_at": datetime.utcnow()
                }
                users_collection.insert_one(user_data)

        return jsonify({
            "success": True, 
            "userId": user_google_id, 
            "name": user_name
        }), 200

    except ValueError:
        return jsonify({"error": "Bhai, fake ya expired token hai!"}), 400
    except Exception as e:
        print("Auth error aaya:", str(e))
        return jsonify({"error": str(e)}), 500


# =======================================================
# 2. CHAT API ROUTE (Fixed Empty Response Format)
# =======================================================
@app.route('/api/chat', methods=['POST', 'OPTIONS'])
def chat():
    if request.method == 'OPTIONS':
        return jsonify({"status": "ok"}), 200
        
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "Invalid JSON body"}), 400
            
        user_message = data.get("message", "")
        session_id = data.get("sessionId") or data.get("userId") or "default_session"
        
        if not user_message:
            return jsonify({"error": "Message is empty"}), 400

        # --- MEMORY LOGIC: Standard dict structure format use karenge ---
        formatted_contents = []
        if chats_collection is not None:
            try:
                # Pichli 10 chats uthao taaki safe rahein
                past_chats = chats_collection.find({"sessionId": session_id}).sort("timestamp", 1).limit(10)
                for c in past_chats:
                    if c.get("user_msg") and c.get("bot_msg"):
                        formatted_contents.append({"role": "user", "parts": [c["user_msg"]]})
                        formatted_contents.append({"role": "model", "parts": [c["bot_msg"]]})
            except Exception as e:
                print("History fetch karne me dikkat hui:", str(e))

        # Naya user message list me daalo
        formatted_contents.append({"role": "user", "parts": [user_message]})

        # --- DYNAMIC TIME LOGIC ---
        try:
            tz = pytz.timezone('Asia/Kolkata')
            current_now = datetime.now(tz)
        except Exception:
            current_now = datetime.now()
            
        current_date = current_now.strftime("%d %B %Y")  
        current_time = current_now.strftime("%I:%M %p")  
        current_day = current_now.strftime("%A")         

        system_prompt = (
            f"You are a smart AI Assistant with a dark-themed futuristic chat UI. "
            f"Keep responses neat, concise, and helpful. "
            f"CRITICAL CONTEXT: The user's current live time is {current_time}, "
            f"the date is {current_date}, and today is {current_day}. "
            f"Always use this exact information if the user asks about the current time, date, today, or tomorrow."
        )

        config = types.GenerateContentConfig(system_instruction=system_prompt)
        client = get_gemini_client()

        # Gemini Call (with standard array configuration)
        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=formatted_contents,
            config=config
        )
        
        bot_reply = response.text
        
        # Safe fallback check agar fir bhi khali aaye
        if not bot_reply or bot_reply.strip() == "":
            bot_reply = "Bhai, main samajh gaya. Batao aage kya help chahiye?"
            
        # --- DATABASE ME SAVE KARNA ---
        if chats_collection is not None:
            try:
                chat_log = {
                    "sessionId": session_id,
                    "user_msg": user_message,
                    "bot_msg": bot_reply,
                    "timestamp": datetime.utcnow()
                }
                chats_collection.insert_one(chat_log)
            except Exception as db_err:
                print("Database me save karne me dikkat aayi:", str(db_err))
            
        return jsonify({"reply": bot_reply})
        
    except Exception as e:
        print("Error aaya:", str(e))
        return jsonify({"error": str(e)}), 500


# =======================================================
# 3. CHAT HISTORY ROUTE (Frontend sidebar ke liye)
# =======================================================
@app.route('/api/history', methods=['GET'])
def get_history():
    if chats_collection is None:
        return jsonify({"history": [], "message": "Database connected nahi hai bhai!"})
        
    session_id = request.args.get("sessionId") or request.args.get("userId") or "default_session"
    
    try:
        chats = chats_collection.find({"sessionId": session_id}).sort("timestamp", 1)
        history = []
        for chat in chats:
            history.append({
                "user": chat.get("user_msg", ""),
                "bot": chat.get("bot_msg", "")
            })
        return jsonify({"history": history}), 200
    except Exception as e:
        print("History nikalne me error:", str(e))
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
