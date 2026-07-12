import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Prevent parent-level lockfiles from changing Turbopack's workspace root.
  turbopack: { root: process.cwd() },
};
export default nextConfig;
