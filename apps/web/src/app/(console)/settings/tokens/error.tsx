"use client";

import { ErrorState } from "@/components/error-state";

export default function TokensError(props: { error: Error & { digest?: string }; reset: () => void }) {
  return <ErrorState title="Couldn't load your API tokens" {...props} />;
}
