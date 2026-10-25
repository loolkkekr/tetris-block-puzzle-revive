from flask import Flask, jsonify, request
from datetime import datetime, timezone
import json
import os
import uuid
import hashlib
import hmac
import base64

app = Flask(__name__)

USERS_FILE = 'users.json'


# ---------- helpers ----------
def now_utc():
    return datetime.now(timezone.utc)


def iso_now_ms():
    return now_utc().strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z'


def load_users():
    if not os.path.exists(USERS_FILE):
        return {}
    try:
        with open(USERS_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def save_users(users):
    with open(USERS_FILE, 'w', encoding='utf-8') as f:
        json.dump(users, f, ensure_ascii=False, indent=2)



def generate_user_id():
    # 24-символьный hex, как ObjectId
    return uuid.uuid4().hex[:24]


# ---------- JWT (HS256) ----------
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
    data = request.get_json(silent=True) or {}

    device_id = data.get('deviceId')
    game_id = data.get('gameId', 'T4')
    device_info = data.get('deviceInfo') or {}

    if not device_id:
        return jsonify({"error": "deviceId is required"}), 400

    # ---- загрузка / создание пользователя ----
    users = load_users()
    is_new_user = device_id not in users

    if is_new_user:
        users[device_id] = {
            "userId": generate_user_id(),
            "deviceId": device_id,
            "gameId": game_id,
            "supportCode": "Discord: loolkkekr",
            "installTime": int(now_utc().timestamp() * 1000),
            "balance": 100,
            "createdAt": iso_now_ms(),
        }

    user = users[device_id]

    # если клиент прислал installTime — обновим у себя (если он не был задан)
    client_install_time = device_info.get('installTime')
    if client_install_time is not None:
        user['installTime'] = client_install_time
    save_users(users)

    user_id = user['userId']
    support_code = user['supportCode']
    install_time = user['installTime']

    # ---- токены ----
    issued_at = int(now_utc().timestamp())
    expires_at = issued_at + 86400  # 24 часа

    session_token = uuid.uuid4().hex

    play_link_payload = {
        "iat": issued_at,
        "nbf": issued_at,
        "exp": expires_at,
        "partnerApplicationId": f"{game_id}Tetris_Prod_34c9cba7-f7c6-4166-b96e-d810e63a36cf",
        "playerId": user_id,
        "playstudiosUserId": user_id,
        "supportCode": support_code,
        "identityProvider": "Anonymous",
    }
    play_link_token = make_jwt(play_link_payload, JWT_SECRET)

    response = {
        "sessionToken": session_token,
        "sessionExpiration": expires_at * 1000,  # мс
        "userId": user_id,
        "installTime": install_time,
        "playLinkToken": play_link_token,
        "lp": {"currentBalance": user['balance']},
        "supportCode": support_code,
        "isNewUser": is_new_user,
        "isDailyLimitReached": False,
        "time": {"currentTime": iso_now_ms()},
        "socialInfo": {
            "isSocialAcc": False,
            "connectedTo": None,
            "email": None
        },
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
    }

    return jsonify(response)


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
