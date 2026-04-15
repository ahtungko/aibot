import json
import random
import secrets
import time
from typing import Any

import discord
from discord.ext import commands, tasks

import cogs.economy as economy
from config import COMMAND_PREFIX


BLACKJACK_GAME = "blackjack"

TABLE_WAITING = "waiting"
TABLE_IN_PROGRESS = "in_progress"
TABLE_FINISHED = "finished"
TABLE_CANCELLED = "cancelled"

PLAYER_JOINED = "joined"
PLAYER_PLAYING = "playing"
PLAYER_STOOD = "stood"
PLAYER_BUSTED = "busted"
PLAYER_BLACKJACK = "blackjack"
PLAYER_LEFT = "left"

WAITING_TABLE_TIMEOUT = 300
TURN_TIMEOUT = 45
FINISHED_TABLE_TIMEOUT = 300
MAX_BLACKJACK_TABLE_PLAYERS = 4
MIN_BLACKJACK_TABLE_PLAYERS = 2


def _now_ts(now_ts: int | None = None) -> int:
    return int(now_ts if now_ts is not None else time.time())


def _json_dump(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False)


def _json_load(raw: str | None, default):
    if not raw:
        return default
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return default


def ensure_table_schema(conn=None):
    own_conn = conn is None
    if own_conn:
        conn = economy.get_db()

    conn.execute(
        "CREATE TABLE IF NOT EXISTS game_tables ("
        "table_id TEXT PRIMARY KEY, "
        "game_type TEXT NOT NULL, "
        "guild_id TEXT DEFAULT '', "
        "channel_id TEXT NOT NULL, "
        "creator_id TEXT NOT NULL, "
        "bet_amount INTEGER NOT NULL, "
        "min_players INTEGER DEFAULT 2, "
        "max_players INTEGER DEFAULT 4, "
        "status TEXT NOT NULL, "
        "current_turn_user_id TEXT DEFAULT '', "
        "state_json TEXT DEFAULT '{}', "
        "message_id TEXT DEFAULT '', "
        "created_at INTEGER NOT NULL, "
        "updated_at INTEGER NOT NULL, "
        "expires_at INTEGER NOT NULL)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS game_table_players ("
        "table_id TEXT NOT NULL, "
        "user_id TEXT NOT NULL, "
        "seat INTEGER NOT NULL, "
        "bet_amount INTEGER NOT NULL, "
        "status TEXT NOT NULL DEFAULT 'joined', "
        "hand_json TEXT DEFAULT '[]', "
        "hand_value INTEGER DEFAULT 0, "
        "is_natural INTEGER DEFAULT 0, "
        "result_text TEXT DEFAULT '', "
        "payout INTEGER DEFAULT 0, "
        "joined_at INTEGER NOT NULL, "
        "acted_at INTEGER NOT NULL, "
        "PRIMARY KEY (table_id, user_id))"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS game_table_actions ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "table_id TEXT NOT NULL, "
        "user_id TEXT DEFAULT '', "
        "action TEXT NOT NULL, "
        "payload_json TEXT DEFAULT '{}', "
        "created_at INTEGER NOT NULL)"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_game_tables_channel_status ON game_tables(channel_id, status)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_game_tables_expiry ON game_tables(status, expires_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_game_table_players_user ON game_table_players(user_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_game_table_actions_table ON game_table_actions(table_id, created_at)")

    if own_conn:
        conn.commit()
        conn.close()


def create_blackjack_deck(rng: random.Random | None = None) -> list[str]:
    suits = ["♠️", "♥️", "♣️", "♦️"]
    ranks = ["2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K", "A"]
    deck = [f"{rank} {suit}" for suit in suits for rank in ranks]
    (rng or random).shuffle(deck)
    return deck


def calculate_blackjack_value(hand: list[str]) -> int:
    value = 0
    aces = 0
    for card in hand:
        rank = str(card).split()[0]
        if rank in {"J", "Q", "K"}:
            value += 10
        elif rank == "A":
            value += 11
            aces += 1
        else:
            value += int(rank)

    while value > 21 and aces:
        value -= 10
        aces -= 1
    return value


def _draw_card(state: dict) -> str:
    deck = state.get("deck") or []
    if not deck:
        deck = create_blackjack_deck()
        state["deck"] = deck
    return deck.pop()


def _serialize_player_row(row) -> dict:
    return {
        "table_id": row[0],
        "user_id": row[1],
        "seat": int(row[2]),
        "bet_amount": int(row[3]),
        "status": row[4],
        "hand": _json_load(row[5], []),
        "hand_value": int(row[6] or 0),
        "is_natural": bool(row[7]),
        "result_text": row[8] or "",
        "payout": int(row[9] or 0),
        "joined_at": int(row[10] or 0),
        "acted_at": int(row[11] or 0),
    }


def _table_row_to_dict(row) -> dict:
    return {
        "table_id": row[0],
        "game_type": row[1],
        "guild_id": row[2] or "",
        "channel_id": row[3],
        "creator_id": row[4],
        "bet_amount": int(row[5]),
        "min_players": int(row[6]),
        "max_players": int(row[7]),
        "status": row[8],
        "current_turn_user_id": row[9] or "",
        "state": _json_load(row[10], {}),
        "message_id": row[11] or "",
        "created_at": int(row[12]),
        "updated_at": int(row[13]),
        "expires_at": int(row[14]),
    }


def _get_players_for_table(table_id: str, conn) -> list[dict]:
    rows = economy.db_query(
        "SELECT table_id, user_id, seat, bet_amount, status, hand_json, hand_value, is_natural, result_text, payout, joined_at, acted_at "
        "FROM game_table_players WHERE table_id = ? ORDER BY seat ASC",
        (table_id,),
        fetchall=True,
        conn=conn,
    ) or []
    return [_serialize_player_row(row) for row in rows]


def get_recent_table_actions(table_id: str, limit: int = 6, conn=None) -> list[dict]:
    own_conn = conn is None
    if own_conn:
        conn = economy.get_db()
    ensure_table_schema(conn)
    rows = economy.db_query(
        "SELECT user_id, action, payload_json, created_at FROM game_table_actions "
        "WHERE table_id = ? ORDER BY id DESC LIMIT ?",
        (table_id, int(limit)),
        fetchall=True,
        conn=conn,
    ) or []
    actions = [
        {
            "user_id": row[0] or "",
            "action": row[1],
            "payload": _json_load(row[2], {}),
            "created_at": int(row[3]),
        }
        for row in rows
    ]
    if own_conn:
        conn.close()
    return list(reversed(actions))


def get_table(table_id: str, conn=None) -> dict | None:
    own_conn = conn is None
    if own_conn:
        conn = economy.get_db()
    ensure_table_schema(conn)
    row = economy.db_query(
        "SELECT table_id, game_type, guild_id, channel_id, creator_id, bet_amount, min_players, max_players, status, "
        "current_turn_user_id, state_json, message_id, created_at, updated_at, expires_at "
        "FROM game_tables WHERE table_id = ?",
        (table_id,),
        fetchone=True,
        conn=conn,
    )
    table = None
    if row:
        table = _table_row_to_dict(row)
        table["players"] = _get_players_for_table(table_id, conn)
        table["recent_actions"] = get_recent_table_actions(table_id, limit=6, conn=conn)
    if own_conn:
        conn.close()
    return table


def _log_table_action(table_id: str, user_id: str, action: str, payload: dict | None = None, *, now_ts: int | None = None, conn=None):
    economy.db_query(
        "INSERT INTO game_table_actions (table_id, user_id, action, payload_json, created_at) VALUES (?, ?, ?, ?, ?)",
        (table_id, str(user_id or ""), action, _json_dump(payload or {}), _now_ts(now_ts)),
        commit=True,
        conn=conn,
    )


def _save_table_row(table: dict, *, conn, now_ts: int | None = None):
    now_value = _now_ts(now_ts)
    economy.db_query(
        "UPDATE game_tables SET creator_id = ?, status = ?, current_turn_user_id = ?, state_json = ?, message_id = ?, updated_at = ?, expires_at = ? "
        "WHERE table_id = ?",
        (
            table["creator_id"],
            table["status"],
            table.get("current_turn_user_id", ""),
            _json_dump(table.get("state", {})),
            table.get("message_id", ""),
            now_value,
            int(table["expires_at"]),
            table["table_id"],
        ),
        commit=True,
        conn=conn,
    )
    table["updated_at"] = now_value


def _save_player_row(player: dict, *, conn):
    economy.db_query(
        "UPDATE game_table_players SET status = ?, hand_json = ?, hand_value = ?, is_natural = ?, result_text = ?, payout = ?, acted_at = ? "
        "WHERE table_id = ? AND user_id = ?",
        (
            player["status"],
            _json_dump(player.get("hand", [])),
            int(player.get("hand_value", 0)),
            1 if player.get("is_natural") else 0,
            player.get("result_text", ""),
            int(player.get("payout", 0)),
            int(player.get("acted_at", 0)),
            player["table_id"],
            player["user_id"],
        ),
        commit=True,
        conn=conn,
    )


def set_table_message_id(table_id: str, message_id: int | str, conn=None):
    own_conn = conn is None
    if own_conn:
        conn = economy.get_db()
    ensure_table_schema(conn)
    economy.db_query(
        "UPDATE game_tables SET message_id = ?, updated_at = ? WHERE table_id = ?",
        (str(message_id), _now_ts(), table_id),
        commit=True,
        conn=conn,
    )
    if own_conn:
        conn.close()


def get_user_active_table(user_id: str, conn=None) -> dict | None:
    own_conn = conn is None
    if own_conn:
        conn = economy.get_db()
    ensure_table_schema(conn)
    row = economy.db_query(
        "SELECT t.table_id FROM game_tables t "
        "JOIN game_table_players p ON p.table_id = t.table_id "
        "WHERE p.user_id = ? AND t.status IN (?, ?) "
        "ORDER BY t.created_at DESC LIMIT 1",
        (str(user_id), TABLE_WAITING, TABLE_IN_PROGRESS),
        fetchone=True,
        conn=conn,
    )
    table = get_table(row[0], conn=conn) if row else None
    if own_conn:
        conn.close()
    return table


def list_channel_tables(channel_id: str | int, conn=None, *, include_finished: bool = False) -> list[dict]:
    own_conn = conn is None
    if own_conn:
        conn = economy.get_db()
    ensure_table_schema(conn)
    statuses = [TABLE_WAITING, TABLE_IN_PROGRESS]
    if include_finished:
        statuses.extend([TABLE_FINISHED, TABLE_CANCELLED])
    placeholders = ",".join("?" for _ in statuses)
    rows = economy.db_query(
        f"SELECT table_id, game_type, guild_id, channel_id, creator_id, bet_amount, min_players, max_players, status, "
        f"current_turn_user_id, state_json, message_id, created_at, updated_at, expires_at "
        f"FROM game_tables WHERE channel_id = ? AND status IN ({placeholders}) ORDER BY created_at ASC",
        (str(channel_id), *statuses),
        fetchall=True,
        conn=conn,
    ) or []
    tables = []
    for row in rows:
        table = _table_row_to_dict(row)
        table["players"] = _get_players_for_table(table["table_id"], conn)
        table["recent_actions"] = get_recent_table_actions(table["table_id"], limit=4, conn=conn)
        tables.append(table)
    if own_conn:
        conn.close()
    return tables


def resolve_table_reference(table_ref: str, conn=None) -> dict | None:
    own_conn = conn is None
    if own_conn:
        conn = economy.get_db()
    ensure_table_schema(conn)
    table_ref = str(table_ref).strip()
    table = get_table(table_ref, conn=conn)
    if table is None:
        row = economy.db_query(
            "SELECT table_id FROM game_tables WHERE table_id LIKE ? ORDER BY created_at DESC LIMIT 1",
            (f"%{table_ref}",),
            fetchone=True,
            conn=conn,
        )
        table = get_table(row[0], conn=conn) if row else None
    if own_conn:
        conn.close()
    return table


def _get_joinable_blackjack_table(channel_id: str, guild_id: str, bet_amount: int, conn) -> dict | None:
    rows = economy.db_query(
        "SELECT table_id FROM game_tables WHERE game_type = ? AND channel_id = ? AND guild_id = ? "
        "AND bet_amount = ? AND status = ? ORDER BY created_at ASC",
        (BLACKJACK_GAME, str(channel_id), str(guild_id), int(bet_amount), TABLE_WAITING),
        fetchall=True,
        conn=conn,
    ) or []
    for (table_id,) in rows:
        table = get_table(table_id, conn=conn)
        if table and len(table["players"]) < table["max_players"]:
            return table
    return None


def create_blackjack_table(user_id: str, guild_id: str, channel_id: str, bet_amount: int, *, now_ts: int | None = None, conn=None) -> dict:
    ensure_table_schema(conn)
    current_time = _now_ts(now_ts)
    active = get_user_active_table(user_id, conn=conn)
    if active:
        raise ValueError("You are already seated at an active table.")

    success, pay_msg = economy.pay_jc(str(user_id), int(bet_amount), conn=conn)
    if not success:
        raise ValueError(pay_msg)

    table_id = f"bj-{secrets.token_hex(4)}"
    state = {
        "dealer_hand": [],
        "deck": [],
        "started_at": 0,
        "finished_at": 0,
        "last_result": "Waiting for more players...",
    }
    economy.db_query(
        "INSERT INTO game_tables (table_id, game_type, guild_id, channel_id, creator_id, bet_amount, min_players, max_players, status, current_turn_user_id, state_json, message_id, created_at, updated_at, expires_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '', ?, ?, ?)",
        (
            table_id,
            BLACKJACK_GAME,
            str(guild_id),
            str(channel_id),
            str(user_id),
            int(bet_amount),
            MIN_BLACKJACK_TABLE_PLAYERS,
            MAX_BLACKJACK_TABLE_PLAYERS,
            TABLE_WAITING,
            "",
            _json_dump(state),
            current_time,
            current_time,
            current_time + WAITING_TABLE_TIMEOUT,
        ),
        commit=True,
        conn=conn,
    )
    economy.db_query(
        "INSERT INTO game_table_players (table_id, user_id, seat, bet_amount, status, hand_json, hand_value, is_natural, result_text, payout, joined_at, acted_at) "
        "VALUES (?, ?, 0, ?, ?, '[]', 0, 0, ?, 0, ?, ?)",
        (table_id, str(user_id), int(bet_amount), PLAYER_JOINED, pay_msg, current_time, current_time),
        commit=True,
        conn=conn,
    )
    _log_table_action(table_id, str(user_id), "create", {"bet_amount": int(bet_amount)}, now_ts=current_time, conn=conn)
    return get_table(table_id, conn=conn)


def join_blackjack_table(table_id: str, user_id: str, *, now_ts: int | None = None, conn=None) -> dict:
    ensure_table_schema(conn)
    current_time = _now_ts(now_ts)
    table = get_table(table_id, conn=conn)
    if not table:
        raise ValueError("That table does not exist anymore.")
    if table["game_type"] != BLACKJACK_GAME:
        raise ValueError("That table is not a blackjack table.")
    if table["status"] != TABLE_WAITING:
        raise ValueError("That table is no longer accepting players.")
    if len(table["players"]) >= table["max_players"]:
        raise ValueError("That table is already full.")
    if any(player["user_id"] == str(user_id) for player in table["players"]):
        raise ValueError("You are already seated at that table.")

    active = get_user_active_table(user_id, conn=conn)
    if active:
        raise ValueError("You are already seated at an active table.")

    success, pay_msg = economy.pay_jc(str(user_id), int(table["bet_amount"]), conn=conn)
    if not success:
        raise ValueError(pay_msg)

    next_seat = max((player["seat"] for player in table["players"]), default=-1) + 1
    economy.db_query(
        "INSERT INTO game_table_players (table_id, user_id, seat, bet_amount, status, hand_json, hand_value, is_natural, result_text, payout, joined_at, acted_at) "
        "VALUES (?, ?, ?, ?, ?, '[]', 0, 0, ?, 0, ?, ?)",
        (table_id, str(user_id), next_seat, int(table["bet_amount"]), PLAYER_JOINED, pay_msg, current_time, current_time),
        commit=True,
        conn=conn,
    )

    table["expires_at"] = current_time + WAITING_TABLE_TIMEOUT
    table["state"]["last_result"] = "A new player joined the table."
    _save_table_row(table, conn=conn, now_ts=current_time)
    _log_table_action(table_id, str(user_id), "join", {"bet_amount": int(table["bet_amount"])}, now_ts=current_time, conn=conn)
    return get_table(table_id, conn=conn)


def create_or_join_blackjack_table(user_id: str, guild_id: str, channel_id: str, bet_amount: int, *, now_ts: int | None = None, conn=None) -> tuple[str, dict]:
    ensure_table_schema(conn)
    table = _get_joinable_blackjack_table(str(channel_id), str(guild_id), int(bet_amount), conn)
    if table:
        return "joined", join_blackjack_table(table["table_id"], user_id, now_ts=now_ts, conn=conn)
    return "created", create_blackjack_table(user_id, guild_id, channel_id, bet_amount, now_ts=now_ts, conn=conn)


def _get_player(table: dict, user_id: str) -> dict | None:
    return next((player for player in table["players"] if player["user_id"] == str(user_id)), None)


def _advance_turn(table: dict, *, after_user_id: str | None = None) -> str:
    ordered = sorted(table["players"], key=lambda item: item["seat"])
    active = [player["user_id"] for player in ordered if player["status"] == PLAYER_PLAYING]
    if not active:
        table["current_turn_user_id"] = ""
        return ""

    if after_user_id and after_user_id in [player["user_id"] for player in ordered]:
        seats = {player["user_id"]: idx for idx, player in enumerate(ordered)}
        start_index = seats[after_user_id] + 1
        for offset in range(len(ordered)):
            candidate = ordered[(start_index + offset) % len(ordered)]
            if candidate["status"] == PLAYER_PLAYING:
                table["current_turn_user_id"] = candidate["user_id"]
                return candidate["user_id"]

    table["current_turn_user_id"] = active[0]
    return active[0]


def start_blackjack_table(table_id: str, started_by_id: str, *, now_ts: int | None = None, conn=None, deck: list[str] | None = None) -> dict:
    ensure_table_schema(conn)
    current_time = _now_ts(now_ts)
    table = get_table(table_id, conn=conn)
    if not table:
        raise ValueError("That table does not exist anymore.")
    if table["status"] != TABLE_WAITING:
        raise ValueError("That table has already started.")
    if not any(player["user_id"] == str(started_by_id) for player in table["players"]):
        raise ValueError("You are not seated at that table.")
    if len(table["players"]) < table["min_players"]:
        raise ValueError(f"You need at least {table['min_players']} players to start this table.")

    state = table["state"]
    state["deck"] = list(deck) if deck is not None else create_blackjack_deck()
    state["dealer_hand"] = []
    state["started_at"] = current_time
    state["finished_at"] = 0
    state["last_result"] = "Cards dealt. Good luck!"

    for player in sorted(table["players"], key=lambda item: item["seat"]):
        hand = [_draw_card(state), _draw_card(state)]
        hand_value = calculate_blackjack_value(hand)
        is_natural = hand_value == 21
        player["hand"] = hand
        player["hand_value"] = hand_value
        player["is_natural"] = is_natural
        player["status"] = PLAYER_BLACKJACK if is_natural else PLAYER_PLAYING
        player["result_text"] = "Natural Blackjack!" if is_natural else "Waiting for turn"
        player["payout"] = 0
        player["acted_at"] = current_time
        _save_player_row(player, conn=conn)

    state["dealer_hand"] = [_draw_card(state), _draw_card(state)]
    table["status"] = TABLE_IN_PROGRESS
    table["current_turn_user_id"] = ""
    table["expires_at"] = current_time + TURN_TIMEOUT
    _advance_turn(table)
    _save_table_row(table, conn=conn, now_ts=current_time)
    _log_table_action(table_id, str(started_by_id), "start", {}, now_ts=current_time, conn=conn)

    if not table["current_turn_user_id"]:
        return _finish_blackjack_table(table_id, now_ts=current_time, conn=conn)
    return get_table(table_id, conn=conn)


def _resolve_standard_win(player: dict, *, conn):
    payout = int(player["bet_amount"] + (player["bet_amount"] * 0.9))
    tax_amount = int(player["bet_amount"] * 0.1)
    player["payout"] = payout
    player["result_text"] = f"Win +{payout:,} JC"
    economy.track_fee(tax_amount, conn=conn)
    economy.add_balance(player["user_id"], payout, conn=conn)
    economy.log_transaction(player["user_id"], payout, "Table Blackjack Win", conn=conn)
    economy.log_transaction(player["user_id"], -tax_amount, "Table Blackjack Tax", processed=1, conn=conn)
    economy.apply_progress_events(player["user_id"], {"blackjack_wins": 1, "gambling_wins": 1}, conn=conn)


def _resolve_natural_win(player: dict, *, conn):
    payout = int(player["bet_amount"] + (player["bet_amount"] * 1.2))
    tax_amount = int(player["bet_amount"] * 0.1)
    player["payout"] = payout
    player["result_text"] = f"Natural! +{payout:,} JC"
    economy.track_fee(tax_amount, conn=conn)
    economy.add_balance(player["user_id"], payout, conn=conn)
    economy.log_transaction(player["user_id"], payout, "Table Blackjack Win (Natural)", conn=conn)
    economy.log_transaction(player["user_id"], -tax_amount, "Table Blackjack Tax", processed=1, conn=conn)
    economy.apply_progress_events(player["user_id"], {"blackjack_wins": 1, "gambling_wins": 1}, conn=conn)


def _resolve_push(player: dict, *, conn):
    player["payout"] = int(player["bet_amount"])
    player["result_text"] = f"Push +{player['bet_amount']:,} JC"
    economy.add_balance(player["user_id"], player["bet_amount"], conn=conn)
    economy.log_transaction(player["user_id"], player["bet_amount"], "Table Blackjack Push", conn=conn)


def _resolve_loss(player: dict, *, conn, result_text: str = "Loss"):
    player["payout"] = 0
    player["result_text"] = result_text
    economy.track_fee(player["bet_amount"], conn=conn)
    economy.log_transaction(player["user_id"], -player["bet_amount"], "Table Blackjack Loss", processed=1, conn=conn)


def _finish_blackjack_table(table_id: str, *, now_ts: int | None = None, conn=None) -> dict:
    current_time = _now_ts(now_ts)
    table = get_table(table_id, conn=conn)
    if not table:
        raise ValueError("That table does not exist anymore.")

    state = table["state"]
    dealer_hand = list(state.get("dealer_hand") or [])
    while calculate_blackjack_value(dealer_hand) < 17:
        dealer_hand.append(_draw_card(state))
    dealer_value = calculate_blackjack_value(dealer_hand)
    dealer_natural = dealer_value == 21 and len(dealer_hand) == 2
    state["dealer_hand"] = dealer_hand
    state["finished_at"] = current_time

    winners = []
    pushes = []
    for player in table["players"]:
        player["hand_value"] = calculate_blackjack_value(player.get("hand", []))
        if player["status"] == PLAYER_LEFT:
            _resolve_loss(player, conn=conn, result_text="Forfeit")
        elif player["status"] == PLAYER_BUSTED:
            _resolve_loss(player, conn=conn, result_text="Bust")
        elif player["status"] == PLAYER_BLACKJACK:
            if dealer_natural:
                _resolve_push(player, conn=conn)
                pushes.append(player["user_id"])
            else:
                _resolve_natural_win(player, conn=conn)
                winners.append(player["user_id"])
        else:
            if dealer_value > 21:
                _resolve_standard_win(player, conn=conn)
                winners.append(player["user_id"])
            elif player["hand_value"] > dealer_value:
                _resolve_standard_win(player, conn=conn)
                winners.append(player["user_id"])
            elif player["hand_value"] == dealer_value:
                _resolve_push(player, conn=conn)
                pushes.append(player["user_id"])
            else:
                _resolve_loss(player, conn=conn)
        player["acted_at"] = current_time
        _save_player_row(player, conn=conn)

    table["status"] = TABLE_FINISHED
    table["current_turn_user_id"] = ""
    table["expires_at"] = current_time + FINISHED_TABLE_TIMEOUT
    state["last_result"] = (
        f"Round finished. Dealer shows {dealer_value}. "
        f"Winners: {len(winners)} | Pushes: {len(pushes)}"
    )
    _save_table_row(table, conn=conn, now_ts=current_time)
    _log_table_action(table_id, "", "finish", {"dealer_value": dealer_value}, now_ts=current_time, conn=conn)
    return get_table(table_id, conn=conn)


def process_blackjack_action(table_id: str, user_id: str, action: str, *, now_ts: int | None = None, conn=None) -> dict:
    ensure_table_schema(conn)
    current_time = _now_ts(now_ts)
    table = get_table(table_id, conn=conn)
    if not table:
        raise ValueError("That table does not exist anymore.")
    if table["status"] != TABLE_IN_PROGRESS:
        raise ValueError("That table is not in progress.")
    if table["current_turn_user_id"] != str(user_id):
        raise ValueError("It is not your turn.")

    player = _get_player(table, user_id)
    if not player:
        raise ValueError("You are not seated at that table.")
    if player["status"] != PLAYER_PLAYING:
        raise ValueError("You cannot act right now.")

    state = table["state"]
    action = str(action).lower().strip()
    if action == "hit":
        drawn = _draw_card(state)
        player["hand"].append(drawn)
        player["hand_value"] = calculate_blackjack_value(player["hand"])
        player["acted_at"] = current_time
        if player["hand_value"] > 21:
            player["status"] = PLAYER_BUSTED
            player["result_text"] = f"Bust on {player['hand_value']}"
            _advance_turn(table, after_user_id=user_id)
            state["last_result"] = f"Seat {player['seat'] + 1} busted after drawing {drawn}."
        elif player["hand_value"] == 21:
            player["status"] = PLAYER_STOOD
            player["result_text"] = f"Locked 21 with {drawn}"
            _advance_turn(table, after_user_id=user_id)
            state["last_result"] = f"Seat {player['seat'] + 1} made 21."
        else:
            table["current_turn_user_id"] = str(user_id)
            player["result_text"] = f"Hit {drawn} → {player['hand_value']}"
            state["last_result"] = f"Seat {player['seat'] + 1} drew {drawn}."
    elif action == "stand":
        player["status"] = PLAYER_STOOD
        player["result_text"] = f"Stand on {player['hand_value']}"
        player["acted_at"] = current_time
        _advance_turn(table, after_user_id=user_id)
        state["last_result"] = f"Seat {player['seat'] + 1} stood on {player['hand_value']}."
    else:
        raise ValueError("Unknown action.")

    _save_player_row(player, conn=conn)
    if table["current_turn_user_id"]:
        table["expires_at"] = current_time + TURN_TIMEOUT
        _save_table_row(table, conn=conn, now_ts=current_time)
        _log_table_action(table_id, str(user_id), action, {}, now_ts=current_time, conn=conn)
        return get_table(table_id, conn=conn)

    _save_table_row(table, conn=conn, now_ts=current_time)
    _log_table_action(table_id, str(user_id), action, {}, now_ts=current_time, conn=conn)
    return _finish_blackjack_table(table_id, now_ts=current_time, conn=conn)


def leave_blackjack_table(table_id: str, user_id: str, *, now_ts: int | None = None, conn=None) -> dict:
    ensure_table_schema(conn)
    current_time = _now_ts(now_ts)
    table = get_table(table_id, conn=conn)
    if not table:
        raise ValueError("That table does not exist anymore.")

    player = _get_player(table, user_id)
    if not player:
        raise ValueError("You are not seated at that table.")

    if table["status"] == TABLE_WAITING:
        economy.add_balance(str(user_id), player["bet_amount"], conn=conn)
        economy.log_transaction(str(user_id), player["bet_amount"], "Table Blackjack Cancelled (Refund)", conn=conn)
        economy.db_query(
            "DELETE FROM game_table_players WHERE table_id = ? AND user_id = ?",
            (table_id, str(user_id)),
            commit=True,
            conn=conn,
        )
        remaining_players = _get_players_for_table(table_id, conn)
        if remaining_players:
            table["creator_id"] = remaining_players[0]["user_id"]
            table["expires_at"] = current_time + WAITING_TABLE_TIMEOUT
            table["state"]["last_result"] = "A player left before the game started."
            _save_table_row(table, conn=conn, now_ts=current_time)
        else:
            table["status"] = TABLE_CANCELLED
            table["creator_id"] = str(user_id)
            table["current_turn_user_id"] = ""
            table["expires_at"] = current_time + 60
            table["state"]["last_result"] = "Table cancelled. Everyone left before the round started."
            _save_table_row(table, conn=conn, now_ts=current_time)
        _log_table_action(table_id, str(user_id), "leave", {"phase": table["status"]}, now_ts=current_time, conn=conn)
        return get_table(table_id, conn=conn)

    if table["status"] != TABLE_IN_PROGRESS:
        raise ValueError("That table is already over.")

    player["status"] = PLAYER_LEFT
    player["result_text"] = "Left table"
    player["acted_at"] = current_time
    _save_player_row(player, conn=conn)
    _log_table_action(table_id, str(user_id), "leave", {"phase": TABLE_IN_PROGRESS}, now_ts=current_time, conn=conn)

    if table["current_turn_user_id"] == str(user_id):
        _advance_turn(table, after_user_id=user_id)

    if table["current_turn_user_id"]:
        table["state"]["last_result"] = "A player forfeited their hand."
        table["expires_at"] = current_time + TURN_TIMEOUT
        _save_table_row(table, conn=conn, now_ts=current_time)
        return get_table(table_id, conn=conn)

    table["state"]["last_result"] = "Last active player forfeited. Settling table."
    _save_table_row(table, conn=conn, now_ts=current_time)
    return _finish_blackjack_table(table_id, now_ts=current_time, conn=conn)


def cancel_blackjack_table(table_id: str, *, reason: str, now_ts: int | None = None, conn=None) -> dict | None:
    ensure_table_schema(conn)
    current_time = _now_ts(now_ts)
    table = get_table(table_id, conn=conn)
    if not table:
        return None

    if table["status"] == TABLE_WAITING:
        for player in table["players"]:
            economy.add_balance(player["user_id"], player["bet_amount"], conn=conn)
            economy.log_transaction(player["user_id"], player["bet_amount"], "Table Blackjack Cancelled (Refund)", conn=conn)
        table["status"] = TABLE_CANCELLED
        table["current_turn_user_id"] = ""
        table["expires_at"] = current_time + 60
        table["state"]["last_result"] = f"Table cancelled: {reason}."
        _save_table_row(table, conn=conn, now_ts=current_time)
        _log_table_action(table_id, "", "cancel", {"reason": reason}, now_ts=current_time, conn=conn)
        return get_table(table_id, conn=conn)
    return table


def delete_table(table_id: str, conn=None):
    ensure_table_schema(conn)
    economy.db_query("DELETE FROM game_table_actions WHERE table_id = ?", (table_id,), commit=True, conn=conn)
    economy.db_query("DELETE FROM game_table_players WHERE table_id = ?", (table_id,), commit=True, conn=conn)
    economy.db_query("DELETE FROM game_tables WHERE table_id = ?", (table_id,), commit=True, conn=conn)


def run_table_maintenance(*, now_ts: int | None = None, conn=None) -> dict:
    ensure_table_schema(conn)
    current_time = _now_ts(now_ts)
    result = {"changed": [], "deleted": []}

    waiting_rows = economy.db_query(
        "SELECT table_id FROM game_tables WHERE game_type = ? AND status = ? AND expires_at <= ?",
        (BLACKJACK_GAME, TABLE_WAITING, current_time),
        fetchall=True,
        conn=conn,
    ) or []
    for (table_id,) in waiting_rows:
        table = cancel_blackjack_table(table_id, reason="waiting timeout", now_ts=current_time, conn=conn)
        if table:
            result["changed"].append(table["table_id"])

    in_progress_rows = economy.db_query(
        "SELECT table_id, current_turn_user_id FROM game_tables WHERE game_type = ? AND status = ? AND expires_at <= ?",
        (BLACKJACK_GAME, TABLE_IN_PROGRESS, current_time),
        fetchall=True,
        conn=conn,
    ) or []
    for table_id, current_turn_user_id in in_progress_rows:
        if current_turn_user_id:
            table = process_blackjack_action(table_id, current_turn_user_id, "stand", now_ts=current_time, conn=conn)
            if table:
                table["state"]["last_result"] = "Turn timer expired. Auto-stand applied."
                _save_table_row(table, conn=conn, now_ts=current_time)
                result["changed"].append(table_id)

    expired_rows = economy.db_query(
        "SELECT table_id FROM game_tables WHERE game_type = ? AND status IN (?, ?) AND expires_at <= ?",
        (BLACKJACK_GAME, TABLE_FINISHED, TABLE_CANCELLED, current_time),
        fetchall=True,
        conn=conn,
    ) or []
    for (table_id,) in expired_rows:
        delete_table(table_id, conn=conn)
        result["deleted"].append(table_id)

    return result


class BlackjackTableView(discord.ui.View):
    def __init__(self, cog: "TableGames", table: dict):
        super().__init__(timeout=180)
        self.cog = cog
        self.table_id = table["table_id"]
        self.status = table["status"]

        if self.status != TABLE_WAITING:
            self.join_button.disabled = True
            self.start_button.disabled = True

        if self.status != TABLE_IN_PROGRESS:
            self.hit_button.disabled = True
            self.stand_button.disabled = True

        if self.status in {TABLE_FINISHED, TABLE_CANCELLED}:
            self.leave_button.disabled = True

    @discord.ui.button(label="Join", style=discord.ButtonStyle.green, emoji="🎟️")
    async def join_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.handle_join_interaction(interaction, self.table_id)

    @discord.ui.button(label="Start", style=discord.ButtonStyle.blurple, emoji="▶️")
    async def start_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.handle_start_interaction(interaction, self.table_id)

    @discord.ui.button(label="Hit", style=discord.ButtonStyle.green, emoji="➕")
    async def hit_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.handle_action_interaction(interaction, self.table_id, "hit")

    @discord.ui.button(label="Stand", style=discord.ButtonStyle.secondary, emoji="🛑")
    async def stand_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.handle_action_interaction(interaction, self.table_id, "stand")

    @discord.ui.button(label="Leave", style=discord.ButtonStyle.red, emoji="🚪")
    async def leave_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.handle_leave_interaction(interaction, self.table_id)

    @discord.ui.button(label="Refresh", style=discord.ButtonStyle.secondary, emoji="🔄")
    async def refresh_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        await self.cog.refresh_table_message(self.table_id)


class TableGames(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        with economy.db_transaction() as conn:
            ensure_table_schema(conn)
        self.table_maintenance_task.start()

    def cog_unload(self):
        self.table_maintenance_task.cancel()

    @tasks.loop(seconds=15)
    async def table_maintenance_task(self):
        await self.bot.wait_until_ready()
        with economy.db_transaction() as conn:
            maintenance = run_table_maintenance(conn=conn)
        for table_id in maintenance["changed"]:
            try:
                await self.refresh_table_message(table_id)
            except Exception:
                pass

    @table_maintenance_task.before_loop
    async def before_table_maintenance_task(self):
        await self.bot.wait_until_ready()

    def _display_name(self, guild: discord.Guild | None, user_id: str) -> str:
        if guild:
            member = guild.get_member(int(user_id))
            if member:
                return member.display_name
        user = self.bot.get_user(int(user_id))
        if user:
            return user.display_name
        return f"User {user_id}"

    def _format_recent_actions(self, table: dict, guild: discord.Guild | None) -> str:
        lines = []
        for action in table.get("recent_actions", [])[-4:]:
            actor = self._display_name(guild, action["user_id"]) if action["user_id"] else "System"
            label = action["action"].replace("_", " ").title()
            lines.append(f"• {actor}: {label}")
        return "\n".join(lines) if lines else "No actions yet."

    def _format_player_line(self, guild: discord.Guild | None, player: dict, table: dict) -> str:
        name = self._display_name(guild, player["user_id"])
        turn_marker = " ← turn" if table.get("current_turn_user_id") == player["user_id"] and table["status"] == TABLE_IN_PROGRESS else ""
        if table["status"] == TABLE_WAITING:
            return f"`S{player['seat'] + 1}` **{name}** — ready with **{player['bet_amount']:,} JC**"

        hand = " | ".join(player["hand"]) if player["hand"] else "—"
        suffix = f"{turn_marker} | {player['status'].replace('_', ' ').title()}"
        if table["status"] == TABLE_FINISHED:
            suffix = f" | {player['result_text'] or player['status'].title()}"
            if player["payout"] > 0:
                suffix += f" | payout **{player['payout']:,} JC**"
        elif player["result_text"]:
            suffix += f" | {player['result_text']}"
        return f"`S{player['seat'] + 1}` **{name}** — {hand} (**{player['hand_value']}**) {suffix}"

    def build_table_embed(self, table: dict, guild: discord.Guild | None = None) -> discord.Embed:
        color_map = {
            TABLE_WAITING: discord.Color.blurple(),
            TABLE_IN_PROGRESS: discord.Color.gold(),
            TABLE_FINISHED: discord.Color.green(),
            TABLE_CANCELLED: discord.Color.red(),
        }
        short_id = table["table_id"][-6:]
        embed = discord.Embed(
            title=f"🎴 Blackjack Table #{short_id}",
            color=color_map.get(table["status"], discord.Color.blurple()),
        )

        status_text = {
            TABLE_WAITING: "Waiting for players",
            TABLE_IN_PROGRESS: "In progress",
            TABLE_FINISHED: "Finished",
            TABLE_CANCELLED: "Cancelled",
        }.get(table["status"], table["status"].title())
        embed.description = (
            f"**Status:** {status_text}\n"
            f"**Bet:** {table['bet_amount']:,} JC per player\n"
            f"**Players:** {len(table['players'])}/{table['max_players']}\n"
            f"**Rules:** Shared table, JenBot house payouts, same tax model as `{COMMAND_PREFIX}bj`."
        )

        state = table.get("state", {})
        dealer_hand = list(state.get("dealer_hand") or [])
        if table["status"] in {TABLE_FINISHED, TABLE_CANCELLED}:
            dealer_text = " | ".join(dealer_hand) if dealer_hand else "—"
            if dealer_hand:
                dealer_text += f" (**{calculate_blackjack_value(dealer_hand)}**)"
        elif dealer_hand:
            dealer_text = f"{dealer_hand[0]} | ❓"
        else:
            dealer_text = "Cards not dealt yet."
        embed.add_field(name="Dealer", value=dealer_text, inline=False)

        player_lines = [self._format_player_line(guild, player, table) for player in table["players"]]
        embed.add_field(name="Players", value="\n".join(player_lines)[:1024] or "No players", inline=False)

        if table["status"] == TABLE_IN_PROGRESS and table.get("current_turn_user_id"):
            turn_name = self._display_name(guild, table["current_turn_user_id"])
            embed.add_field(name="Turn", value=f"**{turn_name}** — auto-stand <t:{table['expires_at']}:R>", inline=False)
        elif table["status"] == TABLE_WAITING:
            embed.add_field(name="Start Window", value=f"Table expires <t:{table['expires_at']}:R> if nobody starts.", inline=False)

        recent_text = (state.get("last_result") or "No recent updates.")[:512]
        recent_actions = self._format_recent_actions(table, guild)[:400]
        embed.add_field(name="Recent Activity", value=f"{recent_text}\n{recent_actions}", inline=False)
        embed.set_footer(text=f"Use {COMMAND_PREFIX}bjtable <bet> to create/join • {COMMAND_PREFIX}table leave to exit")
        return embed

    async def refresh_table_message(self, table_id: str):
        with economy.db_transaction() as conn:
            table = get_table(table_id, conn=conn)
        if not table or not table.get("message_id"):
            return

        channel = self.bot.get_channel(int(table["channel_id"]))
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(int(table["channel_id"]))
            except Exception:
                return

        try:
            message = await channel.fetch_message(int(table["message_id"]))
        except Exception:
            return

        await message.edit(
            embed=self.build_table_embed(table, getattr(channel, "guild", None)),
            view=BlackjackTableView(self, table),
        )

    async def _create_or_join_via_command(self, ctx: commands.Context, amount: int):
        with economy.db_transaction() as conn:
            action, table = create_or_join_blackjack_table(
                str(ctx.author.id),
                str(ctx.guild.id if ctx.guild else 0),
                str(ctx.channel.id),
                int(amount),
                conn=conn,
            )

        if action == "created":
            message = await ctx.send(
                f"🎴 {ctx.author.mention} opened Blackjack Table **#{table['table_id'][-6:]}** for **{amount:,} JC**. Click **Join** to sit.",
                embed=self.build_table_embed(table, ctx.guild),
                view=BlackjackTableView(self, table),
            )
            with economy.db_transaction() as conn:
                set_table_message_id(table["table_id"], message.id, conn=conn)
            return

        await self.refresh_table_message(table["table_id"])
        await ctx.send(f"🎟️ {ctx.author.mention} joined Blackjack Table **#{table['table_id'][-6:]}** for **{amount:,} JC**.")

    async def handle_join_interaction(self, interaction: discord.Interaction, table_id: str):
        try:
            with economy.db_transaction() as conn:
                table = join_blackjack_table(table_id, str(interaction.user.id), conn=conn)
        except ValueError as exc:
            await interaction.response.send_message(f"❌ {exc}", ephemeral=True)
            return

        await interaction.response.send_message(
            f"🎟️ You joined table **#{table['table_id'][-6:]}** for **{table['bet_amount']:,} JC**.",
            ephemeral=True,
        )
        await self.refresh_table_message(table_id)

    async def handle_start_interaction(self, interaction: discord.Interaction, table_id: str):
        try:
            with economy.db_transaction() as conn:
                table = start_blackjack_table(table_id, str(interaction.user.id), conn=conn)
        except ValueError as exc:
            await interaction.response.send_message(f"❌ {exc}", ephemeral=True)
            return

        await interaction.response.defer()
        await self.refresh_table_message(table["table_id"])

    async def handle_action_interaction(self, interaction: discord.Interaction, table_id: str, action: str):
        try:
            with economy.db_transaction() as conn:
                table = process_blackjack_action(table_id, str(interaction.user.id), action, conn=conn)
        except ValueError as exc:
            await interaction.response.send_message(f"❌ {exc}", ephemeral=True)
            return

        await interaction.response.defer()
        await self.refresh_table_message(table["table_id"])

    async def handle_leave_interaction(self, interaction: discord.Interaction, table_id: str):
        try:
            with economy.db_transaction() as conn:
                table = leave_blackjack_table(table_id, str(interaction.user.id), conn=conn)
        except ValueError as exc:
            await interaction.response.send_message(f"❌ {exc}", ephemeral=True)
            return

        await interaction.response.send_message("🚪 You left the table.", ephemeral=True)
        await self.refresh_table_message(table["table_id"])

    @commands.command(name="bjtable", aliases=["tablebj", "multibj"])
    async def bjtable_command(self, ctx: commands.Context, amount: str = None):
        """Create or join a multiplayer blackjack table in this channel."""
        value, error = await economy.validate_bet(ctx, amount)
        if error:
            await ctx.send(error)
            return
        await self._create_or_join_via_command(ctx, value)

    @commands.group(name="table", invoke_without_command=True)
    async def table_group(self, ctx: commands.Context):
        """Show your active table or list active tables in this channel."""
        with economy.db_transaction() as conn:
            active = get_user_active_table(str(ctx.author.id), conn=conn)
            channel_tables = list_channel_tables(ctx.channel.id, conn=conn)

        if active:
            await ctx.send(embed=self.build_table_embed(active, ctx.guild), view=BlackjackTableView(self, active))
            return

        if not channel_tables:
            await ctx.send(f"No active table in this channel. Start one with `{COMMAND_PREFIX}bjtable <bet>`.")
            return

        lines = [
            f"• **#{table['table_id'][-6:]}** — `{table['status']}` — {len(table['players'])}/{table['max_players']} players — **{table['bet_amount']:,} JC**"
            for table in channel_tables[:10]
        ]
        embed = discord.Embed(title="🎴 Active Blackjack Tables", description="\n".join(lines), color=discord.Color.blurple())
        embed.set_footer(text=f"Use {COMMAND_PREFIX}table show <id> or click the table message buttons.")
        await ctx.send(embed=embed)

    @table_group.command(name="list", aliases=["ls"])
    async def table_list_command(self, ctx: commands.Context):
        with economy.db_transaction() as conn:
            tables = list_channel_tables(ctx.channel.id, conn=conn)
        if not tables:
            await ctx.send(f"No active table in this channel. Use `{COMMAND_PREFIX}bjtable <bet>`.")
            return
        lines = [
            f"• **#{table['table_id'][-6:]}** — `{table['status']}` — {len(table['players'])}/{table['max_players']} players — **{table['bet_amount']:,} JC**"
            for table in tables[:10]
        ]
        await ctx.send("\n".join(lines))

    @table_group.command(name="show", aliases=["status"])
    async def table_show_command(self, ctx: commands.Context, table_id: str = None):
        if not table_id:
            await ctx.send(f"Usage: `{COMMAND_PREFIX}table show <table_id>`")
            return
        with economy.db_transaction() as conn:
            table = resolve_table_reference(table_id, conn=conn)
        if not table:
            await ctx.send("❌ That table doesn't exist.")
            return
        await ctx.send(embed=self.build_table_embed(table, ctx.guild), view=BlackjackTableView(self, table))

    @table_group.command(name="leave")
    async def table_leave_command(self, ctx: commands.Context, table_id: str = None):
        with economy.db_transaction() as conn:
            active = get_user_active_table(str(ctx.author.id), conn=conn) if not table_id else resolve_table_reference(table_id, conn=conn)
        if not active:
            await ctx.send("❌ You are not seated at an active table.")
            return

        try:
            with economy.db_transaction() as conn:
                table = leave_blackjack_table(active["table_id"], str(ctx.author.id), conn=conn)
        except ValueError as exc:
            await ctx.send(f"❌ {exc}")
            return

        await self.refresh_table_message(table["table_id"])
        await ctx.send(f"🚪 {ctx.author.mention} left table **#{table['table_id'][-6:]}**.")


async def setup(bot):
    await bot.add_cog(TableGames(bot))
