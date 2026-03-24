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
    cursor.execute("SELECT CAST(SUM(balance + bank) AS INTEGER) as total_jc FROM wallets")
    total_jc = int(cursor.fetchone()['total_jc'] or 0)

    # 2. Total Gold Grams
    cursor.execute("SELECT SUM(gold_grams) as total_gold FROM investments")
    total_gold = cursor.fetchone()['total_gold'] or 0.0

    # 3. Global Fee Vaults
    # JC Vault
    cursor.execute("SELECT value FROM settings WHERE key = 'fee_vault'")
    vault_row = cursor.fetchone()
    vault_jc = 0
    if vault_row:
        try:
            # Try to parse as JSON in case it's in the old/alternate format
            parsed = json.loads(vault_row['value'])
            if isinstance(parsed, dict):
                vault_jc = int(parsed.get('jc_total', 0))
            else:
                vault_jc = int(parsed)
        except (json.JSONDecodeError, ValueError, TypeError):
            try:
                vault_jc = int(vault_row['value'])
            except (ValueError, TypeError):
                pass

    # Gold Vault
    cursor.execute("SELECT value FROM settings WHERE key = 'gold_fee_vault'")
    gold_vault_row = cursor.fetchone()
    vault_gold = 0.0
    if gold_vault_row:
        try:
            vault_gold = float(gold_vault_row['value'])
        except (ValueError, TypeError):
            pass

    # 5. Get Latest Gold Price for Net Worth calculation
    cursor.execute("SELECT value FROM settings WHERE key = 'last_gold_price'")
    price_row = cursor.fetchone()
    last_gold_price = float(price_row['value']) if price_row else 0.0

    # 6. User Stats
    cursor.execute("SELECT COUNT(*) as count FROM wallets")
    user_count = cursor.fetchone()['count'] or 0
    
    # 8. 7-Day Transaction Volume (Daily Sinks/Faucets)
    # Aggregated by day using epoch in string
    cursor.execute('''
        SELECT 
            date(cast(timestamp as real), 'unixepoch') as day, 
            cast(sum(case when amount > 0 then amount else 0 end) as integer) as faucets,
            cast(sum(case when amount < 0 then abs(amount) else 0 end) as integer) as sinks
        FROM transactions 
        WHERE cast(timestamp as real) > strftime('%s', 'now', '-7 days')
        GROUP BY day 
        ORDER BY day ASC
    ''')
    daily_history = cursor.fetchall()
    history_json = [dict(row) for row in daily_history]

    # 9. Tax Flow Activity (Recent Taxes, Fees, Fines)
    cursor.execute('''
        SELECT timestamp, user_id, amount, type as reason 
        FROM transactions 
        WHERE (type LIKE '%Tax%' OR type LIKE '%Fee%' OR type LIKE '%Fine%')
        ORDER BY id DESC 
        LIMIT 20
    ''')
    tax_transactions = cursor.fetchall()

    # 10. Total Tax Revenue (Lifetime JC from taxes/fines/fees)
    cursor.execute('''
        SELECT CAST(SUM(ABS(amount)) AS INTEGER) as total_collected 
        FROM transactions 
        WHERE (type LIKE '%Tax%' OR type LIKE '%Fee%' OR type LIKE '%Fine%')
    ''')
    total_tax_revenue = int(cursor.fetchone()['total_collected'] or 0)

    # 11. Top 5 Richest Players (Wallet + Bank + Gold Value)
    cursor.execute('''
        SELECT w.user_id, CAST((w.balance + w.bank + (COALESCE(i.gold_grams, 0) * ?)) AS INTEGER) as net_worth 
        FROM wallets w
        LEFT JOIN investments i ON w.user_id = i.user_id
        ORDER BY net_worth DESC 
        LIMIT 5
    ''', (last_gold_price,))
    top_players = cursor.fetchall()

    # 5. Recent Transactions
    cursor.execute('''
        SELECT timestamp, user_id, amount, type as reason 
        FROM transactions 
        ORDER BY id DESC 
        LIMIT 50
    ''')
    recent_transactions = cursor.fetchall()
    
    # 6. Rain Settings
    cursor.execute("SELECT value FROM settings WHERE key = 'rain_rate'")
    rain_rate = cursor.fetchone()
    rain_rate = rain_rate['value'] if rain_rate else '0.1'

    cursor.execute("SELECT value FROM settings WHERE key = 'rain_min'")
    rain_min = cursor.fetchone()
    rain_min = rain_min['value'] if rain_min else '100'

    cursor.execute("SELECT value FROM settings WHERE key = 'rain_max'")
    rain_max = cursor.fetchone()
    rain_max = rain_max['value'] if rain_max else '500'

    cursor.execute("SELECT value FROM settings WHERE key = 'rain_pool'")
    rain_pool = cursor.fetchone()
    rain_pool = rain_pool['value'] if rain_pool else '5000'

    # 7. Mystery Box Settings
    cursor.execute("SELECT value FROM settings WHERE key = 'box_legendary_rate'")
    box_legendary_rate = cursor.fetchone()
    box_legendary_rate = box_legendary_rate['value'] if box_legendary_rate else '0.001'

    cursor.execute("SELECT value FROM settings WHERE key = 'box_epic_rate'")
    box_epic_rate = cursor.fetchone()
    box_epic_rate = box_epic_rate['value'] if box_epic_rate else '0.01'

    cursor.execute("SELECT value FROM settings WHERE key = 'box_rare_rate'")
    box_rare_rate = cursor.fetchone()
    box_rare_rate = box_rare_rate['value'] if box_rare_rate else '0.03'

    # 8. Mystery Box Event Settings
    cursor.execute("SELECT value FROM settings WHERE key = 'box_legendary_event'")
    box_leg_event = cursor.fetchone()
    box_leg_event = float(box_leg_event['value']) if box_leg_event else None

    cursor.execute("SELECT value FROM settings WHERE key = 'box_epic_event'")
    box_epic_event = cursor.fetchone()
    box_epic_event = float(box_epic_event['value']) if box_epic_event else None

    cursor.execute("SELECT value FROM settings WHERE key = 'box_rare_event'")
    box_rare_event = cursor.fetchone()
    box_rare_event = float(box_rare_event['value']) if box_rare_event else None

    cursor.execute("SELECT value FROM settings WHERE key = 'box_event_expiry'")
    box_event_expiry = cursor.fetchone()
    box_event_expiry = int(box_event_expiry['value']) if box_event_expiry else 0

    now = int(datetime.now().timestamp())
    is_event_active = box_event_expiry > now
    
    event_remaining = ""
    if is_event_active:
        rem = box_event_expiry - now
        mins, secs = divmod(rem, 60)
        hours, mins = divmod(mins, 60)
        if hours > 0:
            event_remaining = f"{hours}h {mins}m"
        else:
            event_remaining = f"{mins}m {secs}s"

    # 9. Taxman Settings
    cursor.execute("SELECT value FROM settings WHERE key = 'taxman_enabled'")
    taxman_enabled = cursor.fetchone()
    taxman_enabled = (taxman_enabled['value'].lower() == 'true') if taxman_enabled else False

    cursor.execute("SELECT value FROM settings WHERE key = 'taxman_percent'")
    taxman_percent = cursor.fetchone()
    taxman_percent = taxman_percent['value'] if taxman_percent else '10'

    cursor.execute("SELECT value FROM settings WHERE key = 'last_tax_timestamp'")
    last_tax = cursor.fetchone()
    last_tax = int(last_tax['value']) if last_tax else 0

    next_tax = last_tax + (24 * 60 * 60)
    taxman_imminent = (now >= next_tax) if taxman_enabled else False
    
    taxman_remaining = ""
    if taxman_enabled and not taxman_imminent:
        rem = next_tax - now
        h, m = divmod(rem // 60, 60)
        taxman_remaining = f"{h}h {m}m"

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
        vault_jc=vault_jc,
        vault_gold=vault_gold,
        user_count=user_count,
        history_json=json.dumps(history_json),
        tax_transactions=[enrich_user_data(row, 'user_id') for row in tax_transactions],
        total_tax_revenue=total_tax_revenue,
        top_players=enriched_top_players,
        transactions=enriched_transactions,
        rain_rate=rain_rate,
        rain_min=rain_min,
        rain_max=rain_max,
        rain_pool=rain_pool,
        box_legendary_rate=box_legendary_rate,
        box_epic_rate=box_epic_rate,
        box_rare_rate=box_rare_rate,
        is_event_active=is_event_active,
        box_leg_event=box_leg_event,
        box_epic_event=box_epic_event,
        box_rare_event=box_rare_event,
        event_remaining=event_remaining,
        event_expiry_ts=box_event_expiry,
        taxman_enabled=taxman_enabled,
        taxman_percent=taxman_percent,
        taxman_imminent=taxman_imminent,
        taxman_remaining=taxman_remaining,
        next_tax_ts=next_tax,
        last_tax_ts=last_tax
    )

if __name__ == '__main__':
    # Bind to '::' to support IPv6-only VPS hosting
    app.run(host='::', port=5000, debug=True)
