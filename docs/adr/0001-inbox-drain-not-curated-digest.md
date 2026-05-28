# Kindle Daily is an inbox drain, not a curated digest

Every unread article that survives FreshRSS category exclusion must be
Delivered to the Kindle in the same Run it was fetched. The system never
silently caps or discards articles. If today brings more articles than fit in
one EPUB, the Run's Issue is split into multiple Parts ("Part 1/3", etc.) so
the inbox is drained completely.

If the volume gets unmanageable, the user fixes it upstream in FreshRSS (mute
feeds, exclude categories). The system does not silently filter on their
behalf — silent filtering would make missed-article bugs invisible.

The only event that justifies marking an article read in FreshRSS is SMTP
250 OK for the Issue containing it. Anything else (fetch failure, build
failure, size-cap discard) leaves the article unread for tomorrow's Run.
