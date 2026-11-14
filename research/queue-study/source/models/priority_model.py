import numpy as np
from .base import QueueModel


class PriorityQueueModel(QueueModel):
    name = "priority"
    description = ("Очередь по приоритету: priority = deadline_текущей_лабы − week; "
                   "сортировка по возрастанию (просроченные — первыми, "
                   "ранние — в конце)")

    def order_queue(self, state):
        active = state.active()
        if active.size == 0:
            return active

        # priority < 0  → просрочка на столько недель
        # priority = 0  → ровно дедлайн
        # priority > 0  → сдаёт заранее на столько недель
        current_deadlines = state.deadlines[state.current_lab[active]]
        priorities = current_deadlines - state.week

        # сортируем по возрастанию; равные приоритеты разрешаем случайно
        tiebreak = state.rng.random(active.size)
        order_idx = np.lexsort((tiebreak, priorities))
        return active[order_idx]