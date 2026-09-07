import type { Metadata, Viewport } from "next";

import "./globals.css";

const TITLE = "LUMEN — See what changed. Understand why. Know what matters.";
const DESCRIPTION =
  "An intelligent market watchlist that detects what changed, explains why it "
  + "matters, and helps you focus on the signals worth your attention.";

export const metadata: Metadata = {
  title: {
    default: TITLE,
    // Page-level titles render as "Dashboard · LUMEN".
    template: "%s · LUMEN",
  },
  description: DESCRIPTION,
  applicationName: "LUMEN",
  keywords: [
    "market intelligence",
    "stock watchlist",
    "NSE",
    "market change detection",
  ],
  openGraph: {
    title: TITLE,
    description: DESCRIPTION,
    siteName: "LUMEN",
    type: "website",
  },
  twitter: {
    card: "summary_large_image",
    title: TITLE,
    description: DESCRIPTION,
  },
  robots: { index: true, follow: true },
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  themeColor: "#0d1117",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
