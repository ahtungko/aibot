# Multi-User Daily Check-in (Once Per Day, Reset at 00:00 GMT+8)

## Changes Required

### 1. D1 Database — New Schema

```sql
-- Drop and recreate (or migrate if you have data to keep)
DROP TABLE IF EXISTS checkins;

CREATE TABLE checkins (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    user_name TEXT,
    checkin_date TEXT NOT NULL,   -- "2026-03-21" in GMT+8
    checkin_time DATETIME DEFAULT CURRENT_TIMESTAMP,
    note TEXT,
    ai_reply TEXT
);

-- Enforce one check-in per user per day
CREATE UNIQUE INDEX idx_user_date ON checkins (user_id, checkin_date);
```

The `UNIQUE INDEX` on [(user_id, checkin_date)](file:///d:/Github/JenBot/jbot.py#708-721) makes it **impossible** to insert a second check-in for the same user on the same day. The `checkin_date` is calculated in GMT+8 so it resets at midnight your time.

---

### 2. Cloudflare Worker — Updated Logic

Key changes:
- Accept `user_id` and `user_name` from the bot
- Calculate today's date in **GMT+8**
- Check if user already checked in today → return error if so
- Insert with `user_id` and `checkin_date`

```js
// New: get today's date in GMT+8
const now = new Date();
const gmt8 = new Date(now.getTime() + 8 * 60 * 60 * 1000);
const todayGMT8 = gmt8.toISOString().split("T")[0]; // "2026-03-21"

// Check if already checked in today
const existing = await env.DB.prepare(
  "SELECT id FROM checkins WHERE user_id = ? AND checkin_date = ?"
).bind(user_id, todayGMT8).first();

if (existing) {
  return Response with "Already checked in today!"
}

// Insert with user info
await env.DB.prepare(
  "INSERT INTO checkins (user_id, user_name, checkin_date, note, ai_reply) VALUES (?, ?, ?, ?, ?)"
).bind(user_id, user_name, todayGMT8, note, aiReply).run();
```

---

### 3. Discord Bot ([jbot.py](file:///d:/Github/JenBot/jbot.py)) — Send User Identity

Add `user_id` and `user_name` to the payload:

```python
payload = {
    "user_pass": CHECKIN_AUTH_PASS,
    "user_id": str(ctx.author.id),
    "user_name": str(ctx.author),
    "checkin_note": note
}
```

Handle the "already checked in" response from worker.

---

## Verification

1. `!ck hello` → ✅ Check-in logged
2. `!ck again` → ❌ "Already checked in today!"  
3. After 00:00 GMT+8 → ✅ Can check in again
4. Different user → ✅ Can check in independently
