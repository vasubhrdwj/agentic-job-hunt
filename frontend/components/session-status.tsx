"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import {
  deleteOwnerSession,
  getOwnerSession,
  type OwnerSession,
} from "@/lib/session";
import { clearAllRunAccess } from "@/lib/run-access";
import { clearPendingHuntIdempotency } from "@/lib/hunt-idempotency";

export function SessionStatus() {
  const router = useRouter();
  const [session, setSession] = useState<OwnerSession | null>(null);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    let active = true;
    getOwnerSession()
      .then((value) => {
        if (active) setSession(value);
      })
      .catch(() => undefined)
      .finally(() => {
        if (active) setLoaded(true);
      });
    return () => {
      active = false;
    };
  }, []);

  if (!loaded) {
    return <span className="text-xs text-zinc-500">Checking private workspace…</span>;
  }
  if (!session) {
    return (
      <Link
        href="/login"
        className="text-sm font-medium text-zinc-700 underline underline-offset-4 hover:text-zinc-950 dark:text-zinc-300 dark:hover:text-white"
      >
        Owner sign in
      </Link>
    );
  }
  return (
    <div className="flex items-center gap-3 text-sm">
      <span className="text-zinc-600 dark:text-zinc-400">
        Private workspace · {session.display_name}
      </span>
      <button
        type="button"
        className="font-medium text-zinc-700 underline underline-offset-4 hover:text-zinc-950 dark:text-zinc-300 dark:hover:text-white"
        onClick={async () => {
          await deleteOwnerSession();
          clearAllRunAccess();
          clearPendingHuntIdempotency();
          setSession(null);
          router.replace("/login");
          router.refresh();
        }}
      >
        Sign out
      </button>
    </div>
  );
}
