import type { NextRequest } from "next/server";

import { wakeSleepingBackend } from "@/lib/cadence-wake";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";
export const maxDuration = 60;

export function GET(request: NextRequest) {
  return wakeSleepingBackend(request);
}
