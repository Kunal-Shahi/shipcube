"use client";

import { ErrorState } from "@/components/error-state";

export default function DataError(props: { error: Error & { digest?: string }; reset: () => void }) {
  return <ErrorState title="Couldn't load the data model" {...props} />;
}
