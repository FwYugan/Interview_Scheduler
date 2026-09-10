# 面试时间调整网站：项目现状与后续计划

更新时间：2026-09-11

## 一、项目目标

网站用于让学生查看面试时间安排，并提交面试时间调整请求。管理员能够维护面试人员、面试时间段和调整请求，并为每位面试人员保存仅管理员可见的备注。

主要功能包括：

1. 展示按日期和时间段划分的面试安排表。
2. 每个时间段设置最大面试人数，并始终显示剩余名额。
3. 学生使用姓名、学号、电话号码后四位核验身份。
4. 学生选择原面试时间和申请的新面试时间，提交调整请求。
5. 管理员录入面试人员、安排面试时间、处理调整请求。
6. 管理员为面试人员记录私有备注，学生不能查看。

## 二、已知环境

### 服务器

- 系统：Ubuntu。
- 公网地址：`http://82.156.112.198`。
- Python 3：服务器原本已安装，具体版本尚未记录。
- Git：已安装，截图显示版本为 `2.43.0`。
- SQLite：已安装，截图显示版本为 `3.45.1`。
- Caddy：已安装，截图显示版本为 `v2.11.4`。
- Caddy 服务：`enabled` 且 `active (running)`。
- Caddy 当前使用 `/etc/caddy/Caddyfile`。
- 访问公网 IP 已显示 Caddy 默认页面，说明服务器网络、80 端口和 Caddy 基础服务正常。

### 本机

- 使用 Windows + VS Code 进行开发。
- Git、VS Code、SSH 已确认存在。
- 本机开发命令应在 VS Code 集成终端中执行。
- Python：已确认 `py --version` 为 `Python 3.14.7`，`py -m pip --version` 为 `pip 26.2.1`。
- 本机不需要 Node.js 或独立 SQLite 软件；Python 自带的 `sqlite3` 模块即可访问数据库。

## 三、已完成操作

1. 创建了 Ubuntu 安装脚本 `scripts/bootstrap_ubuntu.sh`。
2. 脚本已上传到服务器并执行。
3. 服务器已安装 Git、SQLite、Python pip、Python venv 和 Caddy。
4. Caddy 已设置为开机启动并正在运行。
5. 创建了服务器安装说明 `SERVER_SETUP.md`。
6. 已通过浏览器访问公网 IP，确认看到 Caddy 默认页。
7. 已在本机确认 Python 和 pip 可用。
8. 已初始化本地 Git 仓库，并创建适用于 Python、SQLite、虚拟环境和密钥文件的 `.gitignore`。
9. GitHub 远程仓库已关联：`https://github.com/FwYugan/Interview_Scheduler.git`，默认分支为 `main`。
10. 已完成最小可用版本：Flask 应用、SQLite 数据库、管理员登录、人员手动录入、时间段管理、初始排期、学生调整申请和管理员审核。
11. 已加入 CSV 批量导入模块，支持中文和英文表头、UTF-8/Excel 常见中文编码、逐行校验和按学号更新。

## 四、当前尚未完成的部分

看到 Caddy 默认页面只说明 Web 服务器正常，不代表业务网站已经部署。以下内容仍需开发：

- 真实业务数据的导入和管理员账号初始化。
- 本机端到端测试和界面验收。
- Caddy 反向代理到 Python 应用。
- Python 应用的 systemd 服务。
- 生产环境备份、日志和安全配置。

## 五、推荐的技术方案

### 后端

建议使用 Flask。项目规模适中，便于学习、部署和维护；SQLite 使用 Python 内置 `sqlite3` 模块即可。生产环境使用 Gunicorn 运行 Flask，Caddy 负责接收公网 HTTP/HTTPS 请求并反向代理到 Gunicorn。

当前依赖：

- Flask
- Gunicorn
- python-dotenv
- pytest

### 数据表初稿

- `interviewees`：姓名、学号、电话号码后四位、管理员私有备注、创建时间。
- `slots`：日期、开始时间、结束时间、最大人数、状态。
- `appointments`：面试人员与时间段的关联、状态、创建时间。
- `change_requests`：原时间段、新时间段、申请人、申请状态、管理员处理信息和时间。
- `admin_users`：管理员账号、密码哈希、是否启用。

调整请求必须同时记录原时间段和目标时间段。数据库写入时要在事务中再次检查目标时间段剩余名额，不能只依赖页面显示的空位。

## 六、建议实施顺序

### 第 1 步：确认本机 Python

在 VS Code 集成终端检查：

```powershell
py --version
py -m pip --version
```

如果 VS Code 找不到解释器，安装 Python 3，并安装 Python、Pylance 扩展。随后在项目根目录创建虚拟环境：

```powershell
py -m venv .venv
```

### 第 2 步：初始化 Git 项目

本地 Git 仓库、`.gitignore` 和 GitHub 远程仓库均已创建。远程地址为 `https://github.com/FwYugan/Interview_Scheduler.git`，默认分支为 `main`。不要把管理员密码、密钥或真实电话号码提交到 GitHub。

后续每次完成一组功能后，在 VS Code 终端执行 `git add .`、`git commit -m "说明本次改动"` 和 `git push`。推送前务必确认 `.env`、`instance/` 和真实人员 CSV 未被纳入提交。

### 第 3 步：先完成最小可用版本

此步骤已完成。当前第一版采用以下业务规则：

1. 管理员在创建每个时间段时单独填写最大人数，因此不同时间段可以有不同容量。
2. 学生提交调整申请后，管理员审核通过才会变更面试安排；审核时系统会在 SQLite 事务中再次检查目标时间段是否仍有名额。
3. 学生身份核验使用姓名、学号和电话号码后四位；公开页面只展示姓名、时间和名额，不显示电话、学号或管理员备注。
4. CSV 导入需要“姓名、学号、电话号码”列，或 `name,student_id,phone` 英文列。相同学号会更新姓名和电话，同时保留管理员备注、排期和历史申请。

本机启动方式：

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\flask.exe --app app init-db
.\.venv\Scripts\python.exe app.py
```

运行前先复制 `.env.example` 为 `.env`，并在 `.env` 中设置一段随机的 `SECRET_KEY`。然后访问 `http://127.0.0.1:5000`。管理员入口为 `/admin/login`。首次 `init-db` 会在终端中安全地要求输入管理员用户名和密码。

### 第 4 步：本机测试

准备测试数据，验证以下情况：

- 时间段满员后不能继续安排。
- 两个请求同时争抢最后一个名额时只能成功一个。
- 学生不能读取管理员备注。
- 错误的姓名、学号或电话后四位不能提交他人的请求。
- 管理员可以查看完整的调整历史。

### 第 5 步：部署到服务器

将代码上传或从 GitHub 拉取到服务器，在项目目录创建服务器虚拟环境并安装依赖。之后创建 Gunicorn 的 systemd 服务，让应用开机自动启动，再把 `/etc/caddy/Caddyfile` 配置为反向代理到本机 Gunicorn 端口。

### 第 6 步：上线前检查

- 管理员账号使用强密码，并只保存密码哈希。
- 生产环境关闭 Flask 调试模式。
- SQLite 数据库文件只允许应用用户读写。
- 定期备份 SQLite 数据库。
- 云安全组开放 80；使用域名和 HTTPS 时再开放 443。
- 仅使用 IP 地址时可以先用 HTTP 测试；可信 HTTPS 通常需要绑定域名。

## 七、下一阶段建议

下一步应先在本机完成 Flask 项目骨架、数据库模型和基础页面，再部署到服务器。不要立即修改 Caddy 默认配置；等 Python 应用在本机能够正常运行后，再进行 Gunicorn 和 Caddy 反向代理配置，这样更容易定位问题。

在开始编码前，需要确定两个业务规则：

1. 每个时间段的最大面试人数是多少，是否所有时间段相同。
2. 学生提交调整请求后，是管理员审核通过后变更，还是系统在有空位时自动变更。
