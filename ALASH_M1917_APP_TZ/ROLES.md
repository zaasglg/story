# ROLE MATRIX

| Құқық | Worker | Master | Admin | Boss |
|---|---|---|---|---|
| Өз профилі | view | view | view | view |
| Басқа worker public profile | view | view | view | view |
| Passport | own | - | yes | yes |
| Worker phone | own | direct reports қажет болса | yes | yes |
| Worker bank account | own | - | yes | yes |
| Hours/m³ | own | direct reports view | CRUD | view |
| Brigade/Object assignment | own view | view | CRUD | view |
| Expenses | - | - | CRUD | view/CRUD |
| Company revenue | - | - | FORBIDDEN | full |
| Net profit | - | - | FORBIDDEN | full |
| Object profit | - | - | FORBIDDEN | full |
| Boss bank accounts | - | - | FORBIDDEN | full |
| Salary/bonus/penalty/payment | own | - | request -> Boss approval | approve/edit |
| Audit log | limited | - | admin scope | all |
| General announcements | receive | receive | create | create |
| Chat | yes | yes | yes | yes |

Admin finance masking backend response деңгейінде жасалсын; тек UI-да жасыру жеткіліксіз.
