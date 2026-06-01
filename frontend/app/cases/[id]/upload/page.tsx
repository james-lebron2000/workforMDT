'use client'

import { useEffect, useState } from 'react'
import { useRouter, useParams } from 'next/navigation'
import { api, humanizeError } from '@/lib/api'
import UploadDropzone from '@/components/UploadDropzone'
import Recorder from '@/components/Recorder'
import ProgressStream from '@/components/ProgressStream'
import type { StateEvent } from '@/lib/sse'
import { useToast } from '@/components/Toast'

const STATUS_LABEL: Record<string, string> = {
  draft: '草稿',
  collecting: '资料收集中',
  summary_confirmed: '病史已确认',
  recording: '录音中',
  analyzing: '分析中',
  reviewing: '待医生确认',
  completed: '已完成',
}

export default function UploadPage() {
  const router = useRouter()
  const toast = useToast()
  const params = useParams() as { id: string }
  const sessionId = params.id

  const [data, setData] = useState<any>(null)
  const [analyzing, setAnalyzing] = useState(false)
  const [genSummary, setGenSummary] = useState(false)
  const [confirming, setConfirming] = useState(false)
  const [tick, setTick] = useState(0)

  // 编辑摘要的本地草稿
  const [editingSummary, setEditingSummary] = useState(false)
  const [draftSummary, setDraftSummary] = useState<{
    chief_need: string
    history_summary: string
    current_problem: string
  } | null>(null)

  async function reload() {
    try {
      const d = await api.getSession(sessionId)
      setData(d)
    } catch (e: any) {
      console.error(e)
    }
  }

  useEffect(() => {
    reload()
  }, [sessionId, tick])

  // 30s 兜底轮询 — SSE 才是主同步通道(ProgressStream onStateChange → reload),
  // 这里只在 SSE 失联(NAT 杀连接 / 网络抖动重连失败)时保证最终一致。
  // 老的"仅 collecting/analyzing 5s 轮"已被 SSE 全状态覆盖替代。
  useEffect(() => {
    const t = setInterval(() => setTick((x) => x + 1), 30_000)
    return () => clearInterval(t)
  }, [])

  // SSE 状态事件 → 触发 reload。session_deleted 特殊处理:跳回列表。
  function onStateEvent(ev: StateEvent) {
    if (ev.kind === 'session_deleted') {
      toast.warn('该病例已被其他端删除')
      router.push('/cases')
      return
    }
    // 其余 kind(record_added/record_updated/voice_updated/summary_updated/
    // summary_confirmed/tnm_updated/opinion_updated/final_updated/
    // field_updated/analysis_done) 一律 refetch
    reload()
  }

  const sess = data?.session
  const records: any[] = data?.records || []
  const voices: any[] = data?.voices || []
  const summary = data?.summary
  const patientVoice = voices.find((v) => v.voice_type === 'patient_request')
  const mdtVoice = voices.find((v) => v.voice_type === 'mdt_discussion')

  const ocrDone = records.filter((r) => r.ocr_status === 'done').length
  const ocrTotal = records.length

  // 状态机
  const status = sess?.status || 'draft'
  const step1Done = ocrDone > 0
  const step2Done = status === 'summary_confirmed' || status === 'recording' || status === 'analyzing' || status === 'reviewing' || status === 'completed'
  const step3Done = mdtVoice?.asr_status === 'done'
  const step4Done = status === 'reviewing' || status === 'completed'

  async function onGenerateSummary() {
    setGenSummary(true)
    try {
      await api.triggerSummary(sessionId)
      // 任务异步,UI 上等 SSE / 轮询刷
      setTimeout(() => setTick((x) => x + 1), 1000)
    } catch (e: any) {
      toast.error('生成摘要失败:' + humanizeError(e))
    } finally {
      setGenSummary(false)
    }
  }

  async function onSaveSummaryEdits() {
    if (!draftSummary) return
    setEditingSummary(false)
    try {
      const cur = summary || {}
      for (const k of ['chief_need', 'history_summary', 'current_problem'] as const) {
        if ((cur[k] || '') !== (draftSummary[k] || '')) {
          await api.editField(sessionId, `summary.${k}`, draftSummary[k] || '')
        }
      }
      setTick((x) => x + 1)
    } catch (e: any) {
      toast.error('保存失败:' + humanizeError(e))
    }
  }

  async function onConfirmSummary() {
    if (!confirm('确认病史摘要已与患者核对、无误,并明确本次 MDT 待解答问题?\n确认后将进入 MDT 录音环节,病史一旦定稿便不再修改。')) return
    setConfirming(true)
    try {
      await api.confirmSummary(sessionId)
      toast.success('病史已确认,可进入 MDT 录音')
      setTick((x) => x + 1)
    } catch (e: any) {
      toast.error('确认失败:' + humanizeError(e))
    } finally {
      setConfirming(false)
    }
  }

  async function onExportBrief() {
    try {
      const blob = await api.exportBrief(sessionId)
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `MDT-${sess?.patient?.code || sessionId}-brief.pdf`
      document.body.appendChild(a)
      a.click()
      a.remove()
      URL.revokeObjectURL(url)
      toast.success('会前摘要 PDF 已下载')
    } catch (e: any) {
      toast.error('导出失败:' + humanizeError(e))
    }
  }

  async function onStartAnalyze() {
    if (!confirm('开始 AI 综合分析?将整理 MDT 录音 + 资料生成多学科报告。')) return
    setAnalyzing(true)
    try {
      await api.triggerAnalyze(sessionId)
      toast.info('AI 分析已启动,大约 3~5 分钟出结果。可在确认页查看进度。')
      router.push(`/cases/${sessionId}/review`)
    } catch (e: any) {
      toast.error('分析失败:' + humanizeError(e))
      setAnalyzing(false)
    }
  }

  return (
    <div className="space-y-4">
      {/* 头部 */}
      <div>
        <button
          className="text-sm text-gray-500 hover:text-gray-700"
          onClick={() => router.push('/cases')}
        >
          ← 病例列表
        </button>
        <h1 className="text-lg font-semibold mt-1">
          {sess?.title || `MDT-${sess?.patient?.code || '...'}`}
        </h1>
        <p className="text-xs text-gray-500">
          患者代号:{sess?.patient?.code || '—'} ·{' '}
          <span className="tag-pending">{STATUS_LABEL[status] || status}</span>
        </p>
      </div>

      <div className="card">
        <ProgressStream sessionId={sessionId} onStateChange={onStateEvent} />
      </div>

      {/* 步骤 1:资料采集 */}
      <Stepper n={1} title="拍照采集患者资料" done={step1Done} active={!step1Done}>
        <UploadDropzone sessionId={sessionId} onDone={() => setTick((t) => t + 1)} />
        {records.length > 0 && (
          <div className="border-t mt-3 pt-2">
            <h3 className="text-xs text-gray-500 mb-1">已上传({ocrDone}/{ocrTotal} OCR 完成)</h3>
            <ul className="space-y-1 text-xs max-h-48 overflow-y-auto">
              {records.map((r: any) => (
                <li key={r.id} className="flex items-center justify-between">
                  <span className="truncate flex-1 mr-2">
                    {(r.file_key || '').split('/').pop()}
                  </span>
                  <span className={
                    r.ocr_status === 'done' ? 'tag-done' :
                    r.ocr_status === 'failed' ? 'tag-failed' :
                    r.ocr_status === 'processing' ? 'tag-processing' :
                    'tag-pending'
                  }>
                    {r.ocr_status === 'done' ? 'OCR ✓' :
                     r.ocr_status === 'failed' ? '失败' :
                     r.ocr_status === 'processing' ? '识别中' :
                     '待识别'}
                  </span>
                </li>
              ))}
            </ul>
          </div>
        )}
      </Stepper>

      {/* 步骤 2:询问患者诉求 + 病史汇总(交互核对) */}
      <Stepper n={2} title="询问诉求并核对病史摘要" done={step2Done} active={step1Done && !step2Done} locked={!step1Done}>
        {/* 患者诉求录音(可选) */}
        <div className="space-y-2">
          <h3 className="text-xs text-gray-500">2.1 录"患者诉求"(1-3 分钟,可选)</h3>
          {patientVoice?.asr_status === 'done' ? (
            <div className="text-xs text-emerald-700 bg-emerald-50 rounded px-2 py-1">
              ✓ 已录制并转写完毕
            </div>
          ) : (
            <Recorder
              sessionId={sessionId}
              voiceType="patient_request"
              label="🎙️ 患者诉求"
              chunkSeconds={60}
              onFinished={() => setTick((t) => t + 1)}
            />
          )}
        </div>

        {/* 病史摘要生成 */}
        <div className="space-y-2 border-t pt-3 mt-3">
          <h3 className="text-xs text-gray-500">2.2 AI 生成病史摘要,与患者一起核对</h3>
          {!summary ? (
            <button
              className="btn btn-secondary w-full"
              onClick={onGenerateSummary}
              disabled={genSummary || ocrDone === 0}
            >
              {genSummary ? '生成中…' : '✨ 一键生成病史摘要(基于已上传资料)'}
            </button>
          ) : (
            <div className="space-y-2">
              <div className="flex justify-between items-center">
                <span className="text-xs text-gray-500">AI 摘要(可在与患者问诊时实时编辑)</span>
                {!editingSummary ? (
                  <div className="flex gap-2">
                    <button
                      className="text-xs text-gray-500 hover:text-brand-600"
                      onClick={onGenerateSummary}
                      disabled={genSummary || step2Done}
                    >
                      ↻ 重新生成
                    </button>
                    {!step2Done && (
                      <button
                        className="text-xs text-brand-600 hover:underline"
                        onClick={() => {
                          setEditingSummary(true)
                          setDraftSummary({
                            chief_need: summary.chief_need || '',
                            history_summary: summary.history_summary || '',
                            current_problem: summary.current_problem || '',
                          })
                        }}
                      >
                        ✎ 编辑
                      </button>
                    )}
                  </div>
                ) : (
                  <div className="flex gap-2">
                    <button className="text-xs text-gray-500" onClick={() => setEditingSummary(false)}>取消</button>
                    <button className="text-xs text-brand-600 font-medium" onClick={onSaveSummaryEdits}>保存</button>
                  </div>
                )}
              </div>
              <SummaryView
                summary={summary}
                editing={editingSummary}
                draft={draftSummary}
                setDraft={setDraftSummary}
              />
            </div>
          )}

          {summary && !step2Done && (
            <div className="border-t pt-2 mt-2">
              <button
                className="btn btn-primary w-full"
                onClick={onConfirmSummary}
                disabled={confirming}
              >
                {confirming ? '确认中…' : '✅ 病史已与患者核对确认,进入 MDT 录音'}
              </button>
              <p className="text-xs text-gray-400 italic mt-1 text-center">
                确认后病史定稿,不再修改;此后所有 AI 报告基于已确认的病史。
              </p>
            </div>
          )}

          {step2Done && (
            <div className="border-t pt-2 mt-2 space-y-1">
              <button
                className="btn btn-secondary w-full"
                onClick={onExportBrief}
              >
                📄 导出会前摘要 PDF(发给与会医生预读)
              </button>
              <p className="text-xs text-gray-400 italic text-center">
                仅含已与患者核对的诉求/病史/治疗时间轴/MDT 待解答问题,不含 TNM/各科意见/治疗建议
              </p>
            </div>
          )}
        </div>
      </Stepper>

      {/* 步骤 3:MDT 会议录音 */}
      <Stepper n={3} title="MDT 会议录音" done={step3Done} active={step2Done && !step3Done} locked={!step2Done}>
        {step2Done ? (
          mdtVoice?.asr_status === 'done' ? (
            <div className="text-xs text-emerald-700 bg-emerald-50 rounded px-2 py-1">
              ✓ MDT 录音转写完毕({mdtVoice.transcript?.length || 0} 段)
            </div>
          ) : (
            <Recorder
              sessionId={sessionId}
              voiceType="mdt_discussion"
              label="🏥 整段 MDT 会议(可达数十分钟,边录边自动上传)"
              chunkSeconds={90}
              onFinished={() => setTick((t) => t + 1)}
            />
          )
        ) : (
          <p className="text-xs text-gray-400">完成上一步后解锁。整段录音、AI 自动按科室分流。</p>
        )}
      </Stepper>

      {/* 步骤 4:AI 综合分析 */}
      <Stepper n={4} title="AI 综合分析" done={step4Done} active={step3Done && !step4Done} locked={!step3Done}>
        {step3Done ? (
          <>
            <ul className="text-xs space-y-1 text-gray-600 mb-2">
              <li>· 当前临床判断</li>
              <li>· TNM 分期 + 依据(包括不确定项)</li>
              <li>· 6 大核心科室意见(缺席科室标注"本次讨论未明确记录")</li>
              <li>· 检查建议 / 治疗建议(注明:需医生最终确认)</li>
              <li>· 推荐就诊医生 / 门诊</li>
              <li>· 给患者及家属的反馈话术</li>
            </ul>
            <button
              className="btn btn-primary w-full"
              onClick={onStartAnalyze}
              disabled={analyzing}
            >
              {analyzing ? 'AI 分析中…' : '🤖 一键 AI 综合分析'}
            </button>
            <p className="text-xs italic text-rose-600 text-center mt-1">
              AI 辅助生成,需主治医师复核
            </p>
          </>
        ) : (
          <p className="text-xs text-gray-400">完成 MDT 录音转写后解锁。</p>
        )}
      </Stepper>
    </div>
  )
}

function Stepper({
  n, title, done, active, locked, children,
}: {
  n: number; title: string; done: boolean; active: boolean; locked?: boolean
  children: React.ReactNode
}) {
  return (
    <div className={
      'card border-l-4 ' +
      (done ? 'border-l-emerald-500' :
       active ? 'border-l-brand-500' :
       locked ? 'border-l-gray-200 opacity-60' :
       'border-l-gray-300')
    }>
      <div className="flex items-center justify-between mb-2">
        <h2 className="font-medium text-sm flex items-center gap-2">
          <span className={
            'inline-flex w-6 h-6 rounded-full items-center justify-center text-xs font-bold ' +
            (done ? 'bg-emerald-500 text-white' :
             active ? 'bg-brand-500 text-white' :
             'bg-gray-200 text-gray-500')
          }>
            {done ? '✓' : n}
          </span>
          步骤 {n} · {title}
        </h2>
        {done && <span className="tag-done">已完成</span>}
        {locked && !done && <span className="text-xs text-gray-400">🔒 待解锁</span>}
      </div>
      <div className={locked ? 'pointer-events-none' : ''}>{children}</div>
    </div>
  )
}

function SummaryView({
  summary, editing, draft, setDraft,
}: {
  summary: any
  editing: boolean
  draft: any
  setDraft: (d: any) => void
}) {
  if (editing && draft) {
    return (
      <div className="space-y-2 text-sm">
        <Field label="本次就诊需求与预期">
          <textarea
            className="w-full border rounded px-2 py-1 text-sm"
            rows={2}
            value={draft.chief_need}
            onChange={(e) => setDraft({ ...draft, chief_need: e.target.value })}
          />
        </Field>
        <Field label="病史摘要">
          <textarea
            className="w-full border rounded px-2 py-1 text-sm"
            rows={5}
            value={draft.history_summary}
            onChange={(e) => setDraft({ ...draft, history_summary: e.target.value })}
          />
        </Field>
        <Field label="当前问题">
          <textarea
            className="w-full border rounded px-2 py-1 text-sm"
            rows={2}
            value={draft.current_problem}
            onChange={(e) => setDraft({ ...draft, current_problem: e.target.value })}
          />
        </Field>
      </div>
    )
  }

  return (
    <div className="space-y-2 text-sm">
      <Field label="本次就诊需求与预期">
        <p className="whitespace-pre-wrap text-gray-800">{summary.chief_need || '—'}</p>
      </Field>
      <Field label="病史摘要">
        <p className="whitespace-pre-wrap text-gray-800">{summary.history_summary || '—'}</p>
      </Field>
      {summary.treatment_timeline?.length > 0 && (
        <Field label="既往治疗时间轴">
          <ul className="space-y-1">
            {summary.treatment_timeline.map((t: any, i: number) => (
              <li key={i} className="flex gap-2">
                <span className="text-xs text-gray-400 w-20 whitespace-nowrap">{t.date || '—'}</span>
                <span className="flex-1 text-xs">{t.event}</span>
              </li>
            ))}
          </ul>
        </Field>
      )}
      <Field label="当前问题">
        <p className="whitespace-pre-wrap text-gray-800">{summary.current_problem || '—'}</p>
      </Field>
      {summary.mdt_questions?.length > 0 && (
        <Field label="本次 MDT 待解答问题">
          <ul className="list-disc list-inside text-xs text-gray-700">
            {summary.mdt_questions.map((q: string, i: number) => <li key={i}>{q}</li>)}
          </ul>
        </Field>
      )}
    </div>
  )
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <div className="text-xs text-gray-500 mb-0.5">{label}</div>
      {children}
    </div>
  )
}
