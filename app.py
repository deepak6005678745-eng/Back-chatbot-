import os
from flask import Flask, request, jsonify
from google import genai

app = Flask(__name__)

# --- CORS Headers Handle Karne Ke Liye ---
@app.after_request
def add_cors_headers(response):
    response.headers.add('Access-Control-Allow-Origin', '*')
    response.headers.add('Access-Control-Allow-Headers', 'Content-Type,Authorization')
    response.headers.add('Access-Control-Allow-Methods', 'GET,PUT,POST,DELETE,OPTIONS')
    return response

# Naya Gemini API Client Setup
# Agar tumhare paas apni alag key hai toh check kar lena, nahi toh yeh default chalegi
API_KEY = os.getenv("GEMINI_API_KEY")
if not API_KEY:
    raise ValueError("Bhai, GEMINI_API_KEY environment variable set nahi hai!")
    
client = genai.Client(api_key=API_KEY)

# 1. MAIN PAGE ROUTE (Bina templates folder ke HTML load karega)
@app.route('/')
def home():
    try:
        with open('index.html', 'r', encoding='utf-8') as f:
            return f.read()
    except FileNotFoundError:
        return "<h3>Bhai, 'index.html' file nahi mili! Use isi app.py ke sath wale folder mein rakho.</h3>", 404

# 2. CHAT API ROUTE
@app.route('/api/chat', methods=['POST', 'OPTIONS'])
def chat():
    if request.method == 'OPTIONS':
        return jsonify({"status": "ok"}), 200
        
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "Invalid JSON body"}), 400
            
        user_message = data.get("message", "")
        if not user_message:
            return jsonify({"error": "Message is empty"}), 400
            
        # Naye client ke mutabik response generate karna
        response = client.models.generate_content(
            model='gemini-2.5-flash',  # Ekdam naya stable model
            contents=user_message,
        )
        
        bot_reply = response.text
        
        # Agar response sach mein khali aaye toh fallback text
        if not bot_reply:
            bot_reply = "Bhai, Gemini ne response generate nahi kiya, shayed API key block hai."
            
        return jsonify({"reply": bot_reply})
        
    except Exception as e:
        print("Error aaya:", str(e))
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
