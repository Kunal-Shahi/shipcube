import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import localFont from "next/font/local";
import { headers } from "next/headers";
import { themeScript } from "@/components/theme-toggle";
import "./globals.css";

const geistSans = Geist({ variable: "--font-geist-sans", subsets: ["latin"] });
const geistMono = Geist_Mono({ variable: "--font-geist-mono", subsets: ["latin"] });
// Headings on the landing page and in the console. Self-hosted (src/app/fonts): CI builds failed
// while downloading it from Google Fonts.
const display = localFont({
  src: "./fonts/bricolage-grotesque-latin.woff2",
  variable: "--font-display",
  weight: "200 800",
  display: "swap",
});

export const metadata: Metadata = {
  title: { default: "ShipCube", template: "%s · ShipCube" },
  description: "Deploy, share and monitor the internal apps you build with your coding agent.",
};

export default async function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  const nonce = (await headers()).get("x-nonce") ?? undefined;
  // The font variables go on <html>: the theme (globals.css) reads them there, on :root.
  return (
    <html lang="en" className={`${geistSans.variable} ${geistMono.variable} ${display.variable}`} suppressHydrationWarning>
      <head>
        {/* Browsers hide the nonce attribute after load, so the client sees "" here. */}
        <script nonce={nonce} suppressHydrationWarning dangerouslySetInnerHTML={{ __html: themeScript }} />
      </head>
      <body>{children}</body>
    </html>
  );
}
