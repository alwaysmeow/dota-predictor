CREATE TABLE IF NOT EXISTS dotabuff_teams (
    team_id BIGINT PRIMARY KEY,
    slug TEXT,
    name TEXT,
    url TEXT,
    image_url TEXT,
    last_match_text TEXT,
    last_match_at TIMESTAMPTZ,
    last_match_title TEXT,
    popularity_rank INTEGER,
    matches INTEGER,
    win_rate NUMERIC(6, 2),
    kda NUMERIC(8, 2),
    gpm INTEGER,
    xpm INTEGER,
    duration_seconds INTEGER,
    duration TEXT,
    source_url TEXT,
    raw JSONB,
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_dotabuff_teams_name ON dotabuff_teams (name);
CREATE INDEX IF NOT EXISTS idx_dotabuff_teams_slug ON dotabuff_teams (slug);
CREATE INDEX IF NOT EXISTS idx_dotabuff_teams_popularity_rank ON dotabuff_teams (popularity_rank);

CREATE TABLE IF NOT EXISTS dotabuff_team_matches (
    team_id BIGINT NOT NULL,
    match_id BIGINT NOT NULL,
    source_url TEXT,
    page INTEGER,
    result TEXT,
    won BOOLEAN,
    played_at TIMESTAMPTZ,
    played_at_text TEXT,
    played_at_title TEXT,
    league_id BIGINT,
    league_slug TEXT,
    league_name TEXT,
    league_url TEXT,
    league_image_url TEXT,
    series_id BIGINT,
    series_url TEXT,
    series_region TEXT,
    duration_seconds INTEGER,
    duration TEXT,
    heroes JSONB NOT NULL DEFAULT '[]'::jsonb,
    opponent_team_id BIGINT,
    opponent_slug TEXT,
    opponent_name TEXT,
    opponent_url TEXT,
    opponent_image_url TEXT,
    raw JSONB,
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (team_id, match_id)
);

CREATE INDEX IF NOT EXISTS idx_dotabuff_team_matches_match_id ON dotabuff_team_matches (match_id);
CREATE INDEX IF NOT EXISTS idx_dotabuff_team_matches_played_at ON dotabuff_team_matches (played_at);
CREATE INDEX IF NOT EXISTS idx_dotabuff_team_matches_opponent_team_id ON dotabuff_team_matches (opponent_team_id);
CREATE INDEX IF NOT EXISTS idx_dotabuff_team_matches_league_id ON dotabuff_team_matches (league_id);

CREATE TABLE IF NOT EXISTS dotabuff_matches (
    match_id BIGINT PRIMARY KEY,
    source_url TEXT,
    is_professional_match BOOLEAN,
    radiant_team_id BIGINT,
    radiant_team_name TEXT,
    radiant_team_url TEXT,
    dire_team_id BIGINT,
    dire_team_name TEXT,
    dire_team_url TEXT,
    winner_side TEXT,
    winner_team TEXT,
    raw JSONB,
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_dotabuff_matches_winner_side ON dotabuff_matches (winner_side);
CREATE INDEX IF NOT EXISTS idx_dotabuff_matches_is_professional ON dotabuff_matches (is_professional_match);
CREATE INDEX IF NOT EXISTS idx_dotabuff_matches_radiant_team_id ON dotabuff_matches (radiant_team_id);
CREATE INDEX IF NOT EXISTS idx_dotabuff_matches_dire_team_id ON dotabuff_matches (dire_team_id);

CREATE TABLE IF NOT EXISTS dotabuff_match_players (
    match_id BIGINT NOT NULL REFERENCES dotabuff_matches (match_id) ON DELETE CASCADE,
    player_slot SMALLINT NOT NULL,
    side TEXT NOT NULL,
    player_id BIGINT,
    player_name TEXT,
    player_url TEXT,
    hero TEXT,
    hero_slug TEXT,
    role TEXT,
    role_icon TEXT,
    lane TEXT,
    lane_icon TEXT,
    raw JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (match_id, player_slot)
);

CREATE INDEX IF NOT EXISTS idx_dotabuff_match_players_player_id ON dotabuff_match_players (player_id);
CREATE INDEX IF NOT EXISTS idx_dotabuff_match_players_hero_slug ON dotabuff_match_players (hero_slug);
CREATE INDEX IF NOT EXISTS idx_dotabuff_match_players_side ON dotabuff_match_players (side);

ALTER TABLE dotabuff_teams ALTER COLUMN raw DROP NOT NULL;
ALTER TABLE dotabuff_team_matches ALTER COLUMN raw DROP NOT NULL;
ALTER TABLE dotabuff_matches ALTER COLUMN raw DROP NOT NULL;
ALTER TABLE dotabuff_match_players ALTER COLUMN raw DROP NOT NULL;

UPDATE dotabuff_teams SET raw = NULL WHERE raw IS NOT NULL;
UPDATE dotabuff_team_matches SET raw = NULL WHERE raw IS NOT NULL;
UPDATE dotabuff_matches SET raw = NULL WHERE raw IS NOT NULL;
UPDATE dotabuff_match_players SET raw = NULL WHERE raw IS NOT NULL;
