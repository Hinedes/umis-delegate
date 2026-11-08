# UMIS-D

A clinic system (patients, visits, billing, shifts, stock) with an AI
assistant built in. The assistant can only do what the signed-in user is
allowed to do, and only through a fixed list of commands.

Django 5, PostgreSQL 18, htmx. Runs on a Vultr VM; the model runs on Vultr
Serverless Inference.

## What it's for

A small clinic's front desk spends its day on the same chains of clicks:
find the patient, register them if they're new, book a slot with the right
doctor, add the services, take the payment. Each step lives on a different
page.

With UMIS-D the receptionist types "register Julia Orlova, Thursday 4pm with
Dr. Smirnov, consultation, paid in cash" and the assistant does all of it:
searches for her, creates the record, checks the doctor's free slots, books
the visit, adds the service, records the payment. One message, several
commands.
The visit shows up in the registry like any other.

What it won't do is step outside the receptionist's job. It can't verify a
payment, because a receptionist can't. It can't cancel a visit without a
person approving it. Everything it did is in the audit log with the
receptionist's name on it.

## How it runs on Vultr

```
 browser ──HTTPS──▶ Caddy ──▶ Django + Gunicorn ──▶ PostgreSQL 18
                     │              │
                     │              └──HTTPS──▶ Vultr Serverless Inference
                     │                          (DeepSeek, tool calling)
                     └── Let's Encrypt certificate

 all three containers: Docker Compose on one Vultr Cloud Compute VM
```

**Vultr Cloud Compute.** One VM (2 vCPU, New Jersey) runs the whole
stack with Docker Compose: Caddy for HTTPS, the Django app on Gunicorn, and
PostgreSQL with its data on a Docker volume. The VM is the system of record:
patients, visits, payments, agent runs and the audit log all live in its
database.

**Vultr Serverless Inference.** The assistant talks to
`https://api.vultrinference.com/v1/chat/completions`, the OpenAI-compatible
endpoint, using `deepseek-v4.1-flash-1017`. The app sends the conversation
plus the assistant's commands as tools. The model
answers with tool calls or with a final reply. Reasoning effort (off, low,
high, max) is set from the policy page and sent with each request. No model
runs on the VM itself.

### What happens to one message

1. The chat widget posts the message. The app starts an agent run for that
   user and builds the prompt: the user's roles, which commands they can run,
   the clinic's doctors and services, and any instructions from the policy.
2. The model is called on Vultr. If it asks to run a command, the request
   goes to the gateway, never straight to the database.
3. The gateway checks, in order: is the assistant paused, is the command
   switched off, does the user have the permission, are the arguments valid,
   is this user allowed to touch this particular visit or patient. Any "no"
   stops the step and is logged as denied.
4. Cancelling a visit and verifying a payment don't run yet. They create an
   approval tied to the exact arguments, which a person approves or rejects
   on the run page. It expires after a few minutes.
5. Allowed commands run inside a database transaction. Commands that
   change an existing visit or payment lock its row first, so the assistant and a person
   clicking the same button can't both win.
6. The result goes back to the model, which can call another command (up to
   the step limit) or answer. Every step, allowed or denied, lands in the
   audit log.

The model never sees SQL, a shell, or HTTP. It only sees the 11 commands
below (minus any switched off). The prompt tells it which ones this user
may run, and the gateway refuses the rest anyway.

## The assistant's commands

Defined in `control/commands.py`:

```
query.patient.search       mutate.patient.create
query.visit.get            mutate.visit.request
query.visit.availability   mutate.visit.add_service
query.finance.period       mutate.visit.confirm
                           mutate.visit.cancel        (needs approval)
                           mutate.payment.record
                           mutate.payment.verify      (needs approval)
```

On `/control/`, a superuser can pause the assistant, set its reasoning effort,
add instructions, cap steps per run, set the approval window, and switch
commands off or require approval for them. These settings only make it
stricter. Every change is audited.

## Run it

```sh
cp .env.example .env
```

Set `SECRET_KEY` and `POSTGRES_PASSWORD` in `.env`. Over plain HTTP, also set
`SESSION_COOKIE_SECURE=False` and `CSRF_COOKIE_SECURE=False`, or the login
form will reject you. Then:

```sh
docker compose up --build
docker compose exec web python manage.py seed_demo
```

Open http://localhost:8000 and sign in as `admin`, `dr_smirnov`, `dr_kim`,
`nurse1` or `reception1`. They all share one password: `DEMO_PASSWORD` from
`.env`, or a random one that `seed_demo` prints if you left it blank.

To use the assistant, put your Vultr key in
`VULTR_SERVERLESS_INFERENCE_API_KEY`. Without it the app works and the
assistant just says it isn't configured.

## Deploying on a Vultr VM

1. Create a Cloud Compute instance (Ubuntu, 2 vCPU is enough) and install
   Docker.
2. Clone this repo, fill in `.env` as above, and keep the cookie settings at
   `True`.
3. Put Caddy in front: add a `caddy:2` service to the Compose file with
   ports 80 and 443, and drop the `8000:8000` port from `web`. This
   `Caddyfile` gets a certificate on its own; without a domain,
   `<ip-with-dashes>.sslip.io` works:

   ```
   203-0-113-10.sslip.io {
       reverse_proxy web:8000
   }
   ```

4. Set `ALLOWED_HOSTS` and `CSRF_TRUSTED_ORIGINS` to that hostname,
   `SECURE_SSL_REDIRECT=True`, and start the stack.

`TRUSTED_PROXY_COUNT` (default 1) is how many proxies sit in front; it's used
to find the real client IP for the audit log. The other settings are in
`.env.example`.

## Roles

`seed_demo` creates six groups: Reception, Doctor, Nurse, Operations,
Read-only and Admin. The code checks permissions, not group names, so you can
change what a group can do in Django admin. A doctor without the view-all
permission only sees their own patients and visits. The assistant follows the
same rules, because it calls the same permission checks.

## Other commands

```sh
python manage.py seed_groups              # roles only, no demo data
python manage.py seed_payment_methods     # cash, card, transfer
python manage.py seed_expense_categories
python manage.py seed_locations           # one stock location per floor
python manage.py update_visit_statuses    # overdue planned visits -> waiting
python manage.py seed_demo --reset        # needs DEMO_ALLOW_RESET=True
```

## License

MIT
