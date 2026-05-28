'use client'

import { useState } from 'react'
import { api, humanizeError } from '@/lib/api'
import { useToast } from '@/components/Toast'

interface TNM {
  tnm_type?: string
  t_stage?: string
  n_stage?: string
  m_stage?: string
  overall_stage?: string
  basis?: string
  uncertainty?: string
  confidence?: number
}

export default function TNMStageCard({
  sessionId,
  tnm,
  onSaved,
}: {
  sessionId: string
  tnm: TNM | null
  onSaved?: () => void
}) {
  const toast = useToast()
  const low = !tnm || (tnm.confidence ?? 0) < 0.7
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState<TNM>(tnm || {})
  const [busy, setBusy] = useState(false)

  async function saveAll() {
    setBusy(true)
    try {
      const fields: (keyof TNM)[] = [
        'tnm_type', 't_stage', 'n_stage', 'm_stage',
        'overall_stage', 'basis', 'uncertainty',
      ]
      for (const f of fields) {
        const cur = (tnm as any)?.[f] ?? ''
        const next = (draft as any)?.[f] ?? ''
        if (cur !== next) {
          await api.editField(sessionId, `tnm.${f}`, next)
        }
      }
      toast.success('TNM 已保存')
      setEditing(false)
      onSaved?.()
    } catch (e: any) {
      toast.error('保存失败:' + humanizeError(e))
    } finally {
      setBusy(false)
    }
  }

  async function regen() {
    if (!confirm('重新生成「TNM 分期」?')) return
    setBusy(true)
    try {
      await api.regenerate(sessionId, 'tnm')
      toast.info('已触发重新生成,约 1 分钟后刷新')
      onSaved?.()
    } catch (e: any) {
      toast.error('重新生成失败:' + humanizeError(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className={'card ' + (low ? 'border-rose-300 border-2' : '')}>
      <div className="flex items-center justify-between mb-2">
        <h3 className="font-medium text-sm flex items-center gap-2">
          ④ TNM 分期
          {low && <span className="tag-failed">低置信度 · 请复核</span>}
        </h3>
        <div className="flex gap-2 text-xs">
          <button className="text-gray-500 hover:text-brand-600" onClick={regen} disabled={busy}>
            ↻ 重新生成
          </button>
          {!editing ? (
            <button className="text-brand-600 hover:underline" onClick={() => { setEditing(true); setDraft(tnm || {}) }}>
              ✎ 编辑
            </button>
          ) : (
            <>
              <button className="text-gray-500" onClick={() => setEditing(false)}>取消</button>
              <button className="text-brand-600 font-medium hover:underline" onClick={saveAll} disabled={busy}>
                {busy ? '保存中…' : '保存'}
              </button>
            </>
          )}
        </div>
      </div>

      {!tnm && !editing ? (
        <div className="text-sm text-gray-400">尚未生成</div>
      ) : editing ? (
        <div className="space-y-2 text-sm">
          <div className="grid grid-cols-4 gap-2">
            <select
              className="border rounded px-2 py-1 text-sm"
              value={draft.tnm_type || ''}
              onChange={(e) => setDraft({ ...draft, tnm_type: e.target.value })}
            >
              <option value="">类型</option>
              <option value="cTNM">cTNM</option>
              <option value="pTNM">pTNM</option>
              <option value="ycTNM">ycTNM</option>
              <option value="ypTNM">ypTNM</option>
              <option value="rTNM">rTNM</option>
            </select>
            <input className="border rounded px-2 py-1 text-sm" placeholder="T" value={draft.t_stage || ''} onChange={(e) => setDraft({ ...draft, t_stage: e.target.value })} />
            <input className="border rounded px-2 py-1 text-sm" placeholder="N" value={draft.n_stage || ''} onChange={(e) => setDraft({ ...draft, n_stage: e.target.value })} />
            <input className="border rounded px-2 py-1 text-sm" placeholder="M" value={draft.m_stage || ''} onChange={(e) => setDraft({ ...draft, m_stage: e.target.value })} />
          </div>
          <input className="border rounded px-2 py-1 text-sm w-full" placeholder="总体分期(如 IIIA)" value={draft.overall_stage || ''} onChange={(e) => setDraft({ ...draft, overall_stage: e.target.value })} />
          <textarea className="border rounded px-2 py-1 text-sm w-full" rows={2} placeholder="依据" value={draft.basis || ''} onChange={(e) => setDraft({ ...draft, basis: e.target.value })} />
          <textarea className="border rounded px-2 py-1 text-sm w-full" rows={2} placeholder="不确定项" value={draft.uncertainty || ''} onChange={(e) => setDraft({ ...draft, uncertainty: e.target.value })} />
        </div>
      ) : (
        <div className="space-y-2">
          <div className="grid grid-cols-4 gap-2 text-sm">
            <Cell label="类型" value={tnm!.tnm_type} />
            <Cell label="T" value={tnm!.t_stage} />
            <Cell label="N" value={tnm!.n_stage} />
            <Cell label="M" value={tnm!.m_stage} />
          </div>
          <div className="text-sm">
            <span className="text-gray-500">总体分期:</span>
            <span className="font-medium">{tnm!.overall_stage || '—'}</span>
          </div>
          {tnm!.basis && (
            <div className="text-xs text-gray-600">
              <span className="text-gray-400">依据:</span>{tnm!.basis}
            </div>
          )}
          {tnm!.uncertainty && (
            <div className="text-xs text-amber-700">
              <span className="text-amber-500">⚠ 不确定项:</span>{tnm!.uncertainty}
            </div>
          )}
          <div className="text-xs text-gray-400">
            置信度:{((tnm!.confidence ?? 0) * 100).toFixed(0)}%
          </div>
        </div>
      )}
    </div>
  )
}

function Cell({ label, value }: { label: string; value?: string }) {
  return (
    <div className="bg-gray-50 rounded px-2 py-1.5">
      <div className="text-xs text-gray-400">{label}</div>
      <div className="font-medium">{value || '—'}</div>
    </div>
  )
}
