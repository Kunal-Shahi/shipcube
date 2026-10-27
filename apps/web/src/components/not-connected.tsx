import { PlugZap } from "lucide-react";
import { supports, type Feature } from "@/lib/api";
import { FEATURE_ENDPOINT } from "@/lib/server/data";
import { Panel } from "./ui";

/**
 * Renders an honest placeholder when this deployment's API can't serve a feature yet,
 * and null when it can. Pages call it before fetching, so production never shows mocks.
 */
export async function notConnected(feature: Feature) {
  if (await supports(feature)) return null;
  const f = FEATURE_ENDPOINT[feature];
  return (
    <Panel>
      <div className="flex flex-col items-start gap-2 px-6 py-10">
        <PlugZap className="size-5 text-fg-subtle" aria-hidden />
        <h2 className="text-[14px] font-semibold">{f.name} isn&apos;t available yet</h2>
        <p className="max-w-[64ch] text-fg-muted">
          This console reads everything from ShipCube&apos;s control-plane API, and the API doesn&apos;t serve this yet. Nothing is shown
          here rather than made-up data.
        </p>
        <p className="font-mono text-[12px] text-fg-subtle">Needs {f.endpoint}</p>
      </div>
    </Panel>
  );
}
