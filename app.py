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

def generate_code():
    return ''.join(random.choices(string.ascii_uppercase, k=4))

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

    return redirect(f"/room/{code}")

# =========================
# Socket
# =========================
@socketio.on("chat")
def chat(data):
    emit("chat", data, to=data["room"])

@socketio.on("global_chat")
def global_chat(data):
    emit("global_chat", data, broadcast=True)

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

@socketio.on("disconnect")
def disconnect():
    for code, room in rooms.items():
        if request.sid in room["sids"]:
            user = room["sids"].pop(request.sid)

            if user in room["players"]:
                room["players"].remove(user)
            if user in room["alive"]:
                room["alive"].remove(user)

            if user == room["host"]:
                room["host"] = room["players"][0] if room["players"] else None

            emit("update", room, to=code)

@socketio.on("start")
def start(data):
    code = data["room"]
    user = data["username"]

    room = rooms[code]

    if user != room["host"]:
        return

    if len(room["players"]) < 2:
        return

    n = len(room["players"])
    bullets = ["real"]*n + ["blank"]*n
    random.shuffle(bullets)

    room["bullets"] = bullets
    room["alive"] = room["players"][:]
    room["turn"] = 0
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

    current = room["players"][room["turn"]]
    if shooter != current:
        return

    bullet = room["bullets"].pop(0)

    if bullet == "real" and target in room["alive"]:
        room["alive"].remove(target)

    if not (bullet == "blank" and shooter == target):
        room["turn"] = (room["turn"] + 1) % len(room["players"])

    emit("shot", {
        "shooter": shooter,
        "target": target,
        "bullet": bullet
    }, to=code)

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
