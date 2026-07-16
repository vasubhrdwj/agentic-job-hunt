# Manual browser matrix

This matrix is manual until the repository can install and pin Playwright plus
its browser binaries. CI does not claim cross-browser automation yet.

Test the same deployed commit in current **Chromium**, **Firefox**, and
**WebKit/Safari**. Use a desktop viewport and a mobile viewport of **390 × 844**.
Use mock/local data and do not start a hunt, saved-search scan, contact search,
or other provider call.

For each browser:

1. Open `/login`. Confirm the owner-token field has a visible label, focus is
   visible, Tab reaches the submit button, Enter submits, and an invalid token
   produces a readable error without entering the workspace.
2. Sign in with the test owner. Confirm navigation lands on `/today`, there is
   one main landmark, headings are ordered, navigation has an accessible name,
   and every interactive control has a visible or accessible label.
3. Open **Weekly Review** (`/review`). Confirm the summary, funnel metrics,
   source signals, aging applications, follow-up rescue data, and next-week
   plan load without console or network errors.
4. Open **Privacy** (`/privacy`). Confirm export, retention, deletion preview,
   and destructive confirmation controls are keyboard reachable and clearly
   distinguish reversible from permanent actions. Do not perform deletion in
   a shared environment.
5. At 390px, confirm `document.documentElement.scrollWidth <=
   document.documentElement.clientWidth` on Login, Today, Weekly Review, and
   Privacy. Check dialogs, tables/cards, nav, long URLs, and error messages for
   clipping or horizontal overflow.
6. Navigate the complete path with keyboard only. Confirm focus never
   disappears or becomes trapped, and returns sensibly after any dialog.
7. Record browser/version, viewport, commit, pass/fail, screenshots for visual
   failures, console errors, and the request ID for API failures.

Any owner-isolation error, inaccessible destructive action, invisible focus,
uncaught exception, or mobile horizontal overflow blocks release.
