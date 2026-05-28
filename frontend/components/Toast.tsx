'use client'

/**
 * 轻量 Toast 通知 — 替换阻塞式 alert()。
 *
 * 为什么不用第三方:
 * - 临床手机端要求最小依赖、加载快、可被微信内核解析
 * - 行为可控:错误类 Toast 必须足够"显眼+久"(8s),不能像普通 toast 一闪而过
 *   — 误触放飞错误提示意味着医生看不到关键问题
 *
 * 使用:
 *   const toast = useToast()
 *   toast.error('保存失败:' + humanizeError(e))
 *   toast.success('已复制')
 *   toast.warn('部分片段未上传')
 *   toast.info('AI 分析已启动,大约 3 分钟出结果')
 */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from 'react'

type Kind = 'success' | 'error' | 'warn' | 'info'

interface ToastItem {
  id: string
  kind: Kind
  message: string
  ttl: number // ms;0 = 不自动关
}

interface ToastApi {
  success: (msg: string, opts?: { ttl?: number }) => void
  error: (msg: string, opts?: { ttl?: number }) => void
  warn: (msg: string, opts?: { ttl?: number }) => void
  info: (msg: string, opts?: { ttl?: number }) => void
  dismiss: (id: string) => void
}

const ToastContext = createContext<ToastApi | null>(null)

const DEFAULT_TTL: Record<Kind, number> = {
  success: 2500,
  info: 3500,
  warn: 6000,
  error: 8000, // 临床场景:错误必须看到
}

const STYLE: Record<Kind, { bg: string; border: string; icon: string }> = {
  success: { bg: 'bg-emerald-50', border: 'border-emerald-300', icon: '✅' },
  error: { bg: 'bg-rose-50', border: 'border-rose-300', icon: '⚠️' },
  warn: { bg: 'bg-amber-50', border: 'border-amber-300', icon: '⚠️' },
  info: { bg: 'bg-sky-50', border: 'border-sky-300', icon: 'ℹ️' },
}

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([])
  const timers = useRef<Map<string, number>>(new Map())

  const dismiss = useCallback((id: string) => {
    const t = timers.current.get(id)
    if (t) {
      window.clearTimeout(t)
      timers.current.delete(id)
    }
    setItems((prev) => prev.filter((it) => it.id !== id))
  }, [])

  const push = useCallback(
    (kind: Kind, message: string, opts?: { ttl?: number }) => {
      const id = (typeof crypto !== 'undefined' && crypto.randomUUID)
        ? crypto.randomUUID()
        : `t-${Date.now()}-${Math.random()}`
      const ttl = opts?.ttl ?? DEFAULT_TTL[kind]
      setItems((prev) => {
        // 限制同时最多 4 条,溢出丢最旧
        const next = [...prev, { id, kind, message, ttl }]
        return next.slice(-4)
      })
      if (ttl > 0) {
        const handle = window.setTimeout(() => dismiss(id), ttl)
        timers.current.set(id, handle)
      }
    },
    [dismiss],
  )

  useEffect(() => {
    return () => {
      timers.current.forEach((h) => window.clearTimeout(h))
      timers.current.clear()
    }
  }, [])

  const api: ToastApi = {
    success: (m, o) => push('success', m, o),
    error: (m, o) => push('error', m, o),
    warn: (m, o) => push('warn', m, o),
    info: (m, o) => push('info', m, o),
    dismiss,
  }

  return (
    <ToastContext.Provider value={api}>
      {children}
      <div
        aria-live="polite"
        role="status"
        className="fixed inset-x-0 z-[60] flex flex-col items-center gap-2 px-3 pointer-events-none"
        style={{ top: 'calc(env(safe-area-inset-top) + 12px)' }}
      >
        {items.map((it) => {
          const s = STYLE[it.kind]
          return (
            <div
              key={it.id}
              className={`pointer-events-auto w-full max-w-md rounded-lg border ${s.border} ${s.bg} shadow-md px-3 py-2 text-sm flex items-start gap-2 animate-toast-in`}
              onClick={() => dismiss(it.id)}
            >
              <span className="text-base leading-5 shrink-0">{s.icon}</span>
              <div className="flex-1 break-words leading-relaxed whitespace-pre-line">
                {it.message}
              </div>
              <button
                className="text-gray-400 hover:text-gray-700 text-xs shrink-0 px-1"
                aria-label="关闭"
                onClick={(e) => {
                  e.stopPropagation()
                  dismiss(it.id)
                }}
              >
                ✕
              </button>
            </div>
          )
        })}
      </div>
    </ToastContext.Provider>
  )
}

/**
 * useToast — 在任意客户端组件里拿到 toast api。
 * 若组件没被 ToastProvider 包裹,会回退到 window.alert,保证不静默丢消息。
 */
export function useToast(): ToastApi {
  const ctx = useContext(ToastContext)
  if (ctx) return ctx
  // Fallback:虽然不该走到这里,但出错时不能静默
  const fallback = (msg: string) => {
    if (typeof window !== 'undefined') window.alert(msg)
  }
  return {
    success: fallback,
    error: fallback,
    warn: fallback,
    info: fallback,
    dismiss: () => {},
  }
}
