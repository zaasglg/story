# GEMINI-ГЕ НЕГІЗГІ PROMPT

Сен senior product architect, UX/UI designer және full-stack engineer ретінде ALASH | M-1917 жүйесін production-ready етіп жаса.

Құрастыр:
1. iOS + Android worker app.
2. Responsive Boss/Admin web panel.
3. Secure backend API.
4. PostgreSQL.
5. Phone + OTP authentication.
6. RBAC.
7. QR digital pass.
8. Work hours/m³/payroll.
9. Expenses.
10. Boss revenue + profit.
11. Boss approval workflow.
12. Audit Log.
13. Chat/notifications/support.
14. Equipment issue/return.
15. Rating/red history.

Ұсынылатын stack: React Native/Expo немесе Flutter; Next.js; NestJS/TypeScript; PostgreSQL; S3; FCM/APNs; WebSocket.

КРИТИКАЛЫҚ ҚҰҚЫҚТАР:
- Worker role-ды өзі таңдамайды.
- Master тек master_id арқылы өз worker-лерін көреді.
- Master уақыт/сағат/м³ көреді, өзгерте алмайды және қаржы көрмейді.
- Admin барлық worker/work/object/brigade/attendance/m³/expense дерегін CRUD жасайды.
- Admin барлық күндік, айлық және объект шығынын көреді.
- Admin company revenue, net profit, profit margin және Boss bank accounts көрмейді.
- Admin worker payroll bank account-тарын көреді.
- Admin salary/bonus/penalty/advance/payment өзгерісін енгізгенде PENDING_BOSS request жасалады.
- Boss approve/reject етеді. APPROVED болмайынша final payroll/ledger-ге кірмейді.
- Boss revenue-ді өзі енгізеді/өзгертеді.
- Boss толық кіріс + шығын + таза пайда + объект бойынша пайда + банк шоттарын көреді.
- Барлық өзгеріс actor/time/old/new/action арқылы Audit Log.
- Passport, phone, bank, salary, profit sensitive.
- Worker басқа worker-дің m³, salary, income, advance, penalty amount, passport, phone көрмейді.
- Worker public profile: photo, name, role, city, brigade, rating, shifts, hours, rating history, bonus history, pass.
- Required work authorization жоқ/мерзімі өткен болса pass BLOCKED және жұмысқа жіберілмейді.
- Pass: photo, name, role, city, brigade, ID, QR, status, permissions, issued equipment.
- Issued equipment: каска, жилет, аяқ киім, қолғап, белдік, көзілдірік, рулетка және т.б.
- Rating автоматты: start 5.0, min 0, max 5; absence -0.1; 14 consecutive days +0.1; no belt -2.0; no glasses -0.2; no helmet -0.9. Absence streak-ті reset етеді.
- Red/absence history жойылмайды.
- Historical tariff ескі күндерді қайта есептемейді.

UI: clean corporate, Polati-like simplicity, white cards + dark navy/gold brand, responsive, Kazakh-first.

Output: repo structure, migrations, API, screens, seed, tests, README, env.example, deploy guide.
