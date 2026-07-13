import { getServerOwnerSession } from "@/lib/server-session";
import { redirect } from "next/navigation";

export const dynamic = "force-dynamic";

export default async function HomePage() {
  const session = await getServerOwnerSession();
  if (!session) redirect("/login");
  redirect("/today");
}
