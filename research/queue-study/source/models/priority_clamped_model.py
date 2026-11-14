import numpy as np
from .base import QueueModel

# Нижняя граница объединяет все значения, меньшие или равные ей.
# При -1 любые просроченные работы образуют один класс.
MIN_PRIORITY = -1


class PriorityClampedQueueModel(QueueModel):
    name = "priority_clamped"
    min_priority = MIN_PRIORITY
    description = (
        f"priority = max({MIN_PRIORITY}, deadline текущей работы - week); "
        "при равенстве приоритетов порядок случайный"
    )

    def order_queue(self, state):
        active = state.active()
        priorities = state.deadlines[state.current_lab[active]] - state.week
        priorities = np.maximum(priorities, self.min_priority)
        ties = state.rng.random(active.size)
        return active[np.lexsort((ties, priorities))]


class PriorityClampedZeroQueueModel(PriorityClampedQueueModel):
    name = "priority_clamped_zero"
    min_priority = 0
    description = (
        "priority = max(0, deadline текущей работы - week); "
        "текущие и просроченные работы равны, порядок внутри класса случайный"
    )
