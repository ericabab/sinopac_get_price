from flask import Flask, request, jsonify
import shioaji as sj
import os
import threading
import time
from dotenv import load_dotenv

if not os.getenv("RENDER") and not os.getenv("DOCKER") and not os.getenv("HEROKU"):
    load_dotenv()
    print("載入本地 .env 檔")
else:
    print("偵測到雲端環境，略過 .env 載入")

app = Flask(__name__)

API_KEY = os.getenv("API_KEY", "my_secret")
SINO_API_KEY = os.environ["SINO_API_KEY"]
SINO_SECRET_KEY = os.environ["SINO_SECRET_KEY"]

CACHE_TTL = 1
CACHE_CLEAN_INTERVAL = 60
REQUEST_LIMIT_INTERVAL = 0.5

cache = {}
last_cache_clean_time = time.time()
last_request_time = 0

api = None
last_ping_time = 0


def init_shioaji():
    """初始化並登入 Shioaji"""
    global api
    try:
        print("[INFO] 初始化 Shioaji...")
        api = sj.Shioaji(simulation=True)
        api.login(api_key=SINO_API_KEY, secret_key=SINO_SECRET_KEY, contracts_timeout=10000)
        print("[INFO] Shioaji 登入成功")
    except Exception as e:
        print(f"[ERROR] Shioaji 初始化失敗: {e}")
        api = None


def keep_alive():
    """定時 ping 避免斷線"""
    global api, last_ping_time
    while True:
        try:
            if api:
                now = time.time()
                if now - last_ping_time > 30:  # 每 30 秒 ping 一次
                    api.list_accounts()  # 輕量 API 呼叫
                    last_ping_time = now
        except Exception as e:
            print(f"[WARN] 連線可能斷線，嘗試重連: {e}")
            init_shioaji()
        time.sleep(60)


def check_rate_limit(now):
    global last_request_time
    if now - last_request_time < REQUEST_LIMIT_INTERVAL:
        return False
    last_request_time = now
    return True


def check_auth():
    password = request.headers.get("Authorization") or request.args.get("password")
    return password == API_KEY


def clean_cache(now):
    global last_cache_clean_time
    if now - last_cache_clean_time > CACHE_CLEAN_INTERVAL:
        expired_keys = [
            key for key, value in cache.items()
            if now - value["timestamp"] > CACHE_TTL
        ]
        for key in expired_keys:
            del cache[key]
        last_cache_clean_time = now
        if expired_keys:
            print(f"清理過期快取: {expired_keys}")


@app.route('/get_contract', methods=['GET'])
def get_contract():
    if not check_auth():
        return jsonify({"error": "Unauthorized. Invalid password."}), 401
    if not api:
        return jsonify({"error": "Shioaji 未初始化"}), 500

    code = request.args.get('code')
    if not code:
        return jsonify({"error": "Missing 'code' parameter"}), 400

    now = time.time()
    if not check_rate_limit(now):
        return jsonify({"error": f"Too many requests. Please wait {REQUEST_LIMIT_INTERVAL} sec"}), 429

    clean_cache(now)

    if code in cache and now - cache[code]["timestamp"] < CACHE_TTL:
        return jsonify(cache[code]["data"])

    try:
        contracts = []
        stock_list = code.split(',')
        for s in stock_list:
            contract = api.Contracts.Stocks.get(s.upper())
            if contract:
                contracts.append(contract)
        if not contracts:
            return jsonify({"error": f"No valid contract with {code}"}), 404

        snapshots = api.snapshots(contracts)
        result = [
            {"symbol": s.code, "price": s.close,
             "change_price": s.change_price, "change_rate": s.change_rate}
            for s in snapshots
        ]
        cache[code] = {"data": result, "timestamp": now}
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/")
def home():
    return "✅ Bot is running!"


@app.route('/healthz', methods=['GET'])
def healthz():
    return "OK", 200


def run_web():
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)


# 啟動
init_shioaji()
threading.Thread(target=keep_alive, daemon=True).start()
threading.Thread(target=run_web).start()
