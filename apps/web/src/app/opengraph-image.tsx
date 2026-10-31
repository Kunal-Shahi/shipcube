import { ImageResponse } from "next/og";

// The link preview when the landing page is shared. Next serves it at /opengraph-image (public in
// src/middleware.ts) and points og:image at it, relative to the request's host (see generateMetadata
// in (public)/page.tsx).
export const alt = "ShipCube: your agent builds it, the cloud runs it";
export const size = { width: 1200, height: 630 };
export const contentType = "image/png";

/** The landing page's display face, as a TTF (the image renderer can't read woff2). Optional: the
 * default font is used if Google Fonts can't be reached while building. */
async function displayFont(): Promise<ArrayBuffer | null> {
  try {
    // An old user agent makes Google Fonts answer with TrueType URLs.
    const css = await (
      await fetch("https://fonts.googleapis.com/css2?family=Bricolage+Grotesque:wght@800", { headers: { "User-Agent": "Mozilla/4.0" } })
    ).text();
    const url = css.match(/src: url\((https:[^)]+)\)/)?.[1];
    return url ? await (await fetch(url)).arrayBuffer() : null;
  } catch {
    return null;
  }
}

export default async function OpenGraphImage() {
  const font = await displayFont();
  return new ImageResponse(
    (
      <div
        style={{
          width: "100%",
          height: "100%",
          display: "flex",
          flexDirection: "column",
          justifyContent: "space-between",
          padding: "72px 80px",
          backgroundColor: "#070a18",
          backgroundImage: "radial-gradient(circle at 85% 30%, rgba(123,140,255,0.35), rgba(7,10,24,0) 60%)",
          color: "#eef1ff",
          fontFamily: "sans-serif",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 18, fontSize: 34, fontWeight: 700 }}>
          <svg width="44" height="44" viewBox="0 0 20 20">
            <path d="M10 1.5 17.5 5.75 10 10 2.5 5.75Z" fill="#eef1ff" />
            <path d="M2.5 5.75 10 10v8.5L2.5 14.25Z" fill="#a6aed6" />
            <path d="M17.5 5.75 10 10v8.5l7.5-4.25Z" fill="#7b8cff" />
          </svg>
          ShipCube
        </div>
        <div style={{ display: "flex", flexDirection: "column", gap: 24 }}>
          <div style={{ fontSize: 80, fontWeight: 800, lineHeight: 1.02, letterSpacing: -2, maxWidth: 1000, fontFamily: font ? "Display" : undefined }}>
            Your agent builds it. The cloud runs it.
          </div>
          <div style={{ fontSize: 30, color: "#a6aed6", maxWidth: 940 }}>
            One control plane for AI-built apps, jobs and data on Google Cloud, driven from Claude Code, Cursor and other agents.
          </div>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 14, fontSize: 26, color: "#ffb547" }}>
          <div style={{ width: 14, height: 14, borderRadius: 7, background: "#ffb547" }} />
          Anything risky waits for a person
        </div>
      </div>
    ),
    { ...size, fonts: font ? [{ name: "Display", data: font, weight: 800, style: "normal" }] : [] },
  );
}
