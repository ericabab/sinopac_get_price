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

# 初始化 Shioaji
api = sj.Shioaji(simulation=True)  # 模擬環境，真實環境改成 simulation=False

# 登入
api.login(
    api_key=os.environ["SINO_API_KEY"],
    secret_key=os.environ["SINO_SECRET_KEY"],
    contracts_timeout=10000,
)

cache = {}
CACHE_TTL = 1  # 秒數
CACHE_CLEAN_INTERVAL = 60  # 60 秒清理過期快取
last_cache_clean_time = time.time()

# 全域請求頻率限制
last_request_time = 0
REQUEST_LIMIT_INTERVAL = 0.5  # 1 秒

API_KEY = os.getenv("API_KEY", "mysecret")  # 預設密碼為 mysecret，可放到 .env

def check_rate_limit(now):
    """檢查全域 1 秒請求限制"""
    global last_request_time
    if now - last_request_time < REQUEST_LIMIT_INTERVAL:
        return False
    last_request_time = now
    return True


def check_auth():
    """檢查 API 密碼"""
    password = request.headers.get("Authorization") or request.args.get("password")
    return password == API_KEY


#@app.before_request
def before_request():
    """全域驗證"""
    if not check_auth():
        return jsonify({"error": "Unauthorized. Invalid password."}), 401


def clean_cache(now):
    """清理過期快取"""
    global last_cache_clean_time

    # 每 60 秒清理一次
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
    
    code = request.args.get('code')
    if not code:
        return jsonify({"error": "Missing 'code' parameter"}), 400
    
    now = time.time()

    if not check_rate_limit(now):
        print(f"Too many requests. Please wait {REQUEST_LIMIT_INTERVAL} second before retry.")
        return jsonify({"error": f"Too many requests. Please wait {REQUEST_LIMIT_INTERVAL} second before retry."}), 429

    clean_cache(now)

    if code in cache and now - cache[code]["timestamp"] < CACHE_TTL:
        print("cache hit")
        return jsonify(cache[code]["data"])

    try:
        # 取得股票合約
        contracts = []
        stock_list = code.split(',')
        if not stock_list:
            print("[INFO] No valid stock")
            return jsonify({"error": f"No valid stock with {code}"}), 404
        for s in stock_list:
            if ss := api.Contracts.Stocks[s.upper()]:
                contracts.append(ss)
        if not contracts:
            return jsonify({"error": f"No valid contract with {code}"}), 404
        snapshots = api.snapshots(contracts)
        result = [{"symbol": s.code, "price": s.close,
                   "change_price": s.change_price, "change_rate": s.change_rate} for s in snapshots]
        cache[code] = {"data": result, "timestamp": now}
        print(result)
        return jsonify(result)
    except:
        return jsonify({"error": f"Stock code {code} not found"}), 404


@app.route("/")
def home():
    return "✅ Bot is running!"


@app.route('/healthz', methods=['GET'])
def healthz():
    return "Bot is health ^_^"


def run_web():
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)


threading.Thread(target=run_web).start()
