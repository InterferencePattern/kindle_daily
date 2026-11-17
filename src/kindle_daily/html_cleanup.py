"""HTML cleanup for article bodies.

Everything that turns a raw article body into Kindle-safe HTML: truncation
detection on the FreshRSS summary, duplicate-title and subscription-CTA
stripping, trafilatura XML-to-HTML translation, and the tag/link sanitizer.
Pure functions over strings; no I/O.
"""

from __future__ import annotations

import logging
import re
from urllib.parse import urljoin

from lxml import html as lxml_html

log = logging.getLogger(__name__)


def word_count(html: str) -> int:
    """Rough word count of an HTML fragment.

    Strips tags, then splits on whitespace — good enough for a reading-time
    estimate.
    """
    return len(re.sub(r"<[^>]+>", " ", html).split())


def parse_fragment(body_html: str):
    """Parse an HTML fragment under a ``<div>`` root; ``None`` if unparseable."""
    try:
        return lxml_html.fragment_fromstring(body_html, create_parent="div")
    except (lxml_html.etree.ParserError, ValueError):
        return None


def serialize(root, wrapper: str = "div") -> str:
    """Serialize ``root`` to HTML, stripping its ``<wrapper>`` tag."""
    out = lxml_html.tostring(root, encoding="unicode", method="html")
    head, tail = f"<{wrapper}>", f"</{wrapper}>"
    if out.startswith(head) and out.endswith(tail):
        out = out[len(head) : -len(tail)]
    return out


# --------------------------------------------------- truncation detection

# Anchor text that signals "this RSS body is just an excerpt." Matched
# case-insensitively against the collapsed text of each <a> in the summary.
# Trailing arrows / ellipses / punctuation are tolerated via ``\W*$``.
_TRUNCATION_MARKER_RE = re.compile(
    r"^(read\s+(more|on|(the\s+)?(full\s+)?(article|story|post))|continue\s+reading)\W*$",
    re.IGNORECASE,
)

# Summaries shorter than this are treated as teasers even if they contain no
# marker link. Catches publishers (e.g. ACLU) who emit only a one-sentence
# dek with no "read more" anchor — without this we'd ship the dek as the
# article body. 150 was chosen as comfortably below the shortest plausible
# full post in the feeds we read.
_FULL_TEXT_MIN_WORDS = 150

# WordPress's the_excerpt() and many other CMSes (Drupal, Joomla, Ghost,
# Squarespace) end truncated excerpts with a bracketed ellipsis. The body
# is sometimes long enough to pass the word-count floor but is still a
# teaser. Detecting this marker is essentially free and avoids per-feed
# allowlists. Matches: [...], [ ... ], [&hellip;], […], with optional
# whitespace inside the brackets.
_BRACKET_ELLIPSIS_RE = re.compile(
    r"\[\s*(?:&hellip;|…|\.\s*\.\s*\.+)\s*\]",
    re.IGNORECASE,
)


def summary_appears_truncated(summary_html: str) -> bool:
    """Return True if the RSS summary looks like a teaser rather than full text.

    Three signals are checked:

    1. Total word count below :data:`_FULL_TEXT_MIN_WORDS`.
    2. A bracketed-ellipsis marker (``[...]``, ``[&hellip;]``, ``[…]``) —
       WordPress's excerpt truncation glyph, also used by most other CMSes.
    3. Anchor text matching :data:`_TRUNCATION_MARKER_RE` ("read more", etc.).

    Any one is sufficient. Used to decide whether the FreshRSS-stored body
    can be trusted as-is or whether we should fetch the live page.
    """
    if word_count(summary_html) < _FULL_TEXT_MIN_WORDS:
        return True
    if _BRACKET_ELLIPSIS_RE.search(summary_html):
        return True
    root = parse_fragment(summary_html)
    if root is None:
        return False
    for a in root.xpath(".//a"):
        text = " ".join("".join(a.itertext()).split())
        if text and _TRUNCATION_MARKER_RE.match(text):
            return True
    return False


# --------------------------------------------------------- title dedupe


def strip_duplicate_title(body_html: str, title: str) -> str:
    """Drop a body ``<h1>`` whose text equals ``title``.

    Some publishers (notably The Verge full-RSS feed) include the article
    headline inline in their HTML, which trafilatura preserves. The chapter
    page already renders the title as its own ``<h1>``, so without this we
    get the title twice. Subheadings (``<h2>``/``<h3>``) are left alone.
    """
    if not title or "<h1" not in body_html.lower():
        return body_html

    root = parse_fragment(body_html)
    if root is None:
        return body_html

    target = _normalize(title)
    removed = False
    for h1 in root.xpath(".//h1"):
        text = "".join(h1.itertext())
        if _normalize(text) == target:
            drop(h1)
            removed = True

    if not removed:
        return body_html

    return serialize(root)


# --------------------------------------------------- subscription CTA strip

# Recognisable solicitation constructs, not tied to one publisher. Each is an
# action verb bound to a media-support object, so they fire on subscribe/donate
# CTAs across feeds but not on prose that merely mentions "subscribe".
_SUBSCRIPTION_CTA_RE = re.compile(
    r"support\w*\s+(?:our|its|independent|quality|local|nonprofit|award)"
    r"|by\s+(?:purchasing|buying)\s+an?\s+subscription"
    r"|consider\s+(?:subscrib|support|donat)"
    r"|subscrib\w*\s+(?:today|now)"
    r"|sign\s+up\s+for\s+(?:our|the)\b.*newsletter"
    r"|become\s+a\s+(?:member|subscriber|supporter)"
    r"|donate\s+(?:today|now)",
    re.IGNORECASE,
)
# Words that mark a short heading as the promo's lead-in (e.g. SciAm's
# "On supporting science journalism"), removed only when it directly precedes a
# matched CTA paragraph — so real section headings are left alone.
_CTA_HEADING_WORDS = (
    "subscrib",
    "support",
    "donat",
    "member",
    "newsletter",
    "journalism",
)


def strip_subscription_cta(body_html: str) -> str:
    """Drop subscribe/donate solicitation blocks embedded in article bodies.

    Some publishers (e.g. Scientific American) inject a "support our journalism"
    promo into the body content stream as an unstyled heading + paragraph, with
    no class or container to target. trafilatura treats it as article text and
    keeps it. We match the paragraph by its solicitation phrasing and, if the
    element immediately before it is a short promo lead-in heading, drop that
    too. The match is on phrasing common across publishers, not a single
    publisher's exact text.

    ponytail: text heuristic — the upstream HTML carries no structural marker,
    so phrasing is the only signal. Tune _SUBSCRIPTION_CTA_RE if a real
    paragraph gets eaten or a new CTA wording slips through.
    """
    if "<p" not in body_html.lower():
        return body_html

    root = parse_fragment(body_html)
    if root is None:
        return body_html

    removed = False
    for p in root.xpath(".//p"):
        if not _SUBSCRIPTION_CTA_RE.search(_normalize("".join(p.itertext()))):
            continue
        prev = p.getprevious()
        if prev is not None and prev.tag in ("h2", "h3", "h4"):
            heading = _normalize("".join(prev.itertext()))
            if len(heading.split()) <= 10 and any(
                w in heading for w in _CTA_HEADING_WORDS
            ):
                drop(prev)
        drop(p)
        removed = True

    if not removed:
        return body_html

    return serialize(root)


def _normalize(s: str) -> str:
    """Lowercase, replace nbsp with space, collapse internal whitespace."""
    return " ".join(s.replace("\xa0", " ").lower().split())


def drop(element) -> None:
    """Remove an element while preserving its tail text."""
    parent = element.getparent()
    if parent is None:
        return
    tail = element.tail or ""
    if tail:
        prev = element.getprevious()
        if prev is not None:
            prev.tail = (prev.tail or "") + tail
        else:
            parent.text = (parent.text or "") + tail
    parent.remove(element)


# ------------------------------------------------------ trafilatura XML

# Trafilatura emits images as <graphic src="..." alt="..."/>. The translator
# below converts trafilatura's TEI-ish XML to HTML the EPUB renderer understands.
_TRAFILATURA_TAG_RENAMES = {
    "p": "p",
    "quote": "blockquote",
    "code": "code",
    "item": "li",
    "row": "tr",
    "cell": "td",
    "table": "table",
    "lb": "br",
}

_HEAD_LEVEL_BY_REND = {"h1": "h2", "h2": "h2", "h3": "h3", "h4": "h4"}


def html_from_trafilatura_xml(xml_body: str) -> str:
    """Translate trafilatura's XML output to clean HTML.

    Trafilatura's XML preserves images inline as ``<graphic src=...>``
    along with TEI-ish tags (``<head>``, ``<p>``, ``<list>``, ``<item>``,
    ``<hi>``, ``<quote>``, ``<row>``, ``<cell>``, ``<ref>``, ``<lb/>``)
    that we map to plain HTML equivalents. Unknown tags are left in place;
    :func:`sanitize_html` unwraps them later.

    Returns an empty string if the input is empty or unparseable.
    """
    if not xml_body or not xml_body.strip():
        return ""
    # Parse as XML, not HTML — lxml.html treats <head> as the HTML head
    # element and reshapes the tree. We wrap in a dummy root so multiple
    # top-level tags are allowed.
    try:
        root = lxml_html.etree.fromstring(f"<kdroot>{xml_body}</kdroot>")
    except lxml_html.etree.XMLSyntaxError:
        return xml_body  # fall back: emit as-is, downstream still works

    # Translate in two passes: first rename/rewrite, then unwrap unknowns.
    # Walking with iter() while mutating is fine because we never add new
    # elements — we only rename tags and rewrite attributes.
    for el in list(root.iter()):
        tag = el.tag if isinstance(el.tag, str) else None
        if tag is None:
            continue
        if tag == "graphic":
            src = el.get("src", "")
            alt = el.get("alt", "")
            el.tag = "img"
            el.attrib.clear()
            if src:
                el.set("src", src)
            el.set("alt", alt)
            continue
        if tag == "ref":
            target = el.get("target", "")
            el.tag = "a"
            el.attrib.clear()
            if target:
                el.set("href", target)
            continue
        if tag == "head":
            level = _HEAD_LEVEL_BY_REND.get(el.get("rend", ""), "h2")
            el.tag = level
            el.attrib.clear()
            continue
        if tag == "list":
            el.tag = "ol" if el.get("rend") == "ol" else "ul"
            el.attrib.clear()
            continue
        if tag == "hi":
            el.tag = "strong" if el.get("rend") == "#b" else "em"
            el.attrib.clear()
            continue
        if tag in _TRAFILATURA_TAG_RENAMES:
            el.tag = _TRAFILATURA_TAG_RENAMES[tag]
            el.attrib.clear()
            continue
        # Any other unknown tag is unwrapped later by sanitize_html.

    return serialize(root, wrapper="kdroot")


# ---------------------------------------------------------------- sanitize

# Tags kept as-is. Everything else is unwrapped (text survives, tag goes).
# Kindle's reflowable renderer silently falls back to fixed layout (E016) on
# markup it can't reflow, so only plain document tags get through.
_ALLOWED_HTML_TAGS = frozenset(
    {
        "a",
        "abbr",
        "b",
        "blockquote",
        "br",
        "caption",
        "cite",
        "code",
        "dd",
        "div",
        "dl",
        "dt",
        "em",
        "figcaption",
        "figure",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "hr",
        "i",
        "img",
        "li",
        "ol",
        "p",
        "pre",
        "q",
        "s",
        "small",
        "span",
        "strong",
        "sub",
        "sup",
        "table",
        "tbody",
        "td",
        "th",
        "thead",
        "tr",
        "u",
        "ul",
    }
)

# Tags removed together with their content: media/embeds Kindle can't render
# (an empty podcast <audio> is enough to trigger E016), plus code and forms
# whose inner text would be junk if unwrapped.
_DROPPED_HTML_TAGS = frozenset(
    {
        "audio",
        "button",
        "canvas",
        "embed",
        "form",
        "iframe",
        "input",
        "math",
        "noscript",
        "object",
        "script",
        "select",
        "style",
        "svg",
        "template",
        "textarea",
        "video",
    }
)

_LINK_SCHEMES = ("http://", "https://", "mailto:")


def sanitize_html(body_html: str, base_url: str) -> str:
    """Reduce an article body to Kindle-safe HTML.

    Runs on every body, whether it came from the FreshRSS summary or from
    trafilatura, so the EPUB never depends on what a feed happened to send.

    Parameters
    ----------
    body_html : str
        Article body HTML fragment.
    base_url : str
        Article URL, used to resolve relative ``href`` values. Links that
        can't be made absolute ``http(s)``/``mailto`` lose their ``href``.

    Returns
    -------
    str
        The sanitized fragment, or ``""`` if it couldn't be parsed.
    """
    if not body_html or not body_html.strip():
        return ""
    root = parse_fragment(body_html)
    if root is None:
        log.warning("could not parse body for %s", base_url)
        return ""

    for el in list(root.iter(*_DROPPED_HTML_TAGS)):
        drop(el)

    for el in list(root.iter()):
        if el is root or not isinstance(el.tag, str):
            continue
        if el.tag not in _ALLOWED_HTML_TAGS:
            _unwrap(el)

    for a in root.iter("a"):
        href = (a.get("href") or "").strip()
        absolute = urljoin(base_url, href) if base_url and href else href
        if absolute.lower().startswith(_LINK_SCHEMES):
            a.set("href", absolute)
        else:
            a.attrib.pop("href", None)

    return serialize(root)


def _unwrap(element) -> None:
    """Replace ``element`` with its children, preserving text and tails.

    Used to drop a tag we don't want in the HTML output without losing its
    content. ``<foo>hi <b>bold</b> bye</foo>`` becomes ``hi <b>bold</b> bye``.
    """
    parent = element.getparent()
    if parent is None:
        return
    index = list(parent).index(element)
    # Promote element.text onto the previous sibling's tail / parent's text
    head_text = element.text or ""
    if head_text:
        if index == 0:
            parent.text = (parent.text or "") + head_text
        else:
            prev = parent[index - 1]
            prev.tail = (prev.tail or "") + head_text
    # Move children up, in order, into element's slot
    for offset, child in enumerate(list(element)):
        parent.insert(index + offset, child)
    # element.tail goes after the last promoted child (or onto prev/parent.text
    # if element had no children)
    tail_text = element.tail or ""
    if tail_text:
        last_promoted = parent[index + len(element) - 1] if len(element) else None
        if last_promoted is not None:
            last_promoted.tail = (last_promoted.tail or "") + tail_text
        elif index == 0:
            parent.text = (parent.text or "") + tail_text
        else:
            prev = parent[index - 1]
            prev.tail = (prev.tail or "") + tail_text
    parent.remove(element)
