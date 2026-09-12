import type { NextConfig } from "next";

const BACKEND = process.env.BACKEND_ORIGIN ?? "http://127.0.0.1:8000";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  // The browser talks to the frontend origin only; these rewrites proxy
  // the API to FastAPI (backend/src/main.py). Keeps the app CORS-free on
  // localhost and behind any reverse proxy (e.g. tailscale serve).
  async rewrites() {
    return [
      { source: "/api/v1/:path*", destination: `${BACKEND}/api/v1/:path*` },
      { source: "/health", destination: `${BACKEND}/health` },
    ];
  },
};

export default nextConfig;