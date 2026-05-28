'use client'

import { useEffect, useState } from 'react'
import { useRouter } from 'next/navigation'
import { api, humanizeError } from '@/lib/api'
import ConsentGate from '@/components/ConsentGate'
import DoctorProfileGate from '@/components/DoctorProfileGate'
import { useToast } from '@/components/Toast'

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

  useEffect(() => {
    api.listSessions().then((d) => setList(d.sessions)).catch(console.error)
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

  return (
    <DoctorProfileGate>
    <ConsentGate>
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-lg font-semibold">病例列表</h1>
        <button className="btn btn-primary" onClick={() => setShowCreate(true)}>
          + 新建 MDT
        </button>
      </div>

      {list.length === 0 && (
        <div className="card text-gray-500 text-center py-8">
          还没有 MDT 病例。点击右上角"新建 MDT"开始。
        </div>
      )}

      <ul className="space-y-2">
        {list.map((s) => (
          <li
            key={s.id}
            className="card flex items-center justify-between cursor-pointer hover:bg-gray-50"
            onClick={() => router.push(`/cases/${s.id}/upload`)}
          >
            <div>
              <div className="font-medium">{s.title || `MDT-${s.patient_code}`}</div>
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
        ))}
      </ul>

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
