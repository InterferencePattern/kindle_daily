# One Issue per Run, not one Issue per Category

A Run produces a single combined Issue containing every non-excluded Category
as a TOC section inside one EPUB. The previous design shipped one Issue per
Category, which cluttered the Kindle library with N editions per day and made
the device unpleasant to navigate.

The accepted cost is blast radius. Under the old model, a build or SMTP
failure on Politics still let Photography ship. Under the new model, a
failure on the day's combined Issue means nothing ships that day. This is
acceptable because (a) build failures are essentially impossible with
well-formed inputs — per-article fetch and oversize failures are already
handled upstream of build, (b) SMTP send already retries before giving up,
and (c) a silent partial-delivery fallback would resurrect the multi-edition
problem and hide failures from the user. A loud zero-delivery day prompts
the user to check logs; tomorrow's Run retries the unread items naturally
under the inbox-drain contract.

Category boundaries are preserved as the unit at which Parts split when an
Issue exceeds the size cap. A single Category that on its own overflows the
cap falls back to mid-Category splitting (the original chunker behaviour).
This keeps reading coherence high in the common case while still honouring
inbox drain in the pathological case.

Categories inside the Issue are ordered by article count, descending, with
alphabetical tiebreaks. Misc plays by the same rule as everyone else —
deliberately no special-casing, so the ordering stays explainable and stable
without a config file the user has to maintain when subscriptions change.
