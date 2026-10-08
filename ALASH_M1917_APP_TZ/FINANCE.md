# ҚАРЖЫ ЖҮЙЕСІ

## Revenue
Тек BOSS:
- date
- city
- object
- amount
- currency
- category
- comment
- attachment

Мысал:
Липецк / Нысан №1 / Қыркүйек
Кіріс = 350 000 RUB.

## Expenses
ADMIN толық басқарады:
- күндік
- айлық
- объект бойынша
- қала бойынша
- категория бойынша

Категория: salary, bonus, material, transport, food, accommodation, equipment, fuel, other.

Мысал:
Материал 80 000
Транспорт 35 000
Тамақ 30 000
Тұру 45 000
Жабдық 30 000
Барлығы 220 000 RUB.

Admin 220 000 RUB шығынды көреді.
Admin 350 000 revenue және 130 000 net profit көрмейді.

## Profit
BOSS ONLY:
net_profit = approved_revenue - approved_expenses

Object profit:
object_net_profit = approved_object_revenue - approved_object_expenses

## Payroll
daily_earnings = m3 * historical_rate
monthly_pay = sum(approved_daily_earnings) + approved_bonus - approved_penalty
remaining = monthly_pay - approved_advance - approved_payment

## Approval
Admin salary/bonus/penalty/advance/payment өзгертсе:
PENDING_BOSS -> APPROVED немесе REJECTED.
Тек APPROVED болғаны final ledger-ге кіреді.

## Bank
Worker payroll bank account: Admin + Boss.
Boss/company bank accounts: Boss only.
Never expose Boss bank records to Admin API.
