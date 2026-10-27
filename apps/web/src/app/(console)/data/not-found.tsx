import { ButtonLink, EmptyState, Panel } from "@/components/ui";

export default function NotFound() {
  return (
    <Panel>
      <EmptyState
        title="Not in the data model"
        body="This cube or migration doesn't exist in this workspace. It may have been renamed by a later migration."
        action={<ButtonLink href="/data">See all cubes</ButtonLink>}
      />
    </Panel>
  );
}
