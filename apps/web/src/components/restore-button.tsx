"use client";

import { RotateCcw } from "lucide-react";
import { useState } from "react";
import { Button } from "./ui";

export function RestoreButton({ name, size = "sm" }: { name: string; size?: "sm" | "md" }) {
  const [state, setState] = useState<"idle" | "pending" | "done">("idle");
  if (state === "done") return <span role="status" className="text-[12px] text-good">Restored. {name} is serving again.</span>;
  return (
    <Button
      size={size}
      pending={state === "pending"}
      onClick={() => {
        setState("pending");
        setTimeout(() => setState("done"), 700);
      }}
    >
      {state !== "pending" && <RotateCcw className="size-3.5" aria-hidden />}
      Restore
    </Button>
  );
}
