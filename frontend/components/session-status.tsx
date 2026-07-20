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
import { clearPendingSubmissionHandoffs } from "@/lib/application-submission-handoff";

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
    return <span className="text-xs text-zinc-500">Checking your session…</span>;
  }
  if (!session) {
    return (
      <Link
        href="/login"
        className="text-sm font-medium text-zinc-700 underline underline-offset-4 hover:text-zinc-950 dark:text-zinc-300 dark:hover:text-white"
      >
        Sign in
      </Link>
    );
  }
  return (
    <div className="flex items-center gap-3 text-sm">
      <Link
        href="/account"
        className="text-zinc-600 hover:text-zinc-950 dark:text-zinc-400 dark:hover:text-white"
      >
        Signed in · {session.display_name}
      </Link>
      {!session.account_attached ? (
        <Link
          href="/account"
          className="rounded-full border border-amber-300 bg-amber-50 px-2.5 py-1 text-xs font-medium text-amber-950 hover:bg-amber-100 dark:border-amber-800 dark:bg-amber-950/40 dark:text-amber-100 dark:hover:bg-amber-950"
        >
          Secure account
        </Link>
      ) : null}
      <button
        type="button"
        className="font-medium text-zinc-700 underline underline-offset-4 hover:text-zinc-950 dark:text-zinc-300 dark:hover:text-white"
        onClick={async () => {
          await deleteOwnerSession();
          clearAllRunAccess();
          clearPendingHuntIdempotency();
          clearPendingSubmissionHandoffs();
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
