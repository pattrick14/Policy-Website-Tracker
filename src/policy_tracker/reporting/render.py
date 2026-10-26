"""Render a ``Digest`` into archive files and an email (PROJECT_PLAN Step 6).

Every source string is untrusted: Markdown output backslash-escapes markup
characters, HTML output is ``html.escape``d, links are emitted only for
http(s) URLs, and CSV cells that a spreadsheet would read as a formula are
prefixed with an apostrophe. Renderers read only the ``Digest``.
"""

from __future__ import annotations

import csv
import html
import io
import json
import re
from collections.abc import Iterable, Sequence
from email.message import EmailMessage
from urllib.parse import quote, urlsplit

from policy_tracker.core.models import Discovery, EventType
from policy_tracker.core.serialize import to_data
from policy_tracker.reporting import deadlines as dl
from policy_tracker.reporting.digest import SECTIONS, section_of
from policy_tracker.reporting.types import (
    Category,
    Deadline,
    Digest,
    DigestItem,
    RenderedEmail,
    RenderedFile,
)

MAX_EMAIL_BYTES = 5 * 1024 * 1024
OPEN_DEADLINES_PATH = "open_deadlines.csv"
ROUTINE_EMAIL_LIMITS = (100, 25, 0)  # compact appendix rows tried in order to fit the cap
SECTION_EMAIL_LIMITS = (None, 200, 50)

CATEGORY_LABELS: dict[Category, str] = {
    Category.ACTION_REQUIRED: "Action required",
    Category.RULES_AND_REGULATION: "Rules and regulation",
    Category.POLICY_AND_DIRECTION: "Policy and direction",
    Category.RESEARCH_AND_REPORTING: "Research and reporting",
    Category.ROUTINE: "Routine",
}
CATEGORY_KEYS: dict[Category, str] = {
    Category.ACTION_REQUIRED: "action_required",
    Category.RULES_AND_REGULATION: "rules_and_regulation",
    Category.POLICY_AND_DIRECTION: "policy_and_direction",
    Category.RESEARCH_AND_REPORTING: "research_and_reporting",
    Category.ROUTINE: "routine",
}
EVENT_LABELS: dict[EventType, str] = {
    EventType.NEW_MATTER: "New matter",
    EventType.NEW_DOCUMENT: "New document",
    EventType.NEW_APPEARANCE: "Now also listed on another feed",
    EventType.METADATA_CHANGED: "Listing details changed",
    EventType.STATUS_CHANGED: "Section changed",
    EventType.DEADLINE_CHANGED: "Deadline changed",
    EventType.CONTENT_REPLACED: "File replaced (server validators changed)",
    EventType.WITHDRAWN: "Removed from source",
    EventType.REAPPEARED: "Reappeared on source",
}
WARNING_LABELS: dict[str, str] = {
    "hindi_only": "Document is Hindi-only; English listing details shown",
    "scanned": "Scanned document; no text layer",
    "ocr_unavailable": "OCR unavailable",
    "ocr_failed": "OCR failed",
    "ocr_text": "Text obtained by OCR",
    "extraction_failed": "Text extraction failed; listing details kept",
    "extraction_partial": "Only part of the document text was read",
    "unsupported_format": "File type not supported for text extraction",
}
_NEW_EVENTS = frozenset({EventType.NEW_DOCUMENT, EventType.NEW_MATTER, EventType.CONTENT_REPLACED})
_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")
_MD_SPECIAL = re.compile(r"([\\`*_\[\]<>|~&#!])")
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")

EVENT_CSV_COLUMNS = (
    "run_id", "section", "category", "event_type", "discovery", "discovery_label", "site_id",
    "feed_id", "entity_key", "title", "url", "published_date", "published_raw", "first_seen_at",
    "deadlines", "deadline_changed", "previous_deadlines", "warnings", "reasons", "event_id",
)  # fmt: skip
OPEN_DEADLINE_COLUMNS = (
    "deadline_date", "deadline_time", "sort_key", "ambiguous", "raw", "evidence", "method",
    "title", "url", "category", "site_id", "feed_id", "entity_key", "published_date",
    "first_seen_at",
)  # fmt: skip


# ---------------------------------------------------------------------- helpers


def clean(text: str | None) -> str:
    """Collapse whitespace and strip control characters from a source string."""

    return " ".join(_CONTROL.sub(" ", text or "").split())


def md_escape(text: str | None) -> str:
    return _MD_SPECIAL.sub(r"\\\1", clean(text))


def safe_url(url: str | None) -> str | None:
    """Percent-encoded http(s) URL, or None for any other scheme."""

    if not url:
        return None
    url = clean(url)
    if urlsplit(url).scheme.lower() not in ("http", "https"):
        return None
    return quote(url, safe=":/?#[]@!$&*+,;=%~-._")


def neutralize(cell: str) -> str:
    """Prefix a CSV cell that a spreadsheet would evaluate as a formula."""

    return "'" + cell if cell.startswith(_FORMULA_PREFIXES) else cell


def _period_path(digest: Digest, suffix: str) -> str:
    run = digest.run_id
    if re.match(r"^\d{8}T", run):
        year, month = run[:4], run[4:6]
    else:
        year, month = digest.generated_at[:4], digest.generated_at[5:7]
    return f"digests/{year}/{month}/{run}.{suffix}"


def category_label(item: DigestItem) -> str:
    """Display label; unclassified captured publications read "Unclassified"."""

    return "Unclassified" if section_of(item) == "other" else CATEGORY_LABELS[item.category]


def show_deadlines(item: DigestItem) -> bool:
    """D-007: show deadlines only when discovered (new item) or changed."""

    return item.deadline_changed or item.event_type in _NEW_EVENTS


def deadline_text(item: DigestItem) -> str | None:
    if not show_deadlines(item):
        return None
    current = "; ".join(dl.display(d) for d in item.deadlines) or "none"
    if item.deadline_changed:
        previous = item.details.get("previous_deadlines", "none")
        return f"Deadline changed: {current} (previously: {previous})"
    if item.deadlines:
        return f"Deadline: {current}"
    return None


def event_text(item: DigestItem) -> str:
    label = EVENT_LABELS.get(item.event_type, item.event_type.value)
    details = item.details
    if item.event_type is EventType.METADATA_CHANGED and details.get("changed_fields"):
        label += f" ({details['changed_fields']})"
    if item.event_type is EventType.STATUS_CHANGED and "section.new" in details:
        old, new = details.get("section.old") or "none", details["section.new"] or "none"
        label += f" ({old} -> {new})"
    if item.event_type is EventType.NEW_APPEARANCE:
        label = f"Now also listed on {item.feed_id}"
    if item.discovery is Discovery.BACKFILL:
        label += f" - {details.get('discovery_label', 'Backfill')} (not a new publication)"
    return label


def published_text(item: DigestItem) -> str:
    published = clean(item.published_raw) or "not stated"
    if item.published_date and item.published_date != clean(item.published_raw):
        published += f" ({item.published_date})"
    return f"Published: {published}; first seen: {item.first_seen_at[:10]}"


def warning_texts(item: DigestItem) -> list[str]:
    return [WARNING_LABELS.get(w, w) for w in item.warnings]


def _grouped(items: Iterable[DigestItem]) -> dict[str, list[DigestItem]]:
    groups: dict[str, list[DigestItem]] = {key: [] for key, _ in SECTIONS}
    for item in items:
        groups[section_of(item)].append(item)
    return groups


def _health_summary(digest: Digest) -> str:
    ok = sum(1 for h in digest.health if h.accepted and h.enumeration_status == "complete")
    return f"{ok} of {len(digest.health)} feeds collected completely."


def _health_problems(digest: Digest) -> list[str]:
    lines = []
    for h in digest.health:
        if (
            h.accepted
            and h.enumeration_status == "complete"
            and h.baseline_status
            in (
                "complete",
                "",
            )
        ):
            continue
        state = "accepted" if h.accepted else "not accepted"
        reasons = ", ".join(h.quarantine_reasons) or "none"
        lines.append(
            f"{h.feed_id} ({h.site_id or 'unknown site'}): {state}, enumeration "
            f"{h.enumeration_status}, baseline {h.baseline_status or 'unknown'}, reasons: "
            f"{reasons}, consecutive failures: {h.consecutive_failures}"
        )
    return lines


def _banners(digest: Digest) -> list[str]:
    out = []
    if digest.partial:
        out.append(
            "PARTIAL DIGEST: some feeds failed or were incomplete. Their known records were "
            "kept and nothing was reported as removed. See Source health."
        )
    if digest.gap_warning:
        out.append(f"GAP WARNING: {clean(digest.gap_warning)}")
    return out


def _headline(digest: Digest) -> str:
    actions = sum(1 for i in digest.items if section_of(i) == "action")
    return f"{actions} action/deadline item(s), {len(digest.items)} update(s) in total"


# --------------------------------------------------------------------- markdown


def render_markdown(digest: Digest) -> RenderedFile:
    """Complete Markdown archive: digests/YYYY/MM/<run_id>.md."""

    out: list[str] = [
        f"# Policy tracker digest: {md_escape(digest.period_label)}",
        "",
        (
            f"Run `{md_escape(digest.run_id)}`, generated {md_escape(digest.generated_at)}. "
            f"{md_escape(_headline(digest))}."
        ),
        "",
    ]
    for banner in _banners(digest):
        out += [f"> **{md_escape(banner)}**", ""]
    groups = _grouped(digest.items)
    if not digest.items:
        out += ["No new or changed publications were found on tracked feeds this run.", ""]
    for key, heading in SECTIONS:
        items = groups[key]
        if not items:
            continue
        out += [f"## {heading} ({len(items)})", ""]
        if key == "routine":
            out += [
                "| Title | Feed | Published | Event |",
                "| --- | --- | --- | --- |",
            ]
            for item in items:
                title = md_escape(item.title)
                url = safe_url(item.url)
                cell = f"[{title}]({url})" if url else title
                out.append(
                    f"| {cell} | {md_escape(item.feed_id)} | "
                    f"{md_escape(item.published_raw) or 'not stated'} | "
                    f"{md_escape(event_text(item))} |"
                )
            out.append("")
            continue
        for item in items:
            out += _md_item(item)
        out.append("")
    if digest.notes:
        out += ["## Coverage notes", ""]
        out += [f"- {md_escape(note)}" for note in digest.notes]
        out.append("")
    out += ["## Source health", "", md_escape(_health_summary(digest)), ""]
    out += [
        "| Feed | Site | Accepted | Enumeration | Items | Baseline | Failures | Reasons |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for h in digest.health:
        out.append(
            f"| {md_escape(h.feed_id)} | {md_escape(h.site_id)} | {'yes' if h.accepted else 'no'} "
            f"| {md_escape(h.enumeration_status)} | {h.item_count} | "
            f"{md_escape(h.baseline_status)} | {h.consecutive_failures} | "
            f"{md_escape(', '.join(h.quarantine_reasons))} |"
        )
    out.append("")
    text = "\n".join(out).rstrip("\n") + "\n"
    return RenderedFile(_period_path(digest, "md"), text.encode("utf-8"), "text/markdown")


def _md_item(item: DigestItem) -> list[str]:
    title = md_escape(item.title)
    url = safe_url(item.url)
    head = f"[{title}]({url})" if url else title
    flag = "**[DEADLINE CHANGED]** " if item.deadline_changed else ""
    lines = [
        f"- {flag}**{head}**",
        (
            f"  - {md_escape(category_label(item))} · {md_escape(item.site_id)} / "
            f"{md_escape(item.feed_id)} · {md_escape(event_text(item))}"
        ),
        f"  - {md_escape(published_text(item))}",
    ]
    deadline = deadline_text(item)
    if deadline:
        lines.append(f"  - **{md_escape(deadline)}**")
        lines += [f"    - Evidence: {md_escape(d.evidence)}" for d in _shown(item)]
    for warning in warning_texts(item):
        lines.append(f"  - Warning: {md_escape(warning)}")
    if item.reasons:
        lines.append(f"  - Classified by: {md_escape(', '.join(item.reasons))}")
    return lines


def _shown(item: DigestItem) -> Sequence[Deadline]:
    return item.deadlines if show_deadlines(item) else ()


# ------------------------------------------------------------------------- json


def render_json(digest: Digest) -> RenderedFile:
    """Machine-readable archive: digests/YYYY/MM/<run_id>.json."""

    def item_data(item: DigestItem) -> dict[str, object]:
        data = to_data(item)
        data["category"] = CATEGORY_KEYS[item.category]
        data["section"] = section_of(item)
        return data

    document = {
        "kind": "digest",
        "schema_version": 1,
        "run_id": digest.run_id,
        "generated_at": digest.generated_at,
        "period_label": digest.period_label,
        "partial": digest.partial,
        "gap_warning": digest.gap_warning,
        "notes": list(digest.notes),
        "items": [item_data(i) for i in digest.items],
        "open_deadlines": [item_data(i) for i in digest.open_deadlines],
        "health": to_data(list(digest.health)),
    }
    text = json.dumps(document, sort_keys=True, ensure_ascii=False, indent=2) + "\n"
    return RenderedFile(_period_path(digest, "json"), text.encode("utf-8"), "application/json")


# -------------------------------------------------------------------------- csv


def _csv_bytes(columns: Sequence[str], rows: Iterable[Sequence[str]]) -> bytes:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow(columns)
    for row in rows:
        writer.writerow([neutralize(clean(cell)) for cell in row])
    return buffer.getvalue().encode("utf-8")


def render_csv(digest: Digest) -> RenderedFile:
    """One row per digest item: digests/YYYY/MM/<run_id>.csv."""

    rows = []
    for item in digest.items:
        rows.append(
            (
                digest.run_id,
                section_of(item),
                CATEGORY_KEYS[item.category],
                item.event_type.value,
                item.discovery.value,
                item.details.get("discovery_label", ""),
                item.site_id,
                item.feed_id,
                item.entity_key,
                item.title,
                item.url or "",
                item.published_date or "",
                item.published_raw or "",
                item.first_seen_at,
                "; ".join(dl.display(d) for d in item.deadlines),
                "yes" if item.deadline_changed else "no",
                item.details.get("previous_deadlines", ""),
                "; ".join(item.warnings),
                "; ".join(item.reasons),
                item.event_id,
            )
        )
    return RenderedFile(
        _period_path(digest, "csv"), _csv_bytes(EVENT_CSV_COLUMNS, rows), "text/csv"
    )


def render_open_deadlines_csv(digest: Digest) -> RenderedFile:
    """Complete current view of open deadlines, one row per deadline (D-007)."""

    rows = []
    for item in digest.open_deadlines:
        for d in item.deadlines:
            rows.append(
                (
                    d.date or "",
                    d.time or dl.TIME_NOT_STATED,
                    d.sort_key,
                    "yes" if d.ambiguous else "no",
                    d.raw,
                    d.evidence,
                    d.method,
                    item.title,
                    item.url or "",
                    CATEGORY_KEYS[item.category],
                    item.site_id,
                    item.feed_id,
                    item.entity_key,
                    item.published_date or "",
                    item.first_seen_at,
                )
            )
    rows.sort(key=lambda r: (r[2], r[7].casefold(), r[12]))
    return RenderedFile(OPEN_DEADLINES_PATH, _csv_bytes(OPEN_DEADLINE_COLUMNS, rows), "text/csv")


# ------------------------------------------------------------------------ email


def _subject(digest: Digest) -> str:
    if digest.items:
        actions = sum(1 for i in digest.items if section_of(i) == "action")
        summary = f"{actions} action, {len(digest.items)} update(s)"
    else:
        summary = "no new items (heartbeat)"
    partial = " [PARTIAL]" if digest.partial else ""
    return clean(f"Policy tracker: {digest.period_label} - {summary}{partial}")


def _links(digest: Digest, base: str | None) -> list[tuple[str, str | None]]:
    paths = [
        _period_path(digest, "md"),
        _period_path(digest, "csv"),
        _period_path(digest, "json"),
        OPEN_DEADLINES_PATH,
    ]
    if base is None:
        return [(path, None) for path in paths]
    return [(path, safe_url(base.rstrip("/") + "/" + path)) for path in paths]


def _limit(items: list[DigestItem], limit: int | None) -> tuple[list[DigestItem], int]:
    if limit is None or len(items) <= limit:
        return items, 0
    return items[:limit], len(items) - limit


def _text_body(
    digest: Digest,
    routine_limit: int,
    section_limit: int | None,
    links: list[tuple[str, str | None]],
    attachment_note: str,
) -> str:
    out = [f"Policy tracker digest - {clean(digest.period_label)}", f"Run {digest.run_id}", ""]
    out += [*_banners(digest), ""] if _banners(digest) else []
    groups = _grouped(digest.items)
    if not digest.items:
        out += [
            "No new or changed publications were found on tracked feeds this week.",
            "This heartbeat confirms that the tracker ran.",
            "",
        ]
    for key, heading in SECTIONS:
        items = groups[key]
        if not items:
            continue
        out += [f"== {heading} ({len(items)}) ==", ""]
        if key == "routine":
            shown, hidden = _limit(items, routine_limit)
            for item in shown:
                out.append(
                    f"- {clean(item.title)} | {item.feed_id} | "
                    f"{clean(item.published_raw) or 'not stated'} | {event_text(item)}"
                )
            if hidden:
                out.append(f"... and {hidden} more routine record(s) in the CSV and archive.")
            out.append("")
            continue
        shown, hidden = _limit(items, section_limit)
        for item in shown:
            out += _text_item(item)
        if hidden:
            out += [f"... and {hidden} more item(s) in the CSV and archive.", ""]
    if digest.notes:
        out += ["== Coverage notes ==", ""] + [f"- {clean(n)}" for n in digest.notes] + [""]
    out += ["== Source health ==", "", _health_summary(digest)]
    out += [f"- {line}" for line in _health_problems(digest)]
    out += ["", "== Archive ==", ""]
    out += [f"- {path}" + (f": {url}" if url else " (tracker-data branch)") for path, url in links]
    if attachment_note:
        out += ["", attachment_note]
    return "\n".join(out).rstrip("\n") + "\n"


def _text_item(item: DigestItem) -> list[str]:
    flag = "[DEADLINE CHANGED] " if item.deadline_changed else ""
    lines = [
        f"* {flag}{clean(item.title)}",
        (f"  {category_label(item)} | {item.site_id} / {item.feed_id} | {event_text(item)}"),
        f"  {published_text(item)}",
    ]
    deadline = deadline_text(item)
    if deadline:
        lines.append(f"  {deadline}")
        lines += [f"    Evidence: {clean(d.evidence)}" for d in _shown(item)]
    lines += [f"  Warning: {w}" for w in warning_texts(item)]
    url = safe_url(item.url)
    if url:
        lines.append(f"  {url}")
    return [*lines, ""]


_CSS = {
    "body": "font-family:Arial,Helvetica,sans-serif;font-size:14px;color:#1a1a1a;",
    "banner": "background:#fdecea;border-left:4px solid #b3261e;padding:8px;margin:8px 0;",
    "gap": "background:#fff4e5;border-left:4px solid #b26a00;padding:8px;margin:8px 0;",
    "action": "background:#fff8e1;border-left:4px solid #b26a00;padding:6px;margin:6px 0;",
    "item": "padding:6px;margin:6px 0;border-left:4px solid #d0d7de;",
    "flag": "background:#b3261e;color:#ffffff;padding:1px 4px;font-weight:bold;",
    "meta": "color:#555555;font-size:12px;",
    "warn": "color:#8a4b00;font-size:12px;",
    "table": "border-collapse:collapse;font-size:12px;",
    "cell": "border:1px solid #d0d7de;padding:2px 6px;text-align:left;",
}


def _e(text: str | None) -> str:
    return html.escape(clean(text), quote=True)


def _a(text: str, url: str | None) -> str:
    safe = safe_url(url)
    return f'<a href="{html.escape(safe, quote=True)}">{_e(text)}</a>' if safe else _e(text)


def _html_body(
    digest: Digest,
    routine_limit: int,
    section_limit: int | None,
    links: list[tuple[str, str | None]],
    attachment_note: str,
) -> str:
    out = [
        "<!DOCTYPE html>",
        '<html><head><meta charset="utf-8"><title>Policy tracker digest</title></head>',
        f'<body style="{_CSS["body"]}">',
        f"<h1>Policy tracker digest: {_e(digest.period_label)}</h1>",
        (
            f'<p style="{_CSS["meta"]}">Run {_e(digest.run_id)}, generated '
            f"{_e(digest.generated_at)}. {_e(_headline(digest))}.</p>"
        ),
    ]
    if digest.partial:
        out.append(f'<p style="{_CSS["banner"]}"><strong>{_e(_banners(digest)[0])}</strong></p>')
    if digest.gap_warning:
        out.append(
            f'<p style="{_CSS["gap"]}"><strong>GAP WARNING:</strong> {_e(digest.gap_warning)}</p>'
        )
    if not digest.items:
        out.append(
            "<p>No new or changed publications were found on tracked feeds this week. "
            "This heartbeat confirms that the tracker ran.</p>"
        )
    groups = _grouped(digest.items)
    for key, heading in SECTIONS:
        items = groups[key]
        if not items:
            continue
        out.append(f"<h2>{_e(heading)} ({len(items)})</h2>")
        if key == "routine":
            shown, hidden = _limit(items, routine_limit)
            if shown:
                cell = _CSS["cell"]
                out.append(
                    f'<table style="{_CSS["table"]}"><tr><th style="{cell}">Title</th>'
                    f'<th style="{cell}">Feed</th><th style="{cell}">Published</th>'
                    f'<th style="{cell}">Event</th></tr>'
                )
                for item in shown:
                    out.append(
                        f'<tr><td style="{cell}">{_a(item.title, item.url)}</td>'
                        f'<td style="{cell}">{_e(item.feed_id)}</td>'
                        f'<td style="{cell}">{_e(item.published_raw) or "not stated"}</td>'
                        f'<td style="{cell}">{_e(event_text(item))}</td></tr>'
                    )
                out.append("</table>")
            if hidden:
                out.append(
                    f"<p>... and {hidden} more routine record(s) in the CSV and archive.</p>"
                )
            continue
        shown, hidden = _limit(items, section_limit)
        for item in shown:
            out.append(_html_item(item, highlight=key == "action"))
        if hidden:
            out.append(f"<p>... and {hidden} more item(s) in the CSV and archive.</p>")
    if digest.notes:
        out.append("<h2>Coverage notes</h2><ul>")
        out += [f"<li>{_e(n)}</li>" for n in digest.notes]
        out.append("</ul>")
    out.append(f"<h2>Source health</h2><p>{_e(_health_summary(digest))}</p>")
    problems = _health_problems(digest)
    if problems:
        out.append("<ul>" + "".join(f"<li>{_e(p)}</li>" for p in problems) + "</ul>")
    out.append("<h2>Archive</h2><ul>")
    for path, url in links:
        out.append(f"<li>{_a(path, url)}</li>" if url else f"<li>{_e(path)} (tracker-data)</li>")
    out.append("</ul>")
    if attachment_note:
        out.append(f"<p>{_e(attachment_note)}</p>")
    out.append("</body></html>")
    return "\n".join(out) + "\n"


def _html_item(item: DigestItem, *, highlight: bool) -> str:
    style = _CSS["action"] if highlight else _CSS["item"]
    flag = f'<span style="{_CSS["flag"]}">DEADLINE CHANGED</span> ' if item.deadline_changed else ""
    parts = [
        f'<div style="{style}">',
        f"<div>{flag}<strong>{_a(item.title, item.url)}</strong></div>",
        (
            f'<div style="{_CSS["meta"]}">{_e(category_label(item))} | '
            f"{_e(item.site_id)} / {_e(item.feed_id)} | {_e(event_text(item))}</div>"
        ),
        f'<div style="{_CSS["meta"]}">{_e(published_text(item))}</div>',
    ]
    deadline = deadline_text(item)
    if deadline:
        parts.append(f"<div><strong>{_e(deadline)}</strong></div>")
        parts += [
            f'<div style="{_CSS["meta"]}">Evidence: {_e(d.evidence)}</div>' for d in _shown(item)
        ]
    parts += [f'<div style="{_CSS["warn"]}">Warning: {_e(w)}</div>' for w in warning_texts(item)]
    parts.append("</div>")
    return "".join(parts)


def _mime_size(subject: str, text: str, html_body: str, files: Sequence[RenderedFile]) -> int:
    message = EmailMessage()
    message["Subject"] = subject
    message.set_content(text, charset="utf-8")
    message.add_alternative(html_body, subtype="html", charset="utf-8")
    for f in files:
        maintype, _, subtype = f.media_type.partition("/")
        message.add_attachment(
            f.content, maintype=maintype, subtype=subtype, filename=f.path.rsplit("/", 1)[-1]
        )
    return len(message.as_bytes())


def render_email(
    digest: Digest,
    *,
    attach_csv: bool = True,
    max_bytes: int = MAX_EMAIL_BYTES,
    artifact_base_url: str | None = None,
) -> RenderedEmail:
    """Plain-text and HTML email; CSVs attached only while the message stays under the cap."""

    subject = _subject(digest)
    links = _links(digest, artifact_base_url)
    attachments = (render_csv(digest), render_open_deadlines_csv(digest)) if attach_csv else ()
    too_big = (
        "The CSV files are not attached because the message would exceed "
        f"{max_bytes // (1024 * 1024)} MiB; use the archive links above."
    )
    attached = "The digest CSV and open_deadlines.csv are attached."
    best: RenderedEmail | None = None
    for section_limit in SECTION_EMAIL_LIMITS:
        for routine_limit in ROUTINE_EMAIL_LIMITS:
            if attachments:
                text = _text_body(digest, routine_limit, section_limit, links, attached)
                body = _html_body(digest, routine_limit, section_limit, links, attached)
                size = _mime_size(subject, text, body, attachments)
                if size < max_bytes:
                    return RenderedEmail(subject, text, body, attachments, size)
            note = too_big if attachments else ""
            text = _text_body(digest, routine_limit, section_limit, links, note)
            body = _html_body(digest, routine_limit, section_limit, links, note)
            size = _mime_size(subject, text, body, ())
            best = RenderedEmail(subject, text, body, (), size)
            if size < max_bytes:
                return best
    assert best is not None
    return best
