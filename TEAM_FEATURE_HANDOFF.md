# Feature Handoff — Quick Setup and Test Guide

## What was added

- Advisor investment planning for gold, EGX30 ETF, and USD.
- Live, mock, or disabled pricing controlled by environment variables.
- Advisor plans can be saved as **Planned investments**.
- Planned investments are fully editable and do not affect portfolio totals.
- A planned item can be moved to **Owned** after entering the real purchase
  quantity, price, fees, and date.
- Owned investments can also be added and edited manually.
- Owned cards show latest value and estimated gain or loss.
- My investments page, dashboard summary, chat plan card, Arabic/English UI,
  mobile layout, and validation were added.
- Silent token refresh keeps active users and admins signed in, restores the
  session after reload, and handles concurrent requests/tabs.
- OCR/Kaggle was not changed.
- The NLI/Maestro design was reviewed only. No routing change was implemented;
  the NLI scope guard remains disabled by default.

## 1. Pull the correct branches

Pull the latest frontend and AI service changes from `main`, and the latest
backend changes from `dev`.

## 2. Create missing `.env` files

Create a `.env` from `.env.example` in the backend, frontend, AI service, and
`backend/deploy` folders only when that `.env` does not already exist. Do not
replace files containing working team secrets.

Set these values:

`nbe-financial-advisor-frontend/.env`

```env
VITE_API_BASE_URL=http://localhost:8000
```

`nbe-financial-advisor-backend/.env`

```env
DJANGO_CORS_ALLOWED_ORIGINS=http://localhost:5173,http://localhost:8080,http://127.0.0.1:5173
```

`nbe-financial-advisor-ai-service/.env`

```env
AI_SERVICE_CHAT_MODEL__USE_MOCK=0
AI_SERVICE_CHAT_MODEL__OPENAI_BASE_URL=https://api.openai.com/v1
AI_SERVICE_CHAT_MODEL__OPENAI_API_KEY=your-key
AI_SERVICE_CHAT_MODEL__MODEL_NAME=gpt-4o-mini
AI_SERVICE_MINERU__USE_MOCK=1
```

Use the team's approved model URL, key, and model name. Mock chat mode produces
`Mock response to: ...` and cannot test the real Advisor conversation.

`nbe-financial-advisor-backend/deploy/.env`

```env
MARKET_DATA_ENABLED=1
MARKET_DATA_PROVIDER=http
MARKET_DATA_BASE_URL=http://market-data-gateway:8004
MARKET_DATA_API_KEY=dev-market-data-key
SCOPE_GUARD_ENABLED=0
```

Pricing choices:

- Live development prices: `MARKET_DATA_ENABLED=1` and
  `MARKET_DATA_PROVIDER=http`
- Offline fixed prices: `MARKET_DATA_ENABLED=1` and
  `MARKET_DATA_PROVIDER=mock`
- No pricing: `MARKET_DATA_ENABLED=0`

Silent token refresh needs no new environment value.

## 3. Start Docker

```bash
cd nbe-financial-advisor-backend
docker compose -f deploy/docker-compose.dev.yml config -q
docker compose -f deploy/docker-compose.dev.yml up -d --build
docker compose -f deploy/docker-compose.dev.yml ps
```

Migrations and seeded users run automatically. If startup fails:

```bash
docker compose -f deploy/docker-compose.dev.yml logs --tail=100 \
  backend ai-service market-data-gateway frontend
```

Open `http://localhost:5173/en/sign-in`.

## 4. Seeded login

```text
Email: seed_user_4@example.com
Password: SeedPass123!
```

Other teammates can use `seed_user_2@example.com` or
`seed_user_3@example.com` with the same password.

Restarting the backend resets only the synthetic seeded users' plans and
holdings.

## 5. Test investment planning

1. Open **Advisor** and send:
   `Help me build an investment plan using my remaining money`.
2. Reply `about 1,200 EGP`.
3. Answer with `growth`, `moderate`, `medium`, and `medium`.
4. Confirm gold, EGX30 ETF, and USD are shown in priority order.
5. Choose naturally, for example `gold and egx`.
6. Confirm prices, allocations, quantities, and remaining cash are shown.
7. Press **Save as planned**.
8. Open **My investments**. Confirm the items are under Planned and do not
   affect Owned totals.
9. Edit a planned item's amount, quantity, and reference price.
10. Press **I bought this**, enter the actual purchase details, and move it to
    Owned.
11. Confirm the Owned card shows paid cost, latest value, and gain/loss.
12. Test **Add a purchase** and edit the new Owned item.
13. Return to Advisor and send `Show my spending breakdown this month` to
    confirm normal chat still works.

## 6. Test silent token refresh

1. Sign in, reload the dashboard, and confirm the session is restored.
2. Open two dashboard tabs and reload both; both should remain signed in.
3. Keep a visible tab open for over 30 minutes. In DevTools Network, confirm
   `POST /auth/refresh/` succeeds without an `Authorization` header or redirect.
4. Delete the `refresh_token` cookie and reload. The app should now require
   login.

Use `localhost` consistently. Mixing `localhost` and `127.0.0.1` can cause
cookie problems.

## Important production note

The bundled public price sources are for development. Product/compliance must
approve them or replace `MARKET_DATA_BASE_URL` with an approved internal
provider before enabling live pricing in production.
