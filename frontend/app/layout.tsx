import './globals.css'
import type { Metadata, Viewport } from 'next'
import { ToastProvider } from '@/components/Toast'
import AppHeader from '@/components/AppHeader'

export const metadata: Metadata = {
  title: 'TumorBoard AI - 肿瘤多学科智能会诊',
  description: '医生个人辅助工具,生成 MDT 多学科病例整理报告',
  manifest: '/manifest.json',
  appleWebApp: {
    capable: true,
    title: 'TumorBoard',
    statusBarStyle: 'default',
  },
  icons: {
    icon: [
      { url: '/icons/icon-192.png', sizes: '192x192', type: 'image/png' },
      { url: '/icons/icon-512.png', sizes: '512x512', type: 'image/png' },
    ],
    apple: [{ url: '/icons/apple-touch-icon.png', sizes: '180x180' }],
  },
  formatDetection: {
    telephone: false, // 防 iOS 把数字自动识别成拨号(误触)
  },
}

export const viewport: Viewport = {
  themeColor: '#2557d6',
  width: 'device-width',
  initialScale: 1,
  // 允许放大到 5 倍 — 老花眼医生友好;不锁 maximumScale=1(iOS 无障碍敏感)
  maximumScale: 5,
  userScalable: true,
  // 安全区适配 iPhone 刘海/底部 home indicator
  viewportFit: 'cover',
}

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="zh-CN">
      <head>
        {/* iOS Safari 加到主屏后的状态栏样式 + PWA */}
        <meta name="apple-mobile-web-app-capable" content="yes" />
        <meta name="apple-mobile-web-app-status-bar-style" content="default" />
        <meta name="mobile-web-app-capable" content="yes" />
        {/* 微信 X5 内核兜底 — 强制使用极速模式渲染,关掉视频自动嗅探 */}
        <meta name="x5-orientation" content="portrait" />
        <meta name="format-detection" content="telephone=no, email=no, address=no" />
      </head>
      <body className="min-h-screen pb-safe">
        <ToastProvider>
          <AppHeader />
          <main className="max-w-3xl mx-auto px-4 py-4 pb-24">{children}</main>
          <footer className="fixed bottom-0 inset-x-0 bg-white/95 backdrop-blur border-t border-gray-100 text-center py-2 text-[11px] text-gray-400 pb-safe">
            v0.2 · MVP · 患者数据仅本机存储 · policy v1.1
          </footer>
        </ToastProvider>
      </body>
    </html>
  )
}
