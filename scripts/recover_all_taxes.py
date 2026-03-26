import sqlite3
import os

# --- CONFIGURATION ---
#DB_PATH = r"d:\Github\JenBot\economy.db"
DB_PATH = r"/root/aibot/economy.db"

def get_setting(cursor, key, default=0):
    cursor.execute("SELECT value FROM settings WHERE key = ?", (key,))
    row = cursor.fetchone()
    return int(row[0]) if row and row[0] else default

def set_setting(cursor, key, value):
    cursor.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, str(value)))

def recover():
    if not os.path.exists(DB_PATH):
        print(f"Error: Database not found at {DB_PATH}")
        return

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # Get last processed ID
    last_id = get_setting(cursor, 'last_recovered_tx_id', 0)
    
    # Get current max ID
    cursor.execute("SELECT MAX(id) FROM transactions")
    max_id_row = cursor.fetchone()
    max_id = max_id_row[0] if max_id_row and max_id_row[0] else 0

    if max_id <= last_id:
        print(f"📊 No new transactions found since last recovery (Last ID: {last_id}).")
        conn.close()
        return

    print(f"🚀 Processing transactions from ID {last_id + 1} to {max_id}...")
    total_recovered = 0

    # 1. Blackjack Wins (10%)
    cursor.execute("""
        SELECT amount, type FROM transactions 
        WHERE id > ? AND (type = 'Blackjack Win' OR type = 'Blackjack Win (Natural)') AND amount > 0
    """, (last_id,))
    bj_rows = cursor.fetchall()
    bj_tax = 0
    for amount, tx_type in bj_rows:
        mult = 2.2 if "Natural" in tx_type else 1.9
        bet = amount / mult
        tax = int(bet * 0.1)
        bj_tax += tax
    print(f"🃏 Blackjack Recovery: {bj_tax:,} JC")
    total_recovered += bj_tax

    # 2. Robbery Success (5%)
    cursor.execute("""
        SELECT amount FROM transactions 
        WHERE id > ? AND type LIKE 'Robbed %' AND amount > 0
    """, (last_id,))
    rob_rows = cursor.fetchall()
    rob_tax = 0
    for (amount,) in rob_rows:
        stolen = amount / 0.95
        tax = int(stolen * 0.05)
        rob_tax += tax
    print(f"🥷 Robbery Recovery: {rob_tax:,} JC")
    total_recovered += rob_tax

    # 3. BJ Duel Win (5%)
    cursor.execute("""
        SELECT amount FROM transactions 
        WHERE id > ? AND type = 'BJ Duel Win' AND amount > 0
    """, (last_id,))
    duel_rows = cursor.fetchall()
    duel_tax = 0
    for (amount,) in duel_rows:
        pot = amount / 0.95
        fee = int(pot * 0.05)
        duel_tax += fee
    print(f"⚔️ BJ Duel Recovery: {duel_tax:,} JC")
    total_recovered += duel_tax

    # 4. Crash Entry Fees (10-15%)
    cursor.execute("""
        SELECT type FROM transactions 
        WHERE id > ? AND type LIKE 'Crash Game (Fee: % JC)'
    """, (last_id,))
    crash_entry_rows = cursor.fetchall()
    crash_entry_tax = 0
    for (tx_type,) in crash_entry_rows:
        try:
            parts = tx_type.split("Fee: ")
            if len(parts) > 1:
                fee_val = parts[1].split(" JC")[0]
                crash_entry_tax += int(fee_val)
        except: continue
    print(f"🚀 Crash Entry Recovery: {crash_entry_tax:,} JC")
    total_recovered += crash_entry_tax

    if total_recovered > 0:
        cursor.execute("SELECT value FROM settings WHERE key = 'fee_vault'")
        vault_row = cursor.fetchone()
        current_vault = int(vault_row[0] or 0) if vault_row else 0
        new_vault = current_vault + total_recovered
        
        # Update settings
        set_setting(cursor, 'fee_vault', new_vault)
        set_setting(cursor, 'last_recovered_tx_id', max_id)
        
        conn.commit()
        print(f"\n✅ SUCCESS! Injected {total_recovered:,} JC into the Vault.")
        print(f"💰 New Vault Balance: {new_vault:,} JC")
    else:
        # Still update the last_id to avoid pointless scans next time
        set_setting(cursor, 'last_recovered_tx_id', max_id)
        conn.commit()
        print("\nℹ️ No missing taxes found in the new transactions.")

    conn.close()

if __name__ == "__main__":
    recover()
