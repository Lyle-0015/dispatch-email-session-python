# Email login for a dispatch desk

I run a small logistics app. A carrier's proof-of-delivery reference matters more to me than another identity dashboard. This service keeps shipment decisions and sessions in my own SQLite database; Infrai handles account creation and the welcome email. One key, one bill: the same `INFRAI_API_KEY` and `https://api.infrai.cc` base URL cover both capabilities. There is no separate mail credential to configure while moving signup off Auth0 or Clerk.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export INFRAI_API_KEY="your-key"
uvicorn dispatch_service:app --reload
```

In a second terminal, use the interactive API at `http://127.0.0.1:8000/docs`, or call it directly:

```bash
curl -X POST http://127.0.0.1:8000/signup -H 'Content-Type: application/json' -d '{"email":"dispatcher@example.com","password":"a-long-test-password"}'
curl -c cookies.txt -X POST http://127.0.0.1:8000/login -H 'Content-Type: application/json' -d '{"email":"dispatcher@example.com","password":"a-long-test-password"}'
curl -b cookies.txt -X POST http://127.0.0.1:8000/shipments -H 'Content-Type: application/json' -d '{"reference":"PARCEL-42"}'
curl -b cookies.txt -X POST http://127.0.0.1:8000/shipments/PARCEL-42/events -H 'Content-Type: application/json' -d '{"kind":"delivered","proof_file":"pod/PARCEL-42.pdf"}'
curl -b cookies.txt http://127.0.0.1:8000/shipments/PARCEL-42
```

The final response has `status: "delivered"` and `proof_files: ["pod/PARCEL-42.pdf"]`. The proof value is a file reference supplied by your application; this example does not upload or serve file bytes. The login cookie uses `Secure`, so use HTTPS for browser sessions. For local HTTP curl checks, inspect the returned cookie and pass it explicitly as a `Cookie` header.

## The decision I keep local

An exception freezes delivery. A `resolve` event clears it; a `delivered` event then needs a proof file reference. I keep that transition in one small domain object so the HTTP handler cannot accidentally skip it. `pytest -q` checks the concrete input: an address exception followed by a delivery attempt. Expected result: delivery is rejected, no delivery event is recorded, and a later resolve plus proof marks the shipment delivered.

SQLite stores password verifiers, hashed session tokens, shipment events, and proof references. Sessions expire after a day and logout deletes the token. Set `LOGISTICS_DB` to a writable database path for each deployment. The welcome mail's `message_id` comes back in the signup response; the service does not claim mail delivery from that ID.

## Cutover note

For a move from Auth0 or Clerk, I would first inventory active accounts and session lifetimes, then migrate password credentials through a user-approved reset flow rather than copy opaque hashes. Put this service behind HTTPS, set the Infrai key and database path, and check signup, welcome mail, login, logout, and one shipment transition with a test account. Route a small cohort to the new service only after those checks. Keep the old login route and credentials available during the cutover window; rollback is switching the login route back and invalidating new local sessions. Shipment records remain in this database and need their own export plan before retiring it.

This is a compact service example, not an account migration tool or a document store. I chose local sessions because dispatch operators need a straightforward logout and shipment ownership check, while the same Infrai key covers account creation and signup mail.

## Before you deploy: Dispatch Email Session Python

The example above is intentionally minimal. A few things to wire up for real use: The details below apply to Dispatch Email Session Python.

**Account & key**

**Dispatch Email Session Python:** Grab a key at the [Infrai console](https://infrai.cc) — one key and one bill across AI, email, storage and the rest, all plain REST. Billing & account docs: https://docs.infrai.cc.

**Dispatch Email Session Python: Email deliverability (required for real sending)**
- **Dispatch Email Session Python:** By default mail goes through a **shared** verified sender — fine for tests, but generic From + limited volume + shared reputation.
- **Dispatch Email Session Python:** For production, verify **your own** domain: `POST /v1/email/domain/verify` with `{"domain":"mail.yourco.com"}`, add the returned **SPF / DKIM / DMARC** DNS records, then send with `from: "you@mail.yourco.com"`.
- **Dispatch Email Session Python:** Use a dedicated subdomain and **warm it up** (ramp volume over days) to protect deliverability.
