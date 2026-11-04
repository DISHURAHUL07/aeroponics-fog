"""
Aeroponics IoT System - SQLite Database Module
==============================================
Manages local SQLite storage for sensor readings, pump state transitions,
fault alerts, and latency performance comparisons.

Designed to be simple, self-healing, thread-safe, and beginner-readable.
"""

import os
import sqlite3
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional

DEFAULT_DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "aeroponics.db")


def get_iso_timestamp() -> str:
    """Returns current ISO formatted timestamp."""
    return datetime.now().isoformat()


def get_connection(db_path: str = DEFAULT_DB_PATH) -> sqlite3.Connection:
    """Creates a thread-safe connection to the SQLite database."""
    conn = sqlite3.connect(db_path, timeout=15.0, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(db_path: str = DEFAULT_DB_PATH) -> None:
    """Initializes the database schema if tables do not already exist."""
    with get_connection(db_path) as conn:
        cursor = conn.cursor()

        # 1. Table for periodic sensor telemetry
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS readings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                temperature REAL,
                humidity REAL,
                water_level REAL,
                ph REAL,
                ec REAL,
                root_temp REAL
            )
        """)

        # 2. Table for misting pump activation/deactivation events
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS pump_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                state TEXT NOT NULL,
                mode TEXT NOT NULL,
                trigger_reason TEXT,
                latency_ms REAL DEFAULT 0.0
            )
        """)

        # 3. Table for detected sensor and actuator faults
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS faults (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                fault_type TEXT NOT NULL,
                sensor_name TEXT NOT NULL,
                severity TEXT NOT NULL,
                message TEXT NOT NULL,
                resolved INTEGER DEFAULT 0
            )
        """)

        # 4. Table for tracking Fog vs Cloud decision latency
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS latency_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                architecture TEXT NOT NULL,
                latency_ms REAL NOT NULL,
                status TEXT NOT NULL
            )
        """)

        # Create indices for fast time-window queries on the dashboard
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_readings_time ON readings(timestamp)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_faults_time ON faults(timestamp)")
        conn.commit()


def log_reading(data: Dict[str, Any], db_path: str = DEFAULT_DB_PATH) -> None:
    """Inserts a new sensor telemetry reading."""
    ts = data.get("timestamp", get_iso_timestamp())
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO readings (timestamp, temperature, humidity, water_level, ph, ec, root_temp)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            ts,
            data.get("temperature"),
            data.get("humidity"),
            data.get("water_level"),
            data.get("ph"),
            data.get("ec"),
            data.get("root_temp")
        ))
        conn.commit()


def log_pump_event(state: str, mode: str, reason: str = "", latency_ms: float = 0.0,
                   db_path: str = DEFAULT_DB_PATH) -> None:
    """Records a pump state change (e.g. turned ON/OFF, reason, latency)."""
    ts = get_iso_timestamp()
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO pump_events (timestamp, state, mode, trigger_reason, latency_ms)
            VALUES (?, ?, ?, ?, ?)
        """, (ts, state, mode, reason, latency_ms))
        conn.commit()


def log_fault(fault_type: str, sensor_name: str, severity: str, message: str,
              db_path: str = DEFAULT_DB_PATH) -> None:
    """Records an anomaly or fault detection alert."""
    ts = get_iso_timestamp()
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO faults (timestamp, fault_type, sensor_name, severity, message, resolved)
            VALUES (?, ?, ?, ?, ?, 0)
        """, (ts, fault_type, sensor_name, severity, message))
        conn.commit()


def log_latency(architecture: str, latency_ms: float, status: str = "SUCCESS",
                db_path: str = DEFAULT_DB_PATH) -> None:
    """Logs decision latency for Fog vs Cloud comparison."""
    ts = get_iso_timestamp()
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO latency_logs (timestamp, architecture, latency_ms, status)
            VALUES (?, ?, ?, ?)
        """, (ts, architecture, latency_ms, status))
        conn.commit()


def get_recent_readings(minutes: int = 5, db_path: str = DEFAULT_DB_PATH) -> List[Dict[str, Any]]:
    """Fetches sensor readings logged within the last N minutes."""
    cutoff = (datetime.utcnow() - timedelta(minutes=minutes)).isoformat()
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT * FROM readings
            WHERE timestamp >= ?
            ORDER BY id ASC
        """, (cutoff,))
        rows = cursor.fetchall()
        return [dict(row) for row in rows]


def get_latest_reading(db_path: str = DEFAULT_DB_PATH) -> Optional[Dict[str, Any]]:
    """Fetches the single most recent sensor reading."""
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT * FROM readings
            ORDER BY id DESC LIMIT 1
        """)
        row = cursor.fetchone()
        return dict(row) if row else None


def get_recent_faults(limit: int = 20, db_path: str = DEFAULT_DB_PATH) -> List[Dict[str, Any]]:
    """Fetches the latest active or historic faults."""
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT * FROM faults
            ORDER BY id DESC LIMIT ?
        """, (limit,))
        rows = cursor.fetchall()
        return [dict(row) for row in rows]


def get_recent_pump_events(limit: int = 15, db_path: str = DEFAULT_DB_PATH) -> List[Dict[str, Any]]:
    """Fetches recent pump state transitions."""
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT * FROM pump_events
            ORDER BY id DESC LIMIT ?
        """, (limit,))
        rows = cursor.fetchall()
        return [dict(row) for row in rows]


def get_latency_data(limit: int = 100, db_path: str = DEFAULT_DB_PATH) -> List[Dict[str, Any]]:
    """Fetches latency comparison records."""
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT * FROM latency_logs
            ORDER BY id DESC LIMIT ?
        """, (limit,))
        rows = cursor.fetchall()
        return [dict(row) for row in rows]


def clear_all_faults(db_path: str = DEFAULT_DB_PATH) -> None:
    """Marks all faults as resolved or clears them when student clicks 'Clear Faults'."""
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE faults SET resolved = 1 WHERE resolved = 0")
        conn.commit()


if __name__ == "__main__":
    init_db()
    print("SQLite database initialized successfully at:", DEFAULT_DB_PATH)
