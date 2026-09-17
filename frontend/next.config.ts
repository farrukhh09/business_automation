import type { NextConfig } from "next";

/**
 * Browser → Next.js `/api/*` → backend `/api/*` (07-frontend.md §1): no CORS in the browser.
 * Rewrites are resolved at build time for `output: "standalone"`, so the Docker build
 * receives BACKEND_INTERNAL_URL as a build argument.
 */
const backendUrl = (process.env.BACKEND_INTERNAL_URL ?? "http://localhost:8000").replace(/\/+$/, "");

const nextConfig: NextConfig = {
  output: "standalone",
  poweredByHeader: false,
  reactStrictMode: true,
  experimental: {
    // The rewrite proxy aborts after 30 s by default; route optimisation, geocoding and report
    // generation are synchronous backend calls that may take longer.
    proxyTimeout: 120_000,
  },
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: `${backendUrl}/api/:path*`,
      },
    ];
  },
  async headers() {
    return [
      {
        source: "/:path*",
        headers: [
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "X-Frame-Options", value: "DENY" },
          { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
        ],
      },
    ];
  },
};

export default nextConfig;
