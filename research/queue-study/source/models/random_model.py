import numpy as np
from .base import QueueModel


class RandomQueueModel(QueueModel):
    name = "random"
    description = "Каждую неделю все активные студенты перемешиваются случайно"

    def order_queue(self, state):
        return state.rng.permutation(state.active())