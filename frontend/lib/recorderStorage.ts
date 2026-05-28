/**
 * 录音分片本地兜底 - IndexedDB 双写。
 *
 * 设计理由(临床场景的真实风险):
 * - 30 分钟 MDT 录音,上传分片到 MinIO 时碰到弱网/微信浏览器后台被回收/iOS 锁屏
 *   → 单纯靠内存 buffer + 失败重试不够,刷页面就丢光。
 * - 把每片 (voiceId, chunkIndex) → Blob 落到 IndexedDB,即录即存。
 * - 上传成功后立即从 IndexedDB 删,保持库小;上传失败保留,重试时取。
 * - finalize 成功后整批清空(voiceId 维度)。
 * - 页面刷新/崩溃后,Recorder 用 listOrphansForSession 查到未传完的片,
 *   提示医生"上次录音有 N 片未上传,是否继续?"
 *
 * DB_VERSION=2 增加 sessionId/voiceType 索引,旧库会被清空(临床场景容忍一次)。
 */

const DB_NAME = 'mdt-recorder'
const DB_VERSION = 2
const STORE = 'chunks'

export interface PendingChunk {
  voiceId: string
  sessionId: string
  voiceType: string // 'patient_request' | 'mdt_discussion'
  chunkIndex: number
  mime: string
  blob: Blob
  size: number
  createdAt: number
}

export interface OrphanGroup {
  voiceId: string
  sessionId: string
  voiceType: string
  count: number
  totalBytes: number
  latestAt: number
  mime: string
}

let _dbPromise: Promise<IDBDatabase> | null = null

function open(): Promise<IDBDatabase> {
  if (typeof indexedDB === 'undefined') {
    return Promise.reject(new Error('当前浏览器不支持 IndexedDB,无法本地缓存录音'))
  }
  if (_dbPromise) return _dbPromise
  _dbPromise = new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME, DB_VERSION)
    req.onupgradeneeded = () => {
      const db = req.result
      // 升级:旧版只有 voiceId 索引,新增 sessionId 索引;一次性清空旧库
      if (db.objectStoreNames.contains(STORE)) {
        db.deleteObjectStore(STORE)
      }
      const store = db.createObjectStore(STORE, { keyPath: 'key' })
      store.createIndex('voiceId', 'voiceId', { unique: false })
      store.createIndex('sessionId', 'sessionId', { unique: false })
    }
    req.onsuccess = () => resolve(req.result)
    req.onerror = () => reject(req.error)
  })
  return _dbPromise
}

function key(voiceId: string, chunkIndex: number): string {
  return `${voiceId}::${chunkIndex.toString().padStart(5, '0')}`
}

export async function saveChunk(c: PendingChunk): Promise<void> {
  const db = await open()
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE, 'readwrite')
    tx.objectStore(STORE).put({ key: key(c.voiceId, c.chunkIndex), ...c })
    tx.oncomplete = () => resolve()
    tx.onerror = () => reject(tx.error)
  })
}

export async function deleteChunk(voiceId: string, chunkIndex: number): Promise<void> {
  const db = await open()
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE, 'readwrite')
    tx.objectStore(STORE).delete(key(voiceId, chunkIndex))
    tx.oncomplete = () => resolve()
    tx.onerror = () => reject(tx.error)
  })
}

export async function listChunks(voiceId: string): Promise<PendingChunk[]> {
  const db = await open()
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE, 'readonly')
    const store = tx.objectStore(STORE)
    const idx = store.index('voiceId')
    const req = idx.getAll(IDBKeyRange.only(voiceId))
    req.onsuccess = () =>
      resolve(
        (req.result as any[]).map((r) => ({
          voiceId: r.voiceId,
          sessionId: r.sessionId,
          voiceType: r.voiceType,
          chunkIndex: r.chunkIndex,
          mime: r.mime,
          blob: r.blob,
          size: r.size,
          createdAt: r.createdAt,
        })),
      )
    req.onerror = () => reject(req.error)
  })
}

export async function clearVoice(voiceId: string): Promise<void> {
  const chunks = await listChunks(voiceId)
  await Promise.all(chunks.map((c) => deleteChunk(c.voiceId, c.chunkIndex)))
}

/**
 * 查找本 session 下所有未上传的 voiceId 分组。
 * 用于 Recorder mount 时检测"上次有未传完的录音"。
 */
export async function listOrphansForSession(
  sessionId: string,
  voiceType?: string,
): Promise<OrphanGroup[]> {
  const db = await open()
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE, 'readonly')
    const store = tx.objectStore(STORE)
    const idx = store.index('sessionId')
    const req = idx.getAll(IDBKeyRange.only(sessionId))
    req.onsuccess = () => {
      const rows = req.result as any[]
      const groups = new Map<string, OrphanGroup>()
      for (const r of rows) {
        if (voiceType && r.voiceType !== voiceType) continue
        const g = groups.get(r.voiceId) || {
          voiceId: r.voiceId,
          sessionId: r.sessionId,
          voiceType: r.voiceType,
          count: 0,
          totalBytes: 0,
          latestAt: 0,
          mime: r.mime || '',
        }
        g.count += 1
        g.totalBytes += r.size || 0
        g.latestAt = Math.max(g.latestAt, r.createdAt || 0)
        g.mime = g.mime || r.mime || ''
        groups.set(r.voiceId, g)
      }
      resolve([...groups.values()].sort((a, b) => b.latestAt - a.latestAt))
    }
    req.onerror = () => reject(req.error)
  })
}
