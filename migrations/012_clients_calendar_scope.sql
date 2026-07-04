-- Vincula cada cliente al calendario donde fue creado, para que el autocompletado
-- de /calendar/api/clients solo muestre clientes de los calendarios a los que el
-- usuario tiene acceso. Los clientes existentes quedan con calendar_id NULL
-- (se siguen mostrando a todos, como comportamiento heredado).

ALTER TABLE clients ADD COLUMN IF NOT EXISTS calendar_id text;
CREATE INDEX IF NOT EXISTS idx_clients_calendar_id ON clients(calendar_id);
