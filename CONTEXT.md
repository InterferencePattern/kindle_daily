# Kindle Daily

A daily job that drains the user's FreshRSS unread queue onto their Kindle as
a single magazine-style EPUB per day.

## Language

**Issue**:
The one EPUB delivered to the Kindle for a given **Run**. Contains every
non-excluded **Category** present in today's unread set, grouped as TOC
sections inside a single book. Exactly one Issue per Run on the happy path.
_Avoid_: "edition", "digest", "newsletter"

**Part**:
A size-capped slice of an Issue. When an Issue's content exceeds the per-EPUB
size cap, it is split into multiple Parts ("Part 1/3", "Part 2/3", …) each
shipped as its own `.epub`. Splits happen on Category boundaries; only a
single Category that alone overflows the cap is split mid-Category.
_Avoid_: "chunk" (internal implementation term), "volume"

**Category**:
A FreshRSS folder/label (e.g. "Photography", "Tech"). Articles with no
FreshRSS category land in the **Misc** category. Categories are the
structural unit inside an Issue (TOC sections) and the unit at which Parts
break, but are never themselves the unit of delivery.
_Avoid_: "section" (overloaded with EPUB sections), "topic", "feed group"

**Run**:
One end-to-end execution of the pipeline (cron-triggered at 05:00 local). A
Run produces zero Issues (nothing to send) or one Issue, possibly split into
multiple Parts.
_Avoid_: "job", "build"

**Delivered**:
An article is _Delivered_ once Gmail SMTP returns 250 OK on the Part that
contains it. Delivery is the only event that permits marking the article as
read in FreshRSS.
_Avoid_: "sent", "shipped"

**Inbox drain**:
The system's contract with the user: every unread article that survives the
category-exclusion filter MUST be Delivered in the same Run it was fetched.
Nothing is silently capped or dropped. If a single Issue would be too large
for one EPUB, it is split across multiple Parts in the same Run.
_Avoid_: "digest mode" (the system is explicitly NOT a digest)

## Example dialogue

> **Dev:** This morning's unread set is 80 articles across four categories
> and would build to a 28 MB EPUB. Our cap is 15 MB. What do we ship?
>
> **Domain:** All 80. Inbox drain means a Run that fetches 80 items must
> Deliver 80 items. Split the Issue into Parts on Category boundaries —
> "Part 1/3", "Part 2/3", "Part 3/3" — and ship them all this morning.
> Only mark items read after the SMTP 250 OK for the Part they were in.
>
> **Dev:** What if Photography alone is 18 MB?
>
> **Domain:** Then that one Category gets split mid-Category as a fallback:
> Photography articles are packed into successive Parts until they fit.
> The other Categories ride along in whichever Part has room.
>
> **Dev:** What if a single article on its own exceeds the cap?
>
> **Domain:** That's an actual failure — log it, leave the item unread,
> and continue with the rest. We don't have a story for "the article
> itself doesn't fit" yet.
