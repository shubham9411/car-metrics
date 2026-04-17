import time
import logging
import sqlite3
from datetime import datetime, timezone, timedelta

import sys
import os
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from storage import db

logger = logging.getLogger("scripts.aggregate_env")

def run_aggregation():
    """Run environmental data aggregation for all missing hours."""
    conn = db.get_connection()
    
    # 1. Find last aggregated timestamp
    res = conn.execute("SELECT MAX(ts) as last_ts FROM env_hourly_summary").fetchone()
    last_ts = res["last_ts"]
    
    if last_ts is None:
        # Start from the very beginning
        res = conn.execute("SELECT MIN(ts) as first_ts FROM env_readings").fetchone()
        if not res or res["first_ts"] is None:
            return # No data to aggregate
        # Start at the beginning of that hour
        start_ts = (res["first_ts"] // 3600) * 3600
    else:
        # Start at the next hour
        start_ts = last_ts + 3600
        
    now = time.time()
    current_hour_start = (now // 3600) * 3600
    
    logger.info("Starting aggregation from %s to %s", 
                datetime.fromtimestamp(start_ts, tz=timezone.utc).isoformat(),
                datetime.fromtimestamp(current_hour_start, tz=timezone.utc).isoformat())
    
    count = 0
    while start_ts < current_hour_start:
        end_ts = start_ts + 3600
        
        # Aggregate this hour
        query = """
            SELECT 
                AVG(temperature) as avg_temp,
                AVG(humidity) as avg_hum,
                AVG(CASE WHEN iaq_score >= 1 THEN iaq_score END) as avg_iaq,
                COUNT(*) as readings_count
            FROM env_readings
            WHERE ts >= ? AND ts < ?
        """
        row = conn.execute(query, (start_ts, end_ts)).fetchone()
        
        if row and row["readings_count"] > 0:
            conn.execute("""
                INSERT INTO env_hourly_summary (ts, avg_temp, avg_hum, avg_iaq, count)
                VALUES (?, ?, ?, ?, ?)
            """, (start_ts, row["avg_temp"], row["avg_hum"], row["avg_iaq"], row["readings_count"]))
            count += 1
            
        start_ts = end_ts
        
    if count > 0:
        conn.commit()
    
    # Optional: Delete raw data older than 30 days to keep DB small? 
    # For now, let's keep it.
    
    logger.info("Aggregated %d new hours", count)

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run_aggregation()
