DELETE FROM oidc_auth_transactions;

ALTER TABLE oidc_auth_transactions
    RENAME COLUMN nonce TO nonce_hash;
