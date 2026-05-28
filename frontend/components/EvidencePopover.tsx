'use client'

import { useState } from 'react'

export default function EvidencePopover({
  snippet,
  source,
}: {
  snippet?: string | null
  source?: string | null
}) {
  const [open, setOpen] = useState(false)
  if (!snippet) return null
  return (
    <span className="relative inline-block">
      <button
        type="button"
        className="text-xs text-brand-600 hover:underline ml-1"
        onClick={() => setOpen((v) => !v)}
      >
        [依据]
      </button>
      {open && (
        <span className="absolute z-10 left-0 top-full mt-1 w-72 bg-white border border-gray-200 rounded-md shadow-lg p-2 text-xs text-gray-700">
          {source && <div className="text-gray-400 mb-1">来源:{source}</div>}
          <div className="whitespace-pre-wrap">"{snippet}"</div>
        </span>
      )}
    </span>
  )
}
