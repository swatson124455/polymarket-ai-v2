-- Rollback 071_strategy_lifecycle.sql
DROP TABLE IF EXISTS strategy_predictions;
DROP TABLE IF EXISTS strategy_transitions;
DROP TABLE IF EXISTS capital_allocations;
DROP TABLE IF EXISTS strategy_performance;
DROP TABLE IF EXISTS strategies;
