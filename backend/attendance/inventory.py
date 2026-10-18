"""College property, through its life (said 17 October 2026).

    registered ──► in use ──► transferred / handed over (custodian changes)
                     │
                     ├──► repair reported ──► under repair ──► repaired: condition good
                     │                                     └─► beyond repair: unserviceable
                     ├──► inspected: a defect changes the condition, missing is marked
                     │    missing, found elsewhere is a transfer — applied when the
                     │    inspection is closed (the review); closed, it is locked
                     │
                     └──► disposal proposed (estate) ──► approved / rejected (DPFPA)
                                                      └─► carried out: leaves the register

Nothing here is edited after it happens: a transfer, a repair, a disposal or an
inspection result stands, and a mistake is put right by another entry. Only an
asset in use moves, goes for repair, or is proposed for disposal.

Custody is a member of staff chosen from the accounts, not a typed name, and
what one person holds can be handed to another in one step.

The laboratory keeps its own stock book (reagents, glassware, consumables):
received, issued, written off; the balance is the sum.
"""

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Q, Sum
from django.utils import timezone

from . import notifications
from .models import (
    Asset, AssetDisposal, AssetHandover, AssetMaintenance, AssetTransfer, EstateOfficerProfile,
    InventoryInspection, InventoryInspectionItem, LabItem, LabStockMovement, Notification,
)
from .roles import can_approve_disposals, can_keep_lab_stock, is_estate_officer

User = get_user_model()


class InventoryError(ValueError):
    """Something the property rules do not allow; the message says what."""


# ── who ───────────────────────────────────────────────────────────────────────

def staff_directory():
    """Every active member of staff an asset can be in the care of."""
    from .views import ROLE_LABELS, full_name_for, roles_for
    rows = []
    for user in User.objects.filter(is_active=True).order_by('username'):
        roles = roles_for(user)
        if not roles:
            continue
        rows.append({'id': user.id, 'name': full_name_for(user), 'username': user.username,
                     'title': ROLE_LABELS.get(roles[0], roles[0])})
    return sorted(rows, key=lambda row: row['name'].lower())


def custodian_label(user):
    from .views import full_name_for
    return full_name_for(user) if user else ''


def _staff(user_or_id, *, field='custodian'):
    """A member of staff with an office, or an error naming the field."""
    if user_or_id in (None, ''):
        raise InventoryError('Choose the member of staff it is in the care of.')
    user = user_or_id if isinstance(user_or_id, User) else User.objects.filter(pk=user_or_id).first()
    if user is None or not user.is_active or user.id not in {row['id'] for row in staff_directory()}:
        raise InventoryError('Choose a member of staff from the list.')
    return user


def estate_officers():
    return [p.user for p in EstateOfficerProfile.objects.filter(is_active=True, user__is_active=True)
            .select_related('user')]


def dpfpa_users():
    return notifications._users_holding(notifications.DeputyFinanceProfile)


# ── splitting a batch ─────────────────────────────────────────────────────────

def _split(asset, quantity, actor, **changes):
    """The part of a batch an action applies to: the asset itself when it is
    the whole batch, otherwise a new record under a traceable tag."""
    if quantity < 1 or quantity > asset.quantity:
        raise InventoryError(f'Only {asset.quantity} item(s) are in {asset.asset_tag}.')
    if quantity == asset.quantity:
        for field, value in changes.items():
            setattr(asset, field, value)
        asset.updated_by = actor
        asset.save()
        return asset
    suffix = 1
    while Asset.objects.filter(asset_tag=f'{asset.asset_tag}/{suffix:03d}').exists():
        suffix += 1
    fields = {f.name: getattr(asset, f.name) for f in Asset._meta.concrete_fields
              if f.name not in ('id', 'asset_tag', 'quantity', 'created_by', 'updated_by', 'created_at', 'updated_at')}
    fields.update(changes)
    part = Asset.objects.create(asset_tag=f'{asset.asset_tag}/{suffix:03d}', quantity=quantity,
                                created_by=actor, updated_by=actor, **fields)
    asset.quantity -= quantity
    asset.updated_by = actor
    asset.save(update_fields=['quantity', 'updated_by', 'updated_at'])
    return part


def _in_use(asset, doing):
    asset = Asset.objects.select_for_update().get(pk=asset.pk)
    if asset.status != Asset.ACTIVE:
        raise InventoryError(f'{asset.asset_tag} is {asset.get_status_display().lower()}: it cannot be {doing}.')
    return asset


# ── moving and handing over ───────────────────────────────────────────────────

@transaction.atomic
def transfer(asset, *, to_location, quantity, custodian, reason, transferred_at, actor):
    asset = _in_use(asset, 'transferred')
    if asset.location_id == to_location.id:
        raise InventoryError('Choose a different destination location.')
    if not to_location.is_active:
        raise InventoryError(f'{to_location.name} is no longer in use.')
    if not (reason or '').strip():
        raise InventoryError('Say why it is moving.')
    custodian = _staff(custodian)
    from_location = asset.location
    moved = _split(asset, quantity, actor, location=to_location, custodian=custodian,
                   responsible_office=custodian_label(custodian))
    return AssetTransfer.objects.create(
        asset=asset, from_location=from_location, to_location=to_location, quantity=quantity,
        resulting_asset=moved, new_responsible_office=custodian_label(custodian), new_custodian=custodian,
        reason=reason.strip(), transferred_at=transferred_at, recorded_by=actor)


@transaction.atomic
def handover(*, from_user, to_user, assets=None, note='', handed_at=None, actor):
    """Hand what one member of staff holds to another. `assets` narrows it;
    left out, it is everything in their care that has not left the register."""
    to_user = _staff(to_user)
    if from_user is None and not assets:
        raise InventoryError('Choose the items: records from before custodians were kept are handed over one by one.')
    if from_user is not None and from_user.id == to_user.id:
        raise InventoryError('Hand it to somebody else.')
    held = Asset.objects.select_for_update().exclude(status=Asset.DISPOSED)
    held = held.filter(custodian=from_user) if from_user is not None else held.filter(custodian__isnull=True)
    if assets is not None:
        held = held.filter(id__in=[a.id if hasattr(a, 'id') else a for a in assets])
    held = list(held)
    if not held:
        raise InventoryError('Nothing in their care to hand over.')
    record = AssetHandover.objects.create(from_user=from_user, to_user=to_user, note=(note or '')[:300],
                                          handed_at=handed_at or timezone.localdate(), recorded_by=actor)
    record.assets.set(held)
    Asset.objects.filter(id__in=[a.id for a in held]).update(
        custodian=to_user, responsible_office=custodian_label(to_user), updated_by=actor,
        updated_at=timezone.now())
    return record


# ── repairs ───────────────────────────────────────────────────────────────────

@transaction.atomic
def report_repair(asset, *, quantity, issue, reported_date, provider='', actor):
    asset = _in_use(asset, 'sent for repair')
    if not (issue or '').strip():
        raise InventoryError('Describe what is wrong.')
    part = _split(asset, quantity, actor, status=Asset.UNDER_REPAIR)
    return AssetMaintenance.objects.create(asset=part, quantity=quantity, issue=issue.strip(),
                                           reported_date=reported_date, provider=(provider or '').strip(),
                                           recorded_by=actor)


@transaction.atomic
def start_repair(record, *, provider='', actor=None):
    record = AssetMaintenance.objects.select_for_update().get(pk=record.pk)
    if record.status != AssetMaintenance.REPORTED:
        raise InventoryError(f'This repair is already {record.get_status_display().lower()}.')
    record.status = AssetMaintenance.IN_PROGRESS
    if provider:
        record.provider = provider.strip()[:200]
    record.save(update_fields=['status', 'provider'])
    return record


@transaction.atomic
def complete_repair(record, *, outcome, action_taken, completed_date, cost=0, provider='', actor):
    """Repaired: back in use in good condition. Beyond repair: back on the
    register as unserviceable, for the estate officer to propose disposing of."""
    record = AssetMaintenance.objects.select_for_update().select_related('asset').get(pk=record.pk)
    if record.status == AssetMaintenance.COMPLETED:
        raise InventoryError('This repair is already completed.')
    if outcome not in (AssetMaintenance.REPAIRED, AssetMaintenance.BEYOND_REPAIR):
        raise InventoryError('Say whether it was repaired or is beyond repair.')
    if not (action_taken or '').strip():
        raise InventoryError('Say what was done.')
    if completed_date is None or completed_date < record.reported_date:
        raise InventoryError('The completion date cannot be before the repair was reported.')
    record.status = AssetMaintenance.COMPLETED
    record.outcome = outcome
    record.action_taken = action_taken.strip()
    record.completed_date = completed_date
    record.cost = Decimal(str(cost or 0))
    if provider:
        record.provider = provider.strip()[:200]
    record.save()
    asset = record.asset
    asset.status = Asset.ACTIVE
    asset.condition = Asset.GOOD if outcome == AssetMaintenance.REPAIRED else Asset.UNSERVICEABLE
    asset.updated_by = actor
    asset.save(update_fields=['status', 'condition', 'updated_by', 'updated_at'])
    return record


# ── inspections ───────────────────────────────────────────────────────────────

DEFECT_CONDITIONS = (Asset.FAIR, Asset.POOR, Asset.UNSERVICEABLE)


def _open(inspection):
    if inspection.status != InventoryInspection.OPEN:
        raise InventoryError('This inspection is closed: its results cannot be changed.')


@transaction.atomic
def record_result(inspection, asset, *, result, new_condition='', found_location=None, note=''):
    """One asset's result, while the inspection is open. Nothing changes on the
    register until the inspection is closed."""
    inspection = InventoryInspection.objects.select_for_update().get(pk=inspection.pk)
    _open(inspection)
    if asset.location_id != inspection.location_id or asset.status == Asset.DISPOSED:
        raise InventoryError(f'{asset.asset_tag} is not on the register at {inspection.location.name}.')
    if result not in dict(InventoryInspectionItem.RESULT_CHOICES):
        raise InventoryError('Found, missing, damaged or relocated.')
    if result == InventoryInspectionItem.DAMAGED and new_condition not in DEFECT_CONDITIONS:
        raise InventoryError('A defect found: say what condition it is in now (fair, poor or unserviceable).')
    if result == InventoryInspectionItem.RELOCATED:
        if found_location is None or found_location.id == inspection.location_id:
            raise InventoryError('Relocated: say where it was found.')
    item, _ = InventoryInspectionItem.objects.update_or_create(
        inspection=inspection, asset=asset,
        defaults={'result': result, 'note': (note or '')[:1000],
                  'new_condition': new_condition if result == InventoryInspectionItem.DAMAGED else '',
                  'found_location': found_location if result == InventoryInspectionItem.RELOCATED else None})
    return item


def inspection_assets(inspection):
    """What the inspection must account for: everything on the register at
    that location when it is held."""
    return Asset.objects.filter(location=inspection.location).exclude(status=Asset.DISPOSED)


@transaction.atomic
def close_inspection(inspection, *, actor):
    """The review: every asset at the location accounted for, then the results
    applied to the register, and the inspection locked."""
    inspection = InventoryInspection.objects.select_for_update().select_related('location').get(pk=inspection.pk)
    _open(inspection)
    items = {item.asset_id: item for item in inspection.items.select_related('asset', 'found_location')}
    unchecked = [a.asset_tag for a in inspection_assets(inspection) if a.id not in items]
    if unchecked:
        raise InventoryError(f'{len(unchecked)} item(s) not checked yet: {", ".join(unchecked[:10])}'
                             + (' …' if len(unchecked) > 10 else '') + '.')
    for item in items.values():
        asset = Asset.objects.select_for_update().get(pk=item.asset_id)
        if item.result == InventoryInspectionItem.DAMAGED:
            asset.condition = item.new_condition
            if asset.status == Asset.MISSING:
                asset.status = Asset.ACTIVE
        elif item.result == InventoryInspectionItem.MISSING:
            if asset.status == Asset.ACTIVE:
                asset.status = Asset.MISSING
        elif item.result == InventoryInspectionItem.FOUND:
            if asset.status == Asset.MISSING:
                asset.status = Asset.ACTIVE
        elif item.result == InventoryInspectionItem.RELOCATED:
            AssetTransfer.objects.create(
                asset=asset, from_location=asset.location, to_location=item.found_location,
                quantity=asset.quantity, resulting_asset=asset,
                new_responsible_office=asset.responsible_office, new_custodian=asset.custodian,
                reason=f'Found at {item.found_location.name} during the inspection of '
                       f'{inspection.location.name} on {inspection.inspection_date:%d %b %Y}.',
                transferred_at=inspection.inspection_date, recorded_by=actor)
            asset.location = item.found_location
            if asset.status == Asset.MISSING:
                asset.status = Asset.ACTIVE
        asset.updated_by = actor
        asset.save()
    inspection.status = InventoryInspection.CLOSED
    inspection.closed_by = actor
    inspection.closed_at = timezone.now()
    inspection.save(update_fields=['status', 'closed_by', 'closed_at'])
    return inspection


# ── disposal ──────────────────────────────────────────────────────────────────

@transaction.atomic
def propose_disposal(asset, *, quantity, reason, method='', proposed_date, actor):
    asset = _in_use(asset, 'proposed for disposal')
    if not (reason or '').strip():
        raise InventoryError('Say why it should be disposed of.')
    part = _split(asset, quantity, actor, status=Asset.PENDING_DISPOSAL)
    disposal = AssetDisposal.objects.create(asset=part, quantity=quantity, reason=reason.strip(),
                                            method=(method or '').strip()[:160], proposed_date=proposed_date,
                                            recorded_by=actor)
    notifications.notify(dpfpa_users(), kind=Notification.DISPOSAL_PROPOSED,
                         title=f'Disposal to approve: {part.asset_tag}',
                         body=f'{part.name} × {quantity} — {disposal.reason[:150]}',
                         link='executive:disposals')
    return disposal


@transaction.atomic
def decide_disposal(disposal, *, approve, note='', actor):
    if not can_approve_disposals(actor):
        raise InventoryError('DPFPA approves disposals.')
    disposal = AssetDisposal.objects.select_for_update().select_related('asset').get(pk=disposal.pk)
    if disposal.status != AssetDisposal.PROPOSED:
        raise InventoryError(f'This disposal is already {disposal.get_status_display().lower()}.')
    note = (note or '').strip()
    if not approve and not note:
        raise InventoryError('Say why it is rejected.')
    disposal.status = AssetDisposal.APPROVED if approve else AssetDisposal.REJECTED
    disposal.decided_by = actor
    disposal.decided_at = timezone.now()
    disposal.decision_note = note[:300]
    disposal.save(update_fields=['status', 'decided_by', 'decided_at', 'decision_note'])
    if not approve:
        asset = disposal.asset
        asset.status = Asset.ACTIVE
        asset.updated_by = actor
        asset.save(update_fields=['status', 'updated_by', 'updated_at'])
    verdict = 'approved' if approve else 'rejected'
    notifications.notify(estate_officers(), kind=Notification.DISPOSAL_DECIDED,
                         title=f'Disposal {verdict}: {disposal.asset.asset_tag}',
                         body=note or ('Carry it out and record how.' if approve else ''),
                         link='inventory:disposal')
    return disposal


@transaction.atomic
def complete_disposal(disposal, *, disposal_date, method, reference='', proceeds=0, actor):
    """An approved disposal carried out: the asset leaves the register."""
    disposal = AssetDisposal.objects.select_for_update().select_related('asset').get(pk=disposal.pk)
    if disposal.status != AssetDisposal.APPROVED:
        raise InventoryError('Only a disposal DPFPA has approved can be carried out.')
    if not (method or '').strip():
        raise InventoryError('Say how it was disposed of (sold, scrapped, donated…).')
    if disposal_date is None or disposal_date < disposal.proposed_date:
        raise InventoryError('The disposal date cannot be before it was proposed.')
    disposal.status = AssetDisposal.DISPOSED
    disposal.disposal_date = disposal_date
    disposal.method = method.strip()[:160]
    disposal.reference = (reference or '').strip()[:160]
    disposal.proceeds = Decimal(str(proceeds or 0))
    disposal.save()
    asset = disposal.asset
    asset.status = Asset.DISPOSED
    asset.updated_by = actor
    asset.save(update_fields=['status', 'updated_by', 'updated_at'])
    return disposal


# ── the laboratory's stock book ───────────────────────────────────────────────

SIGN = {LabStockMovement.RECEIVED: 1, LabStockMovement.ISSUED: -1, LabStockMovement.WRITTEN_OFF: -1}


def lab_balance(item):
    totals = dict(item.movements.values_list('kind').annotate(total=Sum('quantity')))
    return sum((SIGN[kind] * (total or 0) for kind, total in totals.items()), Decimal('0'))


def lab_balances():
    rows = []
    totals = {}
    for item_id, kind, total in (LabStockMovement.objects.values_list('item_id', 'kind')
                                 .annotate(total=Sum('quantity'))):
        totals[item_id] = totals.get(item_id, Decimal('0')) + SIGN[kind] * (total or 0)
    today = timezone.localdate()
    expiring = {}
    for movement in (LabStockMovement.objects.filter(kind=LabStockMovement.RECEIVED, expiry_date__isnull=False)
                     .order_by('expiry_date')):
        expiring.setdefault(movement.item_id, movement.expiry_date)
    for item in LabItem.objects.all():
        balance = totals.get(item.id, Decimal('0'))
        soonest = expiring.get(item.id)
        rows.append({'id': item.id, 'name': item.name, 'category': item.category,
                     'category_display': item.get_category_display(), 'unit': item.unit,
                     'reorder_level': item.reorder_level, 'is_active': item.is_active, 'balance': balance,
                     'low': item.is_active and balance <= item.reorder_level,
                     'soonest_expiry': soonest,
                     'expired': bool(soonest and soonest < today)})
    return rows


@transaction.atomic
def record_lab_movement(item, *, kind, quantity, moved_on, actor, supplier='', unit_cost=None,
                        batch_number='', expiry_date=None, module=None, issued_to='', note=''):
    if not can_keep_lab_stock(actor):
        raise InventoryError("The laboratory's stock book is the lab technician's.")
    item = LabItem.objects.select_for_update().get(pk=item.pk)
    quantity = Decimal(str(quantity or 0))
    if quantity <= 0:
        raise InventoryError('The quantity must be more than nothing.')
    if kind == LabStockMovement.RECEIVED:
        if not (supplier or '').strip():
            raise InventoryError('Say who supplied it.')
    elif kind == LabStockMovement.ISSUED:
        if not ((issued_to or '').strip() or module):
            raise InventoryError('Say what it was issued for — the practical (module) or the person.')
    elif kind == LabStockMovement.WRITTEN_OFF:
        if not (note or '').strip():
            raise InventoryError('Say why it is written off (expired, broken, spilt…).')
    else:
        raise InventoryError('Received, issued or written off.')
    if kind != LabStockMovement.RECEIVED and quantity > lab_balance(item):
        raise InventoryError(f'Only {lab_balance(item)} {item.unit} of {item.name} in stock.')
    return LabStockMovement.objects.create(
        item=item, kind=kind, quantity=quantity, moved_on=moved_on, recorded_by=actor,
        supplier=(supplier or '').strip()[:200], unit_cost=unit_cost, batch_number=(batch_number or '').strip()[:80],
        expiry_date=expiry_date, module=module, issued_to=(issued_to or '').strip()[:200],
        note=(note or '').strip()[:300])
