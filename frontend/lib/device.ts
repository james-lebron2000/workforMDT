// 设备ID(匿名识别) - MVP 阶段替代登录
const KEY = 'tb_device_id'

export function getDeviceId(): string {
  if (typeof window === 'undefined') return ''
  let id = window.localStorage.getItem(KEY)
  if (!id) {
    id = 'dev-' + crypto.randomUUID()
    window.localStorage.setItem(KEY, id)
  }
  return id
}
