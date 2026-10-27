import { EmptyState, Panel, ButtonLink } from "@/components/ui";

export default function NotFound() {
  return (
    <Panel>
      <EmptyState
        title="App not found"
        body="It may have been deleted, or it belongs to a workspace you're not a member of. Ask the owner to share it with you."
        action={<ButtonLink href="/apps">Back to apps</ButtonLink>}
      />
    </Panel>
  );
}
