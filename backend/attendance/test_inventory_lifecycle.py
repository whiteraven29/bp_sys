"""College property through its life (said 17 October 2026):

  * a disposed asset is not live: off the register, the figures, transfers,
    repairs and inspections; part of a batch can be disposed of;
  * DPFPA approves (or rejects, with a reason) what the estate officer
    proposes; only an approved disposal is carried out;
  * history is not edited: transfers, repairs, disposals and inspection results
    are never changed or deleted, an asset is never deleted, and where it is,
    who holds it, how many and its condition change only through what happens;
  * a repair marks the asset under repair; repaired sets it good, beyond repair
    unserviceable;
  * an inspection's results apply when it is closed (the review): a defect
    changes the condition, missing is marked, relocated becomes a transfer;
    closed, it cannot be changed;
  * value: cost, purchase date, supplier, funding; the register adds it up;
  * custody is a member of staff, and what one holds can be handed to another;
  * the laboratory's stock book belongs to the lab technician.
"""

from datetime import date

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from .models import (
    Asset, AssetCategory, AssetDisposal, AssetHandover, AssetMaintenance, AssetTransfer, InventoryInspection,
    InventoryLocation, LabItem, LabStockMovement, Notification,
)
from .views import set_roles

User = get_user_model()


def office(username, *roles):
    user = User.objects.create_user(username, password='pw')
    set_roles(user, list(roles), full_name=username.title())
    client = APIClient()
    client.force_authenticate(user)
    return user, client


class PropertyBase(TestCase):
    def setUp(self):
        self.estate_user, self.estate = office('estate', 'estate_officer')
        self.dpfpa_user, self.dpfpa = office('dpfpa', 'dpfpa')
        self.tutor_user, _ = office('mwalimu', 'tutor')
        self.office, _ = InventoryLocation.objects.get_or_create(name='Lifecycle Test Office')
        self.store, _ = InventoryLocation.objects.get_or_create(name='Lifecycle Test Store')
        self.category, _ = AssetCategory.objects.get_or_create(name='Lifecycle Test Furniture')
        self.chairs = self.register('BP/CH/1', quantity=10, unit_cost='45000')

    def register(self, tag, quantity=1, unit_cost=None, location=None, custodian=None):
        made = self.estate.post('/api/assets/', {
            'asset_tag': tag, 'name': 'Chair', 'category': self.category.id,
            'location': (location or self.office).id, 'custodian': (custodian or self.estate_user).id,
            'quantity': quantity, 'condition': 'good', 'purchase_date': '2026-01-15',
            **({'unit_cost': unit_cost} if unit_cost else {}), 'supplier': 'Kariakoo Furniture',
            'funding_source': 'college'}, format='json')
        self.assertEqual(made.status_code, 201, made.data)
        return Asset.objects.get(pk=made.data['id'])

    def fresh(self, asset):
        return Asset.objects.get(pk=asset.pk)


class RegisterTests(PropertyBase):
    def test_custody_is_a_member_of_staff(self):
        self.assertEqual(self.chairs.custodian, self.estate_user)
        self.assertEqual(self.chairs.responsible_office, 'Estate')
        refused = self.estate.post('/api/assets/', {
            'asset_tag': 'BP/X', 'name': 'Desk', 'category': self.category.id, 'location': self.office.id,
            'quantity': 1, 'condition': 'good', 'responsible_office': 'Somebody'}, format='json')
        self.assertEqual(refused.status_code, 400)
        self.assertIn('custodian', refused.data)

    def test_value_is_kept_and_added_up(self):
        self.assertEqual(self.estate.get(f'/api/assets/{self.chairs.id}/').data['total_value'], '450000.00')
        summary = self.dpfpa.get('/api/inventory/summary/').data
        self.assertEqual(summary['value'], 450000)

    def test_where_who_how_many_and_condition_are_not_edited(self):
        changed = self.estate.patch(f'/api/assets/{self.chairs.id}/', {
            'name': 'Visitor chair', 'location': self.store.id, 'quantity': 3, 'condition': 'poor',
            'custodian': self.dpfpa_user.id, 'status': 'disposed'}, format='json')
        self.assertEqual(changed.status_code, 200)
        chairs = self.fresh(self.chairs)
        self.assertEqual(chairs.name, 'Visitor chair')                       # a correction
        self.assertEqual((chairs.location, chairs.quantity, chairs.condition, chairs.custodian, chairs.status),
                         (self.office, 10, 'good', self.estate_user, Asset.ACTIVE))

    def test_nothing_is_deleted_and_history_is_not_edited(self):
        self.assertEqual(self.estate.delete(f'/api/assets/{self.chairs.id}/').status_code, 405)
        moved = self.estate.post('/api/asset-transfers/', {
            'asset': self.chairs.id, 'quantity': 2, 'to_location': self.store.id,
            'new_custodian': self.estate_user.id, 'reason': 'Store', 'transferred_at': '2026-10-17'}, format='json')
        self.assertEqual(moved.status_code, 201, moved.data)
        transfer = moved.data['id']
        self.assertEqual(self.estate.patch(f'/api/asset-transfers/{transfer}/', {'reason': 'x'}, format='json').status_code, 405)
        self.assertEqual(self.estate.delete(f'/api/asset-transfers/{transfer}/').status_code, 405)

    def test_dpfpa_reads_and_writes_nothing(self):
        self.assertEqual(self.dpfpa.get('/api/assets/').status_code, 200)
        self.assertEqual(self.dpfpa.patch(f'/api/assets/{self.chairs.id}/', {'name': 'x'}, format='json').status_code, 403)


class DisposalTests(PropertyBase):
    def propose(self, quantity=3, asset=None):
        return self.estate.post('/api/asset-disposals/', {
            'asset': (asset or self.chairs).id, 'quantity': quantity, 'reason': 'Broken beyond repair',
            'method': 'Scrap', 'proposed_date': '2026-10-17'}, format='json')

    def test_part_of_a_batch_waits_for_dpfpa_and_cannot_be_moved(self):
        made = self.propose()
        self.assertEqual(made.status_code, 201, made.data)
        disposal = AssetDisposal.objects.get()
        self.assertEqual((disposal.asset.quantity, disposal.asset.status), (3, Asset.PENDING_DISPOSAL))
        self.assertEqual(self.fresh(self.chairs).quantity, 7)
        self.assertTrue(Notification.objects.filter(kind=Notification.DISPOSAL_PROPOSED, user=self.dpfpa_user).exists())
        moved = self.estate.post('/api/asset-transfers/', {
            'asset': disposal.asset.id, 'to_location': self.store.id, 'new_custodian': self.estate_user.id,
            'reason': 'x', 'transferred_at': '2026-10-17'}, format='json')
        self.assertEqual(moved.status_code, 400)

    def test_dpfpa_approves_then_it_is_carried_out_and_leaves_the_register(self):
        self.propose()
        disposal = AssetDisposal.objects.get()
        self.assertEqual(self.estate.post(f'/api/asset-disposals/{disposal.id}/decide/', {'decision': 'approve'},
                                          format='json').status_code, 403)
        early = self.estate.post(f'/api/asset-disposals/{disposal.id}/complete/', {
            'disposal_date': '2026-10-20', 'method': 'Scrap'}, format='json')
        self.assertEqual(early.status_code, 400)                              # not approved yet
        approved = self.dpfpa.post(f'/api/asset-disposals/{disposal.id}/decide/', {'decision': 'approve'}, format='json')
        self.assertEqual(approved.status_code, 200, approved.data)
        self.assertTrue(Notification.objects.filter(kind=Notification.DISPOSAL_DECIDED, user=self.estate_user).exists())
        done = self.estate.post(f'/api/asset-disposals/{disposal.id}/complete/', {
            'disposal_date': '2026-10-20', 'method': 'Sold for scrap', 'reference': 'RCPT-77', 'proceeds': '15000'},
            format='json')
        self.assertEqual(done.status_code, 200, done.data)
        gone = disposal.asset
        self.assertEqual(self.fresh(gone).status, Asset.DISPOSED)
        listed = [a['id'] for a in self.estate.get('/api/assets/').data]
        self.assertNotIn(gone.id, listed)
        self.assertEqual(self.dpfpa.get('/api/inventory/summary/').data['items'], 7)
        for attempt in (
            self.estate.post('/api/asset-maintenance/', {'asset': gone.id, 'quantity': 3, 'issue': 'x',
                                                         'reported_date': '2026-10-21'}, format='json'),
            self.propose(asset=gone),
        ):
            self.assertEqual(attempt.status_code, 400)

    def test_a_rejection_needs_a_reason_and_puts_it_back_in_use(self):
        self.propose(quantity=10)
        disposal = AssetDisposal.objects.get()
        self.assertEqual(self.dpfpa.post(f'/api/asset-disposals/{disposal.id}/decide/', {'decision': 'reject'},
                                         format='json').status_code, 400)
        self.dpfpa.post(f'/api/asset-disposals/{disposal.id}/decide/', {'decision': 'reject', 'note': 'Repair them'},
                        format='json')
        self.assertEqual(self.fresh(self.chairs).status, Asset.ACTIVE)
        self.assertEqual(self.propose(quantity=10).status_code, 201)         # may be proposed again


class RepairTests(PropertyBase):
    def report(self, quantity=1):
        made = self.estate.post('/api/asset-maintenance/', {
            'asset': self.chairs.id, 'quantity': quantity, 'issue': 'Broken leg', 'reported_date': '2026-10-17'},
            format='json')
        self.assertEqual(made.status_code, 201, made.data)
        return AssetMaintenance.objects.get(pk=made.data['id'])

    def test_repaired_is_back_in_use_in_good_condition(self):
        repair = self.report(quantity=2)
        self.assertEqual(repair.asset.status, Asset.UNDER_REPAIR)
        self.assertEqual(self.estate.post(f'/api/asset-maintenance/{repair.id}/start/', {'provider': 'Juma Welding'},
                                          format='json').status_code, 200)
        done = self.estate.post(f'/api/asset-maintenance/{repair.id}/complete/', {
            'outcome': 'repaired', 'action_taken': 'Leg welded', 'completed_date': '2026-10-19', 'cost': '20000'},
            format='json')
        self.assertEqual(done.status_code, 200, done.data)
        part = self.fresh(repair.asset)
        self.assertEqual((part.status, part.condition), (Asset.ACTIVE, Asset.GOOD))
        self.assertEqual(self.estate.post(f'/api/asset-maintenance/{repair.id}/complete/', {
            'outcome': 'repaired', 'action_taken': 'x', 'completed_date': '2026-10-19'}, format='json').status_code, 400)
        self.assertEqual(self.estate.patch(f'/api/asset-maintenance/{repair.id}/', {'cost': '1'}, format='json').status_code, 405)

    def test_beyond_repair_is_unserviceable(self):
        repair = self.report()
        self.estate.post(f'/api/asset-maintenance/{repair.id}/complete/', {
            'outcome': 'beyond_repair', 'action_taken': 'Frame cracked', 'completed_date': '2026-10-19'}, format='json')
        self.assertEqual(self.fresh(repair.asset).condition, Asset.UNSERVICEABLE)


class InspectionTests(PropertyBase):
    def setUp(self):
        super().setUp()
        self.desk = self.register('BP/DK/1')
        self.cabinet = self.register('BP/CB/1')
        made = self.estate.post('/api/inventory-inspections/', {
            'location': self.office.id, 'inspection_date': '2026-10-17', 'inspector_name': 'Estate',
            'status': 'closed'}, format='json')
        self.inspection = InventoryInspection.objects.get(pk=made.data['id'])

    def result(self, asset, result, **extra):
        return self.estate.post('/api/inventory-inspection-items/', {
            'inspection': self.inspection.id, 'asset': asset.id, 'result': result, **extra}, format='json')

    def test_opened_open_and_a_defect_needs_its_condition(self):
        self.assertEqual(self.inspection.status, InventoryInspection.OPEN)    # cannot be created closed
        self.assertEqual(self.result(self.desk, 'damaged').status_code, 400)

    def test_closing_is_the_review_that_applies_the_results(self):
        self.result(self.chairs, 'damaged', new_condition='poor')
        self.result(self.desk, 'missing')
        self.result(self.cabinet, 'relocated', found_location=self.store.id)
        unchecked = self.register('BP/LT/1')
        refused = self.estate.post(f'/api/inventory-inspections/{self.inspection.id}/close/')
        self.assertEqual(refused.status_code, 400)
        self.assertIn('BP/LT/1', refused.data['detail'])
        self.result(unchecked, 'found')
        closed = self.estate.post(f'/api/inventory-inspections/{self.inspection.id}/close/')
        self.assertEqual(closed.status_code, 200, closed.data)
        self.assertEqual(self.fresh(self.chairs).condition, Asset.POOR)
        self.assertEqual(self.fresh(self.desk).status, Asset.MISSING)
        self.assertEqual(self.fresh(self.cabinet).location, self.store)
        self.assertTrue(AssetTransfer.objects.filter(asset=self.cabinet, to_location=self.store).exists())
        # Closed: locked.
        self.assertEqual(self.result(self.desk, 'found').status_code, 400)

    def test_a_missing_asset_found_at_the_next_inspection_is_back(self):
        Asset.objects.filter(pk=self.desk.pk).update(status=Asset.MISSING)
        for asset in (self.chairs, self.cabinet):
            self.result(asset, 'found')
        self.result(self.desk, 'found')
        self.estate.post(f'/api/inventory-inspections/{self.inspection.id}/close/')
        self.assertEqual(self.fresh(self.desk).status, Asset.ACTIVE)


class HandoverTests(PropertyBase):
    def test_everything_one_holds_goes_to_another(self):
        self.register('BP/DK/1')
        made = self.estate.post('/api/inventory/handovers/', {
            'from_user': self.estate_user.id, 'to_user': self.tutor_user.id, 'note': 'Leaving the college',
            'handed_at': '2026-10-17'}, format='json')
        self.assertEqual(made.status_code, 201, made.data)
        self.assertEqual(made.data['asset_count'], 2)
        self.assertEqual(set(Asset.objects.values_list('custodian', flat=True)), {self.tutor_user.id})
        self.assertEqual(Asset.objects.first().responsible_office, 'Mwalimu')
        self.assertEqual(AssetHandover.objects.get().assets.count(), 2)

    def test_only_to_a_member_of_staff(self):
        stranger = User.objects.create_user('nobody', password='pw')
        refused = self.estate.post('/api/inventory/handovers/', {
            'from_user': self.estate_user.id, 'to_user': stranger.id}, format='json')
        self.assertEqual(refused.status_code, 400)


class LabStockTests(TestCase):
    def setUp(self):
        self.lab_user, self.lab = office('lab', 'lab_technician')
        _, self.dpfpa = office('dpfpa', 'dpfpa')
        _, self.estate = office('estate', 'estate_officer')
        made = self.lab.post('/api/lab-items/', {'name': 'Ethanol 96%', 'category': 'reagent', 'unit': 'ml',
                                                 'reorder_level': '500'}, format='json')
        self.assertEqual(made.status_code, 201, made.data)
        self.item = LabItem.objects.get()

    def move(self, kind, quantity, **extra):
        return self.lab.post('/api/lab-movements/', {'item': self.item.id, 'kind': kind, 'quantity': quantity,
                                                     'moved_on': '2026-10-17', **extra}, format='json')

    def test_received_issued_written_off_and_the_balance(self):
        self.assertEqual(self.move('received', '2000', supplier='MSD', batch_number='B1',
                                   expiry_date='2027-06-30').status_code, 201)
        self.assertEqual(self.move('issued', '1200', issued_to='Level 5 practical').status_code, 201)
        self.assertEqual(self.move('issued', '5000', issued_to='Too much').status_code, 400)
        self.assertEqual(self.move('written_off', '100').status_code, 400)            # needs a reason
        self.assertEqual(self.move('written_off', '100', note='Spilt').status_code, 201)
        row = self.dpfpa.get('/api/lab-items/').data[0]
        self.assertEqual((float(row['balance']), row['low']), (700.0, False))
        self.move('issued', '300', issued_to='Level 4 practical')
        self.assertTrue(self.lab.get('/api/lab-items/').data[0]['low'])

    def test_the_stock_book_is_the_lab_technicians(self):
        self.assertEqual(self.estate.get('/api/lab-items/').status_code, 403)
        self.assertEqual(self.dpfpa.post('/api/lab-items/', {'name': 'x', 'unit': 'g'}, format='json').status_code, 403)
        self.move('received', '10', supplier='MSD')
        movement = LabStockMovement.objects.get()
        self.assertIn(self.lab.patch(f'/api/lab-movements/{movement.id}/', {'quantity': '1'},
                                     format='json').status_code, (404, 405))       # no way to edit an entry
        self.assertEqual(LabStockMovement.objects.get().quantity, 10)

    def test_lab_technician_is_an_open_role_the_ict_officer_gives(self):
        _, ict = office('ict', 'ict_officer')
        made = ict.post('/api/staff-accounts/', {'role': 'lab_technician', 'full_name': 'Lab Two',
                                                 'username': 'lab2', 'password': 'Lab#2026xx'}, format='json')
        self.assertEqual(made.status_code, 201, made.data)
