# backend/transformers/trizlabz_transformer.py
"""
Data Transformation Service
Transforms raw CSV data into analytics-ready metrics
"""

import pandas as pd
from typing import Dict, List


class MathUtils:
    @staticmethod
    def safe_round(val, n=2) -> float:
        try:
            return round(float(val), n)
        except Exception:
            return 0.0


# Statuses where robot was physically active (moved, consumed battery)
# Includes cancelled/failed because robot DID run before being stopped
PHYSICAL_STATUSES = ['COMPLETED', 'RUNNING', 'EXECUTING', 'CANCELLED', 'FAILED']


class UtilizationTransformer:
    def __init__(self, window_hours: int = 24):
        self.window_hours = window_hours
        self.window_seconds = window_hours * 3600

    def transform(self, telemetry: pd.DataFrame, missions: pd.DataFrame) -> Dict:
        empty = {'working_hours': 0, 'idle_hours': self.window_hours, 'utilization_rate': 0}

        if missions.empty:
            return empty

        # Include all physically active missions (completed + cancelled that actually ran)
        has_times = missions['startedAt'].notna() & missions['completedAt'].notna()
        working_mask = missions['status'].str.upper().isin(PHYSICAL_STATUSES)
        working = missions[working_mask & has_times].copy()

        if working.empty:
            return empty

        # Calculate duration, exclude missions with anomalous duration (>24h = bad data)
        working = working.copy()
        working['_dur'] = (
            working['completedAt'] - working['startedAt']
        ).dt.total_seconds().clip(lower=0)

        # Filter out data anomalies (>24h duration for a single mission is impossible)
        valid_mask = working['_dur'] <= 86400
        if not valid_mask.all():
            n_anomalies = (~valid_mask).sum()
            print(f"[UTILIZATION] Excluding {n_anomalies} mission(s) with anomalous duration >24h")
        working = working[valid_mask]

        working_seconds = working['_dur'].sum()
        working_hours = MathUtils.safe_round(working_seconds / 3600)
        idle_hours = MathUtils.safe_round(max(0, self.window_hours - working_hours))
        util_rate = MathUtils.safe_round((working_seconds / self.window_seconds) * 100)

        return {
            'working_hours': working_hours,
            'idle_hours': idle_hours,
            'utilization_rate': util_rate,
        }


class TaskPerformanceTransformer:
    def transform(self, missions: pd.DataFrame) -> Dict:
        empty = {
            'total_tasks': 0,
            'completed_tasks': 0,
            'failed_tasks': 0,
            'success_rate': 0,
            'avg_completion_time': 0
        }

        if missions.empty:
            return empty

        total = len(missions)
        completed = int((missions['status'].str.upper() == 'COMPLETED').sum())
        failed = int((missions['status'].str.upper() == 'FAILED').sum())

        has_times = missions['startedAt'].notna() & missions['completedAt'].notna()
        if has_times.any():
            duration = (
                missions.loc[has_times, 'completedAt'] -
                missions.loc[has_times, 'startedAt']
            ).dt.total_seconds() / 60
            avg_time = MathUtils.safe_round(duration.clip(lower=0).mean())
        else:
            avg_time = 0

        return {
            'total_tasks': total,
            'completed_tasks': completed,
            'failed_tasks': failed,
            'success_rate': MathUtils.safe_round((completed / total) * 100) if total > 0 else 0,
            'avg_completion_time': avg_time,
        }


class ChargeCycleDetector:
    def __init__(self, merge_window_hours: float = 2.0, charge_threshold: float = 5.0):
        self.merge_window_hours = merge_window_hours
        self.charge_threshold = charge_threshold

    def detect_cycles(self, battery_series: pd.Series, timestamps: pd.Series = None) -> int:
        charge_sessions = self._detect_charge_sessions(battery_series)
        if len(charge_sessions) == 0:
            return 0
        if timestamps is not None:
            try:
                merged_sessions = self._merge_sessions_by_time(charge_sessions, timestamps)
                return len(merged_sessions)
            except Exception:
                pass
        return len(charge_sessions)

    def _detect_charge_sessions(self, battery_series: pd.Series) -> List[Dict]:
        sessions = []
        in_charge = False
        session_start_idx = None
        b_list = battery_series.reset_index(drop=True)
        diffs = b_list.diff()
        for i in range(len(b_list)):
            diff_value = diffs.iloc[i] if i < len(diffs) else None
            if pd.notna(diff_value) and diff_value > self.charge_threshold:
                if not in_charge:
                    in_charge = True
                    session_start_idx = i
            else:
                if in_charge and session_start_idx is not None:
                    sessions.append({'start_idx': session_start_idx, 'end_idx': i - 1})
                    in_charge = False
        if in_charge and session_start_idx is not None:
            sessions.append({'start_idx': session_start_idx, 'end_idx': len(b_list) - 1})
        return sessions

    def _merge_sessions_by_time(self, sessions: List[Dict], timestamps: pd.Series) -> List[Dict]:
        timestamps = pd.to_datetime(timestamps).reset_index(drop=True)
        merged = []
        current_session = sessions[0]
        for i in range(1, len(sessions)):
            next_session = sessions[i]
            current_end_time = timestamps.iloc[current_session['end_idx']]
            next_start_time = timestamps.iloc[next_session['start_idx']]
            time_gap_hours = (next_start_time - current_end_time).total_seconds() / 3600
            if time_gap_hours <= self.merge_window_hours:
                current_session['end_idx'] = next_session['end_idx']
            else:
                merged.append(current_session)
                current_session = next_session
        merged.append(current_session)
        return merged


class BatteryTransformer:
    def __init__(self):
        self.cycle_detector = ChargeCycleDetector()

    def transform(self, telemetry: pd.DataFrame) -> Dict:
        """
        Transforms telemetry into battery-level KPIs.

        Notes:
          - battery_health: approximated as average SOC (%) over the window.
            This is NOT true degradation health (which requires cycle-count vs
            rated lifespan data). Treat as "average charge level" until a
            proper health model is implemented.
          - battery_drain_pct: cumulative % drain observed in the telemetry window.
            This is NOT kWh — converting to energy units requires the battery's
            rated capacity (Wh), which is not available in telemetry.
          - estimated_runtime: linear estimate based on current charge only.
        """
        empty = {
            'current_charge': 0.0,
            'battery_health': 0.0,
            'battery_drain_pct': 0.0,
            'estimated_runtime': 0.0,
            'charge_cycles_today': 0,
        }
        if telemetry.empty:
            return empty

        telemetry_sorted = telemetry.sort_values('timestamp')
        battery = pd.to_numeric(telemetry_sorted['battery'], errors='coerce').dropna()

        if battery.empty:
            return empty

        current = battery.iloc[-1]
        avg_battery = battery.mean()
        # battery_health approximated as avg SOC — see docstring for limitations
        health = min(100.0, avg_battery)
        diffs = battery.diff()
        drops = diffs[diffs < 0].abs().sum()
        # drain expressed as % points, NOT kWh
        battery_drain_pct = MathUtils.safe_round(drops)
        runtime_multiplier = current / 100.0
        estimated_runtime = MathUtils.safe_round(runtime_multiplier * 8)
        timestamps = telemetry_sorted['timestamp'] if 'timestamp' in telemetry_sorted.columns else None
        charge_cycles = self.cycle_detector.detect_cycles(battery, timestamps)

        return {
            'current_charge':     MathUtils.safe_round(current),
            'battery_health':     MathUtils.safe_round(health),
            'battery_drain_pct':  battery_drain_pct,
            'estimated_runtime':  estimated_runtime,
            'charge_cycles_today': charge_cycles,
        }


class HourlyUtilizationTransformer:
    def transform(self, missions: pd.DataFrame, latest=None, cutoff=None) -> List[Dict]:
        if missions.empty:
            return []

        # Include all physically active missions
        has_times = missions['startedAt'].notna() & missions['completedAt'].notna()
        working_mask = missions['status'].str.upper().isin(PHYSICAL_STATUSES)
        working = missions[working_mask & has_times].copy()

        # Determine time range from ALL missions (not just working)
        if latest is None:
            latest = missions['startedAt'].max()
        if cutoff is None:
            cutoff = latest - pd.Timedelta(hours=24)

        min_hour = pd.Timestamp(cutoff).floor('h')
        max_hour = pd.Timestamp(latest).floor('h')
        hours = pd.date_range(min_hour, max_hour, freq='h')

        # Exclude anomalous missions (>24h duration)
        working['_dur'] = (working['completedAt'] - working['startedAt']).dt.total_seconds()
        working = working[working['_dur'] <= 86400]

        if working.empty:
            return [{'timestamp': h.strftime('%d-%b %H:%M'), 'utilization': 0} for h in hours]

        results = []
        for hour_start in hours:
            hour_end = hour_start + pd.Timedelta(hours=1)
            overlap_secs = 0.0
            for _, row in working.iterrows():
                mission_end = row['completedAt']
                overlap = (
                    min(mission_end, hour_end) -
                    max(row['startedAt'], hour_start)
                ).total_seconds()
                if overlap > 0:
                    overlap_secs += overlap
            utilization = MathUtils.safe_round(min(100.0, (overlap_secs / 3600) * 100))
            results.append({
                'timestamp': hour_start.strftime('%d-%b %H:%M'),
                'utilization': utilization,
            })
        return results


class HourlyEnergyTransformer:
    def transform(self, telemetry: pd.DataFrame, latest=None, cutoff=None) -> List[Dict]:
        if telemetry.empty:
            return []
        df = telemetry.copy()
        df['battery_n'] = pd.to_numeric(df['battery'], errors='coerce')
        df = df.sort_values('timestamp')
        if latest is None:
            latest = df['timestamp'].max()
        if cutoff is None:
            cutoff = latest - pd.Timedelta(hours=24)
        df = df[df['timestamp'] >= pd.Timestamp(cutoff)]
        df['hour'] = df['timestamp'].dt.floor('h')

        def hour_energy(grp):
            battery = grp['battery_n'].dropna()
            if len(battery) < 2:
                return 0.0
            drops = battery.diff()[battery.diff() < 0].abs().sum()
            return MathUtils.safe_round(drops)

        grouped = df.groupby('hour').apply(hour_energy).reset_index()
        grouped.columns = ['hour', 'energy_kwh']
        grouped = grouped.set_index('hour')
        all_hours = pd.date_range(
            pd.Timestamp(cutoff).floor('h'),
            pd.Timestamp(latest).floor('h'),
            freq='h'
        )
        return [
            {
                'timestamp': h.strftime('%d-%b %H:%M'),
                'energy_kwh': float(grouped.loc[h, 'energy_kwh']) if h in grouped.index else 0.0
            }
            for h in all_hours
        ]


class TaskTimelineTransformer:
    def transform(self, missions: pd.DataFrame, latest=None, cutoff=None) -> List[Dict]:
        if missions.empty:
            return []
        df = missions.copy()
        if latest is None:
            latest = df['startedAt'].max()
        if cutoff is None:
            cutoff = latest - pd.Timedelta(hours=24)
        min_hour = pd.Timestamp(cutoff).floor('h')
        max_hour = pd.Timestamp(latest).floor('h')
        all_hours = pd.date_range(min_hour, max_hour, freq='h')
        df['hour'] = df['startedAt'].dt.floor('h')
        completed_h = df[df['status'].str.upper() == 'COMPLETED'].groupby('hour').size()
        failed_h = df[df['status'].str.upper() == 'FAILED'].groupby('hour').size()
        return [
            {
                'timestamp': h.strftime('%d-%b %H:%M'),
                'completed': int(completed_h.get(h, 0)),
                'failed': int(failed_h.get(h, 0)),
            }
            for h in all_hours
        ]


class TrizlabzTransformerService:
    def __init__(self):
        self.utilization = UtilizationTransformer()
        self.task_performance = TaskPerformanceTransformer()
        self.battery = BatteryTransformer()
        self.hourly_utilization = HourlyUtilizationTransformer()
        self.hourly_energy = HourlyEnergyTransformer()
        self.task_timeline = TaskTimelineTransformer()

    def transform_utilization(self, telemetry, missions): return self.utilization.transform(telemetry, missions)
    def transform_task_performance(self, missions): return self.task_performance.transform(missions)
    def transform_battery(self, telemetry): return self.battery.transform(telemetry)
    def transform_hourly_utilization(self, missions, latest=None, cutoff=None): return self.hourly_utilization.transform(missions, latest, cutoff)
    def transform_hourly_energy(self, telemetry, latest=None, cutoff=None): return self.hourly_energy.transform(telemetry, latest, cutoff)
    def transform_task_timeline(self, missions, latest=None, cutoff=None): return self.task_timeline.transform(missions, latest, cutoff)


_service = TrizlabzTransformerService()

def transform_utilization(telemetry, missions): return _service.transform_utilization(telemetry, missions)
def transform_task_performance(missions): return _service.transform_task_performance(missions)
def transform_battery(telemetry): return _service.transform_battery(telemetry)
def transform_hourly_utilization(missions, latest=None, cutoff=None): return _service.transform_hourly_utilization(missions, latest, cutoff)
def transform_hourly_energy(telemetry, latest=None, cutoff=None): return _service.transform_hourly_energy(telemetry, latest, cutoff)
def transform_task_timeline(missions, latest=None, cutoff=None): return _service.transform_task_timeline(missions, latest, cutoff)