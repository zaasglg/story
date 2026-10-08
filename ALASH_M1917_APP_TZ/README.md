# ALASH | M-1917 — Қызметкерлерді басқару жүйесі

Мақсат: iOS/Android worker app + Boss/Admin web panel.

Рөлдер:
- BOSS — толық бақылау; кірісті өзі енгізеді; шығын, кіріс, таза пайда, объект пайдасы және өз/компания банк шоттарын көреді.
- ADMIN — барлық операциялық деректі өзгертеді; барлық шығынды қосады/өзгертеді; күндік/айлық/объект шығынын көреді; компания кірісін, таза пайданы және Boss банк шотын көрмейді.
- MASTER — тек өз қол астындағы жұмысшыларын көреді; уақыт/сағат/м³ тек оқуға.
- WORKER — өз толық дерегі; басқа жұмысшылардың тек рұқсат етілген public profile дерегі.

Қатаң ереже:
- Рөлді қолданушы өзі таңдамайды.
- Server-side RBAC міндетті.
- Salary/bonus/penalty/advance/payment өзгерістері Admin енгізсе, BOSS APPROVAL қажет.
- Барлық өзгеріс Audit Log-та сақталады.
- Delete = soft delete/archive.
- Revenue-ді тек Boss енгізеді/өзгертеді.
- Expense-ті Admin толық CRUD жасайды.
- Net profit тек Boss-қа көрінеді.

Бастау реті: GEMINI_PROMPT.md -> ROLES.md -> FINANCE.md -> DATA_MODEL.sql -> SCREENS.md -> API.md -> TESTS.md.
