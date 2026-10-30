# backend/api/routes.py
from flask import Blueprint, jsonify, request
from backend.services.analytics_service import (
    fetch_device_list, fetch_dashboard_data,
    fetch_mission_data, clear_cache, get_device_name,
)
from backend.services.health_service import check_readiness
import traceback

api_bp = Blueprint('api', __name__, url_prefix='/api')


@api_bp.route('/devices', methods=['GET'])
def get_devices():
    """
    Returns a list of device objects with both id and name.
    name = endpointId (human-readable robot name e.g. AMR12601071239XCR)
    id   = UUID (used internally for all API calls)

    Response:
    {
      "devices": [
        {"id": "0f12e514-...", "name": "AMR12601071239XCR"},
        ...
      ]
    }
    """
    try:
        device_ids = fetch_device_list()
        devices    = [
            {"id": did, "name": get_device_name(did)}
            for did in device_ids
        ]
        return jsonify({'devices': devices})
    except Exception as e:
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@api_bp.route('/data/<string:device_id>', methods=['GET'])
def get_device_data(device_id: str):
    try:
        force      = request.args.get('refresh', 'false').lower() == 'true'
        start_date = request.args.get('start_date')
        end_date   = request.args.get('end_date')
        data = fetch_dashboard_data(device_id, force_refresh=force,
                                    start_date=start_date, end_date=end_date)
        return jsonify(data)
    except Exception as e:
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@api_bp.route('/mission/<string:device_id>/<int:mission_index>', methods=['GET'])
def get_mission_data(device_id: str, mission_index: int):
    try:
        data = fetch_mission_data(device_id, mission_index)
        return jsonify(data)
    except Exception as e:
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@api_bp.route('/cache/clear', methods=['POST'])
def cache_clear():
    """POST /api/cache/clear  or  POST /api/cache/clear?device=<id>"""
    try:
        device_id = request.args.get('device')
        result    = clear_cache(device_id)
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@api_bp.route('/health', methods=['GET'])
def health():
    return jsonify({'status': 'alive'})

@api_bp.route('/ready', methods=['GET'])
def ready():
    """
    Readiness: live-probes every dependency and returns 503 if any is down.

    There is deliberately no try/except here. check_readiness() never raises
    and already returns sanitised, closed-vocabulary reasons; catching at this
    layer would reintroduce the str(e) leak this endpoint exists to avoid.
    """
    body, status = check_readiness()
    return jsonify(body), status
