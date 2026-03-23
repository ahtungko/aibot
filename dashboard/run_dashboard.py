import os
import sqlite3
import json
import urllib.request
import urllib.error
from flask import Flask, render_template

app = Flask(__name__)

# Import from config.py to ensure the token exactly matches the main bot
import sys
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from config import DISCORD_BOT_TOKEN as BOT_TOKEN

from datetime import datetime


user_cache = {}

def get_discord_user(user_id):
    if not BOT_TOKEN:
        return {"username": "Unknown", "global_name": "Unknown", "avatar": None}
    
    if user_id in user_cache:
        return user_cache[user_id]
        
    req = urllib.request.Request(f"https://discord.com/api/v10/users/{user_id}")
    req.add_header("Authorization", f"Bot {BOT_TOKEN}")
    req.add_header("User-Agent", "DiscordBot (https://github.com/ahtungko/JenBot, 1.0.0)")
    try:
        with urllib.request.urlopen(req, timeout=1.5) as response:
            data = json.loads(response.read().decode())
            user_cache[user_id] = data
            return data
    except Exception:
        pass
        
    return {"username": "Unknown", "global_name": "Unknown", "avatar": None}

def enrich_user_data(row, user_id_key):
    user_id = row[user_id_key]
    user_info = get_discord_user(user_id)
    name = user_info.get('global_name') or user_info.get('username') or f"User {user_id}"
    avatar_hash = user_info.get('avatar')
    
    if avatar_hash:
        avatar_url = f"https://cdn.discordapp.com/avatars/{user_id}/{avatar_hash}.png"
    else:
        # Default discord avatar
        avatar_url = f"https://cdn.discordapp.com/embed/avatars/{int(user_id) % 5}.png"
        
    row_dict = dict(row)
    row_dict['discord_name'] = name
    row_dict['avatar_url'] = avatar_url
    return row_dict

# Path to the Jenkins economy database
DB_PATH = os.path.join(os.path.dirname(__file__), '..', 'economy.db')

def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

@app.route('/')
def index():
    if not os.path.exists(DB_PATH):
        return "Database not found. Make sure the bot has run at least once.", 500

    conn = get_db_connection()
    cursor = conn.cursor()

    # 1. Total JC in Circulation (Wallet + Bank)
    cursor.execute("SELECT SUM(balance + bank) as total_jc FROM wallets")
    total_jc = cursor.fetchone()['total_jc'] or 0

    # 2. Total Gold Grams
    cursor.execute("SELECT SUM(gold_grams) as total_gold FROM investments")
    total_gold = cursor.fetchone()['total_gold'] or 0.0

    # 3. Global Fee Vault
    cursor.execute("SELECT value FROM settings WHERE key = 'fee_vault'")
    vault_row = cursor.fetchone()
    vault_data = {"jc_total": 0, "gold_total": 0.0}
    if vault_row:
        try:
            parsed = json.loads(vault_row['value'])
            if isinstance(parsed, dict):
                vault_data = parsed
            elif isinstance(parsed, (int, float)):
                vault_data["jc_total"] = int(parsed)
        except json.JSONDecodeError:
            try:
                vault_data["jc_total"] = int(vault_row['value'])
            except ValueError:
                pass


    # 4. Top 5 Richest Players
    cursor.execute('''
        SELECT user_id, (balance + bank) as net_worth 
        FROM wallets 
        ORDER BY net_worth DESC 
        LIMIT 5
    ''')
    top_players = cursor.fetchall()

    # 5. Recent Transactions
    cursor.execute('''
        SELECT timestamp, user_id, amount, type as reason 
        FROM transactions 
        ORDER BY id DESC 
        LIMIT 50
    ''')
    recent_transactions = cursor.fetchall()
    
    conn.close()
    
    # Enrich with discord data and format timestamps
    enriched_top_players = [enrich_user_data(row, 'user_id') for row in top_players]
    
    enriched_transactions = []
    for row in recent_transactions:
        row_dict = enrich_user_data(row, 'user_id')
        try:
            ts = row['timestamp']
            if isinstance(ts, (int, float)) or (isinstance(ts, str) and ts.isdigit()):
                dt = datetime.fromtimestamp(int(ts))
            else:
                ts_str = str(ts).split('.')[0] 
                dt = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S")
            row_dict['timestamp_formatted'] = dt.strftime("%b %d, %I:%M %p")
        except Exception:
            row_dict['timestamp_formatted'] = row['timestamp']
        enriched_transactions.append(row_dict)

    return render_template(
        'dashboard.html',
        total_jc=total_jc,
        total_gold=total_gold,
        vault_jc=vault_data.get('jc_total', 0),
        vault_gold=vault_data.get('gold_total', 0.0),
        top_players=enriched_top_players,
        transactions=enriched_transactions
    )

if __name__ == '__main__':
    # Bind to '::' to support IPv6-only VPS hosting
    app.run(host='::', port=5000, debug=True)
