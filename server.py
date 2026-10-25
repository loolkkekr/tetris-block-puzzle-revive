from flask import Flask, jsonify, request
from datetime import datetime, timezone
import json
import os
import base64
import copy
import gzip

app = Flask(__name__)

USERS_FILE = 'users.json'
GAME_DATA_FILE = 'game_data.json'

BOOSTER_IDS = ('Rotate', 'SwapShape', 'Hint')
BOOSTER_AMOUNT = 99


# ---------- helpers ----------
def now_utc():
    return datetime.now(timezone.utc)


def iso_now_ms():
    return now_utc().strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z'


def load_json(path, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return default


def save_json(path, data):
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_users():
    return load_json(USERS_FILE, {})


def save_users(users):
    save_json(USERS_FILE, users)


def load_game_data():
    return load_json(GAME_DATA_FILE, {})


def save_game_data(data):
    save_json(GAME_DATA_FILE, data)


def force_boosters(data: dict) -> dict:
    data = copy.deepcopy(data)
    owned = data.get('ownedItems')
    if not isinstance(owned, dict):
        owned = {}
        data['ownedItems'] = owned
    currencies = owned.get('currencies')
    if not isinstance(currencies, dict):
        currencies = {}
        owned['currencies'] = currencies

    for booster_id in BOOSTER_IDS:
        entry = currencies.get(booster_id)
        if not isinstance(entry, dict):
            entry = {}
            currencies[booster_id] = entry
        entry['CurrencyId'] = booster_id
        entry.setdefault('Tag', None)
        entry['CurrencyAmounts'] = [{"Amount": BOOSTER_AMOUNT}]
        entry.setdefault('CurrencyExpiringAmounts', [])
        entry.setdefault('CurrencyAmountExpiringInfinite', None)

    return data


def _b64url_decode(segment: str) -> bytes:
    padding = '=' * (-len(segment) % 4)
    return base64.urlsafe_b64decode(segment + padding)


def decode_jwt_payload(token: str) -> dict | None:
    """Достаёт payload JWT без проверки подписи.
    В проде подпись надо проверять по публичному ключу auth-service."""
    try:
        parts = token.split('.')
        if len(parts) != 3:
            return None
        return json.loads(_b64url_decode(parts[1]))
    except (ValueError, json.JSONDecodeError):
        return None


def get_bearer_token() -> str | None:
    auth = request.headers.get('Authorization', '')
    if not auth:
        return None
    parts = auth.split(None, 1)
    if len(parts) == 2 and parts[0].lower() == 'bearer':
        return parts[1].strip()
    return None


def resolve_device_id_from_token():
    """Возвращает (device_id, error_response).
    Берёт deviceId из payload JWT в Authorization: Bearer ..."""
    token = get_bearer_token()
    if not token:
        return None, (jsonify({"error": "Authorization: Bearer <token> required"}), 401)

    payload = decode_jwt_payload(token)
    if not payload:
        return None, (jsonify({"error": "invalid JWT"}), 401)

    device_id = payload.get('deviceId')
    if not device_id:
        return None, (jsonify({"error": "deviceId not found in token"}), 401)

    return device_id, None


def read_request_body():
    """Читает тело запроса, при необходимости распаковывает gzip/deflate,
    и парсит как JSON. Возвращает (obj, error_response)."""
    raw = request.get_data(cache=False) or b''
    if not raw:
        return {}, None

    encoding = (request.headers.get('Content-Encoding') or '').lower().strip()
    try:
        if 'gzip' in encoding:
            raw = gzip.decompress(raw)
        # deflate/zlib иногда тоже шлют — но по заголовку выше gzip,
        # для простоты deflate можно добавить при необходимости
    except OSError:
        return None, (jsonify({"error": "failed to decompress request body"}), 400)

    try:
        obj = json.loads(raw.decode('utf-8'))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None, (jsonify({"error": "invalid JSON body"}), 400)

    return obj, None


# ---------- endpoints ----------
@app.route('/api/v1/time', methods=['GET'])
def get_time():
    return jsonify({"payload": {"currentTime": iso_now_ms()}})


@app.route('/api/v1/popup/configuration', methods=['GET'])
def get_popup_configuration():
    return jsonify({
        "isEnabled": False,
        "version": "1.0.1",
        "offerTriggers": {}
    })


@app.route('/api/v1/offers', methods=['GET'])
def get_offers():
    return jsonify({
        "offers": [],
        "isEnabled": False,
        "version": "1.0.4",
        "unlockLevel": 0
    })


@app.route('/api/v1/login/auth', methods=['POST'])
def login_auth():
    body, err = read_request_body()
    if err:
        return err

    device_id = body.get('deviceId')
    game_id = body.get('gameId', 'T4')
    device_info = body.get('deviceInfo') or {}

    if not device_id:
        return jsonify({"error": "deviceId is required"}), 400

    users = load_users()
    is_new_user = device_id not in users

    if is_new_user:
        users[device_id] = {
            "userId": __import__('uuid').uuid4().hex[:24],
            "deviceId": device_id,
            "gameId": game_id,
            "installTime": int(now_utc().timestamp() * 1000),
            "balance": 100,
            "createdAt": iso_now_ms(),
        }

    user = users[device_id]
    if device_info.get('installTime') is not None:
        user['installTime'] = device_info['installTime']
    save_users(users)

    # --- здесь могла бы быть выдача реального JWT от auth-service ---
    # для совместимости оставляем простой hex-токен
    import uuid as _uuid
    session_token = _uuid.uuid4().hex
    issued_at = int(now_utc().timestamp())
    expires_at = issued_at + 86400

    return jsonify({
        "sessionToken": session_token,
        "sessionExpiration": expires_at * 1000,
        "userId": user['userId'],
        "installTime": user['installTime'],
        "lp": {"currentBalance": user['balance']},
        "isNewUser": is_new_user,
        "isDailyLimitReached": False,
        "time": {"currentTime": iso_now_ms()},
        "socialInfo": {"isSocialAcc": False, "connectedTo": None, "email": None},
        "myVipStatus": {"tier": 0},
    })


@app.route('/api/v1/game/data', methods=['POST'])
def post_game_data():
    device_id, err = resolve_device_id_from_token()
    if err:
        return err

    body, err = read_request_body()
    if err:
        return err

    if not isinstance(body, dict):
        return jsonify({"error": "body must be a JSON object"}), 400

    # применяем бустеры = 99
    game_state = force_boosters(body)

    all_data = load_game_data()
    all_data[device_id] = {
        "data": game_state,
        "updatedAt": iso_now_ms(),
    }
    save_game_data(all_data)

    # клиент ожидает подтверждения приёма
    return jsonify({"ok": True})


@app.route('/api/v1/game/data', methods=['GET'])
def get_game_data():
    device_id, err = resolve_device_id_from_token()
    if err:
        return err

    all_data = load_game_data()
    entry = all_data.get(device_id)
    if not entry:
        return jsonify({})

    data = force_boosters(entry['data'])
    return jsonify(data)


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
