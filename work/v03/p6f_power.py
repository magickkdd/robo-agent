"""Can the E1 numbers that the v0.3 report quotes as findings actually be distinguished from chance?

Two of them:

1. `L2.dependency_edge_violation` on the `full` arm — reported as **1.0 (2/2)**, and written up as
   "the model wrote dependency edges and violated all of them". n=2. Under any null where a
   violation is no more likely than not, 2/2 happens one time in four. That is a coin landing
   heads twice, not a finding.

2. `wo_replanning` 9/12 against `full` 8/12 — the arm that looks *better* without replanning. n=12
   each. A one-episode difference is noise at this size.

Both are re-analyses of data already on disk, so this costs nothing. The point is not to decide
whether the effects are real — n cannot do that — but to find out whether the report's wording is
supported by the denominators it quotes.
"""
import json
import math
import os
import sys
from itertools import combinations

ARCHIVE = "/home/czx/embodied-agent-batches/v03/e1"
SUMMARY = os.path.join(ARCHIVE, "lh_score", "two_sources_summary.json")


def wilson(n, d):
    """Wilson 95%, the same construction the project's instruments publish."""
    if not d:
        return None
    z = 1.959963984540054
    p = n / d
    den = 1 + z * z / d
    centre = (p + z * z / (2 * d)) / den
    half = z * math.sqrt(p * (1 - p) / d + z * z / (4 * d * d)) / den
    return (round(max(0.0, centre - half), 4), round(min(1.0, centre + half), 4))


def exact_two_sided_binom(n, d, p=0.5):
    """Exact two-sided binomial p-value against a fair-coin null.

    Two-sided by the probability-of-this-or-more-extreme method, which is what scipy's
    `binomtest` does, so the number here is the one a reader would compute independently.
    """
    if not d:
        return None
    probs = [math.comb(d, k) * p ** k * (1 - p) ** (d - k) for k in range(d + 1)]
    return min(1.0, sum(q for q in probs if q <= probs[n] + 1e-12))


def fisher_exact_2x2(a, b, c, d):
    """Two-sided Fisher exact p for [[a, b], [c, d]] — the right test for 2x2 counts this small."""
    n = a + b + c + d
    r1, c1 = a + b, a + c

    def prob(x):
        return (math.comb(r1, x) * math.comb(n - r1, c1 - x)) / math.comb(n, c1)

    lo, hi = max(0, c1 - (n - r1)), min(r1, c1)
    obs = prob(a)
    return min(1.0, sum(prob(x) for x in range(lo, hi + 1) if prob(x) <= obs + 1e-12))


print("=" * 78)
print("1. L2.dependency_edge_violation on the full arm")
print("=" * 78)
n, d = 2, 2
print(f"  reported     : {n}/{d} = {n/d:.3f}")
print(f"  Wilson 95%   : {wilson(n, d)}")
print(f"  exact two-sided binomial p (null = 0.5): {exact_two_sided_binom(n, d):.4f}")
print(f"  the 95% interval runs from {wilson(n, d)[0]:.3f} to 1.0, so the data do not locate the")
print("  rate at all. (Two-sided p is 0.25, not 0.5: k=2 and k=0 are equally extreme, so the")
print("  probability-of-more-extreme sum doubles the one-sided tail.)")
print()
print("  what n=2 CAN support: the mechanism is reachable on this arm. Two episodes wrote")
print("  dependency edges and the guard caught them, so L2 is not structurally n/m anymore.")
print("  what n=2 CANNOT support: 'the model violates 100% of its dependency edges'.")
print()

print("=" * 78)
print("2. wo_replanning 9/12 against full 8/12")
print("=" * 78)
a, b = 9, 3   # wo_replanning: success / not
c, e = 8, 4   # full
print("  wo_replanning: {0}/{1} = {2:.3f}   Wilson 95% {3}".format(
    a, a + b, a / (a + b), wilson(a, a + b)))
print(f"  full         : {c}/{c+e} = {c/(c+e):.3f}   Wilson 95% {wilson(c, c+e)}")
p = fisher_exact_2x2(a, b, c, e)
print(f"  Fisher exact two-sided p: {p:.4f}")
print(f"  difference: {a / (a + b) - c / (c + e):+.4f} "
      f"({(a / (a + b) - c / (c + e)) * (a + b):.2f} episodes)")
print()
print("  the intervals overlap almost completely, and the exact test cannot separate them.")
# How big would each arm have to be to see a gap this size? The first version of this line
# searched for the smallest *high* count against 10/20, which stops immediately (0/20 against
# 10/20 is already significant at p=4e-4) and printed a meaningless "about 20 episodes per arm".
# What is wanted is the other direction: hold the effect at ~8 points and grow n.
def smallest_n_for(base=0.70, delta=0.0833):
    n = 5
    while n < 20000:
        k1, k2 = round(base * n), round((base + delta) * n)
        if fisher_exact_2x2(k1, n - k1, k2, n - k2) < 0.05:
            return n
        n += 1
    return None

need = smallest_n_for()
print(f"  to see a {8.33:.1f}-point gap (0.70 vs 0.78) at p<0.05 needs about "
      f"{need} episodes per arm — {need / 12:.0f}x this batch's 12.")
print(f"  at n=12 per arm this row cannot decide between the arms, in either direction.")
print()

print("=" * 78)
print("3. what the report actually says, and whether the denominators support it")
print("=" * 78)
REPORT = ("/home/czx/embodied-agent-robot-agent-embodied-agent-4/docs/"
          "continuous-decision-v0.3-final-report.md")
text = open(REPORT, encoding="utf-8").read()
for needle, label in (("1.0 (2/2)", "L2 2/2"), ("0.5789 (11/19)", "L6 11/19"),
                      ("9/12", "wo_replanning 9/12")):
    print(f"  {label:24s} present in the report: {needle in text}")
print()
print("  L6.replanning_effectiveness 11/19, checked the same way rather than assumed:")
n6, d6 = 11, 19
p6 = exact_two_sided_binom(n6, d6)
print(f"    {n6}/{d6} = {n6/d6:.4f}, Wilson 95% {wilson(n6, d6)}")
print(f"    exact two-sided binomial p vs 0.5: {p6:.4f}")
print(f"    -> also NOT distinguishable from chance (p={p6:.2f}). An earlier draft of this")
print(f"       script asserted 'p<0.01' in a print while computing {p6:.4f} two lines later; the")
print("       assertion was the error and it is what nearly went into a summary. 0.5789 at n=19")
print("       is a number to report, not a rate to conclude from.")
print("    the comparison that actually matters for L6 is across decision sources, and there the")
print("    denominators differ (19 model-seat rounds against 4 rule-seat rounds), so it cannot be")
print("    settled by either seat alone either.")
