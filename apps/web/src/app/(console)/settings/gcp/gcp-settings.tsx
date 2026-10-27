"use client";

import { GcpLinker } from "@/components/gcp-linker";
import type { GcpLink } from "@/lib/types";

export function GcpSettings(props: { link: GcpLink; deployer: string; canLink: boolean; creditsLeft?: string }) {
  return (
    <GcpLinker
      initial={props.link}
      deployer={props.deployer}
      canLink={props.canLink}
      creditsLabel={props.creditsLeft ? `your ShipCube credits (${props.creditsLeft} left)` : undefined}
    />
  );
}
