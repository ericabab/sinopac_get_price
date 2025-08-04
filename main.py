from flask import Flask, request, jsonify
import shioaji as sj
import os
import threading

#from dotenv import load_dotenv
#load_dotenv()

app = Flask(__name__)

# 初始化 Shioaji
api = sj.Shioaji(simulation=True)  # 模擬環境，真實環境改成 simulation=False

# 登入
api.login(
    api_key=os.environ["API_KEY"],
    secret_key=os.environ["SECRET_KEY"],
    contracts_timeout=10000,
)


@app.route('/get_contract', methods=['GET'])
def get_contract():
    code = request.args.get('code')
    if not code:
        return jsonify({"error": "Missing 'code' parameter"}), 400

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
        return jsonify(result)
    except:
        return jsonify({"error": f"Stock code {code} not found"}), 404


@app.route("/")
def home():
    return "✅ Bot is running!"


def run_web():
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)

threading.Thread(target=run_web).start()
