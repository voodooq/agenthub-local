const fs = require('fs');
const path = require('path');

/**
 * 统一加载全仓应用版本配置：
 * 1. 优先读取仓库根目录 ../version.json（本地源码 / Next 静态导出）
 * 2. 若在 Docker ./frontend 独立上下文构建中无法访问父目录，则平滑回退读取同级 ./package.json
 */
function loadAppVersionConfig() {
  const rootVersionFile = path.resolve(__dirname, '..', 'version.json');
  if (fs.existsSync(rootVersionFile)) {
    try {
      const parsed = JSON.parse(fs.readFileSync(rootVersionFile, 'utf-8'));
      const ver = String(parsed.version || '').replace(/^v/i, '').trim();
      const edition = String(parsed.edition || '').trim() || 'Strata Edition';
      if (ver) {
        return { version: ver, edition };
      }
    } catch (err) {
      console.warn('[next.config.js] 读取根目录 version.json 失败，将回退到 package.json:', err.message);
    }
  }

  const pkgFile = path.resolve(__dirname, 'package.json');
  try {
    const pkg = JSON.parse(fs.readFileSync(pkgFile, 'utf-8'));
    return {
      version: String(pkg.version || '0.0.0').replace(/^v/i, '').trim(),
      edition: 'Strata Edition',
    };
  } catch {
    return { version: '0.0.0', edition: 'Strata Edition' };
  }
}

const appVersionConfig = loadAppVersionConfig();

/** @type {import('next').NextConfig} */
const isDev = process.env.NODE_ENV === 'development';

const nextConfig = {
  reactStrictMode: true,
  env: {
    NEXT_PUBLIC_APP_VERSION: appVersionConfig.version,
    NEXT_PUBLIC_APP_EDITION: appVersionConfig.edition,
  },
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
