"""Comparison keys for title deduplication; original article fields stay intact."""


def title_key(title):
    """Collapse Unicode whitespace while preserving word boundaries and all other text."""
    return " ".join(title.split()) if isinstance(title, str) else title
