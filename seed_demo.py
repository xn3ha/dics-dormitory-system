"""Fill DICS with sample residents, allocations, complaints and maintenance requests.
Run once:  python seed_demo.py     (all demo residents use password 'resident123')"""
import sqlite3
from datetime import date, timedelta
from werkzeug.security import generate_password_hash
import app as A

A.init_db()
con = sqlite3.connect(A.app.config["DATABASE"])
if con.execute("SELECT 1 FROM users WHERE role='resident'").fetchone():
    raise SystemExit("Residents already exist - demo data not added.")

pw = generate_password_hash("resident123")
people = [("Aarav Sharma", "aarav@demo.com", "approved"), ("Diya Patel", "diya@demo.com", "approved"),
          ("Rohan Mehta", "rohan@demo.com", "approved"), ("Ananya Iyer", "ananya@demo.com", "approved"),
          ("Kabir Singh", "kabir@demo.com", "approved"), ("Meera Nair", "meera@demo.com", "approved"),
          ("Vihaan Gupta", "vihaan@demo.com", "pending"), ("Isha Reddy", "isha@demo.com", "pending"),
          ("Arjun Das", "arjun@demo.com", "rejected")]
ids = {}
for i, (n, e, s) in enumerate(people):
    cur = con.execute("INSERT INTO users(name,email,passwordHash,role,registrationStatus,phone) VALUES(?,?,?,?,?,?)",
                      (n, e, pw, "resident", s, f"98765432{i:02d}"))
    ids[e] = cur.lastrowid
# (resident, roomId): rooms 1-3 are block A floor 1, 4-6 block A floor 2
for e, rid in [("aarav@demo.com", 1), ("diya@demo.com", 1), ("rohan@demo.com", 2),
               ("ananya@demo.com", 4), ("kabir@demo.com", 4), ("meera@demo.com", 5)]:
    con.execute("UPDATE users SET allocatedRoomId=? WHERE userId=?", (rid, ids[e]))
    con.execute("UPDATE rooms SET currentOccupancy=currentOccupancy+1 WHERE roomId=?", (rid,))
day = lambda n: (date.today() - timedelta(days=n)).isoformat()
for e, d, st, resp, n in [("aarav@demo.com", "Noise from the corridor after midnight.", "open", "", 1),
                          ("diya@demo.com", "Hot water is unavailable on the first floor.", "in progress", "Plumber has been informed.", 3),
                          ("rohan@demo.com", "Mess food quality has dropped this week.", "resolved", "Raised with the mess contractor.", 7)]:
    con.execute("INSERT INTO complaints(residentId,description,status,response,createdDate) VALUES(?,?,?,?,?)",
                (ids[e], d, st, resp, day(n)))
for e, c, d, st, n in [("ananya@demo.com", "Electrical", "Ceiling fan makes a loud noise.", "open", 2),
                       ("kabir@demo.com", "Plumbing", "Bathroom tap is leaking.", "in progress", 4),
                       ("meera@demo.com", "Internet", "Wi-Fi keeps disconnecting.", "completed", 6),
                       ("aarav@demo.com", "Furniture", "Study table drawer is broken.", "open", 1)]:
    con.execute("INSERT INTO maintenance_requests(residentId,category,description,status,createdDate) VALUES(?,?,?,?,?)",
                (ids[e], c, d, st, day(n)))
con.commit()
print("Demo data added. Resident login example: aarav@demo.com / resident123")
