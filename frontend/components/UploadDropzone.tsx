'use client'

import { useRef, useState } from 'react'
import { api } from '@/lib/api'

interface FileRow {
  name: string
  status: 'uploading' | 'done' | 'failed'
  message?: string
}

export default function UploadDropzone({
  sessionId,
  onDone,
}: {
  sessionId: string
  onDone?: () => void
}) {
  const inputRef = useRef<HTMLInputElement>(null)
  const cameraRef = useRef<HTMLInputElement>(null)
  const [files, setFiles] = useState<FileRow[]>([])
  const [busy, setBusy] = useState(false)

  function classify(file: File): string {
    const name = file.name.toLowerCase()
    if (file.type.startsWith('image/')) return 'image'
    if (name.endsWith('.pdf')) return 'pdf'
    if (name.endsWith('.docx') || name.endsWith('.doc')) return 'doc'
    return 'other'
  }

  async function uploadOne(file: File): Promise<void> {
    const file_type = classify(file)
    const idx = files.length
    setFiles((prev) => [...prev, { name: file.name, status: 'uploading' }])
    try {
      const { record_id, upload_url } = await api.presignFile({
        session_id: sessionId,
        filename: file.name,
        file_type,
        mime_type: file.type || undefined,
      })
      const putRes = await fetch(upload_url, {
        method: 'PUT',
        body: file,
        headers: { 'Content-Type': file.type || 'application/octet-stream' },
      })
      if (!putRes.ok) throw new Error(`PUT ${putRes.status}`)
      // 触发 OCR
      if (file_type === 'image' || file_type === 'pdf') {
        await api.triggerOcr(record_id)
      }
      setFiles((prev) =>
        prev.map((f, i) => (f.name === file.name ? { ...f, status: 'done' } : f)),
      )
    } catch (e: any) {
      setFiles((prev) =>
        prev.map((f) =>
          f.name === file.name ? { ...f, status: 'failed', message: e.message } : f,
        ),
      )
    }
  }

  async function handleFiles(list: FileList | null) {
    if (!list) return
    setBusy(true)
    try {
      const arr = Array.from(list)
      // 串行上传,避免预签名峰值
      for (const f of arr) {
        await uploadOne(f)
      }
      onDone?.()
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="space-y-3">
      <div className="grid grid-cols-2 gap-2">
        <button
          className="btn btn-secondary py-3"
          onClick={() => cameraRef.current?.click()}
          disabled={busy}
        >
          📸 拍照/扫描
        </button>
        <button
          className="btn btn-secondary py-3"
          onClick={() => inputRef.current?.click()}
          disabled={busy}
        >
          📎 上传文件
        </button>
      </div>
      <input
        ref={cameraRef}
        type="file"
        accept="image/*"
        capture="environment"
        multiple
        className="hidden"
        onChange={(e) => handleFiles(e.target.files)}
      />
      <input
        ref={inputRef}
        type="file"
        accept="image/*,application/pdf,.doc,.docx"
        multiple
        className="hidden"
        onChange={(e) => handleFiles(e.target.files)}
      />

      {files.length > 0 && (
        <ul className="space-y-1 text-xs">
          {files.map((f, i) => (
            <li key={i} className="flex items-center justify-between">
              <span className="truncate flex-1 mr-2">{f.name}</span>
              {f.status === 'uploading' && <span className="tag-processing">上传中</span>}
              {f.status === 'done' && <span className="tag-done">已传</span>}
              {f.status === 'failed' && (
                <span className="tag-failed" title={f.message}>失败</span>
              )}
            </li>
          ))}
        </ul>
      )}

      <p className="text-xs text-gray-400">
        支持化验单、病理报告、影像报告(图片/PDF)。上传后将自动 OCR 识别。
      </p>
    </div>
  )
}
