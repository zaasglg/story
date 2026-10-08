# ACCEPTANCE TESTS

1. Unauthorized phone cannot receive OTP.
2. User cannot self-select role.
3. Master sees only assigned workers.
4. Master sees hours/m³ but cannot edit and cannot see money.
5. Admin can CRUD workers, brigades, objects, attendance, work-days, equipment and expenses.
6. Admin sees daily/monthly/object expenses.
7. Admin cannot access company revenue.
8. Admin cannot access net profit.
9. Admin cannot access Boss bank accounts.
10. Admin can see worker payroll bank accounts.
11. Admin salary/bonus/penalty/advance/payment changes create PENDING_BOSS.
12. Boss can approve/reject.
13. Revenue can be created/edited only by Boss.
14. Boss sees revenue + approved expenses + net profit.
15. Boss sees object-level profit.
16. Worker cannot see another worker's m³/salary/income/advance/penalty amount/passport/phone.
17. Worker can see public profile fields.
18. Pass shows QR, status, permissions and issued equipment.
19. Missing required work authorization => BLOCKED.
20. Red/absence history never disappears.
21. Old tariff does not recalculate old days.
22. Every critical mutation is audited.
