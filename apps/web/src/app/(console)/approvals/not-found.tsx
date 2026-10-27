import { ButtonLink, EmptyState, Panel } from "@/components/ui";

export default function NotFound() {
  return (
    <Panel>
      <EmptyState
        title="Approval request not found"
        body="The link may be from another workspace, or the request is older than 90 days. Ask the agent to try the action again; it will get a new link."
        action={<ButtonLink href="/approvals">See all approvals</ButtonLink>}
      />
    </Panel>
  );
}
