"use client";

import { RotateCcw } from "lucide-react";
import { useState } from "react";
import { restoreAppAction } from "@/app/(console)/actions";
import { ActionErrorText } from "./action-error";
import { Button } from "./ui";
import { useAction } from "./use-action";

export function RestoreButton({ appId, name, size = "sm" }: { appId: string; name: string; size?: "sm" | "md" }) {
  const [done, setDone] = useState(false);
  const restore = useAction(restoreAppAction);
  if (done) return <span role="status" className="text-[12px] text-good">Restored. {name} is serving again.</span>;
  return (
    <div className="flex flex-col items-end gap-1">
      <Button
        size={size}
        pending={restore.pending}
        onClick={async () => {
          if ((await restore.run(appId)) !== undefined) setDone(true);
        }}
      >
        {!restore.pending && <RotateCcw className="size-3.5" aria-hidden />}
        Restore
      </Button>
      <ActionErrorText error={restore.error} message={restore.message} />
    </div>
  );
}
