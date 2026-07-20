"use client";

import { type FormEvent, useState } from "react";

import {
  claimWorkspaceAccount,
  type OwnerSession,
} from "@/lib/session";
import {
  FormField,
  inputClasses,
  primaryButtonClasses,
  StatusMessage,
  WorkspaceSection,
} from "@/components/workspace-ui";

export function AccountWorkspace({
  initialSession,
  secured,
}: {
  initialSession: OwnerSession;
  secured: boolean;
}) {
  const [email, setEmail] = useState("");
  const [confirmEmail, setConfirmEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function claimAccount(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (normalizedEmail(email) !== normalizedEmail(confirmEmail)) {
      setError("Email addresses do not match.");
      return;
    }
    if (password !== confirmPassword) {
      setError("Passwords do not match.");
      return;
    }
    setPending(true);
    setError(null);
    try {
      await claimWorkspaceAccount({ email, password });
      window.location.replace("/account?secured=1");
    } catch (reason) {
      setPassword("");
      setConfirmPassword("");
      setError(
        reason instanceof Error
          ? reason.message
          : "Unable to secure this workspace right now.",
      );
      setPending(false);
    }
  }

  if (initialSession.account_attached) {
    return (
      <div className="space-y-6">
        {secured ? (
          <StatusMessage kind="success">
            Account secured. You can now sign in with this email and password.
          </StatusMessage>
        ) : null}
        <WorkspaceSection
          eyebrow="Account"
          title="Your workspace is secured"
          description="This account is the only identity that can open its saved profile, resumes, searches, applications, and outreach history."
        >
          <dl className="grid gap-5 sm:grid-cols-2">
            <AccountDetail label="Display name" value={initialSession.display_name} />
            <AccountDetail
              label="Email"
              value={initialSession.account_email}
            />
          </dl>
          <div className="mt-6 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm leading-6 text-amber-950 dark:border-amber-900 dark:bg-amber-950/40 dark:text-amber-100">
            Local beta: email verification, password changes, and password recovery
            are not available yet. Keep your password somewhere safe and do not reuse
            it on another service.
          </div>
        </WorkspaceSection>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {error ? <StatusMessage kind="error">{error}</StatusMessage> : null}
      <WorkspaceSection
        eyebrow="One-time upgrade"
        title="Secure this existing workspace"
        description="Attach an email and password to the workspace you already use. Your profile, resumes, searches, applications, and outreach history stay with this account."
      >
        <form onSubmit={claimAccount} className="max-w-xl space-y-5">
          <FormField label="Email" htmlFor="account-email">
            <input
              id="account-email"
              name="account-email"
              type="email"
              inputMode="email"
              autoComplete="email"
              required
              maxLength={254}
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              className={inputClasses}
            />
          </FormField>
          <FormField label="Confirm email" htmlFor="account-confirm-email">
            <input
              id="account-confirm-email"
              name="account-confirm-email"
              type="email"
              inputMode="email"
              autoComplete="email"
              required
              maxLength={254}
              value={confirmEmail}
              onChange={(event) => setConfirmEmail(event.target.value)}
              className={inputClasses}
            />
          </FormField>
          <FormField
            label="Create a password"
            htmlFor="account-password"
            hint="Use at least 12 characters and a password unique to this app. Password recovery is not available in this beta yet."
          >
            <input
              id="account-password"
              name="account-password"
              type="password"
              autoComplete="new-password"
              required
              minLength={12}
              maxLength={128}
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              className={inputClasses}
            />
          </FormField>
          <FormField label="Confirm password" htmlFor="account-confirm-password">
            <input
              id="account-confirm-password"
              name="account-confirm-password"
              type="password"
              autoComplete="new-password"
              required
              minLength={12}
              maxLength={128}
              value={confirmPassword}
              onChange={(event) => setConfirmPassword(event.target.value)}
              className={inputClasses}
            />
          </FormField>
          <button type="submit" disabled={pending} className={primaryButtonClasses}>
            {pending ? "Securing workspace…" : "Secure this existing workspace"}
          </button>
        </form>
        <p className="mt-5 max-w-2xl text-xs leading-5 text-zinc-500 dark:text-zinc-400">
          This is a one-time change. After it succeeds, email and password become
          the sign-in method for this workspace. Other active browser sessions are
          revoked as a safety measure.
        </p>
      </WorkspaceSection>
    </div>
  );
}

function AccountDetail({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="text-xs font-medium uppercase tracking-[0.12em] text-zinc-500">
        {label}
      </dt>
      <dd className="mt-2 break-words text-sm font-medium text-zinc-900 dark:text-zinc-100">
        {value}
      </dd>
    </div>
  );
}

function normalizedEmail(value: string): string {
  return value.normalize("NFKC").trim().toLocaleLowerCase("en-US");
}
