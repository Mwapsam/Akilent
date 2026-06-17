automator is a multi-tenant email-automation SaaS (Django 6, Postgres, Celery/Redis, Flutterwave billing, iRedMail provisioning, Tailwind + Alpine UI). The core product is feature-complete as an MVP: self-service signup → onboarding → domain/mailbox provisioning → per-plan quota enforcement → Flutterwave subscription billing → usage tracking + delivery logs/analytics. WhatsApp and Bitrix verticals exist but are soft-disabled behind feature flags and carry unfinished TODOs.

Goal of this work: close the gaps that block onboarding the first paying clients as an Email-SaaS-only v1 (WhatsApp/Bitrix stay flagged off). Infra is already hosted (server, iRedMail, DNS, TLS exist), so deliverables focus on application + payment + trust gaps, ordered strictly by hard launch blockers first.



**FIELD_ENCRYPTION_KEY** ***set or the app will raise an error on startup. Generate one with:***    
    `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`



curl -i -X POST https://api.progstack.org/api/login -H 'Content-Type: application/json' -d '{"username":"postmaster@progstack.org","password":"@Hello2061#"}'

## Frontend / static assets

The UI is server-rendered Django templates styled with **compiled Tailwind CSS**
(design tokens in `assets/app.css`) plus self-hosted **Alpine.js**. No Node/npm —
styling is built with the Tailwind **standalone CLI**.

One-time: download the CLI binary into `tools/` (gitignored):

```
# Windows x64 (adjust the asset name for macOS/Linux)
curl -L -o tools/tailwindcss.exe \
  https://github.com/tailwindlabs/tailwindcss/releases/latest/download/tailwindcss-windows-x64.exe
```

Build the stylesheet (re-run after changing templates or `assets/app.css`):

```
tools/tailwindcss.exe -i assets/app.css -o static/css/app.css --minify
# or: tools/tailwindcss.exe -i assets/app.css -o static/css/app.css --watch
```

The built `static/css/app.css` and vendored `static/js/*` and `static/fonts/*`
are committed, so the running app has styles without needing the CLI present.
In production, `python manage.py collectstatic` compresses + cache-busts them
(served by WhiteNoise).



These are Flutterwave test-mode cards (work only with a test secret key — FLWSECK_TEST-…). The two most-used ones:

Type	Number	CVV	Expiry	PIN	OTP
Mastercard (PIN → OTP)	5531 8866 5214 2950	564	09/32 (any future)	3310	12345
Visa (3DS redirect)	4187 4274 1556 4246	828	09/32	3310	12345
Other commonly used scenarios:

No-auth success: 5438 8980 1456 0229 — CVV 564, exp 10/31, PIN 3310, OTP 12345
Verve: 5061 4604 1012 0223 210 — CVV 780, exp 09/32, PIN 3310, OTP 12345
Insufficient funds (decline test): 5258 5859 2266 6506 — same CVV/exp/PIN/OTP pattern
Flow in the checkout: enter card → enter PIN 3310 → enter OTP 12345 → it redirects back to /billing/callback/.

A few things specific to your setup:

This only works when FLUTTERWAVE_SECRET_KEY is the test key (FLWSECK_TEST-…). With a live key these cards are rejected.
For the recurring flow you just built: the payment-plan enrollment + first charge use the same test cards. Auto-renewal charges are simulated by Flutterwave and arrive as charge.completed webhooks — to exercise that locally you'll need the webhook reachable (e.g. an ngrok tunnel pointed at /billing/webhook/ with FLUTTERWAVE_WEBHOOK_HASH set).
One caveat: I can't fetch live docs here, and Flutterwave rotates these test values occasionally. If any card is rejected, grab the current list from developer.flutterwave.com → "Test cards" or your dashboard's test-mode docs. Want me to add a short "Testing payments" section to the README with these cards and the ngrok/webhook steps?