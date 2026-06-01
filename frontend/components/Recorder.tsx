'use client'

import { useEffect, useRef, useState } from 'react'
import { api } from '@/lib/api'
import { getDeviceId } from '@/lib/device'
import {
  clearVoice,
  deleteChunk,
  listChunks,
  listOrphansForSession,
  saveChunk,
  type OrphanGroup,
} from '@/lib/recorderStorage'
import { useToast } from '@/components/Toast'

type VoiceType = 'patient_request' | 'mdt_discussion'

interface RecorderProps {
  /** Session 模式:这次录音绑定到单个 session(原有用法) */
  sessionId?: string
  voiceType?: VoiceType
  /** Meeting 模式:整段群组录音,后端预生成 voice_id,前端只负责上传分片 + finalize */
  meetingId?: string
  presetVoiceId?: string
  label: string
  chunkSeconds?: number
  /**
   * session 模式:回调收 voice_id
   * meeting 模式:回调时已 finalize(mp3 拼好),由父组件决定何时调 /finalize 触发 ASR+切分
   */
  onFinished?: (voiceId: string) => void
}

interface ChunkRecord {
  index: number
  status: 'uploading' | 'done' | 'failed'
  bytes: number
  attempts: number
  mime: string
  uploadedBytes: number
}

const BASE = process.env.NEXT_PUBLIC_API_BASE || ''
const MAX_RETRIES = 3
const RETRY_BACKOFF_MS = [1500, 3000, 6000]

/**
 * 移动端 MDT 录音器 — 临床上场关键组件。
 *
 * 红线设计:
 * 1. 即录即存:每 chunkSeconds 落一片到 IndexedDB,同时尝试上传 MinIO,上传成功才删本地。
 *    刷新/断网/锁屏都不丢。
 * 2. Wake Lock:录音中常亮屏幕,防 iOS 锁屏后 MediaRecorder 暂停。
 * 3. 自动重试 3 次 + 手动重传:网络抖动也能挺过去。
 * 4. mime 上报:前端把 MediaRecorder.mimeType(webm/m4a)随 chunk0 上报,
 *    后端 finalize 阶段用 ffmpeg 据此转码成豆包音频理解能识别的 mp3。
 */
export default function Recorder({
  sessionId,
  voiceType,
  meetingId,
  presetVoiceId,
  label,
  chunkSeconds = 90,
  onFinished,
}: RecorderProps) {
  const isMeeting = Boolean(meetingId)
  // 在 meeting 模式下,virtual sessionId 用于 IndexedDB 路径隔离;真正上传走 meeting 端点
  const storageSessionId = sessionId || (meetingId ? `meeting-${meetingId}` : '')
  const storageVoiceType: VoiceType =
    voiceType || (isMeeting ? 'mdt_discussion' : 'patient_request')
  const toast = useToast()
  const [recording, setRecording] = useState(false)
  const [elapsed, setElapsed] = useState(0)
  const [chunks, setChunks] = useState<ChunkRecord[]>([])
  const [voiceId, setVoiceId] = useState<string | null>(null)
  const [finalizing, setFinalizing] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [recordedMime, setRecordedMime] = useState<string>('')
  const [wakeLockActive, setWakeLockActive] = useState(false)
  const [phase, setPhase] = useState<string>('等待录音')
  // 恢复未传完的旧录音
  const [orphans, setOrphans] = useState<OrphanGroup[]>([])
  const [recovering, setRecovering] = useState(false)

  const mediaRecorderRef = useRef<MediaRecorder | null>(null)
  const streamRef = useRef<MediaStream | null>(null)
  const timerRef = useRef<number | null>(null)
  const chunkIndexRef = useRef(0)
  const voiceIdRef = useRef<string | null>(null)
  const mimeRef = useRef<string>('')
  const wakeLockRef = useRef<any>(null) // WakeLockSentinel,iOS 16.4+/Android Chrome 84+

  useEffect(() => {
    return () => {
      stopStream()
      releaseWakeLock()
      if (timerRef.current) window.clearInterval(timerRef.current)
    }
  }, [])

  // mount 时:看 IndexedDB 里这个 session+voiceType 是否有未上传的片
  useEffect(() => {
    if (!storageSessionId) return
    let cancelled = false
    listOrphansForSession(storageSessionId, storageVoiceType)
      .then((groups) => {
        if (cancelled) return
        if (groups.length > 0) {
          setOrphans(groups)
        }
      })
      .catch(() => {
        // IndexedDB 不可用就算了 — 不阻塞新录音
      })
    return () => {
      cancelled = true
    }
  }, [storageSessionId, storageVoiceType])

  // visibilitychange 时若 WakeLock 被系统释放,尝试重新申请(iOS Safari 会丢)
  useEffect(() => {
    function onVis() {
      if (document.visibilityState === 'visible' && recording && !wakeLockRef.current) {
        requestWakeLock().catch(() => {})
      }
    }
    document.addEventListener('visibilitychange', onVis)
    return () => document.removeEventListener('visibilitychange', onVis)
  }, [recording])

  function stopStream() {
    streamRef.current?.getTracks().forEach((t) => t.stop())
    streamRef.current = null
  }

  async function requestWakeLock() {
    try {
      // @ts-ignore - wakeLock 还在 lib.dom partial coverage
      if ('wakeLock' in navigator && navigator.wakeLock?.request) {
        // @ts-ignore
        const sentinel = await navigator.wakeLock.request('screen')
        wakeLockRef.current = sentinel
        setWakeLockActive(true)
        sentinel.addEventListener?.('release', () => {
          wakeLockRef.current = null
          setWakeLockActive(false)
        })
      }
    } catch (e) {
      // 拒绝 / 不支持 — 不致命,继续录但提示
      console.warn('wake lock denied', e)
    }
  }

  function releaseWakeLock() {
    try {
      wakeLockRef.current?.release?.()
    } catch {}
    wakeLockRef.current = null
    setWakeLockActive(false)
  }

  function pickMime(): string {
    // 顺序:优先选 ffmpeg 兼容性好的 webm/opus,iOS Safari 退到 mp4(m4a/aac)
    const candidates = [
      'audio/webm;codecs=opus',
      'audio/webm',
      'audio/mp4', // iOS Safari 16+
      'audio/mp4;codecs=mp4a.40.2',
      'audio/ogg;codecs=opus',
    ]
    for (const m of candidates) {
      if (typeof MediaRecorder !== 'undefined' && (MediaRecorder as any).isTypeSupported?.(m)) {
        return m
      }
    }
    return ''
  }

  function extFromMime(mime: string): string {
    if (!mime) return 'bin'
    const m = mime.toLowerCase()
    if (m.includes('webm')) return 'webm'
    if (m.includes('mp4') || m.includes('m4a')) return 'm4a'
    if (m.includes('wav')) return 'wav'
    if (m.includes('ogg')) return 'ogg'
    if (m.includes('mpeg')) return 'mp3'
    return 'bin'
  }

  function uploadForm(
    endpoint: string,
    form: FormData,
    onProgress: (loaded: number, total: number) => void,
  ): Promise<void> {
    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest()
      xhr.open('POST', endpoint)
      xhr.setRequestHeader('X-Device-Id', getDeviceId())
      xhr.upload.onprogress = (ev) => {
        if (ev.lengthComputable) onProgress(ev.loaded, ev.total)
      }
      xhr.onload = () => {
        if (xhr.status >= 200 && xhr.status < 300) {
          resolve()
          return
        }
        reject(new Error(`HTTP ${xhr.status}: ${xhr.responseText || '上传失败'}`))
      }
      xhr.onerror = () => reject(new Error('网络连接失败'))
      xhr.onabort = () => reject(new Error('上传已取消'))
      xhr.send(form)
    })
  }

  async function ensureVoice(mime: string): Promise<string> {
    if (voiceIdRef.current) return voiceIdRef.current
    // Meeting 模式:voice_id 在 /api/v1/mdt-meetings POST 时就由后端预生成,直接用 presetVoiceId
    if (isMeeting) {
      if (!presetVoiceId) throw new Error('meeting 模式需要 presetVoiceId(由后端在创建 meeting 时返回)')
      voiceIdRef.current = presetVoiceId
      setVoiceId(presetVoiceId)
      return presetVoiceId
    }
    if (!sessionId) throw new Error('session 模式需要 sessionId')
    const ext = extFromMime(mime)
    const filename = `${storageVoiceType}-${Date.now()}.${ext}`
    const { voice_id } = await api.presignVoice({
      session_id: sessionId,
      filename,
      voice_type: storageVoiceType,
    })
    voiceIdRef.current = voice_id
    setVoiceId(voice_id)
    return voice_id
  }

  /**
   * 上传单片到后端 /audio/chunk。
   * - 成功后从 IndexedDB 删除本片
   * - 失败 → markFailed,触发自动重试(由 retryFailedChunks 调度)
   */
  async function uploadChunk(blob: Blob, index: number, mime: string, attempt = 0): Promise<boolean> {
    const vid = voiceIdRef.current
    if (!vid) return false

    setChunks((prev) => {
      const exists = prev.find((c) => c.index === index)
      if (exists) {
        return prev.map((c) =>
          c.index === index
            ? {
                ...c,
                status: 'uploading',
                attempts: attempt + 1,
                mime,
                bytes: blob.size,
                uploadedBytes: 0,
              }
            : c,
        )
      }
      return [
        ...prev,
        {
          index,
          status: 'uploading',
          bytes: blob.size,
          attempts: attempt + 1,
          mime,
          uploadedBytes: 0,
        },
      ]
    })
    setPhase(`正在上传第 ${index + 1} 片`)

    try {
      const form = new FormData()
      form.append('voice_id', vid)
      form.append('chunk_index', String(index))
      // 上报 mime,后端 chunk0 时落库,finalize 据此决定 ffmpeg 输入容器
      form.append('mime', mime)
      const ext = extFromMime(mime)
      form.append('file', blob, `chunk-${index}.${ext}`)
      let endpoint: string
      if (isMeeting) {
        endpoint = `${BASE}/api/v1/mdt-meetings/${meetingId}/voice/chunk`
      } else {
        form.append('session_id', sessionId!)
        endpoint = `${BASE}/api/v1/audio/chunk`
      }
      await uploadForm(endpoint, form, (loaded, total) => {
        setChunks((prev) =>
          prev.map((c) =>
            c.index === index
              ? { ...c, uploadedBytes: Math.min(loaded, total || blob.size), bytes: total || blob.size }
              : c,
          ),
        )
      })

      // 上传成功 — 删除本地 IndexedDB 缓存(节省空间)
      try {
        await deleteChunk(vid, index)
      } catch {}

      setChunks((prev) =>
        prev.map((c) =>
          c.index === index ? { ...c, status: 'done', uploadedBytes: c.bytes } : c,
        ),
      )
      setPhase(recording ? '录音中,分片自动上传' : '分片上传中,等待全部完成')
      return true
    } catch (e: any) {
      console.warn(`chunk ${index} upload attempt ${attempt + 1} failed`, e)
      // 重试 backoff
      if (attempt + 1 < MAX_RETRIES) {
        const delay = RETRY_BACKOFF_MS[attempt] || 5000
        setChunks((prev) =>
          prev.map((c) =>
            c.index === index
              ? { ...c, status: 'uploading', attempts: attempt + 1, uploadedBytes: 0 }
              : c,
          ),
        )
        await new Promise((r) => setTimeout(r, delay))
        return uploadChunk(blob, index, mime, attempt + 1)
      }
      setChunks((prev) =>
        prev.map((c) => (c.index === index ? { ...c, status: 'failed' } : c)),
      )
      setError(
        `第 ${index + 1} 片上传失败 (${MAX_RETRIES} 次),已保存到本地。请点"重传失败片",或保持网络稳定后再点"完成"。`,
      )
      setPhase('有分片上传失败')
      return false
    }
  }

  /** 录音中收到一片:先入 IndexedDB,再异步触发上传 */
  async function handleDataChunk(blob: Blob, mime: string) {
    if (!blob || blob.size === 0) return
    const idx = chunkIndexRef.current++
    const vid = voiceIdRef.current
    if (!vid) return
    // 双写 IndexedDB:刷新/断网/退出能恢复
    try {
      await saveChunk({
        voiceId: vid,
        sessionId: storageSessionId,
        voiceType: storageVoiceType,
        chunkIndex: idx,
        mime,
        blob,
        size: blob.size,
        createdAt: Date.now(),
      })
    } catch (e) {
      console.warn('IndexedDB save failed', e)
      // 继续上传 — 本地缓存失败不应阻塞主流程
    }
    // 不 await,后台异步上传
    uploadChunk(blob, idx, mime).catch(() => {})
  }

  async function start() {
    setError(null)
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          channelCount: 1,
          sampleRate: 16000,
          echoCancellation: true,
          noiseSuppression: true,
        },
      })
      streamRef.current = stream

      const mime = pickMime()
      const mr = new (window as any).MediaRecorder(
        stream,
        mime ? { mimeType: mime, audioBitsPerSecond: 32000 } : undefined,
      )
      mediaRecorderRef.current = mr
      // 用 mr.mimeType 兜底(Chrome/Safari 实际可能与 candidate 不完全一致)
      const actualMime = (mr.mimeType || mime || '').toString()
      mimeRef.current = actualMime
      setRecordedMime(actualMime)

      await ensureVoice(actualMime)
      chunkIndexRef.current = 0
      setPhase('录音中,分片自动上传')

      mr.ondataavailable = (e: BlobEvent) => {
        if (e.data && e.data.size > 0) {
          handleDataChunk(e.data, actualMime).catch(() => {})
        }
      }
      mr.onerror = (e: any) => {
        setError('录音出错:请检查麦克风权限是否被拒,然后刷新页面重试。')
        console.error(e)
      }

      mr.start(chunkSeconds * 1000)
      setRecording(true)
      setElapsed(0)
      timerRef.current = window.setInterval(() => {
        setElapsed((s) => s + 1)
      }, 1000) as unknown as number

      // 申请屏幕常亮,防锁屏
      await requestWakeLock()
    } catch (e: any) {
      const msg = e?.message || String(e)
      if (msg.includes('Permission') || msg.includes('NotAllowed')) {
        setError(
          '麦克风权限被拒绝。iPhone:设置 → Safari → 麦克风 → 允许;安卓:浏览器设置 → 应用权限 → 麦克风。',
        )
      } else if (msg.includes('NotFound')) {
        setError('未检测到麦克风设备。')
      } else {
        setError('无法开始录音:' + msg)
      }
    }
  }

  async function stop() {
    const mr = mediaRecorderRef.current
    if (!mr) return
    if (timerRef.current) {
      window.clearInterval(timerRef.current)
      timerRef.current = null
    }
    // 等待最后一片
    const last = new Promise<void>((resolve) => {
      const orig = mr.ondataavailable
      mr.ondataavailable = async (e: BlobEvent) => {
        if (orig) {
          try {
            await (orig as any).call(mr, e)
          } catch {}
        }
        resolve()
      }
    })
    try {
      mr.stop()
    } catch {}
    setRecording(false)
    await last
    stopStream()
    releaseWakeLock()
  }

  /** 重传所有 failed 状态的片 — 从 IndexedDB 拿回 blob */
  async function retryFailed() {
    const vid = voiceIdRef.current
    if (!vid) return
    setError(null)
    const failed = chunks.filter((c) => c.status === 'failed')
    if (failed.length === 0) return
    const pending = await listChunks(vid)
    const byIdx = new Map(pending.map((p) => [p.chunkIndex, p]))
    for (const c of failed) {
      const local = byIdx.get(c.index)
      if (!local) {
        setError(`第 ${c.index + 1} 片本地缓存丢失,无法重传。`)
        continue
      }
      await uploadChunk(local.blob, c.index, local.mime, 0)
    }
  }

  /**
   * 恢复未传完的旧录音 — 把 IndexedDB 里的片上传到原 voiceId,
   * 然后让医生点"完成并转写"走 finalize。
   */
  async function recoverOrphan(group: OrphanGroup) {
    if (recovering || recording) return
    setRecovering(true)
    setError(null)
    try {
      const pending = await listChunks(group.voiceId)
      if (pending.length === 0) {
        setOrphans((prev) => prev.filter((o) => o.voiceId !== group.voiceId))
        return
      }
      // 接管 voiceId + mime,索引指针置为下一个空位
      voiceIdRef.current = group.voiceId
      setVoiceId(group.voiceId)
      mimeRef.current = group.mime || ''
      setRecordedMime(group.mime || '')
      const maxIdx = pending.reduce((m, p) => Math.max(m, p.chunkIndex), -1)
      chunkIndexRef.current = maxIdx + 1
      // 初始化 chunks state
      setChunks(
        pending
          .slice()
          .sort((a, b) => a.chunkIndex - b.chunkIndex)
          .map((p) => ({
            index: p.chunkIndex,
            status: 'failed',
            bytes: p.size,
            attempts: 0,
            mime: p.mime,
            uploadedBytes: 0,
          })),
      )
      // 把 orphans 列表删掉这一组
      setOrphans((prev) => prev.filter((o) => o.voiceId !== group.voiceId))
      toast.info(`正在恢复 ${pending.length} 片,共 ${(group.totalBytes / 1024 / 1024).toFixed(1)} MB`)
      // 顺序上传所有片
      for (const p of pending.sort((a, b) => a.chunkIndex - b.chunkIndex)) {
        await uploadChunk(p.blob, p.chunkIndex, p.mime, 0)
      }
      toast.success('已恢复全部片段,可点"完成并转写"提交')
    } catch (e: any) {
      setError('恢复失败:' + (e?.message || String(e)))
    } finally {
      setRecovering(false)
    }
  }

  async function discardOrphan(group: OrphanGroup) {
    if (!confirm(
      `确认丢弃这段未上传的录音?(${group.count} 片 / ${(group.totalBytes / 1024 / 1024).toFixed(1)} MB)\n丢弃后不可恢复。`,
    )) {
      return
    }
    try {
      await clearVoice(group.voiceId)
    } catch {}
    setOrphans((prev) => prev.filter((o) => o.voiceId !== group.voiceId))
  }

  async function finalize() {
    if (!voiceIdRef.current) return
    setFinalizing(true)
    setError(null)
    setPhase('正在合并录音并转码')
    try {
      const chunkCount = chunkIndexRef.current
      let endpoint: string
      let body: any
      if (isMeeting) {
        endpoint = `${BASE}/api/v1/mdt-meetings/${meetingId}/voice/upload-finalize`
        body = {
          voice_id: voiceIdRef.current,
          chunk_count: chunkCount,
          source_mime: mimeRef.current || recordedMime,
        }
      } else {
        endpoint = `${BASE}/api/v1/audio/finalize`
        body = {
          session_id: sessionId,
          voice_id: voiceIdRef.current,
          chunk_count: chunkCount,
          source_mime: mimeRef.current || recordedMime,
        }
      }
      const res = await fetch(endpoint, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-Device-Id': getDeviceId(),
        },
        body: JSON.stringify(body),
      })
      if (!res.ok) {
        const errText = await res.text().catch(() => '')
        throw new Error(`HTTP ${res.status}: ${errText || '后端拼接转码失败'}`)
      }
      // finalize 成功:清空本地 IndexedDB
      try {
        await clearVoice(voiceIdRef.current)
      } catch {}
      if (!isMeeting) {
        // 单 session 模式:立即触发 ASR
        setPhase('录音已合并,正在提交转写')
        await api.triggerAsr(voiceIdRef.current)
      }
      // meeting 模式:父组件决定何时调 /finalize 触发 ASR+切分(可能需要二次确认)
      setPhase(isMeeting ? '录音已合并,可开始 AI 切分' : '已提交转写,请查看实时进度')
      onFinished?.(voiceIdRef.current)
    } catch (e: any) {
      const msg = e?.message || String(e)
      if (msg.includes('音频转码失败') || msg.includes('chunk') && msg.includes('缺失')) {
        setError(`合并失败:${msg}`)
      } else {
        setError('合并失败,请稍后重试。详情:' + msg.slice(0, 200))
      }
    } finally {
      setFinalizing(false)
    }
  }

  const mm = String(Math.floor(elapsed / 60)).padStart(2, '0')
  const ss = String(elapsed % 60).padStart(2, '0')
  const okCount = chunks.filter((c) => c.status === 'done').length
  const uploadingCount = chunks.filter((c) => c.status === 'uploading').length
  const failedCount = chunks.filter((c) => c.status === 'failed').length
  const totalBytes = chunks.reduce((sum, c) => sum + c.bytes, 0)
  const uploadedBytes = chunks.reduce((sum, c) => {
    if (c.status === 'done') return sum + c.bytes
    return sum + Math.min(c.uploadedBytes || 0, c.bytes)
  }, 0)
  const uploadPercent = totalBytes > 0 ? Math.round((uploadedBytes / totalBytes) * 100) : 0
  const uploadedMb = (uploadedBytes / 1024 / 1024).toFixed(1)
  const totalMb = (totalBytes / 1024 / 1024).toFixed(1)
  const displayPhase = finalizing
    ? phase
    : failedCount > 0
    ? '有分片上传失败'
    : uploadingCount > 0
    ? phase
    : chunks.length > 0 && okCount === chunks.length
    ? isMeeting
      ? '分片已上传,可完成录音'
      : '分片已上传,可完成并转写'
    : phase

  return (
    <div className="card space-y-3">
      <div className="flex items-center justify-between">
        <div className="min-w-0 flex-1">
          <div className="font-medium text-base">{label}</div>
          <div className="text-xs text-gray-500 mt-0.5">
            每 {chunkSeconds}s 自动落本地+上传,断网/锁屏可恢复
            {wakeLockActive && <span className="ml-2 text-emerald-600">· 屏幕常亮中</span>}
          </div>
        </div>
        <div className="text-2xl sm:text-3xl font-mono tabular-nums shrink-0">
          {recording && (
            <span className="inline-block w-2.5 h-2.5 rounded-full bg-rose-500 animate-pulse mr-2 align-middle" />
          )}
          {mm}:{ss}
        </div>
      </div>

      {/* 未上传的旧录音恢复 — Recorder mount 时若 IndexedDB 有缓存,显示在这里 */}
      {orphans.length > 0 && !recording && chunks.length === 0 && (
        <div className="bg-amber-50 border border-amber-300 rounded-md p-3 space-y-2">
          <div className="text-sm font-medium text-amber-800">
            ⚠️ 检测到上次录音未传完,可恢复
          </div>
          {orphans.map((g) => {
            const mb = (g.totalBytes / 1024 / 1024).toFixed(1)
            const minsAgo = Math.max(1, Math.round((Date.now() - g.latestAt) / 60000))
            return (
              <div
                key={g.voiceId}
                className="bg-white/70 rounded p-2 text-xs space-y-1"
              >
                <div className="text-gray-700">
                  共 <b>{g.count}</b> 片 · {mb} MB · 约 {minsAgo} 分钟前
                </div>
                <div className="flex gap-2">
                  <button
                    className="btn bg-amber-500 text-white hover:bg-amber-600 flex-1 py-1.5 min-h-10 text-sm"
                    onClick={() => recoverOrphan(g)}
                    disabled={recovering}
                    type="button"
                  >
                    {recovering ? '恢复中…' : '继续上传'}
                  </button>
                  <button
                    className="btn btn-ghost flex-1 py-1.5 min-h-10 text-sm"
                    onClick={() => discardOrphan(g)}
                    disabled={recovering}
                    type="button"
                  >
                    丢弃
                  </button>
                </div>
              </div>
            )
          })}
          <div className="text-[11px] text-amber-700 italic">
            建议先把旧片上传完再开始新录音,避免混在一起。
          </div>
        </div>
      )}

      {!recording && chunks.length === 0 && (
        <button
          className="btn btn-primary w-full min-h-12 text-base"
          onClick={start}
          type="button"
        >
          🎙️ 开始录音
        </button>
      )}

      {recording && (
        <button
          className="btn bg-rose-500 text-white hover:bg-rose-600 w-full min-h-12 text-base"
          onClick={stop}
          type="button"
        >
          ⏹ 结束录音
        </button>
      )}

      {!recording && chunks.length > 0 && (
        <div className="grid grid-cols-2 gap-2">
          <button
            className="btn btn-ghost min-h-12 text-base"
            onClick={start}
            type="button"
          >
            继续录音
          </button>
          <button
            className="btn btn-primary min-h-12 text-base"
            onClick={finalize}
            disabled={finalizing || uploadingCount > 0 || failedCount > 0}
            type="button"
          >
            {finalizing ? '处理中…' : isMeeting ? '完成录音' : '完成并转写'}
          </button>
        </div>
      )}

      {chunks.length > 0 && (
        <div className="text-xs text-gray-600 space-y-2">
          <div className="flex items-center justify-between gap-2">
            <span className="truncate">{displayPhase}</span>
            <span className="shrink-0 tabular-nums">{uploadPercent}%</span>
          </div>
          <div className="h-2 bg-gray-100 rounded-full overflow-hidden">
            <div
              className={`h-full transition-all ${failedCount > 0 ? 'bg-rose-500' : 'bg-brand-500'}`}
              style={{ width: `${Math.min(100, Math.max(0, uploadPercent))}%` }}
            />
          </div>
          <div>
            分片进度:{okCount}/{chunks.length} 已上传 · {uploadedMb}/{totalMb} MB
            {uploadingCount > 0 && (
              <span className="text-amber-600 ml-2">{uploadingCount} 上传中</span>
            )}
            {failedCount > 0 && (
              <span className="text-rose-600 ml-2">{failedCount} 片失败</span>
            )}
          </div>
          {recordedMime && (
            <div className="text-gray-400">编码:{recordedMime}(后端将转 mp3)</div>
          )}
        </div>
      )}

      {failedCount > 0 && !recording && (
        <button
          className="btn btn-secondary w-full min-h-11"
          onClick={retryFailed}
          type="button"
        >
          🔄 重传失败片 ({failedCount})
        </button>
      )}

      {error && (
        <div className="text-sm text-rose-700 bg-rose-50 border border-rose-200 rounded p-2">
          ⚠️ {error}
        </div>
      )}
    </div>
  )
}
