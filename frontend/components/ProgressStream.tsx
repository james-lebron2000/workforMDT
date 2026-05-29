'use client'

import { useEffect, useState } from 'react'
import {
  subscribeProgress,
  subscribeMeetingProgress,
  ProgressEvent,
  ConnectionState,
} from '@/lib/sse'

const STATE_LABEL: Record<ConnectionState, { text: string; cls: string }> = {
  connecting: { text: '连接中…', cls: 'text-gray-400' },
  open: { text: '已连接', cls: 'text-emerald-600' },
  reconnecting: { text: '重连中…', cls: 'text-amber-600' },
  closed: { text: '已断开', cls: 'text-gray-400' },
  failed: { text: '连接失败,请刷新页面', cls: 'text-rose-600' },
}

export default function ProgressStream({
  sessionId,
  meetingId,
}: {
  sessionId?: string
  meetingId?: string
}) {
  const [events, setEvents] = useState<ProgressEvent[]>([])
  const [state, setState] = useState<ConnectionState>('connecting')
  const [attempt, setAttempt] = useState(0)

  useEffect(() => {
    const handlers = {
      onMessage: (ev: ProgressEvent) => {
        setEvents((prev) => {
          const next = [...prev, ev]
          return next.slice(-30)
        })
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
        <span className="text-gray-400">
          {state === 'open' ? '等待任务进度…' : stateInfo.text}
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
