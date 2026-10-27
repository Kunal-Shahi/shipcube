import { CloudUpload, Gauge, Globe, Trash2 } from "lucide-react";
import type { ApprovalKind, ApprovalStatus } from "@/lib/types";
import { Pill } from "./ui";

export const APPROVAL_KIND: Record<ApprovalKind, { label: string; icon: typeof Globe; verb: string }> = {
  first_linked_deploy: { label: "Deploy to your GCP", icon: CloudUpload, verb: "Approve deploy" },
  cap_raise: { label: "Cap raise", icon: Gauge, verb: "Approve new cap" },
  domain_grant: { label: "Domain sharing", icon: Globe, verb: "Approve sharing" },
  delete_app: { label: "Delete app", icon: Trash2, verb: "Delete app" },
};

export function ApprovalStatusPill({ status }: { status: ApprovalStatus }) {
  switch (status) {
    case "pending":
      return <Pill tone="accent">Waiting for you</Pill>;
    case "approved":
      return <Pill tone="good">Approved</Pill>;
    case "rejected":
      return <Pill tone="critical">Rejected</Pill>;
    case "expired":
      return <Pill>Expired</Pill>;
  }
}
