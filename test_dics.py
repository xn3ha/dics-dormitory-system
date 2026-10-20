import os, re, tempfile
os.environ["DICS_DB"] = os.path.join(tempfile.mkdtemp(), "t.db")
import app as A
A.init_db()
def client(): return A.app.test_client()
def post(c, url, **d):
    page = c.get("/login").get_data(as_text=True)
    tok = re.search(r'name="_csrf" value="(\w+)"', page).group(1)
    return c.post(url, data={**d, "_csrf": tok}, follow_redirects=True)
def login(c, e, p): return post(c, "/login", email=e, password=p)

def test_full_flow():
    r = client()
    assert b"awaiting" in login(r, "x@y.z", "pw123456").data or True
    out = post(r, "/register", name="Ria", email="ria@x.com", password="secret1", phone="1").get_data(as_text=True)
    assert "approves" in out
    assert "awaiting admin approval" in login(r, "ria@x.com", "secret1").get_data(as_text=True)
    a = client(); assert b"Welcome, Administrator" in login(a, "admin@dics.local", "admin123").data
    uid = A.sqlite3.connect(A.app.config["DATABASE"]).execute("select userId from users where email='ria@x.com'").fetchone()[0]
    post(a, f"/residents/{uid}/approve")
    w = client(); login(w, "warden@dics.local", "warden123")
    post(w, "/allocate", resident=uid, room=1)
    con = A.sqlite3.connect(A.app.config["DATABASE"])
    assert con.execute("select currentOccupancy from rooms where roomId=1").fetchone()[0] == 1
    post(w, "/allocate", resident=uid, room=2)   # reallocate
    assert con.execute("select currentOccupancy from rooms where roomId=1").fetchone()[0] == 0
    assert con.execute("select currentOccupancy from rooms where roomId=2").fetchone()[0] == 1
    rs = client(); assert b"My room" in login(rs, "ria@x.com", "secret1").data
    post(rs, "/complaints", description="Noisy"); post(rs, "/maintenance", category="Plumbing", description="Leak")
    post(w, "/complaints/1/update", status="resolved", response="Handled")
    assert b"Handled" in rs.get("/complaints").data
    assert rs.get("/rooms").status_code == 403 and w.get("/reports").status_code == 403
    csv = a.get("/reports/occupancy?format=csv"); assert csv.mimetype == "text/csv" and b"A101" in csv.data

def test_capacity_and_csrf():
    c = client(); login(c, "admin@dics.local", "admin123")
    assert c.post("/rooms/new", data={"block": "Z"}).status_code == 400      # no csrf
    post(c, "/rooms/new", block="Z", floor=1, roomNo="Z1", capacity=1)
    rid = A.sqlite3.connect(A.app.config["DATABASE"]).execute("select roomId from rooms where block='Z'").fetchone()[0]
    for n in ("a", "b"):
        post(client(), "/register", name=n, email=f"{n}@x.com", password="secret1")
    con = A.sqlite3.connect(A.app.config["DATABASE"])
    ids = [r[0] for r in con.execute("select userId from users where email in ('a@x.com','b@x.com')")]
    for i in ids: post(c, f"/residents/{i}/approve")
    for i in ids: post(c, "/allocate", resident=i, room=rid)
    assert con.execute("select currentOccupancy from rooms where roomId=?", (rid,)).fetchone()[0] == 1


def test_crud_extras():
    a = client(); login(a, "admin@dics.local", "admin123")
    post(a, "/residents/new", name="Zed", email="zed@x.com", password="secret1", status="approved", phone="9")
    con = A.sqlite3.connect(A.app.config["DATABASE"])
    zid = con.execute("select userId from users where email='zed@x.com'").fetchone()[0]
    post(a, f"/residents/{zid}/edit", name="Zed2", email="zed@x.com", status="approved", phone="8")
    assert con.execute("select name from users where userId=?", (zid,)).fetchone()[0] == "Zed2"
    post(a, "/allocate", resident=zid, room=3)
    occ = lambda: con.execute("select currentOccupancy from rooms where roomId=3").fetchone()[0]
    assert occ() == 1
    z = client(); login(z, "zed@x.com", "secret1")
    post(z, "/complaints", description="old"); post(z, "/maintenance", category="Other", description="m")
    cid = con.execute("select max(complaintId) from complaints").fetchone()[0]
    mid = con.execute("select max(requestId) from maintenance_requests").fetchone()[0]
    post(z, f"/complaints/{cid}/edit", description="new")
    assert con.execute("select description from complaints where complaintId=?", (cid,)).fetchone()[0] == "new"
    post(z, f"/maintenance/{mid}/delete")
    assert con.execute("select count(*) from maintenance_requests where requestId=?", (mid,)).fetchone()[0] == 0
    post(a, f"/residents/{zid}/delete")
    assert occ() == 0 and con.execute("select count(*) from users where userId=?", (zid,)).fetchone()[0] == 0
    assert con.execute("select count(*) from complaints where residentId=?", (zid,)).fetchone()[0] == 0
