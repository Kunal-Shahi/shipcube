import type { Metadata } from "next";
import { Bricolage_Grotesque, Geist, Geist_Mono } from "next/font/google";
import { headers } from "next/headers";
import { themeScript } from "@/components/theme-toggle";
import "./globals.css";

const geistSans = Geist({ variable: "--font-geist-sans", subsets: ["latin"] });
const geistMono = Geist_Mono({ variable: "--font-geist-mono", subsets: ["latin"] });
// Headings on the landing page and in the console.
const display = Bricolage_Grotesque({ variable: "--font-display", subsets: ["latin"], weight: ["600", "800"] });

export const metadata: Metadata = {
  title: { default: "ShipCube", template: "%s · ShipCube" },
  description: "Deploy, share and monitor the internal apps you build with your coding agent.",
};

export default async function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  const nonce = (await headers()).get("x-nonce") ?? undefined;
  return (
    <html lang="en" suppressHydrationWarning>
      <head>
        {/* Browsers hide the nonce attribute after load, so the client sees "" here. */}
        <script nonce={nonce} suppressHydrationWarning dangerouslySetInnerHTML={{ __html: themeScript }} />
      </head>
      <body className={`${geistSans.variable} ${geistMono.variable} ${display.variable}`}>{children}</body>
    </html>
  );
}
