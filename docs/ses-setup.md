# AWS SES setup runbook

How to bring the email service up on Amazon SES: create the AWS resources, wire
the credentials into the app, verify a sending domain, and confirm bounce/
complaint ingestion. Do the steps in order.

The app has two independent backend selectors, both defaulting to Stalwart/SMTP:

| Setting | Stalwart value | SES value |
| --- | --- | --- |
| `MAIL_PROVIDER_BACKEND` (domain identity + DKIM) | `stalwart` | `ses` |
| `EMAIL_SEND_PROVIDER_BACKEND` (outbound delivery) | `smtp` | `ses` |

Set **both** to `ses` for a full cutover. They can be set by env var (below) or,
at runtime without a redeploy, in the superadmin **Mail provider settings**
screen (`MailProviderSettings` singleton), which overrides the env vars.

---

## 1. Create the IAM user (or role) and access key

The app needs SESv2 permissions for domain identities and sending. Create a
dedicated principal — do **not** reuse the S3 static-files key.

Minimal policy:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "AutomatorSES",
      "Effect": "Allow",
      "Action": [
        "ses:SendEmail",
        "ses:SendRawEmail",
        "ses:CreateEmailIdentity",
        "ses:TagResource",
        "ses:GetEmailIdentity",
        "ses:DeleteEmailIdentity",
        "ses:GetConfigurationSet",
        "ses:GetConfigurationSetEventDestinations"
      ],
      "Resource": "*"
    }
  ]
}
```

- On EC2/ECS, attach this as a **role** instead and skip the access key —
  boto3 picks up the instance/task role automatically.
- Otherwise create an **access key** for the user; you'll paste it in step 3.

> The app never rotates DKIM keys or edits configuration sets, so no
> `Put*`/`Update*` SES permissions are required at runtime. Steps 2 and 4 below
> use the AWS CLI with your own admin credentials.

---

## 2. Create the configuration set + SNS topic + event destinations

A configuration set carries the event destinations that forward bounce/
complaint/delivery notifications to SNS. Pick a name, e.g. `automator-primary`.

```bash
REGION=us-east-1
CONFIG_SET=automator-primary

# 2a. Configuration set
aws sesv2 create-configuration-set \
  --configuration-set-name "$CONFIG_SET" --region "$REGION"

# 2b. SNS topic
aws sns create-topic --name ses-bounces-complaints --region "$REGION"
# -> note the "TopicArn" in the output, e.g.
#    arn:aws:sns:us-east-1:123456789012:ses-bounces-complaints
TOPIC_ARN=arn:aws:sns:us-east-1:123456789012:ses-bounces-complaints

# 2c. Event destinations: bounce, complaint, delivery -> the SNS topic
for pair in "Bounce:BOUNCE" "Complaint:COMPLAINT" "Delivery:DELIVERY"; do
  name="${pair%%:*}Notification"; type="${pair##*:}"
  aws sesv2 create-configuration-set-event-destination \
    --configuration-set-name "$CONFIG_SET" \
    --event-destination-name "$name" \
    --event-destination "MatchingEventTypes=$type,Enabled=true,SnsDestination={TopicArn=$TOPIC_ARN}" \
    --region "$REGION"
done
```

Then allow SES to publish to the topic (SNS access policy) — the console prompts
for this automatically when you add the destination via the UI; via CLI, ensure
the topic policy allows `SNS:Publish` from `ses.amazonaws.com` for your account.

Validate anytime with the bundled helper (read-only; prints current state +
these instructions):

```bash
python manage.py setup_ses_reputation_monitoring \
  --configuration-set automator-primary --region us-east-1 --sns-topic-arn "$TOPIC_ARN"
```

---

## 3. Put the credentials into the app

### Local / `.env`

```dotenv
MAIL_PROVIDER_BACKEND=ses
EMAIL_SEND_PROVIDER_BACKEND=ses
AWS_ACCESS_KEY_ID=AKIA...
AWS_SECRET_ACCESS_KEY=...
AWS_REGION=us-east-1
SES_CONFIGURATION_SET=automator-primary
SES_SNS_TOPIC_ARN=arn:aws:sns:us-east-1:123456789012:ses-bounces-complaints
DEFAULT_FROM_EMAIL=no-reply@yourdomain.com
```

With `MAIL_PROVIDER_BACKEND=ses` or `EMAIL_SEND_PROVIDER_BACKEND=ses`, the
production startup guard (`automator/settings.py`, `if not DEBUG`) **requires**
`AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, and `AWS_REGION`; it warns if
`SES_SNS_TOPIC_ARN` is unset. The Stalwart/SMTP vars are then not needed.

### Production (GitHub Actions deploy)

Add these as **repository secrets** (Settings → Secrets and variables →
Actions). `.github/workflows/deploy.yml` already forwards them into the
server-side `.env`:

```
AWS_ACCESS_KEY_ID
AWS_SECRET_ACCESS_KEY
AWS_REGION
EMAIL_SEND_PROVIDER_BACKEND      # = ses
MAIL_PROVIDER_BACKEND            # = ses
SES_CONFIGURATION_SET            # = automator-primary
SES_SNS_TOPIC_ARN               # = arn:aws:sns:...:ses-bounces-complaints
```

### Or set at runtime (no redeploy)

Log in as a superadmin → **Mail provider settings**:
`infra_backend = ses`, `send_backend = ses`, `aws_region`,
`ses_configuration_set`, `ses_sns_topic_arn`. Credentials still come from the
`AWS_*` env vars / IAM role — only the region and resource names live in the DB.

---

## 4. Subscribe SNS to the webhook

```bash
aws sns subscribe --topic-arn "$TOPIC_ARN" \
  --protocol https \
  --notification-endpoint https://<YOUR_HOST>/email/webhooks/ses/ \
  --region "$REGION"
```

The endpoint (`apps/email/ses_webhooks.py`) auto-confirms the subscription, then
verifies every message's SNS signature and checks the `TopicArn` against
`SES_SNS_TOPIC_ARN` / `MailProviderSettings.ses_sns_topic_arn` before processing.
If the ARN doesn't match, messages are rejected — so step 3's ARN must be exact.

---

## 5. Verify a sending domain

1. Dashboard → **Domains** → add your domain (e.g. `mail.yourdomain.com`).
2. The domain card now shows **six** DNS records:
   - 1 × verification **TXT**
   - 3 × DKIM **CNAME** (`<token>._domainkey...` → `<token>.dkim.amazonses.com`)
   - 1 × SPF **TXT** (`v=spf1 include:amazonses.com ~all`)
   - 1 × DMARC **TXT** (`v=DMARC1; p=none; rua=...`)
3. Publish all of them at your DNS host. For the CNAMEs, disable any proxying
   (Cloudflare: set to *DNS only* / grey cloud).
4. Click **Check DNS**, or wait — `reverify_pending_domains` re-checks every
   pending domain every 15 minutes. The domain flips to **Verified** once
   ownership + all three DKIM CNAMEs resolve **and** SES reports
   `VerificationStatus = SUCCESS`.
5. Confirm on the AWS side: `aws sesv2 get-email-identity --email-identity mail.yourdomain.com`.

Sending is blocked until `status = VERIFIED` and `is_active = true`.

---

## 6. Leave the SES sandbox

A new SES account is sandboxed (only verified recipients, ~200 msg/day, 1 msg/s).
Before real traffic: **SES console → Account dashboard → Request production
access**, and request a sending-rate increase sized to your volume.

Then set the rate limiter to stay under your granted rate:

- `MailProviderSettings.ses_send_rate_limit` (default `14`) = **max sends/sec
  across all workers combined**.
- Set `REDIS_URL` (falls back to `REDIS_CACHE_URL`, then `CELERY_RESULT_BACKEND`)
  so the limiter is the shared Redis token bucket. Without a reachable Redis it
  degrades to a **per-process** bucket, so the real rate becomes
  `ses_send_rate_limit × worker count` — set the value to
  `granted_rate ÷ worker_count` in that case.

---

## 7. Smoke test

1. Send a transactional email from the verified domain via the API. Check the
   received headers: `Authentication-Results` should show **spf=pass**,
   **dkim=pass**, **dmarc=pass**.
2. Run a bulk campaign of ≥2 chunks — confirm the `campaigns` Celery queue worker
   picks it up (the compose `celery_worker` consumes
   `-Q celery,email,outbound,campaigns,webhooks`).
3. Send to `bounce@simulator.amazonses.com` and
   `complaint@simulator.amazonses.com` → the SNS webhook fires, a
   `SuppressionListEntry` is created, and further sends to that address are
   blocked. Re-deliver the same SNS message → no-op (DB idempotency via
   `ProcessedSnsMessage`).
4. Send to a normal address → the `Delivery` notification sets the
   `EmailMessage` to `DELIVERED`.

---

## 8. Reputation circuit breaker (tuning)

Per-account bounce/complaint rates are tracked in `SendReputation`; a halted
account's non-system sends are blocked (campaigns → `PAUSED`) until reset.
Thresholds live on `MailProviderSettings` (defaults inside SES's enforcement
lines):

| Field | Default | Meaning |
| --- | --- | --- |
| `reputation_bounce_warn` | `0.05` | flag account (warn) at 5% bounce |
| `reputation_bounce_halt` | `0.10` | block non-system sends at 10% bounce |
| `reputation_complaint_halt` | `0.005` | block at 0.5% complaints |
| `reputation_min_volume` | `100` | min sends in the window before the breaker acts |
| `reputation_window_hours` | `24` | trailing measurement window |

A halt fires a Slack alert (needs `SLACK_WEBHOOK_URL`). Reset via Django admin →
**Send reputations** → select rows → *Reset circuit breaker*. The
`alert_on_failure_spike` beat task separately pages Slack if platform-wide send
failures spike (≥20% of terminal sends in 60 min).

---

## Rollback

Set `MAIL_PROVIDER_BACKEND=stalwart` / `EMAIL_SEND_PROVIDER_BACKEND=smtp` (env or
`MailProviderSettings`) and redeploy/restart. Existing SES-verified domains keep
their `EmailDnsRecord` rows; Stalwart domains fall back to the legacy single-TXT
DKIM path. The cutover is reversible.
