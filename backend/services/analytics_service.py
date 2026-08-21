# backend/services/analytics_service.py
"""
Analytics Service - Dual Database Version
PostgreSQL: missions, schedulers
InfluxDB: telemetry time-series

Battery data quality
--------------------
The InfluxDB 'battery' field contains sub-minute oscillations that pass
rate-based filters but inflate drain totals. The definitive fix is to
resample battery readings to 1-minute median buckets before any drain
calculation. This collapses all intra-minute noise to one stable value
per minute, leaving only real slow drift visible in drain/attribution.

The battery LEVEL chart (right) already worked because hourly mean()
averaged out the noise. The drain chart and attributor now use the same
1-minute resampled data so all three views are internally consistent.

Charts:
  Left  — Hourly Battery Drain (%): real discharge per active hour
  Right — Battery Level Over Time (%): avg SOC per hour as a line chart

Fixes applied (2026-04-07):
  - Bug 1: transform_battery() now receives device-filtered raw telemetry
            via transform_battery_from_clean(), not the empty win_telemetry.
  - Bug 2: Charge cycle detection now runs on 1-min resampled (noise-free)
            data via transform_battery_from_clean(), not raw noisy telemetry.
  - Bug 3: clear_cache() now correctly matches compound cache keys
            (format: "device_id|start|end") when clearing per-device.
"""


import time

import pandas as pd
import numpy as np
import re
from typing import Dict, List, Optional
from backend.services.data_loader import DataLoaderService
from backend.transformers.trizlabz_transformer import (
    transform_utilization,
    transform_task_performance,
    transform_battery,
    transform_hourly_utilization,
    transform_hourly_energy,
    transform_task_timeline,
)
import threading

MAX_BATTERY_DROP_PCT_PER_MINUTE = 2.0
RESAMPLE_INTERVAL = '1min'   # bucket size for noise reduction
_DEVICE_ID_PATTERN = re.compile(r'^[a-zA-Z0-9\-]{1,64}$')

def _strip_tz(ts):
    if ts is None:
        return None
    try:
        t = pd.Timestamp(ts)
        return t.tz_localize(None) if t.tzinfo is not None else t
    except Exception:
        return None


def _filter_device(telemetry: pd.DataFrame, device_id: str) -> pd.DataFrame:
    if telemetry.empty or not device_id:
        return telemetry.copy()
    short_id = device_id.split('-')[0] if '-' in device_id else device_id
    for col in ('deviceId', 'device_id'):
        if col not in telemetry.columns:
            continue
        vals = telemetry[col].astype(str)
        mask = vals == device_id
        if mask.any():
            return telemetry[mask].copy()
        mask = vals == short_id
        if mask.any():
            result = telemetry[mask].copy()
            result[col] = device_id
            return result
        mask = vals.str.startswith(short_id)
        if mask.any():
            result = telemetry[mask].copy()
            result[col] = device_id
            return result
        print(f"[FILTER] WARNING: no rows matched device '{device_id[:8]}'. "
              f"Sample: {vals.unique()[:5].tolist()}")
        return pd.DataFrame(columns=telemetry.columns)
    return telemetry.copy()


def _trim_to_window(telemetry, start_time, end_time, pad_hours=0.5):
    if telemetry.empty or 'timestamp' not in telemetry.columns:
        return telemetry
    start = _strip_tz(start_time)
    end   = _strip_tz(end_time)
    if start is None or end is None:
        return telemetry
    pad = pd.Timedelta(hours=pad_hours)
    ts  = telemetry['timestamp']
    if ts.dt.tz is not None:
        ts = ts.dt.tz_localize(None)
    trimmed = telemetry[(ts >= start - pad) & (ts <= end + pad)].copy()
    print(f"[TRIM] {len(telemetry)} → {len(trimmed)} rows "
          f"({start.strftime('%m-%d %H:%M')} → {end.strftime('%m-%d %H:%M')} ±{pad_hours}h)")
    return trimmed


def _resample_battery(df: pd.DataFrame, interval: str = RESAMPLE_INTERVAL) -> pd.DataFrame:
    """
    Resample battery readings to median per time bucket.

    This is the definitive noise fix. Sub-minute oscillations (17+ readings/min
    that zigzag between 0-100%) collapse to a single stable median value per
    minute. Only genuine slow drift (real battery discharge at ~0.05%/min)
    survives resampling.

    Input:  DataFrame with 'timestamp' and 'battery_n' columns (any frequency)
    Output: DataFrame with one row per interval, reset integer index.
            Columns: timestamp (bucket start), battery_n (median), ts_naive.
    """
    if df.empty or 'battery_n' not in df.columns or 'timestamp' not in df.columns:
        return df

    work = df.copy()
    ts   = work['timestamp']
    if ts.dt.tz is not None:
        ts = ts.dt.tz_localize(None)
    work['ts_naive'] = ts

    work = work.set_index('ts_naive').sort_index()
    resampled = work['battery_n'].resample(interval).median().dropna()

    if resampled.empty:
        return pd.DataFrame()

    result = pd.DataFrame({
        'timestamp': resampled.index,
        'ts_naive':  resampled.index,
        'battery_n': resampled.values,
    }).reset_index(drop=True)

    orig_min  = df['battery_n'].min()
    orig_max  = df['battery_n'].max()
    clean_min = result['battery_n'].min()
    clean_max = result['battery_n'].max()
    print(f"[RESAMPLE] {len(df)} → {len(result)} rows ({interval} median) | "
          f"{orig_min:.1f}–{orig_max:.1f}% → {clean_min:.1f}–{clean_max:.1f}%")
    return result


def _clean_battery(df: pd.DataFrame) -> pd.DataFrame:
    """
    Secondary noise filter: remove physically impossible rate-of-change readings.
    Applied AFTER resampling as a safety net.
    Returns a DataFrame with reset integer index.
    """
    if df.empty or 'battery_n' not in df.columns or 'ts_naive' not in df.columns:
        return df

    df   = df.sort_values('ts_naive').copy()
    bat  = df['battery_n'].values
    ts   = df['ts_naive'].values
    keep = [True]
    for i in range(1, len(bat)):
        dt_min = (ts[i] - ts[i-1]) / np.timedelta64(1, 'm')
        if dt_min <= 0:
            keep.append(False)
            continue
        rate = abs(bat[i] - bat[i-1]) / dt_min
        keep.append(rate <= MAX_BATTERY_DROP_PCT_PER_MINUTE)

    cleaned = df[keep].reset_index(drop=True)
    if len(cleaned) < 2:
        return df.reset_index(drop=True)
    return cleaned


def _prepare_battery_df(telemetry, device_id, start_time, end_time):
    """
    Full pipeline: filter device → trim window → resample → clean.
    Returns a stable 1-min-resolution battery DataFrame with reset index.
    Columns: timestamp, ts_naive, battery_n.
    """
    df = _filter_device(telemetry, device_id)
    df = _trim_to_window(df, start_time, end_time, pad_hours=0.5)
    if df.empty or 'battery' not in df.columns:
        return pd.DataFrame()

    df = df.copy()
    df['battery_n'] = pd.to_numeric(df['battery'], errors='coerce')
    df = df.dropna(subset=['battery_n', 'timestamp']).copy()

    df = _resample_battery(df)
    if df.empty:
        return pd.DataFrame()

    df = _clean_battery(df)
    return df


# ---------------------------------------------------------------------------
# FIX Bug 1 + Bug 2: transform_battery_from_clean
# ---------------------------------------------------------------------------

def transform_battery_from_clean(clean_df: pd.DataFrame) -> dict:
    """
    Run BatteryTransformer on already-resampled 1-min noise-free data.

    Fixes two bugs:
      Bug 1 — previously transform_battery() received win_telemetry which
               could be empty when InfluxDB window didn't match mission window,
               causing Battery Health and Charge Cycles to show N/A.
      Bug 2 — previously charge cycle detection ran on raw noisy telemetry,
               causing false positives (noise spikes counted as charge events)
               and false negatives (slow real charges missed). Now it runs on
               clean 1-min median data, matching the drain/attributor pipeline.
    """
    empty = {
        'current_charge': 0.0,
        'battery_health': 0.0,
        'battery_drain_pct': 0.0,
        'estimated_runtime': 0.0,
        'charge_cycles_today': 0,
    }
    if clean_df.empty or 'battery_n' not in clean_df.columns:
        print("[BATTERY] clean_df empty — returning zero KPIs")
        return empty

    # Build a minimal telemetry-like DataFrame that BatteryTransformer expects:
    # it only needs 'timestamp' and 'battery' columns.
    proxy = clean_df[['timestamp', 'battery_n']].copy()
    proxy = proxy.rename(columns={'battery_n': 'battery'})
    result = transform_battery(proxy)
    print(f"[BATTERY] health={result['battery_health']}%  "
          f"current={result['current_charge']}%  "
          f"cycles={result['charge_cycles_today']}  "
          f"drain={result['battery_drain_pct']}%")
    return result


# ---------------------------------------------------------------------------
# BatteryAttributor
# ---------------------------------------------------------------------------

class BatteryAttributor:
    """
    Pre-computes per-mission battery attribution from 1-minute-resampled,
    noise-cleaned, device-filtered, window-trimmed telemetry.
    """

    def __init__(self, telemetry, missions, device_id, start_time=None, end_time=None):
        self._battery_used:       Dict[int, float]           = {}
        self._start_battery:      Dict[int, Optional[float]] = {}
        self._end_battery:        Dict[int, Optional[float]] = {}
        self._method:             Dict[int, str]             = {}
        self._idle_drain_by_hour: Dict[pd.Timestamp, float]  = {}

        if telemetry.empty or missions is None or missions.empty:
            return

        clean_df = _prepare_battery_df(telemetry, device_id, start_time, end_time)
        if clean_df.empty:
            print(f"[ATTRIBUTOR] No usable battery data for {device_id[:8]}")
            return

        batt = clean_df['battery_n']
        print(f"[ATTRIBUTOR] Ready: {len(clean_df)} 1-min buckets | "
              f"battery {batt.min():.1f}%–{batt.max():.1f}%")

        self._compute(clean_df, missions)

    def _compute(self, df, missions):
        mdf = missions[missions['startedAt'].notna() & missions['completedAt'].notna()].copy()
        if mdf.empty:
            return
        mdf = mdf.sort_values('startedAt', ascending=False).reset_index(drop=True)

        def _s(ts):
            t = pd.Timestamp(ts)
            return t.tz_localize(None) if t.tzinfo else t

        mdf['ms'] = mdf['startedAt'].apply(_s)
        mdf['me'] = mdf['completedAt'].apply(_s)

        for idx in mdf.index:
            self._battery_used[idx]  = 0.0
            self._start_battery[idx] = None
            self._end_battery[idx]   = None
            self._method[idx]        = 'unavailable'

        for i in range(len(df) - 1):
            a_ts  = df.at[i,     'ts_naive']
            b_ts  = df.at[i + 1, 'ts_naive']
            a_bat = float(df.at[i,     'battery_n'])
            b_bat = float(df.at[i + 1, 'battery_n'])
            iv_s  = (b_ts - a_ts).total_seconds()
            if iv_s <= 0:
                continue
            drain = a_bat - b_bat

            if drain <= 0:
                overlapping = mdf[(mdf['ms'] < b_ts) & (mdf['me'] > a_ts)]
                for idx, row in overlapping.iterrows():
                    if self._start_battery[idx] is None:
                        frac = max(0.0, min(1.0, (row['ms'] - a_ts).total_seconds() / iv_s))
                        self._start_battery[idx] = round(a_bat + (b_bat - a_bat) * frac, 2)
                    frac_e = max(0.0, min(1.0, (row['me'] - a_ts).total_seconds() / iv_s))
                    self._end_battery[idx] = round(a_bat + (b_bat - a_bat) * frac_e, 2)
                    if self._method[idx] == 'unavailable':
                        self._method[idx] = 'interpolated'
                continue

            overlapping = mdf[(mdf['ms'] < b_ts) & (mdf['me'] > a_ts)]
            total_mission_s = 0.0
            overlaps: Dict[int, float] = {}
            for idx, row in overlapping.iterrows():
                ov = (min(row['me'], b_ts) - max(row['ms'], a_ts)).total_seconds()
                if ov > 0:
                    overlaps[idx] = ov
                    total_mission_s += ov

            for idx, ov_s in overlaps.items():
                share = drain * (ov_s / iv_s)
                self._battery_used[idx] = round(self._battery_used[idx] + share, 3)
                self._method[idx] = 'interpolated'
                if self._start_battery[idx] is None:
                    frac_s = max(0.0, min(1.0, (mdf.at[idx, 'ms'] - a_ts).total_seconds() / iv_s))
                    self._start_battery[idx] = round(a_bat - drain * frac_s, 2)
                frac_e = max(0.0, min(1.0, (mdf.at[idx, 'me'] - a_ts).total_seconds() / iv_s))
                self._end_battery[idx] = round(a_bat - drain * frac_e, 2)

            idle_s = iv_s - total_mission_s
            if idle_s > 0:
                hour_key = a_ts.floor('h')
                self._idle_drain_by_hour[hour_key] = round(
                    self._idle_drain_by_hour.get(hour_key, 0.0) + drain * (idle_s / iv_s), 3)

        for idx, row in mdf.iterrows():
            pad    = pd.Timedelta(seconds=90)
            in_win = df[(df['ts_naive'] >= row['ms'] - pad) & (df['ts_naive'] <= row['me'] + pad)]
            bat    = in_win['battery_n'].dropna()
            if len(bat) >= 2:
                sb = round(float(bat.iloc[0]), 2)
                eb = round(float(bat.iloc[-1]), 2)
                self._start_battery[idx] = sb
                self._end_battery[idx]   = eb
                self._battery_used[idx]  = round(abs(sb - eb), 3)
                self._method[idx]        = 'direct'

        total_mission = sum(self._battery_used.values())
        total_idle    = sum(self._idle_drain_by_hour.values())
        print(f"[ATTRIBUTOR] {len(self._battery_used)} missions | "
              f"mission drain: {total_mission:.2f}%  "
              f"idle drain: {total_idle:.2f}%  "
              f"total: {total_mission + total_idle:.2f}%")
        for idx in list(self._battery_used.keys())[:5]:
            print(f"  mission[{idx}]: used={self._battery_used[idx]:.3f}%  "
                  f"start={self._start_battery[idx]}%  "
                  f"end={self._end_battery[idx]}%  "
                  f"({self._method[idx]})")

    def get(self, mission_index):
        if mission_index not in self._battery_used:
            return self._empty()
        start_b = self._start_battery.get(mission_index) or 0.0
        end_b   = self._end_battery.get(mission_index)   or 0.0
        used    = round(self._battery_used[mission_index], 2)
        return {
            'start_battery':      round(start_b, 2),
            'end_battery':        round(end_b,   2),
            'battery_used':       used,
            'avg_battery':        round((start_b + end_b) / 2, 2),
            'min_battery':        round(min(start_b, end_b), 2),
            'max_battery':        round(max(start_b, end_b), 2),
            'measurement_method': self._method.get(mission_index, 'unavailable'),
        }

    @staticmethod
    def _empty():
        return {'start_battery': 0.0, 'end_battery': 0.0, 'battery_used': 0.0,
                'avg_battery': 0.0, 'min_battery': 0.0, 'max_battery': 0.0,
                'measurement_method': 'unavailable'}


# ---------------------------------------------------------------------------
# Supporting builders
# ---------------------------------------------------------------------------

class SummaryBuilder:
    @staticmethod
    def build(utilization, task_perf, battery, schedulers):
        sc = sf = 0
        if not schedulers.empty:
            sc = int(schedulers['totalMissionsCompleted'].sum())
            sf = int(schedulers['totalMissionsFailed'].sum())
        return {
            'working_hours':       utilization['working_hours'],
            'idle_hours':          utilization['idle_hours'],
            'utilization_rate':    utilization['utilization_rate'],
            'tasks_completed':     task_perf['completed_tasks'],
            'tasks_failed':        task_perf['failed_tasks'],
            'success_rate':        task_perf['success_rate'],
            'current_battery':     battery['current_charge'],
            'battery_health':      battery['battery_health'],
            'energy_consumed_kwh': battery['battery_drain_pct'],
            'estimated_runtime':   battery['estimated_runtime'],
            'charge_cycles_today': battery['charge_cycles_today'],
            'scheduler_completed': sc,
            'scheduler_failed':    sf,
        }


class RowBuilder:
    @staticmethod
    def build_telemetry_rows(telemetry):
        if telemetry.empty:
            return []
        cols = ['timestamp', 'deviceId', 'status', 'battery', 'speed',
                'driving', 'zoneId', 'lastNodeId', 'operatingMode']
        df = telemetry[[c for c in cols if c in telemetry.columns]].copy()
        df = df.sort_values('timestamp').tail(1)
        df['timestamp'] = df['timestamp'].astype(str)
        return df.fillna('').to_dict(orient='records')

    @staticmethod
    def build_mission_rows(missions):
        if missions.empty:
            return []
        cols = ['missionName', 'deviceId', 'status', 'distanceTravelled',
                'completedWaypoints', 'totalWaypoints', 'startedAt', 'completedAt', 'error']
        df = missions[[c for c in cols if c in missions.columns]].copy()
        if 'startedAt' in df.columns:
            df = df.sort_values('startedAt', ascending=False)
        for col in ['startedAt', 'completedAt']:
            if col in df.columns:
                df[col] = df[col].astype(str)
        return df.fillna('').to_dict(orient='records')

    @staticmethod
    def build_battery_rows(telemetry, battery_dict):
        if telemetry.empty:
            return []
        cols = ['timestamp', 'deviceId', 'battery', 'status', 'speed', 'zoneId', 'lastNodeId']
        df = telemetry[[c for c in cols if c in telemetry.columns]].copy()
        df = df.sort_values('timestamp').tail(1)
        df['timestamp'] = df['timestamp'].astype(str)
        rows = df.fillna('').to_dict(orient='records')
        for row in rows:
            row['charge_cycles_today'] = battery_dict.get('charge_cycles_today', 0)
        return rows


class MissionDataExtractor:
    @staticmethod
    def get_mission(missions, mission_index):
        ms = missions.sort_values('startedAt', ascending=False) if 'startedAt' in missions.columns else missions
        return ms.iloc[mission_index]

    @staticmethod
    def filter_telemetry_by_mission(telemetry, mission_start, mission_end, device_id=None):
        if telemetry.empty or 'timestamp' not in telemetry.columns:
            return telemetry
        if pd.isna(mission_start):
            return pd.DataFrame()
        if pd.isna(mission_end):
            mission_end = pd.Timestamp.now()
        df  = _filter_device(telemetry, device_id) if device_id else telemetry.copy()
        ms  = _strip_tz(mission_start)
        me  = _strip_tz(mission_end)
        pad = pd.Timedelta(seconds=90)
        ts  = df['timestamp']
        if ts.dt.tz is not None:
            ts = ts.dt.tz_localize(None)
        return df[(ts >= ms - pad) & (ts <= me + pad)].copy()

    @staticmethod
    def build_mission_details(mission):
        return {
            'missionName':        str(mission.get('missionName', '')),
            'deviceId':           str(mission.get('deviceId', '')),
            'status':             str(mission.get('status', '')),
            'distanceTravelled':  float(mission.get('distanceTravelled', 0)) if pd.notna(mission.get('distanceTravelled')) else 0.0,
            'completedWaypoints': int(mission.get('completedWaypoints', 0)) if pd.notna(mission.get('completedWaypoints')) else 0,
            'totalWaypoints':     int(mission.get('totalWaypoints', 0)) if pd.notna(mission.get('totalWaypoints')) else 0,
            'startedAt':          str(mission.get('startedAt', '')),
            'completedAt':        str(mission.get('completedAt', '')),
            'error':              str(mission.get('error', '')) if pd.notna(mission.get('error')) else '',
        }


class MissionUtilizationCalculator:
    NON_ACTIVE = {'CANCELLED', 'FAILED'}

    @staticmethod
    def calculate(mission, telemetry):
        status = str(mission.get('status', '')).upper()
        start  = mission.get('startedAt')
        end    = mission.get('completedAt')
        if status in MissionUtilizationCalculator.NON_ACTIVE:
            if pd.notna(start) and pd.notna(end):
                s = _strip_tz(start); e = _strip_tz(end)
                dur = max(0, (e - s).total_seconds())
                fmt = MissionUtilizationCalculator._fmt(dur)
                is_anomaly = dur > 86400
            else:
                dur = 0; fmt = 'N/A'; is_anomaly = False
            return {
                'mission_duration_seconds':   round(dur, 2),
                'mission_duration_formatted': fmt,
                'utilization_percent':        round((dur/86400)*100, 4) if dur > 0 else 0.0,
                'utilization_note':           f'Mission was {status.lower()} — robot was physically active for {fmt}',
                'duration_anomaly':           is_anomaly,
            }
        if pd.isna(start):
            return {'mission_duration_seconds': 0, 'mission_duration_formatted': 'N/A',
                    'utilization_percent': 0.0, 'utilization_note': 'No start time recorded'}
        if pd.isna(end):
            end = pd.Timestamp.now()
        s = _strip_tz(start); e = _strip_tz(end)
        dur = max(0, (e - s).total_seconds())
        return {
            'mission_duration_seconds':   round(dur, 2),
            'mission_duration_formatted': MissionUtilizationCalculator._fmt(dur),
            'utilization_percent':        round((dur/86400)*100, 4),
            'utilization_note':           '',
            'duration_anomaly':           dur > 86400,
        }

    @staticmethod
    def _fmt(dur):
        h = int(dur//3600); m = int((dur%3600)//60); s = int(dur%60)
        if h > 0:   return f"{h}h {m}m {s}s"
        if m > 0:   return f"{m}m {s}s"
        return f"{s}s"


class MissionRowBuilder:
    @staticmethod
    def build_telemetry_rows(telemetry):
        """
        Returns all telemetry rows for the mission window including rich fields:
        position (x/y/theta), errors, operatingMode, speed, status, battery.
        Used by the frontend to render path visualizer, error panel,
        speed chart, and operating mode timeline.
        """
        if telemetry.empty:
            return []
        cols = [
            'timestamp', 'deviceId', 'status', 'battery', 'speed',
            'driving', 'zoneId', 'lastNodeId', 'operatingMode',
            'position', 'errors', 'actionStates', 'pathName',
        ]
        df = telemetry[[c for c in cols if c in telemetry.columns]].copy()
        if 'timestamp' in df.columns:
            df = df.sort_values('timestamp')
            df['timestamp'] = df['timestamp'].astype(str)

        # Parse position JSON into flat x, y, theta columns for easy frontend use
        if 'position' in df.columns:
            import json
            def _parse_pos(v):
                if not v or v == '':
                    return None, None, None
                try:
                    p = json.loads(v) if isinstance(v, str) else v
                    return p.get('x'), p.get('y'), p.get('theta')
                except Exception:
                    return None, None, None
            parsed = df['position'].apply(_parse_pos)
            df['pos_x']     = [p[0] for p in parsed]
            df['pos_y']     = [p[1] for p in parsed]
            df['pos_theta'] = [p[2] for p in parsed]
            df = df.drop(columns=['position'])

        # Parse errors JSON array into a flat string list
        if 'errors' in df.columns:
            import json
            def _parse_errors(v):
                if not v or v == '' or v == '[]':
                    return []
                try:
                    return json.loads(v) if isinstance(v, str) else v
                except Exception:
                    return []
            df['errors'] = df['errors'].apply(_parse_errors)

        return df.fillna('').to_dict(orient='records')

    @staticmethod
    def build_battery_rows(telemetry, battery_dict):
        if telemetry.empty:
            return []
        cols = ['timestamp', 'deviceId', 'battery', 'status', 'speed', 'zoneId', 'lastNodeId']
        df = telemetry[[c for c in cols if c in telemetry.columns]].copy()
        if 'timestamp' in df.columns:
            df = df.sort_values('timestamp')
        if len(df) > 10:
            df = pd.concat([df.head(5), df.tail(5)])
        if 'timestamp' in df.columns:
            df['timestamp'] = df['timestamp'].astype(str)
        rows = df.fillna('').to_dict(orient='records')
        for row in rows:
            row['battery_used'] = battery_dict.get('battery_used', 0)
            row['avg_battery']  = battery_dict.get('avg_battery', 0)
        return rows


# ---------------------------------------------------------------------------
# Main analytics service
# ---------------------------------------------------------------------------

class AnalyticsService:
    def __init__(self):
        self.summary_builder        = SummaryBuilder()
        self.row_builder            = RowBuilder()
        self.mission_extractor      = MissionDataExtractor()
        self.utilization_calculator = MissionUtilizationCalculator()
        self.mission_row_builder    = MissionRowBuilder()
        self.data_loader            = DataLoaderService()
        self._cache:            Dict[str, dict]              = {}
        self._raw_cache:        Dict[str, dict]              = {}
        self._attributor_cache: Dict[str, BatteryAttributor] = {}
        # deviceId → endpointId (human-readable robot name) map
        # Built lazily on first access, cached for session lifetime
        self._device_name_map: Dict[str, str]             = {}
        self._cache_lock = threading.Lock()
        self._in_progress: set = set()
        

    def _get_device_name(self, device_id: str) -> str:
        """
        Return human-readable robot name (endpointId) for a deviceId.
        Fetches from InfluxDB on first call, cached thereafter.
        Falls back to first 8 chars of UUID if endpointId not available.
        Example: "0f12e514-..." → "AMR12601071239XCR"
        """
        if device_id in self._device_name_map:
            return self._device_name_map[device_id]
        if not _DEVICE_ID_PATTERN.match(device_id):
            print(f"[NAME MAP] Rejected malformed deviceId: {device_id !r}")
            return device_id[:8] if device_id else 'unknown'
        try:
            flux = f'''
from(bucket: "device_core")
  |> range(start: 2025-01-01T00:00:00Z)
  |> filter(fn: (r) => r["_measurement"] == "device_telemetry")
  |> filter(fn: (r) => r["_field"] == "battery")
  |> filter(fn: (r) => r["deviceId"] == "{device_id}")
  |> first()
'''
            result  = self.data_loader.influx.query_api.query(
                flux, org=self.data_loader.influx.org
            )
            records = [r for t in result for r in t.records]
            name    = records[0].values.get('endpointId', '') if records else ''
            name    = name if name else device_id[:8]
        except Exception as e:
            print(f"[NAME MAP] Could not fetch endpointId for {device_id[:8]}: {e}")
            name = device_id[:8]
        self._device_name_map[device_id] = name
        print(f"[NAME MAP] {device_id[:8]} → {name}")
        return name

    def _sum_discharge_only(self, battery_series, timestamps=None):
        """Sum only genuine drops (rate-checked if timestamps provided)."""
        total = 0.0
        vals  = battery_series.dropna().tolist()
        times = timestamps.tolist() if timestamps is not None else [None] * len(vals)
        for i in range(1, len(vals)):
            diff = vals[i] - vals[i-1]
            if diff >= 0:
                continue
            drop = abs(diff)
            if times[i] is not None and times[i-1] is not None:
                dt_min = (times[i] - times[i-1]) / pd.Timedelta(minutes=1)
                if dt_min > 0 and (drop / dt_min) > MAX_BATTERY_DROP_PCT_PER_MINUTE:
                    continue
            total += drop
        return round(total, 2)

    def _calculate_hourly_battery_drain(self, telemetry, device_id, latest, cutoff, missions=None):
        """
        Left chart: cumulative battery drain (%) per active hour.
        Uses 1-minute resampled battery — noise-free and accurate.
        """
        clean_df = _prepare_battery_df(telemetry, device_id, cutoff, latest)
        if clean_df.empty:
            return []
        try:
            df = clean_df.copy()
            latest_ts = _strip_tz(latest) if latest else df['timestamp'].max()
            cutoff_ts = _strip_tz(cutoff) if cutoff else df['timestamp'].min()
            df['hour'] = df['timestamp'].dt.floor('h')

            active_hours: set = set()
            if missions is not None and not missions.empty:
                for _, m in missions.iterrows():
                    s = _strip_tz(m.get('startedAt'))
                    e = _strip_tz(m.get('completedAt'))
                    if s is None:
                        continue
                    e = e or s + pd.Timedelta(minutes=5)
                    h = s.floor('h')
                    while h <= e.floor('h'):
                        active_hours.add(h)
                        h += pd.Timedelta(hours=1)

            results = {}
            for hour, grp in df.groupby('hour'):
                grp = grp.sort_values('timestamp')
                results[hour] = self._sum_discharge_only(grp['battery_n'], grp['timestamp'])

            all_hours = pd.date_range(
                pd.Timestamp(cutoff_ts).floor('h'),
                pd.Timestamp(latest_ts).floor('h'),
                freq='h',
            )
            drain_list = [
                {
                    'timestamp': h.strftime('%d-%b %H:%M'),
                    'drain_pct': results.get(h, 0.0) if (not active_hours or h in active_hours) else 0.0,
                }
                for h in all_hours
            ]
            total = sum(d['drain_pct'] for d in drain_list)
            print(f"[DRAIN] Hourly drain computed: {len(drain_list)} hours, total = {total:.2f}%")
            return drain_list
        except Exception as e:
            print(f"[DRAIN] Calculation failed: {e}")
            import traceback; traceback.print_exc()
            return []

    def _calculate_hourly_battery_level(self, telemetry, device_id, latest, cutoff):
        """
        Right chart: average battery SOC (%) per hour.
        Uses 1-minute resampled battery for a clean level curve.
        """
        clean_df = _prepare_battery_df(telemetry, device_id, cutoff, latest)
        if clean_df.empty:
            return []
        try:
            df = clean_df.copy()
            latest_ts = _strip_tz(latest) if latest else df['timestamp'].max()
            cutoff_ts = _strip_tz(cutoff) if cutoff else df['timestamp'].min()
            df['hour'] = df['timestamp'].dt.floor('h')

            grouped_avg = df.groupby('hour')['battery_n'].mean().round(1)
            grouped_min = df.groupby('hour')['battery_n'].min().round(1)
            grouped_max = df.groupby('hour')['battery_n'].max().round(1)

            all_hours = pd.date_range(
                pd.Timestamp(cutoff_ts).floor('h'),
                pd.Timestamp(latest_ts).floor('h'),
                freq='h',
            )
            return [
                {
                    'timestamp': h.strftime('%d-%b %H:%M'),
                    'avg_level': float(grouped_avg.loc[h]) if h in grouped_avg.index else None,
                    'min_level': float(grouped_min.loc[h]) if h in grouped_min.index else None,
                    'max_level': float(grouped_max.loc[h]) if h in grouped_max.index else None,
                }
                for h in all_hours
            ]
        except Exception as e:
            print(f"[BATTERY LEVEL] Failed: {e}")
            return []

    def fetch_device_list(self):
        devices = self.data_loader.load_device_list()
        # Pre-fetch all device names so they are cached before UI loads
        for dev in devices:
            self._get_device_name(dev)

        def warm():
            for dev in devices:
                try:
                    name = self._get_device_name(dev)
                    print(f"🔥 Pre-warming {name}...")
                    self.fetch_dashboard_data(dev, force_refresh=True)
                except Exception as e:
                    name = self._get_device_name(dev)
                    print(f"⚠️  Pre-warm failed {name}: {e}")
        threading.Thread(target=warm, daemon=True).start()
        return devices

    def fetch_dashboard_data(self, device_id, force_refresh=False,
                             start_date=None, end_date=None):
        cache_key = f"{device_id}|{start_date or ''}|{end_date or ''}"

        while True:
            with self._cache_lock:
                if not force_refresh and cache_key in self._cache:
                    print(f"⚡ Cache HIT {device_id[:8]}...")
                    return self._cache[cache_key]
                if cache_key not in self._in_progress:
                    self._in_progress.add(cache_key)
                    break
            time.sleep(0.1)    
        

        print(f"🔄 Cache MISS — loading {device_id[:8]}...")
        try:
            raw        = self.data_loader.load_data_for_device(device_id, start_date, end_date)
            missions   = raw['missions']
            telemetry  = raw['telemetry']
            schedulers = pd.DataFrame()

            start_time = raw.get('start_time')
            end_time   = raw.get('end_time')
            latest     = _strip_tz(end_time)
            cutoff     = _strip_tz(start_time)

            NON_PHYSICAL = ['PENDING', 'PLANNING']
            if not missions.empty and 'status' in missions.columns:
                physical_missions = missions[
                    ~missions['status'].str.upper().isin(NON_PHYSICAL) &
                    missions['startedAt'].notna() &
                    missions['completedAt'].notna()
                ]
            else:
                physical_missions = missions

            dev_telemetry = _filter_device(telemetry, device_id)
            win_telemetry = _trim_to_window(dev_telemetry, start_time, end_time, pad_hours=0.5)

            attributor = BatteryAttributor(telemetry, missions, device_id, start_time, end_time)
            self._attributor_cache[device_id] = attributor

            utilization   = transform_utilization(win_telemetry, physical_missions)
            task_perf     = transform_task_performance(missions)
            hourly_util   = transform_hourly_utilization(physical_missions, latest, cutoff)
            hourly_drain  = self._calculate_hourly_battery_drain(telemetry, device_id, latest, cutoff, physical_missions)
            hourly_level  = self._calculate_hourly_battery_level(telemetry, device_id, latest, cutoff)
            task_timeline = transform_task_timeline(missions, latest, cutoff)

        # ── FIX Bug 1 + Bug 2 ────────────────────────────────────────────────
        # Previously: transform_battery(win_telemetry)
        #   - win_telemetry is empty when InfluxDB window doesn't match → N/A KPIs
        #   - raw noisy data → false charge cycle counts
        # Now: use the same 1-min resampled clean data used by drain/attributor
            clean_batt_df = _prepare_battery_df(telemetry, device_id, cutoff, latest)
            battery       = transform_battery_from_clean(clean_batt_df)
        # ─────────────────────────────────────────────────────────────────────

            summary = self.summary_builder.build(utilization, task_perf, battery, schedulers)

            device_name = self._get_device_name(device_id)
            name_map    = {device_id: device_name}

            def _swap_names(rows):
                for row in rows:
                    if 'deviceId' in row:
                        row['deviceId'] = name_map.get(str(row['deviceId']), str(row['deviceId']))
                return rows

            result = {
                'device_id':          device_id,
                'device_name':        device_name,
                'utilization':        utilization,
                'task_performance':   task_perf,
                'battery':            battery,
                'hourly_utilization': hourly_util,
                'hourly_drain':       hourly_drain,
                'hourly_level':       hourly_level,
                'hourly_energy':      hourly_drain,
                'task_timeline':      task_timeline,
                'summary':            summary,
                'telemetry_rows':     _swap_names(self.row_builder.build_telemetry_rows(win_telemetry)),
                'mission_rows':       _swap_names(self.row_builder.build_mission_rows(missions)),
                'battery_rows':       _swap_names(self.row_builder.build_battery_rows(win_telemetry, battery)),
            }
            with self._cache_lock:
                self._cache[cache_key] = result
                self._raw_cache[cache_key] = raw
                self._last_key = getattr(self, '_last_key', {})
                self._last_key[device_id] = cache_key
            return result
        finally:
            with self._cache_lock:
                self._in_progress.discard(cache_key)

    def fetch_mission_data(self, device_id, mission_index):
        self._last_key = getattr(self, '_last_key', {})
        cache_key = self._last_key.get(device_id, device_id)

        
        with self._cache_lock:
            if cache_key in self._raw_cache:
                print(f"⚡ Mission cache HIT {device_id[:8]}...")
                raw = self._raw_cache[cache_key]
            else:
                raw = None


        if raw is None:
            print(f"🔄 Mission cache MISS — loading {device_id[:8]}...")
            raw = self.data_loader.load_data_for_device(device_id)
            with self._cache_lock:
                self._raw_cache[cache_key] = raw
        

        missions  = raw['missions']
        telemetry = raw['telemetry']

        if missions.empty or mission_index >= len(missions):
            return {'error': 'Mission not found'}

        if device_id not in self._attributor_cache:
            self._attributor_cache[device_id] = BatteryAttributor(
                telemetry, missions, device_id,
                raw.get('start_time'), raw.get('end_time'),
            )

        mission       = self.mission_extractor.get_mission(missions, mission_index)
        mission_start = mission.get('startedAt')
        mission_end   = mission.get('completedAt')

        mission_telemetry = self.mission_extractor.filter_telemetry_by_mission(
            telemetry, mission_start, mission_end, device_id)

        print(f"[MISSION] idx={mission_index}  {mission_start} → {mission_end}")
        print(f"[MISSION] telemetry in window: {len(mission_telemetry)}")

        mission_battery     = self._attributor_cache[device_id].get(mission_index)
        mission_utilization = self.utilization_calculator.calculate(mission, mission_telemetry)
        mission_details     = self.mission_extractor.build_mission_details(mission)

        print(f"[MISSION] battery_used={mission_battery['battery_used']}%  "
              f"start={mission_battery['start_battery']}%  "
              f"end={mission_battery['end_battery']}%  "
              f"method={mission_battery['measurement_method']}")

        device_name  = self._get_device_name(device_id)
        name_map     = {device_id: device_name}

        def _swap_names(rows):
            for row in rows:
                if 'deviceId' in row:
                    row['deviceId'] = name_map.get(str(row['deviceId']), str(row['deviceId']))
            return rows

        # Also replace deviceId in mission_details
        mission_details['deviceId'] = device_name

        return {
            'mission':        mission_details,
            'battery':        mission_battery,
            'utilization':    mission_utilization,
            'telemetry_rows': _swap_names(self.mission_row_builder.build_telemetry_rows(mission_telemetry)),
            'battery_rows':   _swap_names(self.mission_row_builder.build_battery_rows(mission_telemetry, mission_battery)),
            'device_id':      device_id,
            'device_name':    device_name,
        }


# ── Public API ────────────────────────────────────────────────────────────────
_service = AnalyticsService()

def fetch_device_list(): return _service.fetch_device_list()
def fetch_dashboard_data(device_id, force_refresh=False, start_date=None, end_date=None):
    return _service.fetch_dashboard_data(device_id, force_refresh, start_date, end_date)
def fetch_mission_data(device_id, mission_index): return _service.fetch_mission_data(device_id, mission_index)
def get_device_name(device_id): return _service._get_device_name(device_id)


# ── FIX Bug 3: clear_cache key mismatch ──────────────────────────────────────
# Previously used _cache.pop(device_id) but keys are "device_id|start|end"
# so per-device clears never worked — stale empty data served indefinitely.
def clear_cache(device_id=None):
    if device_id:
        keys_to_delete = [k for k in _service._cache if k.startswith(device_id)]
        for k in keys_to_delete:
            _service._cache.pop(k, None)
            _service._raw_cache.pop(k, None)
        _service._attributor_cache.pop(device_id, None)
        return {"cleared": device_id}
    else:
        _service._cache.clear()
        _service._raw_cache.clear()
        _service._attributor_cache.clear()
        return {"cleared": "all"}