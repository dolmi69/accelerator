"""Complaints about published project cards and the "Опасно!" mark."""
from django.conf import settings
from django.db.models import Count

from founder.models import CardReport


def mark_report_state(cards, user):
    """Sets card.reported_by_me and card.is_dangerous for a page of cards (two queries)."""
    cards = list(cards)
    ids = [card.pk for card in cards]
    if not ids:
        return cards
    counts = dict(CardReport.objects.filter(card_id__in=ids).values_list('card_id').annotate(total=Count('pk')))
    mine = set(CardReport.objects.filter(card_id__in=ids, reporter=user).values_list('card_id', flat=True)) \
        if user.is_authenticated else set()
    for card in cards:
        card.reported_by_me = card.pk in mine
        card.is_dangerous = card.reported_by_me or counts.get(card.pk, 0) >= settings.CARD_REPORT_THRESHOLD
    return cards
