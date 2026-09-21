"""Tirage au sort pondéré (aucune dépendance externe).

Chaque case (donneur -> receveur) a un poids : 0 = interdit, 1 = normal,
3 = probable, etc. Le tirage suit la loi « probabilité d'une répartition
proportionnelle au produit des poids de ses paires », parmi les répartitions
valides (personne ne s'offre un cadeau à soi-même, aucun poids nul, et
éventuellement aucun échange réciproque A <-> B).

Méthode : on trouve une répartition valide, puis on la « mélange » par
Metropolis-Hastings (échanges de receveurs entre 2 ou 3 donneurs). C'est
exact à la limite et très rapide pour quelques dizaines de personnes.
"""
import random
import time


def _matrix(ids, w):
    n = len(ids)
    return [[0.0 if i == j else float(w[(ids[i], ids[j])]) for j in range(n)] for i in range(n)]


def _blocked(n, ids, avoid_swaps, swap_ok):
    """blocked[i][j] = True si i -> j et j -> i ne doivent pas coexister."""
    ok = {(ids.index(a), ids.index(b)) for a, b in swap_ok}
    return [[avoid_swaps and (i, j) not in ok and (j, i) not in ok for j in range(n)]
            for i in range(n)]


def _initial(W, blocked, rng, max_tries=2000):
    """Trouve une répartition valide (liste : donneur i -> receveur s[i])."""
    n = len(W)
    allowed = [[j for j in range(n) if W[i][j] > 0] for i in range(n)]
    if any(not a for a in allowed):
        return None
    for _ in range(max_tries):
        order = list(range(n))
        rng.shuffle(order)
        order.sort(key=lambda i: len(allowed[i]))  # les plus contraints d'abord
        free = set(range(n))
        s = [-1] * n
        for i in order:
            cands = [j for j in allowed[i] if j in free and not (blocked[i][j] and s[j] == i)]
            if not cands:
                break
            j = rng.choice(cands)
            s[i] = j
            free.discard(j)
        else:
            return s
    return None


class _Chain:
    def __init__(self, W, s, blocked, rng):
        self.W, self.s, self.blocked, self.rng, self.n = W, s, blocked, rng, len(W)

    def step(self):
        n, s, W, rng = self.n, self.s, self.W, self.rng
        if n >= 3 and rng.random() < 0.3:
            i, j, k = rng.sample(range(n), 3)
            new = {i: s[j], j: s[k], k: s[i]}
        else:
            i = rng.randrange(n)
            j = rng.randrange(n - 1)
            if j >= i:
                j += 1
            new = {i: s[j], j: s[i]}
        old_w = new_w = 1.0
        for x, y in new.items():
            old_w *= W[x][s[x]]
            new_w *= W[x][y]
            if new_w == 0.0:
                return
        for x, y in new.items():
            if self.blocked[x][y] and new.get(y, s[y]) == x:
                return
        if new_w >= old_w or rng.random() * old_w < new_w:
            for x, y in new.items():
                s[x] = y


def _burn_in(n):
    return max(3000, 300 * n)


def draw(ids, w, avoid_swaps=True, rng=None, swap_ok=()):
    """Retourne {donneur: receveur} ou None si aucun tirage n'est possible.

    ids : liste d'identifiants ; w : {(donneur, receveur): poids}.
    swap_ok : paires (a, b) dont l'échange réciproque reste permis.
    """
    rng = rng or random.SystemRandom()
    W = _matrix(ids, w)
    blocked = _blocked(len(ids), ids, avoid_swaps, swap_ok)
    s = _initial(W, blocked, rng)
    if s is None:
        return None
    chain = _Chain(W, s, blocked, rng)
    for _ in range(_burn_in(len(ids))):
        chain.step()
    return {ids[i]: ids[s[i]] for i in range(len(ids))}


def simulate(ids, w, avoid_swaps=True, runs=2000, budget=3.0, swap_ok=()):
    """Estime la probabilité réelle de chaque paire.

    Retourne ({(donneur, receveur): nombre}, nb_tirages) ou None si impossible.
    """
    rng = random.Random()
    n = len(ids)
    W = _matrix(ids, w)
    blocked = _blocked(n, ids, avoid_swaps, swap_ok)
    s = _initial(W, blocked, rng)
    if s is None:
        return None
    chain = _Chain(W, s, blocked, rng)
    for _ in range(_burn_in(n)):
        chain.step()
    counts = {}
    done = 0
    start = time.monotonic()
    for _ in range(runs):
        for _ in range(2 * n):  # espacement entre deux échantillons
            chain.step()
        for i in range(n):
            key = (ids[i], ids[s[i]])
            counts[key] = counts.get(key, 0) + 1
        done += 1
        if time.monotonic() - start > budget:
            break
    return counts, done
