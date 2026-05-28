'use client'

import EvidencePopover from './EvidencePopover'

interface Opinion {
  id?: string
  department: string
  doctor_label?: string | null
  opinion?: string | null
  rationale?: string | null
  recommendation?: string | null
  evidence_source?: string | null
  evidence_snippet?: string | null
  confidence?: number
  is_missing?: boolean
}

const DEPT_ORDER = ['外科', '肿瘤内科', '放射科', '放疗科', '介入科', '病理科']

export default function DeptOpinionCard({ opinions }: { opinions: Opinion[] }) {
  // 按 6 大核心科室排序,其他追加
  const sorted = [...opinions].sort((a, b) => {
    const ia = DEPT_ORDER.indexOf(a.department)
    const ib = DEPT_ORDER.indexOf(b.department)
    if (ia === -1 && ib === -1) return a.department.localeCompare(b.department)
    if (ia === -1) return 1
    if (ib === -1) return -1
    return ia - ib
  })

  return (
    <div className="card space-y-3">
      <h3 className="font-medium text-sm">多学科医生意见</h3>
      <ul className="space-y-3">
        {sorted.map((o, i) => (
          <li
            key={o.id || i}
            className={
              'p-3 rounded-md border ' +
              (o.is_missing
                ? 'border-amber-300 bg-amber-50'
                : 'border-gray-200 bg-gray-50')
            }
          >
            <div className="flex items-center justify-between mb-1">
              <span className="font-medium text-sm">{o.department}</span>
              {o.is_missing ? (
                <span className="tag-failed">本次未明确记录</span>
              ) : (
                <span className="text-xs text-gray-400">
                  置信度 {((o.confidence ?? 0) * 100).toFixed(0)}%
                </span>
              )}
            </div>
            {o.is_missing ? (
              <p className="text-xs text-amber-700">
                录音中未识别到该科室发言,建议补充。
              </p>
            ) : (
              <div className="text-sm space-y-1">
                {o.opinion && (
                  <div>
                    <span className="text-gray-500">意见:</span>
                    {o.opinion}
                    <EvidencePopover snippet={o.evidence_snippet} source={o.evidence_source} />
                  </div>
                )}
                {o.rationale && (
                  <div className="text-xs text-gray-600">
                    <span className="text-gray-400">理由:</span>{o.rationale}
                  </div>
                )}
                {o.recommendation && (
                  <div className="text-xs text-brand-700">
                    <span className="text-gray-400">建议:</span>{o.recommendation}
                  </div>
                )}
              </div>
            )}
          </li>
        ))}
      </ul>
    </div>
  )
}
