"use client";

import { ErrorState } from "@/components/error-state";

export default function AppsError(props: { error: Error & { digest?: string }; reset: () => void }) {
  return <ErrorState title="Couldn't load your apps" {...props} />;
}
