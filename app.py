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
# Render ke environment variables mein GEMINI_KEY_1 se GEMINI_KEY_5 tak keys daal dena
api_keys = [
    os.getenv("GEMINI_KEY_1"),
    os.getenv("GEMINI_KEY_2"),
    os.getenv("GEMINI_KEY_3"),
    os.getenv("GEMINI_KEY_4"),
    os.getenv("GEMINI_KEY_5"),
    os.getenv("GEMINI_API_KEY") # Backup ke liye agar tumne purani wali key hi rakhi ho
]

# Jo keys active hain (None nahi hain), unhe filter karo
active_keys = [key for key in api_keys if key]

if not active_keys:
    raise ValueError("Bhai, ek bhi GEMINI API KEY environment variable me set nahi hai!")

def get_gemini_client():
    """Har request par randomly ek key chun kar client initialize karega"""
    selected_key = random.choice(active_keys)
    return genai.Client(api_key=selected_key)


# --- Google Auth Client ID ---
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID") 


# --- MongoDB Database Connection Setup ---
MONGO_URI = os.getenv("MONGO_URI")

if not MONGO_URI:
    print("WARNING: MONGO_URI set nahi hai. Database connect nahi ho payega!")
    chats_collection = None
    users_collection = None
else:
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

        # Google library se token verify karo
        idinfo = id_token.verify_oauth2_token(token, google_requests.Request(), GOOGLE_CLIENT_ID)

        user_email = idinfo.get("email")
        user_name = idinfo.get("name")
        user_google_id = idinfo.get("sub") 

        # Database me save/check karo
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
# 2. CHAT API ROUTE (With Real-Time Clock & Memory)
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
        session_id = data.get("sessionId", "default_session") 
        
        if not user_message:
            return jsonify({"error": "Message is empty"}), 400

        # --- MEMORY LOGIC: Database se pichli chat history uthao ---
        formatted_contents = []
        if chats_collection is not None:
            try:
                past_chats = chats_collection.find({"sessionId": session_id}).sort("timestamp", 1)
                for c in past_chats:
                    formatted_contents.append(
                        types.Content(role="user", parts=[types.Part.from_text(text=c["user_msg"])])
                    )
                    formatted_contents.append(
                        types.Content(role="model", parts=[types.Part.from_text(text=c["bot_msg"])])
                    )
            except Exception as e:
                print("History fetch karne me dikkat hui:", str(e))

        # Naya message content list me add karo
        formatted_contents.append(
            types.Content(role="user", parts=[types.Part.from_text(text=user_message)])
        )

        # --- DYNAMIC TIME LOGIC: Live Date, Day aur Time nikalna ---
        try:
            tz = pytz.timezone('Asia/Kolkata')
            current_now = datetime.now(tz)
        except Exception:
            current_now = datetime.now()
            
        current_date = current_now.strftime("%d %B %Y")  
        current_time = current_now.strftime("%I:%M %p")  
        current_day = current_now.strftime("%A")         

        # System Instruction me live time inject kiya
        system_prompt = (
            f"You are a smart AI Assistant with a dark-themed futuristic chat UI. "
            f"Keep responses neat, concise, and helpful. "
            f"CRITICAL CONTEXT: The user's current live time is {current_time}, "
            f"the date is {current_date}, and today is {current_day}. "
            f"Always use this exact information if the user asks about the current time, date, today, or tomorrow."
        )

        config = types.GenerateContentConfig(system_instruction=system_prompt)

        # Dynamic client select karo (Rotation Logic)
        client = get_gemini_client()

        # Gemini Call
        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=formatted_contents,
            config=config
        )
        
        bot_reply = response.text
        if not bot_reply:
            bot_reply = "Bhai, Gemini ne response generate nahi kiya."
            
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
        
    session_id = request.args.get("sessionId", "default_session")
    try:
        chats = chats_collection.find({"sessionId": session_id}).sort("timestamp", 1)
        history = []
        for chat in chats:
            history.append({
                "user": chat.get("user_msg", ""),
                "bot": chat.get("bot_msg", "")
            })
        return jsonify({"history": history})
    except Exception as e:
        print("History nikalne me error:", str(e))
        return jsonify({"error": str(e)}), 500


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
