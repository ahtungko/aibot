from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import sys
import time
from pathlib import Path

MINES_LOSS_TX = "Mines Loss"
REFUND_MINES_LOSS_TX = f"Refund: {MINES_LOSS_TX}"
FEE_VAULT_KEY = "fee_vault"
FIX_MARKER_KEY = "maintenance_mines_vault_fix_v1"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Recalculate the global fee vault after the historical bug where `Mines Loss` "
            "incorrectly moved JC into the vault. Dry-run by default."
        )
    )
    parser.add_argument(
        "--db",
        default=str(Path(__file__).resolve().parents[1] / "economy.db"),
        help="Path to the SQLite database. Defaults to the repo economy.db.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply the correction to `settings.fee_vault` and store a maintenance marker.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Allow apply even if this maintenance marker already exists.",
    )
    parser.add_argument(
        "--show-ids",
        action="store_true",
        help="Print the impacted transaction IDs for audit/review.",
    )
    return parser.parse_args()


def connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def ensure_settings_table(conn: sqlite3.Connection) -> None:
    conn.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)")


def get_setting(conn: sqlite3.Connection, key: str, default: str | None = None) -> str | None:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row[0] if row else default


def set_setting(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )


def read_fee_vault(conn: sqlite3.Connection) -> int:
    raw_value = get_setting(conn, FEE_VAULT_KEY, "0")
    try:
        return int(float(raw_value or "0"))
    except (TypeError, ValueError):
        return 0


def fetch_impacted_rows(conn: sqlite3.Connection, tx_type: str, *, positive: bool | None = None) -> list[sqlite3.Row]:
    sql = "SELECT id, user_id, amount, timestamp FROM transactions WHERE type = ?"
    params: list[object] = [tx_type]
    if positive is True:
        sql += " AND amount > 0"
    elif positive is False:
        sql += " AND amount < 0"
    sql += " ORDER BY id"
    return list(conn.execute(sql, params).fetchall())


def build_report(conn: sqlite3.Connection) -> dict:
    loss_rows = fetch_impacted_rows(conn, MINES_LOSS_TX, positive=False)
    refund_rows = fetch_impacted_rows(conn, REFUND_MINES_LOSS_TX, positive=True)

    loss_total = sum(abs(int(row["amount"])) for row in loss_rows)
    refund_total = sum(int(row["amount"]) for row in refund_rows)
    vault_delta = refund_total - loss_total
    current_vault = read_fee_vault(conn)
    corrected_vault = current_vault + vault_delta

    return {
        "loss_rows": loss_rows,
        "refund_rows": refund_rows,
        "loss_count": len(loss_rows),
        "refund_count": len(refund_rows),
        "loss_total": loss_total,
        "refund_total": refund_total,
        "vault_delta": vault_delta,
        "current_vault": current_vault,
        "corrected_vault": corrected_vault,
    }


def print_report(report: dict, *, show_ids: bool) -> None:
    print("=== Mines Vault Recalculation ===")
    print(f"Mines loss rows        : {report['loss_count']}")
    print(f"Mines loss total       : {report['loss_total']:,} JC")
    print(f"Refund: Mines Loss rows: {report['refund_count']}")
    print(f"Refund total           : {report['refund_total']:,} JC")
    print(f"Vault delta to apply   : {report['vault_delta']:+,} JC")
    print(f"Current fee_vault      : {report['current_vault']:,} JC")
    print(f"Corrected fee_vault    : {report['corrected_vault']:,} JC")

    if report["corrected_vault"] < 0:
        print("WARNING: corrected fee_vault would be negative. This usually means the bad Mines vault inflow was already spent/drained.")

    if show_ids:
        loss_ids = ", ".join(str(row["id"]) for row in report["loss_rows"]) or "None"
        refund_ids = ", ".join(str(row["id"]) for row in report["refund_rows"]) or "None"
        print(f"Loss IDs               : {loss_ids}")
        print(f"Refund IDs             : {refund_ids}")


def make_backup(db_path: Path) -> Path:
    timestamp = time.strftime("%Y%m%d-%H%M%S")
    backup_path = db_path.with_name(f"{db_path.stem}.mines-vault-backup.{timestamp}{db_path.suffix}")
    shutil.copy2(db_path, backup_path)
    return backup_path


def apply_fix(db_path: Path, *, force: bool, show_ids: bool) -> int:
    with connect(db_path) as conn:
        ensure_settings_table(conn)
        marker_raw = get_setting(conn, FIX_MARKER_KEY)
        if marker_raw and not force:
            print("ERROR: maintenance marker already exists. Use --force only if you intentionally want to re-apply.")
            return 1

    backup_path = make_backup(db_path)
    print(f"Backup created         : {backup_path}")

    with connect(db_path) as conn:
        ensure_settings_table(conn)
        conn.execute("BEGIN IMMEDIATE")
        report = build_report(conn)
        print_report(report, show_ids=show_ids)

        set_setting(conn, FEE_VAULT_KEY, str(report["corrected_vault"]))
        marker_payload = {
            "applied_at": int(time.time()),
            "vault_delta": report["vault_delta"],
            "loss_count": report["loss_count"],
            "loss_total": report["loss_total"],
            "refund_count": report["refund_count"],
            "refund_total": report["refund_total"],
            "vault_before": report["current_vault"],
            "vault_after": report["corrected_vault"],
            "backup_path": str(backup_path),
        }
        set_setting(conn, FIX_MARKER_KEY, json.dumps(marker_payload, sort_keys=True))
        conn.commit()

    print("APPLY MODE             : complete")
    return 0


def main() -> int:
    args = parse_args()
    db_path = Path(args.db).expanduser().resolve()

    if not db_path.exists():
        print(f"ERROR: database not found: {db_path}")
        return 1

    with connect(db_path) as conn:
        ensure_settings_table(conn)
        marker_raw = get_setting(conn, FIX_MARKER_KEY)
        report = build_report(conn)
        print_report(report, show_ids=args.show_ids)

        if marker_raw:
            print(f"Existing marker        : {marker_raw}")

    if not args.apply:
        print("MODE                   : dry-run (no changes written)")
        print("Use `--apply` to write the corrected fee_vault value.")
        return 0

    return apply_fix(db_path, force=args.force, show_ids=args.show_ids)


if __name__ == "__main__":
    sys.exit(main())
