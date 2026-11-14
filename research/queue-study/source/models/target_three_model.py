import numpy as np
from common import _compute_scores
from .base import QueueModel


class TargetThreeQueueModel(QueueModel):
    name = "target_three"
    description = "Довести до 25 баллов: ближе к порогу идут раньше; достигшие порога больше не обслуживаются"

    def order_queue(self, state):
        scores = _compute_scores(state.completion_week)
        active = state.active()
        active = active[scores[active] < 25]
        ties = state.rng.random(active.size)
        return active[np.lexsort((ties, 25 - scores[active]))]
