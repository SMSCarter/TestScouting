import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(__file__), "sabreos_server.db")

def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS matches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            team_number TEXT NOT NULL,
            match_number TEXT NOT NULL,
            auto_score INTEGER NOT NULL,
            teleop_cycles INTEGER NOT NULL,
            scout_initials TEXT DEFAULT '',
            endgame_status TEXT DEFAULT 'None',
            endgame_points INTEGER DEFAULT 0,
            scout_calculated_score INTEGER NOT NULL,
            tba_official_score INTEGER,
            requires_review INTEGER DEFAULT 0,
            discrepancy_percentage REAL,
            resolved_override INTEGER DEFAULT 0,
            created_at TEXT,
            synced_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(team_number, match_number)
        )
    """)
    
    # Safe migrations for existing databases
    for col, col_type in [("scout_initials", "TEXT DEFAULT ''"), 
                          ("endgame_status", "TEXT DEFAULT 'None'"), 
                          ("endgame_points", "INTEGER DEFAULT 0"), 
                          ("resolved_override", "INTEGER DEFAULT 0")]:
        try:
            cursor.execute(f"ALTER TABLE matches ADD COLUMN {col} {col_type}")
        except Exception:
            pass
            
    conn.commit()
    conn.close()

def save_match_records(records):
    """
    Saves or updates a batch of match records received from scouts.
    Computes scout_calculated_score for each record, factoring in auto, teleop, and endgame points.
    """
    conn = get_connection()
    cursor = conn.cursor()
    saved_ids = []
    
    for r in records:
        team_number = r.get("team_number")
        match_number = r.get("match_number")
        auto_score = int(r.get("auto_score", 0))
        teleop_cycles = int(r.get("teleop_cycles", 0))
        scout_initials = r.get("scout_initials", "")
        endgame_status = r.get("endgame_status", "None")
        created_at = r.get("created_at")
        
        # Determine endgame climbing points: Parked (2p), Climbed (3p), Harmony (4p), None (0p)
        status_lower = endgame_status.lower()
        if "park" in status_lower:
            endgame_points = 2
        elif "harmony" in status_lower:
            endgame_points = 4
        elif "climb" in status_lower:
            endgame_points = 3
        else:
            endgame_points = 0
        
        # Calculate scouted score: Auto + (Teleop Cycles * 3) + Endgame Climb Points
        scout_calculated_score = auto_score + (teleop_cycles * 3) + endgame_points
        
        # Insert or replace (upsert logic)
        cursor.execute("""
            INSERT INTO matches (
                team_number, match_number, auto_score, teleop_cycles, scout_initials, 
                endgame_status, endgame_points, scout_calculated_score, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(team_number, match_number) DO UPDATE SET
                auto_score = excluded.auto_score,
                teleop_cycles = excluded.teleop_cycles,
                scout_initials = excluded.scout_initials,
                endgame_status = excluded.endgame_status,
                endgame_points = excluded.endgame_points,
                scout_calculated_score = excluded.scout_calculated_score,
                created_at = excluded.created_at,
                synced_at = CURRENT_TIMESTAMP
        """, (team_number, match_number, auto_score, teleop_cycles, scout_initials, 
              endgame_status, endgame_points, scout_calculated_score, created_at))
        
        # Retrieve the updated/inserted row ID
        cursor.execute(
            "SELECT id FROM matches WHERE team_number = ? AND match_number = ?", 
            (team_number, match_number)
        )
        row = cursor.fetchone()
        if row:
            saved_ids.append(row["id"])
            
    conn.commit()
    conn.close()
    return saved_ids

def update_verification_status(match_id, tba_score, requires_review, discrepancy_pct):
    conn = get_connection()
    cursor = conn.cursor()
    # Only update requires_review if the match has not been manual-overridden/resolved by a lead scout
    cursor.execute("""
        UPDATE matches
        SET tba_official_score = ?,
            requires_review = CASE WHEN resolved_override = 1 THEN 0 ELSE ? END,
            discrepancy_percentage = ?
        WHERE id = ?
    """, (tba_score, 1 if requires_review else 0, discrepancy_pct, match_id))
    conn.commit()
    conn.close()

def resolve_flag_override(match_id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE matches
        SET resolved_override = 1,
            requires_review = 0
        WHERE id = ?
    """, (match_id,))
    conn.commit()
    conn.close()

def get_all_matches():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM matches ORDER BY match_number ASC, team_number ASC")
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]

def get_match_by_id(match_id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM matches WHERE id = ?", (match_id,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None
