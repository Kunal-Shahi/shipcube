"use client";

import { ErrorState } from "@/components/error-state";

export default function ApprovalsError(props: { error: Error & { digest?: string }; reset: () => void }) {
  return <ErrorState title="Couldn't load approvals" {...props} />;
}
