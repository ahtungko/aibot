import sqlite3
import os
import time

# Paths
#DB_PATH = r"d:\Github\JenBot\economy.db"
DB_PATH = r"/root/aibot/economy.db"

def migrate():
    if not os.path.exists(DB_PATH):
        print(f"❌ Error: Database not found at {DB_PATH}")
        return

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    print("🚀 Starting Tax Visibility Migration...")
    
    # 1. Migrate 'Bought Gold'
    # Current log: amount = -total_spent (including fee)
    # Target: 
    #   - Original -> rename to "Bought Gold (Migrated)"
    #   - New -> "Gold Purchase Fee" with amount = -fee
    # Assumption: 5% fee for historical records (safe default)
    cursor.execute("SELECT id, user_id, amount, timestamp FROM transactions WHERE type = 'Bought Gold'")
    bought_gold = cursor.fetchall()
    
    print(f"📦 Processing {len(bought_gold)} 'Bought Gold' records...")
    for row_id, uid, amount, ts in bought_gold:
        total_spent = abs(amount)
        # Assuming 5% fee was added to the base price: Price + 0.05*Price = Total -> Price = Total / 1.05
        # Fee = Total - Price
        fee = int(total_spent - (total_spent / 1.05))
        if fee < 1: fee = 1
        
        # Insert the fee record
        cursor.execute("INSERT INTO transactions (user_id, amount, type, timestamp) VALUES (?, ?, ?, ?)",
                       (uid, -fee, "Gold Purchase Fee", ts))
        
        # Update the original record to reflect the net purchase and a new name
        # We subtract the fee from the original amount (which is negative)
        new_purchase_amount = amount + fee 
        cursor.execute("UPDATE transactions SET type = 'Bought Gold (Migrated)', amount = ? WHERE id = ?",
                       (new_purchase_amount, row_id))

    # 2. Migrate 'Work Payment' (renamed to 'Work Reward' in new code)
    # Current log: amount = net_reward
    # Target: 
    #   - Original -> rename to "Work Reward (Migrated)"
    #   - New -> "Work Tax" with amount = -tax
    # Assumption: 10% tax for historical records (safe average)
    cursor.execute("SELECT id, user_id, amount, timestamp FROM transactions WHERE type = 'Work Payment'")
    work_payments = cursor.fetchall()
    
    print(f"⚒️ Processing {len(work_payments)} 'Work Payment' records...")
    for row_id, uid, amount, ts in work_payments:
        net_reward = amount
        # Assuming 10% tax was taken from gross: Gross - 0.10*Gross = Net -> Gross = Net / 0.90
        # Tax = Gross - Net
        tax = int((net_reward / 0.90) - net_reward)
        if tax < 1: tax = 1
        
        # Insert the tax record
        cursor.execute("INSERT INTO transactions (user_id, amount, type, timestamp) VALUES (?, ?, ?, ?)",
                       (uid, -tax, "Work Tax", ts))
        
        # Rename the original record
        cursor.execute("UPDATE transactions SET type = 'Work Reward (Migrated)' WHERE id = ?", (row_id,))

    # 3. Handle 'Sold Gold'
    # Current log: amount = net_payout
    # Target:
    #   - Original -> rename to "Sold Gold (Migrated)"
    #   - New -> "Gold Sale Fee" with amount = -fee
    cursor.execute("SELECT id, user_id, amount, timestamp FROM transactions WHERE type = 'Sold Gold'")
    sold_gold = cursor.fetchall()
    
    print(f"💰 Processing {len(sold_gold)} 'Sold Gold' records...")
    for row_id, uid, amount, ts in sold_gold:
        net_payout = amount
        # Net = Gross - 0.05*Gross -> Gross = Net / 0.95
        # Fee = Gross - Net
        fee = int((net_payout / 0.95) - net_payout)
        if fee < 1: fee = 1
        
        # Insert the fee record
        cursor.execute("INSERT INTO transactions (user_id, amount, type, timestamp) VALUES (?, ?, ?, ?)",
                       (uid, -fee, "Gold Sale Fee", ts))
        
        # Rename original
        cursor.execute("UPDATE transactions SET type = 'Sold Gold (Migrated)' WHERE id = ?", (row_id,))

    conn.commit()
    conn.close()
    print("✅ Migration complete! Dashboard tax metrics should now reflect historical data.")

if __name__ == "__main__":
    migrate()
