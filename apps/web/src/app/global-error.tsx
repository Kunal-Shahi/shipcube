"use client";

// Last-resort boundary when the root layout itself fails. It replaces the whole document,
// so it carries its own minimal markup and styles.
export default function GlobalError({ error, reset }: { error: Error & { digest?: string }; reset: () => void }) {
  return (
    <html lang="en">
      <body style={{ fontFamily: "system-ui, sans-serif", padding: 32, maxWidth: 560, color: "#15171c", background: "#f7f8fa" }}>
        <h1 style={{ fontSize: 20 }}>ShipCube&apos;s console couldn&apos;t start</h1>
        <p style={{ color: "#5d626d" }}>
          Your deployed apps aren&apos;t affected. Reload the page; if it keeps failing, report the reference below.
        </p>
        {error.digest && <p style={{ fontFamily: "ui-monospace, monospace", fontSize: 12 }}>Reference {error.digest}</p>}
        <button type="button" onClick={reset} style={{ marginTop: 12, padding: "6px 12px" }}>
          Try again
        </button>
      </body>
    </html>
  );
}
