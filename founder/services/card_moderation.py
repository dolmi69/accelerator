"""Checks the published text of a card after a complaint and withdraws it on a rule violation.

Only the text the community sees (published_data) is checked; drafts are never scanned.
No model is called: the check is local regular expressions.
"""
from django.db.models import F
from django.utils import timezone

from founder.models import ProjectCard
from founder.services.card_rules import first_violation


def published_text(published):
    """Every text value of the published snapshot, in a stable order (radar block and other non-text skipped)."""
    return [value for key, value in sorted(published.items()) if isinstance(value, str) and value.strip()]


def find_violation(published):
    """Returns the first matching rule for the published card, or None."""
    for text in published_text(published or {}):
        rule = first_violation(text)
        if rule:
            return rule
    return None


def withdraw(card, rule):
    """Takes a published card off the feed and blocks it for good.

    Returns True when this call made the change; False if the card was already withdrawn.
    """
    updated = ProjectCard.objects.filter(pk=card.pk, published_at__isnull=False).update(
        published_at=None, published_data={}, revision=F('revision') + 1,
        blocked_at=timezone.now(), block_rule=rule.code, block_reason=rule.category)
    return updated == 1
