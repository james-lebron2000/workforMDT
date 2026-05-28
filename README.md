<div align="center">

# TumorBoard AI

**肿瘤多学科智能会诊助手 · AI-assisted MDT Report Generator**

[中文](#中文版) · [English](#english) · [部署 / Deployment](#部署-deployment)

⚠️ **AI 辅助生成,需主治医师复核 · AI-assisted, requires attending physician review**

</div>

---

## 中文版

> **医生个人辅助工具** — 用手机拍照 + 录音,自动生成 12 节 MDT(多学科会诊)结构化报告。
> 不接 HIS,不进医院内网。AI 辅助生成,需主治医师复核。

### 设计目标

医生只做 4 步:**上传资料 → 上传录音 → AI 生成 → 医生确认 → 导出报告**

适用对象:中国肿瘤科主治医师(MDT 协调人),整合外科 / 肿瘤内科 / 放射科 / 放疗科 / 介入科 / 病理科 6 个核心科室的意见。

### 产品红线

| 约束 | 实现 |
|---|---|
| **不接触 HIS / 院内网** | 患者代号匿名识别,数据仅 PWA + 自部署后端 |
| **不存真实姓名** | 患者代号必填、姓名空字段;PII 入 LLM 前闭包脱敏 |
| **绝不伪造科室意见** | 录音未出现 → `is_missing=true` 显式标记"未明确记录" |
| **TNM 必给依据** | `basis` / `uncertainty` pydantic 必填;空值降级 Tx |
| **治疗建议必须复核** | 每条 `needs_doctor_confirm=true`,导出件硬编码水印 |
| **患者话术不承诺疗效** | QC Agent 关键词扫描("治愈/一定/保证/百分百/包治/永不复发"),命中即重生成 |
| **PII 脱敏闭包安全** | mapping 仅在单次 LLM 调用闭包内,不入 Redis/全局,1000 并发零串台 |
| **同意书门禁** | 未签 `policy_version=v1.1` 禁所有上传/录音/AI 生成,后端 403 |
| **30 / 365 天清理** | MinIO Lifecycle 自动归档 + 彻底删除 |
| **病史先核对再开 MDT** | `status >= summary_confirmed` 才能开 MDT 录音 |

### 架构

```
┌──────────────────────────────────────────────────────┐
│  Frontend - Next.js 14 PWA(微信内置浏览器友好)       │
│   /cases  /cases/[id]/{upload,review,export}          │
└──────────────────────┬───────────────────────────────┘
                       │ HTTPS / SSE
┌──────────────────────┴───────────────────────────────┐
│  Backend - FastAPI (8000)                             │
│   7 Agents + PII 脱敏 + QC + 同意书门禁                │
│   Celery workers: ocr / asr / mdt 三队列              │
└──┬─────────┬───────────┬──────────┬──────────────────┘
   │         │           │          │            │
 Postgres  Redis       MinIO    火山 OCR      火山豆包
 (业务表) (队列+SSE)  (原文件)  general_     LLM + ASR
                              basic        (OpenAI 兼容)
```

**7 Agent 串联**:OCR → Case Summary → 患者核对 → ASR(火山豆包音频理解)→ MDT Opinion(科室归类 + 意见提炼)→ TNM + Recommendation → QC 终检

### 技术栈

| 层 | 选型 |
|---|---|
| 前端 | Next.js 14 + Tailwind + PWA |
| 后端 | FastAPI + Celery + SQLAlchemy 2 |
| 数据库 | PostgreSQL 16 |
| 队列 / SSE | Redis 7 |
| 对象存储 | MinIO(S3 兼容) |
| OCR | 火山引擎 general_basic(AK/SK) |
| ASR | 火山引擎豆包音频理解 API(默认)/ FunASR(可选自部署) |
| LLM | 豆包 / 通义 / Kimi / Claude / GPT(OpenAI 兼容,一键切换) |
| 反向代理 | Caddy(自动 HTTPS) |
| 部署 | Docker Compose,单机 4C8G 起 |

### 目录结构

```
MDT/
├── docker-compose.yml          # 业务节点:Postgres/Redis/MinIO/Backend/3xWorker/Frontend
├── docker-compose.gpu.yml      # 自部署 GPU 节点(可选)
├── .env.example                # 环境变量模板
├── Caddyfile.example           # 反向代理样板
├── frontend/                   # Next.js PWA
├── backend/                    # FastAPI + Celery + 7 Agents
│   ├── routers/                # auth / sessions / files / audio / jobs / report / consent / health
│   ├── agents/                 # 7 agents (ocr / case_summary / tnm / mdt_opinion / recommendation / qc)
│   ├── services/               # llm_client / minio / volcengine_ocr / volcengine_audio / export
│   ├── utils/                  # pii_scrubber / medical_terms / hallucination_detector
│   ├── prompts/v1/             # 版本化 prompt
│   └── tests/                  # pytest 套件(含 1000 并发脱敏测)
├── ai-services/                # 可选自部署 OCR / ASR
├── templates/                  # docx / html / pptx 报告模板
└── docs/                       # 隐私政策 / 部署 / QA checklist
```

### 红线复核要点

1. **`.env` 永远不提交**(已 `.gitignore`),仅 `.env.example` 进库
2. 任何 LLM 调用前 `scrub_session()` 脱敏,返回后 restore 立即丢弃 mapping
3. 报告 docx/pdf/pptx 强制页眉页脚水印"AI 辅助,需主治医师复核"
4. `qc_status=failed` 阻止导出(`routers/report.py` HTTP 400)
5. 火山引擎 API 服务条款约定:不留存原音频/原图,不用于训练

---

## English

> **Personal assistant for oncology physicians** — Snap photos + record on phone, auto-generates a 12-section MDT (Multi-Disciplinary Team) report.
> Does **not** connect to hospital HIS / EMR / PACS. AI-assisted, requires attending physician review.

### Goal

Doctor's workflow is **4 steps only**: Upload materials → Record audio → AI generation → Doctor confirms → Export report.

Target users: oncology physicians in China (MDT coordinators) who consolidate opinions from 6 core departments — Surgery / Medical Oncology / Radiology / Radiotherapy / Interventional Radiology / Pathology.

### Product Red Lines

| Constraint | Implementation |
|---|---|
| **No HIS / hospital network access** | Patient codes only; data lives in PWA + self-hosted backend |
| **No real patient names stored** | Patient code required; name field always empty; PII scrubbed before LLM |
| **Never fabricate department opinions** | If absent from recording → `is_missing=true`, explicit "not on record" marker |
| **TNM must have basis** | `basis` / `uncertainty` required by pydantic; empty falls back to `Tx` |
| **Treatment plan requires doctor confirm** | Every recommendation tagged `needs_doctor_confirm=true`, watermarked in exports |
| **No outcome promises in patient script** | QC Agent scans for banned terms (cure/guaranteed/100%/never recurs); regenerates on hit |
| **PII scrub closure-safe** | Mapping lives only in single LLM call closure; never in Redis/global; 1000-concurrent test passes |
| **Consent gate** | Without `policy_version=v1.1`, all upload/record/AI endpoints return 403 |
| **30/365-day retention** | MinIO Lifecycle auto-archive + hard delete |
| **History must be patient-confirmed before MDT** | `status >= summary_confirmed` is gate for MDT recording |

### Architecture

Same diagram as Chinese section above. In short:

- **Frontend**: Next.js 14 PWA, WeChat in-browser friendly
- **Backend**: FastAPI + 7 chained agents + PII scrubber + QC
- **Workers**: 3 Celery queues (ocr / asr / mdt)
- **Storage**: PostgreSQL (business data), Redis (queue/SSE), MinIO (files)
- **AI**: Volcengine `general_basic` OCR + Doubao audio-understanding ASR + Doubao Seed LLM (OpenAI-compatible, hot-swappable to Qwen/Kimi/Claude/GPT)

### Tech Stack

| Layer | Choice |
|---|---|
| Frontend | Next.js 14 + Tailwind + PWA |
| Backend | FastAPI + Celery + SQLAlchemy 2 |
| Database | PostgreSQL 16 |
| Queue / SSE | Redis 7 |
| Object Storage | MinIO (S3-compatible) |
| OCR | Volcengine `general_basic` (AK/SK) |
| ASR | Volcengine Doubao audio-understanding API (default) / FunASR (optional self-hosted) |
| LLM | Doubao / Qwen / Kimi / Claude / GPT (OpenAI-compatible, single env-var swap) |
| Reverse Proxy | Caddy (auto-HTTPS) |
| Deployment | Docker Compose, 4C8G single-node minimum |

### Security & Compliance Highlights

- `.env` **never committed** (gitignored), only `.env.example` in repo
- PII scrub happens before any LLM call, restore immediately after, mapping discarded
- All exported reports have hard-coded "AI-assisted, requires physician review" watermarks
- QC failures (`qc_status=failed`) block exports at backend (HTTP 400)
- Volcengine terms: no retention of raw audio/image, no training use

---

## 部署 / Deployment

> Production deployment target: **single Linux VM (≥4C8G, 60GB SSD)** with Docker + Caddy.
> 已在 **腾讯云 Ubuntu 24.04, 4C8G** 验证可跑(2-3 个并发会话,30 分钟录音流畅)。

### Prerequisites · 准备

| Item | Requirement |
|---|---|
| Server / 服务器 | Ubuntu 22.04+ or Debian 12+, 4C8G, ≥60GB disk |
| Domain / 域名 | 已备案(如国内服务器);DNS 可解析两个子域名 |
| Ports / 端口 | 80, 443 对外开放(安全组 + 系统防火墙) |
| Volcengine API | AK/SK + Doubao API Key([方舟控制台](https://console.volcengine.com/ark)) |

### 1. DNS · 域名解析

在域名管理面板(阿里云 / 腾讯云 DNSPod / Cloudflare 任一)加 2 条 A 记录:

| 类型 | 主机记录 | 解析值 |
|---|---|---|
| A | `mdt` | `<服务器公网 IP>` |
| A | `minio` | `<服务器公网 IP>` |

例:`mdt.inseq.top` + `minio.inseq.top`。

### 2. Server Setup · 服务器初始化

```bash
ssh ubuntu@<your-server-ip>

# Install Docker & Caddy (Ubuntu 22.04+)
sudo apt update
sudo apt install -y docker.io docker-compose-plugin debian-keyring debian-archive-keyring apt-transport-https
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | sudo tee /etc/apt/sources.list.d/caddy-stable.list
sudo apt update && sudo apt install -y caddy

# Firewall
sudo ufw allow 80 && sudo ufw allow 443

# Clone repo
sudo mkdir -p /opt/mdt && sudo chown $USER /opt/mdt
cd /opt/mdt
git clone https://github.com/james-lebron2000/workforMDT.git .
```

### 3. Configure `.env` · 配置环境变量

```bash
cp .env.example backend/.env
nano backend/.env
```

**必填项 / Required**:

```env
# 强随机生成 / generate via: openssl rand -hex 32
APP_SECRET=<64-char-hex>
POSTGRES_PASSWORD=<random>
MINIO_ACCESS_KEY=<random>
MINIO_SECRET_KEY=<random>

# 火山引擎 OCR (https://console.volcengine.com/iam/keymanage)
VOLCENGINE_AK=<your-volcengine-ak>
VOLCENGINE_SK=<your-volcengine-sk>

# 豆包 LLM + 音频理解 (https://console.volcengine.com/ark)
DOUBAO_API_KEY=<your-doubao-ark-key>
DOUBAO_MODEL=doubao-seed-2-0-pro-260215
DOUBAO_AUDIO_MODEL=doubao-seed-2.0-lite

# 公网 endpoint(改成你的子域名)
MINIO_PUBLIC_ENDPOINT=https://minio.<your-domain>
APP_ENV=production
```

### 4. Caddy 反向代理 · Reverse Proxy

```bash
sudo cp /opt/mdt/Caddyfile.example /etc/caddy/Caddyfile
sudo nano /etc/caddy/Caddyfile
# 修改 mdt.inseq.top / minio.inseq.top 为你的实际域名
sudo systemctl reload caddy
sudo journalctl -u caddy -f
# 等到看到 "certificate obtained successfully" 就 OK
```

### 5. Start Services · 启动服务

```bash
cd /opt/mdt
echo "NEXT_PUBLIC_API_BASE=https://mdt.<your-domain>" > frontend/.env.production
docker compose up -d --build
docker compose logs -f backend
# 等到看到 "Uvicorn running on http://0.0.0.0:8000" 即 OK
```

### 6. Verify · 验收 5 连

```bash
dig mdt.<your-domain> +short                                # DNS 通了
curl -I https://mdt.<your-domain>                           # HTTPS OK
curl https://mdt.<your-domain>/health                       # backend 活着(浅)
curl https://mdt.<your-domain>/health/deep | jq             # PG/Redis/MinIO/LLM/火山 全绿
curl -I https://minio.<your-domain>/minio/health/live       # MinIO OK
# 浏览器开 https://mdt.<your-domain>/diagnostics — 7 项全绿(网络/后端/麦克风/编码/WakeLock/IndexedDB/浏览器)
```

### 7. Day-0 Operations

| Task | Command |
|---|---|
| 查看日志 | `docker compose logs -f --tail=200 backend worker-mdt` |
| 数据库备份 | `docker compose exec postgres pg_dump -U tumorboard tumorboard \| gzip > backup-$(date +%F).sql.gz` |
| 拉新代码 | `cd /opt/mdt && git pull && docker compose up -d --build` |
| 重启单服务 | `docker compose restart backend` |
| 数据库迁移 | `docker compose exec backend alembic upgrade head` |

---

## Local Development · 本地开发

### Backend

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
# 起 Postgres/Redis/MinIO 后:
alembic upgrade head
uvicorn main:app --reload --port 8000
# 另开终端跑 worker
celery -A services.celery_app:celery_app worker -Q ocr,asr,mdt --loglevel=info
```

### Frontend

```bash
cd frontend
npm install
NEXT_PUBLIC_API_BASE=http://localhost:8000 npm run dev
# http://localhost:3000
```

### Tests

```bash
cd backend
pytest -v
# 重点测试:
#  tests/test_pii_concurrent.py — 1000 并发脱敏零串台
#  tests/test_qc_agent.py       — 承诺词命中重生成
#  tests/test_tnm_schema.py     — 枚举强约束
```

---

## LLM Provider Switching · LLM 切换

```bash
# .env 改一行即可
LLM_PROVIDER=qwen                # doubao / qwen / kimi / claude / gpt
LLM_FALLBACK_PROVIDERS=kimi,doubao   # 失败自动降级链
docker compose restart backend worker-mdt
```

Each provider uses OpenAI-compatible `/chat/completions` (Claude uses the Anthropic SDK). Prompts in `backend/prompts/v1/` are version-controlled — switching providers does not require touching business code.

---

## Report Output · 报告输出

12 sections matching China MDT standard:

1. 患者基本信息 (代号) / Patient basics (code only)
2. 本次就诊需求与预期收获 / Current visit request
3. 病历摘要 / History summary
4. 既往治疗时间轴 / Treatment timeline (with evidence)
5. 当前临床判断 / Current clinical judgment
6. TNM 分期 + basis + uncertainty
7. 多学科医生意见 / 6 core dept opinions (absentees explicitly marked)
8. 检查建议 / Exam recommendations
9. 治疗建议 (标"需医生最终确认") / Treatment recs (with "physician confirm" tag)
10. 推荐就诊医生 / Referral
11. 患者反馈话术 (不承诺疗效) / Patient script (no outcome promises)
12. 待补充资料 + 医生审核意见 / Pending items + doctor's note

Export formats: **docx** (python-docx) / **pdf** (weasyprint) / **pptx** (python-pptx) / **WeChat card** (plain text).

---

## Risk Mitigation · 风险与缓解

| Risk | Mitigation |
|---|---|
| Speaker + dept misclassification | Manual edit in review page, one-click regenerate |
| LLM TNM hallucination | Pydantic enum constraint; `basis`/`evidence_snippet` required; confidence<0.7 highlighted |
| 30-min recording interrupted | MediaRecorder 90s chunking, live upload to MinIO, IndexedDB local fallback, recovery banner on next mount |
| PII scrub concurrency leak | Closure-only mapping; 1000-thread unit test passes |
| GPU single point of failure | OCR/ASR multi-replica with round-robin; ASR async retry 24h |

---

## Roadmap · 路线图

- **Phase 1 (MVP, current)**: Upload → record → AI → confirm → export full loop
- **Phase 2**: MDT decision database; RECIST assessment; imaging intelligent parsing
- **Phase 3**: Clinical trial matching; QC feedback training sample pool

---

## Legal Disclaimer · 法律声明

**This is NOT a medical device.** This tool is a personal workflow assistant for licensed physicians. All AI outputs are explicitly watermarked "AI-assisted, requires attending physician review". **All diagnostic and treatment decisions remain the sole responsibility of the licensed physician.** For use as a formal medical information system, Class II medical device filing and Cybersecurity Multi-Level Protection (等保) compliance are required.

**这不是医疗器械。** 本工具是医生个人工作流辅助,所有输出明确标注"AI 辅助生成,需主治医师复核"。**临床诊断与治疗决策权完全在持牌医师。** 如需作为正式医疗信息系统使用,需走二类医疗器械备案 + 等级保护流程。

---

## License

MIT for code · Medical content for evaluation only · Patient data ownership belongs to patients.

---

<div align="center">

**⚠️ AI 辅助生成,需主治医师复核 · AI-assisted, requires attending physician review**

Built with care for Chinese oncology MDT workflows.

</div>
