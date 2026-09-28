import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { cn } from "@/lib/utils";

/** Labelled form field: label (with optional "Optional" marker), the
 * control, and an optional hint line under it. */
export function Field({
  id,
  label,
  optional,
  hint,
  children,
  className,
}: {
  id?: string;
  label: string;
  optional?: boolean;
  hint?: React.ReactNode;
  children: React.ReactNode;
  className?: string;
}): React.ReactElement {
  return (
    <div className={cn("flex flex-col gap-1.5", className)}>
      <Label htmlFor={id}>
        {label}
        {optional ? (
          <span className="ml-1.5 font-normal text-muted-foreground">
            Optional
          </span>
        ) : null}
      </Label>
      {children}
      {hint ? <p className="text-xs text-muted-foreground">{hint}</p> : null}
    </div>
  );
}

/** A setting that's on or off: title + one-line explanation on the left,
 * switch on the right. Stack several inside <ToggleList>. */
export function ToggleRow({
  id,
  label,
  description,
  checked,
  onCheckedChange,
  disabled,
}: {
  id: string;
  label: string;
  description?: React.ReactNode;
  checked: boolean;
  onCheckedChange: (v: boolean) => void;
  disabled?: boolean;
}): React.ReactElement {
  return (
    <div
      className={cn(
        "flex items-start justify-between gap-4 px-4 py-3",
        disabled && "opacity-60",
      )}
    >
      <div className="min-w-0">
        <Label htmlFor={id} className="text-sm font-medium">
          {label}
        </Label>
        {description ? (
          <p className="mt-0.5 text-xs text-muted-foreground">{description}</p>
        ) : null}
      </div>
      <Switch
        id={id}
        checked={checked}
        onCheckedChange={onCheckedChange}
        disabled={disabled}
        className="mt-0.5 shrink-0"
      />
    </div>
  );
}

/** Bordered group of ToggleRows with hairlines between them. */
export function ToggleList({
  children,
}: {
  children: React.ReactNode;
}): React.ReactElement {
  return (
    <div className="divide-y divide-border rounded-lg border border-border">
      {children}
    </div>
  );
}

/** Small uppercase-free section label inside a longer form. */
export function FormSectionLabel({
  children,
}: {
  children: React.ReactNode;
}): React.ReactElement {
  return (
    <h3 className="text-xs font-medium text-muted-foreground">{children}</h3>
  );
}
