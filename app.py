import csv
import io
import os
import secrets
import sqlite3
from datetime import date, datetime
from functools import wraps

import click
from dotenv import load_dotenv
from flask import Flask, abort, flash, g, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash


SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS admins (
    id INTEGER PRIMARY KEY,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    is_owner INTEGER NOT NULL DEFAULT 0 CHECK (is_owner IN (0, 1)),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS interviewees (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    student_id TEXT NOT NULL UNIQUE,
    phone TEXT NOT NULL,
    admin_notes TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS slots (
    id INTEGER PRIMARY KEY,
    interview_date TEXT NOT NULL,
    start_time TEXT NOT NULL,
    end_time TEXT NOT NULL,
    capacity INTEGER NOT NULL CHECK (capacity > 0),
    active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (interview_date, start_time, end_time)
);

CREATE TABLE IF NOT EXISTS appointments (
    id INTEGER PRIMARY KEY,
    interviewee_id INTEGER NOT NULL UNIQUE REFERENCES interviewees(id) ON DELETE CASCADE,
    slot_id INTEGER NOT NULL REFERENCES slots(id) ON DELETE RESTRICT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS change_requests (
    id INTEGER PRIMARY KEY,
    interviewee_id INTEGER NOT NULL REFERENCES interviewees(id) ON DELETE CASCADE,
    old_slot_id INTEGER NOT NULL REFERENCES slots(id) ON DELETE RESTRICT,
    requested_slot_id INTEGER NOT NULL REFERENCES slots(id) ON DELETE RESTRICT,
    submitted_name TEXT NOT NULL,
    submitted_student_id TEXT NOT NULL,
    submitted_phone_last4 TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'approved', 'rejected')),
    admin_message TEXT NOT NULL DEFAULT '',
    submitted_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    processed_at TEXT,
    processed_by INTEGER REFERENCES admins(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS evaluations (
    id INTEGER PRIMARY KEY,
    interviewee_id INTEGER NOT NULL REFERENCES interviewees(id) ON DELETE CASCADE,
    admin_id INTEGER NOT NULL REFERENCES admins(id) ON DELETE RESTRICT,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_change_requests_interviewee_time
ON change_requests (interviewee_id, submitted_at DESC);
"""


def create_app(test_config=None):
    load_dotenv()
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_mapping(
        SECRET_KEY=os.environ.get("SECRET_KEY", "development-only-change-before-deploy"),
        DATABASE=os.path.join(app.instance_path, "interview.db"),
        MAX_CONTENT_LENGTH=2 * 1024 * 1024,
    )
    if test_config:
        app.config.update(test_config)
    os.makedirs(app.instance_path, exist_ok=True)

    def ensure_schema(db):
        db.executescript(SCHEMA)
        admin_columns = {row[1] for row in db.execute("PRAGMA table_info(admins)").fetchall()}
        if "is_owner" not in admin_columns:
            db.execute("ALTER TABLE admins ADD COLUMN is_owner INTEGER NOT NULL DEFAULT 0")
        if db.execute("SELECT COUNT(*) FROM admins").fetchone()[0] and not db.execute("SELECT 1 FROM admins WHERE is_owner = 1 LIMIT 1").fetchone():
            db.execute("UPDATE admins SET is_owner = 1 WHERE id = (SELECT MIN(id) FROM admins)")
        owner_ids = [row[0] for row in db.execute("SELECT id FROM admins WHERE is_owner = 1 ORDER BY id").fetchall()]
        if len(owner_ids) > 1:
            db.execute("UPDATE admins SET is_owner = 0 WHERE is_owner = 1 AND id <> ?", (owner_ids[0],))
        db.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_single_admin_owner ON admins(is_owner) WHERE is_owner = 1")
        db.commit()

    def get_db():
        if "db" not in g:
            g.db = sqlite3.connect(app.config["DATABASE"])
            g.db.row_factory = sqlite3.Row
            g.db.execute("PRAGMA foreign_keys = ON")
            ensure_schema(g.db)
        return g.db

    @app.teardown_appcontext
    def close_db(_error=None):
        db = g.pop("db", None)
        if db is not None:
            db.close()

    def init_db():
        get_db().executescript(SCHEMA)
        get_db().commit()

    @app.cli.command("init-db")
    @click.option("--admin-username", prompt="管理员用户名", default="admin", show_default=True)
    @click.option("--admin-password", prompt="管理员密码", hide_input=True, confirmation_prompt=True)
    def init_db_command(admin_username, admin_password):
        """创建数据库，并初始化第一个管理员账号。"""
        init_db()
        db = get_db()
        existing = db.execute("SELECT id FROM admins WHERE username = ?", (admin_username.strip(),)).fetchone()
        if existing:
            click.echo("数据库已创建；该管理员用户名已存在，未修改密码。")
            return
        db.execute(
            "INSERT INTO admins (username, password_hash, is_owner) VALUES (?, ?, ?)",
            (admin_username.strip(), generate_password_hash(admin_password), 1 if db.execute("SELECT COUNT(*) FROM admins").fetchone()[0] == 0 else 0),
        )
        db.commit()
        click.echo("数据库和管理员账号已创建。")

    def normalize_phone(value):
        return "".join(character for character in value.strip() if character.isdigit())

    def format_slot(slot):
        return f"{slot['interview_date']} {slot['start_time']}-{slot['end_time']}"

    def query_slots(include_inactive=False):
        clause = "" if include_inactive else "WHERE s.active = 1"
        return get_db().execute(
            f"""
            SELECT s.*, COUNT(a.id) AS booked,
                   GROUP_CONCAT(i.name, '、') AS interviewee_names
            FROM slots AS s
            LEFT JOIN appointments AS a ON a.slot_id = s.id
            LEFT JOIN interviewees AS i ON i.id = a.interviewee_id
            {clause}
            GROUP BY s.id
            ORDER BY s.interview_date, s.start_time, s.end_time
            """
        ).fetchall()

    def csrf_token():
        if "csrf_token" not in session:
            session["csrf_token"] = secrets.token_urlsafe(32)
        return session["csrf_token"]

    def require_csrf():
        token = request.form.get("csrf_token", "")
        if not token or not secrets.compare_digest(token, session.get("csrf_token", "")):
            abort(400, "请求已失效，请刷新页面后重试。")

    def login_required(view):
        @wraps(view)
        def wrapped_view(*args, **kwargs):
            if "admin_id" not in session:
                flash("请先登录管理员账号。", "error")
                return redirect(url_for("admin_login"))
            return view(*args, **kwargs)
        return wrapped_view

    def owner_required(view):
        @wraps(view)
        def wrapped_view(*args, **kwargs):
            if "admin_id" not in session:
                flash("请先登录管理员账号。", "error")
                return redirect(url_for("admin_login"))
            if not session.get("is_owner"):
                abort(403, "只有站长可以管理管理员账号。")
            return view(*args, **kwargs)
        return wrapped_view

    @app.context_processor
    def inject_helpers():
        return {"csrf_token": csrf_token, "format_slot": format_slot}

    @app.get("/")
    def index():
        slots = query_slots()
        grouped_slots = {}
        for slot in slots:
            grouped_slots.setdefault(slot["interview_date"], []).append(slot)
        search_query = request.args.get("name", "").strip()
        search_result = None
        if search_query:
            search_result = get_db().execute(
                """
                SELECT i.name, i.student_id, s.interview_date, s.start_time, s.end_time
                FROM interviewees i
                JOIN appointments a ON a.interviewee_id = i.id
                JOIN slots s ON s.id = a.slot_id
                WHERE i.name LIKE ?
                ORDER BY s.interview_date, s.start_time, i.student_id
                """,
                (f"%{search_query}%",),
            ).fetchall()
        return render_template(
            "index.html",
            slots=slots,
            grouped_slots=grouped_slots,
            search_query=search_query,
            search_result=search_result,
        )

    @app.route("/request/verify", methods=["GET", "POST"])
    def verify_identity():
        if request.method == "POST":
            require_csrf()
            name = request.form.get("name", "").strip()
            student_id = request.form.get("student_id", "").strip()
            phone_last4 = normalize_phone(request.form.get("phone_last4", ""))
            person = get_db().execute(
                "SELECT * FROM interviewees WHERE name = ? AND student_id = ?",
                (name, student_id),
            ).fetchone()
            if not person or len(phone_last4) != 4 or person["phone"][-4:] != phone_last4:
                flash("身份信息不匹配，请核对姓名、学号和电话号码后四位。", "error")
            else:
                appointment = get_db().execute(
                    "SELECT slot_id FROM appointments WHERE interviewee_id = ?", (person["id"],)
                ).fetchone()
                if not appointment:
                    flash("暂未找到你的面试安排，请联系管理员。", "error")
                else:
                    session["verified_interviewee_id"] = person["id"]
                    return redirect(url_for("create_change_request"))
        return render_template("verify_identity.html")

    @app.route("/request/change", methods=["GET", "POST"])
    def create_change_request():
        interviewee_id = session.get("verified_interviewee_id")
        if not interviewee_id:
            flash("请先完成身份核验。", "error")
            return redirect(url_for("verify_identity"))
        db = get_db()
        person = db.execute("SELECT * FROM interviewees WHERE id = ?", (interviewee_id,)).fetchone()
        appointment = db.execute(
            "SELECT a.slot_id, s.* FROM appointments a JOIN slots s ON s.id = a.slot_id WHERE a.interviewee_id = ?",
            (interviewee_id,),
        ).fetchone()
        if not person or not appointment:
            session.pop("verified_interviewee_id", None)
            flash("暂未找到有效的面试安排，请重新核验。", "error")
            return redirect(url_for("verify_identity"))
        choices = [slot for slot in query_slots() if slot["id"] != appointment["slot_id"] and slot["booked"] < slot["capacity"]]
        if request.method == "POST":
            require_csrf()
            target_id = request.form.get("requested_slot_id", type=int)
            try:
                db.execute("BEGIN IMMEDIATE")
                target = db.execute("SELECT * FROM slots WHERE id = ? AND active = 1", (target_id,)).fetchone()
                current = db.execute("SELECT * FROM appointments WHERE interviewee_id = ?", (interviewee_id,)).fetchone()
                recent = db.execute(
                    "SELECT submitted_at FROM change_requests WHERE interviewee_id = ? ORDER BY submitted_at DESC LIMIT 1",
                    (interviewee_id,),
                ).fetchone()
                if not target or target_id == appointment["slot_id"] or not current or current["slot_id"] != appointment["slot_id"]:
                    raise ValueError("请选择有效的新时间段，当前安排可能已经改变。")
                if recent:
                    elapsed = db.execute("SELECT (julianday('now') - julianday(?)) * 86400", (recent["submitted_at"],)).fetchone()[0]
                    if elapsed is not None and elapsed < 30:
                        raise ValueError(f"两次调整申请至少间隔 30 秒，请等待 {max(1, int(30 - elapsed))} 秒。")
                booked = db.execute("SELECT COUNT(*) FROM appointments WHERE slot_id = ?", (target_id,)).fetchone()[0]
                if booked >= target["capacity"]:
                    raise ValueError("目标时间段刚刚已满，请选择其他时间段。")
                db.execute(
                    """
                    UPDATE appointments SET slot_id = ? WHERE interviewee_id = ? AND slot_id = ?
                    """,
                    (target_id, interviewee_id, appointment["slot_id"]),
                )
                if db.execute("SELECT changes()").fetchone()[0] != 1:
                    raise ValueError("当前安排已经改变，请刷新后重试。")
                db.execute(
                    """
                    INSERT INTO change_requests
                    (interviewee_id, old_slot_id, requested_slot_id, submitted_name, submitted_student_id, submitted_phone_last4,
                     status, admin_message, processed_at)
                    VALUES (?, ?, ?, ?, ?, ?, 'approved', '系统自动处理：目标时间段有空位。', CURRENT_TIMESTAMP)
                    """,
                    (interviewee_id, appointment["slot_id"], target_id, person["name"], person["student_id"], person["phone"][-4:]),
                )
                db.commit()
                session.pop("verified_interviewee_id", None)
                flash("面试时间已自动调整成功。", "success")
                return redirect(url_for("index"))
            except (ValueError, sqlite3.Error) as error:
                db.rollback()
                flash(str(error) if isinstance(error, ValueError) else "调整失败，请刷新后重试。", "error")
        return render_template("change_request.html", person=person, appointment=appointment, choices=choices)

    @app.route("/admin/login", methods=["GET", "POST"])
    def admin_login():
        if request.method == "POST":
            require_csrf()
            username = request.form.get("username", "").strip()
            password = request.form.get("password", "")
            admin = get_db().execute("SELECT * FROM admins WHERE username = ?", (username,)).fetchone()
            if admin and check_password_hash(admin["password_hash"], password):
                session.clear()
                session["admin_id"] = admin["id"]
                session["admin_username"] = admin["username"]
                session["is_owner"] = bool(admin["is_owner"])
                csrf_token()
                return redirect(url_for("admin_dashboard"))
            flash("用户名或密码错误。", "error")
        return render_template("admin_login.html")

    @app.post("/admin/logout")
    @login_required
    def admin_logout():
        require_csrf()
        session.clear()
        flash("已退出管理员账号。", "success")
        return redirect(url_for("index"))

    @app.get("/admin")
    @login_required
    def admin_dashboard():
        db = get_db()
        counts = {
            "interviewees": db.execute("SELECT COUNT(*) FROM interviewees").fetchone()[0],
            "slots": db.execute("SELECT COUNT(*) FROM slots WHERE active = 1").fetchone()[0],
            "requests": db.execute("SELECT COUNT(*) FROM change_requests").fetchone()[0],
        }
        return render_template("admin_dashboard.html", counts=counts)

    @app.route("/admin/admins", methods=["GET", "POST"])
    @owner_required
    def admin_accounts():
        db = get_db()
        if request.method == "POST":
            require_csrf()
            username = request.form.get("username", "").strip()
            password = request.form.get("password", "")
            if not username or len(password) < 8:
                flash("管理员用户名不能为空，密码至少需要 8 位。", "error")
            else:
                try:
                    db.execute("INSERT INTO admins (username, password_hash, is_owner) VALUES (?, ?, 0)", (username, generate_password_hash(password)))
                    db.commit()
                    flash("管理员账号已添加。", "success")
                    return redirect(url_for("admin_accounts"))
                except sqlite3.IntegrityError:
                    flash("该用户名已存在。", "error")
        admins = db.execute("SELECT id, username, is_owner, created_at FROM admins ORDER BY is_owner DESC, username").fetchall()
        return render_template("admin_accounts.html", admins=admins)

    @app.post("/admin/admins/<int:admin_id>/password")
    @owner_required
    def reset_admin_password(admin_id):
        require_csrf()
        password = request.form.get("password", "")
        if len(password) < 8:
            flash("密码至少需要 8 位。", "error")
        else:
            db = get_db()
            db.execute("UPDATE admins SET password_hash = ? WHERE id = ?", (generate_password_hash(password), admin_id))
            db.commit()
            flash("管理员密码已更新。", "success")
        return redirect(url_for("admin_accounts"))

    @app.post("/admin/admins/<int:admin_id>/delete")
    @owner_required
    def delete_admin(admin_id):
        require_csrf()
        if admin_id == session["admin_id"]:
            flash("不能删除当前站长账号。", "error")
            return redirect(url_for("admin_accounts"))
        db = get_db()
        target = db.execute("SELECT is_owner FROM admins WHERE id = ?", (admin_id,)).fetchone()
        if not target:
            flash("管理员账号不存在。", "error")
        elif target["is_owner"]:
            flash("站长账号不能删除。", "error")
        else:
            try:
                db.execute("DELETE FROM admins WHERE id = ?", (admin_id,))
                db.commit()
                flash("管理员账号已删除。", "success")
            except sqlite3.IntegrityError:
                flash("该管理员已有评价记录，不能删除；请保留账号以维护历史记录。", "error")
        return redirect(url_for("admin_accounts"))

    @app.route("/admin/interviewees", methods=["GET", "POST"])
    @login_required
    def admin_interviewees():
        db = get_db()
        if request.method == "POST":
            require_csrf()
            name = request.form.get("name", "").strip()
            student_id = request.form.get("student_id", "").strip()
            phone = normalize_phone(request.form.get("phone", ""))
            if not name or not student_id or not 7 <= len(phone) <= 20:
                flash("请填写姓名、学号和有效电话号码。", "error")
            else:
                try:
                    db.execute("INSERT INTO interviewees (name, student_id, phone) VALUES (?, ?, ?)", (name, student_id, phone))
                    db.commit()
                    flash("面试人员已添加。", "success")
                    return redirect(url_for("admin_interviewees"))
                except sqlite3.IntegrityError:
                    flash("该学号已存在，请使用 CSV 导入更新，或检查输入。", "error")
        people = db.execute("SELECT * FROM interviewees ORDER BY student_id").fetchall()
        return render_template("admin_interviewees.html", people=people)

    @app.get("/admin/interviewees/<int:interviewee_id>")
    @login_required
    def admin_interviewee_detail(interviewee_id):
        db = get_db()
        person = db.execute("SELECT * FROM interviewees WHERE id = ?", (interviewee_id,)).fetchone()
        if not person:
            abort(404)
        appointment = db.execute(
            """
            SELECT s.* FROM appointments a JOIN slots s ON s.id = a.slot_id
            WHERE a.interviewee_id = ?
            """, (interviewee_id,)
        ).fetchone()
        evaluations = db.execute(
            """
            SELECT e.*, a.username AS admin_username
            FROM evaluations e JOIN admins a ON a.id = e.admin_id
            WHERE e.interviewee_id = ? ORDER BY e.created_at DESC, e.id DESC
            """, (interviewee_id,)
        ).fetchall()
        return render_template("admin_interviewee_detail.html", person=person, appointment=appointment, evaluations=evaluations)

    @app.post("/admin/interviewees/<int:interviewee_id>/evaluations")
    @login_required
    def add_evaluation(interviewee_id):
        require_csrf()
        content = request.form.get("content", "").strip()
        if not content:
            flash("评价内容不能为空。", "error")
        else:
            db = get_db()
            exists = db.execute("SELECT id FROM interviewees WHERE id = ?", (interviewee_id,)).fetchone()
            if not exists:
                abort(404)
            db.execute("INSERT INTO evaluations (interviewee_id, admin_id, content) VALUES (?, ?, ?)", (interviewee_id, session["admin_id"], content))
            db.commit()
            flash("评价已添加。", "success")
        return redirect(url_for("admin_interviewee_detail", interviewee_id=interviewee_id))

    @app.post("/admin/interviewees/<int:interviewee_id>/notes")
    @login_required
    def update_notes(interviewee_id):
        require_csrf()
        notes = request.form.get("admin_notes", "").strip()
        get_db().execute(
            "UPDATE interviewees SET admin_notes = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (notes, interviewee_id),
        )
        get_db().commit()
        flash("管理员备注已保存。", "success")
        return redirect(url_for("admin_interviewees"))

    @app.route("/admin/import", methods=["GET", "POST"])
    @login_required
    def import_csv():
        if request.method == "POST":
            require_csrf()
            uploaded = request.files.get("csv_file")
            if not uploaded or not uploaded.filename:
                flash("请选择 CSV 文件。", "error")
                return redirect(url_for("import_csv"))
            if not uploaded.filename.lower().endswith(".csv"):
                flash("只能上传 .csv 文件。", "error")
                return redirect(url_for("import_csv"))
            raw = uploaded.read()
            text = None
            for encoding in ("utf-8-sig", "utf-8", "gb18030"):
                try:
                    text = raw.decode(encoding)
                    break
                except UnicodeDecodeError:
                    continue
            if text is None:
                flash("文件编码无法识别，请另存为 UTF-8 编码的 CSV。", "error")
                return redirect(url_for("import_csv"))
            reader = csv.DictReader(io.StringIO(text))
            aliases = {
                "name": {"name", "姓名"},
                "student_id": {"student_id", "学号"},
                "phone": {"phone", "电话号码", "电话", "手机号"},
            }
            headers = {header.strip().lstrip("\ufeff").lower(): header for header in (reader.fieldnames or []) if header}
            column = {}
            for key, names in aliases.items():
                source = next((headers[name] for name in names if name in headers), None)
                if not source:
                    flash("CSV 必须包含姓名、学号、电话号码三列（或 name、student_id、phone）。", "error")
                    return redirect(url_for("import_csv"))
                column[key] = source
            db = get_db()
            created = updated = 0
            errors = []
            for row_number, row in enumerate(reader, start=2):
                name = (row.get(column["name"]) or "").strip()
                student_id = (row.get(column["student_id"]) or "").strip()
                phone = normalize_phone(row.get(column["phone"]) or "")
                if not name or not student_id or not 7 <= len(phone) <= 20:
                    errors.append(f"第 {row_number} 行：姓名、学号或电话号码无效")
                    continue
                exists = db.execute("SELECT id FROM interviewees WHERE student_id = ?", (student_id,)).fetchone()
                if exists:
                    db.execute(
                        "UPDATE interviewees SET name = ?, phone = ?, updated_at = CURRENT_TIMESTAMP WHERE student_id = ?",
                        (name, phone, student_id),
                    )
                    updated += 1
                else:
                    db.execute("INSERT INTO interviewees (name, student_id, phone) VALUES (?, ?, ?)", (name, student_id, phone))
                    created += 1
            db.commit()
            message = f"导入完成：新增 {created} 人，更新 {updated} 人。"
            if errors:
                message += f"跳过 {len(errors)} 行：" + "；".join(errors[:8])
                if len(errors) > 8:
                    message += "；其余错误请检查源文件。"
            flash(message, "success" if not errors else "warning")
            return redirect(url_for("admin_interviewees"))
        return render_template("admin_import.html")

    @app.route("/admin/slots", methods=["GET", "POST"])
    @login_required
    def admin_slots():
        if request.method == "POST":
            require_csrf()
            raw_date = request.form.get("interview_date", "")
            start_time = request.form.get("start_time", "")
            end_time = request.form.get("end_time", "")
            capacity = request.form.get("capacity", type=int)
            try:
                date.fromisoformat(raw_date)
                datetime.strptime(start_time, "%H:%M")
                datetime.strptime(end_time, "%H:%M")
                if start_time >= end_time or not capacity or capacity < 1 or capacity > 1000:
                    raise ValueError
                get_db().execute(
                    "INSERT INTO slots (interview_date, start_time, end_time, capacity) VALUES (?, ?, ?, ?)",
                    (raw_date, start_time, end_time, capacity),
                )
                get_db().commit()
                flash("时间段已创建。", "success")
                return redirect(url_for("admin_slots"))
            except (ValueError, sqlite3.IntegrityError):
                flash("时间段信息无效，或相同时间段已存在。", "error")
        slots = query_slots(include_inactive=True)
        slot_people = {}
        for slot in slots:
            slot_people[slot["id"]] = get_db().execute(
                "SELECT i.id, i.name, i.student_id FROM appointments a JOIN interviewees i ON i.id = a.interviewee_id WHERE a.slot_id = ? ORDER BY i.student_id",
                (slot["id"],),
            ).fetchall()
        return render_template("admin_slots.html", slots=slots, slot_people=slot_people)

    @app.post("/admin/slots/<int:slot_id>/toggle")
    @login_required
    def toggle_slot(slot_id):
        require_csrf()
        get_db().execute("UPDATE slots SET active = 1 - active WHERE id = ?", (slot_id,))
        get_db().commit()
        flash("时间段状态已更新。", "success")
        return redirect(url_for("admin_slots"))

    @app.route("/admin/appointments", methods=["GET", "POST"])
    @login_required
    def admin_appointments():
        db = get_db()
        if request.method == "POST":
            require_csrf()
            interviewee_id = request.form.get("interviewee_id", type=int)
            slot_id = request.form.get("slot_id", type=int)
            try:
                db.execute("BEGIN IMMEDIATE")
                person = db.execute("SELECT id FROM interviewees WHERE id = ?", (interviewee_id,)).fetchone()
                slot = db.execute("SELECT * FROM slots WHERE id = ? AND active = 1", (slot_id,)).fetchone()
                booked = db.execute("SELECT COUNT(*) FROM appointments WHERE slot_id = ?", (slot_id,)).fetchone()[0]
                assigned = db.execute("SELECT id FROM appointments WHERE interviewee_id = ?", (interviewee_id,)).fetchone()
                if not person or not slot or assigned or booked >= slot["capacity"]:
                    raise ValueError
                db.execute("INSERT INTO appointments (interviewee_id, slot_id) VALUES (?, ?)", (interviewee_id, slot_id))
                db.commit()
                flash("面试安排已保存。", "success")
                return redirect(url_for("admin_appointments"))
            except (ValueError, sqlite3.Error):
                db.rollback()
                flash("无法安排：人员可能已有安排，或目标时间段已满。", "error")
        people = db.execute(
            "SELECT i.* FROM interviewees i LEFT JOIN appointments a ON a.interviewee_id = i.id WHERE a.id IS NULL ORDER BY i.student_id"
        ).fetchall()
        slots = [slot for slot in query_slots() if slot["booked"] < slot["capacity"]]
        appointments = db.execute(
            """
            SELECT a.id, i.id AS interviewee_id, i.name, i.student_id, s.interview_date, s.start_time, s.end_time
            FROM appointments a JOIN interviewees i ON i.id = a.interviewee_id JOIN slots s ON s.id = a.slot_id
            ORDER BY s.interview_date, s.start_time, i.student_id
            """
        ).fetchall()
        return render_template("admin_appointments.html", people=people, slots=slots, appointments=appointments)

    @app.get("/admin/change-requests")
    @login_required
    def admin_change_requests():
        rows = get_db().execute(
            """
            SELECT r.*, old.interview_date AS old_date, old.start_time AS old_start, old.end_time AS old_end,
                   target.interview_date AS target_date, target.start_time AS target_start, target.end_time AS target_end
            FROM change_requests r
            JOIN slots old ON old.id = r.old_slot_id
            JOIN slots target ON target.id = r.requested_slot_id
            ORDER BY CASE r.status WHEN 'pending' THEN 0 ELSE 1 END, r.submitted_at DESC
            """
        ).fetchall()
        return render_template("admin_change_requests.html", requests=rows)

    @app.errorhandler(413)
    def too_large(_error):
        flash("上传文件不能超过 2 MB。", "error")
        return redirect(url_for("import_csv"))

    return app


app = create_app()

if __name__ == "__main__":
    app.run(debug=True)
