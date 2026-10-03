import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Phone testing hits the dev server from the LAN, and Next blocks
  // cross-origin /_next/* dev resources by default — which leaves the page
  // stuck on its server-rendered loading state with no error.
  allowedDevOrigins: ["127.0.0.1", "localhost", "192.168.68.70", "192.168.68.*"],
  // The scaffold writes AGENTS.md/CLAUDE.md into web/ on every run.
  agentRules: false,
};

export default nextConfig;
