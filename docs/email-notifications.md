# Optional email notifications

Catchup can email a notification after its push worker has saved an explanation, remapped the repository to the explained commit, and atomically completed the job. Delivery runs in a separate background loop; provider errors are retried from a database outbox and never change the push job's completed state.

## Provider setup

Catchup uses the Resend email HTTP API through the existing `httpx` dependency. Create a Resend account, create an API key, and verify a sending domain/address in Resend. Resend currently lists a free plan with monthly and daily limits on its [pricing page](https://resend.com/pricing); confirm the current limits and account terms before production use. Do not put provider credentials in the frontend.

Set these environment variables on the **backend** (locally in `.env`, or in Render's service environment):

| Variable | Required | Description |
| --- | --- | --- |
| `EMAIL_NOTIFICATIONS_ENABLED` | Yes to enable | `true` enables notifications; defaults to `false`. |
| `EMAIL_API_KEY` | Yes to send | Resend API key. |
| `EMAIL_FROM` | Yes to send | Sender identity on a domain verified with Resend, e.g. `Catchup <notifications@example.com>`. |
| `EMAIL_TO` | Yes to send | Recipient email address. |
| `APP_BASE_URL` | Optional | Catchup frontend base URL. When set to an HTTP(S) URL, email includes a workspace link. |

If notifications are disabled or any sending credential is missing, push processing continues normally and no email is queued. The email contains the repository, default branch when available, both commit SHAs, the persisted one-line explanation, and a GitHub comparison URL.

## Database and deployment

The idempotent `CREATE TABLE IF NOT EXISTS push_email_notifications` statement lives in `backend/app/schema.sql`. Apply the updated schema once to an existing database before deploying the backend (the project's supported initializer is `python -m backend.app.db`). New installations receive the table during normal schema initialization. The outbox has one unique row per completed job and uses a stable provider idempotency key across retries; Resend documents that keys are retained for 24 hours in its [idempotency key guide](https://resend.com/docs/dashboard/emails/idempotency-keys).

No separate Render worker is required: the API lifespan starts the notification loop beside polling and push explanation processing. Keep the backend service running as a persistent web service for both loops to run.
