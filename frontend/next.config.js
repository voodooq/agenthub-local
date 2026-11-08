/** @type {import('next').NextConfig} */
const isDev = process.env.NODE_ENV === 'development';

const nextConfig = {
  reactStrictMode: true,
  images: {
    unoptimized: true,
  },
  // 生产构建必须始终执行静态导出到 out 目录，供桌面外壳 (WebView2) 与 FastAPI 直接托管
  ...(!isDev
    ? {
        output: 'export',
        trailingSlash: true,
      }
    : {
        async rewrites() {
          const backendUrl = process.env.BACKEND_URL || 'http://localhost:8686';
          return [
            {
              source: '/api/:path*',
              destination: `${backendUrl}/api/:path*`,
            },
          ];
        },
      }),
};

module.exports = nextConfig;
