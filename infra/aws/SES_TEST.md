# Prepare one future SES delivery check

Author: Rishi Sheth. **Preparation only. No AWS operations, credit use or email sending is
authorized now.** An actual sender/recipient address has not been supplied in this work.

## Recipient configuration

Notifications are addressed to the database car owner's `User.email`. `SES_FROM_EMAIL` only
sets the sender. Private bootstrap otherwise uses placeholder owner addresses and creates no
subscriptions, so a successful model/replay check alone does not test email delivery.

Before the first bootstrap of a future empty database, prepare a private 0600 regular JSON
file, no larger than 4 KiB. For example, with non-deliverable example addresses:

```json
{
  "owner@autosound.dev": "verified-primary@example.invalid",
  "owner2@autosound.dev": "verified-secondary@example.invalid"
}
```

Supply only the owner(s) whose email should change; omitted owners retain their defaults.
The keys are logical bootstrap identities, not accounts to verify in SES. Replace example
values privately with the intended verified addresses only after that information is provided.
Do not commit the private file or show it in a recording.

The bootstrap command accepts `--owner-emails-file /private/owner-emails.json` alongside
`--initialize-empty-database` and the existing private credentials-file option. It validates
the file before database access, keeps actual SQL login addresses and the 0600 credentials
bundle aligned, and preserves `bootstrap_identity` so car ownership and historical mapping
remain correct. It changes no passwords/roles, creates no subscriptions and sends no email.
Invalid/duplicate/colliding addresses and unsafe files are rejected. Nonempty databases remain
protected; this is not an existing-user email migration tool.

## Stage privately inside the future API container

Only after the separate deployment/credit authorization and guarded setup, a local private
file can be passed through stdin so its values do not appear in command arguments or logs:

```bash
bash infra/aws/docker/compose.sh exec -T api python -c '
import os, sys
p = "/app/backend/.local_bootstrap/owner-emails.json"
fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(fd, "wb") as stream:
    stream.write(sys.stdin.buffer.read(4097))
' < /private/owner-emails.json
```

The API image's application user owns the private bootstrap directory. Exclusive creation
refuses an existing file; do not overwrite it blindly. Use its container path with the
bootstrap option, then restart the API as documented in the setup guide. Never copy the
private credential bundle into public evidence or run development seeding to set an address.

## Bounded procedure for a later authorized trial

1. Confirm the actual sender and intended recipient identities are verified in Oregon. Budget
   notification email verification is separate. Respect SES sandbox restrictions and keep the
   account on Free; stop for review if the required service is unavailable.
2. Configure `AWS_REGION=us-west-2`, the verified `SES_FROM_EMAIL` and the private owner email
   mapping. Actual AWS settings use `DEV_FAKE_EMAIL=false`; local verification keeps it true.
3. Run historical import and the five replay/other regression checks with email subscriptions
   absent, disabled or `in_app`. Historical import itself never invokes notification delivery.
4. In the owner's subscription UI, enable email for only the selected test car's Emergency
   category. Leave other categories' email disabled and check the alert type is enabled.
5. Trigger one dedicated fresh Siren event as IoT Staff. Record the resulting processed clip
   and alert, then confirm the intended recipient actually receives the message. Disable that
   email subscription before further scenarios, generic component tests or a second run.
6. If delivery is not confirmed, stop and inspect configuration/logs; do not repeatedly trigger
   messages. API/alert success is not proof of delivery. Keep addresses and message contents
   out of shared screenshots/logs; retain only sanitized status/timing evidence.

This is one intended test event, not an exactly-once delivery guarantee: SDK retries, worker
lease races or an ambiguous service response can affect delivery. The current runtime has no
hard mail-count or credit cap. No part of this procedure has been executed against AWS here.
