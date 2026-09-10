# 面试时间调整系统

这是一个使用 Flask 和 SQLite 开发的面试排期与调整申请系统。学生可查看公开排期并提交调整申请；管理员可维护面试人员、时间段、排期和私有备注，并审核申请。

## 本机运行

在 VS Code 终端、项目根目录执行：

```powershell
Copy-Item .env.example .env
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\flask.exe --app app init-db
.\.venv\Scripts\python.exe app.py
```

请打开 `.env`，将 `SECRET_KEY` 改为一段足够长的随机字符串。第一次初始化时，终端会要求输入管理员用户名和密码。密码只以哈希形式保存在本机 SQLite 数据库中。浏览器访问 `http://127.0.0.1:5000`，管理员入口为 `/admin/login`。

开发数据库位于 `instance/interview.db`，已由 `.gitignore` 忽略，不会上传到 GitHub。

## CSV 批量导入

管理员登录后，访问“CSV 导入”。CSV 必须含有下列三列，支持中文或英文表头：

```csv
姓名,学号,电话号码
张三,20260001,13800138000
李四,20260002,13900139000
```

也可使用 `name,student_id,phone`。电话号码中的空格、连字符和括号会被自动忽略。学号重复时，系统更新该人员的姓名和电话，保留管理员备注、既有排期和历史申请。

请勿把真实学生信息保存到仓库或提交到 GitHub。

## 自动化测试

安装依赖后执行：

```powershell
.\.venv\Scripts\python.exe -m pytest
```
