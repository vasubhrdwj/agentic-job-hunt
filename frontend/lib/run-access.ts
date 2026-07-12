"use client";

const PREFIX = "job-hunt-run-access:";

export function saveRunAccess(runId: string, accessToken: string): void {
  sessionStorage.setItem(`${PREFIX}${runId}`, accessToken);
}

export function loadRunAccess(runId: string): string | null {
  return sessionStorage.getItem(`${PREFIX}${runId}`);
}

export function clearRunAccess(runId: string): void {
  sessionStorage.removeItem(`${PREFIX}${runId}`);
}

export function clearAllRunAccess(): void {
  for (let index = sessionStorage.length - 1; index >= 0; index -= 1) {
    const key = sessionStorage.key(index);
    if (key?.startsWith(PREFIX)) sessionStorage.removeItem(key);
  }
}
