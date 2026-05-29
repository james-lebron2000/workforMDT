// SSE 客户端 - 订阅 /api/v1/sessions/{id}/progress
//
// 设计目标:30 分钟 AI 分析过程中,医生网络抖动/页面切到后台都不应该丢进度。
// - 指数退避重连(1s/3s/6s/12s/20s/30s,最多 6 次)
// - visibilitychange visible 时立即重连(iOS Safari 切前台会复活 EventSource)
// - readyState=CLOSED 才视为断;CONNECTING 不算
// - 暴露 onState 让 UI 显示"已重连/重连中"

import { getDeviceId } from './device'

export interface ProgressEvent {
  stage: string
  percent: number
  message: string
  ts: number
  [k: string]: any
}

export type ConnectionState = 'connecting' | 'open' | 'reconnecting' | 'closed' | 'failed'

interface SubscribeOptions {
  onMessage: (ev: ProgressEvent) => void
  onError?: (e: any) => void
  onState?: (s: ConnectionState, attempt: number) => void
}

const BACKOFF_MS = [1000, 3000, 6000, 12000, 20000, 30000]

export function subscribeProgress(
  sessionId: string,
  optsOrOnMessage:
    | SubscribeOptions
    | ((ev: ProgressEvent) => void),
  legacyOnError?: (e: any) => void,
): () => void {
  return subscribeUrl(
    `/api/v1/sessions/${sessionId}/progress`,
    optsOrOnMessage,
    legacyOnError,
  )
}

/** 群组 MDT 会议进度 — channel = meeting_id(UUID 不会撞 session_id) */
export function subscribeMeetingProgress(
  meetingId: string,
  optsOrOnMessage:
    | SubscribeOptions
    | ((ev: ProgressEvent) => void),
  legacyOnError?: (e: any) => void,
): () => void {
  return subscribeUrl(
    `/api/v1/mdt-meetings/${meetingId}/progress`,
    optsOrOnMessage,
    legacyOnError,
  )
}

function subscribeUrl(
  basePath: string,
  optsOrOnMessage:
    | SubscribeOptions
    | ((ev: ProgressEvent) => void),
  legacyOnError?: (e: any) => void,
): () => void {
  // 兼容老签名:subscribeProgress(id, onMessage, onError)
  const opts: SubscribeOptions =
    typeof optsOrOnMessage === 'function'
      ? { onMessage: optsOrOnMessage, onError: legacyOnError }
      : optsOrOnMessage

  let es: EventSource | null = null
  let closedByUser = false
  let attempt = 0
  let retryTimer: number | null = null

  const url = `${basePath}?device_id=${encodeURIComponent(getDeviceId())}`

  function connect() {
    if (closedByUser) return
    opts.onState?.(attempt === 0 ? 'connecting' : 'reconnecting', attempt)
    try {
      es = new EventSource(url, { withCredentials: false })
    } catch (e) {
      scheduleRetry(e)
      return
    }

    es.onopen = () => {
      attempt = 0
      opts.onState?.('open', 0)
    }
    es.onmessage = (e) => {
      try {
        const data = JSON.parse(e.data)
        opts.onMessage(data)
      } catch {
        // 后端心跳 / 注释行可能不是 JSON,忽略
      }
    }
    es.onerror = (e) => {
      opts.onError?.(e)
      // 仅在确实关闭时才重连;CONNECTING 状态浏览器自己会重试
      if (es && es.readyState === EventSource.CLOSED) {
        scheduleRetry(e)
      }
    }
  }

  function scheduleRetry(e: any) {
    if (closedByUser) return
    try {
      es?.close()
    } catch {}
    es = null
    if (attempt >= BACKOFF_MS.length) {
      opts.onState?.('failed', attempt)
      return
    }
    const delay = BACKOFF_MS[attempt]
    attempt += 1
    opts.onState?.('reconnecting', attempt)
    retryTimer = window.setTimeout(connect, delay) as unknown as number
  }

  // 切前台立即重连(iOS Safari 后台久了 EventSource 会失活)
  function onVis() {
    if (closedByUser) return
    if (document.visibilityState === 'visible') {
      const dead = !es || es.readyState === EventSource.CLOSED
      if (dead) {
        if (retryTimer) {
          window.clearTimeout(retryTimer)
          retryTimer = null
        }
        // 重置 attempt 让第一次重连更快(用户在场)
        attempt = 0
        connect()
      }
    }
  }
  document.addEventListener('visibilitychange', onVis)

  connect()

  return () => {
    closedByUser = true
    document.removeEventListener('visibilitychange', onVis)
    if (retryTimer) window.clearTimeout(retryTimer)
    try {
      es?.close()
    } catch {}
    es = null
    opts.onState?.('closed', attempt)
  }
}
