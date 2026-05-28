'use client'

/**
 * 同意书门禁(Consent Gate)
 *
 * 行为:
 *  - 进入受保护页面(如 /cases)时,先 GET /api/v1/consent
 *  - 如果未签或政策版本已变 → 阻断渲染,弹出全屏 modal
 *  - modal 列出 §2 中所有用户必须确认事项(强制 checkbox)
 *  - 全部勾选才能点"我已阅读并同意"
 *  - POST /api/v1/consent 成功后渲染 children
 *
 * 红线:未签同意书的用户绝不能看到/触发任何上传、录音、AI 生成功能
 */
import { useEffect, useState } from 'react'
import { api } from '@/lib/api'

interface Props {
  children: React.ReactNode
}

const REQUIRED_AFFIRMATIONS = [
  '我承诺不在本工具中录入或上传任何可识别患者身份的真实姓名(使用化名/代号)',
  '我承诺在拍照前遮挡病历上的姓名/身份证/手机/住址/病历号',
  '我承诺不依赖本工具的输出做单独的临床决策,所有建议须我本人复核',
  '我承诺不向第三方分享未经我审核的 AI 原始输出',
  '我理解 MDT 录音(原音频)会上传到火山引擎豆包音频理解 API 做转写(服务条款约定不留存/不用于训练),转写后的脱敏文本才会进入云端 LLM 综合分析',
  '我已征得患者及参会医生口头同意录音(MDT 开场标准告知)',
]

export default function ConsentGate({ children }: Props) {
  const [loading, setLoading] = useState(true)
  const [accepted, setAccepted] = useState(false)
  const [policyVersion, setPolicyVersion] = useState<string>('')
  const [checked, setChecked] = useState<boolean[]>(
    () => Array(REQUIRED_AFFIRMATIONS.length).fill(false),
  )
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

  const allChecked = checked.every(Boolean)

  async function onAccept() {
    if (!allChecked) {
      setError('请逐条勾选所有承诺项')
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
            版本 {policyVersion} · 继续使用前请仔细阅读并逐条确认
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
              本工具<strong>绝不</strong>:接入医院内网 · 发送原始图片/音频到云端 ·
              向第三方分享数据 · 用于训练任何模型。
            </p>
            <p className="mt-2">
              <a
                href="/docs/privacy-policy"
                target="_blank"
                className="text-blue-600 underline"
              >
                查看完整隐私政策(docs/privacy-policy.md)
              </a>
            </p>
          </div>

          <div className="space-y-2 pt-2 border-t">
            <div className="text-sm font-medium text-gray-800">您必须确认:</div>
            {REQUIRED_AFFIRMATIONS.map((t, i) => (
              <label
                key={i}
                className="flex gap-2 items-start text-sm text-gray-700 cursor-pointer"
              >
                <input
                  type="checkbox"
                  className="mt-1"
                  checked={checked[i]}
                  onChange={(e) => {
                    const next = [...checked]
                    next[i] = e.target.checked
                    setChecked(next)
                  }}
                />
                <span>{t}</span>
              </label>
            ))}
          </div>
        </div>

        <div className="px-5 py-3 border-t bg-gray-50 rounded-b-2xl">
          {error && (
            <div className="text-xs text-red-600 mb-2">{error}</div>
          )}
          <button
            className="btn btn-primary w-full"
            disabled={!allChecked || submitting}
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
