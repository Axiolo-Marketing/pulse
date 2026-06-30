import * as React from "react";

import { cn } from "@/lib/utils";

// Lightweight label (no Radix dependency) — sufficient for our forms, which
// associate labels via wrapping or htmlFor.
const Label = React.forwardRef<HTMLLabelElement, React.ComponentProps<"label">>(
  ({ className, ...props }, ref) => (
    <label
      ref={ref}
      className={cn(
        "text-sm font-medium leading-none text-foreground",
        className,
      )}
      {...props}
    />
  ),
);
Label.displayName = "Label";

export { Label };
