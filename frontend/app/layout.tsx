import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "PaperPilot — Evidence-first research",
  description: "根拠から考える、論文RAG研究ワークスペース",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="ja"><body>{children}</body></html>;
}
