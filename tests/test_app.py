import io
import re
import sqlite3

import pytest
from werkzeug.security import generate_password_hash

from app import SCHEMA, create_app


@pytest.fixture
def app(tmp_path):
    database = tmp_path / "test.db"
    application = create_app(
        {
            "TESTING": True,
            "SECRET_KEY": "test-secret",
            "DATABASE": str(database),
        }
    )
    connection = sqlite3.connect(database)
    connection.executescript(SCHEMA)
    connection.execute(
        "INSERT INTO admins (username, password_hash) VALUES (?, ?)",
        ("admin", generate_password_hash("correct-password")),
    )
    connection.commit()
    connection.close()
    return application


@pytest.fixture
def client(app):
    return app.test_client()


def csrf_token(client, path):
    page = client.get(path)
    match = re.search(r'name="csrf_token" value="([^"]+)"', page.get_data(as_text=True))
    assert match
    return match.group(1)


def login(client):
    token = csrf_token(client, "/admin/login")
    response = client.post(
        "/admin/login",
        data={"csrf_token": token, "username": "admin", "password": "correct-password"},
    )
    assert response.status_code == 302


def test_csv_import_creates_and_updates_people(app, client):
    login(client)
    token = csrf_token(client, "/admin/import")
    response = client.post(
        "/admin/import",
        data={
            "csrf_token": token,
            "csv_file": (io.BytesIO("姓名,学号,电话号码\n张三,20260001,13800138000\n".encode()), "people.csv"),
        },
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert "新增 1 人" in response.get_data(as_text=True)

    connection = sqlite3.connect(app.config["DATABASE"])
    connection.execute("UPDATE interviewees SET admin_notes = '仅管理员可见' WHERE student_id = '20260001'")
    connection.commit()
    connection.close()

    token = csrf_token(client, "/admin/import")
    response = client.post(
        "/admin/import",
        data={
            "csrf_token": token,
            "csv_file": (io.BytesIO("name,student_id,phone\n张三（更新）,20260001,139-0013-9000\n".encode()), "people.csv"),
        },
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert "更新 1 人" in response.get_data(as_text=True)
    connection = sqlite3.connect(app.config["DATABASE"])
    person = connection.execute("SELECT name, phone, admin_notes FROM interviewees").fetchone()
    connection.close()
    assert person == ("张三（更新）", "13900139000", "仅管理员可见")


def test_public_page_does_not_expose_phone_or_admin_notes(app, client):
    connection = sqlite3.connect(app.config["DATABASE"])
    connection.execute(
        "INSERT INTO interviewees (name, student_id, phone, admin_notes) VALUES (?, ?, ?, ?)",
        ("王同学", "20260002", "13800138000", "私有录取评价"),
    )
    connection.execute(
        "INSERT INTO slots (interview_date, start_time, end_time, capacity) VALUES ('2026-10-01', '09:00', '09:30', 1)"
    )
    connection.execute("INSERT INTO appointments (interviewee_id, slot_id) VALUES (1, 1)")
    connection.commit()
    connection.close()

    response = client.get("/")
    page = response.get_data(as_text=True)
    assert "王同学" in page
    assert "13800138000" not in page
    assert "私有录取评价" not in page

    token = csrf_token(client, "/request/verify")
    response = client.post(
        "/request/verify",
        data={"csrf_token": token, "name": "王同学", "student_id": "20260002", "phone_last4": "8000"},
    )
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/request/change")


def test_approval_keeps_current_appointment_when_target_slot_is_full(app, client):
    connection = sqlite3.connect(app.config["DATABASE"])
    connection.execute("INSERT INTO interviewees (name, student_id, phone) VALUES ('甲', '1', '13800138000')")
    connection.execute("INSERT INTO interviewees (name, student_id, phone) VALUES ('乙', '2', '13900139000')")
    connection.execute(
        "INSERT INTO slots (interview_date, start_time, end_time, capacity) VALUES ('2026-10-01', '09:00', '09:30', 1)"
    )
    connection.execute(
        "INSERT INTO slots (interview_date, start_time, end_time, capacity) VALUES ('2026-10-01', '10:00', '10:30', 1)"
    )
    connection.execute("INSERT INTO appointments (interviewee_id, slot_id) VALUES (1, 1)")
    connection.execute("INSERT INTO appointments (interviewee_id, slot_id) VALUES (2, 2)")
    connection.execute(
        """
        INSERT INTO change_requests
        (interviewee_id, old_slot_id, requested_slot_id, submitted_name, submitted_student_id, submitted_phone_last4)
        VALUES (1, 1, 2, '甲', '1', '8000')
        """
    )
    connection.commit()
    connection.close()

    login(client)
    token = csrf_token(client, "/admin/change-requests")
    response = client.post(
        "/admin/change-requests/1/decision",
        data={"csrf_token": token, "action": "approve", "admin_message": ""},
        follow_redirects=True,
    )
    assert "目标时间段已满或已关闭" in response.get_data(as_text=True)
    connection = sqlite3.connect(app.config["DATABASE"])
    appointment_slot = connection.execute("SELECT slot_id FROM appointments WHERE interviewee_id = 1").fetchone()[0]
    status = connection.execute("SELECT status FROM change_requests WHERE id = 1").fetchone()[0]
    connection.close()
    assert appointment_slot == 1
    assert status == "pending"
