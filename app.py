from flask import Flask, request
import os, re, tempfile, subprocess
import requests
import logging
from datetime import datetime

app = Flask(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN") or "PASTE_TOKEN_HERE"
BASE_URL = f"https://api.telegram.org/bot{BOT_TOKEN}"

# Simpan sesi user: {chat_id: {'code': ..., 'state': 'wait_input', 'var': ...}}
sessions = {}

# ==========================
#  UTILITY FUNCTIONS
# ==========================

def send_message(chat_id, text):
    requests.post(f"{BASE_URL}/sendMessage", json={
        "chat_id": chat_id,
        "text": text,
    })

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

def is_safe_code(code):
    forbidden_patterns = [
        r'\bimport\s+os\b',
        r'\bimport\s+subprocess\b',
        r'\bimport\s+sys\b',
        r'\bimport\s+shutil\b',
        r'importlib',
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

# Setup logging

def save_log(chat_id, username, ip):
    filename = "access.log"
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
        chat_id = data["message"]["chat"]["id"]
        text = data["message"].get("text", "").strip()
        user_info = data["message"].get("from", {})
        username = user_info.get("username", "unknown")
        ip = request.headers.get("X-Forwarded-For", request.remote_addr)
        save_log(chat_id, username,ip)
        # Handle commands
        if text == "/start":
            send_message(chat_id, "Welcome 🥳\nPlease read /policy before using this bot.")
        elif text == "/about":
            send_message(chat_id, "Hi! I'm Bot Ak1m, created by **Daniel Hakim**.\nContact @d4n13lh4k1m for feedback or issues.")
        elif text == "/help":
            send_message(chat_id, "Send Python code starting with /run or directly.\nSupports input() and basic Python features.")
        elif text == "/policy":
            send_message(chat_id, policy)
        elif text == "/cancel":
            if chat_id in sessions:
                sessions.pop(chat_id)
                send_message(chat_id, "✅ Session cancelled.")
            else:
                send_message(chat_id, "No active session to cancel.")
            return "ok"

        # If user is replying input()
        elif chat_id in sessions and sessions[chat_id].get('state') == 'wait_input':
            session = sessions[chat_id]
            var = session['var']
            code = replace_input(session['code'], var, text)

            next_var, next_prompt = extract_input_prompt(code)
            if next_var:
                sessions[chat_id] = {'code': code, 'state': 'wait_input', 'var': next_var}
                send_message(chat_id, next_prompt)
            else:
                if not is_safe_code(code):
                    send_message(chat_id, "⚠️ Final code contains unsafe functions.")
                    sessions.pop(chat_id)
                    return "ok"
                output = run_code(code)
                send_message(chat_id, f"Output:\n{output}")
                sessions.pop(chat_id)
            return "ok"

        # If user sends code with /run
        elif text.startswith("/run"):
            code = text.partition("/run")[2].strip()
            if not code:
                send_message(chat_id, "❗ Please provide code after /run.")
                return "ok"
            if not is_safe_code(code):
                send_message(chat_id, "⚠️ Code contains forbidden functions/libraries.")
                return "ok"
            var, prompt = extract_input_prompt(code)
            if var:
                sessions[chat_id] = {'code': code, 'state': 'wait_input', 'var': var}
                send_message(chat_id, prompt)
            else:
                output = run_code(code)
                send_message(chat_id, f"Output:\n{output}")
            return "ok"

        # If user sends plain code
        elif text:
            if not text.startswith("/run"):
                send_message(chat_id, "Unknown command!")
                return "ok"
            if not is_safe_code(text):
                send_message(chat_id, "⚠️ Code contains forbidden functions/libraries.")
                return "ok"
            var, prompt = extract_input_prompt(text)
            if var:
                sessions[chat_id] = {'code': text, 'state': 'wait_input', 'var': var}
                send_message(chat_id, prompt)
            else:
                output = run_code(text)
                send_message(chat_id, f"Output:\n{output}")
            return "ok"

    return "ok"
