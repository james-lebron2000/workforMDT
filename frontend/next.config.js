/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  experimental: { typedRoutes: false },
  async rewrites() {
    const backend = process.env.NEXT_PUBLIC_API_BASE || 'http://localhost:8000'
    return [
      { source: '/api/:path*', destination: `${backend}/api/:path*` },
    ]
  },
}

module.exports = nextConfig
