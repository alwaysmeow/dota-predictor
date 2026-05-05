ALTER TABLE dotabuff_matches
    ADD COLUMN IF NOT EXISTS played_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_dotabuff_matches_played_at
    ON dotabuff_matches (played_at);
