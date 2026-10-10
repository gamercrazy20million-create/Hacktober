import math
import os
import secrets
import sqlite3
from datetime import date
from functools import wraps

from flask import (Flask, flash, g, redirect, render_template, request,
                   session, url_for)
from werkzeug.security import check_password_hash, generate_password_hash

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
DB_PATH = os.path.join(BASE_DIR, "attendance.db")

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "hackathon-demo-secret-change-me")

SCHEMA = """
CREATE TABLE IF NOT EXISTS professors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    name TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS students (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    name TEXT NOT NULL,
    roll_no TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS subjects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    prof_id INTEGER NOT NULL REFERENCES professors(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    code TEXT UNIQUE NOT NULL,
    min_attendance_pct REAL NOT NULL DEFAULT 75
);
CREATE TABLE IF NOT EXISTS enrollments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    subject_id INTEGER NOT NULL REFERENCES subjects(id) ON DELETE CASCADE,
    student_id INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
    UNIQUE(subject_id, student_id)
);
CREATE TABLE IF NOT EXISTS attendance_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    subject_id INTEGER NOT NULL REFERENCES subjects(id) ON DELETE CASCADE,
    session_date TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(subject_id, session_date)
);
CREATE TABLE IF NOT EXISTS attendance_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL REFERENCES attendance_sessions(id) ON DELETE CASCADE,
    student_id INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
    status TEXT NOT NULL CHECK(status IN ('P', 'A')),
    UNIQUE(session_id, student_id)
);
"""


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


@app.teardown_appcontext
def close_db(exception):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(SCHEMA)
    conn.commit()
    conn.close()


def login_required(role):
    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if session.get("role") != role:
                flash("Please log in first.", "error")
                return redirect(url_for("prof_login" if role == "prof" else "student_login"))
            return view(*args, **kwargs)
        return wrapped
    return decorator


def attendance_advisory(attended, total, target):
    """Compute attendance % and what the student must do to hit target."""
    data = {
        "attended": attended,
        "total": total,
        "absent": total - attended,
        "target": target,
        "pct": round(attended / total * 100, 1) if total else 0.0,
        "status": "no_classes",
        "classes_to_attend": None,
        "can_miss": None,
    }
    if total == 0:
        return data
    pct = attended / total * 100
    if pct >= target:
        data["status"] = "safe"
        data["can_miss"] = max(0, math.floor((100 * attended - target * total) / target))
    elif attended == 0:
        data["status"] = "critical"
        data["classes_to_attend"] = 1
    else:
        data["status"] = "below"
        if target < 100:
            n = math.ceil((target * total - 100 * attended) / (100 - target))
            data["classes_to_attend"] = max(1, n)
    return data


def total_sessions(db, subject_id):
    return db.execute(
        "SELECT COUNT(*) AS c FROM attendance_sessions WHERE subject_id = ?",
        (subject_id,),
    ).fetchone()["c"]


def build_student_stats(db, subject, student_id=None):
    params = [subject["id"], subject["id"]]
    filter_sql = ""
    if student_id is not None:
        filter_sql = " AND st.id = ?"
        params.append(student_id)
    rows = db.execute(
        f"""
        SELECT st.id, st.name, st.roll_no, st.username,
               COALESCE(SUM(CASE WHEN ar.status = 'P' THEN 1 ELSE 0 END), 0) AS attended
        FROM students st
        JOIN enrollments en ON en.student_id = st.id AND en.subject_id = ?
        LEFT JOIN attendance_records ar ON ar.student_id = st.id
             AND ar.session_id IN (SELECT id FROM attendance_sessions WHERE subject_id = ?)
        WHERE 1 = 1 {filter_sql}
        GROUP BY st.id
        ORDER BY st.name COLLATE NOCASE
        """,
        tuple(params),
    ).fetchall()
    total = total_sessions(db, subject["id"])
    target = subject["min_attendance_pct"]
    return [{**dict(r), **attendance_advisory(r["attended"], total, target)} for r in rows]


def get_own_subject(subject_id):
    return get_db().execute(
        "SELECT * FROM subjects WHERE id = ? AND prof_id = ?",
        (subject_id, session["user_id"]),
    ).fetchone()


# ---------------------------------------------------------------------------
# Landing / auth
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/prof/login", methods=["GET", "POST"])
def prof_login():
    if request.method == "POST":
        username = request.form.get("username", "").strip().lower()
        password = request.form.get("password", "")
        row = get_db().execute(
            "SELECT * FROM professors WHERE username = ?", (username,)
        ).fetchone()
        if row and check_password_hash(row["password_hash"], password):
            session.clear()
            session["role"] = "prof"
            session["user_id"] = row["id"]
            session["name"] = row["name"]
            flash(f"Welcome back, {row['name']}!", "success")
            return redirect(url_for("prof_dashboard"))
        flash("Invalid username or password.", "error")
    return render_template("login.html", role="prof")


@app.route("/prof/register", methods=["GET", "POST"])
def prof_register():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        username = request.form.get("username", "").strip().lower()
        password = request.form.get("password", "")
        if not name or not username or len(password) < 4:
            flash("All fields required (password min 4 characters).", "error")
        else:
            db = get_db()
            try:
                cur = db.execute(
                    "INSERT INTO professors (username, password_hash, name) VALUES (?, ?, ?)",
                    (username, generate_password_hash(password), name),
                )
                db.commit()
                session.clear()
                session["role"] = "prof"
                session["user_id"] = cur.lastrowid
                session["name"] = name
                flash("Professor account created!", "success")
                return redirect(url_for("prof_dashboard"))
            except sqlite3.IntegrityError:
                flash("That username is already taken.", "error")
    return render_template("register.html", role="prof")


@app.route("/student/login", methods=["GET", "POST"])
def student_login():
    if request.method == "POST":
        username = request.form.get("username", "").strip().lower()
        password = request.form.get("password", "")
        row = get_db().execute(
            "SELECT * FROM students WHERE username = ?", (username,)
        ).fetchone()
        if row and check_password_hash(row["password_hash"], password):
            session.clear()
            session["role"] = "student"
            session["user_id"] = row["id"]
            session["name"] = row["name"]
            flash(f"Welcome back, {row['name']}!", "success")
            return redirect(url_for("student_dashboard"))
        flash("Invalid username or password.", "error")
    return render_template("login.html", role="student")


@app.route("/student/register", methods=["GET", "POST"])
def student_register():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        roll_no = request.form.get("roll_no", "").strip()
        username = request.form.get("username", "").strip().lower()
        password = request.form.get("password", "")
        if not name or not roll_no or not username or len(password) < 4:
            flash("All fields required (password min 4 characters).", "error")
        else:
            db = get_db()
            try:
                cur = db.execute(
                    "INSERT INTO students (username, password_hash, name, roll_no) VALUES (?, ?, ?, ?)",
                    (username, generate_password_hash(password), name, roll_no),
                )
                db.commit()
                session.clear()
                session["role"] = "student"
                session["user_id"] = cur.lastrowid
                session["name"] = name
                flash("Student account created!", "success")
                return redirect(url_for("student_dashboard"))
            except sqlite3.IntegrityError:
                flash("That username is already taken.", "error")
    return render_template("register.html", role="student")


@app.route("/logout")
def logout():
    session.clear()
    flash("Logged out.", "info")
    return redirect(url_for("index"))


# ---------------------------------------------------------------------------
# Professor routes
# ---------------------------------------------------------------------------

@app.route("/prof/dashboard")
@login_required("prof")
def prof_dashboard():
    subjects = get_db().execute(
        """
        SELECT s.*,
               (SELECT COUNT(*) FROM enrollments e WHERE e.subject_id = s.id) AS student_count,
               (SELECT COUNT(*) FROM attendance_sessions t WHERE t.subject_id = s.id) AS session_count
        FROM subjects s
        WHERE s.prof_id = ?
        ORDER BY s.name COLLATE NOCASE
        """,
        (session["user_id"],),
    ).fetchall()
    return render_template("prof_dashboard.html", subjects=subjects)


@app.route("/prof/subject/new", methods=["GET", "POST"])
@login_required("prof")
def subject_new():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        code = request.form.get("code", "").strip().upper()
        try:
            target = float(request.form.get("min_pct", "75"))
        except ValueError:
            target = 75.0
        if not name:
            flash("Subject name is required.", "error")
        elif not (1 <= target <= 100):
            flash("Target percentage must be between 1 and 100.", "error")
        else:
            db = get_db()
            for _ in range(10):
                if not code:
                    code = secrets.token_hex(3).upper()
                try:
                    db.execute(
                        "INSERT INTO subjects (prof_id, name, code, min_attendance_pct) VALUES (?, ?, ?, ?)",
                        (session["user_id"], name, code, target),
                    )
                    db.commit()
                    flash(f"Subject '{name}' created. Join code: {code}", "success")
                    return redirect(url_for("prof_dashboard"))
                except sqlite3.IntegrityError:
                    if request.form.get("code", "").strip():
                        flash("That subject code is already in use.", "error")
                        return render_template("subject_form.html", form=request.form)
                    code = ""
            flash("Could not generate a unique code, please try again.", "error")
    return render_template("subject_form.html", form=None)


@app.route("/prof/subject/<int:subject_id>")
@login_required("prof")
def subject_detail(subject_id):
    subject = get_own_subject(subject_id)
    if not subject:
        flash("Subject not found.", "error")
        return redirect(url_for("prof_dashboard"))
    db = get_db()
    stats = build_student_stats(db, subject)
    sessions_total = total_sessions(db, subject_id)
    history = db.execute(
        """
        SELECT ses.id, ses.session_date,
               SUM(CASE WHEN ar.status = 'P' THEN 1 ELSE 0 END) AS present_count,
               SUM(CASE WHEN ar.status = 'A' THEN 1 ELSE 0 END) AS absent_count
        FROM attendance_sessions ses
        LEFT JOIN attendance_records ar ON ar.session_id = ses.id
        WHERE ses.subject_id = ?
        GROUP BY ses.id
        ORDER BY ses.session_date DESC
        """,
        (subject_id,),
    ).fetchall()
    today_session = db.execute(
        "SELECT id FROM attendance_sessions WHERE subject_id = ? AND session_date = ?",
        (subject_id, date.today().isoformat()),
    ).fetchone()
    return render_template(
        "subject_detail.html",
        subject=subject,
        stats=stats,
        sessions_total=sessions_total,
        history=history,
        today_session=today_session,
        today=date.today().isoformat(),
    )


@app.route("/prof/subject/<int:subject_id>/add-student", methods=["POST"])
@login_required("prof")
def subject_add_student(subject_id):
    subject = get_own_subject(subject_id)
    if not subject:
        flash("Subject not found.", "error")
        return redirect(url_for("prof_dashboard"))
    username = request.form.get("username", "").strip().lower()
    db = get_db()
    student = db.execute("SELECT * FROM students WHERE username = ?", (username,)).fetchone()
    if not student:
        flash("No student found with that username.", "error")
    else:
        try:
            db.execute(
                "INSERT INTO enrollments (subject_id, student_id) VALUES (?, ?)",
                (subject_id, student["id"]),
            )
            db.commit()
            flash(f"{student['name']} added to {subject['name']}.", "success")
        except sqlite3.IntegrityError:
            flash("That student is already enrolled.", "error")
    return redirect(url_for("subject_detail", subject_id=subject_id))


@app.route("/prof/subject/<int:subject_id>/mark", methods=["GET", "POST"])
@login_required("prof")
def mark_attendance(subject_id):
    subject = get_own_subject(subject_id)
    if not subject:
        flash("Subject not found.", "error")
        return redirect(url_for("prof_dashboard"))
    db = get_db()
    students = db.execute(
        """
        SELECT st.id, st.name, st.roll_no
        FROM students st
        JOIN enrollments en ON en.student_id = st.id
        WHERE en.subject_id = ?
        ORDER BY st.name COLLATE NOCASE
        """,
        (subject_id,),
    ).fetchall()
    if not students:
        flash("No students enrolled yet — add some first.", "error")
        return redirect(url_for("subject_detail", subject_id=subject_id))

    today = date.today().isoformat()
    existing = db.execute(
        "SELECT id FROM attendance_sessions WHERE subject_id = ? AND session_date = ?",
        (subject_id, today),
    ).fetchone()
    if existing:
        flash("Attendance for today is already marked — opening it for edit.", "info")
        return redirect(url_for("edit_session", session_id=existing["id"]))

    if request.method == "POST":
        session_date = request.form.get("session_date") or today
        present_ids = {int(x) for x in request.form.getlist("present")}
        dup = db.execute(
            "SELECT id FROM attendance_sessions WHERE subject_id = ? AND session_date = ?",
            (subject_id, session_date),
        ).fetchone()
        if dup:
            flash("Attendance already exists for that date.", "error")
            return redirect(url_for("edit_session", session_id=dup["id"]))
        cur = db.execute(
            "INSERT INTO attendance_sessions (subject_id, session_date) VALUES (?, ?)",
            (subject_id, session_date),
        )
        new_id = cur.lastrowid
        for st in students:
            status = "P" if st["id"] in present_ids else "A"
            db.execute(
                "INSERT INTO attendance_records (session_id, student_id, status) VALUES (?, ?, ?)",
                (new_id, st["id"], status),
            )
        db.commit()
        absent = len(students) - len(present_ids)
        flash(
            f"Attendance saved — {len(present_ids)} present, {absent} absent.",
            "success",
        )
        return redirect(url_for("subject_detail", subject_id=subject_id))

    return render_template(
        "mark_form.html",
        subject=subject,
        students=students,
        session_date=today,
        present_ids={st["id"] for st in students},
        editing=False,
    )


@app.route("/prof/session/<int:session_id>/edit", methods=["GET", "POST"])
@login_required("prof")
def edit_session(session_id):
    db = get_db()
    row = db.execute(
        """
        SELECT ses.id, ses.subject_id, ses.session_date, s.prof_id, s.name AS subject_name
        FROM attendance_sessions ses
        JOIN subjects s ON s.id = ses.subject_id
        WHERE ses.id = ?
        """,
        (session_id,),
    ).fetchone()
    if not row or row["prof_id"] != session["user_id"]:
        flash("Session not found.", "error")
        return redirect(url_for("prof_dashboard"))
    subject = get_own_subject(row["subject_id"])
    students = db.execute(
        """
        SELECT st.id, st.name, st.roll_no
        FROM students st
        JOIN enrollments en ON en.student_id = st.id
        WHERE en.subject_id = ?
        ORDER BY st.name COLLATE NOCASE
        """,
        (row["subject_id"],),
    ).fetchall()
    existing = {
        r["student_id"]: r["status"]
        for r in db.execute(
            "SELECT student_id, status FROM attendance_records WHERE session_id = ?",
            (session_id,),
        ).fetchall()
    }

    if request.method == "POST":
        present_ids = {int(x) for x in request.form.getlist("present")}
        db.execute("DELETE FROM attendance_records WHERE session_id = ?", (session_id,))
        for st in students:
            status = "P" if st["id"] in present_ids else "A"
            db.execute(
                "INSERT INTO attendance_records (session_id, student_id, status) VALUES (?, ?, ?)",
                (session_id, st["id"], status),
            )
        db.commit()
        flash("Attendance updated.", "success")
        return redirect(url_for("subject_detail", subject_id=row["subject_id"]))

    return render_template(
        "mark_form.html",
        subject=subject,
        students=students,
        session_date=row["session_date"],
        present_ids={sid for sid, status in existing.items() if status == "P"},
        editing=True,
    )


# ---------------------------------------------------------------------------
# Student routes
# ---------------------------------------------------------------------------

@app.route("/student/dashboard")
@login_required("student")
def student_dashboard():
    db = get_db()
    subjects = db.execute(
        """
        SELECT s.* FROM subjects s
        JOIN enrollments e ON e.subject_id = s.id
        WHERE e.student_id = ?
        ORDER BY s.name COLLATE NOCASE
        """,
        (session["user_id"],),
    ).fetchall()
    cards = []
    for s in subjects:
        stats = build_student_stats(db, s, student_id=session["user_id"])
        if stats:
            cards.append({"subject": s, "stats": stats[0]})
    return render_template("student_dashboard.html", cards=cards)


@app.route("/student/subject/<int:subject_id>")
@login_required("student")
def student_subject(subject_id):
    db = get_db()
    subject = db.execute(
        """
        SELECT s.* FROM subjects s
        JOIN enrollments e ON e.subject_id = s.id
        WHERE s.id = ? AND e.student_id = ?
        """,
        (subject_id, session["user_id"]),
    ).fetchone()
    if not subject:
        flash("You are not enrolled in that subject.", "error")
        return redirect(url_for("student_dashboard"))
    stats = build_student_stats(db, subject, student_id=session["user_id"])[0]
    history = db.execute(
        """
        SELECT ses.session_date, ar.status
        FROM attendance_sessions ses
        LEFT JOIN attendance_records ar ON ar.session_id = ses.id AND ar.student_id = ?
        WHERE ses.subject_id = ?
        ORDER BY ses.session_date DESC
        """,
        (session["user_id"], subject_id),
    ).fetchall()
    return render_template("student_subject.html", subject=subject, stats=stats, history=history)


@app.route("/student/enroll", methods=["GET", "POST"])
@login_required("student")
def enroll():
    if request.method == "POST":
        code = request.form.get("code", "").strip().upper()
        db = get_db()
        subject = db.execute("SELECT * FROM subjects WHERE code = ?", (code,)).fetchone()
        if not subject:
            flash("Invalid subject code.", "error")
        else:
            try:
                db.execute(
                    "INSERT INTO enrollments (subject_id, student_id) VALUES (?, ?)",
                    (subject["id"], session["user_id"]),
                )
                db.commit()
                flash(f"Enrolled in {subject['name']}!", "success")
                return redirect(url_for("student_dashboard"))
            except sqlite3.IntegrityError:
                flash("You are already enrolled in that subject.", "error")
    db = get_db()
    my_subjects = db.execute(
        """
        SELECT s.* FROM subjects s
        JOIN enrollments e ON e.subject_id = s.id
        WHERE e.student_id = ?
        ORDER BY s.name COLLATE NOCASE
        """,
        (session["user_id"],),
    ).fetchall()
    return render_template("enroll.html", my_subjects=my_subjects)


if __name__ == "__main__":
    init_db()
    app.run(debug=True, port=5000)
