from flask import Flask, jsonify, request
from datetime import datetime, timezone
import json
import os
import uuid
import hashlib
import hmac
import base64
import copy

app = Flask(__name__)

USERS_FILE = 'users.json'
GAME_DATA_FILE = 'game_data.json'
SESSIONS_FILE = 'sessions.json'

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


def load_users():   return load_json(USERS_FILE, {})
def save_users(u):  save_json(USERS_FILE, u)

def load_game_data(): return load_json(GAME_DATA_FILE, {})
def save_game_data(d): save_json(GAME_DATA_FILE, d)

def load_sessions(): return load_json(SESSIONS_FILE, {})
def save_sessions(s): save_json(SESSIONS_FILE, s)


def generate_support_code():
    return ''.join(uuid.uuid4().hex.upper()[:6])


def generate_user_id():
    return uuid.uuid4().hex[:24]


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b'=').decode('ascii')


def make_jwt(payload: dict, secret: str) -> str:
    header = {"alg": "HS256", "typ": "JWT"}
    header_b64 = b64url(json.dumps(header, separators=(',', ':')).encode())
    payload_b64 = b64url(json.dumps(payload, separators=(',', ':')).encode())
    signing_input = f"{header_b64}.{payload_b64}".encode()
    signature = hmac.new(secret.encode(), signing_input, hashlib.sha256).digest()
    return f"{header_b64}.{payload_b64}.{b64url(signature)}"


JWT_SECRET = 'change-me-in-production'


def force_boosters(data: dict) -> dict:
    """Ставит Rotate/SwapShape/Hint = 99, остальное не трогает."""
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


def get_bearer_token() -> str | None:
    auth = request.headers.get('Authorization', '')
    if not auth:
        return None
    parts = auth.split(None, 1)
    if len(parts) == 2 and parts[0].lower() == 'bearer':
        return parts[1].strip()
    return None


def resolve_device_id_from_token():
    token = get_bearer_token()
    if not token:
        return None, (jsonify({"error": "Authorization: Bearer <token> required"}), 401)

    sessions = load_sessions()
    session = sessions.get(token)
    if not session:
        return None, (jsonify({"error": "invalid or expired session token"}), 401)

    expires_at_ms = session.get('expiresAt', 0)
    now_ms = int(now_utc().timestamp() * 1000)
    if expires_at_ms and now_ms > expires_at_ms:
        sessions.pop(token, None)
        save_sessions(sessions)
        return None, (jsonify({"error": "session token expired"}), 401)

    return session['deviceId'], None


# ---------- endpoints ----------
@app.route('/api/v1/time', methods=['GET'])
def get_time():
    return jsonify({"payload": {"currentTime": iso_now_ms()}})


@app.route('/api/v1/popup/configuration', methods=['GET'])
def get_popup_configuration():
    return jsonify({"isEnabled": False, "version": "1.0.1", "offerTriggers": {}})


@app.route('/api/v1/offers', methods=['GET'])
def get_offers():
    return jsonify({"offers": [], "isEnabled": False, "version": "1.0.4", "unlockLevel": 0})


@app.route('/api/v1/login/auth', methods=['POST'])
def login_auth():
    data = request.get_json(silent=True) or {}
    device_id = data.get('deviceId')
    game_id = data.get('gameId', 'T4')
    device_info = data.get('deviceInfo') or {}

    if not device_id:
        return jsonify({"error": "deviceId is required"}), 400

    users = load_users()
    is_new_user = device_id not in users

    if is_new_user:
        users[device_id] = {
            "userId": generate_user_id(),
            "deviceId": device_id,
            "gameId": game_id,
            "supportCode": generate_support_code(),
            "installTime": int(now_utc().timestamp() * 1000),
            "balance": 100,
            "createdAt": iso_now_ms(),
        }

    user = users[device_id]
    client_install_time = device_info.get('installTime')
    if client_install_time is not None:
        user['installTime'] = client_install_time
    save_users(users)

    user_id = user['userId']
    support_code = user['supportCode']
    install_time = user['installTime']

    issued_at = int(now_utc().timestamp())
    expires_at = issued_at + 86400
    session_token = uuid.uuid4().hex

    sessions = load_sessions()
    sessions[session_token] = {
        "deviceId": device_id,
        "userId": user_id,
        "issuedAt": issued_at * 1000,
        "expiresAt": expires_at * 1000,
    }
    save_sessions(sessions)

    play_link_payload = {
        "iat": issued_at, "nbf": issued_at, "exp": expires_at,
        "partnerApplicationId": f"{game_id}Tetris_Prod_34c9cba7-f7c6-4166-b96e-d810e63a36cf",
        "playerId": user_id, "playstudiosUserId": user_id,
        "supportCode": support_code, "identityProvider": "Anonymous",
    }
    play_link_token = make_jwt(play_link_payload, JWT_SECRET)

    return jsonify({
        "sessionToken": session_token,
        "sessionExpiration": expires_at * 1000,
        "userId": user_id,
        "installTime": install_time,
        "playLinkToken": play_link_token,
        "lp": {"currentBalance": user['balance']},
        "supportCode": support_code,
        "isNewUser": is_new_user,
        "isDailyLimitReached": False,
        "time": {"currentTime": iso_now_ms()},
        "socialInfo": {"isSocialAcc": False, "connectedTo": None, "email": None},
        "myVipStatus": {"tier": 0},
        "avaManifest": {
            "cs": {
                "base-game-configuration": {"p": "dd16f1e5/0/", "s": [], "a": [], "c": True},
                "gameplay-ftue": {"p": "16c2751c/0/", "s": [], "a": [], "c": True},
                "base-game-configuration-with-version": {"p": "f1fae5fd/0/", "s": [], "a": [], "c": True},
                "classic-smart-config": {"p": "ee952b4e/0/", "s": [], "a": [], "c": True},
                "level-chest": {"p": "4b058c61/0/", "s": [], "a": [], "c": True},
            },
            "ms": ["control", "Non_Paying_Users", "Android_Valentine"],
            "h": "e2352e9379dedbe9",
            "t": now_utc().strftime('%m/%d/%Y %H:%M:%S'),
        },
        "loyaltyServiceToken": None,
    })


@app.route('/api/v1/game/data', methods=['POST'])
def post_game_data():
    device_id, err = resolve_device_id_from_token()
    if err:
        return err

    # Принимаем ровно то, что прислал клиент — целиком, без обёрток и вырезаний.
    payload = request.get_json(silent=True)
    if payload is None:
        return jsonify({"error": "invalid JSON body"}), 400
    if not isinstance(payload, dict):
        return jsonify({"error": "JSON body must be an object"}), 400

    # Единственная модификация — гарантированные бустеры по 99.
    payload = force_boosters(payload)

    all_data = load_game_data()
    all_data[device_id] = payload            # храним сам объект, без обёртки
    save_game_data(all_data)

    return jsonify({"ok": True})


@app.route('/api/v1/game/data', methods=['GET'])
def get_game_data():
    device_id, err = resolve_device_id_from_token()
    if err:
        return err

    all_data = load_game_data()
    payload = all_data.get(device_id)

    if payload is None:
        # Первый заход без сохранения — отдаём пустой объект, клиент сам подгрузит дефолты.
        return jsonify({})

    # На всякий случай снова гарантируем 99 (если файл кто-то правил руками).
    payload = force_boosters(payload)
    return jsonify(payload)


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
