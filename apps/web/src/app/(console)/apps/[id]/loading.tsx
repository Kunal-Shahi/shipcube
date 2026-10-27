import { Panel, Skeleton, TableSkeleton } from "@/components/ui";

export default function Loading() {
  return (
    <div className="grid gap-4 lg:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]" aria-busy="true">
      <Panel className="flex flex-col gap-3 p-4">
        <Skeleton className="h-4 w-40" />
        <Skeleton className="w-full" />
        <Skeleton className="w-5/6" />
        <Skeleton className="w-4/6" />
      </Panel>
      <Panel><TableSkeleton rows={4} cols={2} /></Panel>
    </div>
  );
}
