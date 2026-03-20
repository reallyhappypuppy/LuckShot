from flask import Flask, render_template, request, redirect, session
from flask_socketio import SocketIO, join_room, emit
from supabase import create_client
from werkzeug.security import generate_password_hash, check_password_hash
import random, string

app = Flask(__name__)
app.secret_key = "secret"
socketio = SocketIO(app)

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

@app.route("/logout")
def logout():
    session.clear()
    return redirect("/")

@app.route("/rooms")
def rooms_page():
    if "username" not in session:
        return redirect("/login")
    return render_template("rooms.html", username=session["username"], rooms=rooms)

@app.route("/room/<code>")
def room_page(code):
    if "username" not in session:
        return redirect("/login")
    return render_template("room.html", code=code, username=session["username"])

@app.route("/ranking")
def ranking():
    result = supabase.table("users") \
        .select("*") \
        .order("wins", desc=True) \
        .limit(10) \
        .execute()

    return render_template("ranking.html", users=result.data)

# =========================
# 방 생성
# =========================
@app.route("/create_room", methods=["POST"])
def create_room():
    code = generate_code()

    rooms[code] = {
        "players": [],
        "alive": [],
        "bullets": [],
        "turn": 0,
        "host": session["username"],
        "started": False
    }

    return redirect(f"/room/{code}")

# =========================
# Socket
# =========================
@socketio.on("join")
def join(data):
    code = data["room"]
    user = data["username"]

    join_room(code)

    if user not in rooms[code]["players"]:
        rooms[code]["players"].append(user)

    emit("update", rooms[code], to=code)

@socketio.on("chat")
def chat(data):
    emit("chat", data, to=data["room"])

@socketio.on("start")
def start(data):
    code = data["room"]
    room = rooms[code]

    if len(room["players"]) < 2:
        return

    n = len(room["players"])
    bullets = ["real"]*n + ["blank"]*n
    random.shuffle(bullets)

    room["bullets"] = bullets
    room["alive"] = room["players"].copy()
    room["turn"] = 0
    room["started"] = True

    emit("update", room, to=code)

@socketio.on("shoot")
def shoot(data):
    code = data["room"]
    shooter = data["shooter"]
    target = data["target"]

    room = rooms[code]

    current = room["players"][room["turn"]]
    if shooter != current:
        return

    bullet = room["bullets"].pop(0)

    result = {
        "shooter": shooter,
        "target": target,
        "bullet": bullet
    }

    if bullet == "real" and target in room["alive"]:
        room["alive"].remove(target)

    if not (bullet == "blank" and shooter == target):
        room["turn"] = (room["turn"] + 1) % len(room["players"])

    emit("shot", result, to=code)

    # 승리
    if len(room["alive"]) == 1:
        winner = room["alive"][0]

        user = supabase.table("users").select("wins").eq("username", winner).execute()
        wins = user.data[0]["wins"] + 1

        supabase.table("users").update({"wins": wins}).eq("username", winner).execute()

        emit("game_over", {"winner": winner}, to=code)

        room["started"] = False

    if __name__ == "__main__":
        socketio.run(app, host="0.0.0.0", port=10000)