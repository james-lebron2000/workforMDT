'use client'

/**
 * 顶部头条 — 显示品牌 + 当前医生 + AI 复核提醒。
 *
 * 为什么是独立 client 组件:
 *  - localStorage 只能在浏览器读;layout.tsx 是 server component,直接 useState 会报错
 *  - 医生切换后要立刻更新头条名字,所以监听 storage 事件 + 自定义事件 'tb:doctor-changed'
 */

import { useEffect, useState } from 'react'
import { clearDoctorProfile, getDoctorProfile } from './DoctorProfileGate'

export default function AppHeader() {
  const [name, setName] = useState<string>('')
  const [dept, setDept] = useState<string>('')

  useEffect(() => {
    const sync = () => {
      const p = getDoctorProfile()
      setName(p?.name || '')
      setDept(p?.dept || '')
    }
    sync()
    // 跨标签页 / 跨组件同步
    window.addEventListener('storage', sync)
    window.addEventListener('tb:doctor-changed', sync as any)
    return () => {
      window.removeEventListener('storage', sync)
      window.removeEventListener('tb:doctor-changed', sync as any)
    }
  }, [])

  function onSwitchDoctor() {
    if (!confirm('切换医生?当前页将刷新,需要重新填写身份信息。\n(本机已有的病例数据不会丢。)')) return
    clearDoctorProfile()
    window.dispatchEvent(new Event('tb:doctor-changed'))
    // 刷一下回到 /cases,让 Gate 重新拦
    window.location.href = '/cases'
  }

  return (
    <header className="bg-white border-b border-gray-100 sticky top-0 z-30 pt-safe">
      <div className="max-w-3xl mx-auto px-4 py-3 flex items-center justify-between gap-2">
        <a href="/cases" className="font-semibold text-brand-600 text-base shrink-0">
          TumorBoard AI
        </a>

        <div className="flex-1 min-w-0 flex items-center justify-end gap-2">
          {name && (
            <button
              type="button"
              onClick={onSwitchDoctor}
              className="text-[11px] text-gray-600 hover:text-gray-900 truncate max-w-[40vw] underline-offset-2 hover:underline"
              title="点击切换医生"
            >
              {name}
              {dept ? ` · ${dept}` : ''}
            </button>
          )}
          <span className="text-[11px] text-rose-600 italic shrink-0">
            AI 辅助,需复核
          </span>
        </div>
      </div>
    </header>
  )
}
