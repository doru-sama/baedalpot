import os
import hashlib
import secrets
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


def make_password_hash(password: str) -> tuple[str, str]:
    salt = secrets.token_hex(16)
    digest = hashlib.sha256((salt + password).encode("utf-8")).hexdigest()
    return salt, digest


def check_password(password: str, salt: str | None, digest: str | None) -> bool:
    if not salt or not digest:
        return False
    return hashlib.sha256((salt + password).encode("utf-8")).hexdigest() == digest


def to_timestamp_ms(created_at) -> int:
    try:
        dt = datetime.fromisoformat(str(created_at).replace("Z", "+00:00"))
        return int(dt.timestamp() * 1000)
    except Exception:
        return int(datetime.now(timezone.utc).timestamp() * 1000)


def to_client_post(row: dict) -> dict:
    created_at = row.get("created_at")
    ts = row.get("ts")
    if ts is None:
        ts = to_timestamp_ms(created_at)

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


def to_client_message(row: dict) -> dict:
    return {
        "id": str(row.get("id")),
        "post_id": str(row.get("post_id")),
        "nickname": row.get("nickname", ""),
        "message": row.get("message", ""),
        "ts": to_timestamp_ms(row.get("created_at")),
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
    query = supabase.table("posts").select("id,author,cat,menu,budget,size,joined,msg,created_at").order("created_at", desc=True)
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
    password = str(body.get("password", "")).strip()
    cat = str(body.get("cat", "")).strip()
    menu = str(body.get("menu", "")).strip()
    msg = str(body.get("msg", "")).strip()

    try:
        budget = int(body.get("budget", 0))
        size = int(body.get("size", 0))
    except (TypeError, ValueError):
        return jsonify({"error": "budget, size는 숫자여야 합니다."}), 400

    if not author or not password or not cat or not budget or not size:
        return jsonify({"error": "닉네임, 비밀번호, 카테고리, 예산, 모집 인원은 필수입니다."}), 400
    if len(password) < 4:
        return jsonify({"error": "비밀번호는 4자리 이상으로 입력해주세요."}), 400
    if size < 2 or size > 4:
        return jsonify({"error": "모집 인원은 2~4명만 가능합니다."}), 400
    if budget < 1000:
        return jsonify({"error": "1인 예산은 1000원 이상이어야 합니다."}), 400

    salt, digest = make_password_hash(password)
    new_post = {
        "author": author,
        "password_salt": salt,
        "password_hash": digest,
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

    post = result.data[0]
    # 작성자도 첫 참여자로 등록한다. 중복 참여 방지에 사용됨.
    try:
        supabase.table("post_participants").insert({"post_id": post["id"], "nickname": author}).execute()
    except Exception:
        pass
    return jsonify(to_client_post(post)), 201


@app.route("/api/posts/<post_id>/join", methods=["POST"])
def join_post(post_id):
    ok, res, code = require_db()
    if not ok:
        return res, code

    body = request.get_json(silent=True) or {}
    nickname = str(body.get("nickname", "")).strip()
    if not nickname:
        return jsonify({"error": "참여하려면 닉네임을 입력해주세요."}), 400

    found = supabase.table("posts").select("*").eq("id", post_id).limit(1).execute()
    if not found.data:
        return jsonify({"error": "게시글을 찾을 수 없습니다."}), 404

    post = found.data[0]
    joined = int(post.get("joined") or 0)
    size = int(post.get("size") or 0)
    if joined >= size:
        return jsonify({"error": "이미 마감된 팟입니다."}), 409

    exists = supabase.table("post_participants").select("id").eq("post_id", post_id).eq("nickname", nickname).limit(1).execute()
    if exists.data:
        return jsonify({"error": "이미 이 팟에 참여했습니다."}), 409

    try:
        supabase.table("post_participants").insert({"post_id": int(post_id), "nickname": nickname}).execute()
    except Exception:
        return jsonify({"error": "이미 이 팟에 참여했거나 참여 처리에 실패했습니다."}), 409

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
    password = str(request.args.get("password", "")).strip()
    if not author or not password:
        return jsonify({"error": "닉네임과 비밀번호가 필요합니다."}), 400

    found = supabase.table("posts").select("id,password_salt,password_hash").eq("author", author).execute()
    posts = found.data or []
    targets = [p["id"] for p in posts if check_password(password, p.get("password_salt"), p.get("password_hash"))]
    if not targets:
        return jsonify({"deleted": 0, "error": "닉네임 또는 비밀번호가 일치하지 않습니다."}), 403

    for post_id in targets:
        # 외래키 cascade가 없어도 안전하게 직접 삭제
        supabase.table("chat_messages").delete().eq("post_id", post_id).execute()
        supabase.table("post_participants").delete().eq("post_id", post_id).execute()
        supabase.table("posts").delete().eq("id", post_id).execute()
    return jsonify({"deleted": len(targets)})


@app.route("/api/posts/<post_id>/messages", methods=["GET"])
def get_messages(post_id):
    ok, res, code = require_db()
    if not ok:
        return res, code
    result = supabase.table("chat_messages").select("*").eq("post_id", post_id).order("created_at", desc=False).execute()
    return jsonify([to_client_message(row) for row in (result.data or [])])


@app.route("/api/posts/<post_id>/messages", methods=["POST"])
def create_message(post_id):
    ok, res, code = require_db()
    if not ok:
        return res, code

    body = request.get_json(silent=True) or {}
    nickname = str(body.get("nickname", "")).strip()
    message = str(body.get("message", "")).strip()
    if not nickname or not message:
        return jsonify({"error": "닉네임과 메시지를 입력해주세요."}), 400
    if len(message) > 300:
        return jsonify({"error": "메시지는 300자 이내로 입력해주세요."}), 400

    found = supabase.table("posts").select("id").eq("id", post_id).limit(1).execute()
    if not found.data:
        return jsonify({"error": "게시글을 찾을 수 없습니다."}), 404

    result = supabase.table("chat_messages").insert({
        "post_id": int(post_id),
        "nickname": nickname,
        "message": message,
    }).execute()
    if not result.data:
        return jsonify({"error": "메시지 저장에 실패했습니다."}), 500
    return jsonify(to_client_message(result.data[0])), 201


if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=True)
