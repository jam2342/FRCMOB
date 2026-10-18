"""Drop the alliance synergy tables.

Synergy was removed on 2026-10-17. Its measured pair values came from broadcast-video
throughput (team_match_throughputs, analysis_qualities: empty since the pipeline was
retired), so every projection was a confidence-0 role guess. Alliance Advisor now uses
the team model (ratings/pick_value.alliance_outlook). The off-site Neon copy needs the
same drops, or the hourly pg_restore --clean there fails (docs/BACKEND_HOST.md).

Revision ID: 20261017_0026
Revises: 20261017_0025
"""
from alembic import op

revision = "20261017_0026"
down_revision = "20261017_0025"
branch_labels = None
depends_on = None

TABLES = (
    "match_synergy_projections",
    "team_pair_synergy_events",
    "team_pair_synergy_priors",
    "team_event_throughput_strengths",
    "team_season_strengths",
    "team_match_throughputs",
    "analysis_qualities",
)

# The tables as production had them (pg_dump --schema-only, 2026-10-17), empty.
SCHEMA = """
CREATE TABLE public.analysis_qualities (
    run_id integer NOT NULL,
    match_key character varying NOT NULL,
    event_key character varying NOT NULL,
    tracking_quality_score double precision NOT NULL,
    identity_quality_score double precision NOT NULL,
    overall_quality_score double precision NOT NULL,
    details json NOT NULL,
    updated_at timestamp with time zone NOT NULL
);
CREATE TABLE public.match_synergy_projections (
    event_key character varying NOT NULL,
    match_key character varying NOT NULL,
    alliance_color character varying NOT NULL,
    model_version character varying NOT NULL,
    season integer NOT NULL,
    scheduled_time integer,
    expected_throughput double precision,
    alliance_synergy_points double precision NOT NULL,
    projected_throughput double precision,
    alliance_synergy_score_0_100 double precision NOT NULL,
    confidence_0_1 double precision NOT NULL,
    source_label character varying NOT NULL,
    pair_breakdown json NOT NULL,
    params_hash character varying NOT NULL,
    computed_at timestamp with time zone NOT NULL
);
CREATE TABLE public.team_event_throughput_strengths (
    event_key character varying NOT NULL,
    team_key character varying NOT NULL,
    model_version character varying NOT NULL,
    season integer NOT NULL,
    strength_active_bps double precision,
    confidence_0_1 double precision NOT NULL,
    matches_used integer NOT NULL,
    avg_quality double precision NOT NULL,
    coverage_factor double precision NOT NULL,
    params_hash character varying NOT NULL,
    updated_at timestamp with time zone NOT NULL
);
CREATE TABLE public.team_match_throughputs (
    finding_id integer NOT NULL,
    analysis_run_id integer NOT NULL,
    match_key character varying NOT NULL,
    event_key character varying NOT NULL,
    team_key character varying NOT NULL,
    balls_shot_total integer,
    shooting_time_total_seconds double precision,
    bps_total double precision,
    balls_shot_active integer,
    shooting_time_active_seconds double precision,
    active_bps double precision,
    metric_coverage json NOT NULL,
    source character varying NOT NULL,
    updated_at timestamp with time zone NOT NULL
);
CREATE TABLE public.team_pair_synergy_events (
    event_key character varying NOT NULL,
    team_key_a character varying NOT NULL,
    team_key_b character varying NOT NULL,
    model_version character varying NOT NULL,
    season integer NOT NULL,
    synergy_points_event double precision NOT NULL,
    confidence_event double precision NOT NULL,
    n_matches_together integer NOT NULL,
    avg_quality double precision NOT NULL,
    residual_mean_raw double precision NOT NULL,
    params_hash character varying NOT NULL,
    updated_at timestamp with time zone NOT NULL
);
CREATE TABLE public.team_pair_synergy_priors (
    season integer NOT NULL,
    team_key_a character varying NOT NULL,
    team_key_b character varying NOT NULL,
    model_version character varying NOT NULL,
    synergy_points_prior double precision NOT NULL,
    confidence_prior double precision NOT NULL,
    n_matches_together integer NOT NULL,
    avg_quality double precision NOT NULL,
    residual_mean_raw double precision NOT NULL,
    params_hash character varying NOT NULL,
    updated_at timestamp with time zone NOT NULL
);
CREATE TABLE public.team_season_strengths (
    season integer NOT NULL,
    team_key character varying NOT NULL,
    model_version character varying NOT NULL,
    strength_active_bps double precision,
    confidence_0_1 double precision NOT NULL,
    matches_used integer NOT NULL,
    avg_quality double precision NOT NULL,
    coverage_factor double precision NOT NULL,
    params_hash character varying NOT NULL,
    updated_at timestamp with time zone NOT NULL
);
ALTER TABLE ONLY public.analysis_qualities
    ADD CONSTRAINT analysis_qualities_pkey PRIMARY KEY (run_id);
ALTER TABLE ONLY public.match_synergy_projections
    ADD CONSTRAINT match_synergy_projections_pkey PRIMARY KEY (event_key, match_key, alliance_color, model_version);
ALTER TABLE ONLY public.team_event_throughput_strengths
    ADD CONSTRAINT team_event_throughput_strengths_pkey PRIMARY KEY (event_key, team_key, model_version);
ALTER TABLE ONLY public.team_match_throughputs
    ADD CONSTRAINT team_match_throughputs_pkey PRIMARY KEY (finding_id);
ALTER TABLE ONLY public.team_pair_synergy_events
    ADD CONSTRAINT team_pair_synergy_events_pkey PRIMARY KEY (event_key, team_key_a, team_key_b, model_version);
ALTER TABLE ONLY public.team_pair_synergy_priors
    ADD CONSTRAINT team_pair_synergy_priors_pkey PRIMARY KEY (season, team_key_a, team_key_b, model_version);
ALTER TABLE ONLY public.team_season_strengths
    ADD CONSTRAINT team_season_strengths_pkey PRIMARY KEY (season, team_key, model_version);
CREATE INDEX ix_analysis_qualities_event_key ON public.analysis_qualities USING btree (event_key);
CREATE INDEX ix_analysis_qualities_match_key ON public.analysis_qualities USING btree (match_key);
CREATE INDEX ix_analysis_qualities_overall_quality_score ON public.analysis_qualities USING btree (overall_quality_score);
CREATE INDEX ix_analysis_qualities_updated_at ON public.analysis_qualities USING btree (updated_at);
CREATE INDEX ix_match_synergy_projections_computed_at ON public.match_synergy_projections USING btree (computed_at);
CREATE INDEX ix_match_synergy_projections_params_hash ON public.match_synergy_projections USING btree (params_hash);
CREATE INDEX ix_match_synergy_projections_season ON public.match_synergy_projections USING btree (season);
CREATE INDEX ix_team_event_throughput_strengths_params_hash ON public.team_event_throughput_strengths USING btree (params_hash);
CREATE INDEX ix_team_event_throughput_strengths_season ON public.team_event_throughput_strengths USING btree (season);
CREATE INDEX ix_team_event_throughput_strengths_updated_at ON public.team_event_throughput_strengths USING btree (updated_at);
CREATE INDEX ix_team_match_throughputs_analysis_run_id ON public.team_match_throughputs USING btree (analysis_run_id);
CREATE INDEX ix_team_match_throughputs_event_key ON public.team_match_throughputs USING btree (event_key);
CREATE INDEX ix_team_match_throughputs_match_key ON public.team_match_throughputs USING btree (match_key);
CREATE INDEX ix_team_match_throughputs_team_key ON public.team_match_throughputs USING btree (team_key);
CREATE INDEX ix_team_match_throughputs_updated_at ON public.team_match_throughputs USING btree (updated_at);
CREATE INDEX ix_team_pair_synergy_events_params_hash ON public.team_pair_synergy_events USING btree (params_hash);
CREATE INDEX ix_team_pair_synergy_events_season ON public.team_pair_synergy_events USING btree (season);
CREATE INDEX ix_team_pair_synergy_events_updated_at ON public.team_pair_synergy_events USING btree (updated_at);
CREATE INDEX ix_team_pair_synergy_priors_params_hash ON public.team_pair_synergy_priors USING btree (params_hash);
CREATE INDEX ix_team_pair_synergy_priors_updated_at ON public.team_pair_synergy_priors USING btree (updated_at);
CREATE INDEX ix_team_season_strengths_params_hash ON public.team_season_strengths USING btree (params_hash);
CREATE INDEX ix_team_season_strengths_updated_at ON public.team_season_strengths USING btree (updated_at);
ALTER TABLE ONLY public.analysis_qualities
    ADD CONSTRAINT analysis_qualities_event_key_fkey FOREIGN KEY (event_key) REFERENCES public.events(event_key);
ALTER TABLE ONLY public.analysis_qualities
    ADD CONSTRAINT analysis_qualities_match_key_fkey FOREIGN KEY (match_key) REFERENCES public.matches(match_key);
ALTER TABLE ONLY public.analysis_qualities
    ADD CONSTRAINT analysis_qualities_run_id_fkey FOREIGN KEY (run_id) REFERENCES public.analysis_runs(id);
ALTER TABLE ONLY public.match_synergy_projections
    ADD CONSTRAINT match_synergy_projections_event_key_fkey FOREIGN KEY (event_key) REFERENCES public.events(event_key);
ALTER TABLE ONLY public.match_synergy_projections
    ADD CONSTRAINT match_synergy_projections_match_key_fkey FOREIGN KEY (match_key) REFERENCES public.matches(match_key);
ALTER TABLE ONLY public.team_event_throughput_strengths
    ADD CONSTRAINT team_event_throughput_strengths_event_key_fkey FOREIGN KEY (event_key) REFERENCES public.events(event_key);
ALTER TABLE ONLY public.team_event_throughput_strengths
    ADD CONSTRAINT team_event_throughput_strengths_team_key_fkey FOREIGN KEY (team_key) REFERENCES public.teams(team_key);
ALTER TABLE ONLY public.team_match_throughputs
    ADD CONSTRAINT team_match_throughputs_analysis_run_id_fkey FOREIGN KEY (analysis_run_id) REFERENCES public.analysis_runs(id);
ALTER TABLE ONLY public.team_match_throughputs
    ADD CONSTRAINT team_match_throughputs_event_key_fkey FOREIGN KEY (event_key) REFERENCES public.events(event_key);
ALTER TABLE ONLY public.team_match_throughputs
    ADD CONSTRAINT team_match_throughputs_finding_id_fkey FOREIGN KEY (finding_id) REFERENCES public.team_match_findings(id);
ALTER TABLE ONLY public.team_match_throughputs
    ADD CONSTRAINT team_match_throughputs_match_key_fkey FOREIGN KEY (match_key) REFERENCES public.matches(match_key);
ALTER TABLE ONLY public.team_match_throughputs
    ADD CONSTRAINT team_match_throughputs_team_key_fkey FOREIGN KEY (team_key) REFERENCES public.teams(team_key);
ALTER TABLE ONLY public.team_pair_synergy_events
    ADD CONSTRAINT team_pair_synergy_events_event_key_fkey FOREIGN KEY (event_key) REFERENCES public.events(event_key);
ALTER TABLE ONLY public.team_pair_synergy_events
    ADD CONSTRAINT team_pair_synergy_events_team_key_a_fkey FOREIGN KEY (team_key_a) REFERENCES public.teams(team_key);
ALTER TABLE ONLY public.team_pair_synergy_events
    ADD CONSTRAINT team_pair_synergy_events_team_key_b_fkey FOREIGN KEY (team_key_b) REFERENCES public.teams(team_key);
ALTER TABLE ONLY public.team_pair_synergy_priors
    ADD CONSTRAINT team_pair_synergy_priors_team_key_a_fkey FOREIGN KEY (team_key_a) REFERENCES public.teams(team_key);
ALTER TABLE ONLY public.team_pair_synergy_priors
    ADD CONSTRAINT team_pair_synergy_priors_team_key_b_fkey FOREIGN KEY (team_key_b) REFERENCES public.teams(team_key);
ALTER TABLE ONLY public.team_season_strengths
    ADD CONSTRAINT team_season_strengths_team_key_fkey FOREIGN KEY (team_key) REFERENCES public.teams(team_key);
"""


def upgrade():
    for table in TABLES:
        op.drop_table(table)


def downgrade():
    op.execute(SCHEMA)
