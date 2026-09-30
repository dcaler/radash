"""The works ledger: turning several sources' claims into one list you trust.

Nothing in this package decides anything. It *proposes*, with the evidence
attached, and a human rules at the gate (`BUILD_PLAN.md` M2-G). That division
is the whole design: citation indexes are confidently wrong often enough that
an automatic merge would bake their errors into every number downstream, and
the errors are not random — they cluster on exactly the cases that matter, like
a preprint and its published version, or a Zenodo software release counted once
per version.

Three ideas carry the package:

**Fingerprints, not row ids.** Candidate rows are rebuilt from scratch every
refresh, so anything that has to outlive a refresh — above all your rulings —
is keyed by a fingerprint derived from the work itself.

**Proposals carry their reasons.** Every proposal states which signals fired
and how strongly. A proposal you cannot interrogate is a number you cannot
defend, and the gate is where that gets tested.

**Merging is per-number.** Two sources disagreeing about a citation count is
information, not noise. The merge keeps both lanes and records where each
figure came from rather than blending them into one unattributable total.
"""
