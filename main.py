import os, sys, time, logging, signal
import shioaji as sj
from flask import Flask, jsonify, request, send_from_directory
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
# from apscheduler.schedulers.background import BackgroundScheduler
# import pytz
import psutil

# ====== 初始化 ======
app = Flask(__name__)
limiter = Limiter(get_remote_address, app=app)
api = sj.Shioaji(simulation=True)


def create_logger():
    # 保留 root logger 設定
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - ROOT  - %(levelname)s - %(message)s")

    # 建立自己 logger
    new_logger = logging.getLogger("my_main_logger")
    new_logger.setLevel(logging.INFO)  # 設定等級

    # 建立 handler，設定輸出位置（console）
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)

    # 設定格式
    formatter = logging.Formatter("%(asctime)s - MYLOG - %(levelname)s - %(message)s")
    console_handler.setFormatter(formatter)

    # 把 handler 加到 logger
    new_logger.addHandler(console_handler)
    new_logger.propagate = False

    return new_logger


def log_mem_usage():
    result = f"Memory usage: {process.memory_info().rss / 1024 ** 2:.2f} MB"
    my_logger.info(result)
    return result


def handle_exit(signum, frame):
    my_logger.info(f"收到訊號 {signum}, 登出 Shioaji...")
    try:
        api.logout()
        my_logger.info(f"✅ 登出成功 {frame}")
    except Exception as e:
        my_logger.info(f"⚠️ 登出失敗: {e}")
    sys.exit(0)


def get_remaining_quote():
    return api.usage().remaining_bytes


def fetch_contracts_if_ok():
    if not hasattr(api, "Contracts") and api.usage().remaining_bytes > 0 :
        my_logger.info(f"Fetch contracts")
        api.fetch_contracts(contracts_timeout=10000)


def login_shioaji(max_retries=20, retry_interval=5):
    """嘗試登入 Shioaji，直到成功或達到最大重試次數"""
    global api
    for i in range(max_retries):
        try:
            my_logger.info(f"LOGIN... (try {i+1}/{max_retries})")
            # api = sj.Shioaji(simulation=True)
            # api.login(api_key=API_KEY, secret_key=API_SECRET, contracts_timeout=10000)
            api.login(api_key=API_KEY, secret_key=API_SECRET, fetch_contract=False)
            log_mem_usage()
            my_logger.info(f"API Usage: {api.usage()}")
            fetch_contracts_if_ok()
            if api.list_accounts():
                my_logger.info(f"✅ Shioaji login successful.")
                return True
        except Exception as e:
            # api = sj.Shioaji(simulation=True)
            my_logger.error(f"[❌ Login failed: {e}")
        time.sleep(retry_interval)
    my_logger.error(f"⚠️ Max retries reached. Login aborted.")
    return False


def ensure_ready():
    """檢查 Shioaji 是否 ready，否則重新登入"""
    try:
        api.usage()
    except Exception as e:
        my_logger.warning(f"api.usage() failed in ensure_ready: {e}")
        try:
            api.logout()
        except:
            pass
        finally:
            login_shioaji()


def get_from_cache(key):
    if key in cache:
        data, ts = cache[key]
        if time.time() - ts < CACHE_TTL:
            return data
    return None


def set_cache(key, value):
    cache[key] = (value, time.time())


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


@app.route('/favicon.ico')
def favicon():
    return send_from_directory("static", "favicon.ico", mimetype="image/vnd.microsoft.icon")


@app.route('/healthz', methods=['GET'])
def healthz():
    ensure_ready()
    return "OK"


@app.route('/memory', methods=['GET'])
def check_mem():
    return log_mem_usage()


@app.route("/price/<codes>")
@limiter.limit("5 per second")
@require_auth
def get_price(codes):
    ensure_ready()

    remaining = api.usage().remaining_bytes
    if remaining < 0:
        my_logger.info(f"⚠️ 額度不足！已超過 {-remaining} bytes")
        return jsonify({"error": f"⚠️ 額度不足！已超過 {-remaining} bytes"}), 500

    fetch_contracts_if_ok()

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


# ################################################
#        MAIN CODE
# ################################################
if __name__ == "__main__":
    # ====== 建立自己 logger ======
    my_logger = create_logger()

    # ====== 記憶體監測 ======
    process = psutil.Process(os.getpid())

    # ====== 處理中斷 =======
    signal.signal(signal.SIGINT, handle_exit)
    signal.signal(signal.SIGTERM, handle_exit)

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

    # ====== local routes ======
    if os.environ.get("ENABLE_LOCAL_ROUTES") == "true":
        try:
            import local_routes
            local_routes.register(app, api)
            my_logger.info("import local routes OK")
        except ImportError:
            local_routes = None
            my_logger.error("⚠️ local_routes not found, skipping...")

    # ===== 啟動時先登入一次 =====
    login_shioaji()

    # ====== 排程重登 ======
    # scheduler = BackgroundScheduler(timezone=pytz.timezone("Asia/Taipei"))
    # scheduler.add_job(keep_alive, "interval", minutes=5)
    # scheduler.start()

    # ====== 簡易 Cache ======
    CACHE_TTL = 3  # 秒
    cache = {}

    # ====== 啟動 Flask ======
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
