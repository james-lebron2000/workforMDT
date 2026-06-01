'use client'

import { useEffect, useState } from 'react'
import { useRouter, useParams } from 'next/navigation'
import { api } from '@/lib/api'
import ProgressStream from '@/components/ProgressStream'
import EditableCard from '@/components/EditableCard'
import EvidencePopover from '@/components/EvidencePopover'
import TNMStageCard from '@/components/TNMStageCard'
import DeptOpinionCard from '@/components/DeptOpinionCard'
import QCBanner from '@/components/QCBanner'
import type { StateEvent } from '@/lib/sse'
import { useToast } from '@/components/Toast'

export default function ReviewPage() {
  const router = useRouter()
  const toast = useToast()
  const params = useParams() as { id: string }
  const sessionId = params.id

  const [data, setData] = useState<any>(null)
  const [loading, setLoading] = useState(true)
  const [tick, setTick] = useState(0)

  async function reload() {
    setLoading(true)
    try {
      const d = await api.getSession(sessionId)
      setData(d)
    } catch (e: any) {
      console.error(e)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    reload()
  }, [sessionId, tick])

  // 30s 兜底轮询 — SSE 是主同步通道(ProgressStream onStateChange → reload)
  useEffect(() => {
    const t = setInterval(() => setTick((x) => x + 1), 30_000)
    return () => clearInterval(t)
  }, [])

  // SSE 状态事件 → reload。其他端编辑/重生成/删除都在这里收到。
  function onStateEvent(ev: StateEvent) {
    if (ev.kind === 'session_deleted') {
      toast.warn('该病例已被其他端删除')
      router.push('/cases')
      return
    }
    reload()
  }

  if (loading && !data) {
    return <div className="card text-center text-gray-500 py-8">加载中…</div>
  }
  if (!data) return null

  const sess = data.session || {}
  const summary = data.summary || {}
  const tnm = data.tnm
  const opinions: any[] = data.opinions || []
  const finalRec = data.final || {}
  const qc = finalRec.qc_status === 'failed' || (finalRec.qc_issues && finalRec.qc_issues.length > 0)
    ? { passed: false, issues: finalRec.qc_issues }
    : (finalRec.qc_status === 'passed' ? { passed: true, issues: [] } : null)

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <button
          className="text-sm text-gray-500 hover:text-gray-700"
          onClick={() => router.push(`/cases/${sessionId}/upload`)}
        >
          ← 返回上传
        </button>
        <button
          className="btn btn-primary"
          onClick={() => router.push(`/cases/${sessionId}/export`)}
        >
          导出报告 →
        </button>
      </div>

      <h1 className="text-lg font-semibold">
        {sess.title || `MDT-${sess.patient?.code}`}
      </h1>

      {sess.status === 'analyzing' ? (
        <div className="card">
          <h3 className="font-medium text-sm mb-2">🤖 AI 分析中</h3>
          <ProgressStream sessionId={sessionId} onStateChange={onStateEvent} />
        </div>
      ) : (
        // 即便不在 analyzing 状态也订阅 SSE — 多端同步必备(其他端编辑会经此触发 reload)
        // 但 UI 收起,只留细线状态条
        <div className="px-1">
          <ProgressStream sessionId={sessionId} onStateChange={onStateEvent} />
        </div>
      )}

      <QCBanner qc={qc} />

      {/* 1. 患者诉求 */}
      <EditableCard
        sessionId={sessionId}
        title="① 患者诉求与本次预期"
        fieldPath="summary.chief_need"
        value={summary.chief_need || ''}
        regenSection="case_summary"
        onSaved={reload}
      >
        <p className="whitespace-pre-wrap">{summary.chief_need || '—'}</p>
        {summary.mdt_questions && summary.mdt_questions.length > 0 && (
          <div className="mt-2 text-xs">
            <div className="text-gray-500">本次 MDT 需解答:</div>
            <ul className="list-disc list-inside text-gray-700">
              {summary.mdt_questions.map((q: string, i: number) => <li key={i}>{q}</li>)}
            </ul>
          </div>
        )}
      </EditableCard>

      {/* 2. 病历摘要 + 时间轴 */}
      <EditableCard
        sessionId={sessionId}
        title="② 病历摘要"
        fieldPath="summary.history_summary"
        value={summary.history_summary || ''}
        regenSection="case_summary"
        onSaved={reload}
      >
        <p className="whitespace-pre-wrap text-sm">{summary.history_summary || '—'}</p>
        {summary.treatment_timeline && summary.treatment_timeline.length > 0 && (
          <div className="mt-3 border-t pt-2">
            <div className="text-xs text-gray-500 mb-1">既往治疗时间轴</div>
            <ul className="space-y-1 text-sm">
              {summary.treatment_timeline.map((t: any, i: number) => (
                <li key={i} className="flex gap-2">
                  <span className="text-gray-400 text-xs whitespace-nowrap w-20">{t.date || '—'}</span>
                  <span className="flex-1">
                    {t.event}
                    <EvidencePopover snippet={t.evidence_snippet} source={t.evidence_source} />
                  </span>
                </li>
              ))}
            </ul>
          </div>
        )}
      </EditableCard>

      {/* 3. 当前临床判断 */}
      <EditableCard
        sessionId={sessionId}
        title="③ 当前临床判断"
        fieldPath="final.clinical_judgment"
        value={finalRec.clinical_judgment || ''}
        regenSection="final"
        onSaved={reload}
      >
        <p className="whitespace-pre-wrap text-sm">{finalRec.clinical_judgment || '—'}</p>
      </EditableCard>

      {/* 4. TNM */}
      <TNMStageCard sessionId={sessionId} tnm={tnm} onSaved={reload} />

      {/* 5. 多学科意见 */}
      {opinions.length > 0 && <DeptOpinionCard opinions={opinions} />}

      {/* 6. 检查建议 */}
      <EditableCard
        sessionId={sessionId}
        title="⑥ 检查建议"
        fieldPath="final.exam_recommendations"
        value={finalRec.exam_recommendations || []}
        regenSection="final"
        onSaved={reload}
      >
        <RecList items={finalRec.exam_recommendations} kind="exam" />
      </EditableCard>

      {/* 7. 治疗建议 */}
      <EditableCard
        sessionId={sessionId}
        title="⑦ 治疗建议(需医生最终确认)"
        fieldPath="final.treatment_recommendations"
        value={finalRec.treatment_recommendations || []}
        regenSection="final"
        onSaved={reload}
      >
        <RecList items={finalRec.treatment_recommendations} kind="treatment" />
        <p className="text-xs italic text-rose-600 mt-2">
          ⚠️ 治疗方案需主治医师最终确认,AI 建议仅供参考
        </p>
      </EditableCard>

      {/* 8. 推荐医生 */}
      <EditableCard
        sessionId={sessionId}
        title="⑧ 推荐就诊医生/门诊"
        fieldPath="final.referral"
        value={finalRec.referral || []}
        regenSection="final"
        onSaved={reload}
      >
        {Array.isArray(finalRec.referral) && finalRec.referral.length > 0 ? (
          <ul className="text-sm space-y-1">
            {finalRec.referral.map((r: any, i: number) => (
              <li key={i}>
                <span className="font-medium">{r.specialty || r.dept}</span>
                {r.reason && <span className="text-gray-500 text-xs"> · {r.reason}</span>}
                {r.suggested_doctor && (
                  <span className="text-brand-700 text-xs"> · {r.suggested_doctor}</span>
                )}
              </li>
            ))}
          </ul>
        ) : (
          <div className="text-sm text-gray-400">无</div>
        )}
      </EditableCard>

      {/* 9. 患者反馈话术 */}
      <EditableCard
        sessionId={sessionId}
        title="⑨ 给患者的反馈话术"
        fieldPath="final.patient_script"
        value={finalRec.patient_script || ''}
        regenSection="patient_script"
        onSaved={reload}
      >
        <p className="whitespace-pre-wrap text-sm">{finalRec.patient_script || '—'}</p>
        <p className="text-xs italic text-gray-500 mt-2">
          已自动检测,不含"治愈/一定/保证"等承诺词
        </p>
      </EditableCard>

      <div className="text-xs text-rose-600 italic text-center py-4">
        AI 辅助生成,需主治医师复核
      </div>
    </div>
  )
}

function RecList({ items, kind }: { items: any[]; kind: 'exam' | 'treatment' }) {
  if (!Array.isArray(items) || items.length === 0) {
    return <div className="text-sm text-gray-400">无</div>
  }
  return (
    <ul className="space-y-1 text-sm">
      {items.map((r: any, i: number) => (
        <li key={i} className="flex gap-2">
          <span className="text-gray-400 text-xs whitespace-nowrap">
            {r.priority || r.category || '—'}
          </span>
          <span className="flex-1">
            {r.name || r.item || r.description}
            {r.rationale && (
              <span className="text-gray-500 text-xs"> · {r.rationale}</span>
            )}
            {r.needs_doctor_confirm && kind === 'treatment' && (
              <span className="ml-1 text-rose-500 text-xs">[需医生确认]</span>
            )}
            <EvidencePopover snippet={r.evidence_snippet} source={r.evidence_source} />
          </span>
        </li>
      ))}
    </ul>
  )
}
