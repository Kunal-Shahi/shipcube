import Link from "next/link";
import { Wordmark } from "@/components/logo";

export default function NotFound() {
  return (
    <main className="mx-auto flex min-h-dvh max-w-[480px] flex-col justify-center gap-3 px-4">
      <Wordmark />
      <h1 className="text-[20px] font-semibold">Page not found</h1>
      <p className="text-fg-muted">The link may be out of date, or the page was moved.</p>
      <Link href="/apps" className="text-accent hover:underline">
        Go to your apps
      </Link>
    </main>
  );
}
