# MinerU-Popo FastAPI 服务

<p align="center">
  📖 <a href="./README.md"><b>English</b></a> &nbsp;|&nbsp; <a href="./README_zh.md"><b>简体中文</b></a>
</p>

MinerU-Popo 文档后处理流水线的 FastAPI 封装，基于 SQLite 任务队列。

## 安装

```bash
pip install -r api/requirements.txt
```

确保主项目依赖也已安装：

```bash
pip install -r requirements.txt
```

## 前置条件

无需外部服务。任务队列使用 Python 内置的 SQLite 持久化。

## 配置

启动前设置环境变量：

```bash
# 模型路径（推理必需）
export POPO_MODEL_PATH=/path/to/MinerU-Popo

# SQLite 数据库路径（可选，默认 ./data/popo_tasks.db）
export POPO_SQLITE_PATH=./data/popo_tasks.db

# 服务器设置（可选）
export POPO_API_HOST=0.0.0.0
export POPO_API_PORT=8440

# Worker 设置（可选）
export POPO_WORKER_CONCURRENCY=4
export POPO_SYNC_TIMEOUT=300
export POPO_TASK_TTL=86400
```

## 启动服务

```bash
# 开发模式（启动 API 服务器 + 后台 Worker）
python -m api.main

# 生产模式（uvicorn）
uvicorn api.main:app --host 0.0.0.0 --port 8440 --workers 4

# 仅 Worker（独立进程）
python -c "from api.services.worker import run_worker; run_worker()"
```

启动时服务器自动：
1. 初始化 SQLite 数据库
2. 启动后台 Worker 线程，从队列中取任务处理

## API 接口

### 1. 健康检查

```
GET /health
```

返回服务状态、数据库连接、队列长度和活跃 Worker 数。

**示例：**
```bash
curl -s http://localhost:8440/health | python -m json.tool
```

**响应：**
```json
{
  "status": "ok",
  "db_connected": true,
  "queue_length": 0,
  "workers_active": 1,
  "supported_models": ["mineru", "monkeyocr", "PaddleOCR-VL-1.5", "dolphin", "glm-ocr"]
}
```

---

### 2. 同步处理

```
POST /process
Content-Type: multipart/form-data
```

| 字段 | 类型 | 必填 | 说明 |
|-------|------|----------|-------------|
| `file` | File | 是 | 包含 OCR 输出的 ZIP 文件 |
| `model` | String | 是 | OCR 模型：`mineru`、`monkeyocr`、`PaddleOCR-VL-1.5`、`dolphin`、`glm-ocr` |
| `doc_id` | String | 否 | 文档 ID，不填则从 ZIP 结构自动推断 |

上传 ZIP，**阻塞等待完整流水线执行完毕**，在同一响应中返回文档树。

> 适合小文档或调试。大文档建议用异步接口 `POST /tasks`。

**示例：**
```bash
curl -X POST http://localhost:8440/process \
  -F "file=@page_2_mineru.zip" \
  -F "model=mineru" \
  -F "doc_id=page_2" \
  -o result.json
```

**响应（200）：**
```json
{
  "doc_id": "page_2",
  "status": "success",
  "message": "Document processed successfully",
  "tree": {
    "type": "root",
    "title": "",
    "level": 0,
    "children": [
      {
        "type": "text",
        "title": "Default Title",
        "level": 1,
        "content": "...<|txt_split|>...",
        "location": [{"bbox": [0.196, 0.866, 0.298, 0.91], "page": 1}],
        "block_ids": [1, 2, 3]
      },
      {
        "type": "page_number",
        "title": "Page 4 - page_number",
        "content": "006"
      }
    ]
  }
}
```

---

### 3. 提交异步任务（POST /tasks）

```
POST /tasks
Content-Type: multipart/form-data
```

| 字段 | 类型 | 必填 | 说明 |
|-------|------|----------|-------------|
| `file` | File | 是 | 包含 OCR 输出的 ZIP 文件 |
| `model` | String | 是 | OCR 模型名称 |
| `doc_id` | String | 否 | 文档 ID，不填则自动推断 |

上传 ZIP 后**立即返回** `task_id`。任务进入 SQLite 队列，由后台 Worker 异步处理。

**示例：**
```bash
curl -X POST http://localhost:8440/tasks \
  -F "file=@page_2_mineru.zip" \
  -F "model=mineru" \
  -F "doc_id=page_2"
```

**响应（202 Accepted）：**
```json
{
  "task_id": "13a6fe195a844709",
  "status": "pending",
  "message": "Task submitted successfully"
}
```

---

### 4. 查询任务状态与进度（GET /tasks/{task_id}）

```
GET /tasks/{task_id}
```

返回异步任务的当前状态和进度。**轮询此接口**可实时追踪处理进度。

**示例：**
```bash
# 单次查询
curl -s http://localhost:8440/tasks/13a6fe195a844709 | python -m json.tool

# 轮询脚本（bash）
TASK_ID="13a6fe195a844709"
while true; do
  RESP=$(curl -s "http://localhost:8440/tasks/$TASK_ID")
  echo "$RESP" | python -c "import sys,json; d=json.load(sys.stdin); print(d['status'], d['progress'])"
  st=$(echo "$RESP" | python -c "import sys,json; print(json.load(sys.stdin)['status'])")
  [ "$st" = "completed" ] || [ "$st" = "failed" ] && break
  sleep 5
done
```

**响应（处理中）：**
```json
{
  "task_id": "13a6fe195a844709",
  "status": "processing",
  "progress": "[60%] Image-text association (1 chunks)",
  "created_at": "2026-07-02T15:10:37",
  "updated_at": "2026-07-02T15:13:00",
  "doc_id": "page_2",
  "model": "mineru",
  "error": null
}
```

**响应（已完成）：**
```json
{
  "task_id": "13a6fe195a844709",
  "status": "completed",
  "progress": "[100%] Processing completed (4 pages)",
  "created_at": "2026-07-02T15:10:37",
  "updated_at": "2026-07-02T15:14:00",
  "doc_id": "page_2",
  "model": "mineru",
  "error": null
}
```

#### 进度生命周期

| 百分比 | progress 示例 | 阶段 | 耗时特征 |
|--------|-------------|------|---------|
| — | `Task queued` | 入队等待 | 瞬间 |
| `[5%]` | `Normalizing labels...` | 标签归一化 | 1-2s |
| `[15%]` | `Labels normalized (4 pages), starting inference...` | 归一化完成 | 可观测 |
| `[20%]` | `Text truncation analysis (3 chunks)` | 文本截断分析 | 取决于 chunk 数 |
| `[40%]` | `Title hierarchy analysis (2 chunks)` | 标题层级分析 | 取决于 chunk 数 |
| `[60%]` | `Image-text association (1 chunks)` | 图文关联分析 | **最耗时阶段** |
| `[75%]` | `Image-text association complete` | 关联完成 | 瞬间 |
| `[85%]` | `Inference done (4 pages, 48 elements), building tree...` | 推理完成 | 可观测 |
| `[95%]` | `Saving result...` | 构建树 + 保存 | 瞬间 |
| `[100%]` | `Processing completed (4 pages)` | 完成 | 终态 |

> **注意**：快速阶段（<5s）可能在轮询间隔内被跳过，属于正常现象。

**状态机：**
```
pending → processing → completed
                     → failed
```

---

### 5. 获取任务结果（GET /tasks/{task_id}/result）

```
GET /tasks/{task_id}/result
```

返回最终的文档树。仅在任务达到 `completed` 状态后可用。

**示例：**
```bash
curl -s http://localhost:8440/tasks/13a6fe195a844709/result | python -m json.tool
```

**响应（已完成）：**
```json
{
  "task_id": "13a6fe195a844709",
  "status": "completed",
  "result": {
    "doc_id": "page_2",
    "status": "success",
    "message": "Document processed successfully",
    "tree": {
      "type": "root",
      "level": 0,
      "children": [
        {
          "type": "text",
          "title": "Default Title",
          "content": "京沪高铁 ↑ 股票代码 601816<|txt_split|>秋意漫卷...",
          "level": 1,
          "location": [{"bbox": [0.196, 0.866, 0.298, 0.91], "page": 1}],
          "block_ids": [1, 2, 3]
        }
      ]
    }
  },
  "error": null
}
```

**响应（处理中）：**
```json
{
  "task_id": "13a6fe195a844709",
  "status": "processing",
  "error": "Task is still processing"
}
```

**响应（排队中）：**
```json
{
  "task_id": "13a6fe195a844709",
  "status": "pending",
  "error": "Task is still pending"
}
```

---

### 6. JSON 输入（同步）

```
POST /process/json
Content-Type: application/json
```

直接提交已归一化的页面数据（跳过标签归一化步骤）。

**示例：**
```bash
curl -X POST http://localhost:8440/process/json \
  -H "Content-Type: application/json" \
  -d '{
    "doc_id": "mydoc",
    "model": "mineru",
    "pages": {
      "1": [
        {"type": "title", "content": "第一章", "bbox": [0.1, 0.1, 0.5, 0.15]}
      ]
    }
  }'
```

---

## 文档树节点结构

`tree` 中每个节点包含 8 个字段：

| 字段 | 类型 | 说明 |
|-------|------|-------------|
| `type` | string | 节点类型：`root`、`text`、`table`、`image`、`page_number`、`header`、`footer` 等 |
| `title` | string | 章节标题，未检测到时为 `"Default Title"` |
| `metadata` | string | 脚注、补充信息 |
| `content` | string | 正文内容，段落间以 `<\|txt_split\|>` 或 `<\|txt_contd\|>` 分隔 |
| `level` | int | 标题层级（1=一级标题, 2=二级标题…），-1 表示非标题元素 |
| `location` | array | `[{bbox: [x1,y1,x2,y2], page: N}]` — 归一化坐标（0-1） |
| `block_ids` | array | 可追溯到原始 OCR 输出的块 ID |
| `children` | array | 子节点（递归同构） |

## ZIP 文件结构

ZIP 应包含各模型对应格式的 OCR 输出。`doc_id` 从 ZIP 内顶层目录名自动推断（回退到 ZIP 文件名）。

### mineru

新版 MinerU 使用 `hybrid_auto/`，旧版使用 `vlm/`，两者均自动探测。

```
{zip_root}/
└── {doc_id}/
    └── hybrid_auto/          ← 或 vlm/（自动探测）
        ├── {doc_id}_model.json     ← 优先使用
        ├── {doc_id}_middle.json    ← 回退
        ├── {doc_id}_content_list.json
        ├── {doc_id}_origin.pdf     ← VLM 页面渲染（可选）
        ├── {doc_id}_layout.pdf
        └── images/
            └── *.jpg
```

**实测示例：**
```
page_2_mineru.zip
└── page_2/
    └── hybrid_auto/
        ├── page_2_model.json
        ├── page_2_middle.json
        ├── page_2_content_list.json
        ├── page_2_origin.pdf
        ├── page_2_layout.pdf
        └── images/（27 个 jpg 文件）
```

### 其他模型

| 模型 | 关键文件 |
|-------|----------|
| `monkeyocr` | `{doc_id}_middle.json` |
| `PaddleOCR-VL-1.5` | `layout_parsing.json` 或 `{doc_id}_*_res.json` |
| `dolphin` | `recognition_json/{doc_id}.json` |
| `glm-ocr` | `{doc_id}_model.json` 或 `page_*.json` |

## 架构

```
客户端 → FastAPI → SQLite 队列 → Worker → SQLite 结果
   │              │              │
   │          任务状态       处理流水线
   │          任务结果
   │
   └→ 同步响应（/process）
```

## 数据库结构

SQLite 数据库（`popo_tasks.db`）包含单个 `tasks` 表：

```sql
CREATE TABLE tasks (
    task_id TEXT PRIMARY KEY,
    doc_id TEXT NOT NULL,
    model TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    progress TEXT DEFAULT '',
    file_name TEXT DEFAULT '',
    work_dir TEXT DEFAULT '',
    result TEXT DEFAULT '',       -- 处理结果的 JSON 字符串
    error TEXT DEFAULT '',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

可直接查看数据库：
```bash
sqlite3 data/popo_tasks.db "SELECT task_id, status, progress FROM tasks;"
```

## 生产部署

生产环境建议：

1. **独立 Worker 进程**：将 Worker 作为独立进程运行：
   ```bash
   python -c "from api.services.worker import run_worker; run_worker('worker-1')"
   ```

2. **任务清理**：根据 `POPO_TASK_TTL` 自动清理过期的已完成/失败任务

3. **数据库备份**：SQLite 是单文件，备份简单：
   ```bash
   cp data/popo_tasks.db data/popo_tasks.db.backup
   ```
