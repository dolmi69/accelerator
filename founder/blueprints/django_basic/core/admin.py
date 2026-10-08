"""Domain content is visible to the site's owner through Django admin."""
from django.contrib import admin
from .models import Asset, Booking, Item, Lead, Membership, Page, Profile, Review, Slot

for model in (Asset, Booking, Item, Lead, Membership, Page, Profile, Review, Slot):
    admin.site.register(model)

# Orders and payments are changed through transactional services, not raw admin edits.
