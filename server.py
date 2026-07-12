from datetime import datetime, timedelta, timezone

from flask import Flask, jsonify, render_template, request

from insider_lib import build_dataset

app = Flask(__name__)


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/trades")
def api_trades():
    days = min(int(request.args.get("days", 90)), 365)
    to_dt = datetime.now(timezone.utc)
    from_dt = to_dt - timedelta(days=days)
    from_date = from_dt.strftime("%Y-%m-%d")
    to_date = to_dt.strftime("%Y-%m-%d")

    try:
        result = build_dataset(from_date, to_date)
    except Exception as e:
        return jsonify({"error": str(e)}), 502

    result["generated_at"] = to_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    return jsonify(result)


if __name__ == "__main__":
    app.run(debug=True, port=5050)
