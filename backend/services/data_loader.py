# backend/services/data_loader.py
"""
Dual Database Data Loader
- PostgreSQL  → missions, schedulers, waypoints, paths
- InfluxDB    → telemetry time-series (battery, speed, position, status)

InfluxDB schema (confirmed via Data Explorer):
  Measurement : device_telemetry
  Tags        : deviceId, endpointId   ← deviceId is a TAG (not a field)
  Fields      : battery, speed, driving, headerId, vda5050, timestamp

Since deviceId is a tag, Flux tag filters (r["deviceId"] == "...") work
correctly and are applied before fetching data for efficiency. The pandas
_filter_to_device() fallback runs after pivot for UUID prefix edge-cases.
"""

import pandas as pd
from datetime import datetime, timedelta
from sqlalchemy import create_engine, text
from influxdb_client import InfluxDBClient
import pytz
import sys
import os
import re

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import Config

_DEVICE_ID_PATTERN = re.compile(r'^[a-zA-Z0-9\-]{1,64}$')

# ============================================
# DATETIME HANDLER
# ============================================

class DateTimeHandler:

    @staticmethod
    def get_kolkata_tz():
        return pytz.timezone("Asia/Kolkata")

    @staticmethod
    def now_kolkata():
        return datetime.now(DateTimeHandler.get_kolkata_tz())


# ============================================
# POSTGRESQL LOADER
# ============================================

class PostgreSQLLoader:

    def __init__(self):
        self.engine = create_engine(Config.DATABASE_URL)
        print(f"[DATA] PostgreSQL connected: {Config.DATABASE['database']}")

    def get_latest_data_range(self, device_id):
        query = text("""
            SELECT MAX("startedAt") as latest_time
            FROM mission_executions
            WHERE "deviceId" = :device
        """)
        try:
            df = pd.read_sql(query, self.engine, params={"device": device_id})
            if not df.empty and pd.notna(df['latest_time'].iloc[0]):
                latest = pd.to_datetime(df['latest_time'].iloc[0])
                return latest - timedelta(hours=24), latest
            return None, None
        except Exception as e:
            print(f"[WARN] Error finding latest data: {e}")
            return None, None

    def load_missions(self, device_id, start_time=None, end_time=None, limit=None):
        if start_time and end_time:
            query  = text("""
                SELECT * FROM mission_executions
                WHERE "deviceId" = :device
                  AND "startedAt" >= :start_time
                  AND "startedAt" <= :end_time
                ORDER BY "startedAt" DESC
            """)
            params = {"device": device_id, "start_time": start_time, "end_time": end_time}
        elif limit:
            query  = text("""
                SELECT * FROM mission_executions
                WHERE "deviceId" = :device
                ORDER BY "startedAt" DESC LIMIT :limit
            """)
            params = {"device": device_id, "limit": limit}
        else:
            query  = text("""
                SELECT * FROM mission_executions
                WHERE "deviceId" = :device
                ORDER BY "startedAt" DESC
            """)
            params = {"device": device_id}
        try:
            df = pd.read_sql(query, self.engine, params=params)
            print(f"[OK] PostgreSQL: {len(df)} missions loaded")
            return df
        except Exception as e:
            print(f"[ERROR] Error loading missions: {e}")
            return pd.DataFrame()

    def get_mission_counts(self, device_id):
        query = text("""
            SELECT status, COUNT(*) as count
            FROM mission_executions
            WHERE "deviceId" = :device
            GROUP BY status
        """)
        try:
            return pd.read_sql(query, self.engine, params={"device": device_id})
        except Exception as e:
            print(f"[ERROR] Error getting mission counts: {e}")
            return pd.DataFrame()

    def get_devices(self):
        query = text("""
            SELECT DISTINCT "deviceId"
            FROM mission_executions
            ORDER BY "deviceId"
        """)
        try:
            df      = pd.read_sql(query, self.engine)
            devices = df["deviceId"].tolist()
            print(f"[DEVICES] Found {len(devices)} devices in PostgreSQL")
            return devices
        except Exception as e:
            print(f"[ERROR] Error fetching devices: {e}")
            return []

    def close(self):
        self.engine.dispose()


# ============================================
# INFLUXDB LOADER
# ============================================

class InfluxDBLoader:

    def __init__(self):
        cfg = Config.INFLUXDB
        try:
            self.client = InfluxDBClient(
                url=cfg['url'],
                token=cfg['token'],
                org=cfg['org'],
            )
            self.query_api = self.client.query_api()
            self.org    = cfg['org']
            self.bucket = cfg['bucket']
            health = self.client.health()
            if health.status == "pass":
                print(f"[INFLUX] InfluxDB connected: bucket={self.bucket}")
            else:
                print(f"[WARN] InfluxDB health: {health.status}")
        except Exception as e:
            print(f"[ERROR] InfluxDB connection failed: {e}")
            self.client    = None
            self.query_api = None

    def _to_utc_rfc3339(self, dt) -> str:
        """
        Convert any datetime (naive, Kolkata-aware, or UTC-aware) to a
        UTC RFC-3339 string that InfluxDB's Flux range() accepts.
        """
        ts = pd.Timestamp(dt)
        if ts.tzinfo is None:
            # Assume Kolkata if naive (matches your DateTimeHandler)
            ts = ts.tz_localize("Asia/Kolkata")
        return ts.tz_convert("UTC").isoformat().replace("+00:00", "Z")

    def load_telemetry(self, device_id: str, start_time, end_time) -> pd.DataFrame:
        if self.query_api is None:
            print("[WARN] InfluxDB not connected, returning empty telemetry")
            return pd.DataFrame()

        if not _DEVICE_ID_PATTERN.match(device_id):
            print(f"[ERROR] InfluxDB: rejected malformed device_id: {device_id!r}")
            return pd.DataFrame()

        start_str = self._to_utc_rfc3339(start_time)
        end_str   = self._to_utc_rfc3339(end_time)

        # FIX 1: Query WITHOUT customerId tag filter so we catch ALL series
        # (some records have customerId tag, some don't — both must be included)
        flux_query = f"""
        from(bucket: "{self.bucket}")
          |> range(start: {start_str}, stop: {end_str})
          |> filter(fn: (r) => r["_measurement"] == "device_telemetry")
          |> filter(fn: (r) => r["deviceId"] == "{device_id}")
          |> filter(fn: (r) =>
              r["_field"] == "battery"       or
              r["_field"] == "speed"         or
              r["_field"] == "status"        or
              r["_field"] == "driving"       or
              r["_field"] == "operatingMode" or
              r["_field"] == "position"      or
              r["_field"] == "zoneId"        or
              r["_field"] == "orderId"       or
              r["_field"] == "actionStates"  or
              r["_field"] == "errors"        or
              r["_field"] == "lastNodeId"    or
              r["_field"] == "pathName"      or
              r["_field"] == "headerId"      or
              r["_field"] == "vda5050"       or
              r["_field"] == "source"        or
              r["_field"] == "type"
          )
          |> sort(columns: ["_time"])
        """

        try:
            tables  = self.query_api.query(flux_query, org=self.org)
            records = []
            for table in tables:
                for record in table.records:
                    records.append({
                        "timestamp":  record.get_time(),
                        "deviceId":   record.values.get("deviceId", device_id),
                        "endpointId": record.values.get("endpointId", ""),
                        "_field":     record.get_field(),
                        "_value":     record.get_value(),
                    })

            if not records:
                print(f"[WARN] InfluxDB: no telemetry records found for {device_id[:8]} in range")
                print(f"   Range: {start_str} -> {end_str}")
                return pd.DataFrame()

            raw = pd.DataFrame(records)
            raw["ts"] = pd.to_datetime(raw["timestamp"], utc=True).dt.tz_localize(None)
            raw = raw.drop(columns=["timestamp"])

            # Deduplicate before pivot
            raw = raw.drop_duplicates(subset=["ts", "deviceId", "_field"], keep="last")

            print(f"[INFLUX] InfluxDB raw records: {len(raw)} | fields: {sorted(raw['_field'].unique())}")

            # FIX 2: Use pivot (faster, cleaner than pivot_table)
            df = raw.pivot_table(
                index=["ts", "deviceId", "endpointId"],
                columns="_field",
                values="_value",
                aggfunc="last",
            ).reset_index()

            df.columns.name = None
            df = df.rename(columns={"ts": "timestamp"})

            # Numeric coercion for known numeric fields
            for col in ["battery", "speed"]:
                if col in df.columns:
                    df[col] = pd.to_numeric(df[col], errors="coerce")

            print(f"[OK] InfluxDB: {len(df)} telemetry rows | columns: {list(df.columns)}")
            return df

        except Exception as e:
            print(f"[ERROR] InfluxDB query failed: {e}")
            import traceback
            traceback.print_exc()
            return pd.DataFrame()

    def get_devices(self):
        if self.query_api is None:
            return []
        flux_query = f"""
        import "influxdata/influxdb/schema"
        schema.tagValues(bucket: "{self.bucket}", tag: "deviceId")
        """
        try:
            tables  = self.query_api.query(flux_query, org=self.org)
            devices = [r.get_value() for t in tables for r in t.records if r.get_value()]
            print(f"[DEVICES] InfluxDB: found {len(devices)} device(s) via tagValues")
            return devices
        except Exception as e:
            print(f"[WARN] Could not fetch InfluxDB device list: {e}")
            return []

    def close(self):
        if self.client:
            self.client.close()
# ============================================
# TIME WINDOW
# ============================================

class TimeWindowCalculator:

    @staticmethod
    def calculate_24h_window():
        now   = DateTimeHandler.now_kolkata()
        start = now - timedelta(hours=24)
        return start, now


# ============================================
# MAIN DATA LOADER SERVICE
# ============================================

class DataLoaderService:

    def __init__(self):
        self.postgres = PostgreSQLLoader()
        self.influx   = InfluxDBLoader()
        self.window   = TimeWindowCalculator()

    def load_device_list(self):
        pg_devices  = self.postgres.get_devices()
        inf_devices = self.influx.get_devices()
        all_devices = list(dict.fromkeys(pg_devices + inf_devices))
        print(f"[DEVICES] Total unique devices: {len(all_devices)}")
        return all_devices

    def load_data_for_device(self, device_id: str,
                             start_date: str = None, end_date: str = None) -> dict:

        print("\n" + "=" * 60)
        print(f"[DATA] Loading data for device: {device_id}")
        print("=" * 60)

        # ── Date range: custom override vs auto-window ───────────────
        if start_date and end_date:
            # User-specified range: load ALL missions in that date span
            start_time = pd.Timestamp(start_date).normalize()
            end_time   = pd.Timestamp(end_date).normalize() + timedelta(hours=23, minutes=59, seconds=59)
            print(f"[RANGE] Custom range: {start_date} -> {end_date}")
            missions_df = self.postgres.load_missions(device_id, start_time, end_time)

            # Normalise timestamps
            if not missions_df.empty:
                for col in ["startedAt", "completedAt", "createdAt", "updatedAt"]:
                    if col in missions_df.columns:
                        missions_df[col] = pd.to_datetime(
                            missions_df[col], errors="coerce", utc=True
                        ).dt.tz_convert(None)

            print(f"   Missions loaded: {len(missions_df)}")

            # InfluxDB telemetry for custom range
            influx_start = start_time - timedelta(hours=1)
            influx_end   = end_time   + timedelta(hours=1)
            print(f"\n[INFLUX] Loading telemetry from InfluxDB (custom range)...")
            telemetry_df = self.influx.load_telemetry(device_id, influx_start, influx_end)
            print(f"   Telemetry records: {len(telemetry_df)}")

            print("\n" + "=" * 60 + "\n[OK] Data loading complete\n" + "=" * 60 + "\n")
            return {
                "missions":   missions_df,
                "telemetry":  telemetry_df,
                "start_time": start_time,
                "end_time":   end_time,
                "device_id":  device_id,
            }

        # ── Auto-window (default) ────────────────────────────────────
        start_time, end_time = self.window.calculate_24h_window()
        print(f"[WINDOW] Window: {start_time.strftime('%Y-%m-%d %H:%M')} -> "
              f"{end_time.strftime('%Y-%m-%d %H:%M')}")

        print("\n[MISSIONS] Loading missions from PostgreSQL...")
        missions_df = self.postgres.load_missions(device_id, start_time, end_time)

        if missions_df.empty:
            print("[WARN] No missions in last 24h, searching for most recent 24h with data...")
            recent_start, recent_end = self.postgres.get_latest_data_range(device_id)
            if recent_start and recent_end:
                print(f"[OK] Using: {recent_start.strftime('%Y-%m-%d %H:%M')} -> "
                      f"{recent_end.strftime('%Y-%m-%d %H:%M')}")
                start_time  = recent_start
                end_time    = recent_end
                missions_df = self.postgres.load_missions(device_id, start_time, end_time)

        else:
            # Staleness check: if missions exist but are >24h old, re-fetch latest window
            if 'startedAt' in missions_df.columns:
                latest_mission = pd.to_datetime(
                    missions_df['startedAt'].max(), utc=True
                ).tz_convert(None)          # was: tz_localize (wrong — already UTC-aware)
                hours_ago = (pd.Timestamp.now() - latest_mission).total_seconds() / 3600
                if hours_ago > 24:
                    print(f"[WARN] Latest mission is {hours_ago:.0f} hours old, re-fetching latest window...")
                    recent_start, recent_end = self.postgres.get_latest_data_range(device_id)
                    if recent_start and recent_end:
                        print(f"[OK] Using: {recent_start.strftime('%Y-%m-%d %H:%M')} -> "
                              f"{recent_end.strftime('%Y-%m-%d %H:%M')}")
                        start_time  = recent_start
                        end_time    = recent_end
                        missions_df = self.postgres.load_missions(device_id, start_time, end_time)

        # Normalise mission timestamp columns to tz-naive datetime
        # (required for downstream comparisons and JSON serialisation)
        if not missions_df.empty:
            for col in ["startedAt", "completedAt", "createdAt", "updatedAt"]:
                if col in missions_df.columns:
                    missions_df[col] = pd.to_datetime(
                        missions_df[col], errors="coerce", utc=True
                    ).dt.tz_convert(None)

        print(f"   Missions loaded: {len(missions_df)}")

        # ── InfluxDB: telemetry ─────────────────────────────────────
        influx_start = start_time - timedelta(hours=1)
        influx_end   = end_time   + timedelta(hours=1)
        print(f"\n[INFLUX] Loading telemetry from InfluxDB...")
        telemetry_df = self.influx.load_telemetry(device_id, influx_start, influx_end)

        if telemetry_df.empty:
            print("[WARN] No telemetry in window, trying +/-6h buffer...")
            telemetry_df = self.influx.load_telemetry(
                device_id,
                start_time - timedelta(hours=6),
                end_time   + timedelta(hours=6),
            )

        print(f"   Telemetry records: {len(telemetry_df)}")
        if not telemetry_df.empty and 'battery' in telemetry_df.columns:
            batt = pd.to_numeric(telemetry_df['battery'], errors='coerce').dropna()
            if not batt.empty:
                print(f"   Battery range: {batt.min():.1f}% - {batt.max():.1f}%")

        print("\n" + "=" * 60)
        print("[OK] Data loading complete")
        print("=" * 60 + "\n")

        return {
            "missions":   missions_df,
            "telemetry":  telemetry_df,
            "start_time": start_time,
            "end_time":   end_time,
            "device_id":  device_id,
        }

    def close(self):
        self.postgres.close()
        self.influx.close()


# ============================================
# QUICK TEST
# ============================================

if __name__ == "__main__":
    print("\n" + "=" * 60)
    print("TESTING DUAL DATABASE LOADER")
    print("=" * 60 + "\n")

    service = DataLoaderService()
    devices = service.load_device_list()
    print(f"\n[DEVICES] Devices: {devices[:3]}")

    if devices:
        data = service.load_data_for_device(devices[0])
        print(f"\n[DATA] Results for {devices[0]}:")
        print(f"   Missions:  {len(data['missions'])}")
        print(f"   Telemetry: {len(data['telemetry'])}")
        if not data['telemetry'].empty:
            print(f"   Columns:   {list(data['telemetry'].columns)}")
            if 'deviceId' in data['telemetry'].columns:
                print(f"   DeviceIds: {data['telemetry']['deviceId'].unique().tolist()}")

    service.close()
    print("\n[OK] Test complete")