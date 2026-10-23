# Project Context

## Problem

Policy researchers currently need to revisit several Indian government websites
to find newly published or changed drafts, regulations, orders, reports, and
consultation opportunities. Relevant material may appear in broad “What’s New”
feeds, category archives, nested attachments on old pages, or evolving matter
rows. Dates and archive paths are not consistently reliable.

## Goal

Create a weekly, auditable tracker for the websites in `websites.txt`:

- Ministry of Power — <https://www.powermin.gov.in/>
- Ministry of Coal — <https://www.coal.nic.in/>
- Central Electricity Regulatory Commission — <https://cercind.gov.in/>

The tracker must first research each entire site and identify every place where
policy-relevant publications may appear. Only after the inventory is reviewed
should automated collection be implemented.

## Users and output

The primary audience is policy researchers. Each Monday run should provide:

- A concise email prioritizing actionable consultations and policy material.
- Complete Markdown, CSV, and JSON archives of selected-feed events.
- A compact appendix for routine high-volume records.
- Source-health and coverage warnings that make missing or uncertain data clear.

## Confirmed product choices

- Capture all selected-feed items and prioritize the digest.
- Run using GitHub Actions on Monday at 08:00 IST nominal time.
- Deliver by generic SMTP and archive files in a private GitHub repository.
- Use rule-based classification and summaries; no AI API.
- Process English and bilingual material. Preserve English listing metadata when
  a document itself is unavailable in English.
- Perform a silent full-history baseline.
- Send deadline information only when it is discovered or changed.
- Use metadata/validator-based checks for later same-URL PDF replacement.
- Retain generated history indefinitely; do not retain downloaded PDFs.

## Success criteria

The tracker is successful when new or changed publications are reported once,
routine presentation changes do not create alerts, feed failures do not erase
known state, partial baselines recover silently, and every digest can be traced
back to immutable provenance and run-health records.

## Important limitation

Under the selected metadata-only revalidation policy, a server can silently
replace a file without changing ETag, Last-Modified, or related page metadata.
Such a replacement may not be detected. Reports must not claim universal
same-URL replacement detection.
