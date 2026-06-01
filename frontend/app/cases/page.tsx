'use client'

import { useEffect, useRef, useState } from 'react'
import { useRouter } from 'next/navigation'
import { api, humanizeError } from '@/lib/api'
import ConsentGate from '@/components/ConsentGate'
import DoctorProfileGate from '@/components/DoctorProfileGate'
import { useToast } from '@/components/Toast'
import { subscribeUserStream, type ConnectionState } from '@/lib/sse'

interface SessionItem {
  id: string
  patient_code: string
  title: string | null
  mdt_date: string | null
  status: string
}

const STATUS_LABEL: Record<string, string> = {
  draft: '草稿',
  collecting: '资料收集中',
  summary_confirmed: '病史已核对',
  recording: '录音中',
  analyzing: '分析中',
  reviewing: '待医生确认',
  completed: '已完成',
}

export default function CasesPage() {
  const router = useRouter()
  const toast = useToast()
  const [list, setList] = useState<SessionItem[]>([])
  const [showCreate, setShowCreate] = useState(false)
  const [busy, setBusy] = useState(false)
  // 群组录音多选状态
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [startingMeeting, setStartingMeeting] = useState(false)
  const [syncState, setSyncState] = useState<ConnectionState>('connecting')

  // refetch 节流:同一医生同时在另一端做 5 个动作不应该触发 5 次拉取
  const refetchTimerRef = useRef<number | null>(null)
  function scheduleRefetch(immediate = false) {
    if (refetchTimerRef.current) {
      window.clearTimeout(refetchTimerRef.current)
      refetchTimerRef.current = null
    }
    const delay = immediate ? 0 : 300
    refetchTimerRef.current = window.setTimeout(() => {
      refetchTimerRef.current = null
      api.listSessions().then((d) => setList(d.sessions)).catch(console.error)
    }, delay) as unknown as number
  }

  useEffect(() => {
    // 首次拉
    scheduleRefetch(true)
    // SSE 订阅 — 任何会话级/会议级变化都触发节流后 refetch
    const unsub = subscribeUserStream({
      onStateEvent: (ev) => {
        // ev.kind 之一:session_created/session_deleted/session_status_changed
        //              meeting_created/meeting_deleted/meeting_status_changed
        // 一律 refetch;不细化优化,列表场景下数据量小、refetch ≤ 100ms
        scheduleRefetch()
      },
      onState: (s) => setSyncState(s),
      onError: () => {
        // 静默 — onState 已经显示状态条;失败兜底由 fallback polling 接管
      },
    })
    // SSE 兜底:30 秒一次的低频轮询;SSE 正常时是浪费但成本极低,
    // SSE 进 'failed' 状态后这就是唯一的同步通道,绝不能砍。
    const fallback = window.setInterval(() => scheduleRefetch(), 30_000)
    return () => {
      unsub()
      window.clearInterval(fallback)
      if (refetchTimerRef.current) window.clearTimeout(refetchTimerRef.current)
    }
  }, [])

  async function onCreate(form: FormData) {
    setBusy(true)
    try {
      const code = (form.get('code') as string)?.trim()
      if (!code) {
        toast.warn('请输入患者代号(化名/编号,不要填真名)')
        return
      }
      const sess = await api.createSession({
        patient: {
          code,
          sex: (form.get('sex') as string) || undefined,
          age_range: (form.get('age_range') as string) || undefined,
          primary_diagnosis: (form.get('diagnosis') as string) || undefined,
          primary_site: (form.get('primary_site') as string) || undefined,
        },
        title: (form.get('title') as string) || undefined,
      })
      router.push(`/cases/${sess.id}/upload`)
    } catch (e: any) {
      toast.error('创建失败:' + humanizeError(e))
    } finally {
      setBusy(false)
    }
  }

  function toggleSelect(id: string) {
    setSelected((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  function clearSelection() {
    setSelected(new Set())
  }

  async function startGroupMeeting() {
    if (selected.size === 0) return
    setStartingMeeting(true)
    try {
      const sessionIds = list
        .filter((s) => selected.has(s.id))
        .map((s) => s.id)
      // 警示:有未确认病史的 session,需医生明确允许"快路录音"
      const unconfirmed = list.filter(
        (s) =>
          selected.has(s.id) &&
          !['summary_confirmed', 'recording', 'analyzing', 'reviewing', 'completed'].includes(
            s.status,
          ),
      )
      if (unconfirmed.length > 0) {
        const ok = confirm(
          `选中的 ${selected.size} 位患者中,有 ${unconfirmed.length} 位还未与患者核对病史摘要:\n` +
            unconfirmed.map((u) => `  · ${u.patient_code}`).join('\n') +
            `\n\n点"确定"将进入快路录音 — 会后请在各病例页补做"病史已与患者确认"。\n点"取消"先回去做病史核对。`,
        )
        if (!ok) return
      }
      const today = new Date().toISOString().slice(0, 10)
      const meeting = await api.createMeeting({
        session_ids: sessionIds,
        title: `MDT-${today}-${selected.size}人`,
        mdt_date: today,
      })
      router.push(`/mdt/${meeting.id}/record`)
    } catch (e: any) {
      toast.error('创建会议失败:' + humanizeError(e))
    } finally {
      setStartingMeeting(false)
    }
  }

  const hasSelection = selected.size > 0

  return (
    <DoctorProfileGate>
    <ConsentGate>
    <div className="space-y-4 pb-24">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <h1 className="text-lg font-semibold">病例列表</h1>
          <SyncDot state={syncState} />
        </div>
        <button className="btn btn-primary" onClick={() => setShowCreate(true)}>
          + 新建 MDT
        </button>
      </div>

      <div className="text-xs text-gray-500">
        💡 勾选多个病例 → 底部"开始 MDT 录音(N)"统一录一段会议,AI 自动按患者切分;
        或点击单个病例进入"4 步流程"独立准备。
      </div>

      {list.length === 0 && (
        <div className="card text-gray-500 text-center py-8">
          还没有 MDT 病例。点击右上角"新建 MDT"开始。
        </div>
      )}

      <ul className="space-y-2">
        {list.map((s) => {
          const checked = selected.has(s.id)
          return (
            <li
              key={s.id}
              className={
                'card flex items-center gap-3 ' +
                (checked ? 'ring-2 ring-blue-400 bg-blue-50/30' : 'hover:bg-gray-50')
              }
            >
              <label className="shrink-0 cursor-pointer p-1" onClick={(e) => e.stopPropagation()}>
                <input
                  type="checkbox"
                  className="w-5 h-5"
                  checked={checked}
                  onChange={() => toggleSelect(s.id)}
                />
              </label>
              <div
                className="flex-1 min-w-0 cursor-pointer"
                onClick={() => router.push(`/cases/${s.id}/upload`)}
              >
                <div className="font-medium truncate">
                  {s.title || `MDT-${s.patient_code}`}
                </div>
                <div className="text-xs text-gray-500">
                  患者:{s.patient_code} · {s.mdt_date || '未排期'}
                </div>
              </div>
              <span
                className={
                  s.status === 'completed'
                    ? 'tag-done'
                    : s.status === 'analyzing' || s.status === 'recording'
                    ? 'tag-processing'
                    : 'tag-pending'
                }
              >
                {STATUS_LABEL[s.status] || s.status}
              </span>
            </li>
          )
        })}
      </ul>

      {/* 底部固定 CTA — 多选起来才显示 */}
      {hasSelection && (
        <div className="fixed left-0 right-0 bottom-0 bg-white border-t shadow-lg p-3 z-40">
          <div className="max-w-3xl mx-auto flex items-center gap-2">
            <button
              className="btn btn-ghost min-h-12 text-sm shrink-0"
              onClick={clearSelection}
              disabled={startingMeeting}
              type="button"
            >
              取消
            </button>
            <div className="text-sm text-gray-600 flex-1 text-center">
              已选 <b className="text-blue-600">{selected.size}</b> 位患者
            </div>
            <button
              className="btn btn-primary min-h-12 text-base flex-1 max-w-[200px]"
              onClick={startGroupMeeting}
              disabled={startingMeeting}
              type="button"
            >
              {startingMeeting ? '创建中…' : `🎙️ 开始 MDT 录音 (${selected.size})`}
            </button>
          </div>
        </div>
      )}

      {showCreate && (
        <div
          className="fixed inset-0 bg-black/40 flex items-end sm:items-center justify-center z-50"
          onClick={() => setShowCreate(false)}
        >
          <form
            className="card w-full sm:max-w-md rounded-t-2xl sm:rounded-2xl m-0 sm:m-4 space-y-3"
            onClick={(e) => e.stopPropagation()}
            onSubmit={(e) => {
              e.preventDefault()
              onCreate(new FormData(e.currentTarget))
            }}
          >
            <h2 className="text-base font-semibold">新建 MDT 病例</h2>
            <p className="text-xs text-gray-500">
              ⚠️ 请使用患者代号/编号,不要填真实姓名
            </p>

            <input
              name="code"
              required
              className="w-full border rounded-md px-3 py-2 text-sm"
              placeholder="患者代号(必填,如 P-2026-001 或 ZS123)"
            />
            <div className="grid grid-cols-2 gap-2">
              <select name="sex" className="border rounded-md px-3 py-2 text-sm">
                <option value="">性别</option>
                <option value="男">男</option>
                <option value="女">女</option>
              </select>
              <select name="age_range" className="border rounded-md px-3 py-2 text-sm">
                <option value="">年龄段</option>
                <option value="0-20">0-20</option>
                <option value="20-30">20-30</option>
                <option value="30-40">30-40</option>
                <option value="40-50">40-50</option>
                <option value="50-60">50-60</option>
                <option value="60-70">60-70</option>
                <option value="70-80">70-80</option>
                <option value="80+">80+</option>
              </select>
            </div>
            <input
              name="diagnosis"
              className="w-full border rounded-md px-3 py-2 text-sm"
              placeholder="主要诊断(可选,如:直肠癌)"
            />
            <input
              name="primary_site"
              className="w-full border rounded-md px-3 py-2 text-sm"
              placeholder="原发部位(可选,如:直肠中下段)"
            />
            <input
              name="title"
              className="w-full border rounded-md px-3 py-2 text-sm"
              placeholder="会议标题(可选)"
            />

            <div className="flex gap-2 pt-2">
              <button
                type="button"
                className="btn btn-ghost flex-1"
                onClick={() => setShowCreate(false)}
              >
                取消
              </button>
              <button type="submit" className="btn btn-primary flex-1" disabled={busy}>
                {busy ? '创建中…' : '创建并进入'}
              </button>
            </div>
          </form>
        </div>
      )}
    </div>
    </ConsentGate>
    </DoctorProfileGate>
  )
}

// 多端同步状态指示器 — 让医生知道列表是不是实时的
function SyncDot({ state }: { state: ConnectionState }) {
  const map: Record<ConnectionState, { cls: string; title: string }> = {
    connecting: { cls: 'bg-gray-300', title: '连接中…' },
    open: { cls: 'bg-emerald-500', title: '实时同步中(其他端的操作会自动出现)' },
    reconnecting: { cls: 'bg-amber-400 animate-pulse', title: '同步连接重连中,30 秒兜底轮询' },
    closed: { cls: 'bg-gray-300', title: '同步已断开' },
    failed: { cls: 'bg-rose-500', title: '实时同步失败,改用 30 秒轮询;刷新页面可重试' },
  }
  const m = map[state]
  return (
    <span
      className={`inline-block w-2 h-2 rounded-full ${m.cls}`}
      title={m.title}
      aria-label={m.title}
    />
  )
}
