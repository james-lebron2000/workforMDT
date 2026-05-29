'use client'

import { useEffect, useState } from 'react'
import { useParams, useRouter } from 'next/navigation'
import { api, humanizeError, type MeetingDetail, type MeetingMember } from '@/lib/api'
import ConsentGate from '@/components/ConsentGate'
import DoctorProfileGate from '@/components/DoctorProfileGate'
import Recorder from '@/components/Recorder'
import ProgressStream from '@/components/ProgressStream'
import { useToast } from '@/components/Toast'

const STATUS_LABEL: Record<string, string> = {
  draft: '草稿',
  recording: '录音中',
  transcribing: 'ASR 转写中',
  splitting: '按患者切分中',
  analyzing: '各患者分析中',
  completed: '已完成',
  failed: '失败',
}

const SESSION_STATUS_LABEL: Record<string, string> = {
  draft: '草稿',
  collecting: '资料收集中',
  summary_confirmed: '病史已核对',
  recording: '录音中',
  analyzing: '分析中',
  reviewing: '待医生确认',
  completed: '已完成',
}

export default function MeetingRecordPage() {
  const router = useRouter()
  const toast = useToast()
  const params = useParams() as { id: string }
  const meetingId = params.id

  const [meeting, setMeeting] = useState<MeetingDetail | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [finalizing, setFinalizing] = useState(false)
  const [tick, setTick] = useState(0)

  async function reload() {
    try {
      const m = await api.getMeeting(meetingId)
      setMeeting(m)
    } catch (e: any) {
      setError(humanizeError(e))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    reload()
  }, [meetingId, tick])

  // 切分/分析中,每 5 秒刷一次拿最新 split_summary 和成员 session status
  useEffect(() => {
    if (!meeting) return
    if (['transcribing', 'splitting', 'analyzing'].includes(meeting.status)) {
      const t = setInterval(() => setTick((x) => x + 1), 5000)
      return () => clearInterval(t)
    }
  }, [meeting?.status])

  async function onFinalize() {
    if (
      !confirm(
        'AI 将整段 ASR + 按"现在讨论 X 患者"语义切分 + 触发各患者 7-Agent 综合分析,过程 5-15 分钟。\n\n确认开始?',
      )
    )
      return
    setFinalizing(true)
    try {
      await api.finalizeMeetingAnalyze(meetingId)
      toast.success('已触发整段 ASR + 切分 + 各患者分析,可在下方查看进度')
      setTick((x) => x + 1)
    } catch (e: any) {
      toast.error('启动失败:' + humanizeError(e))
    } finally {
      setFinalizing(false)
    }
  }

  if (loading) {
    return (
      <div className="card text-gray-500 text-center py-8">加载中…</div>
    )
  }

  if (error || !meeting) {
    return (
      <div className="card text-rose-600">
        {error || '会议不存在'}
        <button
          className="btn btn-ghost mt-2"
          onClick={() => router.push('/cases')}
        >
          ← 返回病例列表
        </button>
      </div>
    )
  }

  const unconfirmed = meeting.members.filter((m) => !m.has_summary_confirmed)
  const canRecord = meeting.status === 'draft' || meeting.status === 'recording'
  const showFinalize = meeting.audio_finalized && meeting.status === 'recording'
  // meeting.status 在 fanout 后停在 'analyzing';真正的"完成"以各成员 session_status 为准
  const membersWithSplits = meeting.members.filter((m) => m.split_is_missing !== true)
  const allMembersReady =
    membersWithSplits.length > 0 &&
    membersWithSplits.every((m) =>
      ['reviewing', 'completed'].includes(m.session_status),
    )
  const failed = meeting.status === 'failed'
  const done = meeting.status === 'completed' || (meeting.status === 'analyzing' && allMembersReady)
  const inProgress =
    !done && ['transcribing', 'splitting', 'analyzing'].includes(meeting.status)

  return (
    <DoctorProfileGate>
      <ConsentGate>
        <div className="space-y-4 pb-8">
          {/* 头部 */}
          <div>
            <button
              className="text-sm text-gray-500 hover:text-gray-700"
              onClick={() => router.push('/cases')}
            >
              ← 病例列表
            </button>
            <h1 className="text-lg font-semibold mt-1">
              {meeting.title || `MDT 会议 ${meetingId.slice(0, 8)}`}
            </h1>
            <p className="text-xs text-gray-500">
              {meeting.mdt_date || '未排期'} ·{' '}
              <span
                className={
                  done
                    ? 'tag-done'
                    : failed
                    ? 'tag-failed'
                    : inProgress
                    ? 'tag-processing'
                    : 'tag-pending'
                }
              >
                {STATUS_LABEL[meeting.status] || meeting.status}
              </span>{' '}
              · {meeting.members.length} 位患者
            </p>
          </div>

          {/* 快路录音红线松动告警 */}
          {unconfirmed.length > 0 && meeting.status === 'draft' && (
            <div className="card bg-amber-50 border-amber-300">
              <div className="text-sm font-medium text-amber-800 mb-1">
                ⚠️ 快路录音模式
              </div>
              <div className="text-xs text-amber-700 space-y-1">
                <p>
                  当前 <b>{unconfirmed.length}</b> 位患者尚未完成"病史与患者核对",您选择了直接进入 MDT 录音。
                </p>
                <p>
                  会后请回到下方对应病例,点"步骤 2 · 病史已与患者核对确认"补做,否则病史摘要可能含未核对信息。
                </p>
              </div>
            </div>
          )}

          {/* 成员列表 */}
          <div className="card space-y-2">
            <h2 className="text-sm font-medium">参会患者</h2>
            <ul className="space-y-1.5">
              {meeting.members.map((m, i) => (
                <MemberRow
                  key={m.session_id}
                  member={m}
                  index={i + 1}
                  done={done}
                  router={router}
                />
              ))}
            </ul>
          </div>

          {/* 录音区 — draft/recording 时可见 */}
          {canRecord && (
            <div className="card space-y-2">
              <h2 className="text-sm font-medium">
                🎙️ 整段 MDT 录音(全部患者讨论)
              </h2>
              <p className="text-xs text-gray-500">
                录音中请明确说"现在讨论 <b>患者代号</b>",AI 据此切分。每 90 秒落本地+上传,断网/锁屏自动恢复。
              </p>
              {meeting.group_voice_id ? (
                <Recorder
                  meetingId={meetingId}
                  presetVoiceId={meeting.group_voice_id}
                  voiceType="mdt_discussion"
                  label="🏥 MDT 整段录音"
                  chunkSeconds={90}
                  onFinished={() => {
                    // 录音 finalize 成功 → audio_finalized 由后端在下次 reload 时返回 true
                    setTick((x) => x + 1)
                  }}
                />
              ) : (
                <div className="text-xs text-rose-600">
                  ⚠️ 群组录音未初始化,请刷新或返回重新创建会议
                </div>
              )}
            </div>
          )}

          {/* 触发 ASR + 切分 + 分析 */}
          {showFinalize && (
            <div className="card border-l-4 border-l-brand-500 space-y-2">
              <h2 className="text-sm font-medium">下一步:AI 切分并分析</h2>
              <ul className="text-xs space-y-0.5 text-gray-600">
                <li>1. 整段 ASR(火山豆包音频理解 + 说话人分离)</li>
                <li>2. LLM 按"现在讨论 X 患者"语义切分到每位患者</li>
                <li>3. 每位患者并行触发 7-Agent 综合分析(TNM/各科意见/建议/QC)</li>
                <li>4. 完成后各自跳到 /cases/[id]/review 看报告</li>
              </ul>
              <button
                className="btn btn-primary w-full min-h-12"
                onClick={onFinalize}
                disabled={finalizing}
                type="button"
              >
                {finalizing ? '启动中…' : '🤖 开始 AI 切分并分析'}
              </button>
              <p className="text-xs italic text-rose-600 text-center">
                AI 辅助生成,需主治医师复核;切分若有偏差可在各病例页手动调整。
              </p>
            </div>
          )}

          {/* 进度流 */}
          {(inProgress || done || failed) && (
            <div className="card space-y-2">
              <h2 className="text-sm font-medium">实时进度</h2>
              <ProgressStream meetingId={meetingId} />
              {meeting.error && (
                <div className="text-xs text-rose-700 bg-rose-50 rounded p-2">
                  ⚠️ {meeting.error}
                </div>
              )}
              {failed && (
                <button
                  className="btn btn-secondary w-full"
                  onClick={onFinalize}
                  disabled={finalizing}
                  type="button"
                >
                  🔄 重试切分分析
                </button>
              )}
            </div>
          )}

          {/* 完成后 — 跳转汇总 */}
          {done && (
            <div className="card border-l-4 border-l-emerald-500 space-y-2">
              <h2 className="text-sm font-medium text-emerald-700">
                ✓ 会议切分完成
              </h2>
              <p className="text-xs text-gray-600">
                点击下方任一位患者卡片,可进入其报告页查看 AI 整理的多学科意见 + TNM + 治疗建议(均需医生复核确认)。
              </p>
            </div>
          )}
        </div>
      </ConsentGate>
    </DoctorProfileGate>
  )
}

function MemberRow({
  member: m,
  index,
  done,
  router,
}: {
  member: MeetingMember
  index: number
  done: boolean
  router: ReturnType<typeof useRouter>
}) {
  const isMissing = m.split_is_missing === true
  const hasSplit = m.split_segment_count != null
  const segCount = m.split_segment_count || 0
  const confPct = m.split_confidence != null ? Math.round(m.split_confidence * 100) : null
  const canOpenReview =
    done ||
    ['analyzing', 'reviewing', 'completed'].includes(m.session_status)

  return (
    <li
      className={
        'flex items-start gap-2 rounded border px-2 py-2 text-sm ' +
        (isMissing
          ? 'border-amber-300 bg-amber-50'
          : hasSplit
          ? 'border-emerald-200 bg-emerald-50/40'
          : 'border-gray-200')
      }
    >
      <span className="shrink-0 w-6 h-6 rounded-full bg-gray-100 text-xs flex items-center justify-center text-gray-600">
        {index}
      </span>
      <div className="flex-1 min-w-0 space-y-0.5">
        <div className="flex items-center gap-2 flex-wrap">
          <span className="font-medium truncate">{m.patient_code}</span>
          <span className="text-xs text-gray-500 truncate">
            {[m.primary_diagnosis, m.primary_site].filter(Boolean).join(' · ') || '未填诊断'}
          </span>
        </div>
        <div className="text-xs text-gray-500 flex items-center gap-2 flex-wrap">
          <span>状态:{SESSION_STATUS_LABEL[m.session_status] || m.session_status}</span>
          {!m.has_summary_confirmed && (
            <span className="text-amber-600">· 病史未核对</span>
          )}
          {hasSplit && !isMissing && (
            <span className="text-emerald-700">
              · 切分:{segCount} 段{confPct != null && ` (${confPct}%)`}
            </span>
          )}
          {isMissing && (
            <span className="text-amber-700">· 本次未明确讨论</span>
          )}
        </div>
      </div>
      <div className="shrink-0 flex flex-col gap-1">
        {canOpenReview && (
          <button
            className="text-xs text-brand-600 hover:underline px-2 py-1"
            onClick={() => router.push(`/cases/${m.session_id}/review`)}
            type="button"
          >
            查看报告 →
          </button>
        )}
        <button
          className="text-xs text-gray-500 hover:text-gray-700 px-2 py-1"
          onClick={() => router.push(`/cases/${m.session_id}/upload`)}
          type="button"
        >
          病例详情
        </button>
      </div>
    </li>
  )
}
