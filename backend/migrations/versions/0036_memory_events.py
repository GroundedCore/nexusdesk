"""Phase 3 of agent memory: event memories and semantic search support.

Purely additive:

- ``memory_items.embedding`` (vector(1536)) plus an HNSW cosine index, created
  only when the pgvector extension is available on the server; deployments on a
  plain postgres image skip the block and the memory store falls back to the
  TSVECTOR implementation (the DO-block probe keeps both paths on one
  migration, P3-DP-02).
- ``memory_items.content_tsv``: stored generated TSVECTOR over ``content``
  (``simple`` config) with a GIN index — the keyword-search fallback.
- ``memory_items.embedding_failed``: marks rows whose embedding call failed so
  the worker's daily scan can retry them.
- ``ticket_process_log.is_commitment``: the staff-side "mark as commitment"
  entry point (FR-1).
"""

from alembic import op

revision = "0036_memory_events"
down_revision = "0035_memory_customers"
branch_labels = None
depends_on = None


def upgrade():
    # Semantic search column. pgvector is an optional capability: the probe
    # checks pg_available_extensions so a plain postgres image migrates cleanly
    # and simply has no vector column (store probing degrades to text mode).
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM pg_available_extensions WHERE name='vector') THEN
                CREATE EXTENSION IF NOT EXISTS vector;
                ALTER TABLE memory_items ADD COLUMN embedding vector(1536);
                CREATE INDEX memory_items_embedding ON memory_items
                    USING hnsw (embedding vector_cosine_ops);
            END IF;
        END $$;
    """)
    # Keyword fallback: generated full-text vector over the content. 'simple'
    # does not stem or segment; Chinese recall is backstopped by the ILIKE
    # clause in TsVectorStore (see memory/store.py).
    op.execute("""
        ALTER TABLE memory_items ADD COLUMN content_tsv TSVECTOR
        GENERATED ALWAYS AS (to_tsvector('simple', content)) STORED
    """)
    op.execute("CREATE INDEX memory_items_tsv ON memory_items USING gin (content_tsv)")
    # Embedding failure marker, cleared by the worker's daily retry pass.
    op.execute(
        "ALTER TABLE memory_items ADD COLUMN embedding_failed BOOLEAN NOT NULL DEFAULT false"
    )
    # Commitment marker for staff remarks (FR-1).
    op.execute(
        "ALTER TABLE ticket_process_log ADD COLUMN is_commitment BOOLEAN NOT NULL DEFAULT false"
    )


def downgrade():
    op.execute("ALTER TABLE ticket_process_log DROP COLUMN is_commitment")
    op.execute("ALTER TABLE memory_items DROP COLUMN embedding_failed")
    op.execute("DROP INDEX memory_items_tsv")
    op.execute("ALTER TABLE memory_items DROP COLUMN content_tsv")
    # Present only when the pgvector probe block ran.
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM information_schema.columns
                WHERE table_name='memory_items' AND column_name='embedding') THEN
                DROP INDEX memory_items_embedding;
                ALTER TABLE memory_items DROP COLUMN embedding;
            END IF;
        END $$;
    """)
