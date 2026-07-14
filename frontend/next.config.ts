import type { NextConfig } from "next";

const isCloudflarePages = process.env.NEXT_DEPLOY_TARGET === "cloudflare-pages";

const nextConfig: NextConfig = {
  // Prevent parent-level lockfiles from changing Turbopack's workspace root.
  turbopack: { root: process.cwd() },
  // Cloudflare Pages serves this client-first application as static assets.
  // Keep the Node standalone output for the existing Docker/Render deployment.
  output: isCloudflarePages ? "export" : "standalone",
};
export default nextConfig;
