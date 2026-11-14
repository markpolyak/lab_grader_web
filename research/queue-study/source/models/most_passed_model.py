import numpy as np
from .base import QueueModel


class MostPassedQueueModel(QueueModel):
    name = "most_passed"
    description = (
        "Сначала те, кто сдал больше всего лаб "
        "(current_lab по убыванию), при равенстве — случайно"
    )

    def order_queue(self, state):
        active = state.active()
        if active.size == 0:
            return active

        # current_lab = число уже сданных лаб → для убывания берём минус
        keys = -state.current_lab[active]
        tiebreak = state.rng.random(active.size)
        order_idx = np.lexsort((tiebreak, keys))
        return active[order_idx]