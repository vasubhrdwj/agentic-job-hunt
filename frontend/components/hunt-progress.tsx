"use client";

import { useEffect, useState } from "react";

const STAGES = [
  { label: "Searching for matching roles", until: 30 },
  { label: "Finding referral targets", until: 60 },
  { label: "Drafting outreach", until: Infinity },
];

function formatElapsed(seconds: number): string {
  const mins = Math.floor(seconds / 60);
  const secs = seconds % 60;
  return `${mins}:${secs.toString().padStart(2, "0")}`;
}

export function HuntProgress() {
  const [elapsed, setElapsed] = useState(0);

  useEffect(() => {
    const start = Date.now();
    const id = setInterval(() => {
      setElapsed(Math.floor((Date.now() - start) / 1000));
    }, 1000);
    return () => clearInterval(id);
  }, []);

  const activeIndex = STAGES.findIndex((stage) => elapsed < stage.until);
  const safeIndex = activeIndex === -1 ? STAGES.length - 1 : activeIndex;

  return (
    <div
      role="status"
      aria-live="polite"
      className="rounded-lg border border-zinc-200 bg-white p-6 shadow-sm dark:border-zinc-800 dark:bg-zinc-900"
    >
      <div className="flex items-center justify-between text-sm">
        <span className="font-medium">Running hunt…</span>
        <span className="font-mono text-zinc-500">{formatElapsed(elapsed)}</span>
      </div>

      <ul className="mt-4 space-y-2">
        {STAGES.map((stage, index) => {
          const isDone = index < safeIndex;
          const isActive = index === safeIndex;
          return (
            <li key={stage.label} className="flex items-center gap-3 text-sm">
              <span
                className={`inline-flex h-5 w-5 items-center justify-center rounded-full text-xs ${
                  isDone
                    ? "bg-emerald-500 text-white"
                    : isActive
                      ? "bg-indigo-500 text-white"
                      : "bg-zinc-200 text-zinc-500 dark:bg-zinc-800"
                }`}
                aria-hidden
              >
                {isDone ? "✓" : index + 1}
              </span>
              <span
                className={
                  isActive
                    ? "font-medium"
                    : isDone
                      ? "text-zinc-500 line-through"
                      : "text-zinc-500"
                }
              >
                {stage.label}
                {isActive && <span className="ml-2 animate-pulse">…</span>}
              </span>
            </li>
          );
        })}
      </ul>

      <div
        className="mt-4 h-1 overflow-hidden rounded bg-zinc-200 dark:bg-zinc-800"
        aria-hidden
      >
        <div className="h-full w-1/3 animate-[progress_2s_ease-in-out_infinite] bg-indigo-500" />
      </div>

      <p className="mt-4 text-xs text-zinc-500">
        This usually takes 60–90 seconds.
      </p>

      <style>{`
        @keyframes progress {
          0% { transform: translateX(-100%); }
          50% { transform: translateX(150%); }
          100% { transform: translateX(350%); }
        }
      `}</style>
    </div>
  );
}
