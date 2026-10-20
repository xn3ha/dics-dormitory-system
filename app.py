"""Dormitory Information and Control System (DICS).

Layers follow the SRS component diagram:
  Presentation  -> templates/ (Jinja UI)
  Business logic-> route handlers below (auth, residents, rooms, complaints,
                   maintenance, reports)
  Data          -> SQLite database (dics.db)
"""
import csv
import io
import os
import secrets
import sqlite3
from datetime import date
from functools import wraps

from markupsafe import Markup
from flask import (Flask, Response, abort, flash, g, redirect,
                   render_template, request, session, url_for)
from werkzeug.security import check_password_hash, generate_password_hash

BASE = os.path.dirname(os.path.abspath(__file__))
app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("DICS_SECRET", "dev-change-me")
app.config["DATABASE"] = os.environ.get("DICS_DB", os.path.join(BASE, "dics.db"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS users(
  userId INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL, email TEXT NOT NULL UNIQUE, passwordHash TEXT NOT NULL,
  role TEXT NOT NULL CHECK(role IN ('admin','warden','resident')),
  registrationStatus TEXT NOT NULL DEFAULT 'approved'
    CHECK(registrationStatus IN ('pending','approved','rejected')),
  allocatedRoomId INTEGER REFERENCES rooms(roomId),
  phone TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS rooms(
  roomId INTEGER PRIMARY KEY AUTOINCREMENT,
  block TEXT NOT NULL, floor INTEGER NOT NULL, roomNo TEXT NOT NULL,
  capacity INTEGER NOT NULL CHECK(capacity > 0),
  currentOccupancy INTEGER NOT NULL DEFAULT 0, UNIQUE(block, roomNo));
CREATE TABLE IF NOT EXISTS complaints(
  complaintId INTEGER PRIMARY KEY AUTOINCREMENT,
  residentId INTEGER NOT NULL REFERENCES users(userId),
  description TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'open',
  response TEXT DEFAULT '', createdDate TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS maintenance_requests(
  requestId INTEGER PRIMARY KEY AUTOINCREMENT,
  residentId INTEGER NOT NULL REFERENCES users(userId),
  category TEXT NOT NULL, description TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'open', createdDate TEXT NOT NULL);
"""
COMPLAINT_STATUS = ["open", "in progress", "resolved"]
MAINT_STATUS = ["open", "in progress", "completed"]
CATEGORIES = ["Electrical", "Plumbing", "Furniture", "Cleaning", "Internet", "Other"]


# ---------------------------------------------------------------- data layer
def db():
    if "db" not in g:
        g.db = sqlite3.connect(app.config["DATABASE"])
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys=ON")
    return g.db


@app.teardown_appcontext
def close_db(_):
    d = g.pop("db", None)
    if d:
        d.close()


def init_db(seed=True):
    con = sqlite3.connect(app.config["DATABASE"])
    con.executescript(SCHEMA)
    if seed and not con.execute("SELECT 1 FROM users").fetchone():
        for n, e, p, r in [("Administrator", "admin@dics.local", "admin123", "admin"),
                           ("Warden", "warden@dics.local", "warden123", "warden")]:
            con.execute("INSERT INTO users(name,email,passwordHash,role) VALUES(?,?,?,?)",
                        (n, e, generate_password_hash(p), r))
        for blk in "AB":
            for fl in (1, 2):
                for k in (1, 2, 3):
                    con.execute("INSERT INTO rooms(block,floor,roomNo,capacity) VALUES(?,?,?,?)",
                                (blk, fl, f"{blk}{fl}0{k}", 2 if k < 3 else 3))
    con.commit()
    con.close()


# ------------------------------------------------------------- auth / access
def current_user():
    if "uid" not in session:
        return None
    return db().execute("SELECT * FROM users WHERE userId=?", (session["uid"],)).fetchone()


def login_required(*roles):
    def deco(f):
        @wraps(f)
        def wrap(*a, **kw):
            u = current_user()
            if not u:
                return redirect(url_for("login"))
            if roles and u["role"] not in roles:
                abort(403)
            g.user = u
            return f(*a, **kw)
        return wrap
    return deco


@app.before_request
def csrf_protect():
    if request.method == "POST":
        if not secrets.compare_digest(request.form.get("_csrf", ""), session.get("_csrf", "-")):
            abort(400, "Invalid CSRF token")


@app.context_processor
def inject():
    session.setdefault("_csrf", secrets.token_hex(16))
    return dict(csrf=session["_csrf"], me=current_user(),
                tok=Markup(f'<input type="hidden" name="_csrf" value="{session["_csrf"]}">'))


@app.errorhandler(403)
def forbidden(_):
    return render_template("error.html", code=403, msg="You don't have access to this page."), 403


# --------------------------------------------------------------- authenticate
@app.route("/")
def index():
    u = current_user()
    return redirect(url_for("dashboard") if u else url_for("login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form["email"].strip().lower()
        u = db().execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
        if not u or not check_password_hash(u["passwordHash"], request.form["password"]):
            flash("Invalid email or password.", "error")
        elif u["registrationStatus"] == "pending":
            flash("Your registration is awaiting admin approval.", "error")
        elif u["registrationStatus"] == "rejected":
            flash("Your registration was rejected. Contact the administrator.", "error")
        else:
            session.clear()
            session["uid"] = u["userId"]
            return redirect(url_for("dashboard"))
    return render_template("login.html")


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        f = request.form
        email = f["email"].strip().lower()
        if len(f["password"]) < 6:
            flash("Password must be at least 6 characters.", "error")
        elif db().execute("SELECT 1 FROM users WHERE email=?", (email,)).fetchone():
            flash("That email is already registered.", "error")
        else:
            db().execute("INSERT INTO users(name,email,passwordHash,role,registrationStatus,phone)"
                         " VALUES(?,?,?,?,?,?)",
                         (f["name"].strip(), email, generate_password_hash(f["password"]),
                          "resident", "pending", f.get("phone", "").strip()))
            db().commit()
            flash("Registered. You can log in once the admin approves you.", "ok")
            return redirect(url_for("login"))
    return render_template("register.html")


@app.route("/profile", methods=["GET", "POST"])
@login_required()
def profile():
    if request.method == "POST":
        f = request.form
        db().execute("UPDATE users SET name=?, phone=? WHERE userId=?",
                     (f["name"].strip(), f.get("phone", "").strip(), g.user["userId"]))
        if f.get("password"):
            if len(f["password"]) < 6:
                flash("Password must be at least 6 characters.", "error")
                return redirect(url_for("profile"))
            db().execute("UPDATE users SET passwordHash=? WHERE userId=?",
                         (generate_password_hash(f["password"]), g.user["userId"]))
        db().commit()
        flash("Profile updated.", "ok")
        return redirect(url_for("profile"))
    return render_template("profile.html")


# ------------------------------------------------------------------ dashboard
@app.route("/dashboard")
@login_required()
def dashboard():
    d, u = db(), g.user
    stats = {}
    if u["role"] in ("admin", "warden"):
        stats = dict(
            rooms=d.execute("SELECT COUNT(*) FROM rooms").fetchone()[0],
            beds=d.execute("SELECT COALESCE(SUM(capacity),0) FROM rooms").fetchone()[0],
            occupied=d.execute("SELECT COALESCE(SUM(currentOccupancy),0) FROM rooms").fetchone()[0],
            pending=d.execute("SELECT COUNT(*) FROM users WHERE registrationStatus='pending'").fetchone()[0],
            complaints=d.execute("SELECT COUNT(*) FROM complaints WHERE status!='resolved'").fetchone()[0],
            maint=d.execute("SELECT COUNT(*) FROM maintenance_requests WHERE status!='completed'").fetchone()[0])
    room = None
    if u["role"] == "resident" and u["allocatedRoomId"]:
        room = d.execute("SELECT * FROM rooms WHERE roomId=?", (u["allocatedRoomId"],)).fetchone()
    return render_template("dashboard.html", stats=stats, room=room)


# --------------------------------------------------- admin: resident approval
@app.route("/residents")
@login_required("admin", "warden")
def residents():
    rows = db().execute(
        "SELECT u.*, r.block, r.roomNo FROM users u LEFT JOIN rooms r ON r.roomId=u.allocatedRoomId"
        " WHERE u.role='resident' ORDER BY CASE u.registrationStatus WHEN 'pending' THEN 0 ELSE 1 END, u.name"
    ).fetchall()
    return render_template("residents.html", rows=rows)


@app.post("/residents/<int:uid>/<action>")
@login_required("admin")
def resident_decision(uid, action):
    if action not in ("approve", "reject"):
        abort(404)
    status = "approved" if action == "approve" else "rejected"
    d = db()
    r = d.execute("SELECT * FROM users WHERE userId=? AND role='resident'", (uid,)).fetchone()
    if not r:
        abort(404)
    if status == "rejected" and r["allocatedRoomId"]:
        d.execute("UPDATE rooms SET currentOccupancy=currentOccupancy-1 WHERE roomId=?", (r["allocatedRoomId"],))
        d.execute("UPDATE users SET allocatedRoomId=NULL WHERE userId=?", (uid,))
    d.execute("UPDATE users SET registrationStatus=? WHERE userId=?", (status, uid))
    d.commit()
    flash(f"{r['name']} {status}.", "ok")
    return redirect(url_for("residents"))



@app.route("/residents/new", methods=["GET", "POST"])
@app.route("/residents/<int:uid>/edit", methods=["GET", "POST"])
@login_required("admin", "warden")
def resident_form(uid=None):
    d = db()
    res = d.execute("SELECT * FROM users WHERE userId=? AND role='resident'", (uid,)).fetchone() if uid else None
    if uid and not res:
        abort(404)
    if request.method == "POST":
        f = request.form
        name, email = f.get("name", "").strip(), f.get("email", "").strip().lower()
        status = f.get("status", "approved")
        pw = f.get("password", "")
        dup = d.execute("SELECT userId FROM users WHERE email=?", (email,)).fetchone()
        if not name or not email or status not in ("pending", "approved", "rejected"):
            flash("Name and email are required.", "error")
        elif dup and dup["userId"] != uid:
            flash("That email is already in use.", "error")
        elif (not res and len(pw) < 6) or (pw and len(pw) < 6):
            flash("Password must be at least 6 characters.", "error")
        else:
            if res:
                d.execute("UPDATE users SET name=?,email=?,phone=?,registrationStatus=? WHERE userId=?",
                          (name, email, f.get("phone", "").strip(), status, uid))
                if pw:
                    d.execute("UPDATE users SET passwordHash=? WHERE userId=?", (generate_password_hash(pw), uid))
                if status == "rejected" and res["allocatedRoomId"]:
                    d.execute("UPDATE rooms SET currentOccupancy=currentOccupancy-1 WHERE roomId=?", (res["allocatedRoomId"],))
                    d.execute("UPDATE users SET allocatedRoomId=NULL WHERE userId=?", (uid,))
            else:
                d.execute("INSERT INTO users(name,email,passwordHash,role,registrationStatus,phone) VALUES(?,?,?,?,?,?)",
                          (name, email, generate_password_hash(pw), "resident", status, f.get("phone", "").strip()))
            d.commit()
            flash("Resident saved.", "ok")
            return redirect(url_for("residents"))
    return render_template("resident_form.html", res=res)


@app.post("/residents/<int:uid>/delete")
@login_required("admin")
def resident_delete(uid):
    d = db()
    r = d.execute("SELECT * FROM users WHERE userId=? AND role='resident'", (uid,)).fetchone()
    if not r:
        abort(404)
    if r["allocatedRoomId"]:
        d.execute("UPDATE rooms SET currentOccupancy=currentOccupancy-1 WHERE roomId=?", (r["allocatedRoomId"],))
    d.execute("DELETE FROM complaints WHERE residentId=?", (uid,))
    d.execute("DELETE FROM maintenance_requests WHERE residentId=?", (uid,))
    d.execute("DELETE FROM users WHERE userId=?", (uid,))
    d.commit()
    flash(f"{r['name']} and their records were deleted.", "ok")
    return redirect(url_for("residents"))


@app.post("/residents/<int:uid>/unassign")
@login_required("admin", "warden")
def resident_unassign(uid):
    d = db()
    r = d.execute("SELECT * FROM users WHERE userId=? AND role='resident'", (uid,)).fetchone()
    if not r:
        abort(404)
    if r["allocatedRoomId"]:
        d.execute("UPDATE rooms SET currentOccupancy=currentOccupancy-1 WHERE roomId=?", (r["allocatedRoomId"],))
        d.execute("UPDATE users SET allocatedRoomId=NULL WHERE userId=?", (uid,))
        d.commit()
        flash("Resident removed from their room.", "ok")
    return redirect(url_for("residents"))

# ----------------------------------------------------------------------- rooms
@app.route("/rooms")
@login_required("admin", "warden")
def rooms():
    rows = db().execute("SELECT * FROM rooms ORDER BY block, roomNo").fetchall()
    return render_template("rooms.html", rows=rows)


def _room_form(f):
    try:
        return f["block"].strip().upper(), int(f["floor"]), f["roomNo"].strip().upper(), int(f["capacity"])
    except (KeyError, ValueError):
        return None


@app.route("/rooms/new", methods=["GET", "POST"])
@app.route("/rooms/<int:rid>/edit", methods=["GET", "POST"])
@login_required("admin")
def room_form(rid=None):
    d = db()
    room = d.execute("SELECT * FROM rooms WHERE roomId=?", (rid,)).fetchone() if rid else None
    if rid and not room:
        abort(404)
    if request.method == "POST":
        v = _room_form(request.form)
        if not v or v[3] < 1 or not v[0] or not v[2]:
            flash("Fill in all fields; capacity must be at least 1.", "error")
        elif room and v[3] < room["currentOccupancy"]:
            flash("Capacity can't be lower than current occupancy.", "error")
        else:
            try:
                if room:
                    d.execute("UPDATE rooms SET block=?,floor=?,roomNo=?,capacity=? WHERE roomId=?", (*v, rid))
                else:
                    d.execute("INSERT INTO rooms(block,floor,roomNo,capacity) VALUES(?,?,?,?)", v)
                d.commit()
                flash("Room saved.", "ok")
                return redirect(url_for("rooms"))
            except sqlite3.IntegrityError:
                flash("That block/room number already exists.", "error")
    return render_template("room_form.html", room=room)


@app.post("/rooms/<int:rid>/delete")
@login_required("admin")
def room_delete(rid):
    d = db()
    r = d.execute("SELECT * FROM rooms WHERE roomId=?", (rid,)).fetchone()
    if not r:
        abort(404)
    if r["currentOccupancy"] > 0:
        flash("Can't delete a room that still has residents.", "error")
    else:
        d.execute("DELETE FROM rooms WHERE roomId=?", (rid,))
        d.commit()
        flash("Room deleted.", "ok")
    return redirect(url_for("rooms"))


# --------------------------------------------------- warden: allocate/reallocate
@app.route("/allocate", methods=["GET", "POST"])
@login_required("warden", "admin")
def allocate():
    d = db()
    if request.method == "POST":
        try:
            uid, rid = int(request.form["resident"]), int(request.form["room"])
        except (KeyError, ValueError):
            abort(400)
        res = d.execute("SELECT * FROM users WHERE userId=? AND role='resident' AND registrationStatus='approved'",
                        (uid,)).fetchone()
        room = d.execute("SELECT * FROM rooms WHERE roomId=?", (rid,)).fetchone()
        if not res or not room:
            abort(404)
        if res["allocatedRoomId"] == rid:
            flash("Resident is already in that room.", "error")
        elif room["currentOccupancy"] >= room["capacity"]:       # checkAvailability()
            flash(f"Room {room['roomNo']} is full.", "error")
        else:
            if res["allocatedRoomId"]:                           # reallocateRoom: decrementOccupancy
                d.execute("UPDATE rooms SET currentOccupancy=currentOccupancy-1 WHERE roomId=?",
                          (res["allocatedRoomId"],))
            d.execute("UPDATE rooms SET currentOccupancy=currentOccupancy+1 WHERE roomId=?", (rid,))
            d.execute("UPDATE users SET allocatedRoomId=? WHERE userId=?", (rid, uid))
            d.commit()
            flash(f"{res['name']} assigned to {room['block']}-{room['roomNo']}.", "ok")
        return redirect(url_for("allocate"))
    residents_ = d.execute("SELECT u.*, r.block, r.roomNo FROM users u LEFT JOIN rooms r ON r.roomId=u.allocatedRoomId"
                           " WHERE u.role='resident' AND u.registrationStatus='approved' ORDER BY u.name").fetchall()
    avail = d.execute("SELECT * FROM rooms WHERE currentOccupancy<capacity ORDER BY block, roomNo").fetchall()
    return render_template("allocate.html", residents=residents_, rooms=avail)


# ------------------------------------------------------------------ complaints
@app.route("/complaints", methods=["GET", "POST"])
@login_required()
def complaints():
    d, u = db(), g.user
    if request.method == "POST":
        if u["role"] != "resident":
            abort(403)
        desc = request.form.get("description", "").strip()
        if desc:
            d.execute("INSERT INTO complaints(residentId,description,createdDate) VALUES(?,?,?)",
                      (u["userId"], desc, date.today().isoformat()))
            d.commit()
            flash("Complaint submitted.", "ok")
        return redirect(url_for("complaints"))
    q = ("SELECT c.*, u.name FROM complaints c JOIN users u ON u.userId=c.residentId")
    rows = (d.execute(q + " WHERE c.residentId=? ORDER BY c.complaintId DESC", (u["userId"],))
            if u["role"] == "resident" else d.execute(q + " ORDER BY c.complaintId DESC")).fetchall()
    return render_template("complaints.html", rows=rows, statuses=COMPLAINT_STATUS)


@app.post("/complaints/<int:cid>/update")
@login_required("warden", "admin")
def complaint_update(cid):
    st = request.form.get("status")
    if st not in COMPLAINT_STATUS:
        abort(400)
    db().execute("UPDATE complaints SET status=?, response=? WHERE complaintId=?",
                 (st, request.form.get("response", "").strip(), cid))
    db().commit()
    flash("Complaint updated.", "ok")
    return redirect(url_for("complaints"))


# ----------------------------------------------------------------- maintenance
@app.route("/maintenance", methods=["GET", "POST"])
@login_required()
def maintenance():
    d, u = db(), g.user
    if request.method == "POST":
        if u["role"] != "resident":
            abort(403)
        cat, desc = request.form.get("category"), request.form.get("description", "").strip()
        if cat in CATEGORIES and desc:
            d.execute("INSERT INTO maintenance_requests(residentId,category,description,createdDate) VALUES(?,?,?,?)",
                      (u["userId"], cat, desc, date.today().isoformat()))
            d.commit()
            flash("Maintenance request submitted.", "ok")
        return redirect(url_for("maintenance"))
    q = "SELECT m.*, u.name FROM maintenance_requests m JOIN users u ON u.userId=m.residentId"
    rows = (d.execute(q + " WHERE m.residentId=? ORDER BY m.requestId DESC", (u["userId"],))
            if u["role"] == "resident" else d.execute(q + " ORDER BY m.requestId DESC")).fetchall()
    return render_template("maintenance.html", rows=rows, statuses=MAINT_STATUS, categories=CATEGORIES)


@app.post("/maintenance/<int:rid>/update")
@login_required("warden", "admin")
def maintenance_update(rid):
    st = request.form.get("status")
    if st not in MAINT_STATUS:
        abort(400)
    db().execute("UPDATE maintenance_requests SET status=? WHERE requestId=?", (st, rid))
    db().commit()
    flash("Request updated.", "ok")
    return redirect(url_for("maintenance"))



def _own_open(table, pk, item_id):
    """Row the current user may edit/delete: staff any, resident only own and still open."""
    row = db().execute(f"SELECT * FROM {table} WHERE {pk}=?", (item_id,)).fetchone()
    if not row:
        abort(404)
    if g.user["role"] == "resident" and (row["residentId"] != g.user["userId"] or row["status"] != "open"):
        abort(403)
    return row


@app.post("/complaints/<int:cid>/edit")
@login_required()
def complaint_edit(cid):
    _own_open("complaints", "complaintId", cid)
    desc = request.form.get("description", "").strip()
    if desc:
        db().execute("UPDATE complaints SET description=? WHERE complaintId=?", (desc, cid))
        db().commit()
        flash("Complaint edited.", "ok")
    return redirect(url_for("complaints"))


@app.post("/complaints/<int:cid>/delete")
@login_required()
def complaint_delete(cid):
    _own_open("complaints", "complaintId", cid)
    db().execute("DELETE FROM complaints WHERE complaintId=?", (cid,))
    db().commit()
    flash("Complaint deleted.", "ok")
    return redirect(url_for("complaints"))


@app.post("/maintenance/<int:rid>/edit")
@login_required()
def maintenance_edit(rid):
    _own_open("maintenance_requests", "requestId", rid)
    cat, desc = request.form.get("category"), request.form.get("description", "").strip()
    if cat in CATEGORIES and desc:
        db().execute("UPDATE maintenance_requests SET category=?, description=? WHERE requestId=?", (cat, desc, rid))
        db().commit()
        flash("Request edited.", "ok")
    return redirect(url_for("maintenance"))


@app.post("/maintenance/<int:rid>/delete")
@login_required()
def maintenance_delete(rid):
    _own_open("maintenance_requests", "requestId", rid)
    db().execute("DELETE FROM maintenance_requests WHERE requestId=?", (rid,))
    db().commit()
    flash("Request deleted.", "ok")
    return redirect(url_for("maintenance"))

# --------------------------------------------------------------------- reports
REPORTS = {
    "occupancy": ("Room occupancy",
                  "SELECT block, roomNo, floor, capacity, currentOccupancy,"
                  " capacity-currentOccupancy AS vacant FROM rooms ORDER BY block, roomNo"),
    "residents": ("Resident list",
                  "SELECT u.name, u.email, u.phone, u.registrationStatus AS status,"
                  " COALESCE(r.block||'-'||r.roomNo,'—') AS room FROM users u"
                  " LEFT JOIN rooms r ON r.roomId=u.allocatedRoomId WHERE u.role='resident' ORDER BY u.name"),
    "complaints": ("Complaints",
                   "SELECT c.complaintId AS id, u.name, c.description, c.status, c.createdDate AS date"
                   " FROM complaints c JOIN users u ON u.userId=c.residentId ORDER BY c.complaintId"),
    "maintenance": ("Maintenance requests",
                    "SELECT m.requestId AS id, u.name, m.category, m.description, m.status, m.createdDate AS date"
                    " FROM maintenance_requests m JOIN users u ON u.userId=m.residentId ORDER BY m.requestId"),
}


@app.route("/reports")
@login_required("admin")
def reports():
    return render_template("reports.html", reports=REPORTS)


@app.route("/reports/<kind>")
@login_required("admin")
def report(kind):
    if kind not in REPORTS:
        abort(404)
    title, sql = REPORTS[kind]
    cur = db().execute(sql)
    rows, cols = cur.fetchall(), [c[0] for c in cur.description]
    if request.args.get("format") == "csv":
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(cols)
        w.writerows([list(r) for r in rows])
        return Response(buf.getvalue(), mimetype="text/csv",
                        headers={"Content-Disposition": f"attachment; filename={kind}-{date.today()}.csv"})
    return render_template("report.html", title=title, kind=kind, cols=cols, rows=rows)


if __name__ == "__main__":
    init_db()
    app.run(debug=os.environ.get("DICS_DEBUG") == "1", port=int(os.environ.get("PORT", 5000)))
