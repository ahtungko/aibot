import sqlite3
import os
import time

# --- CONFIGURATION ---
#DB_PATH = r"d:\Github\JenBot\economy.db"  # Change this to your real path if needed
DB_PATH = r"/root/aibot/economy.db"  # Change this to your real path if needed

def recover():
    if not os.path.exists(DB_PATH):
        print(f"❌ Error: Database not found at {DB_PATH}")
        return

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    print("🚀 Starting Historical Blackjack Tax Recovery...")
    
    # 1. Fetch settings (fee_vault)
    cursor.execute("SELECT value FROM settings WHERE key = 'fee_vault'")
    res = cursor.fetchone()
    current_vault = int(float(res['value'])) if res else 0
    
    # 2. Find all Blackjack Wins
    # amount = 1.9 * bet -> bet = amount / 1.9
    # amount = 2.2 * bet -> bet = amount / 2.2 (Natural)
    cursor.execute("SELECT id, user_id, amount, type, timestamp FROM transactions WHERE type LIKE 'Blackjack Win%'")
    wins = cursor.fetchall()
    
    total_recovered = 0
    print(f"📦 Found {len(wins)} winning hands...")
    
    for row in wins:
        row_id = row['id']
        uid = row['user_id']
        amount = row['amount']
        tx_type = row['type']
        ts = row['timestamp']
        
        # Calculate original bet
        is_natural = "Natural" in tx_type
        divisor = 2.2 if is_natural else 1.9
        
        original_bet = round(amount / divisor)
        # Tax that was 'lost' to the void (10%)
        tax_lost = int(original_bet * 0.1)
        
        if tax_lost > 0:
            # Inject the tax record
            cursor.execute("INSERT INTO transactions (user_id, amount, type, timestamp) VALUES (?, ?, ?, ?)",
                           (uid, tax_lost, "Blackjack Tax (Recovered)", ts))
            total_recovered += tax_lost

    if total_recovered > 0:
        # Update Vault
        new_vault = current_vault + total_recovered
        cursor.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('fee_vault', ?)", (str(new_vault),))
        
        conn.commit()
        print(f"✅ Recovery Complete!")
        print(f"💰 Total Recovered: {total_recovered:,} JC")
        print(f"🏦 New Vault Balance: {new_vault:,} JC")
    else:
        print("ℹ️ No taxes to recover or no games found.")

    conn.close()

if __name__ == "__main__":
    recover()
