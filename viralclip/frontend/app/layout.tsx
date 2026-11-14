import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: { default: "ViralClip", template: "%s · ViralClip" },
  description: "Turn long videos into captioned short clips and publish them to YouTube.",
};

export const viewport = { themeColor: "#0b0c0e" };

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="min-h-dvh font-sans antialiased">{children}</body>
    </html>
  );
}
