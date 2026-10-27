"use client";

import { GcpLinker } from "@/components/gcp-linker";
import type { GcpLink } from "@/lib/types";

export function GcpSettings({ link, creditsLeft }: { link: GcpLink; creditsLeft: string }) {
  return <GcpLinker initial={link} creditsLabel={`your ShipCube credits (${creditsLeft} left)`} />;
}
