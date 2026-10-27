import { notFound } from "next/navigation";
import { Panel } from "@/components/ui";
import { demoState, getApp } from "@/lib/api";
import { LogViewer } from "./log-viewer";

type Props = {
  params: Promise<{ id: string }>;
  searchParams: Promise<Record<string, string | string[] | undefined>>;
};

export default async function LogsPage({ params, searchParams }: Props) {
  const [{ id }, sp] = await Promise.all([params, searchParams]);
  const app = await getApp(id, demoState(sp));
  if (!app) notFound();
  return (
    <Panel>
      <LogViewer logs={app.logs} streaming={app.health === "deploying"} />
    </Panel>
  );
}
