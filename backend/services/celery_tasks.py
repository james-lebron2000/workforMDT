"""Celery 异步任务 - 三类:
- ocr_task(medical_record_id):拉 MinIO 文件 → OCR 服务 → LLM 抽结构化 → 写库
- asr_task(voice_note_id):拉音频 → ASR 服务 → 写库
- mdt_analysis_task(session_id):串联 04/05/06/07 Agent,产 TNM+科室意见+建议+QC

通过 sse_publisher 推进度;失败重试 3 次,DLQ 兜底。
"""
from __future__ import annotations

import traceback
from typing import Any, Dict

from celery import shared_task
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from config import settings
from models.opinion import DepartmentOpinion
from models.record import MedicalRecord
from models.recommendation import FinalRecommendation
from models.session import MdtSession
from models.summary import CaseSummary
from models.tnm import TnmStaging
from models.voice import VoiceNote
from services import infer_client, minio_client, sse_publisher
from utils.logger import get_logger

logger = get_logger("celery_tasks")


# 同步 Engine - Celery worker 用
_sync_engine = create_engine(
    settings.sync_database_url, pool_pre_ping=True, pool_size=5, max_overflow=5
)
SyncSession = sessionmaker(bind=_sync_engine, expire_on_commit=False)


def _publish(session_id: str, stage: str, percent: int, message: str, **extra: Any):
    try:
        sse_publisher.publish(session_id, stage, percent, message, extra=extra or None)
    except Exception as e:  # noqa: BLE001
        logger.warning("publish_failed", error=str(e))


_MIME_BY_EXT = {
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "png": "image/png",
    "webp": "image/webp",
    "gif": "image/gif",
    "bmp": "image/bmp",
    "heic": "image/heic",
}
_OCR_MAX_PDF_PAGES = 10  # 单份 PDF 限页,防止 token 爆炸


def _prepare_images_for_vision(
    file_bytes: bytes, file_key: str, file_type: str | None
) -> list[tuple[bytes, str]]:
    """把 MinIO 文件转成视觉 LLM 能吃的 [(bytes, mime), ...]。

    - PDF: PyMuPDF 拆页 → 200 DPI PNG(最多 _OCR_MAX_PDF_PAGES 页)
    - 图片: 按扩展名识别 mime,原字节透传
    - 未识别扩展: 默认按 JPEG 处理(LLM 一般能解码常见格式)
    """
    ext = (
        file_key.lower().rsplit(".", 1)[-1].strip()
        if "." in file_key
        else ""
    )

    if (file_type or "").lower() == "pdf" or ext == "pdf":
        import fitz  # PyMuPDF

        doc = fitz.open(stream=file_bytes, filetype="pdf")
        try:
            pages: list[tuple[bytes, str]] = []
            for i, page in enumerate(doc):
                if i >= _OCR_MAX_PDF_PAGES:
                    logger.warning(
                        "ocr_pdf_page_limit_hit",
                        file_key=file_key,
                        total=doc.page_count,
                        kept=_OCR_MAX_PDF_PAGES,
                    )
                    break
                pix = page.get_pixmap(dpi=200)
                pages.append((pix.tobytes("png"), "image/png"))
            return pages
        finally:
            doc.close()

    mime = _MIME_BY_EXT.get(ext, "image/jpeg")
    return [(file_bytes, mime)]


@shared_task(name="tasks.ocr_task", bind=True, max_retries=3, default_retry_delay=10)
def ocr_task(self, medical_record_id: str) -> Dict[str, Any]:
    """单个资料文件 OCR + 结构化。

    两段式(V2 默认):
      a) 火山引擎通用文字识别 (general_basic) → raw_text
         - 图片直接送;PDF 用 PyMuPDF 拆页后逐页 OCR
      b) 豆包 LLM(或 fallback)对 raw_text 做结构化抽取 → OcrExtraction

    备选路径(V2-vision):多模态 LLM 一次性识图+结构化,见 run_ocr_vision_agent;
    通过设置 OCR_BACKEND=vision_llm 切换(暂未实现,代码已就位)。
    """
    from agents.agent_01_ocr import run_ocr_agent
    from services import volcengine_ocr

    with SyncSession() as db:
        record: MedicalRecord | None = db.get(MedicalRecord, medical_record_id)
        if record is None:
            logger.warning("ocr_task_record_missing", id=medical_record_id)
            return {"ok": False, "error": "record_not_found"}
        session_id = str(record.session_id)
        _publish(session_id, "ocr", 5, f"开始解析资料 {record.file_key}")
        try:
            record.ocr_status = "processing"
            db.commit()

            file_bytes = minio_client.get_object_bytes(record.file_key)
            _publish(session_id, "ocr", 20, "读取文件,准备 OCR")

            pages = _prepare_images_for_vision(
                file_bytes, record.file_key, record.file_type
            )
            _publish(
                session_id,
                "ocr",
                40,
                f"调用火山 OCR ({len(pages)} 页)",
            )

            ocr_result = volcengine_ocr.ocr_pages(pages)
            raw_text: str = ocr_result.get("raw_text", "")
            _publish(
                session_id,
                "ocr",
                70,
                f"OCR 完成({len(raw_text)} 字),调 LLM 抽结构化字段",
            )

            structured = run_ocr_agent(raw_text)  # 文本路径 LLM,内部已脱敏 + restore
            _publish(session_id, "ocr", 88, "结构化完成,保存到 MinIO/DB")

            # raw_text 大文本写 MinIO,DB 只存 key(后续下游 agent 拉出后再过 PII scrub)
            raw_text_key = f"sessions/{session_id}/raw_texts/{record.id}.txt"
            minio_client.put_object(
                raw_text_key, raw_text.encode("utf-8"), content_type="text/plain"
            )
            record.raw_text_key = raw_text_key
            record.structured = structured
            # 平均火山 general_basic 在清晰扫描件上准确率 >95%;粗略给 0.9
            record.confidence = 0.9
            record.ocr_status = "done"
            db.commit()

            _publish(
                session_id,
                "ocr",
                100,
                "资料解析完成",
                record_id=str(record.id),
                n_pages=ocr_result.get("n_pages"),
            )
            return {
                "ok": True,
                "record_id": str(record.id),
                "n_pages": ocr_result.get("n_pages"),
            }
        except Exception as e:  # noqa: BLE001
            db.rollback()
            record = db.get(MedicalRecord, medical_record_id)
            if record is not None:
                record.ocr_status = "failed"
                db.commit()
            logger.error(
                "ocr_task_failed",
                id=medical_record_id,
                error=str(e),
                tb=traceback.format_exc()[-800:],
            )
            _publish(session_id, "ocr", -1, f"OCR 失败: {e}")
            try:
                raise self.retry(exc=e)
            except self.MaxRetriesExceededError:
                return {"ok": False, "error": str(e)}


@shared_task(name="tasks.asr_task", bind=True, max_retries=3, default_retry_delay=15)
def asr_task(self, voice_note_id: str) -> Dict[str, Any]:
    """单条录音的 ASR(含说话人分离)。"""
    with SyncSession() as db:
        voice: VoiceNote | None = db.get(VoiceNote, voice_note_id)
        if voice is None:
            return {"ok": False, "error": "voice_not_found"}
        session_id = str(voice.session_id)
        _publish(session_id, "asr", 5, f"开始转写录音 ({voice.voice_type})")

        try:
            voice.asr_status = "processing"
            db.commit()

            audio_bytes = minio_client.get_object_bytes(voice.file_key)
            _publish(session_id, "asr", 20, "调用 ASR 服务")

            hotwords = _load_hotwords()
            # ASR provider 路由 — 默认走火山豆包音频理解(去 GPU 节点);
            # 如需切回自部署 FunASR,设 ASR_PROVIDER=funasr 并填 asr_service_url
            from config import settings as _settings

            if (_settings.asr_provider or "volcengine").lower() == "funasr":
                result = infer_client.asr_transcribe(
                    audio_bytes,
                    filename=voice.file_key,
                    hotwords=hotwords,
                    enable_diarization=(voice.voice_type == "mdt_discussion"),
                )
            else:
                from services import volcengine_audio

                result = volcengine_audio.transcribe(
                    audio_bytes,
                    filename=voice.file_key,
                    voice_type=voice.voice_type,
                    hotwords=hotwords,
                    enable_diarization=(voice.voice_type == "mdt_discussion"),
                )

            voice.transcript = result.get("segments", [])
            voice.duration = result.get("duration")
            voice.asr_status = "done"
            db.commit()

            _publish(
                session_id,
                "asr",
                100,
                f"录音转写完成({len(voice.transcript or [])} 段)",
                voice_id=str(voice.id),
            )
            return {"ok": True, "voice_id": str(voice.id)}
        except Exception as e:  # noqa: BLE001
            db.rollback()
            voice = db.get(VoiceNote, voice_note_id)
            if voice is not None:
                voice.asr_status = "failed"
                db.commit()
            logger.error("asr_task_failed", id=voice_note_id, error=str(e))
            _publish(session_id, "asr", -1, f"ASR 失败: {e}")
            try:
                raise self.retry(exc=e)
            except self.MaxRetriesExceededError:
                return {"ok": False, "error": str(e)}


def _load_hotwords() -> list[str]:
    """医学热词:从 medical_terms 字典拉一部分高频名词喂给 ASR。

    - FunASR:作为 hotword 列表传给推理服务(上限 ~500 条)
    - 火山豆包音频理解:作为 prompt 中的"优先识别词"片段提示模型
    """
    from utils.medical_terms import TERMS_BY_CATEGORY

    keep = []
    for cat in ("化疗药物", "靶向药物", "免疫药物", "分子标志物"):
        keep.extend(TERMS_BY_CATEGORY.get(cat, []))
    return keep[:500]


@shared_task(name="tasks.summary_task", bind=True, max_retries=2, default_retry_delay=20)
def summary_task(self, session_id: str) -> Dict[str, Any]:
    """MDT 前的"病史汇总"轻量任务 - 只跑 case_summary agent。

    输入:已完成 OCR 的资料 + (可选) 已完成 ASR 的患者诉求录音
    输出:CaseSummary 行(version 累加)

    场景:医生拍完照片 + 询问完患者诉求 → 在与患者一起核对前先生成一版摘要
    """
    from agents.agent_03_case_summary import run_case_summary_agent

    with SyncSession() as db:
        sess: MdtSession | None = db.get(MdtSession, session_id)
        if sess is None:
            return {"ok": False, "error": "session_not_found"}

        try:
            _publish(session_id, "summary", 5, "整理病史摘要")
            if sess.status in ("draft",):
                sess.status = "collecting"
                db.commit()

            records = list(
                db.execute(
                    select(MedicalRecord)
                    .where(MedicalRecord.session_id == sess.id)
                    .where(MedicalRecord.ocr_status == "done")
                ).scalars()
            )
            voices = list(
                db.execute(
                    select(VoiceNote)
                    .where(VoiceNote.session_id == sess.id)
                    .where(VoiceNote.voice_type == "patient_request")
                    .where(VoiceNote.asr_status == "done")
                ).scalars()
            )

            patient_request_text = ""
            for v in voices:
                patient_request_text += " ".join(
                    seg.get("text", "") for seg in (v.transcript or [])
                )

            ocr_texts: list[str] = []
            for r in records:
                if r.raw_text_key:
                    try:
                        ocr_texts.append(
                            minio_client.get_object_bytes(r.raw_text_key).decode("utf-8")
                        )
                    except Exception as e:  # noqa: BLE001
                        logger.warning(
                            "raw_text_fetch_failed", record_id=str(r.id), error=str(e)
                        )

            _publish(session_id, "summary", 40, "调 LLM 抽取摘要 + 治疗时间轴")
            summary_payload = run_case_summary_agent(
                ocr_texts=ocr_texts,
                patient_request_text=patient_request_text,
                structured_records=[r.structured for r in records if r.structured],
            )

            # 取最新版本号 +1
            latest = db.execute(
                select(CaseSummary)
                .where(CaseSummary.session_id == sess.id)
                .order_by(CaseSummary.version.desc())
            ).scalar_one_or_none()
            next_version = (latest.version + 1) if latest else 1

            cs = CaseSummary(
                session_id=sess.id,
                chief_need=summary_payload.chief_need,
                history_summary=summary_payload.history_summary,
                treatment_timeline=[t.model_dump() for t in summary_payload.treatment_timeline],
                current_problem=summary_payload.current_problem,
                mdt_questions=summary_payload.mdt_questions,
                version=next_version,
            )
            db.add(cs)
            db.commit()

            _publish(session_id, "summary", 100, "病史摘要已生成,请与患者核对")
            return {"ok": True, "version": next_version}
        except Exception as e:  # noqa: BLE001
            db.rollback()
            logger.error(
                "summary_task_failed",
                session_id=session_id,
                error=str(e),
                tb=traceback.format_exc()[-800:],
            )
            _publish(session_id, "summary", -1, f"摘要生成失败: {e}")
            try:
                raise self.retry(exc=e)
            except self.MaxRetriesExceededError:
                return {"ok": False, "error": str(e)}


@shared_task(name="tasks.mdt_analysis_task", bind=True, max_retries=2, default_retry_delay=20)
def mdt_analysis_task(self, session_id: str) -> Dict[str, Any]:
    """MDT 主分析任务 - 串 04/05/06/07 Agent。

    前置条件:
    - 至少一条 voice_type=mdt_discussion 的 voice_note.asr_status=done
    - 至少一条 case_summaries(否则降级用 OCR raw_text)
    """
    from agents.agent_03_case_summary import run_case_summary_agent
    from agents.agent_04_tnm import run_tnm_agent
    from agents.agent_05_mdt_opinion import run_mdt_opinion_agent
    from agents.agent_06_recommendation import run_recommendation_agent
    from agents.agent_07_qc import run_qc_agent

    with SyncSession() as db:
        sess: MdtSession | None = db.get(MdtSession, session_id)
        if sess is None:
            return {"ok": False, "error": "session_not_found"}

        try:
            sess.status = "analyzing"
            db.commit()
            _publish(session_id, "analyze", 2, "开始 MDT 综合分析")

            records = list(
                db.execute(
                    select(MedicalRecord)
                    .where(MedicalRecord.session_id == sess.id)
                    .where(MedicalRecord.ocr_status == "done")
                ).scalars()
            )
            voices = list(
                db.execute(
                    select(VoiceNote)
                    .where(VoiceNote.session_id == sess.id)
                    .where(VoiceNote.asr_status == "done")
                ).scalars()
            )

            patient_request_text = ""
            mdt_segments: list[dict] = []
            for v in voices:
                if v.voice_type == "patient_request":
                    patient_request_text = " ".join(
                        seg.get("text", "") for seg in (v.transcript or [])
                    )
                elif v.voice_type == "mdt_discussion":
                    mdt_segments.extend(v.transcript or [])

            ocr_texts: list[str] = []
            for r in records:
                if r.raw_text_key:
                    try:
                        ocr_texts.append(
                            minio_client.get_object_bytes(r.raw_text_key).decode("utf-8")
                        )
                    except Exception as e:  # noqa: BLE001
                        logger.warning(
                            "raw_text_fetch_failed", record_id=str(r.id), error=str(e)
                        )

            # Step 1: case summary (若未有,生成;有则取最新)
            _publish(session_id, "analyze", 10, "生成病例摘要")
            existing_summary = db.execute(
                select(CaseSummary)
                .where(CaseSummary.session_id == sess.id)
                .order_by(CaseSummary.version.desc())
            ).scalar_one_or_none()

            if existing_summary is None:
                summary_payload = run_case_summary_agent(
                    ocr_texts=ocr_texts,
                    patient_request_text=patient_request_text,
                    structured_records=[r.structured for r in records if r.structured],
                )
                cs = CaseSummary(
                    session_id=sess.id,
                    chief_need=summary_payload.chief_need,
                    history_summary=summary_payload.history_summary,
                    treatment_timeline=[t.model_dump() for t in summary_payload.treatment_timeline],
                    current_problem=summary_payload.current_problem,
                    mdt_questions=summary_payload.mdt_questions,
                    version=1,
                )
                db.add(cs)
                db.commit()
                summary_dict = summary_payload.model_dump()
            else:
                summary_dict = {
                    "chief_need": existing_summary.chief_need,
                    "history_summary": existing_summary.history_summary,
                    "treatment_timeline": existing_summary.treatment_timeline,
                    "current_problem": existing_summary.current_problem,
                    "mdt_questions": existing_summary.mdt_questions,
                }

            # Step 2: TNM
            _publish(session_id, "analyze", 30, "推断 TNM 分期")
            tnm_payload = run_tnm_agent(
                ocr_texts=ocr_texts,
                case_summary=summary_dict,
            )
            tnm_row = TnmStaging(
                session_id=sess.id,
                tnm_type=tnm_payload.tnm_type,
                t_stage=tnm_payload.t_stage,
                n_stage=tnm_payload.n_stage,
                m_stage=tnm_payload.m_stage,
                overall_stage=tnm_payload.overall_stage,
                basis=tnm_payload.basis,
                uncertainty=tnm_payload.uncertainty,
                confidence=tnm_payload.confidence,
                version=1,
            )
            db.add(tnm_row)
            db.commit()

            # Step 3: MDT opinions
            _publish(session_id, "analyze", 55, "按科室提炼意见")
            opinions = run_mdt_opinion_agent(
                segments=mdt_segments,
                case_summary=summary_dict,
            )
            conf_map = {"high": 0.9, "medium": 0.6, "low": 0.3}
            for op in opinions:
                row = DepartmentOpinion(
                    session_id=sess.id,
                    department=op.department,
                    doctor_label=op.doctor_label,
                    opinion=op.opinion,
                    rationale=op.rationale,
                    recommendation=op.recommendation,
                    evidence_source=op.evidence_source,
                    evidence_snippet=op.evidence_snippet,
                    confidence=conf_map.get(op.confidence, 0.3),
                    is_missing=op.is_missing,
                )
                db.add(row)
            db.commit()

            # Step 4: final recommendation
            _publish(session_id, "analyze", 78, "综合临床判断和治疗建议")
            final = run_recommendation_agent(
                case_summary=summary_dict,
                tnm=tnm_payload.model_dump(),
                opinions=[o.model_dump() for o in opinions],
            )
            fr = FinalRecommendation(
                session_id=sess.id,
                clinical_judgment=final.clinical_judgment,
                exam_recommendations=[e.model_dump() for e in final.suggested_exams],
                treatment_recommendations=[t.model_dump() for t in final.treatment_plan],
                referral=[r.model_dump() for r in final.referral],
                patient_script=final.patient_script,
                qc_status="pending",
                version=1,
            )
            db.add(fr)
            db.commit()

            # Step 5: QC
            _publish(session_id, "analyze", 92, "运行 QC 终检")
            qc_report = run_qc_agent(
                case_summary=summary_dict,
                tnm=tnm_payload.model_dump(),
                opinions=[o.model_dump() for o in opinions],
                final=final.model_dump(),
                source_texts=ocr_texts
                + [seg.get("text", "") for seg in mdt_segments]
                + [patient_request_text],
            )
            fr.qc_status = "passed" if qc_report.passed else "blocked"
            fr.qc_issues = [i.model_dump() for i in qc_report.issues]
            sess.status = "reviewing"
            db.commit()

            _publish(
                session_id,
                "analyze",
                100,
                f"分析完成,QC {fr.qc_status}",
                qc_issues=len(qc_report.issues),
            )
            return {
                "ok": True,
                "qc_status": fr.qc_status,
                "qc_issues": len(qc_report.issues),
            }
        except Exception as e:  # noqa: BLE001
            db.rollback()
            sess = db.get(MdtSession, session_id)
            if sess is not None:
                sess.status = "collecting"  # 退回上一状态,允许重试
                db.commit()
            logger.error(
                "mdt_task_failed",
                session_id=session_id,
                error=str(e),
                tb=traceback.format_exc()[-800:],
            )
            _publish(session_id, "analyze", -1, f"分析失败: {e}")
            try:
                raise self.retry(exc=e)
            except self.MaxRetriesExceededError:
                return {"ok": False, "error": str(e)}
