import { getDeviceId } from './device'

const BASE = process.env.NEXT_PUBLIC_API_BASE || ''

function headers(extra: Record<string, string> = {}) {
  return {
    'Content-Type': 'application/json',
    'X-Device-Id': getDeviceId(),
    ...extra,
  }
}

/**
 * API 错误 — 包装 HTTP 状态 + 给用户看的中文提示。
 * 上层用 `humanizeError(e)` 拿到可直接展示的字符串。
 */
export class ApiError extends Error {
  status: number
  detail: string
  hint: string
  constructor(status: number, detail: string, hint: string) {
    super(`${status} ${detail}`)
    this.name = 'ApiError'
    this.status = status
    this.detail = detail
    this.hint = hint
  }
}

function humanizeStatus(status: number, detail: string): string {
  if (status === 0) return '无法连接服务器,请检查网络或刷新页面重试'
  if (status === 401) return '登录已过期,请刷新页面重新登录'
  if (status === 403) {
    if (detail.includes('consent') || detail.includes('同意书'))
      return '请先勾选并签署知情同意书,然后再继续'
    return '没有权限执行此操作'
  }
  if (status === 404) return '资源不存在,可能已被删除或链接错误'
  if (status === 409) return '当前状态不允许此操作,请刷新查看最新进度'
  if (status === 413) return '上传内容过大,请压缩后重试或分次上传'
  if (status === 429) return '请求太频繁,请稍候片刻再试'
  if (status === 422) {
    if (detail.includes('chunk') && detail.includes('缺失')) return '部分录音片段丢失,请重新录音'
    return '提交内容不符合要求:' + detail
  }
  if (status === 500) {
    if (detail.includes('音频转码失败') || detail.includes('ffmpeg'))
      return '音频转码失败,请检查录音是否正常并点"重试转写"。若反复失败请联系管理员。'
    if (detail.includes('对象存储') || detail.includes('MinIO') || detail.includes('清理失败'))
      return '存储服务暂时不可用,请稍后重试。'
    if (detail.includes('LLM') || detail.includes('豆包') || detail.includes('音频理解'))
      return 'AI 服务暂时不可用,请稍候重试。'
    return '服务器内部错误,请稍后重试。'
  }
  if (status === 503) return '服务暂时不可用(可能正在维护),请稍后重试。'
  return detail || `请求失败 (HTTP ${status})`
}

async function unwrap<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let detail = ''
    try {
      const j = await res.json()
      detail = j?.detail || j?.message || JSON.stringify(j)
    } catch {
      detail = await res.text().catch(() => '')
    }
    const hint = humanizeStatus(res.status, detail)
    throw new ApiError(res.status, detail || `HTTP ${res.status}`, hint)
  }
  return res.json() as Promise<T>
}

/** 把任意 error 转成给用户看的中文提示。UI 层 catch 后用此函数。 */
export function humanizeError(e: unknown): string {
  if (e instanceof ApiError) return e.hint
  if (e instanceof TypeError && /fetch|network/i.test(e.message)) {
    return '网络连接失败,请检查网络后重试'
  }
  const msg = (e as any)?.message || String(e)
  return msg
}

export interface MeetingMember {
  session_id: string
  patient_code: string
  primary_diagnosis: string | null
  primary_site: string | null
  session_status: string
  has_summary_confirmed: boolean
  split_segment_count: number | null
  split_confidence: number | null
  split_is_missing: boolean | null
}

export interface MeetingDetail {
  id: string
  title: string | null
  mdt_date: string | null
  status: string  // draft | recording | transcribing | splitting | analyzing | completed | failed
  group_voice_id: string | null
  audio_finalized: boolean  // mp3 已拼接转码完成,可触发 ASR+切分
  members: MeetingMember[]
  error: string | null
}

export const api = {
  base: BASE,
  async getConsent() {
    const res = await fetch(`${BASE}/api/v1/consent`, { headers: headers() })
    return unwrap<{ policy_version: string; accepted: boolean; accepted_at: string | null }>(res)
  },
  async acceptConsent(policy_version: string) {
    const res = await fetch(`${BASE}/api/v1/consent`, {
      method: 'POST',
      headers: headers(),
      body: JSON.stringify({ policy_version }),
    })
    return unwrap<{ policy_version: string; accepted: boolean; accepted_at: string | null }>(res)
  },
  async login(name?: string, hospital?: string, dept?: string) {
    const res = await fetch(`${BASE}/api/v1/auth/login`, {
      method: 'POST',
      headers: headers(),
      body: JSON.stringify({ device_id: getDeviceId(), name, hospital, dept }),
    })
    return unwrap<{ user_id: string; device_id: string }>(res)
  },
  async listSessions() {
    const res = await fetch(`${BASE}/api/v1/sessions`, { headers: headers() })
    return unwrap<{
      sessions: {
        id: string
        patient_id: string
        patient_code: string
        title: string | null
        mdt_date: string | null
        status: string
      }[]
    }>(res)
  },
  async createSession(payload: {
    patient: {
      code: string
      sex?: string
      age_range?: string
      primary_diagnosis?: string
      primary_site?: string
    }
    title?: string
    mdt_date?: string
  }) {
    const res = await fetch(`${BASE}/api/v1/sessions`, {
      method: 'POST',
      headers: headers(),
      body: JSON.stringify(payload),
    })
    return unwrap<{ id: string; patient_id: string; patient_code: string; status: string }>(res)
  },
  async getSession(id: string) {
    const res = await fetch(`${BASE}/api/v1/sessions/${id}`, { headers: headers() })
    return unwrap<any>(res)
  },
  async deleteSession(id: string) {
    const res = await fetch(`${BASE}/api/v1/sessions/${id}`, {
      method: 'DELETE',
      headers: headers(),
    })
    return unwrap<{ ok: boolean; minio_files_removed: number }>(res)
  },
  async presignFile(payload: {
    session_id: string
    filename: string
    file_type: string
    mime_type?: string
  }) {
    const res = await fetch(`${BASE}/api/v1/files/presign`, {
      method: 'POST',
      headers: headers(),
      body: JSON.stringify(payload),
    })
    return unwrap<{ record_id: string; upload_url: string; file_key: string }>(res)
  },
  async presignVoice(payload: {
    session_id: string
    filename: string
    voice_type: 'patient_request' | 'mdt_discussion'
  }) {
    const res = await fetch(`${BASE}/api/v1/audio/presign`, {
      method: 'POST',
      headers: headers(),
      body: JSON.stringify(payload),
    })
    return unwrap<{ voice_id: string; upload_url: string; file_key: string }>(res)
  },
  async triggerOcr(recordId: string) {
    const res = await fetch(`${BASE}/api/v1/jobs/ocr/${recordId}`, {
      method: 'POST',
      headers: headers(),
    })
    return unwrap<{ ok: boolean; task_id: string }>(res)
  },
  async triggerAsr(voiceId: string) {
    const res = await fetch(`${BASE}/api/v1/jobs/asr/${voiceId}`, {
      method: 'POST',
      headers: headers(),
    })
    return unwrap<{ ok: boolean; task_id: string }>(res)
  },
  async triggerSummary(sessionId: string) {
    const res = await fetch(`${BASE}/api/v1/jobs/summary/${sessionId}`, {
      method: 'POST',
      headers: headers(),
    })
    return unwrap<{ ok: boolean; task_id: string }>(res)
  },
  async confirmSummary(sessionId: string, note?: string) {
    const res = await fetch(`${BASE}/api/v1/sessions/${sessionId}/confirm-summary`, {
      method: 'POST',
      headers: headers(),
      body: JSON.stringify({ note }),
    })
    return unwrap<{ ok: boolean; status: string }>(res)
  },
  async triggerAnalyze(sessionId: string) {
    const res = await fetch(`${BASE}/api/v1/jobs/analyze/${sessionId}`, {
      method: 'POST',
      headers: headers(),
    })
    return unwrap<{ ok: boolean; task_id: string }>(res)
  },
  async editField(sessionId: string, field_path: string, new_value: any) {
    const res = await fetch(`${BASE}/api/v1/report/${sessionId}/field`, {
      method: 'PATCH',
      headers: headers(),
      body: JSON.stringify({ field_path, new_value }),
    })
    return unwrap<{ ok: boolean }>(res)
  },
  async regenerate(sessionId: string, section: string) {
    const res = await fetch(`${BASE}/api/v1/report/${sessionId}/regen`, {
      method: 'POST',
      headers: headers(),
      body: JSON.stringify({ section }),
    })
    return unwrap<{ ok: boolean; task_id: string }>(res)
  },
  async exportUrl(sessionId: string, format: 'docx' | 'pdf' | 'pptx' | 'wechat_card') {
    return `${BASE}/api/v1/report/${sessionId}/export`
  },
  async exportReport(
    sessionId: string,
    format: 'docx' | 'pdf' | 'pptx' | 'wechat_card',
  ) {
    const res = await fetch(`${BASE}/api/v1/report/${sessionId}/export`, {
      method: 'POST',
      headers: headers(),
      body: JSON.stringify({ format }),
    })
    if (!res.ok) throw new Error(await res.text())
    if (format === 'wechat_card') return (await res.json()) as { ok: boolean; card: string }
    return res.blob()
  },
  // ---------- 群组 MDT 会议(多病人单录音 → AI 切分) ----------
  async createMeeting(payload: {
    session_ids: string[]
    title?: string
    mdt_date?: string
  }) {
    const res = await fetch(`${BASE}/api/v1/mdt-meetings`, {
      method: 'POST',
      headers: headers(),
      body: JSON.stringify(payload),
    })
    return unwrap<MeetingDetail>(res)
  },
  async listMeetings() {
    const res = await fetch(`${BASE}/api/v1/mdt-meetings`, { headers: headers() })
    return unwrap<{ meetings: MeetingDetail[] }>(res)
  },
  async getMeeting(id: string) {
    const res = await fetch(`${BASE}/api/v1/mdt-meetings/${id}`, { headers: headers() })
    return unwrap<MeetingDetail>(res)
  },
  async deleteMeeting(id: string) {
    const res = await fetch(`${BASE}/api/v1/mdt-meetings/${id}`, {
      method: 'DELETE',
      headers: headers(),
    })
    return unwrap<{ ok: boolean; minio_files_removed: number }>(res)
  },
  async finalizeMeetingAnalyze(id: string) {
    const res = await fetch(`${BASE}/api/v1/mdt-meetings/${id}/finalize`, {
      method: 'POST',
      headers: headers(),
    })
    return unwrap<{ ok: boolean; task_id: string; meeting_id: string }>(res)
  },
  // MDT 会前摘要 PDF — 仅在 status >= summary_confirmed 时可调
  async exportBrief(sessionId: string) {
    const res = await fetch(`${BASE}/api/v1/report/${sessionId}/export-brief`, {
      method: 'POST',
      headers: headers(),
    })
    if (!res.ok) {
      let detail = ''
      try {
        const j = await res.json()
        detail = j?.detail || JSON.stringify(j)
      } catch {
        detail = await res.text()
      }
      throw new Error(`${res.status} ${detail}`)
    }
    return res.blob()
  },
}
