-- Ранняя сверка не завершает занятие; финальная запускается в местную полночь.
ALTER TABLE queue_reconciliations ADD COLUMN checked_at INTEGER;
