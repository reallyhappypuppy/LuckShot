import eventlet
eventlet.monkey_patch()
from flask import Flask, render_template, request, redirect, session
from flask_socketio import SocketIO, join_room, emit
from supabase import create_client
from werkzeug.security import generate_password_hash, check_password_hash
import random, string

app = Flask(__name__)
app.secret_key = "secret"
socketio = SocketIO(app, cors_allowed_origins="*")

SUPABASE_URL = "https://ooszwbwvgyjzfotuisgn.supabase.co"
SUPABASE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Im9vc3p3Ynd2Z3lqemZvdHVpc2duIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzM3MjAxOTUsImV4cCI6MjA4OTI5NjE5NX0.1r6a-EV22YIpD0L1F98bNndWjreqPdgth098_GdcLC8"
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

rooms = {}
global_chat_log = []

def generate_code():
    return ''.join(random.choices(string.ascii_uppercase, k=4))

def broadcast_rooms():
    socketio.emit("rooms_update", rooms)

# =========================
# 페이지
# =========================
@app.route("/")
def home():
    return render_template("home.html")

# =========================
# 로그인 / 회원가입
# =========================
@app.route("/register", methods=["GET","POST"])
def register():
    if request.method == "POST":
        u = request.form["username"]
        p = request.form["password"]

        if supabase.table("users").select("*").eq("username", u).execute().data:
            return "Username exists"

        supabase.table("users").insert({
            "username": u,
            "password_hash": generate_password_hash(p),
            "wins": 0
        }).execute()

        return redirect("/login")

    return render_template("register.html")


@app.route("/login", methods=["GET","POST"])
def login():
    if request.method == "POST":
        u = request.form["username"]
        p = request.form["password"]

        res = supabase.table("users").select("*").eq("username", u).execute()

        if not res.data:
            return "No user"

        if not check_password_hash(res.data[0]["password_hash"], p):
            return "Wrong password"

        session["username"] = u
        return redirect("/rooms")

    return render_template("login.html")

@app.route("/rooms")
def rooms_page():
    if "username" not in session:
        return redirect("/login")

    ranking = supabase.table("users") \
        .select("*") \
        .order("wins", desc=True) \
        .limit(10) \
        .execute().data

    return render_template("rooms.html", username=session["username"], rooms=rooms, ranking=ranking)

@app.route("/room/<code>")
def room_page(code):
    if "username" not in session:
        return redirect("/login")
    return render_template("room.html", code=code, username=session["username"])

# =========================
# 방 생성
# =========================
@app.route("/create_room", methods=["POST"])
def create_room():
    code = generate_code()
    name = request.form.get("room_name", "NoName")

    rooms[code] = {
        "name": name,
        "players": [],
        "alive": [],
        "bullets": [],
        "turn": 0,
        "host": session["username"],
        "state": "waiting",
        "sids": {}
    }
    broadcast_rooms()
    return redirect(f"/room/{code}")

# =========================
# Socket
# =========================
@socketio.on("chat")
def chat(data):
    emit("chat", data, to=data["room"])

@socketio.on("global_chat")
def global_chat(data):
    global_chat_log.append(data)
    socketio.emit("global_chat", data)

@socketio.on("get_global_chat")
def get_global_chat():
    emit("global_chat_history", global_chat_log)

@socketio.on("get_rooms")
def get_rooms():
    emit("rooms_update", rooms)

@socketio.on("join")
def join(data):
    code = data["room"]
    user = data["username"]

    join_room(code)

    room = rooms[code]

    room["sids"][request.sid] = user

    if user not in room["players"]:
        room["players"].append(user)

    emit("update", room, to=code)
    broadcast_rooms()

@socketio.on("disconnect")
def disconnect():
    for code in list(rooms.keys()):
        room = rooms[code]

        if request.sid in room["sids"]:
            user = room["sids"].pop(request.sid)

            if user in room["players"]:
                room["players"].remove(user)
            if user in room["alive"]:
                room["alive"].remove(user)

            if user == room["host"]:
                room["host"] = room["players"][0] if room["players"] else None

            # ✅ 방 비었으면 삭제
            if len(room["players"]) == 0:
                del rooms[code]

            emit("update", room, to=code)
            broadcast_rooms()
            break

@socketio.on("start")
def start(data):
    code = data["room"]
    user = data["username"]

    room = rooms[code]

    if user != room["host"]:
        return

    if len(room["players"]) < 2:
        return

    random.shuffle(room["players"])

    n = len(room["players"])
    bullets = ["real"]*n + ["blank"]*n
    random.shuffle(bullets)

    room["bullets"] = bullets
    room["alive"] = room["players"][:]
    room["turn_index"] = 0
    room["state"] = "playing"

    emit("update", room, to=code)

@socketio.on("shoot")
def shoot(data):
    code = data["room"]
    shooter = data["shooter"]
    target = data["target"]

    room = rooms[code]

    if room["state"] != "playing":
        return

    alive_players = room["alive"]

    # ✅ 현재 턴
    current = alive_players[room["turn_index"] % len(alive_players)]
    if shooter != current:
        return

    bullet = room["bullets"].pop(0)

    if bullet == "real" and target in room["alive"]:
        room["alive"].remove(target)

    emit("shot", {
        "shooter": shooter,
        "target": target,
        "bullet": bullet
    }, to=code)

    # ✅ 다음 턴 (여기만 사용)
    if len(room["alive"]) > 1:
        room["turn_index"] = room["turn_index"] % len(room["alive"])

        if not (bullet == "blank" and shooter == target):
            room["turn_index"] = (room["turn_index"] + 1) % len(room["alive"])

    # ✅ 상태 동기화 (마지막에 1번만)
    emit("update", room, to=code)

    # 승리 체크
    if len(room["alive"]) == 1:
        winner = room["alive"][0]

        user = supabase.table("users").select("wins").eq("username", winner).execute()
        wins = user.data[0]["wins"] + 1

        supabase.table("users").update({"wins": wins}).eq("username", winner).execute()

        room["state"] = "ended"

        emit("game_over", {"winner": winner}, to=code)
        emit("update", room, to=code)
        
if __name__ == "__main__":
    socketio.run(app, host="0.0.0.0", port=10000)
