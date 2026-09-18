-- sql/schema.sql
-- Core schemas for AML Agent System

\i audit_log.sql

CREATE TABLE IF NOT EXISTS customers (
    customer_id VARCHAR PRIMARY KEY,
    full_name VARCHAR NOT NULL,
    customer_type VARCHAR,
    country VARCHAR,
    kyc_status VARCHAR,
    risk_rating VARCHAR,
    occupation VARCHAR,
    relationship_manager_id VARCHAR,
    email VARCHAR,
    phone VARCHAR,
    address TEXT,
    identity_document VARCHAR,
    date_of_birth DATE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS accounts (
    account_id VARCHAR PRIMARY KEY,
    customer_id VARCHAR REFERENCES customers(customer_id),
    account_number VARCHAR UNIQUE NOT NULL,
    account_type VARCHAR,
    currency VARCHAR,
    status VARCHAR,
    opened_at TIMESTAMP
);

CREATE INDEX idx_accounts_customer_id ON accounts(customer_id);

CREATE TABLE IF NOT EXISTS transactions (
    transaction_id VARCHAR PRIMARY KEY,
    timestamp TIMESTAMP NOT NULL,
    account_id VARCHAR REFERENCES accounts(account_id),
    customer_id VARCHAR REFERENCES customers(customer_id),
    counterparty_id VARCHAR,
    amount DECIMAL(18, 2) NOT NULL,
    currency VARCHAR NOT NULL,
    direction VARCHAR NOT NULL,
    country VARCHAR,
    channel VARCHAR,
    transaction_type VARCHAR,
    counterparty_name VARCHAR,
    remittance_text TEXT,
    status VARCHAR
);

CREATE INDEX idx_transactions_account_id ON transactions(account_id);
CREATE INDEX idx_transactions_customer_id ON transactions(customer_id);
CREATE INDEX idx_transactions_timestamp ON transactions(timestamp);

CREATE TABLE IF NOT EXISTS alerts (
    alert_id VARCHAR PRIMARY KEY,
    transaction_id VARCHAR, -- Weak FK as transaction might be quarantined
    customer_id VARCHAR REFERENCES customers(customer_id),
    alert_type VARCHAR NOT NULL,
    triggered_rules JSONB,
    severity VARCHAR,
    status VARCHAR,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    resolved_at TIMESTAMP,
    resolved_by VARCHAR
);

CREATE INDEX idx_alerts_customer_id ON alerts(customer_id);
CREATE INDEX idx_alerts_transaction_id ON alerts(transaction_id);

CREATE TABLE IF NOT EXISTS investigations (
    investigation_id VARCHAR PRIMARY KEY,
    alert_id VARCHAR REFERENCES alerts(alert_id),
    investigator_agent_run_id VARCHAR,
    supporting_evidence JSONB,
    contradictory_evidence JSONB,
    missing_evidence JSONB,
    regulatory_applicability TEXT,
    assessment TEXT,
    confidence FLOAT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_investigations_alert_id ON investigations(alert_id);

CREATE TABLE IF NOT EXISTS recommendations (
    recommendation_id VARCHAR PRIMARY KEY,
    investigation_id VARCHAR REFERENCES investigations(investigation_id),
    alert_id VARCHAR REFERENCES alerts(alert_id),
    suggested_action TEXT,
    rationale TEXT,
    evidence JSONB,
    confidence FLOAT,
    requires_human_review BOOLEAN,
    policy_validation_result TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_recommendations_investigation_id ON recommendations(investigation_id);
CREATE INDEX idx_recommendations_alert_id ON recommendations(alert_id);

CREATE TABLE IF NOT EXISTS feedback (
    feedback_id VARCHAR PRIMARY KEY,
    alert_id VARCHAR REFERENCES alerts(alert_id),
    analyst_profile_id VARCHAR,
    disposition TEXT,
    pattern_key TEXT,
    notes TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_feedback_alert_id ON feedback(alert_id);
CREATE INDEX idx_feedback_pattern_key ON feedback(pattern_key);

CREATE TABLE IF NOT EXISTS feedback_patterns (
    pattern_key TEXT PRIMARY KEY,
    true_hit_count INT DEFAULT 0,
    false_positive_count INT DEFAULT 0,
    escalated_count INT DEFAULT 0,
    feedback_weight FLOAT DEFAULT 1.0,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS sanctions_entries (
    entry_id VARCHAR PRIMARY KEY,
    source TEXT,
    primary_name TEXT,
    normalized_name TEXT,
    aliases JSONB,
    entity_type TEXT,
    programs JSONB,
    countries JSONB,
    identifiers JSONB,
    source_metadata JSONB,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_sanctions_normalized_name ON sanctions_entries(normalized_name);
CREATE INDEX idx_sanctions_primary_name ON sanctions_entries(primary_name);

CREATE TABLE IF NOT EXISTS regulatory_graph_nodes (
    node_id VARCHAR PRIMARY KEY,
    node_type TEXT NOT NULL,
    label TEXT,
    document_id TEXT,
    metadata JSONB
);

CREATE TABLE IF NOT EXISTS regulatory_graph_edges (
    edge_id VARCHAR PRIMARY KEY,
    source_node_id VARCHAR REFERENCES regulatory_graph_nodes(node_id),
    target_node_id VARCHAR REFERENCES regulatory_graph_nodes(node_id),
    edge_type TEXT NOT NULL,
    metadata JSONB
);

CREATE INDEX idx_reg_graph_edges_source ON regulatory_graph_edges(source_node_id);
CREATE INDEX idx_reg_graph_edges_target ON regulatory_graph_edges(target_node_id);
