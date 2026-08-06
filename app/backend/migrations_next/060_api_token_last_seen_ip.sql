-- Where a connection was last used from.
--
-- audit_events already records the public request IP per event
-- (next_audit.request_ip_details picks the first globally routable candidate),
-- but the API & MCP page shows connections, not events, and had nothing to
-- answer "is this one mine?" with. These columns carry the last observation
-- onto the token itself.
--
-- The value is always what the server observed, never something the client
-- claimed: a client-supplied address is a claim, not evidence, and this field
-- is meant to be read as evidence. last_seen_ip_source records which header
-- the address was taken from so a reader can tell a proxy chain apart from a
-- direct connection.

ALTER TABLE api_access_tokens
    ADD COLUMN IF NOT EXISTS last_seen_ip text,
    ADD COLUMN IF NOT EXISTS last_seen_ip_source text;
