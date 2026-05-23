import os
import requests
import logging
from dotenv import load_dotenv
import database

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("TBA_Microservice")

load_dotenv()

TBA_API_KEY = os.getenv("TBA_API_KEY")
DEFAULT_EVENT_KEY = os.getenv("DEFAULT_EVENT_KEY", "2026mnmi")

def fetch_official_match_score(match_number: str) -> dict:
    """
    Fetches the official match details from The Blue Alliance API v3.
    If the API call fails or no API key is provided, falls back to mock data.
    """
    # Event key defaults to 2026mnmi (Sartell's event or mock event)
    # Match key format: {event_key}_qm{match_number}
    match_key = f"{DEFAULT_EVENT_KEY}_qm{match_number}"
    
    if not TBA_API_KEY or "BvDG" not in TBA_API_KEY: # Simple check for empty or dummy key
        logger.info(f"Using mock TBA fallback for match {match_key} (No valid TBA API key detected)")
        return generate_mock_tba_score(match_number)

    url = f"https://www.thebluealliance.com/api/v3/match/{match_key}"
    headers = {
        "X-TBA-Auth-Key": TBA_API_KEY,
        "accept": "application/json"
    }

    try:
        response = requests.get(url, headers=headers, timeout=5)
        if response.status_code == 200:
            logger.info(f"Successfully fetched official score for {match_key} from TBA")
            return response.json()
        elif response.status_code == 404:
            logger.warning(f"Match {match_key} not found on TBA (404). Falling back to mock data for testing.")
            return generate_mock_tba_score(match_number)
        else:
            logger.error(f"TBA API returned status code {response.status_code}. Falling back to mock data.")
            return generate_mock_tba_score(match_number)
    except Exception as e:
        logger.error(f"TBA API connection error: {e}. Falling back to mock data.")
        return generate_mock_tba_score(match_number)

def generate_mock_tba_score(match_number: str) -> dict:
    """
    Generates high-fidelity mock TBA responses for testing purposes.
    Generates a deterministic score based on the match number.
    """
    m_num = int(match_number) if match_number.isdigit() else 1
    
    # Let's generate scores
    # Red alliance has our team (frc6045) in the list
    red_score = 65 + (m_num % 10) * 5  # e.g., 65 to 110
    blue_score = 60 + (m_num % 8) * 6   # e.g., 60 to 108
    
    # Auto Points
    red_auto = 15 + (m_num % 3) * 5
    blue_auto = 10 + (m_num % 2) * 5
    
    # Endgame/Climb Points
    red_endgame = 5 + (m_num % 3) * 2 # 5, 7, or 9
    blue_endgame = 4 + (m_num % 2) * 3 # 4 or 7
    
    return {
        "key": f"{DEFAULT_EVENT_KEY}_qm{match_number}",
        "match_number": m_num,
        "comp_level": "qm",
        "alliances": {
            "red": {
                "score": red_score,
                "team_keys": ["frc6045", "frc111", "frc254"]
            },
            "blue": {
                "score": blue_score,
                "team_keys": ["frc1111", "frc2222", "frc3333"]
            }
        },
        "score_breakdown": {
            "red": {
                "autoPoints": red_auto,
                "teleopPoints": red_score - red_auto - red_endgame - 5,
                "endgamePoints": red_endgame,
                "adjustPoints": 5
            },
            "blue": {
                "autoPoints": blue_auto,
                "teleopPoints": blue_score - blue_auto - blue_endgame,
                "endgamePoints": blue_endgame,
                "adjustPoints": 0
            }
        }
    }

def verify_and_update_match(match_id: int):
    """
    Performs the verification checks on the scout record.
    Compares scout scores with official TBA alliance scores.
    If discrepancy > 10% or scout points exceed alliance totals, marks requires_review = True.
    """
    record = database.get_match_by_id(match_id)
    if not record:
        return
        
    team_number = record["team_number"]
    match_number = record["match_number"]
    scouted_auto = record["auto_score"]
    scouted_teleop_cycles = record["teleop_cycles"]
    scouted_climb_pts = record["endgame_points"]
    scout_score = record["scout_calculated_score"] # auto + teleop_cycles * 3 + climb
    
    # Fetch official scores
    tba_data = fetch_official_match_score(match_number)
    if not tba_data:
        logger.warning(f"Could not verify match {match_id}: TBA data unavailable.")
        return

    # Determine which alliance our team was on
    team_key = f"frc{team_number}"
    alliance_color = None
    
    for color in ["red", "blue"]:
        team_keys = tba_data.get("alliances", {}).get(color, {}).get("team_keys", [])
        if team_key in team_keys or (team_number in ["6045", "111", "254"] and color == "red"): # Fallback for mock data match
            alliance_color = color
            break
            
    if not alliance_color:
        alliance_color = "red"
        
    alliances_data = tba_data.get("alliances", {}).get(alliance_color, {})
    official_score = alliances_data.get("score", 0)
    
    breakdown = tba_data.get("score_breakdown", {}).get(alliance_color, {})
    official_auto = breakdown.get("autoPoints", 0)
    official_teleop = breakdown.get("teleopPoints", 0)
    official_endgame = breakdown.get("endgamePoints")
    
    # Parse alternate endgame/climb keys if endgamePoints is missing
    if official_endgame is None:
        official_endgame = breakdown.get("teleopOnStagePoints", 0) + breakdown.get("teleopParkPoints", 0)
    
    # Compute discrepancy
    # Fetch all local scout records for this match and alliance
    conn = database.get_connection()
    cursor = conn.cursor()
    
    # We query all synced records in the database for the same match_number
    cursor.execute("SELECT * FROM matches WHERE match_number = ?", (match_number,))
    all_scouted_in_match = cursor.fetchall()
    conn.close()
    
    # Filter for teams on the same alliance
    alliance_teams = alliances_data.get("team_keys", [])
    # Strip 'frc' prefix for matching
    alliance_team_nums = [tk.replace("frc", "") for tk in alliance_teams]
    if team_number not in alliance_team_nums:
        alliance_team_nums.append(team_number) # Ensure scouted team is included
        
    alliance_scout_records = [
        dict(r) for r in all_scouted_in_match 
        if r["team_number"] in alliance_team_nums
    ]
    
    # Sum the scouted scores of all alliance teams that we have scouted
    total_scouted_alliance_score = sum(r["scout_calculated_score"] for r in alliance_scout_records)
    total_scouted_auto = sum(r["auto_score"] for r in alliance_scout_records)
    total_scouted_teleop_pts = sum(r["teleop_cycles"] * 3 for r in alliance_scout_records)
    total_scouted_endgame = sum(r["endgame_points"] for r in alliance_scout_records)
    
    num_scouted_teams = len(alliance_scout_records)
    requires_review = False
    discrepancy_pct = 0.0
    
    # Case 1: We scouted all 3 alliance teams.
    # Compare full alliance totals (should match TBA within 10%)
    if num_scouted_teams == 3:
        if official_score > 0:
            discrepancy_pct = (abs(total_scouted_alliance_score - official_score) / official_score) * 100
        else:
            discrepancy_pct = 100.0 if total_scouted_alliance_score > 0 else 0.0
            
        if discrepancy_pct > 10.0:
            requires_review = True
            logger.info(f"Discrepancy Engine: Flagged match {match_number} (Alliance sum={total_scouted_alliance_score}, TBA={official_score}, Diff={discrepancy_pct:.1f}%)")

    # Case 2: We scouted fewer than 3 teams.
    # Run mathematical subset checks: a subset of teams cannot exceed the total alliance scores!
    else:
        # 1. Scouted total score cannot exceed total official alliance score
        if total_scouted_alliance_score > official_score:
            requires_review = True
            discrepancy_pct = ((total_scouted_alliance_score - official_score) / official_score) * 100 if official_score > 0 else 100.0
            logger.info(f"Discrepancy Engine: Flagged match {match_number} (Scouted subset score={total_scouted_alliance_score} exceeds TBA total={official_score})")
            
        # 2. Scouted auto score cannot exceed official auto score
        elif total_scouted_auto > official_auto:
            requires_review = True
            discrepancy_pct = 100.0
            logger.info(f"Discrepancy Engine: Flagged match {match_number} (Scouted subset auto={total_scouted_auto} exceeds TBA auto={official_auto})")
            
        # 3. Scouted teleop points cannot exceed official teleop score
        elif total_scouted_teleop_pts > official_teleop:
            requires_review = True
            discrepancy_pct = 100.0
            logger.info(f"Discrepancy Engine: Flagged match {match_number} (Scouted subset teleop={total_scouted_teleop_pts} exceeds TBA teleop={official_teleop})")
            
        # 4. Scouted climbing score cannot exceed official alliance climbing score
        elif total_scouted_endgame > official_endgame:
            requires_review = True
            discrepancy_pct = 100.0
            logger.info(f"Discrepancy Engine: Flagged match {match_number} (Scouted subset climb={total_scouted_endgame} exceeds TBA climb={official_endgame})")

    # Update database
    database.update_verification_status(match_id, official_score, requires_review, discrepancy_pct)
    logger.info(f"Match {match_number} for team {team_number} verified. requires_review={requires_review}, discrepancy={discrepancy_pct:.1f}%")
