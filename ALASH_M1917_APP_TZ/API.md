# API / BACKEND CONTRACT

AUTH
POST /auth/request-otp
POST /auth/verify-otp
GET /me
POST /auth/logout

WORKER
GET /workers/me
GET /workers/public
GET /workers/:id/public
GET /workers/me/work-days
GET /workers/me/rating
GET /workers/me/pass
GET /workers/me/equipment
GET /workers/me/bank-account
POST /workers/me/change-request

MASTER
GET /master/team
GET /master/team/:workerId
GET /master/team/:workerId/work-days
GET /master/brigade
GET /master/objects

ADMIN
CRUD /admin/workers
CRUD /admin/brigades
CRUD /admin/objects
CRUD /admin/work-days
CRUD /admin/equipment
CRUD /admin/equipment/issues
CRUD /admin/expenses
GET /admin/expenses/daily
GET /admin/expenses/monthly
GET /admin/expenses/object/:objectId
GET /admin/payroll
POST /admin/financial-requests
GET /admin/financial-requests
GET /admin/audit-logs

BOSS
GET /boss/dashboard
CRUD /boss/revenues
GET /boss/finance
GET /boss/finance/object/:objectId
GET /boss/profit
GET /boss/bank-accounts
GET /boss/audit-logs
GET /boss/pending-approvals
POST /boss/approvals/:id/approve
POST /boss/approvals/:id/reject

Critical:
GET /boss/profit => BOSS only
GET /boss/bank-accounts => BOSS only
GET /admin/expenses/monthly => ADMIN/BOSS
ADMIN revenue/profit access => 403
POST /admin/financial-requests => ADMIN
POST /boss/approvals/:id/approve => BOSS only
