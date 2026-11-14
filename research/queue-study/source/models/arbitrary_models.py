import numpy as np
from .base import QueueModel


class SurnameKQueueModel(QueueModel):
    name = "surname_k"
    description = "Сначала условные фамилии на К: синтетические студенты с индексами 0, 10, 20; внутри класса случайно"

    def order_queue(self, state):
        active = state.active()
        preferred = active % 10 == 0
        return active[np.lexsort((state.rng.random(active.size), ~preferred))]


class OddNumberQueueModel(QueueModel):
    name = "odd_number"
    description = "Сначала нечётные номера студентов (нумерация с 1); внутри класса случайно"

    def order_queue(self, state):
        active = state.active()
        preferred = (active + 1) % 2 == 1
        return active[np.lexsort((state.rng.random(active.size), ~preferred))]
