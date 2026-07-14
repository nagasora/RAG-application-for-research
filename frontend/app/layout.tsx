import type { Metadata } from "next";
import Script from "next/script";
import "katex/dist/katex.min.css";
import "./globals.css";
import { PersonalSettings } from "@/components/personal-settings";

export const metadata: Metadata = {
  title: "PaperPilot — Evidence-first research",
  description: "根拠から考える、論文RAG研究ワークスペース",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="ja" suppressHydrationWarning>
    <head>
      <Script id="paperpilot-theme" strategy="beforeInteractive">{`(function(){try{var key="paperpilot.theme";var value=localStorage.getItem(key);var theme=value==="light"||value==="dark"||value==="system"?value:"system";var dark=theme==="dark"||(theme==="system"&&window.matchMedia("(prefers-color-scheme: dark)").matches);var root=document.documentElement;root.dataset.theme=theme;root.dataset.resolvedTheme=dark?"dark":"light";root.style.colorScheme=dark?"dark":"light";}catch(e){}})();`}</Script>
    </head>
    <body>{children}<PersonalSettings /></body>
  </html>;
}
