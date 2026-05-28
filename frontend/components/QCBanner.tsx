'use client'

interface QCIssue {
  field?: string
  severity: 'low' | 'medium' | 'high' | 'critical'
  message: string
  must_fix?: boolean
}

interface QCReport {
  passed?: boolean
  issues?: QCIssue[] | null
  must_fix?: string[]
}

export default function QCBanner({ qc }: { qc?: QCReport | null }) {
  if (!qc) return null
  const issues = (qc.issues || []) as QCIssue[]
  if (issues.length === 0) {
    return (
      <div className="card bg-emerald-50 border-emerald-200">
        <div className="flex items-center gap-2 text-sm text-emerald-800">
          <span className="font-medium">✓ QC 通过</span>
          <span className="text-xs">未发现幻觉/字段缺失/承诺过度等问题</span>
        </div>
      </div>
    )
  }
  const critical = issues.filter((i) => i.severity === 'critical' || i.must_fix)
  const others = issues.filter((i) => i.severity !== 'critical' && !i.must_fix)
  return (
    <div className={'card ' + (critical.length > 0 ? 'border-rose-300 bg-rose-50' : 'border-amber-300 bg-amber-50')}>
      <h3 className="font-medium text-sm mb-2">
        {critical.length > 0 ? '⚠ 严重问题需修正后才能导出' : '🟡 QC 提醒'}
      </h3>
      {critical.length > 0 && (
        <ul className="text-sm space-y-1 mb-2">
          {critical.map((i, idx) => (
            <li key={idx} className="text-rose-800">
              <span className="font-medium">[{i.field || '通用'}]</span> {i.message}
            </li>
          ))}
        </ul>
      )}
      {others.length > 0 && (
        <ul className="text-xs space-y-0.5 text-amber-800">
          {others.map((i, idx) => (
            <li key={idx}>
              <span className="font-medium">[{i.field || '通用'}]</span> {i.message}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
