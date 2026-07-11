# API Endpoints
**AI-Powered Personal Financial Advisor — Graduation Project (NBE)**

High-level, self-documenting list of routes by domain. Each URL states only its allowed methods — no request/response shapes here (see Data Shapes documents for those) and no implementation detail. Read this as the map of what exists and where; it should be enough on its own to know what a route is for and which controller/view needs to exist, without needing to know how it's built.

Unless marked **[admin]**, every route is scoped to the authenticated user automatically (see API Design Guidelines §6) — there is no `user_id` in any of these paths.

---

## 1. Auth & Onboarding

```
POST   /auth/signup
POST   /auth/login
POST   /auth/logout
POST   /auth/refresh
```

## 2. Profile & Preferences

```
GET    /users/me
PATCH  /users/me
GET    /users/me/preferences
PATCH  /users/me/preferences
POST   /users/me/consent
DELETE /users/me/consent/{consent_id}
DELETE /users/me                          # full account + data deletion (Functional Req. #23)
```

## 3. Bank Accounts

```
GET    /accounts
POST   /accounts
PATCH  /accounts/{account_id}
DELETE /accounts/{account_id}
```

## 4. Statements & Document Ingestion

```
POST   /statements                        # upload (multipart) — stores the file, auto-chains extraction/normalization
GET    /statements
GET    /statements/{statement_id}         # includes the proposed transactions inline once normalized
PATCH  /statements/{statement_id}         # retry/resume a stuck extraction or normalization phase
DELETE /statements/{statement_id}
GET    /statements/{statement_id}/ocr-result
GET    /statements/{statement_id}/ocr-result/download   # proxies document.md through Django, not a signed SeaweedFS URL
POST   /statements/{statement_id}/transactions   # approve the full proposed batch, commit to the ledger
```

## 5. Transactions

```
GET    /transactions                      # filters: account_id, category, from, to; offset-paginated
GET    /transactions/{transaction_id}
POST   /transactions                      # manual entry — subject to duplicate check
PATCH  /transactions/{transaction_id}
DELETE /transactions/{transaction_id}
```

## 6. Budget (the single active plan — `budgets`) and Goal (its own entity)

```
GET    /budget                            # current active plan + allocations (no goal — see /goal below)
POST   /budget                            # create the initial plan (onboarding step 5)
PATCH  /budget                            # update allocations (dashboard edit or chat HITL confirm)
GET    /budget/history                    # budget_history — versioned prior states
GET    /budget/progress                   # actual vs. allocated, current period, per category
GET    /budget/savings-progress           # progress toward goal + projected completion date (requires a Goal, not a Budget)
GET    /budget/starter-templates          # 3–5 onboarding-step starter templates (one flagged suggested)
GET    /goal                              # the user's savings goal — its own entity, one-to-one with User
POST   /goal                              # create it (409 if one already exists)
PATCH  /goal                              # update it (any subset)
DELETE /goal                              # remove it — back to "no goal"
```

## 7. Dashboard

```
GET    /dashboard                         # aggregate: plan, goal, metrics, net worth (see API Design Guidelines §7)
PATCH  /dashboard/goal                    # convenience upsert alias for the standalone Goal entity
```

## 8. Analytics (read-only, backend-computed)

```
GET    /analytics/monthly-summaries
GET    /analytics/category-breakdown
GET    /analytics/recurring-charges
GET    /analytics/anomalies
PATCH  /analytics/anomalies/{anomaly_id}  # mark resolved/dismissed
GET    /analytics/spending-insights
GET    /analytics/net-worth
GET    /analytics/stability-score
```

## 9. AI Assistant (Conversations)

```
POST   /chat/conversations
GET    /chat/conversations
GET    /chat/conversations/{conversation_id}/messages
POST   /chat/conversations/{conversation_id}/messages   # triggers assistant reply (streamed)
POST   /chat/conversations/{conversation_id}/attachments # doc upload shortcut, reuses Statements pipeline
DELETE /chat/conversations/{conversation_id}
```

## 10. Recommendations

```
GET    /recommendations
POST   /recommendations/{recommendation_id}/feedback
```

## 11. Feedback & Support

```
POST   /feedback
POST   /issues
GET    /issues
```

## 12. Administration **[admin]**

Separate credential space from end-user auth (see API Design Guidelines §8). Not implicitly self-scoped — operates across users by design.

```
POST   /admin/auth/login
GET    /admin/feedback                    # all users' reactions
GET    /admin/issues                      # all users' reported issues
PATCH  /admin/issues/{issue_id}           # update status
GET    /admin/products
POST   /admin/products
PATCH  /admin/products/{product_id}
DELETE /admin/products/{product_id}
```

---

## 13. Internal AI Service Endpoints (Django → AI service only — never public, never called by the frontend)

Authenticated via the shared service-to-service token, not user JWTs. Listed here only so the full route surface of the system is visible in one place; see Services and Background Tasks document for trigger conditions and payload notes.

```
POST   /internal/normalize
POST   /internal/embed
POST   /internal/analyze/post-ingestion
POST   /internal/analyze/monthly-summary
POST   /internal/analyze/anomaly-check
POST   /internal/chat                     # SSE stream, proxied by Django
POST   /internal/plan/question
POST   /internal/plan/generate
POST   /internal/recommendations/match
```
