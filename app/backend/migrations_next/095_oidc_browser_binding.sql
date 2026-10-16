ALTER TABLE oidc_auth_transactions
    ADD COLUMN browser_binding_hash text;

DELETE FROM oidc_auth_transactions;

ALTER TABLE oidc_auth_transactions
    ALTER COLUMN browser_binding_hash SET NOT NULL;
