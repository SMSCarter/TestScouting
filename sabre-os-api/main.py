from fastapi import FastAPI, BackgroundTasks, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from typing import List, Optional
import database
import tba
import logging
import math

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("SabreOS_API")

app = FastAPI(title="SabreOS API Server", version="1.0.0")

# Enable CORS for React Native mobile connections
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class ScoutRecord(BaseModel):
    team_number: str
    match_number: str
    auto_score: int
    teleop_cycles: int
    scout_initials: str
    endgame_status: str
    created_at: Optional[str] = None

class SyncPayload(BaseModel):
    records: List[ScoutRecord]

class PredictionPayload(BaseModel):
    red_teams: List[str]
    blue_teams: List[str]

# Startup event: Initialize Database
@app.on_event("startup")
def startup_event():
    logger.info("Initializing SabreOS Server SQLite Database...")
    database.init_db()
    logger.info("Database initialized successfully.")

@app.get("/health")
def health_check():
    return {"status": "healthy", "service": "SabreOS Backend"}

@app.post("/sync")
def sync_records(payload: SyncPayload, background_tasks: BackgroundTasks):
    """
    Endpoint for mobile devices to POST batched match data.
    Saves payloads to database and triggers the TBA Discrepancy Engine in the background.
    """
    logger.info(f"Received sync batch with {len(payload.records)} records.")
    
    if not payload.records:
        raise HTTPException(status_code=400, detail="Empty sync payload")
        
    try:
        # Convert Pydantic models to dicts
        records_dict = [r.dict() for r in payload.records]
        
        # Save to database (computes scout_calculated_score internally)
        saved_ids = database.save_match_records(records_dict)
        logger.info(f"Successfully saved {len(saved_ids)} records to SQLite.")
        
        # Queue the TBA verification as background tasks so response remains fast
        for match_id in saved_ids:
            background_tasks.add_task(tba.verify_and_update_match, match_id)
            
        return {"status": "success", "message": f"Synced {len(saved_ids)} records. Verification scheduled."}
        
    except Exception as e:
        logger.error(f"Sync error: {e}")
        raise HTTPException(status_code=500, detail=f"Database synchronization error: {str(e)}")

@app.get("/matches")
def get_matches():
    """
    Returns all match records in the database, with TBA scores and discrepancy review flags.
    """
    try:
        records = database.get_all_matches()
        return {
            "count": len(records),
            "records": records
        }
    except Exception as e:
        logger.error(f"Error fetching matches: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/resolve/{match_id}")
def resolve_match(match_id: int):
    """
    Endpoint to manually approve/resolve a flagged scouting record.
    """
    try:
        database.resolve_flag_override(match_id)
        logger.info(f"Scout record {match_id} manually approved and resolved by lead scout.")
        return {"status": "success", "message": f"Record {match_id} successfully approved."}
    except Exception as e:
        logger.error(f"Resolve error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/analytics")
def get_analytics():
    """
    Calculates average stats and climb consistency for all scouted teams.
    """
    try:
        records = database.get_all_matches()
        team_stats = compile_team_stats(records)
        return {
            "count": len(team_stats),
            "teams": team_stats
        }
    except Exception as e:
        logger.error(f"Analytics error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/predict")
def predict_match(payload: PredictionPayload):
    """
    Simulates a 3v3 match based on average scouted performance.
    """
    try:
        records = database.get_all_matches()
        team_stats = {t["team_number"]: t for t in compile_team_stats(records)}
        
        red_projection = simulate_alliance(payload.red_teams, team_stats)
        blue_projection = simulate_alliance(payload.blue_teams, team_stats)
        
        # Calculate win probability using ratio
        total_projected = red_projection["total_score"] + blue_projection["total_score"]
        if total_projected > 0:
            red_win_prob = (red_projection["total_score"] / total_projected) * 100
        else:
            red_win_prob = 50.0
            
        return {
            "red_alliance": red_projection,
            "blue_alliance": blue_projection,
            "red_win_probability": round(red_win_prob, 1),
            "blue_win_probability": round(100.0 - red_win_prob, 1),
            "predicted_winner": "Red" if red_projection["total_score"] >= blue_projection["total_score"] else "Blue"
        }
    except Exception as e:
        logger.error(f"Prediction error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/reset")
def reset_database():
    """
    Development endpoint to clear the database.
    """
    try:
        conn = database.get_connection()
        cursor = conn.cursor()
        cursor.execute("DROP TABLE IF EXISTS matches")
        conn.commit()
        conn.close()
        database.init_db()
        logger.info("Database has been reset by user request.")
        return {"status": "success", "message": "Database reset completed."}
    except Exception as e:
        logger.error(f"Reset error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# --- Helper Functions ---

def compile_team_stats(records) -> List[dict]:
    # Group records by team number
    grouped = {}
    for r in records:
        team = r["team_number"]
        if team not in grouped:
            grouped[team] = []
        grouped[team].append(r)
        
    team_stats = []
    for team, matches in grouped.items():
        count = len(matches)
        
        auto_scores = [m["auto_score"] for m in matches]
        teleop_cycles = [m["teleop_cycles"] for m in matches]
        climb_points = [m["endgame_points"] for m in matches]
        total_scores = [m["scout_calculated_score"] for m in matches]
        
        # Calculate climbing success rate
        climb_successes = sum(1 for m in matches if m["endgame_status"] in ["Climbed", "Harmony"])
        climb_rate = (climb_successes / count) * 100 if count > 0 else 0
        
        avg_auto = sum(auto_scores) / count
        avg_cycles = sum(teleop_cycles) / count
        avg_climb = sum(climb_points) / count
        avg_total = sum(total_scores) / count
        
        # Standard deviation for consistency
        std_dev = 0.0
        if count >= 2:
            variance = sum((x - avg_total) ** 2 for x in total_scores) / (count - 1)
            std_dev = math.sqrt(variance)
            
        # Consistency rating from 1 to 10 (lower std dev = higher consistency)
        if count < 2:
            consistency = 5.0 # default
        else:
            # 10 is super consistent (std_dev close to 0), 1 is highly erratic (std_dev > 25)
            consistency = max(1.0, min(10.0, 10 - (std_dev / 2.5)))
            
        team_stats.append({
            "team_number": team,
            "matches_scouted": count,
            "avg_auto_score": round(avg_auto, 1),
            "avg_teleop_cycles": round(avg_cycles, 1),
            "avg_endgame_points": round(avg_climb, 1),
            "avg_total_score": round(avg_total, 1),
            "climb_success_rate": round(climb_rate, 1),
            "consistency_rating": round(consistency, 1),
            "scores_list": total_scores
        })
        
    # Sort teams by average total score descending
    return sorted(team_stats, key=lambda x: x["avg_total_score"], reverse=True)

def simulate_alliance(teams: List[str], team_stats: dict) -> dict:
    """
    Project performance of a 3-team FRC alliance.
    """
    projected_auto = 0.0
    projected_cycles = 0.0
    projected_climb = 0.0
    
    for t in teams:
        stats = team_stats.get(t)
        if stats:
            projected_auto += stats["avg_auto_score"]
            projected_cycles += stats["avg_teleop_cycles"]
            projected_climb += stats["avg_endgame_points"]
        else:
            # Fallback stats for un-scouted team (assumed rookie average)
            projected_auto += 5.0
            projected_cycles += 2.0
            projected_climb += 0.5
            
    projected_teleop_pts = projected_cycles * 3
    
    return {
        "teams": teams,
        "projected_auto": round(projected_auto, 1),
        "projected_teleop_points": round(projected_teleop_pts, 1),
        "projected_endgame_points": round(projected_climb, 1),
        "total_score": round(projected_auto + projected_teleop_pts + projected_climb, 1)
    }

# --- Visual Review Portal and Dashboard ---

@app.get("/portal", response_class=HTMLResponse)
def get_portal_dashboard():
    """
    Serves a stunning, dark-mode Glassmorphic Single Page Dashboard for FRC Lead Scouts.
    """
    html_content = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>SabreOS Lead Scout Dashboard</title>
    <link href="https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;600;800;900&display=swap" rel="stylesheet">
    <style>
        :root {
            --bg-color: #0B0F19;
            --card-bg: rgba(21, 31, 50, 0.65);
            --border-color: rgba(30, 41, 59, 0.8);
            --sartell-blue: #1E56A0;
            --blue-accent: #60A5FA;
            --text-main: #F8FAFC;
            --text-secondary: #94A3B8;
            --glow-orange: rgba(245, 158, 11, 0.25);
            --glow-green: rgba(16, 185, 129, 0.2);
        }

        * {
            box-sizing: border-box;
            margin: 0;
            padding: 0;
            font-family: 'Outfit', sans-serif;
            -webkit-font-smoothing: antialiased;
        }

        body {
            background-color: var(--bg-color);
            background-image: 
                radial-gradient(at 10% 20%, rgba(30, 86, 160, 0.15) 0px, transparent 50%),
                radial-gradient(at 90% 80%, rgba(96, 165, 250, 0.1) 0px, transparent 50%);
            color: var(--text-main);
            min-height: 100vh;
            padding: 24px;
        }

        .container {
            max-width: 1300px;
            margin: 0 auto;
        }

        header {
            display: flex;
            align-items: center;
            justify-content: space-between;
            margin-bottom: 30px;
            padding-bottom: 20px;
            border-bottom: 1px solid var(--border-color);
        }

        .brand-section {
            display: flex;
            align-items: center;
            gap: 16px;
        }

        .logo-emblem {
            width: 55px;
            height: 55px;
            border-radius: 50%;
            background-color: var(--sartell-blue);
            display: flex;
            align-items: center;
            justify-content: center;
            font-weight: 900;
            font-size: 26px;
            color: #fff;
            border: 2px solid #fff;
            box-shadow: 0 0 15px rgba(30, 86, 160, 0.5);
        }

        .header-title h1 {
            font-size: 28px;
            font-weight: 900;
            letter-spacing: 0.5px;
            background: linear-gradient(to right, #FFF, var(--blue-accent));
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
        }

        .header-title p {
            font-size: 13px;
            color: var(--blue-accent);
            font-weight: 600;
            letter-spacing: 1px;
            text-transform: uppercase;
        }

        /* Tabs Menu */
        .tabs {
            display: flex;
            gap: 12px;
            background: rgba(15, 23, 42, 0.5);
            padding: 6px;
            border-radius: 12px;
            border: 1px solid var(--border-color);
        }

        .tab-btn {
            background: none;
            border: none;
            color: var(--text-secondary);
            padding: 10px 20px;
            font-size: 14px;
            font-weight: 600;
            border-radius: 8px;
            cursor: pointer;
            transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
        }

        .tab-btn:hover {
            color: var(--text-main);
            background: rgba(255, 255, 255, 0.05);
        }

        .tab-btn.active {
            color: #fff;
            background: var(--sartell-blue);
            box-shadow: 0 4px 12px rgba(30, 86, 160, 0.4);
        }

        /* Tab Content Panel */
        .tab-panel {
            display: none;
            animation: fadeIn 0.4s ease;
        }

        .tab-panel.active {
            display: block;
        }

        @keyframes fadeIn {
            from { opacity: 0; transform: translateY(8px); }
            to { opacity: 1; transform: translateY(0); }
        }

        /* Dashboard Cards Layout */
        .grid-layout {
            display: grid;
            grid-template-columns: 2fr 1fr;
            gap: 24px;
        }

        @media (max-width: 1024px) {
            .grid-layout {
                grid-template-columns: 1fr;
            }
        }

        .glass-card {
            background: var(--card-bg);
            backdrop-filter: blur(16px);
            border-radius: 20px;
            border: 1px solid var(--border-color);
            padding: 24px;
            margin-bottom: 24px;
            box-shadow: 0 10px 30px rgba(0, 0, 0, 0.3);
        }

        .card-title {
            font-size: 18px;
            font-weight: 800;
            margin-bottom: 20px;
            display: flex;
            align-items: center;
            justify-content: space-between;
        }

        .card-title span {
            color: var(--blue-accent);
            font-size: 12px;
            font-weight: 600;
            background: rgba(96, 165, 250, 0.15);
            padding: 4px 10px;
            border-radius: 6px;
            text-transform: uppercase;
        }

        /* Discrepancy Tables */
        table {
            width: 100%;
            border-collapse: collapse;
            text-align: left;
        }

        th {
            color: var(--text-secondary);
            font-size: 11px;
            font-weight: 800;
            text-transform: uppercase;
            letter-spacing: 1px;
            padding: 12px 16px;
            border-bottom: 1px solid var(--border-color);
        }

        td {
            padding: 16px;
            font-size: 14px;
            color: var(--text-main);
            border-bottom: 1px solid rgba(30, 41, 59, 0.4);
            font-weight: 600;
        }

        tr.flagged-row {
            background: rgba(245, 158, 11, 0.04);
            position: relative;
        }

        tr.flagged-row td {
            border-bottom: 1.5px solid rgba(245, 158, 11, 0.2);
        }

        .badge {
            display: inline-block;
            padding: 4px 8px;
            font-size: 10px;
            font-weight: 800;
            border-radius: 6px;
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }

        .badge-review {
            background: rgba(245, 158, 11, 0.15);
            color: #F59E0B;
            border: 1px solid rgba(245, 158, 11, 0.3);
            animation: pulse-border 2s infinite;
        }

        .badge-verified {
            background: rgba(16, 185, 129, 0.15);
            color: #10B981;
            border: 1px solid rgba(16, 185, 129, 0.3);
        }

        .badge-override {
            background: rgba(96, 165, 250, 0.15);
            color: var(--blue-accent);
            border: 1px solid rgba(96, 165, 250, 0.3);
        }

        @keyframes pulse-border {
            0% { box-shadow: 0 0 0 0 rgba(245, 158, 11, 0.2); }
            70% { box-shadow: 0 0 0 6px rgba(245, 158, 11, 0); }
            100% { box-shadow: 0 0 0 0 rgba(245, 158, 11, 0); }
        }

        /* Action Buttons */
        .btn {
            background: var(--sartell-blue);
            color: #fff;
            border: none;
            padding: 8px 14px;
            font-size: 12px;
            font-weight: 700;
            border-radius: 8px;
            cursor: pointer;
            transition: all 0.3s;
            display: inline-flex;
            align-items: center;
            gap: 6px;
        }

        .btn:hover {
            transform: translateY(-2px);
            box-shadow: 0 4px 12px rgba(30, 86, 160, 0.4);
            filter: brightness(1.1);
        }

        .btn-resolve {
            background: #2563EB;
        }

        /* Predictor Styles */
        .predictor-grid {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 16px;
            margin-bottom: 20px;
        }

        .alliance-card {
            background: rgba(15, 23, 42, 0.4);
            border-radius: 12px;
            padding: 16px;
            border: 1px solid var(--border-color);
        }

        .red-alliance-hdr {
            color: #EF4444;
            font-size: 14px;
            font-weight: 800;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            margin-bottom: 12px;
            display: flex;
            align-items: center;
            gap: 6px;
        }

        .blue-alliance-hdr {
            color: var(--blue-accent);
            font-size: 14px;
            font-weight: 800;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            margin-bottom: 12px;
            display: flex;
            align-items: center;
            gap: 6px;
        }

        .predictor-input {
            width: 100%;
            background: var(--bg-color);
            border: 1.5px solid var(--border-color);
            color: #fff;
            padding: 10px 14px;
            font-size: 15px;
            font-weight: 600;
            border-radius: 8px;
            margin-bottom: 10px;
            outline: none;
            transition: border 0.3s;
        }

        .predictor-input:focus {
            border-color: var(--blue-accent);
        }

        .btn-predict {
            width: 100%;
            padding: 14px;
            font-size: 14px;
            font-weight: 900;
            text-transform: uppercase;
            letter-spacing: 1px;
            border-radius: 10px;
        }

        /* Predictor Results */
        .predictor-results {
            background: rgba(30, 86, 160, 0.1);
            border: 1px dashed var(--blue-accent);
            border-radius: 12px;
            padding: 20px;
            margin-top: 20px;
            display: none;
        }

        .winner-banner {
            text-align: center;
            font-size: 24px;
            font-weight: 900;
            margin-bottom: 15px;
        }

        .winner-banner span.Red {
            color: #EF4444;
        }

        .winner-banner span.Blue {
            color: var(--blue-accent);
        }

        .prob-bar {
            height: 12px;
            background: #EF4444;
            border-radius: 6px;
            overflow: hidden;
            display: flex;
            margin-bottom: 20px;
        }

        .prob-blue {
            height: 100%;
            background: var(--blue-accent);
            transition: width 0.5s;
        }

        .breakdown-row {
            display: flex;
            justify-content: space-between;
            font-size: 13px;
            color: var(--text-secondary);
            padding: 6px 0;
            border-bottom: 1px solid rgba(255,255,255,0.05);
        }

        .breakdown-row span.bold {
            color: #fff;
            font-weight: 700;
        }
    </style>
</head>
<body>
    <div class="container">
        <header>
            <div class="brand-section">
                <div class="logo-emblem">S</div>
                <div class="header-title">
                    <h1>SabreOS Dashboard</h1>
                    <p>FRC Lead Scout Command Station</p>
                </div>
            </div>

            <div class="tabs">
                <button class="tab-btn active" onclick="switchTab('review-panel', this)">Discrepancy Engine</button>
                <button class="tab-btn" onclick="switchTab('analytics-panel', this); fetchTeamStats();">Team Analytics & Predictor</button>
            </div>
        </header>

        <!-- TAB 1: DISCREPANCY REVIEW -->
        <div id="review-panel" class="tab-panel active">
            <div class="glass-card">
                <div class="card-title">
                    Match Caching Queue Review
                    <span id="review-counter">Loading...</span>
                </div>
                <div style="overflow-x: auto;">
                    <table id="matches-table">
                        <thead>
                            <tr>
                                <th>Match</th>
                                <th>Team</th>
                                <th>Scout</th>
                                <th>Auto Score</th>
                                <th>Teleop Cycles</th>
                                <th>Endgame</th>
                                <th>Scout Score</th>
                                <th>TBA Score</th>
                                <th>Status</th>
                                <th>Action</th>
                            </tr>
                        </thead>
                        <tbody id="matches-body">
                            <tr>
                                <td colspan="10" style="text-align: center; color: var(--text-secondary);">Querying central database...</td>
                            </tr>
                        </tbody>
                    </table>
                </div>
            </div>
        </div>

        <!-- TAB 2: TEAM STATS & PREDICTOR -->
        <div id="analytics-panel" class="tab-panel">
            <div class="grid-layout">
                <!-- Left Column: Rankings Table -->
                <div class="glass-card">
                    <div class="card-title">
                        FRC Team Averages
                        <span>Ranked by Scouted contribution</span>
                    </div>
                    <div style="overflow-x: auto;">
                        <table id="rankings-table">
                            <thead>
                                <tr>
                                    <th>Rank</th>
                                    <th>Team</th>
                                    <th>Matches</th>
                                    <th>Avg Auto</th>
                                    <th>Avg Teleop</th>
                                    <th>Avg Climb</th>
                                    <th>Avg Total</th>
                                    <th>Climb Rate</th>
                                    <th>Consistency</th>
                                </tr>
                            </thead>
                            <tbody id="rankings-body">
                                <tr>
                                    <td colspan="9" style="text-align: center; color: var(--text-secondary);">No team data found. Submit matches from mobile app.</td>
                                </tr>
                            </tbody>
                        </table>
                    </div>
                </div>

                <!-- Right Column: Predictor Form -->
                <div class="glass-card" style="height: fit-content;">
                    <div class="card-title">
                        3v3 Match Predictor
                        <span>Pre-Match Strategy</span>
                    </div>

                    <div class="predictor-grid">
                        <div class="alliance-card">
                            <div class="red-alliance-hdr">🔴 Red Alliance</div>
                            <input id="red1" placeholder="Team 1 (e.g. 6045)" class="predictor-input" type="number">
                            <input id="red2" placeholder="Team 2" class="predictor-input" type="number">
                            <input id="red3" placeholder="Team 3" class="predictor-input" type="number">
                        </div>

                        <div class="alliance-card">
                            <div class="blue-alliance-hdr">🔵 Blue Alliance</div>
                            <input id="blue1" placeholder="Team 1" class="predictor-input" type="number">
                            <input id="blue2" placeholder="Team 2" class="predictor-input" type="number">
                            <input id="blue3" placeholder="Team 3" class="predictor-input" type="number">
                        </div>
                    </div>

                    <button class="btn btn-predict" onclick="runPrediction()">⚔️ Simulate Alliance Battle</button>

                    <!-- Predictor Results Box -->
                    <div id="predictor-box" class="predictor-results">
                        <div id="winner-text" class="winner-banner">Predicted Winner: <span class="Red">Red</span></div>
                        
                        <div class="prob-bar">
                            <div id="red-prob" style="width: 60%"></div>
                            <div id="blue-prob" class="prob-blue" style="width: 40%"></div>
                        </div>

                        <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 20px;">
                            <div>
                                <div class="red-alliance-hdr" style="font-size: 11px;">🔴 Red Projected</div>
                                <div class="breakdown-row">Auto points: <span class="bold" id="red-proj-auto">15</span></div>
                                <div class="breakdown-row">Teleop points: <span class="bold" id="red-proj-teleop">45</span></div>
                                <div class="breakdown-row">Climb points: <span class="bold" id="red-proj-climb">6</span></div>
                                <div class="breakdown-row" style="font-weight: 800; border: none; color: #fff;">Total projected: <span class="bold" id="red-proj-total" style="color: #EF4444; font-size:16px;">66</span></div>
                            </div>

                            <div>
                                <div class="blue-alliance-hdr" style="font-size: 11px;">🔵 Blue Projected</div>
                                <div class="breakdown-row">Auto points: <span class="bold" id="blue-proj-auto">10</span></div>
                                <div class="breakdown-row">Teleop points: <span class="bold" id="blue-proj-teleop">40</span></div>
                                <div class="breakdown-row">Climb points: <span class="bold" id="blue-proj-climb">3</span></div>
                                <div class="breakdown-row" style="font-weight: 800; border: none; color: #fff;">Total projected: <span class="bold" id="blue-proj-total" style="color: var(--blue-accent); font-size:16px;">53</span></div>
                            </div>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    </div>

    <script>
        // Tab switching
        function switchTab(panelId, btnEl) {
            document.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('active'));
            document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
            
            document.getElementById(panelId).classList.add('active');
            btnEl.classList.add('active');
        }

        // Fetch matches from API and populate list
        async function fetchMatches() {
            try {
                const response = await fetch('/matches');
                const data = await response.json();
                
                const tbody = document.getElementById('matches-body');
                tbody.innerHTML = '';
                
                let flaggedCount = 0;
                
                if (data.records.length === 0) {
                    tbody.innerHTML = '<tr><td colspan="10" style="text-align: center; color: var(--text-secondary);">No records found. Submit scouting records from the Expo app!</td></tr>';
                    document.getElementById('review-counter').innerText = '0 pending reviews';
                    return;
                }
                
                data.records.forEach(r => {
                    const row = document.createElement('tr');
                    
                    let statusBadge = '';
                    let actionButton = '';
                    
                    if (r.requires_review === 1 && r.resolved_override === 0) {
                        row.classList.add('flagged-row');
                        statusBadge = '<span class="badge badge-review">⚠️ Flagged (' + Math.round(r.discrepancy_percentage) + '%)</span>';
                        actionButton = `<button class="btn btn-resolve" onclick="resolveRecord(${r.id}, this)">✓ Resolve</button>`;
                        flaggedCount++;
                    } else if (r.resolved_override === 1) {
                        statusBadge = '<span class="badge badge-override">Approved Override</span>';
                        actionButton = '<span style="color: var(--text-secondary); font-size: 12px;">Resolved</span>';
                    } else {
                        statusBadge = '<span class="badge badge-verified">Verified ✓</span>';
                        actionButton = '<span style="color: var(--text-secondary); font-size: 12px;">Good</span>';
                    }
                    
                    row.innerHTML = `
                        <td>qm ${r.match_number}</td>
                        <td style="color: var(--blue-accent); font-weight: 800;">${r.team_number}</td>
                        <td style="font-weight: 800;">${r.scout_initials || 'N/A'}</td>
                        <td>${r.auto_score} pts</td>
                        <td>${r.teleop_cycles} cyc</td>
                        <td>${r.endgame_status} (${r.endgame_points}p)</td>
                        <td style="font-weight: 800;">${r.scout_calculated_score} pts</td>
                        <td style="color: var(--text-secondary);">${r.tba_official_score !== null ? r.tba_official_score + ' pts' : 'Pending'}</td>
                        <td>${statusBadge}</td>
                        <td>${actionButton}</td>
                    `;
                    tbody.appendChild(row);
                });
                
                document.getElementById('review-counter').innerText = flaggedCount + ' match' + (flaggedCount !== 1 ? 'es' : '') + ' requires review';
                
            } catch (err) {
                console.error("Error fetching matches:", err);
            }
        }

        // Resolve flagged record
        async function resolveRecord(matchId, buttonEl) {
            try {
                const response = await fetch(`/resolve/${matchId}`, { method: 'POST' });
                const result = await response.json();
                if (result.status === 'success') {
                    // Instantly refresh list
                    fetchMatches();
                }
            } catch (err) {
                alert("Error resolving record: " + err.message);
            }
        }

        // Fetch team statistics
        async function fetchTeamStats() {
            try {
                const response = await fetch('/analytics');
                const data = await response.json();
                
                const tbody = document.getElementById('rankings-body');
                tbody.innerHTML = '';
                
                if (data.teams.length === 0) {
                    tbody.innerHTML = '<tr><td colspan="9" style="text-align: center; color: var(--text-secondary);">No team data found yet. Sync scouting records first!</td></tr>';
                    return;
                }
                
                data.teams.forEach((t, index) => {
                    const row = document.createElement('tr');
                    
                    // Style consistency rating
                    let consistencyStyle = 'color: var(--text-secondary)';
                    if (t.consistency_rating >= 8) consistencyStyle = 'color: #10B981; font-weight:800;';
                    else if (t.consistency_rating < 5) consistencyStyle = 'color: #EF4444; font-weight:800;';
                    
                    row.innerHTML = `
                        <td>#${index + 1}</td>
                        <td style="color: var(--blue-accent); font-weight: 800; font-size:16px;">Team ${t.team_number}</td>
                        <td>${t.matches_scouted} scouted</td>
                        <td>${t.avg_auto_score} pts</td>
                        <td>${t.avg_teleop_cycles} cycles</td>
                        <td>${t.avg_endgame_points} pts</td>
                        <td style="font-weight: 800; font-size:15px; color:#fff;">${t.avg_total_score} pts</td>
                        <td>${t.climb_success_rate}%</td>
                        <td style="${consistencyStyle}">${t.consistency_rating}/10</td>
                    `;
                    tbody.appendChild(row);
                });
            } catch (err) {
                console.error("Error fetching analytics:", err);
            }
        }

        // Run prediction
        async function runPrediction() {
            const red = [
                document.getElementById('red1').value,
                document.getElementById('red2').value,
                document.getElementById('red3').value
            ].filter(v => v.trim() !== '');

            const blue = [
                document.getElementById('blue1').value,
                document.getElementById('blue2').value,
                document.getElementById('blue3').value
            ].filter(v => v.trim() !== '');

            if (red.length !== 3 || blue.length !== 3) {
                alert("Please fill in all 3 teams for both alliances to run simulation!");
                return;
            }

            try {
                const response = await fetch('/predict', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ red_teams: red, blue_teams: blue })
                });
                
                const data = await response.json();
                
                // Show predictor results box
                const box = document.getElementById('predictor-box');
                box.style.display = 'block';
                
                // Winner header
                const winHdr = document.getElementById('winner-text');
                winHdr.innerHTML = `Predicted Winner: <span class="${data.predicted_winner}">${data.predicted_winner} Alliance</span>`;
                
                // Probability bar
                const redProbBar = document.getElementById('red-prob');
                redProbBar.style.width = `${data.red_win_probability}%`;
                
                // Red breakdown
                document.getElementById('red-proj-auto').innerText = `${data.red_alliance.projected_auto} pts`;
                document.getElementById('red-proj-teleop').innerText = `${data.red_alliance.projected_teleop_points} pts`;
                document.getElementById('red-proj-climb').innerText = `${data.red_alliance.projected_endgame_points} pts`;
                document.getElementById('red-proj-total').innerText = `${data.red_alliance.total_score} pts`;
                
                // Blue breakdown
                document.getElementById('blue-proj-auto').innerText = `${data.blue_alliance.projected_auto} pts`;
                document.getElementById('blue-proj-teleop').innerText = `${data.blue_alliance.projected_teleop_points} pts`;
                document.getElementById('blue-proj-climb').innerText = `${data.blue_alliance.projected_endgame_points} pts`;
                document.getElementById('blue-proj-total').innerText = `${data.blue_alliance.total_score} pts`;

            } catch (err) {
                alert("Prediction failed: " + err.message);
            }
        }

        // Initial setup
        fetchMatches();
        // Refresh matches every 8 seconds
        setInterval(fetchMatches, 8000);
    </script>
</body>
</html>
"""
    return HTMLResponse(content=html_content, status_code=200)
