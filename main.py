import os, sys
import time
import shioaji as sj
from flask import Flask, jsonify, request
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from apscheduler.schedulers.background import BackgroundScheduler
import pytz
import logging
import signal
from typing import Optional

# 保留 root logger 設定
logging.basicConfig(level=logging.INFO, format="%(asctime)s - ROOT - %(levelname)s - %(message)s")

# 建立自己 logger
my_logger = logging.getLogger("my_main_logger")
my_logger.setLevel(logging.INFO)  # 設定等級

# 建立 handler，設定輸出位置（console）
console_handler = logging.StreamHandler()
console_handler.setLevel(logging.INFO)

# 設定格式
formatter = logging.Formatter("%(asctime)s - MAIN - %(levelname)s - %(message)s")
console_handler.setFormatter(formatter)

# 把 handler 加到 logger
my_logger.addHandler(console_handler)
my_logger.propagate = False

# ====== 環境變數 ======
if not os.getenv("RENDER") and not os.getenv("DOCKER") and not os.getenv("HEROKU"):
    from dotenv import load_dotenv
    load_dotenv()
    my_logger.info("載入本地 .env 檔")
else:
    my_logger.info("偵測到雲端環境，略過 .env 載入")


API_KEY = os.environ.get("SINO_API_KEY")
API_SECRET = os.environ.get("SINO_SECRET_KEY")
AUTH_PASSWORD = os.environ.get("AUTH_PASSWORD", "your_password")

# ====== 初始化 Flask ======
app = Flask(__name__)
limiter = Limiter(get_remote_address, app=app, default_limits=["5 per second"])

# ====== 初始化 Shioaji as None ======
api: Optional[sj.shioaji.Shioaji] = None

def handle_exit(signum, frame):
    my_logger.info(f"收到訊號 {signum}, 登出 Shioaji...")
    try:
        api.logout()
        my_logger.info(f"✅ 登出成功")
    except Exception as e:
        my_logger.info(f"⚠️ 登出失敗: {e}")
    sys.exit(0)

signal.signal(signal.SIGINT, handle_exit)
signal.signal(signal.SIGTERM, handle_exit)


def login_shioaji(max_retries=20, retry_interval=5):
    """嘗試登入 Shioaji，直到成功或達到最大重試次數"""
    global api
    for _ in range(max_retries):
        try:
            my_logger.info(f"In login_shioaji")
            api = sj.Shioaji(simulation=True)
            api.login(api_key=API_KEY, secret_key=API_SECRET, contracts_timeout=10000)
            my_logger.info(f"API Usage: {api.usage()}")
            if api.list_accounts():
                my_logger.info(f"✅ Shioaji login successful.")
                return True
        except Exception as e:
            my_logger.error(f"[❌ Login failed: {e}")
        time.sleep(retry_interval)
    my_logger.error(f"⚠️ Max retries reached. Login aborted.")
    return False


# 啟動時先登入一次
login_shioaji()


def ensure_ready():
    """檢查 Shioaji 是否 ready，否則重新登入"""
    try:
        if not api.list_accounts():
            login_shioaji()
    except Exception:
        login_shioaji()


# ====== 每日自動重登 ======
def scheduled_relogin():
    global api
    my_logger.info(f"🔄 Scheduled relogin triggered...")
    try:
        api.logout()
    except Exception as e:
        my_logger.info(f"Logout error: {e}")
    login_shioaji()


scheduler = BackgroundScheduler(timezone=pytz.timezone("Asia/Taipei"))
scheduler.add_job(scheduled_relogin, "cron", hour=5, minute=0, misfire_grace_time=60)
scheduler.start()

# ====== 簡易 Cache ======
CACHE_TTL = 3  # 秒
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

    remaining = api.usage().remaining_bytes
    if remaining < 0:
        my_logger.info(f"⚠️ 額度不足！已超過 {-remaining} bytes")
        return jsonify({"error": f"⚠️ 額度不足！已超過 {-remaining} bytes"}), 500


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
                my_logger.info(f"No new stocks needed fetch, result={results}")
                return jsonify(results)

            snapshots = api.snapshots(contracts)
            if not snapshots:
                my_logger.info("snapshot is empty, return error")
                return jsonify({"error": "snapshot is empty"}), 500

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

            my_logger.info(f"result={results}")
            return jsonify(results)
        except Exception as e:
            return jsonify({"error": str(e)}), 500
    return jsonify(results)


# ====== 啟動 Flask ======
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
