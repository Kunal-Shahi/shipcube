"use client";

import { ErrorState } from "@/components/error-state";

export default function AppError(props: { error: Error & { digest?: string }; reset: () => void }) {
  return <ErrorState title="Couldn't load this app" {...props} />;
}
