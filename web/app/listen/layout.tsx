import type { Metadata } from "next";

// A blind listener should see nothing that names the project or its songs.
export const metadata: Metadata = {
  title: "Listening test",
  description: "A short blind listening test.",
};

export default function ListenLayout({ children }: { children: React.ReactNode }) {
  return children;
}
