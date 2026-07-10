-- Down-migration for 080: drop the probability-frame provenance column.
-- NOTE: the up-migration's was_correct/realized_edge NULL-out on historical
-- PSW rows is NOT reversible (the pre-migration values were frame-poisoned
-- and are not preserved anywhere).

ALTER TABLE prediction_log
    DROP COLUMN IF EXISTS prob_frame;
