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
APP_SECRET = os.getenv("APP_SECRET", "baedalpot-dev-secret")

supabase: Client | None = None
if SUPABASE_URL and SUPABASE_KEY:
    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)


def require_db():
    if supabase is None:
        return False, jsonify({"error": "Supabase 환경변수가 설정되지 않았습니다."}), 500
    return True, None, None


def hash_password(password: str, salt: str | None = None) -> tuple[str, str]:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.sha256((salt + password).encode("utf-8")).hexdigest()
    return salt, digest


def check_password(password: str, salt: str | None, digest: str | None) -> bool:
    if not salt or not digest:
        return False
    return hash_password(password, salt)[1] == digest


def make_token(user_id, password_hash: str) -> str:
    raw = f"{user_id}:{password_hash}:{APP_SECRET}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def current_user():
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return None
    token = auth.replace("Bearer ", "", 1).strip()
    user_id = request.headers.get("X-User-Id")
    if not token or not user_id:
        return None
    try:
        result = supabase.table("users").select("id,name,password_hash").eq("id", int(user_id)).limit(1).execute()
        if not result.data:
            return None
        user = result.data[0]
        if make_token(user["id"], user["password_hash"]) != token:
            return None
        return user
    except Exception:
        return None


def require_user():
    user = current_user()
    if not user:
        return None, jsonify({"error": "로그인이 필요합니다."}), 401
    return user, None, None


def to_timestamp_ms(value) -> int:
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return int(dt.timestamp() * 1000)
    except Exception:
        return int(datetime.now(timezone.utc).timestamp() * 1000)


def is_expired(deadline_at) -> bool:
    if not deadline_at:
        return False
    try:
        dt = datetime.fromisoformat(str(deadline_at).replace("Z", "+00:00"))
        return datetime.now(timezone.utc) > dt.astimezone(timezone.utc)
    except Exception:
        return False


def to_client_post(row: dict, participants_by_post: dict | None = None) -> dict:
    participants = []
    if participants_by_post is not None:
        participants = participants_by_post.get(str(row.get("id")), [])
    deadline_at = row.get("deadline_at")
    return {
        "id": str(row.get("id")),
        "user_id": str(row.get("user_id")) if row.get("user_id") is not None else "",
        "ts": to_timestamp_ms(row.get("created_at")),
        "author": row.get("author", ""),
        "cat": row.get("cat", ""),
        "menu": row.get("menu") or "",
        "budget": int(row.get("budget") or 0),
        "size": int(row.get("size") or 0),
        "joined": int(row.get("joined") or 0),
        "msg": row.get("msg") or "",
        "deadline_at": deadline_at or "",
        "deadline_ts": to_timestamp_ms(deadline_at) if deadline_at else None,
        "expired": is_expired(deadline_at),
        "participants": participants,
    }


def to_client_message(row: dict) -> dict:
    return {
        "id": str(row.get("id")),
        "post_id": str(row.get("post_id")),
        "user_id": str(row.get("user_id")) if row.get("user_id") is not None else "",
        "nickname": row.get("nickname", ""),
        "message": row.get("message", ""),
        "ts": to_timestamp_ms(row.get("created_at")),
    }


@app.route("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.route("/api/health")
def health():
    return jsonify({"ok": True})


@app.route("/api/auth/signup", methods=["POST"])
def signup():
    ok, res, code = require_db()
    if not ok:
        return res, code
    body = request.get_json(silent=True) or {}
    name = str(body.get("name", "")).strip()
    password = str(body.get("password", "")).strip()
    if not name or not password:
        return jsonify({"error": "이름과 비밀번호를 입력해주세요."}), 400
    if len(name) > 20:
        return jsonify({"error": "이름은 20자 이내로 입력해주세요."}), 400
    if len(password) < 4:
        return jsonify({"error": "비밀번호는 4자리 이상으로 입력해주세요."}), 400
    exists = supabase.table("users").select("id").eq("name", name).limit(1).execute()
    if exists.data:
        return jsonify({"error": "이미 사용 중인 이름입니다."}), 409
    salt, digest = hash_password(password)
    result = supabase.table("users").insert({"name": name, "password_salt": salt, "password_hash": digest}).execute()
    if not result.data:
        return jsonify({"error": "회원가입에 실패했습니다."}), 500
    user = result.data[0]
    return jsonify({"id": str(user["id"]), "name": user["name"], "token": make_token(user["id"], user["password_hash"])})


@app.route("/api/auth/login", methods=["POST"])
def login():
    ok, res, code = require_db()
    if not ok:
        return res, code
    body = request.get_json(silent=True) or {}
    name = str(body.get("name", "")).strip()
    password = str(body.get("password", "")).strip()
    found = supabase.table("users").select("id,name,password_salt,password_hash").eq("name", name).limit(1).execute()
    if not found.data:
        return jsonify({"error": "이름 또는 비밀번호가 일치하지 않습니다."}), 401
    user = found.data[0]
    if not check_password(password, user.get("password_salt"), user.get("password_hash")):
        return jsonify({"error": "이름 또는 비밀번호가 일치하지 않습니다."}), 401
    return jsonify({"id": str(user["id"]), "name": user["name"], "token": make_token(user["id"], user["password_hash"])})


@app.route("/api/posts", methods=["GET"])
def get_posts():
    ok, res, code = require_db()
    if not ok:
        return res, code
    cat = request.args.get("cat")
    query = supabase.table("posts").select("id,user_id,author,cat,menu,budget,size,joined,msg,deadline_at,created_at").order("created_at", desc=True)
    if cat and cat != "전체":
        query = query.eq("cat", cat)
    result = query.execute()
    posts = result.data or []
    ids = [p["id"] for p in posts]
    participants_by_post = {}
    if ids:
        pres = supabase.table("post_participants").select("post_id,user_id,nickname").in_("post_id", ids).execute()
        for r in pres.data or []:
            participants_by_post.setdefault(str(r["post_id"]), []).append({"user_id": str(r.get("user_id") or ""), "nickname": r.get("nickname", "")})
    return jsonify([to_client_post(row, participants_by_post) for row in posts])


@app.route("/api/posts", methods=["POST"])
def create_post():
    ok, res, code = require_db()
    if not ok:
        return res, code
    user, res, code = require_user()
    if not user:
        return res, code
    body = request.get_json(silent=True) or {}
    cat = str(body.get("cat", "")).strip()
    menu = str(body.get("menu", "")).strip()
    msg = str(body.get("msg", "")).strip()
    deadline_at = str(body.get("deadline_at", "")).strip()
    try:
        budget = int(body.get("budget", 0))
        size = int(body.get("size", 0))
    except (TypeError, ValueError):
        return jsonify({"error": "예산과 모집 인원은 숫자여야 합니다."}), 400
    if not cat or budget < 1000 or size < 2 or size > 4 or not deadline_at:
        return jsonify({"error": "카테고리, 예산, 모집 인원, 마감시간을 확인해주세요."}), 400
    try:
        deadline_dt = datetime.fromisoformat(deadline_at.replace("Z", "+00:00"))
        if deadline_dt.tzinfo is None:
            deadline_dt = deadline_dt.replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) >= deadline_dt.astimezone(timezone.utc):
            return jsonify({"error": "마감시간은 현재 이후로 설정해주세요."}), 400
    except Exception:
        return jsonify({"error": "마감시간 형식이 올바르지 않습니다."}), 400
    new_post = {
        "user_id": int(user["id"]),
        "author": user["name"],
        "cat": cat,
        "menu": menu,
        "budget": budget,
        "size": size,
        "joined": 1,
        "msg": msg,
        "deadline_at": deadline_dt.astimezone(timezone.utc).isoformat(),
    }
    result = supabase.table("posts").insert(new_post).execute()
    if not result.data:
        return jsonify({"error": "게시글 저장에 실패했습니다."}), 500
    post = result.data[0]
    supabase.table("post_participants").insert({"post_id": post["id"], "user_id": int(user["id"]), "nickname": user["name"]}).execute()
    return jsonify(to_client_post(post, {str(post["id"]): [{"user_id": str(user["id"]), "nickname": user["name"]}]})), 201


@app.route("/api/posts/<post_id>/join", methods=["POST"])
def join_post(post_id):
    ok, res, code = require_db()
    if not ok:
        return res, code
    user, res, code = require_user()
    if not user:
        return res, code
    found = supabase.table("posts").select("*").eq("id", post_id).limit(1).execute()
    if not found.data:
        return jsonify({"error": "게시글을 찾을 수 없습니다."}), 404
    post = found.data[0]
    if is_expired(post.get("deadline_at")):
        return jsonify({"error": "마감시간이 지난 팟입니다."}), 409
    joined = int(post.get("joined") or 0)
    size = int(post.get("size") or 0)
    if joined >= size:
        return jsonify({"error": "이미 마감된 팟입니다."}), 409
    exists = supabase.table("post_participants").select("id").eq("post_id", post_id).eq("user_id", int(user["id"])).limit(1).execute()
    if exists.data:
        return jsonify({"error": "이미 이 팟에 참여했습니다."}), 409
    try:
        supabase.table("post_participants").insert({"post_id": int(post_id), "user_id": int(user["id"]), "nickname": user["name"]}).execute()
    except Exception:
        return jsonify({"error": "이미 이 팟에 참여했거나 참여 처리에 실패했습니다."}), 409
    updated = supabase.table("posts").update({"joined": joined + 1}).eq("id", post_id).execute()
    return jsonify(to_client_post(updated.data[0] if updated.data else post))


@app.route("/api/posts/<post_id>", methods=["DELETE"])
def delete_post(post_id):
    ok, res, code = require_db()
    if not ok:
        return res, code
    user, res, code = require_user()
    if not user:
        return res, code
    found = supabase.table("posts").select("id,user_id").eq("id", post_id).limit(1).execute()
    if not found.data:
        return jsonify({"error": "게시글을 찾을 수 없습니다."}), 404
    if str(found.data[0].get("user_id")) != str(user["id"]):
        return jsonify({"error": "작성자만 삭제할 수 있습니다."}), 403
    supabase.table("chat_messages").delete().eq("post_id", post_id).execute()
    supabase.table("post_participants").delete().eq("post_id", post_id).execute()
    supabase.table("posts").delete().eq("id", post_id).execute()
    return jsonify({"deleted": 1})


@app.route("/api/posts/<post_id>/messages", methods=["GET"])
def get_messages(post_id):
    ok, res, code = require_db()
    if not ok:
        return res, code
    user, res, code = require_user()
    if not user:
        return res, code
    postres = supabase.table("posts").select("id,deadline_at").eq("id", post_id).limit(1).execute()
    if not postres.data:
        return jsonify({"error": "게시글을 찾을 수 없습니다."}), 404
    if is_expired(postres.data[0].get("deadline_at")):
        return jsonify({"error": "마감시간이 지난 팟의 채팅은 사용할 수 없습니다."}), 403
    mem = supabase.table("post_participants").select("id").eq("post_id", post_id).eq("user_id", int(user["id"])).limit(1).execute()
    if not mem.data:
        return jsonify({"error": "참여한 팟의 채팅만 볼 수 있습니다."}), 403
    result = supabase.table("chat_messages").select("*").eq("post_id", post_id).order("created_at", desc=False).execute()
    return jsonify([to_client_message(row) for row in (result.data or [])])


@app.route("/api/posts/<post_id>/messages", methods=["POST"])
def create_message(post_id):
    ok, res, code = require_db()
    if not ok:
        return res, code
    user, res, code = require_user()
    if not user:
        return res, code
    body = request.get_json(silent=True) or {}
    message = str(body.get("message", "")).strip()
    if not message:
        return jsonify({"error": "메시지를 입력해주세요."}), 400
    if len(message) > 300:
        return jsonify({"error": "메시지는 300자 이내로 입력해주세요."}), 400
    postres = supabase.table("posts").select("id,deadline_at").eq("id", post_id).limit(1).execute()
    if not postres.data:
        return jsonify({"error": "게시글을 찾을 수 없습니다."}), 404
    if is_expired(postres.data[0].get("deadline_at")):
        return jsonify({"error": "마감시간이 지난 팟의 채팅은 사용할 수 없습니다."}), 403
    mem = supabase.table("post_participants").select("id").eq("post_id", post_id).eq("user_id", int(user["id"])).limit(1).execute()
    if not mem.data:
        return jsonify({"error": "참여한 팟에서만 채팅할 수 있습니다."}), 403
    result = supabase.table("chat_messages").insert({
        "post_id": int(post_id),
        "user_id": int(user["id"]),
        "nickname": user["name"],
        "message": message,
    }).execute()
    if not result.data:
        return jsonify({"error": "메시지 저장에 실패했습니다."}), 500
    return jsonify(to_client_message(result.data[0])), 201


if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=True)
