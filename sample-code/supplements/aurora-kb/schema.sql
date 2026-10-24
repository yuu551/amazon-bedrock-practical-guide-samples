CREATE EXTENSION IF NOT EXISTS vector;
CREATE SCHEMA IF NOT EXISTS bedrock_integration;
CREATE TABLE IF NOT EXISTS bedrock_integration.bedrock_kb (
    id uuid PRIMARY KEY,
    embedding vector(1024),
    chunks text,
    metadata json,
    custom_metadata jsonb
);
CREATE INDEX IF NOT EXISTS bedrock_kb_embedding_idx
    ON bedrock_integration.bedrock_kb USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS bedrock_kb_text_idx
    ON bedrock_integration.bedrock_kb USING gin (to_tsvector('simple', chunks));
CREATE INDEX IF NOT EXISTS bedrock_kb_metadata_idx
    ON bedrock_integration.bedrock_kb USING gin (custom_metadata);
GRANT USAGE ON SCHEMA bedrock_integration TO bedrock_user;
GRANT SELECT, INSERT, UPDATE, DELETE ON bedrock_integration.bedrock_kb TO bedrock_user;
