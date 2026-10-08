# AUDIT + APPROVAL

Every add/edit/archive/approve/reject:
actor, timestamp, entity, entity_id, old_value, new_value, action.

Examples:
Admin changed Sergey m³ 18 -> 20.
Admin created bonus +5,000 RUB.
Boss approved bonus +5,000 RUB.
Admin moved worker Brigade 2 -> Brigade 3.

Soft delete only.

Approval states:
PENDING_BOSS
APPROVED
REJECTED
CANCELLED
