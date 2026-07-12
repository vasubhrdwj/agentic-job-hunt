import { redirect } from "next/navigation";
import type { ReactNode } from "react";

import { getServerOwnerSession } from "@/lib/server-session";

export const dynamic = "force-dynamic";

export default async function PrivateRunsLayout({
  children,
}: Readonly<{ children: ReactNode }>) {
  const session = await getServerOwnerSession();
  if (!session) redirect("/login");
  return children;
}
