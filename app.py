import os
from datetime import datetime, timezone
from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS
from dotenv import load_dotenv
from supabase import create_client, Client

load_dotenv()

app = Flask(__name__, static_folder="public", static_url_path="")
CORS(app)

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY") or os.getenv("SUPABASE_ANON_KEY")

supabase: Client | None = None
if SUPABASE_URL and SUPABASE_KEY:
    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)


def require_db():
    if supabase is None:
        return False, jsonify({"error": "Supabase 환경변수가 설정되지 않았습니다. SUPABASE_URL, SUPABASE_KEY를 확인하세요."}), 500
    return True, None, None


def to_client_post(row: dict) -> dict:
    """Supabase row를 기존 HTML이 쓰던 형태로 변환."""
    created_at = row.get("created_at")
    ts = row.get("ts")
    if ts is None:
        try:
            # Supabase timestamp example: 2026-06-03T03:12:00.123456+00:00
            dt = datetime.fromisoformat(str(created_at).replace("Z", "+00:00"))
            ts = int(dt.timestamp() * 1000)
        except Exception:
            ts = int(datetime.now(timezone.utc).timestamp() * 1000)

    return {
        "id": str(row.get("id")),
        "ts": ts,
        "author": row.get("author", ""),
        "cat": row.get("cat", ""),
        "menu": row.get("menu") or "",
        "budget": int(row.get("budget") or 0),
        "size": int(row.get("size") or 0),
        "joined": int(row.get("joined") or 0),
        "msg": row.get("msg") or "",
    }


@app.route("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.route("/api/health", methods=["GET"])
def health():
    return jsonify({"ok": True, "service": "baedalpot-python-flask"})


@app.route("/api/posts", methods=["GET"])
def get_posts():
    ok, res, code = require_db()
    if not ok:
        return res, code

    cat = request.args.get("cat")
    query = supabase.table("posts").select("*").order("created_at", desc=True)
    if cat and cat != "전체":
        query = query.eq("cat", cat)

    result = query.execute()
    posts = [to_client_post(row) for row in (result.data or [])]
    return jsonify(posts)


@app.route("/api/posts", methods=["POST"])
def create_post():
    ok, res, code = require_db()
    if not ok:
        return res, code

    body = request.get_json(silent=True) or {}
    author = str(body.get("author", "")).strip()
    cat = str(body.get("cat", "")).strip()
    menu = str(body.get("menu", "")).strip()
    msg = str(body.get("msg", "")).strip()

    try:
        budget = int(body.get("budget", 0))
        size = int(body.get("size", 0))
    except (TypeError, ValueError):
        return jsonify({"error": "budget, size는 숫자여야 합니다."}), 400

    if not author or not cat or not budget or not size:
        return jsonify({"error": "author, cat, budget, size는 필수입니다."}), 400
    if size < 2 or size > 4:
        return jsonify({"error": "모집 인원은 2~4명만 가능합니다."}), 400
    if budget < 1000:
        return jsonify({"error": "1인 예산은 1000원 이상이어야 합니다."}), 400

    new_post = {
        "author": author,
        "cat": cat,
        "menu": menu,
        "budget": budget,
        "size": size,
        "joined": 1,
        "msg": msg,
    }
    result = supabase.table("posts").insert(new_post).execute()
    if not result.data:
        return jsonify({"error": "게시글 저장에 실패했습니다."}), 500
    return jsonify(to_client_post(result.data[0])), 201


@app.route("/api/posts/<post_id>/join", methods=["POST"])
def join_post(post_id):
    ok, res, code = require_db()
    if not ok:
        return res, code

    found = supabase.table("posts").select("*").eq("id", post_id).limit(1).execute()
    if not found.data:
        return jsonify({"error": "게시글을 찾을 수 없습니다."}), 404

    post = found.data[0]
    joined = int(post.get("joined") or 0)
    size = int(post.get("size") or 0)
    if joined >= size:
        return jsonify({"error": "이미 마감된 팟입니다."}), 409

    updated = supabase.table("posts").update({"joined": joined + 1}).eq("id", post_id).execute()
    if not updated.data:
        return jsonify({"error": "참여 처리에 실패했습니다."}), 500
    return jsonify(to_client_post(updated.data[0]))


@app.route("/api/posts", methods=["DELETE"])
def delete_posts():
    ok, res, code = require_db()
    if not ok:
        return res, code

    author = str(request.args.get("author", "")).strip()
    if not author:
        return jsonify({"error": "author가 필요합니다."}), 400

    found = supabase.table("posts").select("id").eq("author", author).execute()
    delete_count = len(found.data or [])
    if delete_count:
        supabase.table("posts").delete().eq("author", author).execute()
    return jsonify({"deleted": delete_count})


if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=True)
