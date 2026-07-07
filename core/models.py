from django.db import models


class Ping(models.Model):
    """A trivial row we create to prove the database persists data across restarts."""
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Ping {self.id} @ {self.created_at:%Y-%m-%d %H:%M:%S}"
