import type { NextConfig } from "next";

const apiProxyTarget = process.env.PAPERPILOT_API_PROXY_TARGET || "http://localhost:8000";

const nextConfig: NextConfig = {
  // Keep the frontend self-contained when started from this repository.
  turbopack: { root: process.cwd() },
  // The local all-in-one Compose stack runs Next.js in a container.
  output: "standalone",
  async rewrites() {
    // In local development, browser requests stay on the Next.js origin so
    // cookies and workspace headers follow the same path as production.
    // Deployments that set NEXT_PUBLIC_API_URL keep using that explicit API.
    if (process.env.NEXT_PUBLIC_API_URL) return [];
    return [{ source: "/api/:path*", destination: `${apiProxyTarget}/api/:path*` }];
  },
};
export default nextConfig;
