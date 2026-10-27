"use client";

import { ErrorState } from "@/components/error-state";

export default function ConsoleError(props: { error: Error & { digest?: string }; reset: () => void }) {
  return <ErrorState title="This page couldn't load" {...props} />;
}
