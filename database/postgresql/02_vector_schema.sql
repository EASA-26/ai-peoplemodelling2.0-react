-- Vector-ready PostgreSQL schema for AI People Modelling app
CREATE EXTENSION IF NOT EXISTS vector;

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
CREATE INDEX IF NOT EXISTS idx_source_documents_doc_type ON source_documents (doc_type);
CREATE INDEX IF NOT EXISTS idx_source_documents_source_ref ON source_documents (source_table, source_id);

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
CREATE INDEX IF NOT EXISTS idx_document_chunks_document_id ON document_chunks (document_id);

CREATE TABLE IF NOT EXISTS document_embeddings (
    id BIGSERIAL PRIMARY KEY,
    collection_id BIGINT NOT NULL REFERENCES vector_collections(id) ON DELETE CASCADE,
    chunk_id BIGINT NOT NULL REFERENCES document_chunks(id) ON DELETE CASCADE,
    embedding vector(1536) NOT NULL,
    embedding_model TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (chunk_id)
);

CREATE INDEX IF NOT EXISTS idx_document_embeddings_hnsw
    ON document_embeddings USING hnsw (embedding vector_cosine_ops);

INSERT INTO vector_collections (collection_name, embedding_dimensions, distance_metric)
VALUES
    ('job_descriptions', 1536, 'cosine'),
    ('candidates', 1536, 'cosine'),
    ('position_profiles', 1536, 'cosine'),
    ('talent_cards', 1536, 'cosine')
ON CONFLICT (collection_name) DO NOTHING;
