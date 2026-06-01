'use client'

import { useEffect, useRef, useState } from 'react'
import {
  subscribeProgress,
  subscribeMeetingProgress,
  ProgressEvent,
  StateEvent,
  ConnectionState,
} from '@/lib/sse'

const STATE_LABEL: Record<ConnectionState, { text: string; cls: string }> = {
  connecting: { text: '连接中…', cls: 'text-gray-400' },
  open: { text: '已连接 · 实时同步', cls: 'text-emerald-600' },
  reconnecting: { text: '重连中…', cls: 'text-amber-600' },
  closed: { text: '已断开', cls: 'text-gray-400' },
  failed: { text: '连接失败,请刷新页面', cls: 'text-rose-600' },
}

interface Props {
  sessionId?: string
  meetingId?: string
  /** 收到状态事件(如 record_updated/summary_confirmed/field_updated)时调,
   *  父组件应据此 refetch 当前页数据。父组件可按 kind 做细化(如 session_deleted → 跳回列表)。
   */
  onStateChange?: (ev: StateEvent) => void
}

export default function ProgressStream({
  sessionId,
  meetingId,
  onStateChange,
}: Props) {
  const [events, setEvents] = useState<ProgressEvent[]>([])
  const [state, setState] = useState<ConnectionState>('connecting')
  const [attempt, setAttempt] = useState(0)
  const [lastSync, setLastSync] = useState<number | null>(null)

  // 用 ref 持有最新 callback,避免依赖变化导致重订阅(每次重订阅会丢历史)
  const onStateChangeRef = useRef(onStateChange)
  onStateChangeRef.current = onStateChange

  useEffect(() => {
    const handlers = {
      onMessage: (ev: ProgressEvent) => {
        setEvents((prev) => {
          const next = [...prev, ev]
          return next.slice(-30)
        })
      },
      onStateEvent: (ev: StateEvent) => {
        setLastSync(ev.ts || Date.now() / 1000)
        onStateChangeRef.current?.(ev)
      },
      onState: (s: ConnectionState, n: number) => {
        setState(s)
        setAttempt(n)
      },
    }
    const unsub = meetingId
      ? subscribeMeetingProgress(meetingId, handlers)
      : sessionId
      ? subscribeProgress(sessionId, handlers)
      : () => {}
    return unsub
  }, [sessionId, meetingId])

  const latest = events[events.length - 1]
  const stateInfo = STATE_LABEL[state]

  if (events.length === 0) {
    return (
      <div className="text-xs flex items-center justify-between">
        <span className={state === 'open' ? 'text-emerald-600' : 'text-gray-400'}>
          {state === 'open'
            ? lastSync
              ? `● 实时同步中 · 最近事件 ${fmtAgo(lastSync)}`
              : '● 实时同步中 · 等待事件'
            : stateInfo.text}
        </span>
        {state === 'reconnecting' && (
          <span className="text-xs text-amber-600">第 {attempt} 次</span>
        )}
      </div>
    )
  }

  return (
    <div className="space-y-2">
      {latest && (
        <div>
          <div className="flex items-center justify-between text-xs">
            <span className="text-gray-700 truncate flex-1 mr-2">
              {latest.stage} · {latest.message}
            </span>
            <span className="text-gray-500 shrink-0">{latest.percent}%</span>
          </div>
          <div className="h-2 bg-gray-100 rounded-full overflow-hidden mt-1">
            <div
              className="h-full bg-brand-500 transition-all"
              style={{ width: `${Math.min(100, Math.max(0, latest.percent))}%` }}
            />
          </div>
        </div>
      )}
      <div className="flex items-center justify-between text-[11px]">
        <span className={stateInfo.cls}>
          {state === 'open' && '● '}
          {state === 'reconnecting' && '◌ '}
          {stateInfo.text}
          {state === 'reconnecting' && ` (第 ${attempt} 次)`}
        </span>
        <details className="text-xs text-gray-500">
          <summary className="cursor-pointer text-gray-400">
            所有事件({events.length})
          </summary>
          <ul className="mt-1 space-y-0.5 max-h-40 overflow-y-auto">
            {events.slice().reverse().map((e, i) => (
              <li key={i}>
                <span className="text-gray-400">[{e.stage}]</span> {e.message} · {e.percent}%
              </li>
            ))}
          </ul>
        </details>
      </div>
    </div>
  )
}

function fmtAgo(tsSeconds: number): string {
  const dt = Date.now() / 1000 - tsSeconds
  if (dt < 5) return '刚刚'
  if (dt < 60) return `${Math.floor(dt)} 秒前`
  if (dt < 3600) return `${Math.floor(dt / 60)} 分钟前`
  return `${Math.floor(dt / 3600)} 小时前`
}
