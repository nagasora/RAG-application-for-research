import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Prevent parent-level lockfiles from changing Turbopack's workspace root.
  turbopack: { root: process.cwd() },
  // Produce the minimal Node.js runtime used by the deployment container.
  output: "standalone",
};
export default nextConfig;
