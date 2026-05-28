'use client'

import { useState } from 'react'
import { useRouter, useParams } from 'next/navigation'
import { api, humanizeError } from '@/lib/api'
import { useToast } from '@/components/Toast'

type Format = 'docx' | 'pdf' | 'pptx' | 'wechat_card'

const LABELS: Record<Format, string> = {
  docx: '📄 Word 文档(.docx)',
  pdf: '📕 PDF 报告',
  pptx: '🖥 PPT 汇报版',
  wechat_card: '💬 微信卡片(纯文本)',
}

export default function ExportPage() {
  const router = useRouter()
  const toast = useToast()
  const params = useParams() as { id: string }
  const sessionId = params.id

  const [busy, setBusy] = useState<Format | null>(null)
  const [wechatCard, setWechatCard] = useState<string | null>(null)
  const [confirmed, setConfirmed] = useState(false)

  async function doExport(format: Format) {
    if (!confirmed) {
      toast.warn('请先勾选"已阅读并确认 AI 辅助 + 需医生复核"')
      return
    }
    setBusy(format)
    try {
      const result = await api.exportReport(sessionId, format)
      if (format === 'wechat_card') {
        setWechatCard((result as any).card)
      } else {
        const blob = result as Blob
        const ext = format
        const url = URL.createObjectURL(blob)
        const a = document.createElement('a')
        a.href = url
        a.download = `MDT-${sessionId.slice(0, 8)}.${ext}`
        document.body.appendChild(a)
        a.click()
        document.body.removeChild(a)
        URL.revokeObjectURL(url)
      }
    } catch (e: any) {
      toast.error('导出失败:' + humanizeError(e))
    } finally {
      setBusy(null)
    }
  }

  async function copyCard() {
    if (!wechatCard) return
    try {
      await navigator.clipboard.writeText(wechatCard)
      toast.success('已复制,可直接粘贴到微信')
    } catch {
      toast.warn('剪贴板不可用,请手动选择文本复制')
    }
  }

  return (
    <div className="space-y-4">
      <button
        className="text-sm text-gray-500 hover:text-gray-700"
        onClick={() => router.push(`/cases/${sessionId}/review`)}
      >
        ← 返回确认页
      </button>

      <h1 className="text-lg font-semibold">导出报告</h1>

      <div className="card bg-amber-50 border-amber-200">
        <h3 className="font-medium text-sm mb-2">⚠️ 重要提示</h3>
        <ul className="text-xs text-amber-800 space-y-1 list-disc list-inside">
          <li>本报告为 AI 辅助生成,所有内容需主治医师复核后方可使用</li>
          <li>报告页和导出文件均包含"AI 辅助,需医生复核"水印,不可移除</li>
          <li>治疗建议仅供参考,不能替代医生临床判断</li>
          <li>给患者的话术不承诺疗效,请医生口头宣讲时进一步核对</li>
        </ul>
        <label className="flex items-center gap-2 mt-3 text-sm cursor-pointer">
          <input
            type="checkbox"
            checked={confirmed}
            onChange={(e) => setConfirmed(e.target.checked)}
          />
          <span>我已阅读并理解上述提示,确认导出</span>
        </label>
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
        {(Object.keys(LABELS) as Format[]).map((f) => (
          <button
            key={f}
            className="card hover:bg-gray-50 text-left"
            onClick={() => doExport(f)}
            disabled={!!busy || !confirmed}
          >
            <div className="font-medium text-sm mb-1">{LABELS[f]}</div>
            <div className="text-xs text-gray-500">
              {busy === f ? '生成中…' : '点击下载'}
            </div>
          </button>
        ))}
      </div>

      {wechatCard && (
        <div className="card space-y-2">
          <div className="flex items-center justify-between">
            <h3 className="font-medium text-sm">微信卡片预览</h3>
            <button className="btn btn-primary py-1 px-3 text-sm" onClick={copyCard}>
              复制到剪贴板
            </button>
          </div>
          <textarea
            className="w-full border rounded-md px-2 py-1.5 text-xs font-mono"
            rows={16}
            readOnly
            value={wechatCard}
          />
        </div>
      )}

      <p className="text-xs italic text-rose-600 text-center">
        AI 辅助生成,需主治医师复核
      </p>
    </div>
  )
}
