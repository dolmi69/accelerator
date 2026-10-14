from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from core.catalog_examples import seed_catalog_examples, claim_catalog_examples
from core.models import Item, SiteControl

@override_settings(SITE={'name': 'Аренда инструментов', 'modules': ['accounts', 'catalog']})
class CatalogExampleTests(TestCase):
    def test_seeds_three_real_records_once_and_never_refills_deleted_items(self):
        self.assertEqual(seed_catalog_examples(), 3)
        self.assertEqual(set(Item.objects.values_list('title', flat=True)), {'Дрель', 'Перфоратор', 'Шлифмашина'})
        owner = Item.objects.first().owner
        self.assertFalse(owner.is_active)
        self.assertFalse(owner.has_usable_password())
        self.assertEqual(seed_catalog_examples(), 0)
        Item.objects.all().delete()
        self.assertEqual(seed_catalog_examples(), 0)
        self.assertEqual(Item.objects.count(), 0)

    def test_owner_claim_transfers_examples(self):
        seed_catalog_examples()
        owner = get_user_model().objects.create_user(username='real-owner')
        claim_catalog_examples(owner)
        self.assertEqual(Item.objects.filter(owner=owner).count(), 3)
        self.assertIsNone(SiteControl.objects.get(pk=1).catalog_example_owner_id)

    @override_settings(SITE={'name': 'Кофейня', 'modules': ['accounts']})
    def test_disabled_catalog_not_seeded_until_enabled(self):
        self.assertEqual(seed_catalog_examples(), 0)
        with override_settings(SITE={'name': 'Кофейня', 'modules': ['catalog']}):
            self.assertEqual(seed_catalog_examples(), 3)
        self.assertTrue(Item.objects.filter(title='Латте').exists())

    def test_existing_catalog_is_not_modified(self):
        owner = get_user_model().objects.create_user(username='owner')
        Item.objects.create(owner=owner, title='Своя позиция', description='Описание')
        self.assertEqual(seed_catalog_examples(), 0)
        self.assertEqual(Item.objects.count(), 1)
