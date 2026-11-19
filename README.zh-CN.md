# chessfen

[English](README.md) | 中文

用于处理国际象棋编码——PGN、FEN——以及局面图片的 Python 工具（网页应用 + API）。

## 功能

- **棋盘识别**：上传二维棋盘图片，得到棋子摆放段（Placement）及逐格置信度。
- **PGN 导航**：在一个多局 PGN 中选择对局，导出任意半步之后的完整 FEN。
- **FEN 渲染**：把任意 FEN 渲染为 PNG 或 SVG 棋盘图片。
- **网页界面与 API**：通过管理页签发 API 密钥；界面支持中文、英文和德文。

## 快速开始（Docker）

每个 Release 会把预构建镜像推送到 `ghcr.io/sidnelyzhang/chessfen` 与 Docker Hub 的 `<DOCKERHUB_USERNAME>/chessfen`，并内置固定版本的识别模型。标签包含 `latest` 与版本标签（如 `v1.0.0`）。

```bash
docker run -d --name chessfen \
  -p 127.0.0.1:8000:8000 \
  -e ADMIN_PASSWORD='replace-with-a-strong-password' \
  -e SECRET_KEY='replace-with-a-long-random-secret' \
  -v chessfen-data:/data \
  ghcr.io/sidnelyzhang/chessfen:latest
```

打开 <http://localhost:8000>，在 `/admin` 管理页创建 API 密钥。

也可以使用 compose 文件：

```bash
cp .env.example .env   # 然后修改口令
docker compose up -d
```

`docker-compose.yml` 指向已发布的镜像，并且只把端口绑定到 `127.0.0.1`。若要改为源码构建：

```bash
docker compose -f docker-compose.yml -f docker-compose.build.yml up -d --build
```

在仓库与其 GHCR 包仍为私有期间，拉取前请先登录：`echo "$GITHUB_TOKEN" | docker login ghcr.io -u <github-user> --password-stdin`。

## 开发

使用 [uv](https://docs.astral.sh/uv/) 管理，要求 Python 3.13+。

```bash
uv sync                      # 安装项目与依赖
uv run alembic upgrade head  # 应用数据库迁移（默认 SQLite）
uv run chessfen              # 在 http://127.0.0.1:8000 提供访问
uv run pytest                # 运行测试
```

## 配置

- `CHESSFEN_IMAGE` — `docker-compose.yml` 使用的镜像（默认 `ghcr.io/sidnelyzhang/chessfen:latest`）。
- `DATABASE_URL` — 容器内默认 `sqlite:////data/chessfen.db`；设为 PostgreSQL 连接串（例如 `postgresql+psycopg://user:pass@host/db`）即可切换。
- `ADMIN_PASSWORD` — 启用管理页；未设置时管理登录关闭。
- `SECRET_KEY` — 用于签名管理会话 cookie；设置 `ADMIN_PASSWORD` 时必须同时设置。
- `FORWARDED_ALLOW_IPS` — 允许设置 `X-Forwarded-*` 头的对端（默认 `127.0.0.1`）。请设为反向代理的地址或网段，使限流能看到真实客户端 IP。

### 识别模型（本地开发）

Docker 镜像已内置固定版本的模型。本地开发时下载一次即可：

```bash
mkdir -p models
curl -fL -o models/yolo26m-finetuned.onnx \
  https://huggingface.co/AndrewSpano/2d-chess-ocr/resolve/03d9df9fc14fade1a3579683fd0de215b3864ee1/yolo26m-finetuned.onnx
```

可通过 `RECOGNITION_MODEL_PATH` 修改路径（默认 `models/yolo26m-finetuned.onnx`）。

### 数据

SQLite 数据保存在挂载到 `/data` 的 `chessfen-data` 卷中。使用 PostgreSQL 时，把 `DATABASE_URL` 指向数据库服务即可，该卷不再使用。

### TLS 与反向代理

容器在 8000 端口提供纯 HTTP，`docker-compose.yml` 默认只把端口绑定到 `127.0.0.1`。请在你自己的反向代理处终止 TLS，转发 `Host`、`X-Forwarded-For` 与 `X-Forwarded-Proto`，并把 `FORWARDED_ALLOW_IPS` 设为代理地址，使应用只信任来自它的这些头。

## API

API 通过 `X-API-Key: cf_...` 认证；密钥在管理页签发。端点：`POST /api/v1/render`、`POST /api/v1/pgn`、`POST /api/v1/recognize`。

## 发布

推送带注释的版本 tag 即可发布：

```bash
git tag -a v1.0.0 -m "ChessFEN 1.0.0"
git push origin v1.0.0
```

`Release` 工作流先经过共享的 `Test` 工作流（该工作流也在推送到 `main` 和 Pull Request 时运行）：单元测试、mypy，以及 Docker 镜像冒烟测试（启动容器、签发 API 密钥、用内置模型做一次真实识别）。CI 会下载并校验固定模型，使真实模型测试不再被跳过。

测试通过后并行执行两个独立任务：

1. **Publish**：构建并推送镜像到 GHCR（`ghcr.io/sidnelyzhang/chessfen`）与 Docker Hub（`<DOCKERHUB_USERNAME>/chessfen`），使用发布 tag 打标；稳定版额外打 `latest`。
2. **GitHub Release**：用带注释 tag 的消息作为发布说明创建 Release；轻量 tag 回退为 `Release <tag>`。包含连字符的 tag 会标记为预发布，且不移动 `latest`。

GitHub Release 与镜像发布相互独立，Docker Hub 出现问题不会阻塞 Release。

Docker Hub 需要仓库 Secrets：`DOCKERHUB_USERNAME`（小写）与 `DOCKERHUB_TOKEN`；缺失时发布任务会给出明确报错。GHCR 使用内置 `GITHUB_TOKEN`；如需匿名拉取，请把 GHCR 包设为公开。

## 许可证

ChessFEN 以 GPL-3.0-or-later 发布；完整文本见 [LICENSE](LICENSE)，第三方组件见 [NOTICE](NOTICE)（python-chess GPL-3.0+、2d-chess-ocr 权重 MIT、CBurnett 棋子 CC BY-SA 3.0、resvg_py/resvg MIT/MPL-2.0）。

## Agent 配置

工程技能从 `docs/agents/` 读取本仓库配置（issue 跟踪、triage 标签、域文档）。见 `AGENTS.md`。
