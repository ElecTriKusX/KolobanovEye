"""Online quota planner reference, independent of Dagor and disk writes.

The caller owns the renderer, validation and durable journal. Exactly one
request may be in flight; do not treat a provisional candidate as published.
No proxy score is ever used for quota credit. The final maximum-flow selection
may exchange previous candidates, so retain the useful alternative cells.
"""
from collections import Counter

from tools.ke_visibility import VISIBILITY_BINS
from tools.ke_visibility_quotas import choose_candidates, solve_capacity


class AdaptiveQuotaPlanner:
    """Bounded feedback scheduler for global visibility and exact map totals."""

    def __init__(self, targets, map_targets, attempt_budget_by_map):
        self.targets = dict(targets)
        self.map_targets = dict(map_targets)
        self.budget = dict(attempt_budget_by_map)
        if set(self.budget) != set(self.map_targets) or any(
                type(n) is not int or n < 0 for n in self.budget.values()):
            raise ValueError("Attempt budgets must be nonnegative integers for all planned maps")
        self.attempts = Counter()
        self.outcomes = Counter()
        self.cells = {m: {b: [] for b in VISIBILITY_BINS} for m in self.map_targets}
        self.capacity = {m: dict.fromkeys(VISIBILITY_BINS, 0) for m in self.map_targets}
        self.available = Counter()
        self.retained = 0
        self.ids = set()
        self.pending = None
        self.last_map = None
        _, self.report = choose_candidates([], self.targets, self.map_targets)

    @property
    def selected(self):
        # Materialize IDs only when requested, not on every feedback event.
        return {image_id for mid, bins in self.report["selected_by_map_and_bin"].items()
                for b, count in bins.items() for image_id in self.cells[mid][b][:count]}

    def _probability(self, mid, requested, helpful):
        # Smoothed empirical confusion matrix. A requested category starts
        # with a weak optimistic prior; retain exploration after proxy misses.
        outcomes = (*VISIBILITY_BINS, "rejected")
        def weight(b):
            return (1 + 4 * int(b == requested)
                    + self.outcomes[mid, requested, b])
        return sum(weight(b) for b in helpful) / sum(weight(b) for b in outcomes)

    def next_request(self, *, current_map=None, prefer_current=False):
        """Return (map_id, requested_bin), or None at completion/budget limit.

        Prefer the loaded map if it is within 15% of the best estimated gain,
        reducing reload churn. The native caller can group work by map.
        Feedback is consumed between requests, not in fixed large batches.
        A caller may temporarily prefer a useful resident map even below
        the normal 85% score threshold; never override capacity or budget.
        """
        if self.pending is not None:
            raise RuntimeError("Finish the current request first")
        if self.report["complete"]:
            return None
        maps = [m for m in sorted(self.budget) if self.attempts[m] < self.budget[m]
                and self.map_targets[m]]
        if not maps:
            return None
        helpful = self.report["useful_next_bins_by_map"]
        eligible = [m for m in maps if helpful[m]]
        # Some new edges only become useful after an additional exchange.
        # Bounded exploration is preferable to declaring a false impossibility.
        exploring = not eligible
        choices = []
        for mid in (eligible or maps):
            bins = helpful[mid] if not exploring else [b for b in VISIBILITY_BINS if self.targets[b]]
            for b in bins:
                # An observed miss can still be useful if it belongs to another
                # augmenting bin on this map. The smoothed probability captures it.
                score = self._probability(mid, b, bins)
                relative = self.report["deficit"][b] / self.targets[b]
                remaining = self.report["deficit_by_map"][mid] / self.map_targets[mid]
                choices.append((score, relative, remaining, -self.attempts[mid],
                                mid, b))
        if not choices:
            return None
        def rank(x):
            return (-x[0], -x[1], -x[2], -x[3], x[4], VISIBILITY_BINS.index(x[5]))
        best = min(choices, key=rank)
        loaded = current_map if current_map is not None else self.last_map
        local = [x for x in choices if x[4] == loaded and
                 ((prefer_current and not exploring) or x[0] >= .85 * best[0])]
        if local:
            best = min(local, key=rank)
        self.pending = best[4], best[5]
        self.attempts[self.pending[0]] += 1
        return self.pending

    def finish(self, record=None):
        """Consume a validated rendered candidate or a cheap geometry rejection.

        record=None is for normal candidate rejection before rendering.
        Technical failures (invalid masks, write errors, lost GPU state) must
        abort at the caller, not be silently counted as ordinary misses.
        The caller must finish writing/validating RGB, metadata and masks first.
        """
        if self.pending is None:
            raise RuntimeError("No request is in flight")
        mid, requested = self.pending
        observed = "rejected"
        if record is not None:
            if record["map_id"] != mid:
                raise ValueError("Candidate map does not match current request")
            image_id = record["image_id"]
            if image_id in self.ids:
                raise ValueError("Duplicate candidate image ID")
            measurement = record["visibility_measurement"]
            b = measurement["visibility_bin"]
            if b not in (*VISIBILITY_BINS, "reject_too_occluded"):
                raise ValueError("Unexpected candidate visibility bin")
            if type(measurement["training_eligible"]) is not bool:
                raise ValueError("Candidate eligibility must be a boolean")
            # Keep validated candidate metadata only; images stay in the
            # caller's staging. Cells above either marginal cap can never
            # contribute to a feasible selection. Keep their telemetry anyway.
            if b in VISIBILITY_BINS and measurement["training_eligible"]:
                observed = b
                capacity = self.report["available_by_map_and_bin"][mid][b]
                if capacity < min(self.targets[b], self.map_targets[mid]):
                    self.cells[mid][b].append(image_id)
                    self.capacity[mid][b] += 1
                    self.available[b] += 1
                    self.retained += 1
                    self.report = solve_capacity(self.targets, self.map_targets,
                                                 self.capacity, self.available, self.retained)
            self.ids.add(image_id)
        self.outcomes[mid, requested, observed] += 1
        self.last_map = mid
        self.pending = None

    def status(self):
        """Return the current selection, budget and provisional deficits."""
        return {**self.report,
                "attempts_by_map": {m: self.attempts[m] for m in self.budget},
                "remaining_budget_by_map": {m: self.budget[m] - self.attempts[m] for m in self.budget},
                "observations": sum(self.outcomes.values()),
                "pending": self.pending,
                "status": ("COMPLETE" if self.report["complete"] else
                           "INCOMPLETE" if not self.pending and
                           not any(self.attempts[m] < self.budget[m] and self.map_targets[m]
                                   for m in self.budget) else "RUNNING")}
