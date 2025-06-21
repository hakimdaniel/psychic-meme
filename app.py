from flask import Flask, request
import os, re, tempfile, subprocess
import requests
import logging
from datetime import datetime

app = Flask(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN") or "PASTE_TOKEN_HERE"
BASE_URL = f"https://api.telegram.org/bot{BOT_TOKEN}"

sessions = {}

# ==========================
#  UTILITY FUNCTIONS
# ==========================

def send_message(chat_id, text, thread_id=None):
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode":"Markdown"
    }
    if thread_id:
        payload["message_thread_id"] = thread_id
    requests.post(f"{BASE_URL}/sendMessage", json=payload)

def extract_input_prompt(code):
    pattern = r'\b(\w+)\s*=\s*input\s*\(\s*(?:([\'"])(.*?)\2)?\s*\)'
    for line in code.splitlines():
        match = re.match(pattern, line.strip())
        if match:
            var_name = match.group(1)
            prompt = match.group(3) or f"Enter value for {var_name}:"
            return var_name, prompt
    return None, None

def is_number(val):
    try:
        float(val)
        return True
    except:
        return False

def escape_quotes(s):
    return s.replace('"', r'\"')

def replace_input(code, var_name, user_value):
    lines = code.splitlines()
    pattern = rf'^({re.escape(var_name)})\s*=\s*input\s*\(\s*(?:([\'"]).*?\2)?\s*\)'
    new_lines = []
    for line in lines:
        if re.match(pattern, line.strip()):
            if is_number(user_value):
                new_line = f"{var_name} = {user_value}"
            else:
                escaped = escape_quotes(user_value)
                new_line = f'{var_name} = "{escaped}"'
            new_lines.append(new_line)
        elif 'input' in line:
            continue
        else:
            new_lines.append(line)
    return "\n".join(new_lines)

import re

def is_safe_code(code):
    forbidden_patterns = [
        # 🚫 Import libraries berbahaya
        r'\bimport\s+(os|sys|subprocess|shutil|requests|http|urllib|ftplib|socket|pickle|threading|asyncio|platform|psutil|importlib|multiprocessing|flask)\b',
        r'\bfrom\s+(os|sys|subprocess|shutil|requests|http|urllib|ftplib|socket|pickle|threading|asyncio|platform|psutil|importlib|multiprocessing|flask)\b',
        r'__\w+__',
        r'\beval\s*\(',
        r'\bexec\s*\(',
        r'\bopen\s*\(',
        r'\bcompile\s*\(',
        r'\bexit\s*\(',
        r'\bquit\s*\(',
        r'\bglobals\s*\(',
        r'\bvars\s*\(',
        r'\bgetattr\s*\(',
        r'\bfrom\s+os\b',
        r'\bfrom\s+subprocess\b',
        r'\bfrom\s+sys\b',
        r'\bfrom\s+shutil\b',
    ]
    for pattern in forbidden_patterns:
        if re.search(pattern, code):
            return False
    return True

def run_code(code):
    with tempfile.NamedTemporaryFile(mode='w+', suffix='.py', delete=False) as temp:
        temp.write(code)
        temp.flush()
        try:
            result = subprocess.run(["python3", temp.name], capture_output=True, timeout=5, text=True)
            output = result.stdout or result.stderr
            if len(output) > 4000:
                return output[:4000] + "\n... Output truncated."
            return output
        except Exception as e:
            return f"❌ Error: {str(e)}"
        finally:
            os.unlink(temp.name)

# ==========================
#  POLICY TEXT
# ==========================

policy = (
    "=======================\n"
    " BOT USAGE POLICY\n"
    "=======================\n"
    "• This bot provides basic Python features like print, len, input, etc.\n"
    "• Libraries allowed: math, random, hashlib, base64.\n"
    "• Dangerous operations like os, subprocess, eval are blocked.\n"
    "• Do not spam or misuse the bot.\n"
    "• Admin may block users who abuse the system.\n"
    "\n\nTip: [shift]+[enter] to go to a newline without send a message."
)

def save_log(chat_id, username, ip):
    filename = "test/access.log"
    os.makedirs(os.path.dirname(filename), exist_ok=True)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(filename, "a") as f:
        f.write(f"[{now}] ip: {ip}, chat_id: {chat_id}, username: {username}\n")

# ==========================
#  MAIN ROUTE
# ==========================

@app.route("/", methods=["POST"])
def webhook():
    data = request.json
    if "message" in data:
        message = data["message"]
        chat_id = message["chat"]["id"]
        thread_id = message.get("message_thread_id")  # topic ID jika ada
        chat_type = message["chat"]["type"]

        # ✅ Debug: semak ID mesej yang masuk
        print("DEBUG >> chat_id:", chat_id)
        print("DEBUG >> thread_id:", thread_id)
        print("DEBUG >> chat_type:", chat_type)

        # ✅ Hanya benarkan jika PM atau topic group tertentu
        is_private = chat_type == "private"
        is_allowed_group_topic = chat_id == 2391643285 and thread_id == 33

        if not (is_private or is_allowed_group_topic):
            return "Not allowed", 200

        # ✅ Teruskan proses
        text = message.get("text", "").strip()
        username = message.get("from", {}).get("username", "unknown")
        ip = request.headers.get("X-Forwarded-For", request.remote_addr)
        save_log(chat_id, username, ip)

        if text == "/start":
            send_message(chat_id, "Welcome 🥳\nPlease read /policy before using this bot.", thread_id)
        elif text == "/about":
            send_message(chat_id, "Hi! I'm Bot Ak1m, created by **Daniel Hakim**.\nContact @d4n13lh4k1m for feedback or issues.", thread_id)
        elif text == "/help":
            send_message(chat_id, "Send Python code starting with /run or directly.\nSupports input() and basic Python features.", thread_id)
        elif text == "/policy":
            send_message(chat_id, policy, thread_id)
        elif text == "/cancel":
            if chat_id in sessions:
                sessions.pop(chat_id)
                send_message(chat_id, "✅ Session cancelled.", thread_id)
            else:
                send_message(chat_id, "No active session to cancel.", thread_id)
            return "ok"

        elif chat_id in sessions and sessions[chat_id].get('state') == 'wait_input':
            session = sessions[chat_id]
            var = session['var']
            code = replace_input(session['code'], var, text)

            next_var, next_prompt = extract_input_prompt(code)
            if next_var:
                sessions[chat_id] = {'code': code, 'state': 'wait_input', 'var': next_var}
                send_message(chat_id, next_prompt, thread_id)
            else:
                if not is_safe_code(code):
                    send_message(chat_id, "⚠️ Final code contains unsafe functions.", thread_id)
                    sessions.pop(chat_id)
                    return "ok"
                output = run_code(code)
                send_message(chat_id, f"Output:\n{output}", thread_id)
                sessions.pop(chat_id)
            return "ok"

        elif text.startswith("/run"):
            code = text.partition("/run")[2].strip()
            if not code:
                send_message(chat_id, "❗ Please provide code after /run.", thread_id)
                return "ok"
            if not is_safe_code(code):
                send_message(chat_id, "⚠️ Code contains forbidden functions/libraries.", thread_id)
                return "ok"
            var, prompt = extract_input_prompt(code)
            if var:
                sessions[chat_id] = {'code': code, 'state': 'wait_input', 'var': var}
                send_message(chat_id, prompt, thread_id)
            else:
                output = "```\n" + run_code(code) + "```"
                send_message(chat_id, f"Output:\n{output}", thread_id)
            return "ok"

        elif text:
            if not text.startswith("/run"):
                send_message(chat_id, "Unknown command!", thread_id)
                return "ok"
            if not is_safe_code(text):
                send_message(chat_id, "⚠️ Code contains forbidden functions/libraries.", thread_id)
                return "ok"
            var, prompt = extract_input_prompt(text)
            if var:
                sessions[chat_id] = {'code': text, 'state': 'wait_input', 'var': var}
                send_message(chat_id, prompt, thread_id)
            else:
                output = run_code(text)
                send_message(chat_id, f"Output:\n{output}", thread_id)
            return "ok"

    return "ok"


@app.route('/file/<path:filename>')
def baca_fail(filename):
    path = os.path.join('test', filename)

    if not os.path.isfile(path):
        return "Fail tidak dijumpai", 404

    with open(path, 'r') as f:
        isi = f.read()

    return f"<pre>{isi}</pre>"
