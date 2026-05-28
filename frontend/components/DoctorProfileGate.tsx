'use client'

/**
 * 医生身份门禁(Doctor Profile Gate)
 *
 * 为什么需要:
 *  - MVP 阶段没有正式微信登录,但审计 / 报告署名都需要"医生本人"标识
 *  - 多人共用同一台设备(科室手机/iPad)时,光靠 device_id 不够 — 必须问一次姓名
 *  - 不强制医院/科室(避免阻塞首次使用),但鼓励填,因为科室会影响后续 MDT 角色提示
 *
 * 行为:
 *  - 进入受保护页面时,从 localStorage(`tb_doctor_profile`)读取
 *  - 若不存在或姓名为空 → 阻断渲染,弹出全屏 modal
 *  - 用户填完点"确定" → 写 localStorage + 调用 /api/v1/auth/login 持久化到 users 表
 *  - 之后所有 API 请求经由 device_id 与该医生关联,audit_log 的 actor_id 解析到 User.name
 *
 * 红线:
 *  - 这里问的是【医生本人姓名】,不是患者!modal 文案必须明确,避免医生误填患者真名
 *  - 不强制医院/科室(用户体验优先),但 placeholder 给出标准格式
 *  - localStorage 落地,刷新页面/重启浏览器都不丢
 */
import { useEffect, useState } from 'react'
import { api, humanizeError } from '@/lib/api'

interface Props {
  children: React.ReactNode
}

interface DoctorProfile {
  name: string
  hospital: string
  dept: string
}

const STORAGE_KEY = 'tb_doctor_profile'

export function getDoctorProfile(): DoctorProfile | null {
  if (typeof window === 'undefined') return null
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY)
    if (!raw) return null
    const p = JSON.parse(raw)
    if (!p?.name) return null
    return { name: String(p.name), hospital: String(p.hospital || ''), dept: String(p.dept || '') }
  } catch {
    return null
  }
}

export function clearDoctorProfile() {
  if (typeof window === 'undefined') return
  try {
    window.localStorage.removeItem(STORAGE_KEY)
  } catch {}
}

export default function DoctorProfileGate({ children }: Props) {
  const [loading, setLoading] = useState(true)
  const [profile, setProfile] = useState<DoctorProfile | null>(null)
  const [name, setName] = useState('')
  const [hospital, setHospital] = useState('')
  const [dept, setDept] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    const p = getDoctorProfile()
    setProfile(p)
    if (p) {
      setName(p.name)
      setHospital(p.hospital)
      setDept(p.dept)
    }
    setLoading(false)
  }, [])

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault()
    const n = name.trim()
    if (!n) {
      setError('请填写您的姓名')
      return
    }
    if (n.length > 30) {
      setError('姓名过长(最多 30 字)')
      return
    }
    setSubmitting(true)
    setError('')
    try {
      // 同步到后端 users 表 — best-effort,即便后端临时不可用也允许进入(localStorage 已落)
      try {
        await api.login(n, hospital.trim() || undefined, dept.trim() || undefined)
      } catch (e) {
        // 不阻塞:服务端会在下次调用时通过 device_id 自动建用户
        console.warn('[DoctorProfileGate] login sync failed (will retry on next request):', e)
      }
      const p: DoctorProfile = {
        name: n,
        hospital: hospital.trim(),
        dept: dept.trim(),
      }
      window.localStorage.setItem(STORAGE_KEY, JSON.stringify(p))
      // 通知头条/其它组件刷新
      window.dispatchEvent(new Event('tb:doctor-changed'))
      setProfile(p)
    } catch (e) {
      setError(humanizeError(e))
    } finally {
      setSubmitting(false)
    }
  }

  if (loading) {
    return <div className="text-center text-gray-500 py-12">加载中…</div>
  }

  if (profile) return <>{children}</>

  // 未填 → 全屏 modal
  return (
    <div className="fixed inset-0 z-40 bg-black/50 flex items-end sm:items-center justify-center p-0 sm:p-4">
      <form
        onSubmit={onSubmit}
        className="bg-white w-full sm:max-w-md rounded-t-2xl sm:rounded-2xl flex flex-col max-h-[95vh]"
      >
        <div className="px-5 pt-5 pb-2 border-b">
          <h2 className="text-base font-semibold">请确认您的身份</h2>
          <p className="text-xs text-gray-500 mt-1">
            用于报告署名 / 审计记录。<strong className="text-rose-600">填您自己的姓名,不是患者姓名</strong>。
          </p>
        </div>

        <div className="px-5 py-4 space-y-3 overflow-y-auto flex-1">
          <div>
            <label className="block text-xs text-gray-600 mb-1">
              您的姓名 <span className="text-rose-600">*</span>
            </label>
            <input
              type="text"
              required
              maxLength={30}
              autoFocus
              className="w-full border rounded-md px-3 py-2 text-sm"
              placeholder="例如:张主任"
              value={name}
              onChange={(e) => setName(e.target.value)}
            />
          </div>

          <div>
            <label className="block text-xs text-gray-600 mb-1">医院(可选)</label>
            <input
              type="text"
              maxLength={50}
              className="w-full border rounded-md px-3 py-2 text-sm"
              placeholder="例如:复旦大学附属肿瘤医院"
              value={hospital}
              onChange={(e) => setHospital(e.target.value)}
            />
          </div>

          <div>
            <label className="block text-xs text-gray-600 mb-1">科室(可选)</label>
            <input
              type="text"
              maxLength={30}
              className="w-full border rounded-md px-3 py-2 text-sm"
              placeholder="例如:胸外科 / 肿瘤内科 / 放疗科"
              value={dept}
              onChange={(e) => setDept(e.target.value)}
            />
          </div>

          <div className="text-[11px] text-gray-500 leading-relaxed pt-1">
            信息仅保存在本机 + 后端 users 表(用于 audit 关联)。<br />
            如需更换医生,可在页面底部「切换医生」清除并重填。
          </div>
        </div>

        <div className="px-5 py-3 border-t bg-gray-50 rounded-b-2xl">
          {error && <div className="text-xs text-red-600 mb-2">{error}</div>}
          <button
            type="submit"
            className="btn btn-primary w-full"
            disabled={submitting}
          >
            {submitting ? '保存中…' : '进入工作台'}
          </button>
        </div>
      </form>
    </div>
  )
}
