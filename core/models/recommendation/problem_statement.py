import uuid

from django.db import models
from pgvector.django import HnswIndex, VectorField


class ProblemStatement(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    product = models.ForeignKey(
        "Product",
        on_delete=models.CASCADE,
        related_name="problem_statements",
    )
    statement_text = models.TextField()
    # pgvector configuration matching 1024-dimension embedding strategy
    embedding = VectorField(dimensions=1024, blank=True, null=True)

    class Meta:
        db_table = "problem_statements"
        indexes = [
            HnswIndex(
                name="idx_problem_embedding",
                fields=["embedding"],
                opclasses=["vector_cosine_ops"],
            )
        ]

    def __str__(self):
        return f"Statement for {self.product.title} ({self.id[:8]})"
