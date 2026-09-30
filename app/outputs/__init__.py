"""The two things raDash writes — M7.

`DESIGN.md` §8 sets the whole of the boundary in one line: reading into an
area is the main planning action, so raDash makes that hand-off cheap
**without reaching into anything**. It writes one file to its own `output/`
directory and stops.

It does not write `litrev.yaml`, does not write into any project folder, and
does not queue anything on trundlr. One copy-paste is the price of raDash
never being able to set work in motion off a score you have not sanity-checked,
and that price is correct while the scores are young.

Two artifacts, routed by the signal that produced the candidate:

- a **read-in brief** for a signal 1, 2 or confirmed 3 — go and read the area
  properly, with the seed papers from both sides of the score;
- a **corpus fill list** for an unconfirmed 3 or a signal 4 — go and collect
  the area first, because the honest response to a reading gap is reading.

`emit` is the only module that touches the filesystem, and it refuses a path
outside `output/` rather than trusting its callers to pass a good one.
"""
