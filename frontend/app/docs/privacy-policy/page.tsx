/**
 * 隐私政策详情页 — server component
 *
 * - 在 build/run time 读 public/docs/privacy-policy.md(随 Next image 自动 ship)
 * - 用 react-markdown + remark-gfm 渲染(支持表格/任务列表)
 * - prose 样式来自 @tailwindcss/typography
 *
 * 维护:docs/privacy-policy.md 是 source of truth;
 *      改完后跑 `cp docs/privacy-policy.md frontend/public/docs/privacy-policy.md` 同步。
 */
import fs from 'node:fs/promises'
import path from 'node:path'
import Link from 'next/link'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

export const metadata = {
  title: '隐私政策与使用同意书 - TumorBoard AI',
  description: 'TumorBoard AI 的隐私政策、数据流向、医生义务、数据保留与删除规则。',
}

// 强制每次访问读最新文件(无需 revalidate;build 后文件随容器不变)
export const dynamic = 'force-static'

async function loadPolicy(): Promise<string> {
  const filePath = path.join(
    process.cwd(),
    'public',
    'docs',
    'privacy-policy.md',
  )
  try {
    return await fs.readFile(filePath, 'utf-8')
  } catch (e) {
    return `# 隐私政策加载失败\n\n请联系管理员。\n\n\`${String(e)}\``
  }
}

export default async function PrivacyPolicyPage() {
  const content = await loadPolicy()
  return (
    <div className="max-w-3xl mx-auto py-6 px-4">
      <Link
        href="/cases"
        className="text-sm text-gray-500 hover:text-gray-700"
      >
        ← 返回病例列表
      </Link>
      <article className="prose prose-slate max-w-none mt-4 prose-headings:scroll-mt-16 prose-h1:text-2xl prose-h2:text-xl prose-h3:text-base prose-table:text-xs">
        <ReactMarkdown remarkPlugins={[remarkGfm]}>{content}</ReactMarkdown>
      </article>
      <div className="mt-8 pt-4 border-t text-xs text-gray-500">
        <p>
          原 markdown 文件:
          <a
            href="/docs/privacy-policy.md"
            className="text-blue-600 underline ml-1"
            target="_blank"
            rel="noopener noreferrer"
          >
            /docs/privacy-policy.md
          </a>
        </p>
      </div>
    </div>
  )
}
