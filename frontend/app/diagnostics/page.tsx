'use client'

/**
 * 临床上场前自测页 — 医生在手机上打开 /diagnostics 一键体检。
 *
 * 为什么需要这个页面:
 * 真实门诊里医生不会先调试,而是直接拍照录音。如果当下才发现 Safari/麦克风/网络/
 * 后端 ffmpeg 有问题,会很尴尬。这个页面把 6 项关键能力逐一探测一遍,任一项黄/红
 * 都给出明确的"怎么修"指引(切 Safari、开权限、换网络、联系管理员)。
 */

import { useEffect, useState } from 'react'

type CheckStatus = 'pending' | 'pass' | 'warn' | 'fail'

interface Check {
  id: string
  name: string
  status: CheckStatus
  detail: string
  hint?: string
}

const initial: Check[] = [
  { id: 'browser', name: '浏览器与设备', status: 'pending', detail: '' },
  { id: 'network', name: '网络连接', status: 'pending', detail: '' },
  { id: 'backend', name: '后端服务 /health/deep', status: 'pending', detail: '' },
  { id: 'mic', name: '麦克风权限', status: 'pending', detail: '' },
  { id: 'mediarecorder', name: 'MediaRecorder + 编码格式', status: 'pending', detail: '' },
  { id: 'wakelock', name: '屏幕常亮 (Wake Lock)', status: 'pending', detail: '' },
  { id: 'indexeddb', name: '本地缓存 (IndexedDB)', status: 'pending', detail: '' },
]

function classFor(s: CheckStatus): string {
  switch (s) {
    case 'pass':
      return 'border-emerald-300 bg-emerald-50'
    case 'warn':
      return 'border-amber-300 bg-amber-50'
    case 'fail':
      return 'border-rose-300 bg-rose-50'
    default:
      return 'border-gray-200 bg-white'
  }
}

function emojiFor(s: CheckStatus): string {
  return { pending: '⏳', pass: '✅', warn: '⚠️', fail: '❌' }[s]
}

export default function DiagnosticsPage() {
  const [checks, setChecks] = useState<Check[]>(initial)
  const [running, setRunning] = useState(false)

  function setCheck(id: string, patch: Partial<Check>) {
    setChecks((prev) => prev.map((c) => (c.id === id ? { ...c, ...patch } : c)))
  }

  async function run() {
    setRunning(true)
    setChecks(initial.map((c) => ({ ...c, status: 'pending', detail: '' })))

    // 1. 浏览器与设备
    {
      const ua = typeof navigator !== 'undefined' ? navigator.userAgent : ''
      const isIOS = /iPhone|iPad|iPod/i.test(ua)
      const isWechat = /MicroMessenger/i.test(ua)
      const isSafari = /Safari/i.test(ua) && !/Chrome|CriOS|FxiOS/i.test(ua)
      let status: CheckStatus = 'pass'
      let detail = ua.slice(0, 80)
      let hint: string | undefined
      if (isWechat) {
        status = 'warn'
        hint =
          '微信内置浏览器对录音兼容性差。请点右上角 "..." → 选择"在浏览器打开",iPhone 用 Safari、安卓用 Chrome。'
      } else if (isIOS && !isSafari) {
        status = 'warn'
        hint = 'iOS 上请用原生 Safari 访问,其他浏览器(如 QQ 浏览器、夸克)不一定支持录音。'
      }
      setCheck('browser', { status, detail, hint })
    }

    // 2. 网络连接
    {
      const t0 = performance.now()
      try {
        const res = await fetch('/api/v1/consent', { method: 'GET', cache: 'no-store' })
        const ms = Math.round(performance.now() - t0)
        if (res.status === 401 || res.ok) {
          // 401 也算正常 — 说明服务在,只是未登录
          setCheck('network', {
            status: ms > 2000 ? 'warn' : 'pass',
            detail: `已连接 (${ms}ms)`,
            hint: ms > 2000 ? '响应较慢,弱网下上传可能失败。建议连 WiFi。' : undefined,
          })
        } else {
          setCheck('network', {
            status: 'warn',
            detail: `服务可达但返回 ${res.status}`,
          })
        }
      } catch (e: any) {
        setCheck('network', {
          status: 'fail',
          detail: e?.message || '无法连接',
          hint: '检查 WiFi / 数据,或确认服务器地址。',
        })
      }
    }

    // 3. 后端 /health/deep
    {
      try {
        const res = await fetch('/health/deep', { cache: 'no-store' })
        const body = await res.json().catch(() => ({}))
        const components = body?.components || {}
        const issues: string[] = []
        for (const [k, v] of Object.entries(components as any)) {
          if (!(v as any).ok) issues.push(k)
        }
        if (res.ok && issues.length === 0) {
          setCheck('backend', {
            status: 'pass',
            detail: '所有依赖正常(PG/Redis/MinIO/ffmpeg/LLM/火山 OCR/ASR)',
          })
        } else if (issues.length > 0) {
          setCheck('backend', {
            status: 'fail',
            detail: `不健康组件:${issues.join('、')}`,
            hint: '请联系管理员检查后端服务。MDT 流程可能无法完成。',
          })
        } else {
          setCheck('backend', { status: 'warn', detail: `HTTP ${res.status}` })
        }
      } catch (e: any) {
        setCheck('backend', {
          status: 'fail',
          detail: e?.message || 'health check 失败',
          hint: '后端服务无响应。请联系管理员。',
        })
      }
    }

    // 4. 麦克风权限
    {
      if (typeof navigator === 'undefined' || !navigator.mediaDevices?.getUserMedia) {
        setCheck('mic', {
          status: 'fail',
          detail: '当前浏览器不支持 getUserMedia',
          hint: '请用最新 Safari / Chrome。',
        })
      } else {
        try {
          const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
          stream.getTracks().forEach((t) => t.stop())
          setCheck('mic', { status: 'pass', detail: '已授权' })
        } catch (e: any) {
          const name = e?.name || ''
          if (name === 'NotAllowedError') {
            setCheck('mic', {
              status: 'fail',
              detail: '权限被拒',
              hint:
                'iPhone:设置 → Safari → 麦克风 → 允许;安卓:浏览器设置 → 网站设置 → 麦克风 → 允许。然后刷新页面。',
            })
          } else if (name === 'NotFoundError') {
            setCheck('mic', { status: 'fail', detail: '未检测到麦克风' })
          } else {
            setCheck('mic', { status: 'warn', detail: name || e?.message || '未知错误' })
          }
        }
      }
    }

    // 5. MediaRecorder + 格式
    {
      if (typeof (window as any).MediaRecorder === 'undefined') {
        setCheck('mediarecorder', {
          status: 'fail',
          detail: '浏览器不支持 MediaRecorder',
          hint: 'iOS 14.3+ Safari 才支持。请升级系统或换设备。',
        })
      } else {
        const candidates = [
          'audio/webm;codecs=opus',
          'audio/webm',
          'audio/mp4',
          'audio/mp4;codecs=mp4a.40.2',
          'audio/ogg;codecs=opus',
        ]
        const supported = candidates.filter((m) =>
          (MediaRecorder as any).isTypeSupported?.(m),
        )
        if (supported.length === 0) {
          setCheck('mediarecorder', {
            status: 'fail',
            detail: '未找到可用音频编码',
            hint: '设备或浏览器版本过旧。',
          })
        } else {
          setCheck('mediarecorder', {
            status: 'pass',
            detail: `支持:${supported[0]}(后端会转为 mp3)`,
          })
        }
      }
    }

    // 6. Wake Lock
    {
      // @ts-ignore
      if ('wakeLock' in navigator && (navigator as any).wakeLock?.request) {
        try {
          // @ts-ignore
          const sentinel = await (navigator as any).wakeLock.request('screen')
          await sentinel.release?.()
          setCheck('wakelock', { status: 'pass', detail: '可用,录音时屏幕将保持常亮' })
        } catch (e: any) {
          setCheck('wakelock', {
            status: 'warn',
            detail: e?.message || '申请失败',
            hint: '录音时手机可能锁屏导致中断 — 请手动把"自动锁屏"设为永不,或保持点击屏幕。',
          })
        }
      } else {
        setCheck('wakelock', {
          status: 'warn',
          detail: '浏览器不支持 Wake Lock',
          hint: '录音前手动把"自动锁屏"设为永不(设置 → 显示与亮度 → 自动锁定 → 永不)。',
        })
      }
    }

    // 7. IndexedDB
    {
      if (typeof indexedDB === 'undefined') {
        setCheck('indexeddb', {
          status: 'fail',
          detail: '浏览器不支持 IndexedDB',
          hint: '隐私模式下 IndexedDB 通常被禁。请退出无痕模式。',
        })
      } else {
        try {
          const req = indexedDB.open('mdt-diag-probe', 1)
          req.onupgradeneeded = () => req.result.createObjectStore('t')
          await new Promise<void>((resolve, reject) => {
            req.onsuccess = () => {
              req.result.close()
              resolve()
            }
            req.onerror = () => reject(req.error)
          })
          indexedDB.deleteDatabase('mdt-diag-probe')
          setCheck('indexeddb', { status: 'pass', detail: '可读写' })
        } catch (e: any) {
          setCheck('indexeddb', {
            status: 'fail',
            detail: e?.message || '打开失败',
            hint: '可能在无痕模式或存储已满。',
          })
        }
      }
    }

    setRunning(false)
  }

  useEffect(() => {
    // 自动跑一次,医生打开就能看到结果
    run().catch(() => {})
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const failed = checks.filter((c) => c.status === 'fail').length
  const warned = checks.filter((c) => c.status === 'warn').length
  const passed = checks.filter((c) => c.status === 'pass').length

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-lg font-semibold">🩺 临床上场前自检</h1>
        <p className="text-sm text-gray-500 mt-1">
          一分钟检查麦克风、网络、后端、缓存能力。任一项不绿请按提示修复后再上场。
        </p>
      </div>

      <div className="card flex items-center justify-between">
        <div className="text-sm">
          <span className="text-emerald-600 font-semibold">{passed}</span> 通过
          {warned > 0 && (
            <span className="ml-3 text-amber-600 font-semibold">{warned}</span>
          )}
          {warned > 0 && ' 警告'}
          {failed > 0 && (
            <span className="ml-3 text-rose-600 font-semibold">{failed}</span>
          )}
          {failed > 0 && ' 失败'}
        </div>
        <button
          className="btn btn-primary"
          onClick={() => run()}
          disabled={running}
          type="button"
        >
          {running ? '检查中…' : '重新检查'}
        </button>
      </div>

      <ul className="space-y-2">
        {checks.map((c) => (
          <li key={c.id} className={`card border ${classFor(c.status)} space-y-1`}>
            <div className="flex items-center justify-between">
              <div className="font-medium text-base">
                {emojiFor(c.status)} {c.name}
              </div>
            </div>
            {c.detail && (
              <div className="text-xs text-gray-600 break-words">{c.detail}</div>
            )}
            {c.hint && (
              <div className="text-xs text-gray-800 bg-white/60 rounded p-2 mt-1">
                💡 {c.hint}
              </div>
            )}
          </li>
        ))}
      </ul>

      {failed === 0 && warned === 0 && !running && (
        <div className="card bg-emerald-50 border-emerald-200 text-emerald-900 text-sm">
          ✨ 所有检查通过,可放心进入 MDT 流程。建议把本页加到主屏:
          <ul className="list-disc list-inside text-xs mt-1 text-emerald-800">
            <li>iPhone Safari:点底部分享 → 添加到主屏幕</li>
            <li>Android Chrome:点右上 ⋮ → 安装应用 / 添加到主屏幕</li>
          </ul>
        </div>
      )}
    </div>
  )
}
