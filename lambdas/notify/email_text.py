"""Subject and body of the notification email.

The notify Lambda (app.py) publishes one SNS message per file. SNS sends the
Subject and Message as a plain-text email to every subscriber whose filter
policy matches, and adds its own "unsubscribe" link at the bottom.

    from email_text import build_email

    sns.publish(
        TopicArn=TOPIC_ARN,
        MessageAttributes={...},   # the species attribute for the filter
        **build_email(file_id, {"fox": 2, "cat": 1}, SITE_URL,
                      filename="trailcam_01.jpg", file_type="image",
                      created_at="2026-10-14T09:30:00Z"),
    )

Only file_id and the tags are needed; the stream records carry those. The
other details come from the file's META item and are left out of the email
when they are missing.

The tags can come from automatic tagging or from a manual edit, so the text
says "tagged with" rather than "detected".

Kept free of AWS calls so it can be unit tested on its own. Lambda puts the
function's folder on sys.path, so `from email_text import ...` works in
app.py. tests/conftest.py's load_lambda() does not, so a test that loads
notify's app.py must add lambdas/notify to sys.path first (see
tests/test_notify.py). The module is not called `email.py` because that
would hide Python's own `email` package.
"""

import textwrap
from datetime import datetime, timezone

APP_NAME = "Aussie EcoGuard"
SUBJECT_MAX = 99         # SNS: fewer than 100 characters, no line breaks
SUBJECT_SPECIES_MAX = 3  # more species than this become "and N more"
LINE_WIDTH = 72          # plain-text email; the link is longer and stays on one line

# Species names are the labels in infra/model/config.json; these are the ones
# whose plural is not just "+s".
PLURALS = {
    "fox": "foxes",
    "deer": "deer",
    "cattle": "cattle",
}


def build_email(file_id, tags, site_url, filename=None, file_type=None, created_at=None):
    """Return {"Subject": ..., "Message": ...} for sns.publish(**...).

    tags: {"fox": 2, "cat": 1}, the file's new tags with their counts. Counts
    may be int, Decimal or str (DynamoDB stream records hold numbers as text).
    """
    tags = _clean_tags(tags)
    if not tags:
        raise ValueError("tags must have at least one species")
    return {
        "Subject": build_subject(tags),
        "Message": build_body(file_id, tags, site_url, filename, file_type, created_at),
    }


def build_subject(tags):
    """e.g. "Aussie EcoGuard: 2 foxes and 1 cat tagged"."""
    tags = _clean_tags(tags)
    phrases = [count_phrase(species, count) for species, count in tags]
    if len(phrases) > SUBJECT_SPECIES_MAX:
        shown = phrases[:SUBJECT_SPECIES_MAX - 1]
        phrases = shown + [f"{len(phrases) - len(shown)} more species"]
    subject = f"{APP_NAME}: {join_and(phrases)} tagged"
    subject = " ".join(subject.split())  # no line breaks or tabs
    if len(subject) > SUBJECT_MAX:
        subject = subject[:SUBJECT_MAX - 3].rstrip() + "..."
    return subject


def build_body(file_id, tags, site_url, filename=None, file_type=None, created_at=None):
    tags = _clean_tags(tags)
    kind = file_type if file_type in ("image", "video") else "file"
    width = max(len(species) for species, _ in tags)

    lines = [
        "Hello,",
        "",
        textwrap.fill(
            f"A{'n' if kind == 'image' else ''} {kind} in {APP_NAME} has "
            + ("a new tag that you subscribed to:" if len(tags) == 1
               else "new tags, including at least one species you subscribed to:"),
            LINE_WIDTH),
        "",
    ]
    lines += [f"  {species.ljust(width)}  {count}" for species, count in tags]
    lines.append("")

    details = []
    if filename:
        details.append(f"  File:      {filename}")
    uploaded = format_time(created_at)
    if uploaded:
        details.append(f"  Uploaded:  {uploaded}")
    if details:
        lines += details + [""]

    lines += [
        f"View the {kind} and all of its tags:",
        file_link(site_url, file_id),
        "",
        "You may need to sign in first.",
        "",
        textwrap.fill(
            f"You get this email because you subscribed to species on the "
            f"Notifications tab of {APP_NAME}. To stop these emails, use the "
            f"unsubscribe link below.",
            LINE_WIDTH),
        "",
        f"{APP_NAME}",
    ]
    return "\n".join(lines)


def file_link(site_url, file_id):
    """Link to the file page in the web app (index.html#/file/<sha256>).

    site_url is the website address, e.g. the `website_https_url` Terraform
    output. A hash route is used because the site is plain files on S3, which
    has no server to answer /file/<sha256>.
    """
    base = site_url.split("#", 1)[0]
    return f"{base}#/file/{file_id}"


def count_phrase(species, count):
    """2, "fox" -> "2 foxes"; 1, "cane toad" -> "1 cane toad"."""
    return f"{count} {species if count == 1 else plural(species)}"


def plural(species):
    if species in PLURALS:
        return PLURALS[species]
    if species.endswith(("s", "x", "ch", "sh")):
        return species + "es"
    return species + "s"


def join_and(items):
    if len(items) == 1:
        return items[0]
    return ", ".join(items[:-1]) + " and " + items[-1]


def format_time(created_at):
    """"2026-10-14T09:30:00Z" -> "14 Oct 2026, 09:30 UTC"; None if missing or unreadable."""
    if not created_at:
        return None
    try:
        when = datetime.fromisoformat(str(created_at).replace("Z", "+00:00"))
    except ValueError:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return when.astimezone(timezone.utc).strftime("%d %b %Y, %H:%M UTC").lstrip("0")


def _clean_tags(tags):
    """Dict or list of pairs -> [(species, int count)], most animals first, then by name."""
    pairs = tags.items() if isinstance(tags, dict) else tags
    cleaned = [(str(species).strip(), max(int(count), 1)) for species, count in pairs]
    return sorted(((s, c) for s, c in cleaned if s), key=lambda t: (-t[1], t[0]))
