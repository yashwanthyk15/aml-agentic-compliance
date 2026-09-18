import structlog
from datetime import datetime, timedelta
from typing import Any
from collections import defaultdict

from app.orchestration.state import DeterministicSignal

logger = structlog.get_logger(__name__)

class ScreeningRuleEngine:
    def __init__(self, config: dict | None = None):
        self.config = config or {}
        self.thresholds = {
            "structuring_amount": 50000,
            "structuring_hours": 24,
            "velocity_24h_count": 10,
            "velocity_7d_count": 30,
            "amount_multiplier": 3.0,
            "high_risk_countries": {"AF", "IR", "KP", "SY", "YE"}
        }
        self.thresholds.update(self.config)

    def screen_transactions(self, transactions: list[dict], customer: dict | None = None) -> list[DeterministicSignal]:
        """Run all deterministic rules against a set of transactions."""
        signals = []
        
        try:
            transactions = sorted(transactions, key=lambda x: x.get('timestamp', ''))
        except Exception as e:
            logger.warning("failed_to_sort_transactions", error=str(e))
            
        signals.extend(self._check_structuring(transactions))
        signals.extend(self._check_velocity(transactions))
        signals.extend(self._check_geo(transactions))
        signals.extend(self._check_amount(transactions, customer))
        signals.extend(self._check_counterparty(transactions, customer))
        
        return signals

    def _parse_time(self, t: Any) -> datetime:
        if isinstance(t, datetime):
            return t
        try:
            return datetime.fromisoformat(str(t).replace('Z', '+00:00'))
        except (ValueError, TypeError):
            return datetime.now()

    def _check_structuring(self, transactions: list[dict]) -> list[DeterministicSignal]:
        signals = []
        by_customer = defaultdict(list)
        for tx in transactions:
            by_customer[tx.get('customer_id', 'unknown')].append(tx)
            
        for cust_id, txs in by_customer.items():
            txs = sorted(txs, key=lambda x: self._parse_time(x.get('timestamp', datetime.now())))
            for i, tx in enumerate(txs):
                amount = float(tx.get('amount', 0))
                if amount >= self.thresholds["structuring_amount"]:
                    continue
                    
                window_start = self._parse_time(tx.get('timestamp', datetime.now()))
                window_end = window_start + timedelta(hours=self.thresholds["structuring_hours"])
                
                window_sum = amount
                window_txs = [tx.get('transaction_id')]
                
                for j in range(i+1, len(txs)):
                    next_tx = txs[j]
                    next_time = self._parse_time(next_tx.get('timestamp', datetime.now()))
                    if next_time <= window_end:
                        next_amt = float(next_tx.get('amount', 0))
                        if next_amt < self.thresholds["structuring_amount"]:
                            window_sum += next_amt
                            window_txs.append(next_tx.get('transaction_id'))
                    else:
                        break
                        
                if window_sum > self.thresholds["structuring_amount"] and len(window_txs) > 1:
                    signals.append(DeterministicSignal(
                        rule_id='STRUCT_001',
                        rule_name='Structuring Detection',
                        rule_type='INTERNAL_DEMO_RULE',
                        description=f'Multiple sub-threshold transactions totaling {window_sum:.0f} within 24h window',
                        triggered=True,
                        details={
                            "customer_id": cust_id,
                            "window_sum": window_sum,
                            "transaction_count": len(window_txs),
                            "transaction_ids": window_txs
                        }
                    ))
                    break
                    
        return signals

    def _check_velocity(self, transactions: list[dict]) -> list[DeterministicSignal]:
        signals = []
        by_customer = defaultdict(list)
        for tx in transactions:
            by_customer[tx.get('customer_id', 'unknown')].append(tx)
            
        for cust_id, txs in by_customer.items():
            txs = sorted(txs, key=lambda x: self._parse_time(x.get('timestamp', datetime.now())))
            for i, tx in enumerate(txs):
                window_start = self._parse_time(tx.get('timestamp', datetime.now()))
                end_24h = window_start + timedelta(hours=24)
                end_7d = window_start + timedelta(days=7)
                
                count_24h = 1
                count_7d = 1
                
                for j in range(i+1, len(txs)):
                    next_time = self._parse_time(txs[j].get('timestamp', datetime.now()))
                    if next_time <= end_24h:
                        count_24h += 1
                    if next_time <= end_7d:
                        count_7d += 1
                    else:
                        break
                        
                if count_24h > self.thresholds["velocity_24h_count"] or count_7d > self.thresholds["velocity_7d_count"]:
                    signals.append(DeterministicSignal(
                        rule_id='VELOCITY_001',
                        rule_name='Velocity Anomaly',
                        rule_type='INTERNAL_DEMO_RULE',
                        description=f'High velocity: {count_24h} txns/24h or {count_7d} txns/7d',
                        triggered=True,
                        details={
                            "customer_id": cust_id,
                            "count_24h": count_24h,
                            "count_7d": count_7d
                        }
                    ))
                    break
        return signals

    def _check_geo(self, transactions: list[dict]) -> list[DeterministicSignal]:
        signals = []
        for tx in transactions:
            dest_country = tx.get('destination_country', '') or tx.get('country', '')
            src_country = tx.get('source_country', '')
            matched = dest_country if dest_country in self.thresholds["high_risk_countries"] else ""
            if not matched and src_country in self.thresholds["high_risk_countries"]:
                matched = src_country
            if matched:
                signals.append(DeterministicSignal(
                    rule_id='GEO_001',
                    rule_name='Geographic Anomaly',
                    rule_type='INTERNAL_DEMO_RULE',
                    description=f'Transaction involves high-risk country: {matched}',
                    triggered=True,
                    details={
                        "transaction_id": tx.get('transaction_id'),
                        "destination": dest_country,
                        "source": src_country
                    }
                ))
        return signals

    def _check_amount(self, transactions: list[dict], customer: dict | None) -> list[DeterministicSignal]:
        signals = []
        avg_amt = 1000
        if customer and 'average_transaction_amount' in customer:
            avg_amt = float(customer['average_transaction_amount'])
            
        threshold = avg_amt * self.thresholds["amount_multiplier"]
        
        for tx in transactions:
            amt = float(tx.get('amount', 0))
            if amt > threshold:
                signals.append(DeterministicSignal(
                    rule_id='AMOUNT_001',
                    rule_name='Unusual Amount',
                    rule_type='INTERNAL_DEMO_RULE',
                    description=f'Amount {amt:.0f} exceeds {threshold:.0f} ({self.thresholds["amount_multiplier"]}x customer average)',
                    triggered=True,
                    details={
                        "transaction_id": tx.get('transaction_id'),
                        "amount": amt,
                        "threshold": threshold,
                        "customer_avg": avg_amt
                    }
                ))
        return signals

    def _check_counterparty(self, transactions: list[dict], customer: dict | None) -> list[DeterministicSignal]:
        signals = []
        for tx in transactions:
            if tx.get('is_new_counterparty', False) and float(tx.get('amount', 0)) > 10000:
                signals.append(DeterministicSignal(
                    rule_id='COUNTERPARTY_001',
                    rule_name='Counterparty Anomaly',
                    rule_type='INTERNAL_DEMO_RULE',
                    description=f'New counterparty with high-value transaction ({float(tx.get("amount", 0)):.0f})',
                    triggered=True,
                    details={
                        "transaction_id": tx.get('transaction_id'),
                        "counterparty": tx.get('counterparty_name'),
                        "amount": float(tx.get('amount', 0))
                    }
                ))
        return signals
