import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Keep the frontend self-contained when started from this repository.
  turbopack: { root: process.cwd() },
  // The local all-in-one Compose stack runs Next.js in a container.
  output: "standalone",
};
export default nextConfig;
