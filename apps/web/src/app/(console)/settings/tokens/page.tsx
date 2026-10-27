import type { Metadata } from "next";
import { PageHeader } from "@/components/ui";
import { demoState, listTokens } from "@/lib/api";
import { TokenList } from "./token-list";

export const metadata: Metadata = { title: "API tokens" };

export default async function TokensPage({ searchParams }: { searchParams: Promise<Record<string, string | string[] | undefined>> }) {
  const tokens = await listTokens(demoState(await searchParams));
  return (
    <>
      <PageHeader
        title="API tokens"
        description="Tokens let coding agents use ShipCube's MCP server as you. ShipCube stores only a hash of each token."
      />
      <TokenList initial={tokens} />
    </>
  );
}
