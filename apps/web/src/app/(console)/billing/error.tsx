"use client";

import { ErrorState } from "@/components/error-state";

export default function BillingError(props: { error: Error & { digest?: string }; reset: () => void }) {
  return <ErrorState title="Couldn't load billing" {...props} />;
}
