import { Panel, Skeleton, TableSkeleton } from "@/components/ui";

export default function Loading() {
  return (
    <div aria-busy="true">
      <div className="mb-6 flex flex-col gap-2">
        <Skeleton className="h-5 w-48" />
        <Skeleton className="w-96 max-w-full" />
      </div>
      <Panel>
        <TableSkeleton rows={5} cols={4} />
      </Panel>
    </div>
  );
}
