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


def test_public_name_search_returns_only_scheduled_interview_details(app, client):
    connection = sqlite3.connect(app.config["DATABASE"])
    connection.execute("INSERT INTO interviewees (name, student_id, phone) VALUES ('李同学', '3', '13700137000')")
    connection.execute("INSERT INTO interviewees (name, student_id, phone) VALUES ('李同学', '4', '13600136000')")
    connection.execute("INSERT INTO slots (interview_date, start_time, end_time, capacity) VALUES ('2026-10-02', '13:00', '13:30', 2)")
    connection.execute("INSERT INTO appointments (interviewee_id, slot_id) VALUES (1, 1)")
    connection.commit()
    connection.close()

    response = client.get("/?name=李同学")
    page = response.get_data(as_text=True)
    assert "2026-10-02" in page
    assert "13:00" in page
    assert "13700137000" not in page
    assert "13600136000" not in page


def test_owner_can_add_and_delete_non_owner_admin(app, client):
    login(client)
    token = csrf_token(client, "/admin/admins")
    response = client.post(
        "/admin/admins",
        data={"csrf_token": token, "username": "reviewer", "password": "strong-pass"},
        follow_redirects=True,
    )
    assert "管理员账号已添加" in response.get_data(as_text=True)
    connection = sqlite3.connect(app.config["DATABASE"])
    reviewer_id = connection.execute("SELECT id FROM admins WHERE username = 'reviewer'").fetchone()[0]
    connection.close()
    token = csrf_token(client, "/admin/admins")
    response = client.post(f"/admin/admins/{reviewer_id}/delete", data={"csrf_token": token}, follow_redirects=True)
    assert "管理员账号已删除" in response.get_data(as_text=True)


def test_student_change_is_applied_and_second_request_is_rate_limited(app, client):
    connection = sqlite3.connect(app.config["DATABASE"])
    connection.execute("INSERT INTO interviewees (name, student_id, phone) VALUES ('换时学生', '5', '13500135000')")
    connection.execute("INSERT INTO slots (interview_date, start_time, end_time, capacity) VALUES ('2026-10-03', '09:00', '09:30', 1)")
    connection.execute("INSERT INTO slots (interview_date, start_time, end_time, capacity) VALUES ('2026-10-03', '10:00', '10:30', 1)")
    connection.execute("INSERT INTO appointments (interviewee_id, slot_id) VALUES (1, 1)")
    connection.commit()
    connection.close()

    token = csrf_token(client, "/request/verify")
    response = client.post("/request/verify", data={"csrf_token": token, "name": "换时学生", "student_id": "5", "phone_last4": "5000"})
    assert response.status_code == 302
    token = csrf_token(client, "/request/change")
    response = client.post("/request/change", data={"csrf_token": token, "requested_slot_id": 2}, follow_redirects=True)
    assert "自动调整成功" in response.get_data(as_text=True)

    connection = sqlite3.connect(app.config["DATABASE"])
    assert connection.execute("SELECT slot_id FROM appointments WHERE interviewee_id = 1").fetchone()[0] == 2
    assert connection.execute("SELECT status FROM change_requests WHERE interviewee_id = 1").fetchone()[0] == "approved"
    connection.close()

    token = csrf_token(client, "/request/verify")
    client.post("/request/verify", data={"csrf_token": token, "name": "换时学生", "student_id": "5", "phone_last4": "5000"})
    token = csrf_token(client, "/request/change")
    response = client.post("/request/change", data={"csrf_token": token, "requested_slot_id": 1}, follow_redirects=True)
    assert "至少间隔 30 秒" in response.get_data(as_text=True)


def test_evaluation_is_attributed_to_logged_in_admin(app, client):
    connection = sqlite3.connect(app.config["DATABASE"])
    connection.execute("INSERT INTO interviewees (name, student_id, phone) VALUES ('评价学生', '6', '13400134000')")
    connection.commit()
    connection.close()
    login(client)
    token = csrf_token(client, "/admin/interviewees/1")
    response = client.post("/admin/interviewees/1/evaluations", data={"csrf_token": token, "content": "表现良好"}, follow_redirects=True)
    assert "表现良好" in response.get_data(as_text=True)
    assert "admin" in response.get_data(as_text=True)
