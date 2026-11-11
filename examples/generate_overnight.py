"""Print the nine seeded schedules used in the overnight comparison."""
import random


def generate(case, size):
    rng = random.Random(970 + case)
    tokens = []
    for i in range(size):
        start = rng.choice([21, 22, 23]) if i == 0 else rng.randrange(16, 42)
        worker = rng.choice('xyz')
        room = rng.choice('uv')
        duration = rng.randrange(3, 9)
        tokens.append(f'{chr(65 + i)}{worker}{room}:{start}-{start + duration}')
    return tokens


if __name__ == '__main__':
    for case, size in enumerate([7] * 3 + [11] * 3 + [22] * 3, 1):
        print(case, *generate(case, size))
