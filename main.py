import os
import time
import shioaji as sj
from flask import Flask, jsonify, request
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from datetime import datetime
from apscheduler.schedulers.background import BackgroundScheduler
import pytz
from dotenv import load_dotenv


# ====== 環境變數 ======
if not os.getenv("RENDER") and not os.getenv("DOCKER") and not os.getenv("HEROKU"):
    load_dotenv()
    print("載入本地 .env 檔")
else:
    print("偵測到雲端環境，略過 .env 載入")


API_KEY = os.environ.get("SINO_API_KEY")
API_SECRET = os.environ.get("SINO_SECRET_KEY")
AUTH_PASSWORD = os.environ.get("AUTH_PASSWORD", "your_password")

# ====== 初始化 Flask ======
app = Flask(__name__)
limiter = Limiter(get_remote_address, app=app, default_limits=["5 per second"])

# ====== 初始化 Shioaji ======
api = sj.Shioaji(simulation=True)


def login_shioaji(max_retries=10, retry_interval=3):
    """嘗試登入 Shioaji，直到成功或達到最大重試次數"""
    global api
    retries = 0
    while retries < max_retries:
        try:
            print(f"[{datetime.now()}] Logging in to Shioaji...")
            api = sj.Shioaji(simulation=True)
            api.login(api_key=API_KEY, secret_key=API_SECRET, contracts_timeout=10000)
            if api.list_accounts():
                print(f"[{datetime.now()}] ✅ Shioaji login successful.")
                return True
        except Exception as e:
            print(f"[{datetime.now()}] ❌ Login failed: {e}")
        retries += 1
        time.sleep(retry_interval)
    print(f"[{datetime.now()}] ⚠️ Max retries reached. Login aborted.")
    return False


# 啟動時先登入一次
login_shioaji()


def ensure_ready():
    """檢查 Shioaji 是否 ready，否則重新登入"""
    try:
        _ = api.stock_account
    except Exception:
        login_shioaji()


# ====== 每日自動重登 ======
def scheduled_relogin():
    global api
    print(f"[{datetime.now()}] 🔄 Scheduled relogin triggered...")
    try:
        api.logout()
    except Exception as e:
        print(f"[{datetime.now()}] Logout error: {e}")
    login_shioaji()


scheduler = BackgroundScheduler(timezone=pytz.timezone("Asia/Taipei"))
scheduler.add_job(scheduled_relogin, "cron", hour=5, minute=0)
scheduler.start()

# ====== 簡易 Cache ======
CACHE_TTL = 1  # 秒
cache = {}


def get_from_cache(key):
    if key in cache:
        data, ts = cache[key]
        if time.time() - ts < CACHE_TTL:
            return data
    return None


def set_cache(key, value):
    cache[key] = (value, time.time())


# ====== 認證裝飾器 ======
def require_auth(func):
    def wrapper(*args, **kwargs):
        auth_header = request.headers.get("Authorization", "")
        if auth_header != f"Bearer {AUTH_PASSWORD}":
            return jsonify({"error": "Unauthorized"}), 401
        return func(*args, **kwargs)
    wrapper.__name__ = func.__name__
    return wrapper


# ====== API 路由 ======
@app.route("/")
def home():
    return "✅ Bot is running!"


@app.route('/healthz', methods=['GET'])
def healthz():
    return "OK", 200


@app.route("/price/<codes>")
@limiter.limit("5 per second")
@require_auth
def get_price(codes):
    ensure_ready()

    stock_codes = [code.strip() for code in codes.split(",") if code.strip()]
    results = []

    # 檢查快取，有快取就回傳快取的價格
    codes_to_fetch = []
    for code in stock_codes:
        cached = get_from_cache(f"price:{code}")
        if cached is not None:
            result = cached.copy()
            result["symbol"] = code
            result["source"] = "cache"
            results.append(result)
        else:
            codes_to_fetch.append(code)

    # 不在快取的股票用 shioaji 抓取
    if codes_to_fetch:
        try:
            contracts = []
            for s in codes_to_fetch:
                contract = api.Contracts.Stocks.get(s.upper())
                if contract:
                    contracts.append(contract)
            if not contracts:
                print(f"result={results}")
                return jsonify(results)

            snapshots = api.snapshots(contracts)
            for snap in snapshots:
                data = {
                    "symbol": snap.code,
                    "price": snap.close,
                    "change_price": snap.change_price,
                    "change_rate": snap.change_rate,
                    "source": "shioaji"
                }
                results.append(data)
                set_cache(f"price:{snap.code}", {
                    "price": snap.close,
                    "change_price": snap.change_price,
                    "change_rate": snap.change_rate
                })

            print(f"result={results}")
            return jsonify(results)
        except Exception as e:
            return jsonify({"error": str(e)}), 500


# ====== 啟動 Flask ======
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
