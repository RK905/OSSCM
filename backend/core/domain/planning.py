import math
import statistics
from datetime import timedelta
from decimal import Decimal, ROUND_CEILING
from django.db import transaction
from django.db.models import F, Sum
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from core.models import SupplyPolicy, ForecastRun, DocumentLine, Movement, StockBalance, AuditEvent
from .common import get, require, member, record, amount
from .orders import create_document


def predict(method, history, horizon):
    if not history:
        return [0.0] * horizon
    if method == 'seasonal_naive':
        return [float(history[-52 + i % 52]) for i in range(horizon)]
    if method == 'exponential_smoothing':
        level = float(history[0])
        for value in history[1:]:
            level = .25 * float(value) + .75 * level
        return [max(0, level)] * horizon
    # Syntetos–Boylan corrected Croston forecast for intermittent demand.
    size, interval, gap = 0.0, 0.0, 1
    for value in history:
        if value > 0:
            if interval == 0:
                size, interval = float(value), float(gap)
            else:
                size += .1 * (float(value) - size)
                interval += .1 * (gap - interval)
            gap = 1
        else:
            gap += 1
    return [max(0, .95 * size / interval) if interval else 0.0] * horizon


def evaluate(history, horizon=8):
    if len(history) < 8:
        return {'method': 'manual_reorder', 'reason': 'At least eight complete weeks of demand are required.', 'forecast': [], 'metrics': None, 'intervals': None, 'candidates': {}}
    methods = ['exponential_smoothing', 'croston_sba']
    if len(history) >= 60:
        methods.append('seasonal_naive')
    start = max(52 if 'seasonal_naive' in methods else 4, len(history) - 12)
    candidates = {}
    residuals_by_method = {}
    for method in methods:
        errors = []
        actual = []
        for split in range(start, len(history)):
            forecast = predict(method, history[:split], 1)[0]
            errors.append(forecast - history[split])
            actual.append(history[split])
        absolute = sum(abs(x) for x in errors)
        candidates[method] = {'mae': absolute / len(errors), 'bias': statistics.mean(errors),
                              'wape': absolute / sum(actual) if sum(actual) else None, 'validation_weeks': len(errors)}
        residuals_by_method[method] = errors
    winner = min(methods, key=lambda x: (candidates[x]['mae'], x))
    forecast = predict(winner, history, horizon)
    residuals = residuals_by_method[winner]
    intervals = None
    if len(residuals) >= 8:
        # Explicitly approximate, uncalibrated intervals, widening with horizon.
        scale = math.sqrt(statistics.mean(x * x for x in residuals))
        intervals = [[max(0, f - 1.645 * scale * math.sqrt(i + 1)), f + 1.645 * scale * math.sqrt(i + 1)] for i, f in enumerate(forecast)]
    return {'method': winner, 'forecast': forecast, 'metrics': candidates[winner], 'candidates': candidates,
            'intervals': intervals, 'interval_note': 'Approximate 90% residual-based intervals; coverage is not guaranteed.' if intervals else 'Insufficient validation history for intervals.'}


def monday(day):
    return day - timedelta(days=day.weekday())


def demand_history(policy, today=None):
    end = monday(today or timezone.localdate())
    lines = DocumentLine.objects.filter(product=policy.product, document__warehouse=policy.warehouse, document__kind='sale', document__approved_at__isnull=False, document__approved_at__date__lt=end).select_related('document')
    first = lines.order_by('document__approved_at').first()
    if not first:
        return [], [], [], end, end
    start = max(monday(first.document.approved_at.date()), end - timedelta(weeks=156))
    weeks = (end - start).days // 7
    demand, cancelled, returns = [0.0] * weeks, [0.0] * weeks, [0.0] * weeks
    for line in lines:
        index = (monday(line.document.approved_at.date()) - start).days // 7
        if 0 <= index < weeks:
            demand[index] += float(line.quantity)
    # Cancellations are dated when they occurred rather than retroactively rewriting demand.
    for event in AuditEvent.objects.filter(organization=policy.organization, action='sale.cancelled', created_at__date__gte=start, created_at__date__lt=end, data__warehouse=policy.warehouse_id, data__was_approved=True):
        index = (monday(event.created_at.date()) - start).days // 7
        for entry in event.data.get('lines', []):
            if entry['product'] == policy.product_id:
                cancelled[index] += float(entry['quantity'])
    for movement in Movement.objects.filter(balance__identity__product=policy.product, balance__bin__warehouse=policy.warehouse, kind='customer_return', created_at__date__gte=start, created_at__date__lt=end):
        index = (monday(movement.created_at.date()) - start).days // 7
        returns[index] += float(movement.quantity)
    return demand, cancelled, returns, start, end


@transaction.atomic
def forecast(actor, data):
    require(actor, 'purchasing')
    policy = get(actor, SupplyPolicy, data['policy'])
    demand, cancellations, returns, start, end = demand_history(policy)
    horizon = max(8, math.ceil((policy.lead_days + policy.review_days) / 7))
    result = evaluate(demand, horizon)
    available = StockBalance.objects.filter(identity__product=policy.product, bin__warehouse=policy.warehouse, state='available').exclude(identity__expires_on__lt=timezone.localdate()).aggregate(n=Sum(F('on_hand') - F('reserved')))['n'] or Decimal(0)
    inbound = DocumentLine.objects.filter(product=policy.product, document__warehouse=policy.warehouse, document__kind='purchase', document__status__in=['approved', 'partial']).aggregate(n=Sum(F('quantity') - F('processed') - F('cancelled')))['n'] or Decimal(0)
    if result['method'] == 'manual_reorder':
        target = policy.target_stock if available + inbound <= policy.reorder_point else Decimal(0)
    else:
        weeks = Decimal(policy.lead_days + policy.review_days) / 7
        full = int(weeks)
        target = Decimal(str(sum(result['forecast'][:full]))) + (weeks - full) * Decimal(str(result['forecast'][full] if full < len(result['forecast']) else 0)) + policy.safety_stock
    needed = max(Decimal(0), target - available - inbound)
    recommended = ((max(needed, policy.minimum_order) / policy.pack_size).to_integral_value(rounding=ROUND_CEILING) * policy.pack_size) if needed else Decimal(0)
    # Reconstruct end-of-week availability; a warning, not an assertion of lost demand.
    moves = Movement.objects.filter(balance__identity__product=policy.product, balance__bin__warehouse=policy.warehouse, balance__state='available', created_at__date__lt=end).order_by('created_at')
    availability, stockout_weeks, cursor = Decimal(0), [], 0
    changes = list(moves.values_list('created_at', 'quantity', 'reserved_delta'))
    for i in range(len(demand)):
        boundary = start + timedelta(weeks=i + 1)
        while cursor < len(changes) and changes[cursor][0].date() < boundary:
            availability += changes[cursor][1] - changes[cursor][2]
            cursor += 1
        if availability <= 0:
            stockout_weeks.append((start + timedelta(weeks=i)).isoformat())
    result.update({'training_start': start.isoformat(), 'training_end_exclusive': end.isoformat(), 'horizon_weeks': horizon,
                   'history': demand, 'cancellations': cancellations, 'returns': returns, 'stockout_weeks': stockout_weeks,
                   'data_quality_note': 'Stockout flags reflect nonpositive week-end availability; observed orders may understate demand. Forecasts use accepted gross order demand; cancellations and returns are reported separately in their occurrence week, without rewriting prior demand.',
                   'available': str(available), 'confirmed_inbound': str(inbound), 'target': str(target.quantize(Decimal('.0001'))),
                   'recommended': str(recommended), 'explanation': 'Base units: target less available and confirmed inbound, rounded up to minimum order and pack size. Review inbound due dates before approval.'})
    run = ForecastRun.objects.create(organization=member(actor).organization, policy=policy, result=result, recommended=amount(recommended, False))
    record(actor, 'planning.forecast_created', run)
    return {'id': run.pk, **result}


@transaction.atomic
def accept(actor, data):
    require(actor, 'purchasing')
    run = get(actor, ForecastRun, data['forecast'], lock=True)
    policy = get(actor, SupplyPolicy, run.policy_id)
    if run.document_id or run.recommended <= 0:
        raise ValidationError('Recommendation is already accepted or has no quantity to order.')
    if timezone.now() - run.created_at > timedelta(hours=24):
        raise ValidationError('Recommendation is older than 24 hours. Run a fresh forecast.')
    result = create_document(actor, {'kind': 'purchase', 'reference': data['reference'], 'partner': policy.supplier_id,
                                     'warehouse': policy.warehouse_id, 'notes': f'Forecast {run.pk}: {run.result["explanation"]}',
                                     'lines': [{'product': policy.product_id, 'quantity': str(run.recommended)}]})
    run.document_id = result['id']
    run.save(update_fields=['document'])
    record(actor, 'planning.recommendation_accepted', run, {'document': run.document_id})
    return result
