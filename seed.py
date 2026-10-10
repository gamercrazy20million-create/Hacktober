"""Reset the database and load demo data for the hackathon."""
import os
from datetime import date, timedelta

from werkzeug.security import generate_password_hash

from app import DB_PATH, attendance_advisory, init_db

DEMO_PROF = ("prof", "prof123", "Abhishek")
DEMO_STUDENTS = [
    ("stu1", "pass123", "ARYAN", "CS-26-1"),
    ("stu2", "pass123", "MONDEEP", "CS-26-2"),
    ("stu3", "pass123", "VINAYAK", "CS-26-3"),
    ("stu4", "pass123", "ALOKH MISHRA", "CS-26-4"),
]


def seed():
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)
        print("Removed old attendance.db")
    init_db()

    import sqlite3
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row

    u, p, n = DEMO_PROF
    prof_id = db.execute(
        "INSERT INTO professors (username, password_hash, name) VALUES (?, ?, ?)",
        (u, generate_password_hash(p), n),
    ).lastrowid
    print(f"Professor: {u} / {p}  ({n})")

    student_ids = []
    for u, p, name, roll in DEMO_STUDENTS:
        sid = db.execute(
            "INSERT INTO students (username, password_hash, name, roll_no) VALUES (?, ?, ?, ?)",
            (u, generate_password_hash(p), name, roll),
        ).lastrowid
        student_ids.append(sid)
        print(f"Student:   {u} / {p}  ({name}, {roll})")

    subjects = [
        (prof_id, "Data Structures", "DS101", 75),
        (prof_id, "Database Systems", "DB201", 80),
    ]
    subject_ids = []
    for prof, name, code, target in subjects:
        sid = db.execute(
            "INSERT INTO subjects (prof_id, name, code, min_attendance_pct) VALUES (?, ?, ?, ?)",
            (prof, name, code, target),
        ).lastrowid
        subject_ids.append(sid)
        print(f"Subject:   {name} ({code}), target {target}%")

    for sid in student_ids:
        db.execute(
            "INSERT INTO enrollments (subject_id, student_id) VALUES (?, ?)",
            (subject_ids[0], sid),
        )
    for sid in (student_ids[0], student_ids[1], student_ids[3]):
        db.execute(
            "INSERT INTO enrollments (subject_id, student_id) VALUES (?, ?)",
            (subject_ids[1], sid),
        )

    def add_sessions(subject_id, n_classes, absent_fn):
        today = date.today()
        d = today
        made = 0
        while made < n_classes:
            d -= timedelta(days=1)
            if d.weekday() >= 5:
                continue
            cur = db.execute(
                "INSERT INTO attendance_sessions (subject_id, session_date) VALUES (?, ?)",
                (subject_id, d.isoformat()),
            )
            for i, st in enumerate(student_ids):
                status = "A" if absent_fn(made, i) else "P"
                db.execute(
                    "INSERT INTO attendance_records (session_id, student_id, status) VALUES (?, ?, ?)",
                    (cur.lastrowid, st, status),
                )
            made += 1

    # DS101: 12 classes — Rohan skips often, Sara misses a couple
    def ds_absent(class_idx, student_idx):
        if student_idx == 2:
            return class_idx % 2 == 0 or class_idx in (3, 7)
        if student_idx == 3:
            return class_idx in (2, 9)
        if student_idx == 0:
            return class_idx == 5
        return False

    # DB201: 8 classes — Diya is shaky
    def db_absent(class_idx, student_idx):
        if student_idx == 1:
            return class_idx % 2 == 1
        if student_idx == 3:
            return class_idx == 6
        return False

    add_sessions(subject_ids[0], 12, ds_absent)
    add_sessions(subject_ids[1], 8, db_absent)
    db.commit()

    print("\n--- Demo attendance summary ---")
    for subj_name, subj_id, target in (
        ("Data Structures", subject_ids[0], 75),
        ("Database Systems", subject_ids[1], 80),
    ):
        total = db.execute(
            "SELECT COUNT(*) c FROM attendance_sessions WHERE subject_id = ?", (subj_id,)
        ).fetchone()["c"]
        print(f"\n{subj_name} ({total} classes, target {target}%)")
        rows = db.execute(
            """
            SELECT st.name,
                   SUM(CASE WHEN ar.status = 'P' THEN 1 ELSE 0 END) AS attended
            FROM students st
            JOIN enrollments en ON en.student_id = st.id AND en.subject_id = ?
            LEFT JOIN attendance_records ar ON ar.student_id = st.id
                 AND ar.session_id IN (SELECT id FROM attendance_sessions WHERE subject_id = ?)
            GROUP BY st.id
            """,
            (subj_id, subj_id),
        ).fetchall()
        for r in rows:
            adv = attendance_advisory(r["attended"], total, target)
            if adv["status"] == "safe":
                msg = f"can miss {adv['can_miss']} more"
            elif adv["status"] == "below":
                msg = f"attend next {adv['classes_to_attend']} to reach {target}%"
            else:
                msg = adv["status"]
            print(f"  {r['name']:<14} {adv['pct']:>5.1f}%  ({adv['attended']}/{adv['total']})  -> {msg}")

    db.close()
    print("\nDone. Run: python app.py  then open http://127.0.0.1:5000")


if __name__ == "__main__":
    seed()
