# backend/config.py
"""
Application Configuration
All secrets are read from environment variables (/.env file for local dev).
NEVER hardcode credentials in this file.
"""

import os
from dotenv import load_dotenv

# Silently loads .env from the project root (ignored if file doesn't exist)
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.env'))

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class Config:
    # ============================================
    # DATA DIRECTORY (CSV backup / migration only)
    # ============================================
    OUTPUT_DIR     = os.path.join(BASE_DIR, 'Data')
    MISSION_FILE   = os.path.join(OUTPUT_DIR, 'mission_executions.csv')
    TELEMETRY_FILE = os.path.join(OUTPUT_DIR, 'device_telemetry.csv')
    SCHEDULER_FILE = os.path.join(OUTPUT_DIR, 'scheduler_executions.csv')
    MAP_FILE       = os.path.join(OUTPUT_DIR, 'pois.json')

    # ============================================
    # PostgreSQL — Relational data
    # (missions, schedulers, waypoints, paths)
    # ============================================
    DATABASE_URL = os.getenv('DATABASE_URL')
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL environment variable is not set."
                            "Add it to your .env file (see .env.example)"
        )

    # Individual components (for logging — do NOT log password in prod)
    DATABASE = {
        'user':     os.getenv('DB_USER'),
        'password': os.getenv('DB_PASSWORD'),
        'host':     os.getenv('DB_HOST'),
        'port':     os.getenv('DB_PORT', '5432'),
        'database': os.getenv('DB_NAME'),
    }
    if not DATABASE['password']:
        raise RuntimeError("DB_PASSWORD environment variable is not set."
                            "Add it to your .env file"
        )

    # ============================================
    # InfluxDB — Time-series telemetry
    # (battery, speed, position, status)
    # ============================================
    INFLUXDB = {
        'url':    os.getenv('INFLUX_URL',    'http://localhost:8086'),
        'token':  os.getenv('INFLUX_TOKEN',  ''),          # required — must be set in .env
        'org':    os.getenv('INFLUX_ORG',    'trizlabz'),
        'bucket': os.getenv('INFLUX_BUCKET', 'device_core'),
    }

    # ============================================
    # ANALYTICS
    # ============================================
    HOURS_WINDOW = int(os.getenv('HOURS_WINDOW', '24'))
    DATA_SOURCE  = 'dual'   # 'dual' = postgres + influxdb

    # ============================================
    # FLASK
    # ============================================
    DEBUG = os.getenv('FLASK_DEBUG', 'false').lower() in ('true', '1', 'yes')
    HOST  = os.getenv('FLASK_HOST', '127.0.0.1')
    PORT  = int(os.getenv('FLASK_PORT', '5001'))