import * as Sentry from "@sentry/nextjs";

// Server-side: read from process.env (Vercel/production) with fallback
let SENTRY_DSN =
  process.env.NEXT_PUBLIC_LEARNHOUSE_SENTRY_DSN ||
  process.env.LEARNHOUSE_SENTRY_DSN;
let LEARNHOUSE_ENV =
  process.env.NEXT_PUBLIC_LEARNHOUSE_ENV ||
  process.env.LEARNHOUSE_ENV ||
  "dev";

if (SENTRY_DSN) {
  Sentry.init({
    dsn: SENTRY_DSN,
    environment: LEARNHOUSE_ENV,
    sendDefaultPii: true,
    enableLogs: true,
    tracesSampleRate: LEARNHOUSE_ENV === "dev" ? 1.0 : 0.1,
    beforeSend(event, hint) {
      const msg =
        (hint?.originalException as Error)?.message ??
        event?.exception?.values?.[0]?.value ??
        "";

      if (msg.includes("Failed to find Server Action")) return null;
      if (msg.includes("Organization not found")) return null;
      if (msg.includes("Organization has no config")) return null;
      // Next.js internal: raised when a client/bot sends a malformed
      // Next-Router-State-Tree header. Handled by Next (falls back to full
      // render) — not actionable from app code.
      if (msg.includes("router state header was sent but could not be parsed")) return null;

      return event;
    },
  });
}
