# Optional cleanup for legacy email-notification database objects

The current application no longer reads or writes email-notification data. The schema initializer no longer creates `push_email_notifications` or `repos.notification_email`, and it does not drop legacy objects.

If an existing database still has these objects, cleanup is optional. Do not run this against production without first inspecting the target database, taking a backup, and confirming the intended cleanup. These statements are intentionally separate from the application schema initializer:

```sql
DROP TABLE IF EXISTS push_email_notifications;
ALTER TABLE repos DROP COLUMN IF EXISTS notification_email;
```

Dropping the table also removes its table-local indexes, including `idx_push_email_notifications_pending` and the unique constraint on `job_id`. The operations do not use `CASCADE`; if another object depends on the outbox table, inspect that dependency and decide separately. The `repos` column is dropped only by its explicit statement. No application migration runs these commands.
