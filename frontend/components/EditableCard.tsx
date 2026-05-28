'use client'

import { useState } from 'react'
import { api, humanizeError } from '@/lib/api'
import { useToast } from '@/components/Toast'

interface Props {
  sessionId: string
  title: string
  fieldPath: string
  value: any
  multiline?: boolean
  lowConfidence?: boolean
  regenSection?: string
  children?: React.ReactNode
  onSaved?: () => void
}

export default function EditableCard({
  sessionId,
  title,
  fieldPath,
  value,
  multiline = true,
  lowConfidence = false,
  regenSection,
  children,
  onSaved,
}: Props) {
  const toast = useToast()
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState<string>(typeof value === 'string' ? value : JSON.stringify(value ?? '', null, 2))
  const [busy, setBusy] = useState(false)

  async function save() {
    setBusy(true)
    try {
      let next: any = draft
      if (typeof value !== 'string') {
        try {
          next = JSON.parse(draft)
        } catch {
          toast.warn('JSON 格式不正确,请检查括号/引号是否成对')
          setBusy(false)
          return
        }
      }
      await api.editField(sessionId, fieldPath, next)
      toast.success('已保存')
      setEditing(false)
      onSaved?.()
    } catch (e: any) {
      toast.error('保存失败:' + humanizeError(e))
    } finally {
      setBusy(false)
    }
  }

  async function regen() {
    if (!regenSection) return
    if (!confirm(`重新生成「${title}」?当前内容将被覆盖。`)) return
    setBusy(true)
    try {
      await api.regenerate(sessionId, regenSection)
      toast.info(`已触发重新生成,约 1 分钟后刷新此卡片`)
      onSaved?.()
    } catch (e: any) {
      toast.error('重新生成失败:' + humanizeError(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className={'card ' + (lowConfidence ? 'border-rose-300 border-2' : '')}>
      <div className="flex items-center justify-between mb-2">
        <h3 className="font-medium text-sm flex items-center gap-2">
          {title}
          {lowConfidence && (
            <span className="tag-failed">低置信度 · 请复核</span>
          )}
        </h3>
        <div className="flex gap-1">
          {regenSection && (
            <button
              className="text-xs text-gray-500 hover:text-brand-600"
              onClick={regen}
              disabled={busy}
            >
              ↻ 重新生成
            </button>
          )}
          {!editing ? (
            <button
              className="text-xs text-brand-600 hover:underline"
              onClick={() => setEditing(true)}
            >
              ✎ 编辑
            </button>
          ) : (
            <>
              <button
                className="text-xs text-gray-500"
                onClick={() => {
                  setEditing(false)
                  setDraft(typeof value === 'string' ? value : JSON.stringify(value ?? '', null, 2))
                }}
              >
                取消
              </button>
              <button
                className="text-xs text-brand-600 hover:underline font-medium ml-2"
                onClick={save}
                disabled={busy}
              >
                {busy ? '保存中…' : '保存'}
              </button>
            </>
          )}
        </div>
      </div>

      {editing ? (
        multiline ? (
          <textarea
            className="w-full border rounded-md px-2 py-1.5 text-sm"
            rows={Math.min(10, Math.max(3, draft.split('\n').length))}
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
          />
        ) : (
          <input
            className="w-full border rounded-md px-2 py-1.5 text-sm"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
          />
        )
      ) : (
        <div className="text-sm text-gray-800">{children}</div>
      )}
    </div>
  )
}
