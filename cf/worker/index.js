export default {
  async fetch(request, env) {
    const corsHeaders = {
      "Access-Control-Allow-Origin": "*",
      "Access-Control-Allow-Methods": "POST, OPTIONS",
      "Access-Control-Allow-Headers": "Content-Type",
    };

    if (request.method === "OPTIONS") {
      return new Response(null, { headers: corsHeaders });
    }

    const jsonHeaders = { ...corsHeaders, "Content-Type": "application/json" };

    try {
      const body = await request.json();
      const { user_pass, action } = body;

      // Auth check
      if (user_pass !== env.AUTH_PASS) {
        return new Response(JSON.stringify({ error: "Access Denied" }), {
          status: 403, headers: jsonHeaders,
        });
      }

      // Calculate today's date in GMT+8
      const now = new Date();
      const gmt8 = new Date(now.getTime() + 8 * 60 * 60 * 1000);
      const todayGMT8 = gmt8.toISOString().split("T")[0];

      // Route by action
      const act = action || "checkin";

      if (act === "checkin") {
        return await handleCheckin(body, todayGMT8, env, jsonHeaders);
      } else if (act === "streak") {
        return await handleStreak(body, todayGMT8, env, jsonHeaders);
      } else if (act === "leaderboard") {
        return await handleLeaderboard(todayGMT8, env, jsonHeaders);
      } else {
        return new Response(JSON.stringify({ error: "Unknown action" }), {
          status: 400, headers: jsonHeaders,
        });
      }

    } catch (err) {
      return new Response(JSON.stringify({ error: "Server Error: " + err.message }), {
        status: 500, headers: { ...corsHeaders, "Content-Type": "application/json" },
      });
    }
  }
};

// ─── Check-in ───────────────────────────────────────────

async function handleCheckin(body, todayGMT8, env, headers) {
  const { user_id, user_name, checkin_note } = body;

  if (!user_id) {
    return new Response(JSON.stringify({ error: "Missing user_id" }), {
      status: 400, headers,
    });
  }

  // Check duplicate
  const existing = await env.DB.prepare(
    "SELECT id FROM checkins WHERE user_id = ? AND checkin_date = ?"
  ).bind(user_id, todayGMT8).first();

  if (existing) {
    return new Response(JSON.stringify({
      success: false,
      error: "You already checked in today! 🙅 Come back after midnight (GMT+8)."
    }), { status: 200, headers });
  }

  // AI prompt
  const note = checkin_note || "No notes today";
  const aiPayload = {
    model: "gpt-5.4",
    input: [{
      role: "user",
      content: [{
        type: "input_text",
        text: `Task: Daily check-in. User: "${user_name || "Anonymous"}". Note: "${note}". Response: Give a super playful, sassy, and short Gen-Z style reply in English.`
      }]
    }],
    store: false,
    stream: false,
  };

  const response = await fetch(`${env.API_BASE_URL}v1/responses`, {
    method: "POST",
    headers: {
      "Authorization": `Bearer ${env.API_KEY}`,
      "Content-Type": "application/json",
      "User-Agent": "Cloudflare-Worker-Checkin-App",
    },
    body: JSON.stringify(aiPayload),
  });

  const data = await response.json();
  const aiReply = data.output?.[0]?.content?.[0]?.text || "Check-in logged! AI is currently vibing elsewhere.";

  // Save to D1
  try {
    await env.DB.prepare(
      "INSERT INTO checkins (user_id, user_name, checkin_date, note, ai_reply) VALUES (?, ?, ?, ?, ?)"
    ).bind(user_id, user_name || "Anonymous", todayGMT8, note, aiReply).run();
  } catch (dbError) {
    if (dbError.message && dbError.message.includes("UNIQUE")) {
      return new Response(JSON.stringify({
        success: false,
        error: "You already checked in today! 🙅 Come back after midnight (GMT+8)."
      }), { status: 200, headers });
    }
    console.error("Database save failed:", dbError);
  }

  // Calculate current streak for the response
  const streak = await calculateStreak(user_id, todayGMT8, env);

  return new Response(JSON.stringify({
    success: true,
    message: aiReply,
    streak: streak,
  }), { headers });
}

// ─── Streak ─────────────────────────────────────────────

async function calculateStreak(userId, todayGMT8, env) {
  // Get all check-in dates for this user, ordered descending
  const { results } = await env.DB.prepare(
    "SELECT checkin_date FROM checkins WHERE user_id = ? ORDER BY checkin_date DESC"
  ).bind(userId).all();

  if (!results || results.length === 0) return 0;

  let streak = 0;
  let expectedDate = new Date(todayGMT8 + "T00:00:00Z");

  for (const row of results) {
    const rowDate = new Date(row.checkin_date + "T00:00:00Z");
    if (rowDate.getTime() === expectedDate.getTime()) {
      streak++;
      expectedDate.setDate(expectedDate.getDate() - 1);
    } else if (rowDate.getTime() < expectedDate.getTime()) {
      // Gap found — streak broken
      break;
    }
    // If rowDate > expectedDate, skip (shouldn't happen with DESC order)
  }

  return streak;
}

async function handleStreak(body, todayGMT8, env, headers) {
  const { user_id } = body;

  if (!user_id) {
    return new Response(JSON.stringify({ error: "Missing user_id" }), {
      status: 400, headers,
    });
  }

  const streak = await calculateStreak(user_id, todayGMT8, env);

  // Check if user checked in today
  const checkedToday = await env.DB.prepare(
    "SELECT id FROM checkins WHERE user_id = ? AND checkin_date = ?"
  ).bind(user_id, todayGMT8).first();

  // Total check-ins ever
  const totalRow = await env.DB.prepare(
    "SELECT COUNT(*) as total FROM checkins WHERE user_id = ?"
  ).bind(user_id).first();

  return new Response(JSON.stringify({
    success: true,
    streak: streak,
    checked_today: !!checkedToday,
    total_checkins: totalRow?.total || 0,
  }), { headers });
}

// ─── Leaderboard ────────────────────────────────────────

async function handleLeaderboard(todayGMT8, env, headers) {
  // Get all unique users
  const { results: users } = await env.DB.prepare(
    "SELECT DISTINCT user_id, user_name FROM checkins"
  ).all();

  if (!users || users.length === 0) {
    return new Response(JSON.stringify({
      success: true,
      leaderboard: [],
    }), { headers });
  }

  // Calculate streak for each user
  const streaks = [];
  for (const user of users) {
    const streak = await calculateStreak(user.user_id, todayGMT8, env);
    if (streak > 0) {
      streaks.push({
        user_id: user.user_id,
        user_name: user.user_name,
        streak: streak,
      });
    }
  }

  // Sort by streak descending, take top 10
  streaks.sort((a, b) => b.streak - a.streak);
  const top10 = streaks.slice(0, 10);

  return new Response(JSON.stringify({
    success: true,
    leaderboard: top10,
  }), { headers });
}
