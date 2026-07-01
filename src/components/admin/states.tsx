import { LoaderCircle, TriangleAlert } from "lucide-react";

import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";

export function AdminLoading(): React.ReactElement {
  return (
    <main className="flex min-h-dvh items-center justify-center p-5">
      <div className="flex flex-col items-center gap-3 text-muted-foreground">
        <LoaderCircle className="size-7 animate-spin" aria-hidden="true" />
        <p className="text-sm">Loading…</p>
      </div>
    </main>
  );
}

export function AdminError({
  title,
  body,
  children,
}: {
  title: string;
  body: string;
  children?: React.ReactNode;
}): React.ReactElement {
  return (
    <main className="flex min-h-dvh items-center justify-center p-5">
      <Card className="w-full max-w-md">
        <CardHeader className="items-center gap-4 p-8 text-center">
          <span className="flex size-12 items-center justify-center rounded-full bg-warning-soft text-warning [&_svg]:size-6">
            <TriangleAlert aria-hidden="true" />
          </span>
          <CardTitle>{title}</CardTitle>
          <CardDescription className="text-balance leading-relaxed">
            {body}
          </CardDescription>
        </CardHeader>
        {children ? <CardContent className="text-center">{children}</CardContent> : null}
      </Card>
    </main>
  );
}
