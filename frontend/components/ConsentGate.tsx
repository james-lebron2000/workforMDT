'use client'

/**
 * 同意书门禁(Consent Gate)
 *
 * 行为:
 *  - 进入受保护页面(如 /cases)时,先 GET /api/v1/consent
 *  - 如果未签或政策版本已变 → 阻断渲染,弹出全屏 modal
 *  - modal 用 1 条汇总承诺 + 显眼的"完整隐私政策"链接(详情列在 docs/privacy-policy.md §2)
 *  - 勾选承诺后才能点"我已阅读并同意"
 *  - POST /api/v1/consent 成功后渲染 children
 *
 * 红线:未签同意书的用户绝不能看到/触发任何上传、录音、AI 生成功能
 */
import { useEffect, useState } from 'react'
import { api } from '@/lib/api'

interface Props {
  children: React.ReactNode
}

// 单条汇总承诺 — 完整 6 条医生义务详情见 docs/privacy-policy.md §2
const AFFIRMATION =
  '我已阅读并同意《隐私政策与使用同意书》,理解原音频会上传火山引擎转写、所有 AI 输出仅供参考须本人复核;承诺仅使用患者化名/代号,录音前已征得患者及参会医生口头同意。'

export default function ConsentGate({ children }: Props) {
  const [loading, setLoading] = useState(true)
  const [accepted, setAccepted] = useState(false)
  const [policyVersion, setPolicyVersion] = useState<string>('')
  const [checked, setChecked] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string>('')

  useEffect(() => {
    api
      .getConsent()
      .then((d) => {
        setPolicyVersion(d.policy_version)
        setAccepted(d.accepted)
      })
      .catch((e) => setError('无法连接到服务器:' + e.message))
      .finally(() => setLoading(false))
  }, [])

  async function onAccept() {
    if (!checked) {
      setError('请先勾选承诺项')
      return
    }
    setSubmitting(true)
    setError('')
    try {
      await api.acceptConsent(policyVersion)
      setAccepted(true)
    } catch (e: any) {
      setError(e.message)
    } finally {
      setSubmitting(false)
    }
  }

  if (loading) {
    return (
      <div className="text-center text-gray-500 py-12">加载中…</div>
    )
  }

  if (accepted) return <>{children}</>

  // 未签 → 全屏 modal
  return (
    <div className="fixed inset-0 z-50 bg-black/50 flex items-end sm:items-center justify-center p-0 sm:p-4">
      <div className="bg-white w-full sm:max-w-2xl rounded-t-2xl sm:rounded-2xl flex flex-col max-h-[95vh]">
        <div className="px-5 pt-5 pb-2 border-b">
          <h2 className="text-base font-semibold">隐私政策与使用同意书</h2>
          <p className="text-xs text-gray-500 mt-1">
            版本 {policyVersion} · 继续使用前请仔细阅读并勾选确认
          </p>
        </div>

        <div className="overflow-y-auto px-5 py-4 space-y-3 flex-1">
          <div className="text-sm text-gray-700 leading-relaxed">
            <p>
              <strong>TumorBoard AI</strong> 是医生个人辅助整理工具,<strong>不</strong>是医疗器械,
              <strong>不</strong>接入医院 HIS/EMR/PACS。所有 AI 产出均带"AI 辅助生成,
              需主治医师复核"水印,<strong>最终临床决策必须由您本人独立做出</strong>。
            </p>
            <p className="mt-2">
              本工具<strong>绝不</strong>:接入医院内网 · 发送原始图片/音频到第三方 ·
              用于训练任何模型。
            </p>
          </div>

          <a
            href="/docs/privacy-policy"
            target="_blank"
            rel="noopener noreferrer"
            className="block rounded-lg border border-blue-300 bg-blue-50 px-4 py-3 hover:bg-blue-100 transition"
          >
            <div className="flex items-center justify-between">
              <span className="text-sm font-medium text-blue-900">
                📄 查看完整《隐私政策与使用同意书》
              </span>
              <span className="text-blue-600">→</span>
            </div>
            <p className="text-xs text-blue-700 mt-1">
              包含数据流向、火山引擎转写细节、6 条医生义务详情
            </p>
          </a>

          <label className="flex gap-2 items-start text-sm text-gray-800 cursor-pointer rounded-lg border border-gray-300 px-3 py-3 hover:bg-gray-50">
            <input
              type="checkbox"
              className="mt-1 w-4 h-4 shrink-0"
              checked={checked}
              onChange={(e) => setChecked(e.target.checked)}
            />
            <span className="leading-relaxed">{AFFIRMATION}</span>
          </label>
        </div>

        <div className="px-5 py-3 border-t bg-gray-50 rounded-b-2xl">
          {error && (
            <div className="text-xs text-red-600 mb-2">{error}</div>
          )}
          <button
            className="btn btn-primary w-full min-h-11"
            disabled={!checked || submitting}
            onClick={onAccept}
          >
            {submitting ? '提交中…' : '我已阅读并同意'}
          </button>
          <p className="text-[11px] text-gray-500 mt-2 text-center">
            点击同意后会记录:user_id / policy_version / 时间戳 / IP(30 天后哈希化)
          </p>
        </div>
      </div>
    </div>
  )
}
