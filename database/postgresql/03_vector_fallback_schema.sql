-- Use this only when pgvector extension cannot be enabled.
CREATE TABLE IF NOT EXISTS vector_collections (
    id BIGSERIAL PRIMARY KEY,
    collection_name TEXT NOT NULL UNIQUE,
    embedding_dimensions INTEGER NOT NULL DEFAULT 1536,
    distance_metric TEXT NOT NULL DEFAULT 'cosine',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS source_documents (
    id BIGSERIAL PRIMARY KEY,
    doc_type TEXT NOT NULL,
    source_table TEXT,
    source_id BIGINT,
    title TEXT,
    filepath TEXT,
    content TEXT,
    metadata_json TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS document_chunks (
    id BIGSERIAL PRIMARY KEY,
    document_id BIGINT NOT NULL REFERENCES source_documents(id) ON DELETE CASCADE,
    chunk_index INTEGER NOT NULL,
    chunk_text TEXT NOT NULL,
    token_count INTEGER,
    metadata_json TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (document_id, chunk_index)
);

CREATE TABLE IF NOT EXISTS document_embeddings (
    id BIGSERIAL PRIMARY KEY,
    collection_id BIGINT NOT NULL REFERENCES vector_collections(id) ON DELETE CASCADE,
    chunk_id BIGINT NOT NULL REFERENCES document_chunks(id) ON DELETE CASCADE,
    embedding_json TEXT NOT NULL,
    embedding_model TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (chunk_id)
);
