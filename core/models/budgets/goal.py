import uuid

from django.db import models


class Goal(models.Model):
    """
    A user's single savings goal — its own entity, one-to-one with User,
    not nested under Budget (PLAN.md Checkpoint C). Previously these fields
    (savings_goal_name/goal_target_amount/goal_timeline_months) lived
    directly on Budget; extracting them fixes a real bug along the way: goal
    progress was tracked "since budget.created_at," which is always "now" at
    plan-creation time and never updates when the goal itself is later added
    or changed — Goal.created_at gives progress tracking the right reference
    point (see _goal_progress()/_saved_so_far() in core/views/budgets.py).
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.OneToOneField("User", on_delete=models.CASCADE, related_name="goal")
    name = models.CharField(max_length=255)
    target_amount = models.DecimalField(max_digits=14, decimal_places=2)
    timeline_months = models.IntegerField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "goals"

    def __str__(self):
        return f"Goal for User {self.user_id}: {self.name}"
