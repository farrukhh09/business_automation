"""Follow-up questions (03-business-rules.md §6a, 06-integrations.md §5).

| task | when |
|---|---|
| ``send_follow_ups()`` | beat every 15 min: ask again in the dialogs that are waiting for the customer |

Every 15 minutes rather than on a timer per dialog: the delay is a setting the owner can change at
any moment (``follow_up_after_hours``), and a pass that simply looks at the clock always follows it.
The pass itself is idempotent — a stage already asked about is remembered in the dialog state.
"""

from app.core.database import session_scope
from app.services.follow_up_service import run_follow_ups
from app.tasks.celery_app import celery_app


@celery_app.task(name="app.tasks.follow_up.send_follow_ups")
def send_follow_ups() -> int:
    """Returns how many follow-ups were sent (for the task result log)."""
    with session_scope() as db:
        return len(run_follow_ups(db).sent)
