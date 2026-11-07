/** @type {import('next').NextConfig} */
const isExport = process.env.NEXT_EXPORT === 'true' || process.env.BUILD_DESKTOP === 'true';

const nextConfig = {
  reactStrictMode: true,
  images: {
    unoptimized: true,
  },
  ...(isExport
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
