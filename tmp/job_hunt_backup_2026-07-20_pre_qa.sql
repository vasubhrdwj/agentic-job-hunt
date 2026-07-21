--
-- PostgreSQL database dump
--

\restrict dmEgFzxkT4hLT0LOgmbtJdgHoGnspD3NwOUhYSSomXlLbs75lR4gb9TE0hd4mKI

-- Dumped from database version 16.14
-- Dumped by pg_dump version 16.14

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: achievement_evidence; Type: TABLE; Schema: public; Owner: job_hunt
--

CREATE TABLE public.achievement_evidence (
    id character varying(32) NOT NULL,
    owner_id character varying(64) NOT NULL,
    source_resume_version_id character varying(32),
    encrypted_payload text NOT NULL,
    encryption_key_id character varying(32) NOT NULL,
    skills json NOT NULL,
    origin character varying(20) NOT NULL,
    approval_state character varying(20) NOT NULL,
    approved_at timestamp with time zone,
    rejected_at timestamp with time zone,
    retired_at timestamp with time zone,
    version integer NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_achievement_evidence_approval_state CHECK (((approval_state)::text = ANY ((ARRAY['pending'::character varying, 'approved'::character varying, 'rejected'::character varying, 'retired'::character varying])::text[]))),
    CONSTRAINT ck_achievement_evidence_approval_timestamps CHECK (((((approval_state)::text = 'pending'::text) AND (approved_at IS NULL) AND (rejected_at IS NULL) AND (retired_at IS NULL)) OR (((approval_state)::text = 'approved'::text) AND (approved_at IS NOT NULL) AND (rejected_at IS NULL) AND (retired_at IS NULL)) OR (((approval_state)::text = 'rejected'::text) AND (rejected_at IS NOT NULL) AND (approved_at IS NULL) AND (retired_at IS NULL)) OR (((approval_state)::text = 'retired'::text) AND (approved_at IS NOT NULL) AND (rejected_at IS NULL) AND (retired_at IS NOT NULL)))),
    CONSTRAINT ck_achievement_evidence_origin CHECK (((origin)::text = ANY ((ARRAY['owner_entered'::character varying, 'resume_suggestion'::character varying])::text[]))),
    CONSTRAINT ck_achievement_evidence_version_positive CHECK ((version >= 1))
);


ALTER TABLE public.achievement_evidence OWNER TO job_hunt;

--
-- Name: action_items; Type: TABLE; Schema: public; Owner: job_hunt
--

CREATE TABLE public.action_items (
    id character varying(32) NOT NULL,
    owner_id character varying(64) NOT NULL,
    application_id character varying(32) NOT NULL,
    kind character varying(64) NOT NULL,
    title character varying(240) NOT NULL,
    status character varying(20) NOT NULL,
    due_on date NOT NULL,
    version integer NOT NULL,
    completed_at timestamp with time zone,
    cancelled_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    interview_round_id character varying(32),
    CONSTRAINT ck_action_items_interview_round_kind CHECK (((interview_round_id IS NULL) OR ((kind)::text = 'prepare_interview'::text))),
    CONSTRAINT ck_action_items_kind CHECK (((kind)::text = ANY ((ARRAY['review_and_prepare_application'::character varying, 'submit_application'::character varying, 'follow_up_application'::character varying, 'prepare_recruiter_screen'::character varying, 'prepare_interview'::character varying, 'review_offer'::character varying])::text[]))),
    CONSTRAINT ck_action_items_status CHECK (((status)::text = ANY ((ARRAY['open'::character varying, 'completed'::character varying, 'cancelled'::character varying])::text[]))),
    CONSTRAINT ck_action_items_status_timestamps CHECK (((((status)::text = 'open'::text) AND (completed_at IS NULL) AND (cancelled_at IS NULL)) OR (((status)::text = 'completed'::text) AND (completed_at IS NOT NULL) AND (cancelled_at IS NULL)) OR (((status)::text = 'cancelled'::text) AND (cancelled_at IS NOT NULL) AND (completed_at IS NULL)))),
    CONSTRAINT ck_action_items_title_length CHECK (((length(TRIM(BOTH FROM title)) >= 1) AND (length(TRIM(BOTH FROM title)) <= 240))),
    CONSTRAINT ck_action_items_version_positive CHECK ((version >= 1))
);


ALTER TABLE public.action_items OWNER TO job_hunt;

--
-- Name: alembic_version; Type: TABLE; Schema: public; Owner: job_hunt
--

CREATE TABLE public.alembic_version (
    version_num character varying(32) NOT NULL
);


ALTER TABLE public.alembic_version OWNER TO job_hunt;

--
-- Name: application_action_reviews; Type: TABLE; Schema: public; Owner: job_hunt
--

CREATE TABLE public.application_action_reviews (
    id character varying(32) NOT NULL,
    owner_id character varying(64) NOT NULL,
    application_id character varying(32) NOT NULL,
    action_item_id character varying(32) NOT NULL,
    decision character varying(16) NOT NULL,
    prior_due_on date NOT NULL,
    new_due_on date NOT NULL,
    prior_action_version integer NOT NULL,
    new_action_version integer NOT NULL,
    prior_application_version integer NOT NULL,
    new_application_version integer NOT NULL,
    recording_method character varying(16) NOT NULL,
    recorded_at timestamp with time zone NOT NULL,
    idempotency_key_hash character varying(64) NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_application_action_reviews_action_version_progresses CHECK ((new_action_version = (prior_action_version + 1))),
    CONSTRAINT ck_application_action_reviews_application_version_progresses CHECK ((new_application_version = (prior_application_version + 1))),
    CONSTRAINT ck_application_action_reviews_decision CHECK (((decision)::text = ANY ((ARRAY['continue'::character varying, 'waiting'::character varying])::text[]))),
    CONSTRAINT ck_application_action_reviews_due_date_progresses CHECK ((new_due_on > prior_due_on)),
    CONSTRAINT ck_application_action_reviews_mutation_hash CHECK ((length((idempotency_key_hash)::text) = 64)),
    CONSTRAINT ck_application_action_reviews_prior_versions_positive CHECK (((prior_action_version >= 1) AND (prior_application_version >= 1))),
    CONSTRAINT ck_application_action_reviews_recording_method CHECK (((recording_method)::text = 'manual'::text))
);


ALTER TABLE public.application_action_reviews OWNER TO job_hunt;

--
-- Name: application_activity_events; Type: TABLE; Schema: public; Owner: job_hunt
--

CREATE TABLE public.application_activity_events (
    id character varying(32) NOT NULL,
    owner_id character varying(64) NOT NULL,
    application_id character varying(32) NOT NULL,
    sequence_number integer NOT NULL,
    event_type character varying(64) NOT NULL,
    from_stage character varying(24),
    to_stage character varying(24) NOT NULL,
    action_item_id character varying(32),
    occurred_at timestamp with time zone NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    previous_action_item_id character varying(32),
    submission_id character varying(32),
    effective_on date,
    outcome_id character varying(32),
    interview_round_id character varying(32),
    CONSTRAINT ck_application_activity_events_event_shape CHECK (((((event_type)::text = 'application_created'::text) AND (sequence_number = 1) AND (from_stage IS NULL) AND ((to_stage)::text = 'pursuing'::text) AND (action_item_id IS NOT NULL) AND (previous_action_item_id IS NULL) AND (submission_id IS NULL) AND (effective_on IS NULL) AND (outcome_id IS NULL) AND (interview_round_id IS NULL)) OR (((event_type)::text = 'application_ready_to_apply'::text) AND (sequence_number = 2) AND ((from_stage)::text = 'pursuing'::text) AND ((to_stage)::text = 'ready_to_apply'::text) AND (action_item_id IS NOT NULL) AND (previous_action_item_id IS NOT NULL) AND ((previous_action_item_id)::text <> (action_item_id)::text) AND (submission_id IS NULL) AND (effective_on IS NULL) AND (outcome_id IS NULL) AND (interview_round_id IS NULL)) OR (((event_type)::text = 'application_applied'::text) AND (sequence_number = 3) AND ((from_stage)::text = 'ready_to_apply'::text) AND ((to_stage)::text = 'applied'::text) AND (action_item_id IS NOT NULL) AND (previous_action_item_id IS NOT NULL) AND ((previous_action_item_id)::text <> (action_item_id)::text) AND (submission_id IS NOT NULL) AND (effective_on IS NULL) AND (outcome_id IS NULL) AND (interview_round_id IS NULL)) OR (((event_type)::text = 'application_screening'::text) AND (sequence_number >= 4) AND ((from_stage)::text = 'applied'::text) AND ((to_stage)::text = 'screening'::text) AND (action_item_id IS NOT NULL) AND (previous_action_item_id IS NOT NULL) AND ((previous_action_item_id)::text <> (action_item_id)::text) AND (submission_id IS NULL) AND (effective_on IS NOT NULL) AND (outcome_id IS NULL) AND (interview_round_id IS NULL)) OR (((event_type)::text = 'application_interviewing'::text) AND (sequence_number >= 4) AND ((from_stage)::text = ANY ((ARRAY['applied'::character varying, 'screening'::character varying])::text[])) AND ((to_stage)::text = 'interviewing'::text) AND (action_item_id IS NOT NULL) AND (previous_action_item_id IS NOT NULL) AND ((previous_action_item_id)::text <> (action_item_id)::text) AND (submission_id IS NULL) AND (effective_on IS NOT NULL) AND (outcome_id IS NULL)) OR (((event_type)::text = 'application_offer'::text) AND (sequence_number >= 4) AND ((from_stage)::text = ANY ((ARRAY['applied'::character varying, 'screening'::character varying, 'interviewing'::character varying])::text[])) AND ((to_stage)::text = 'offer'::text) AND (action_item_id IS NOT NULL) AND (previous_action_item_id IS NOT NULL) AND ((previous_action_item_id)::text <> (action_item_id)::text) AND (submission_id IS NULL) AND (effective_on IS NOT NULL) AND (outcome_id IS NULL) AND (interview_round_id IS NULL)) OR (((event_type)::text = 'application_closed'::text) AND (sequence_number >= 2) AND ((from_stage)::text = ANY ((ARRAY['pursuing'::character varying, 'ready_to_apply'::character varying, 'applied'::character varying, 'screening'::character varying, 'interviewing'::character varying, 'offer'::character varying])::text[])) AND ((to_stage)::text = 'closed'::text) AND (action_item_id IS NULL) AND (previous_action_item_id IS NOT NULL) AND (submission_id IS NULL) AND (effective_on IS NOT NULL) AND (outcome_id IS NOT NULL) AND (interview_round_id IS NULL)))),
    CONSTRAINT ck_application_activity_events_event_type CHECK (((event_type)::text = ANY ((ARRAY['application_created'::character varying, 'application_ready_to_apply'::character varying, 'application_applied'::character varying, 'application_screening'::character varying, 'application_interviewing'::character varying, 'application_offer'::character varying, 'application_closed'::character varying])::text[]))),
    CONSTRAINT ck_application_activity_events_sequence_positive CHECK ((sequence_number >= 1))
);


ALTER TABLE public.application_activity_events OWNER TO job_hunt;

--
-- Name: application_artifact_events; Type: TABLE; Schema: public; Owner: job_hunt
--

CREATE TABLE public.application_artifact_events (
    id character varying(32) NOT NULL,
    owner_id character varying(64) NOT NULL,
    application_id character varying(32) NOT NULL,
    application_pack_id character varying(32) NOT NULL,
    artifact_revision_id character varying(32) NOT NULL,
    sequence_number integer NOT NULL,
    event_type character varying(16) NOT NULL,
    tailored_resume_version_id character varying(32),
    occurred_at timestamp with time zone NOT NULL,
    idempotency_key_hash character varying(64) NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_application_artifact_events_event_resume_shape CHECK (((((event_type)::text = 'approved'::text) AND (tailored_resume_version_id IS NOT NULL)) OR (((event_type)::text = 'rejected'::text) AND (tailored_resume_version_id IS NULL)))),
    CONSTRAINT ck_application_artifact_events_event_type CHECK (((event_type)::text = ANY ((ARRAY['approved'::character varying, 'rejected'::character varying])::text[]))),
    CONSTRAINT ck_application_artifact_events_mutation_hash CHECK ((length((idempotency_key_hash)::text) = 64)),
    CONSTRAINT ck_application_artifact_events_sequence_number_positive CHECK ((sequence_number >= 1))
);


ALTER TABLE public.application_artifact_events OWNER TO job_hunt;

--
-- Name: application_artifact_revisions; Type: TABLE; Schema: public; Owner: job_hunt
--

CREATE TABLE public.application_artifact_revisions (
    id character varying(32) NOT NULL,
    owner_id character varying(64) NOT NULL,
    application_id character varying(32) NOT NULL,
    application_pack_id character varying(32) NOT NULL,
    grounding_revision_id character varying(32) NOT NULL,
    parent_artifact_revision_id character varying(32),
    revision_number integer NOT NULL,
    source character varying(20) NOT NULL,
    generator_version character varying(64) NOT NULL,
    encrypted_payload text NOT NULL,
    encryption_key_id character varying(32) NOT NULL,
    content_hash character varying(64) NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_application_artifact_revisions_content_hash CHECK ((length((content_hash)::text) = 64)),
    CONSTRAINT ck_application_artifact_revisions_encrypted_payload_envelope CHECK (((length(TRIM(BOTH FROM encrypted_payload)) >= 1) AND ((length(TRIM(BOTH FROM encryption_key_id)) >= 1) AND (length(TRIM(BOTH FROM encryption_key_id)) <= 32)))),
    CONSTRAINT ck_application_artifact_revisions_generator_version CHECK (((generator_version)::text = 'application-artifacts-deterministic-v1'::text)),
    CONSTRAINT ck_application_artifact_revisions_parent_not_self CHECK (((parent_artifact_revision_id IS NULL) OR ((parent_artifact_revision_id)::text <> (id)::text))),
    CONSTRAINT ck_application_artifact_revisions_revision_number_positive CHECK ((revision_number >= 1)),
    CONSTRAINT ck_application_artifact_revisions_source CHECK (((source)::text = 'deterministic'::text))
);


ALTER TABLE public.application_artifact_revisions OWNER TO job_hunt;

--
-- Name: application_contacts; Type: TABLE; Schema: public; Owner: job_hunt
--

CREATE TABLE public.application_contacts (
    id character varying(32) NOT NULL,
    owner_id character varying(64) NOT NULL,
    application_id character varying(32) NOT NULL,
    contact_plan_id character varying(32) NOT NULL,
    contact_id character varying(32) NOT NULL,
    discovery_provider character varying(64) NOT NULL,
    discovery_query text NOT NULL,
    result_position integer NOT NULL,
    discovered_at timestamp with time zone NOT NULL,
    current_title character varying(300) NOT NULL,
    current_company character varying(200) NOT NULL,
    category character varying(24) NOT NULL,
    verification_status character varying(20) NOT NULL,
    confidence double precision NOT NULL,
    verified_at timestamp with time zone,
    employer_evidence_excerpt character varying(1000),
    employer_evidence_url text,
    employer_evidence_source character varying(64),
    employer_evidence_observed_at timestamp with time zone,
    why_relevant character varying(2000) NOT NULL,
    relationship_status character varying(16) NOT NULL,
    relationship_evidence_summary text,
    relationship_evidence_url text,
    team_proximity_status character varying(16) NOT NULL,
    team_evidence_summary text,
    team_evidence_url text,
    score_total integer NOT NULL,
    score_components json NOT NULL,
    scoring_version character varying(64) NOT NULL,
    pool_rank integer NOT NULL,
    bench_rank integer,
    wave integer,
    bench_state character varying(20) NOT NULL,
    exclusion_reason character varying(100),
    cooldown_until timestamp with time zone,
    unlocked_at timestamp with time zone,
    version integer NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_application_contacts_bench_rank CHECK (((bench_rank IS NULL) OR ((bench_rank >= 1) AND (bench_rank <= 5)))),
    CONSTRAINT ck_application_contacts_bench_selection CHECK ((((bench_rank IS NULL) AND (wave IS NULL) AND ((bench_state)::text = ANY ((ARRAY['candidate'::character varying, 'excluded'::character varying, 'overflow'::character varying])::text[]))) OR ((bench_rank IS NOT NULL) AND (wave IS NOT NULL) AND ((verification_status)::text = 'verified'::text) AND (exclusion_reason IS NULL) AND ((bench_state)::text = ANY ((ARRAY['ready'::character varying, 'reserve'::character varying, 'paused'::character varying, 'stopped'::character varying])::text[]))))),
    CONSTRAINT ck_application_contacts_bench_state CHECK (((bench_state)::text = ANY ((ARRAY['candidate'::character varying, 'excluded'::character varying, 'overflow'::character varying, 'ready'::character varying, 'reserve'::character varying, 'paused'::character varying, 'stopped'::character varying])::text[]))),
    CONSTRAINT ck_application_contacts_bench_unlock CHECK (((((bench_state)::text = 'ready'::text) AND (unlocked_at IS NOT NULL)) OR (((bench_state)::text = 'reserve'::text) AND (unlocked_at IS NULL)) OR ((bench_state)::text = ANY ((ARRAY['candidate'::character varying, 'excluded'::character varying, 'overflow'::character varying, 'paused'::character varying, 'stopped'::character varying])::text[])))),
    CONSTRAINT ck_application_contacts_category CHECK (((category)::text = ANY ((ARRAY['warm_path'::character varying, 'team_peer'::character varying, 'adjacent_peer'::character varying, 'team_leader'::character varying, 'recruiter'::character varying, 'other'::character varying])::text[]))),
    CONSTRAINT ck_application_contacts_confidence CHECK (((confidence >= (0.0)::double precision) AND (confidence <= (1.0)::double precision))),
    CONSTRAINT ck_application_contacts_current_company CHECK (((length(TRIM(BOTH FROM current_company)) >= 1) AND (length(TRIM(BOTH FROM current_company)) <= 200))),
    CONSTRAINT ck_application_contacts_current_title CHECK (((length(TRIM(BOTH FROM current_title)) >= 1) AND (length(TRIM(BOTH FROM current_title)) <= 300))),
    CONSTRAINT ck_application_contacts_employer_evidence_excerpt CHECK (((employer_evidence_excerpt IS NULL) OR ((length(TRIM(BOTH FROM employer_evidence_excerpt)) >= 1) AND (length(TRIM(BOTH FROM employer_evidence_excerpt)) <= 1000)))),
    CONSTRAINT ck_application_contacts_employer_evidence_source CHECK (((employer_evidence_source IS NULL) OR ((length(TRIM(BOTH FROM employer_evidence_source)) >= 1) AND (length(TRIM(BOTH FROM employer_evidence_source)) <= 64)))),
    CONSTRAINT ck_application_contacts_employer_evidence_url CHECK (((employer_evidence_url IS NULL) OR (((length(employer_evidence_url) >= 9) AND (length(employer_evidence_url) <= 2048)) AND (employer_evidence_url ~~ 'https://%'::text)))),
    CONSTRAINT ck_application_contacts_pool_rank CHECK (((pool_rank >= 1) AND (pool_rank <= 12))),
    CONSTRAINT ck_application_contacts_relationship_status CHECK (((relationship_status)::text = ANY ((ARRAY['verified'::character varying, 'inferred'::character varying, 'unknown'::character varying])::text[]))),
    CONSTRAINT ck_application_contacts_result_position_positive CHECK ((result_position >= 1)),
    CONSTRAINT ck_application_contacts_score_total CHECK (((score_total >= 0) AND (score_total <= 1000))),
    CONSTRAINT ck_application_contacts_team_proximity_status CHECK (((team_proximity_status)::text = ANY ((ARRAY['verified'::character varying, 'inferred'::character varying, 'unknown'::character varying])::text[]))),
    CONSTRAINT ck_application_contacts_verification_status CHECK (((verification_status)::text = ANY ((ARRAY['unverified'::character varying, 'verified'::character varying, 'rejected'::character varying, 'stale'::character varying])::text[]))),
    CONSTRAINT ck_application_contacts_verified_evidence CHECK (((((verification_status)::text = 'verified'::text) AND (confidence >= (0.75)::double precision) AND (verified_at IS NOT NULL) AND (employer_evidence_excerpt IS NOT NULL) AND (employer_evidence_url IS NOT NULL) AND (employer_evidence_source IS NOT NULL) AND (employer_evidence_observed_at IS NOT NULL)) OR (((verification_status)::text <> 'verified'::text) AND (bench_rank IS NULL)))),
    CONSTRAINT ck_application_contacts_version_positive CHECK ((version >= 1)),
    CONSTRAINT ck_application_contacts_warm_path_verified CHECK ((((category)::text <> 'warm_path'::text) OR ((relationship_status)::text = 'verified'::text))),
    CONSTRAINT ck_application_contacts_wave CHECK (((wave IS NULL) OR ((wave >= 1) AND (wave <= 5)))),
    CONSTRAINT ck_application_contacts_why_relevant CHECK (((length(TRIM(BOTH FROM why_relevant)) >= 1) AND (length(TRIM(BOTH FROM why_relevant)) <= 2000)))
);


ALTER TABLE public.application_contacts OWNER TO job_hunt;

--
-- Name: application_interview_preparation_revisions; Type: TABLE; Schema: public; Owner: job_hunt
--

CREATE TABLE public.application_interview_preparation_revisions (
    id character varying(32) NOT NULL,
    owner_id character varying(64) NOT NULL,
    application_id character varying(32) NOT NULL,
    preparation_id character varying(32) NOT NULL,
    parent_revision_id character varying(32),
    revision_number integer NOT NULL,
    application_submission_id character varying(32) NOT NULL,
    application_pack_id character varying(32) NOT NULL,
    grounding_revision_id character varying(32) NOT NULL,
    job_posting_id character varying(32) NOT NULL,
    posting_version_id character varying(32) NOT NULL,
    target_kind character varying(24) NOT NULL,
    interview_round_id character varying(32),
    interview_round_version integer,
    source_fingerprint character varying(64) NOT NULL,
    recording_method character varying(20) NOT NULL,
    encrypted_payload text NOT NULL,
    encryption_key_id character varying(32) NOT NULL,
    content_hash character varying(64) NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_application_interview_preparation_revisions_content_hash CHECK ((length((content_hash)::text) = 64)),
    CONSTRAINT ck_application_interview_preparation_revisions_envelope CHECK (((length(TRIM(BOTH FROM encrypted_payload)) >= 1) AND ((length(TRIM(BOTH FROM encryption_key_id)) >= 1) AND (length(TRIM(BOTH FROM encryption_key_id)) <= 32)))),
    CONSTRAINT ck_application_interview_preparation_revisions_parent CHECK (((parent_revision_id IS NULL) OR ((parent_revision_id)::text <> (id)::text))),
    CONSTRAINT ck_application_interview_preparation_revisions_recording CHECK (((recording_method)::text = 'owner_authored'::text)),
    CONSTRAINT ck_application_interview_preparation_revisions_revision_800d CHECK ((revision_number >= 1)),
    CONSTRAINT ck_application_interview_preparation_revisions_source_hash CHECK ((length((source_fingerprint)::text) = 64)),
    CONSTRAINT ck_application_interview_preparation_revisions_target CHECK (((target_kind)::text = ANY ((ARRAY['recruiter_screen'::character varying, 'interview_round'::character varying])::text[]))),
    CONSTRAINT ck_application_interview_preparation_revisions_target_shape CHECK (((((target_kind)::text = 'recruiter_screen'::text) AND (interview_round_id IS NULL)) OR (((target_kind)::text = 'interview_round'::text) AND (interview_round_id IS NOT NULL)))),
    CONSTRAINT ck_application_interview_preparation_revisions_target_version CHECK (((((target_kind)::text = 'recruiter_screen'::text) AND (interview_round_version IS NULL)) OR (((target_kind)::text = 'interview_round'::text) AND (interview_round_version >= 1))))
);


ALTER TABLE public.application_interview_preparation_revisions OWNER TO job_hunt;

--
-- Name: application_interview_preparations; Type: TABLE; Schema: public; Owner: job_hunt
--

CREATE TABLE public.application_interview_preparations (
    id character varying(32) NOT NULL,
    owner_id character varying(64) NOT NULL,
    application_id character varying(32) NOT NULL,
    version integer NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_application_interview_preparations_version_positive CHECK ((version >= 1))
);


ALTER TABLE public.application_interview_preparations OWNER TO job_hunt;

--
-- Name: application_interview_round_events; Type: TABLE; Schema: public; Owner: job_hunt
--

CREATE TABLE public.application_interview_round_events (
    id character varying(32) NOT NULL,
    owner_id character varying(64) NOT NULL,
    application_id character varying(32) NOT NULL,
    interview_round_id character varying(32) NOT NULL,
    sequence_number integer NOT NULL,
    event_type character varying(20) NOT NULL,
    from_status character varying(20),
    to_status character varying(20) NOT NULL,
    scheduled_start_at timestamp with time zone NOT NULL,
    scheduled_timezone character varying(64) NOT NULL,
    duration_minutes integer NOT NULL,
    meeting_format character varying(20) NOT NULL,
    effective_on date,
    cancelled_by character varying(20),
    previous_action_item_id character varying(32) NOT NULL,
    action_item_id character varying(32) NOT NULL,
    recording_method character varying(16) NOT NULL,
    idempotency_key_hash character varying(64) NOT NULL,
    occurred_at timestamp with time zone NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_application_interview_round_events_action_replaced CHECK (((previous_action_item_id)::text <> (action_item_id)::text)),
    CONSTRAINT ck_application_interview_round_events_cancelled_by CHECK (((cancelled_by IS NULL) OR ((cancelled_by)::text = ANY ((ARRAY['employer'::character varying, 'candidate'::character varying, 'mutual'::character varying, 'unknown'::character varying])::text[])))),
    CONSTRAINT ck_application_interview_round_events_duration_minutes CHECK (((duration_minutes >= 15) AND (duration_minutes <= 480))),
    CONSTRAINT ck_application_interview_round_events_event_shape CHECK (((((event_type)::text = 'scheduled'::text) AND (sequence_number = 1) AND (from_status IS NULL) AND ((to_status)::text = 'scheduled'::text) AND (effective_on IS NULL) AND (cancelled_by IS NULL)) OR (((event_type)::text = 'rescheduled'::text) AND (sequence_number >= 2) AND ((from_status)::text = 'scheduled'::text) AND ((to_status)::text = 'scheduled'::text) AND (effective_on IS NULL) AND (cancelled_by IS NULL)) OR (((event_type)::text = 'completed'::text) AND (sequence_number >= 2) AND ((from_status)::text = 'scheduled'::text) AND ((to_status)::text = 'completed'::text) AND (effective_on IS NOT NULL) AND (cancelled_by IS NULL)) OR (((event_type)::text = 'cancelled'::text) AND (sequence_number >= 2) AND ((from_status)::text = 'scheduled'::text) AND ((to_status)::text = 'cancelled'::text) AND (effective_on IS NOT NULL) AND (cancelled_by IS NOT NULL)))),
    CONSTRAINT ck_application_interview_round_events_event_type CHECK (((event_type)::text = ANY ((ARRAY['scheduled'::character varying, 'rescheduled'::character varying, 'completed'::character varying, 'cancelled'::character varying])::text[]))),
    CONSTRAINT ck_application_interview_round_events_from_status CHECK (((from_status IS NULL) OR ((from_status)::text = 'scheduled'::text))),
    CONSTRAINT ck_application_interview_round_events_meeting_format CHECK (((meeting_format)::text = ANY ((ARRAY['video'::character varying, 'phone'::character varying, 'onsite'::character varying, 'unspecified'::character varying])::text[]))),
    CONSTRAINT ck_application_interview_round_events_mutation_hash CHECK ((length((idempotency_key_hash)::text) = 64)),
    CONSTRAINT ck_application_interview_round_events_recording_method CHECK (((recording_method)::text = 'manual'::text)),
    CONSTRAINT ck_application_interview_round_events_scheduled_timezone_length CHECK (((length(TRIM(BOTH FROM scheduled_timezone)) >= 1) AND (length(TRIM(BOTH FROM scheduled_timezone)) <= 64))),
    CONSTRAINT ck_application_interview_round_events_sequence_positive CHECK ((sequence_number >= 1)),
    CONSTRAINT ck_application_interview_round_events_to_status CHECK (((to_status)::text = ANY ((ARRAY['scheduled'::character varying, 'completed'::character varying, 'cancelled'::character varying])::text[])))
);


ALTER TABLE public.application_interview_round_events OWNER TO job_hunt;

--
-- Name: application_interview_rounds; Type: TABLE; Schema: public; Owner: job_hunt
--

CREATE TABLE public.application_interview_rounds (
    id character varying(32) NOT NULL,
    owner_id character varying(64) NOT NULL,
    application_id character varying(32) NOT NULL,
    application_submission_id character varying(32) NOT NULL,
    round_number integer NOT NULL,
    kind character varying(32) NOT NULL,
    title character varying(160) NOT NULL,
    status character varying(20) NOT NULL,
    scheduled_start_at timestamp with time zone NOT NULL,
    scheduled_timezone character varying(64) NOT NULL,
    duration_minutes integer NOT NULL,
    meeting_format character varying(20) NOT NULL,
    completed_on date,
    cancelled_on date,
    cancelled_by character varying(20),
    version integer NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_application_interview_rounds_cancelled_by CHECK (((cancelled_by IS NULL) OR ((cancelled_by)::text = ANY ((ARRAY['employer'::character varying, 'candidate'::character varying, 'mutual'::character varying, 'unknown'::character varying])::text[])))),
    CONSTRAINT ck_application_interview_rounds_duration_minutes CHECK (((duration_minutes >= 15) AND (duration_minutes <= 480))),
    CONSTRAINT ck_application_interview_rounds_kind CHECK (((kind)::text = ANY ((ARRAY['hiring_manager'::character varying, 'technical'::character varying, 'system_design'::character varying, 'behavioral'::character varying, 'case_study'::character varying, 'panel'::character varying, 'final'::character varying, 'other'::character varying])::text[]))),
    CONSTRAINT ck_application_interview_rounds_meeting_format CHECK (((meeting_format)::text = ANY ((ARRAY['video'::character varying, 'phone'::character varying, 'onsite'::character varying, 'unspecified'::character varying])::text[]))),
    CONSTRAINT ck_application_interview_rounds_round_number_positive CHECK ((round_number >= 1)),
    CONSTRAINT ck_application_interview_rounds_scheduled_timezone_length CHECK (((length(TRIM(BOTH FROM scheduled_timezone)) >= 1) AND (length(TRIM(BOTH FROM scheduled_timezone)) <= 64))),
    CONSTRAINT ck_application_interview_rounds_status CHECK (((status)::text = ANY ((ARRAY['scheduled'::character varying, 'completed'::character varying, 'cancelled'::character varying])::text[]))),
    CONSTRAINT ck_application_interview_rounds_status_shape CHECK (((((status)::text = 'scheduled'::text) AND (completed_on IS NULL) AND (cancelled_on IS NULL) AND (cancelled_by IS NULL)) OR (((status)::text = 'completed'::text) AND (completed_on IS NOT NULL) AND (cancelled_on IS NULL) AND (cancelled_by IS NULL)) OR (((status)::text = 'cancelled'::text) AND (cancelled_on IS NOT NULL) AND (cancelled_by IS NOT NULL) AND (completed_on IS NULL)))),
    CONSTRAINT ck_application_interview_rounds_title_length CHECK (((length(TRIM(BOTH FROM title)) >= 1) AND (length(TRIM(BOTH FROM title)) <= 160))),
    CONSTRAINT ck_application_interview_rounds_version_positive CHECK ((version >= 1))
);


ALTER TABLE public.application_interview_rounds OWNER TO job_hunt;

--
-- Name: application_metric_snapshots; Type: TABLE; Schema: public; Owner: job_hunt
--

CREATE TABLE public.application_metric_snapshots (
    id character varying(32) NOT NULL,
    owner_id character varying(64) NOT NULL,
    application_id character varying(32) NOT NULL,
    job_posting_id character varying(32) NOT NULL,
    pursued_posting_version_id character varying(32) NOT NULL,
    acquisition_source character varying(32) NOT NULL,
    attribution_status character varying(32) NOT NULL,
    saved_search_id character varying(32),
    saved_search_version integer,
    saved_search_name character varying(120),
    career_track_id character varying(32),
    career_track_version integer,
    career_track_name character varying(120),
    assessment_state character varying(20) NOT NULL,
    assessment_band character varying(20),
    assessment_algorithm_version character varying(64),
    assessment_reason character varying(32),
    recorded_at timestamp with time zone NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_application_metric_snapshots_acquisition_source CHECK (((acquisition_source)::text = ANY ((ARRAY['job_hunt_search'::character varying, 'referral'::character varying, 'recruiter_inbound'::character varying, 'direct_company'::character varying, 'job_board'::character varying, 'other'::character varying])::text[]))),
    CONSTRAINT ck_application_metric_snapshots_assessment_band CHECK (((assessment_band IS NULL) OR ((assessment_band)::text = ANY ((ARRAY['strong'::character varying, 'core'::character varying, 'stretch'::character varying])::text[])))),
    CONSTRAINT ck_application_metric_snapshots_assessment_reason CHECK (((assessment_reason IS NULL) OR ((assessment_reason)::text = ANY ((ARRAY['assessment_pending'::character varying, 'resume_unavailable'::character varying, 'description_unavailable'::character varying, 'not_requested'::character varying])::text[])))),
    CONSTRAINT ck_application_metric_snapshots_assessment_shape CHECK (((((assessment_state)::text = 'assessed'::text) AND (assessment_band IS NOT NULL) AND (assessment_algorithm_version IS NOT NULL) AND (assessment_reason IS NULL)) OR (((assessment_state)::text = 'not_assessed'::text) AND (assessment_band IS NULL) AND (assessment_algorithm_version IS NULL) AND (assessment_reason IS NOT NULL)))),
    CONSTRAINT ck_application_metric_snapshots_assessment_state CHECK (((assessment_state)::text = ANY ((ARRAY['assessed'::character varying, 'not_assessed'::character varying])::text[]))),
    CONSTRAINT ck_application_metric_snapshots_attribution_shape CHECK (((((attribution_status)::text = 'attribution_missing'::text) AND (saved_search_id IS NULL) AND (saved_search_version IS NULL) AND (saved_search_name IS NULL) AND (career_track_id IS NULL) AND (career_track_version IS NULL) AND (career_track_name IS NULL)) OR (((attribution_status)::text = 'captured'::text) AND ((acquisition_source)::text = 'job_hunt_search'::text) AND (saved_search_id IS NOT NULL) AND (saved_search_version IS NOT NULL) AND (saved_search_name IS NOT NULL) AND (career_track_id IS NOT NULL) AND (career_track_version IS NOT NULL) AND (career_track_name IS NOT NULL)) OR (((attribution_status)::text = 'captured'::text) AND ((acquisition_source)::text <> 'job_hunt_search'::text) AND (saved_search_id IS NULL) AND (saved_search_version IS NULL) AND (saved_search_name IS NULL) AND (career_track_id IS NULL) AND (career_track_version IS NULL) AND (career_track_name IS NULL)))),
    CONSTRAINT ck_application_metric_snapshots_attribution_status CHECK (((attribution_status)::text = ANY ((ARRAY['captured'::character varying, 'attribution_missing'::character varying])::text[]))),
    CONSTRAINT ck_application_metric_snapshots_career_track_version_positive CHECK (((career_track_version IS NULL) OR (career_track_version >= 1))),
    CONSTRAINT ck_application_metric_snapshots_saved_search_version_positive CHECK (((saved_search_version IS NULL) OR (saved_search_version >= 1)))
);


ALTER TABLE public.application_metric_snapshots OWNER TO job_hunt;

--
-- Name: application_milestone_corrections; Type: TABLE; Schema: public; Owner: job_hunt
--

CREATE TABLE public.application_milestone_corrections (
    id character varying(32) NOT NULL,
    owner_id character varying(64) NOT NULL,
    application_id character varying(32) NOT NULL,
    activity_event_id character varying(32) NOT NULL,
    correction_number integer NOT NULL,
    supersedes_correction_id character varying(32),
    previous_effective_on date NOT NULL,
    corrected_effective_on date NOT NULL,
    recording_method character varying(16) NOT NULL,
    recorded_at timestamp with time zone NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_application_milestone_corrections_chain_shape CHECK ((((correction_number = 1) AND (supersedes_correction_id IS NULL)) OR ((correction_number >= 2) AND (supersedes_correction_id IS NOT NULL)))),
    CONSTRAINT ck_application_milestone_corrections_date_changed CHECK ((previous_effective_on <> corrected_effective_on)),
    CONSTRAINT ck_application_milestone_corrections_number_range CHECK (((correction_number >= 1) AND (correction_number <= 50))),
    CONSTRAINT ck_application_milestone_corrections_recording_method CHECK (((recording_method)::text = 'manual'::text))
);


ALTER TABLE public.application_milestone_corrections OWNER TO job_hunt;

--
-- Name: application_outcomes; Type: TABLE; Schema: public; Owner: job_hunt
--

CREATE TABLE public.application_outcomes (
    id character varying(32) NOT NULL,
    owner_id character varying(64) NOT NULL,
    application_id character varying(32) NOT NULL,
    application_submission_id character varying(32),
    stage_at_outcome character varying(24) NOT NULL,
    outcome character varying(32) NOT NULL,
    outcome_on date NOT NULL,
    recording_method character varying(16) NOT NULL,
    recorded_at timestamp with time zone NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_application_outcomes_offer_outcome_stage CHECK (((((outcome)::text = ANY ((ARRAY['offer_accepted'::character varying, 'offer_declined'::character varying])::text[])) AND ((stage_at_outcome)::text = 'offer'::text)) OR ((outcome)::text <> ALL ((ARRAY['offer_accepted'::character varying, 'offer_declined'::character varying])::text[])))),
    CONSTRAINT ck_application_outcomes_outcome CHECK (((outcome)::text = ANY ((ARRAY['rejected'::character varying, 'withdrawn'::character varying, 'offer_accepted'::character varying, 'offer_declined'::character varying, 'no_response'::character varying, 'posting_closed'::character varying])::text[]))),
    CONSTRAINT ck_application_outcomes_recording_method CHECK (((recording_method)::text = 'manual'::text)),
    CONSTRAINT ck_application_outcomes_stage_at_outcome CHECK (((stage_at_outcome)::text = ANY ((ARRAY['pursuing'::character varying, 'ready_to_apply'::character varying, 'applied'::character varying, 'screening'::character varying, 'interviewing'::character varying, 'offer'::character varying])::text[]))),
    CONSTRAINT ck_application_outcomes_submission_shape CHECK (((((stage_at_outcome)::text = ANY ((ARRAY['pursuing'::character varying, 'ready_to_apply'::character varying])::text[])) AND ((outcome)::text = ANY ((ARRAY['withdrawn'::character varying, 'posting_closed'::character varying])::text[])) AND (application_submission_id IS NULL)) OR (((stage_at_outcome)::text = ANY ((ARRAY['applied'::character varying, 'screening'::character varying, 'interviewing'::character varying, 'offer'::character varying])::text[])) AND (application_submission_id IS NOT NULL))))
);


ALTER TABLE public.application_outcomes OWNER TO job_hunt;

--
-- Name: application_pack_events; Type: TABLE; Schema: public; Owner: job_hunt
--

CREATE TABLE public.application_pack_events (
    id character varying(32) NOT NULL,
    owner_id character varying(64) NOT NULL,
    application_id character varying(32) NOT NULL,
    application_pack_id character varying(32) NOT NULL,
    revision_id character varying(32) NOT NULL,
    sequence_number integer NOT NULL,
    event_type character varying(16) NOT NULL,
    occurred_at timestamp with time zone NOT NULL,
    idempotency_key_hash character varying(64) NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_application_pack_events_event_type CHECK (((event_type)::text = 'reviewed'::text)),
    CONSTRAINT ck_application_pack_events_mutation_hash CHECK ((length((idempotency_key_hash)::text) = 64)),
    CONSTRAINT ck_application_pack_events_sequence_number_positive CHECK ((sequence_number >= 1))
);


ALTER TABLE public.application_pack_events OWNER TO job_hunt;

--
-- Name: application_pack_revisions; Type: TABLE; Schema: public; Owner: job_hunt
--

CREATE TABLE public.application_pack_revisions (
    id character varying(32) NOT NULL,
    owner_id character varying(64) NOT NULL,
    application_id character varying(32) NOT NULL,
    application_pack_id character varying(32) NOT NULL,
    parent_revision_id character varying(32),
    revision_number integer NOT NULL,
    source character varying(16) NOT NULL,
    encrypted_payload text NOT NULL,
    encryption_key_id character varying(32) NOT NULL,
    content_hash character varying(64) NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_application_pack_revisions_content_hash CHECK ((length((content_hash)::text) = 64)),
    CONSTRAINT ck_application_pack_revisions_encrypted_payload_envelope CHECK (((length(TRIM(BOTH FROM encrypted_payload)) >= 1) AND ((length(TRIM(BOTH FROM encryption_key_id)) >= 1) AND (length(TRIM(BOTH FROM encryption_key_id)) <= 32)))),
    CONSTRAINT ck_application_pack_revisions_parent_not_self CHECK (((parent_revision_id IS NULL) OR ((parent_revision_id)::text <> (id)::text))),
    CONSTRAINT ck_application_pack_revisions_revision_number_positive CHECK ((revision_number >= 1)),
    CONSTRAINT ck_application_pack_revisions_source CHECK (((source)::text = ANY ((ARRAY['extracted'::character varying, 'edited'::character varying])::text[])))
);


ALTER TABLE public.application_pack_revisions OWNER TO job_hunt;

--
-- Name: application_packs; Type: TABLE; Schema: public; Owner: job_hunt
--

CREATE TABLE public.application_packs (
    id character varying(32) NOT NULL,
    owner_id character varying(64) NOT NULL,
    application_id character varying(32) NOT NULL,
    job_posting_id character varying(32) NOT NULL,
    posting_version_id character varying(32) NOT NULL,
    base_resume_version_id character varying(32) NOT NULL,
    version integer NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_application_packs_version_positive CHECK ((version >= 1))
);


ALTER TABLE public.application_packs OWNER TO job_hunt;

--
-- Name: application_submissions; Type: TABLE; Schema: public; Owner: job_hunt
--

CREATE TABLE public.application_submissions (
    id character varying(32) NOT NULL,
    owner_id character varying(64) NOT NULL,
    application_id character varying(32) NOT NULL,
    application_pack_id character varying(32) NOT NULL,
    application_pack_revision_id character varying(32) NOT NULL,
    application_pack_review_event_id character varying(32) NOT NULL,
    application_artifact_revision_id character varying(32) NOT NULL,
    application_artifact_approval_event_id character varying(32) NOT NULL,
    tailored_resume_version_id character varying(32) NOT NULL,
    destination_url text NOT NULL,
    applied_on date NOT NULL,
    submission_method character varying(16) NOT NULL,
    recorded_at timestamp with time zone NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_application_submissions_destination_url CHECK ((((length(destination_url) >= 9) AND (length(destination_url) <= 2048)) AND (destination_url ~~ 'https://%'::text))),
    CONSTRAINT ck_application_submissions_submission_method CHECK (((submission_method)::text = 'manual'::text))
);


ALTER TABLE public.application_submissions OWNER TO job_hunt;

--
-- Name: applications; Type: TABLE; Schema: public; Owner: job_hunt
--

CREATE TABLE public.applications (
    id character varying(32) NOT NULL,
    owner_id character varying(64) NOT NULL,
    owner_opportunity_id character varying(32) NOT NULL,
    job_posting_id character varying(32) NOT NULL,
    pursued_posting_version_id character varying(32) NOT NULL,
    stage character varying(24) NOT NULL,
    version integer NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    outcome_id character varying(32),
    CONSTRAINT ck_applications_outcome_shape CHECK (((((stage)::text = 'closed'::text) AND (outcome_id IS NOT NULL)) OR (((stage)::text <> 'closed'::text) AND (outcome_id IS NULL)))),
    CONSTRAINT ck_applications_stage CHECK (((stage)::text = ANY ((ARRAY['pursuing'::character varying, 'ready_to_apply'::character varying, 'applied'::character varying, 'screening'::character varying, 'interviewing'::character varying, 'offer'::character varying, 'closed'::character varying])::text[]))),
    CONSTRAINT ck_applications_version_positive CHECK ((version >= 1))
);


ALTER TABLE public.applications OWNER TO job_hunt;

--
-- Name: background_job_events; Type: TABLE; Schema: public; Owner: job_hunt
--

CREATE TABLE public.background_job_events (
    id integer NOT NULL,
    job_id character varying(32) NOT NULL,
    from_status character varying(32),
    to_status character varying(32) NOT NULL,
    actor character varying(200) NOT NULL,
    reason character varying(200),
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.background_job_events OWNER TO job_hunt;

--
-- Name: background_job_events_id_seq; Type: SEQUENCE; Schema: public; Owner: job_hunt
--

CREATE SEQUENCE public.background_job_events_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.background_job_events_id_seq OWNER TO job_hunt;

--
-- Name: background_job_events_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: job_hunt
--

ALTER SEQUENCE public.background_job_events_id_seq OWNED BY public.background_job_events.id;


--
-- Name: background_jobs; Type: TABLE; Schema: public; Owner: job_hunt
--

CREATE TABLE public.background_jobs (
    id character varying(32) NOT NULL,
    kind character varying(64) NOT NULL,
    owner_id character varying(64),
    subject_type character varying(64),
    subject_id character varying(128),
    payload json DEFAULT '{}'::json NOT NULL,
    dedupe_key character varying(255) NOT NULL,
    status character varying(32) DEFAULT 'queued'::character varying NOT NULL,
    priority integer DEFAULT 100 NOT NULL,
    attempt_count integer DEFAULT 0 NOT NULL,
    max_attempts integer DEFAULT 3 NOT NULL,
    run_after timestamp with time zone DEFAULT now() NOT NULL,
    lease_owner character varying(200),
    lease_token character varying(64),
    lease_expires_at timestamp with time zone,
    heartbeat_at timestamp with time zone,
    stage character varying(100) DEFAULT 'queued'::character varying NOT NULL,
    stage_checkpoint character varying(200),
    last_error character varying(200),
    version integer DEFAULT 1 NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    started_at timestamp with time zone,
    completed_at timestamp with time zone,
    failed_at timestamp with time zone,
    cancel_requested_at timestamp with time zone,
    cancelled_at timestamp with time zone,
    dead_lettered_at timestamp with time zone,
    dedupe_scope character varying(72) DEFAULT 'system'::character varying NOT NULL,
    CONSTRAINT ck_background_jobs_attempt_count_nonnegative CHECK ((attempt_count >= 0)),
    CONSTRAINT ck_background_jobs_dedupe_scope_matches_owner CHECK ((((owner_id IS NULL) AND ((dedupe_scope)::text = 'system'::text)) OR ((owner_id IS NOT NULL) AND ((dedupe_scope)::text = ('owner:'::text || (owner_id)::text))))),
    CONSTRAINT ck_background_jobs_max_attempts_positive CHECK ((max_attempts > 0)),
    CONSTRAINT ck_background_jobs_status CHECK (((status)::text = ANY ((ARRAY['queued'::character varying, 'running'::character varying, 'succeeded'::character varying, 'failed'::character varying, 'cancelled'::character varying, 'dead_letter'::character varying])::text[])))
);


ALTER TABLE public.background_jobs OWNER TO job_hunt;

--
-- Name: candidate_profiles; Type: TABLE; Schema: public; Owner: job_hunt
--

CREATE TABLE public.candidate_profiles (
    id character varying(32) NOT NULL,
    owner_id character varying(64) NOT NULL,
    encrypted_payload text NOT NULL,
    encryption_key_id character varying(32) NOT NULL,
    onboarding_state character varying(20) NOT NULL,
    version integer NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_candidate_profiles_onboarding_state CHECK (((onboarding_state)::text = ANY ((ARRAY['profile'::character varying, 'resume'::character varying, 'career_track'::character varying, 'evidence'::character varying, 'saved_search'::character varying, 'complete'::character varying])::text[]))),
    CONSTRAINT ck_candidate_profiles_version_positive CHECK ((version >= 1))
);


ALTER TABLE public.candidate_profiles OWNER TO job_hunt;

--
-- Name: career_tracks; Type: TABLE; Schema: public; Owner: job_hunt
--

CREATE TABLE public.career_tracks (
    id character varying(32) NOT NULL,
    owner_id character varying(64) NOT NULL,
    name character varying(120) NOT NULL,
    role_families json NOT NULL,
    seniority_levels json NOT NULL,
    target_locations json NOT NULL,
    priorities json NOT NULL,
    active boolean NOT NULL,
    version integer NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_career_tracks_version_positive CHECK ((version >= 1))
);


ALTER TABLE public.career_tracks OWNER TO job_hunt;

--
-- Name: contact_plans; Type: TABLE; Schema: public; Owner: job_hunt
--

CREATE TABLE public.contact_plans (
    id character varying(32) NOT NULL,
    owner_id character varying(64) NOT NULL,
    application_id character varying(32) NOT NULL,
    plan_number integer NOT NULL,
    status character varying(20) NOT NULL,
    target_count integer NOT NULL,
    candidate_limit integer NOT NULL,
    confidence_floor double precision NOT NULL,
    policy_version character varying(64) NOT NULL,
    scoring_version character varying(64) NOT NULL,
    background_job_id character varying(32),
    discovered_count integer NOT NULL,
    verified_count integer NOT NULL,
    selected_count integer NOT NULL,
    coverage_status character varying(16) NOT NULL,
    exhausted boolean NOT NULL,
    retryable boolean NOT NULL,
    shortfall_reasons json NOT NULL,
    error_code character varying(100),
    version integer NOT NULL,
    started_at timestamp with time zone,
    finalized_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_contact_plans_active_job_required CHECK ((((status)::text <> ALL ((ARRAY['queued'::character varying, 'running'::character varying])::text[])) OR (background_job_id IS NOT NULL))),
    CONSTRAINT ck_contact_plans_candidate_limit_bounded CHECK (((candidate_limit >= target_count) AND (candidate_limit <= 12))),
    CONSTRAINT ck_contact_plans_completion_coverage CHECK (((((status)::text = 'completed'::text) AND ((coverage_status)::text = ANY ((ARRAY['met'::character varying, 'partial'::character varying])::text[]))) OR (((status)::text <> 'completed'::text) AND ((coverage_status)::text = 'pending'::text)))),
    CONSTRAINT ck_contact_plans_confidence_floor CHECK (((confidence_floor >= (0.75)::double precision) AND (confidence_floor <= (1.0)::double precision))),
    CONSTRAINT ck_contact_plans_counts_ordered CHECK (((discovered_count >= 0) AND (verified_count >= 0) AND (selected_count >= 0) AND (selected_count <= verified_count) AND (verified_count <= discovered_count) AND (discovered_count <= candidate_limit))),
    CONSTRAINT ck_contact_plans_coverage_counts CHECK (((((coverage_status)::text = 'met'::text) AND (selected_count = target_count)) OR (((coverage_status)::text = 'partial'::text) AND (selected_count < target_count)) OR ((coverage_status)::text = 'pending'::text))),
    CONSTRAINT ck_contact_plans_coverage_status CHECK (((coverage_status)::text = ANY ((ARRAY['pending'::character varying, 'met'::character varying, 'partial'::character varying])::text[]))),
    CONSTRAINT ck_contact_plans_exhausted_only_when_completed CHECK (((exhausted = false) OR ((status)::text = 'completed'::text))),
    CONSTRAINT ck_contact_plans_failure_code CHECK (((((status)::text = 'failed'::text) AND (error_code IS NOT NULL)) OR (((status)::text <> 'failed'::text) AND (error_code IS NULL)))),
    CONSTRAINT ck_contact_plans_plan_number_positive CHECK ((plan_number >= 1)),
    CONSTRAINT ck_contact_plans_status CHECK (((status)::text = ANY ((ARRAY['queued'::character varying, 'running'::character varying, 'completed'::character varying, 'failed'::character varying, 'cancelled'::character varying])::text[]))),
    CONSTRAINT ck_contact_plans_status_timestamps CHECK (((((status)::text = 'queued'::text) AND (started_at IS NULL) AND (finalized_at IS NULL)) OR (((status)::text = 'running'::text) AND (started_at IS NOT NULL) AND (finalized_at IS NULL)) OR (((status)::text = ANY ((ARRAY['completed'::character varying, 'failed'::character varying, 'cancelled'::character varying])::text[])) AND (finalized_at IS NOT NULL)))),
    CONSTRAINT ck_contact_plans_target_count_five CHECK ((target_count = 5)),
    CONSTRAINT ck_contact_plans_version_positive CHECK ((version >= 1))
);


ALTER TABLE public.contact_plans OWNER TO job_hunt;

--
-- Name: contacts; Type: TABLE; Schema: public; Owner: job_hunt
--

CREATE TABLE public.contacts (
    id character varying(32) NOT NULL,
    owner_id character varying(64) NOT NULL,
    identity_key text NOT NULL,
    identity_key_hash character varying(64) NOT NULL,
    profile_url text NOT NULL,
    normalized_profile_url text NOT NULL,
    profile_source character varying(24) NOT NULL,
    public_name character varying(200) NOT NULL,
    lifecycle character varying(24) NOT NULL,
    do_not_contact_at timestamp with time zone,
    version integer NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_contacts_do_not_contact_timestamp CHECK (((((lifecycle)::text = 'do_not_contact'::text) AND (do_not_contact_at IS NOT NULL)) OR (((lifecycle)::text <> 'do_not_contact'::text) AND (do_not_contact_at IS NULL)))),
    CONSTRAINT ck_contacts_lifecycle CHECK (((lifecycle)::text = ANY ((ARRAY['active'::character varying, 'do_not_contact'::character varying, 'retired'::character varying])::text[]))),
    CONSTRAINT ck_contacts_name CHECK (((length(TRIM(BOTH FROM public_name)) >= 1) AND (length(TRIM(BOTH FROM public_name)) <= 200))),
    CONSTRAINT ck_contacts_normalized_profile_url CHECK ((((length(normalized_profile_url) >= 9) AND (length(normalized_profile_url) <= 2048)) AND (normalized_profile_url ~~ 'https://%'::text))),
    CONSTRAINT ck_contacts_profile_source CHECK (((profile_source)::text = ANY ((ARRAY['linkedin'::character varying, 'github'::character varying, 'company_page'::character varying, 'other'::character varying])::text[]))),
    CONSTRAINT ck_contacts_profile_url CHECK ((((length(profile_url) >= 9) AND (length(profile_url) <= 2048)) AND (profile_url ~~ 'https://%'::text))),
    CONSTRAINT ck_contacts_version_positive CHECK ((version >= 1))
);


ALTER TABLE public.contacts OWNER TO job_hunt;

--
-- Name: hunt_outcomes; Type: TABLE; Schema: public; Owner: job_hunt
--

CREATE TABLE public.hunt_outcomes (
    id integer NOT NULL,
    hunt_run_id character varying(32) NOT NULL,
    draft_id character varying(128) NOT NULL,
    encrypted_payload text NOT NULL,
    encryption_key_id character varying(32) NOT NULL,
    logged_at timestamp with time zone NOT NULL
);


ALTER TABLE public.hunt_outcomes OWNER TO job_hunt;

--
-- Name: hunt_outcomes_id_seq; Type: SEQUENCE; Schema: public; Owner: job_hunt
--

CREATE SEQUENCE public.hunt_outcomes_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.hunt_outcomes_id_seq OWNER TO job_hunt;

--
-- Name: hunt_outcomes_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: job_hunt
--

ALTER SEQUENCE public.hunt_outcomes_id_seq OWNED BY public.hunt_outcomes.id;


--
-- Name: hunt_runs; Type: TABLE; Schema: public; Owner: job_hunt
--

CREATE TABLE public.hunt_runs (
    id character varying(32) NOT NULL,
    owner_id character varying(64) NOT NULL,
    background_job_id character varying(32) NOT NULL,
    access_hash character varying(64) NOT NULL,
    idempotency_key_hash character varying(64),
    request_hash character varying(64) NOT NULL,
    encrypted_request text,
    request_key_id character varying(32),
    request_expires_at timestamp with time zone NOT NULL,
    encrypted_result text,
    result_key_id character varying(32),
    access_expires_at timestamp with time zone NOT NULL,
    request_cleared_at timestamp with time zone,
    completed_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_hunt_runs_request_envelope_complete CHECK ((((encrypted_request IS NULL) AND (request_key_id IS NULL)) OR ((encrypted_request IS NOT NULL) AND (request_key_id IS NOT NULL)))),
    CONSTRAINT ck_hunt_runs_result_envelope_complete CHECK ((((encrypted_result IS NULL) AND (result_key_id IS NULL)) OR ((encrypted_result IS NOT NULL) AND (result_key_id IS NOT NULL))))
);


ALTER TABLE public.hunt_runs OWNER TO job_hunt;

--
-- Name: job_observations; Type: TABLE; Schema: public; Owner: job_hunt
--

CREATE TABLE public.job_observations (
    id character varying(32) NOT NULL,
    owner_id character varying(64) NOT NULL,
    opportunity_scan_id character varying(32) NOT NULL,
    opportunity_scan_source_id character varying(32) NOT NULL,
    job_posting_id character varying(32) NOT NULL,
    job_posting_version_id character varying(32) NOT NULL,
    job_posting_alias_id character varying(32) NOT NULL,
    first_party_url_verified boolean NOT NULL,
    observed_at timestamp with time zone NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.job_observations OWNER TO job_hunt;

--
-- Name: job_posting_aliases; Type: TABLE; Schema: public; Owner: job_hunt
--

CREATE TABLE public.job_posting_aliases (
    id character varying(32) NOT NULL,
    owner_id character varying(64) NOT NULL,
    job_posting_id character varying(32) NOT NULL,
    alias_kind character varying(16) NOT NULL,
    alias_key text NOT NULL,
    alias_key_hash character varying(64) NOT NULL,
    source character varying(64) NOT NULL,
    company_slug character varying(120) NOT NULL,
    source_job_id character varying(512),
    normalized_url text,
    first_seen_at timestamp with time zone NOT NULL,
    last_seen_at timestamp with time zone NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_job_posting_aliases_alias_kind CHECK (((alias_kind)::text = ANY ((ARRAY['native'::character varying, 'url'::character varying])::text[]))),
    CONSTRAINT ck_job_posting_aliases_alias_shape CHECK (((((alias_kind)::text = 'native'::text) AND (source_job_id IS NOT NULL) AND (normalized_url IS NULL)) OR (((alias_kind)::text = 'url'::text) AND (source_job_id IS NULL) AND (normalized_url IS NOT NULL)))),
    CONSTRAINT ck_job_posting_aliases_seen_order CHECK ((last_seen_at >= first_seen_at))
);


ALTER TABLE public.job_posting_aliases OWNER TO job_hunt;

--
-- Name: job_posting_versions; Type: TABLE; Schema: public; Owner: job_hunt
--

CREATE TABLE public.job_posting_versions (
    id character varying(32) NOT NULL,
    owner_id character varying(64) NOT NULL,
    job_posting_id character varying(32) NOT NULL,
    version_number integer NOT NULL,
    content_hash character varying(64) NOT NULL,
    source character varying(64) NOT NULL,
    source_job_id character varying(512),
    company_name character varying(240) NOT NULL,
    title character varying(300) NOT NULL,
    canonical_url text NOT NULL,
    apply_urls json NOT NULL,
    location character varying(500) NOT NULL,
    summary text NOT NULL,
    description text,
    employment_type character varying(20) NOT NULL,
    posted_at_text character varying(100),
    source_updated_at_text character varying(100),
    source_facts json NOT NULL,
    source_confidence double precision NOT NULL,
    observed_at timestamp with time zone NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_job_posting_versions_confidence_range CHECK (((source_confidence >= (0)::double precision) AND (source_confidence <= (1)::double precision))),
    CONSTRAINT ck_job_posting_versions_employment_type CHECK (((employment_type)::text = ANY ((ARRAY['full_time'::character varying, 'contract'::character varying, 'intern'::character varying, 'unknown'::character varying])::text[]))),
    CONSTRAINT ck_job_posting_versions_number_positive CHECK ((version_number >= 1))
);


ALTER TABLE public.job_posting_versions OWNER TO job_hunt;

--
-- Name: job_postings; Type: TABLE; Schema: public; Owner: job_hunt
--

CREATE TABLE public.job_postings (
    id character varying(32) NOT NULL,
    owner_id character varying(64) NOT NULL,
    identity_kind character varying(16) NOT NULL,
    identity_key text NOT NULL,
    identity_key_hash character varying(64) NOT NULL,
    source character varying(64) NOT NULL,
    company_slug character varying(120) NOT NULL,
    source_job_id character varying(512),
    canonical_url text NOT NULL,
    lifecycle_state character varying(16) NOT NULL,
    closure_reason character varying(32),
    consecutive_complete_omissions integer NOT NULL,
    first_confirmed_at timestamp with time zone NOT NULL,
    last_confirmed_at timestamp with time zone NOT NULL,
    last_changed_at timestamp with time zone,
    last_lifecycle_evaluated_at timestamp with time zone,
    closed_at timestamp with time zone,
    version integer NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_job_postings_closure_reason CHECK (((closure_reason IS NULL) OR ((closure_reason)::text = ANY ((ARRAY['explicit'::character varying, 'two_complete_omissions'::character varying])::text[])))),
    CONSTRAINT ck_job_postings_confirmed_order CHECK ((last_confirmed_at >= first_confirmed_at)),
    CONSTRAINT ck_job_postings_identity_kind CHECK (((identity_kind)::text = ANY ((ARRAY['native'::character varying, 'url'::character varying])::text[]))),
    CONSTRAINT ck_job_postings_identity_shape CHECK (((((identity_kind)::text = 'native'::text) AND (source_job_id IS NOT NULL)) OR (((identity_kind)::text = 'url'::text) AND (source_job_id IS NULL)))),
    CONSTRAINT ck_job_postings_lifecycle_state CHECK (((lifecycle_state)::text = ANY ((ARRAY['open'::character varying, 'closed'::character varying])::text[]))),
    CONSTRAINT ck_job_postings_lifecycle_timestamps CHECK (((((lifecycle_state)::text = 'open'::text) AND (closed_at IS NULL) AND (closure_reason IS NULL)) OR (((lifecycle_state)::text = 'closed'::text) AND (closed_at IS NOT NULL) AND (closure_reason IS NOT NULL)))),
    CONSTRAINT ck_job_postings_omission_closure_threshold CHECK ((((closure_reason)::text <> 'two_complete_omissions'::text) OR (consecutive_complete_omissions >= 2))),
    CONSTRAINT ck_job_postings_omissions_nonnegative CHECK ((consecutive_complete_omissions >= 0)),
    CONSTRAINT ck_job_postings_version_positive CHECK ((version >= 1))
);


ALTER TABLE public.job_postings OWNER TO job_hunt;

--
-- Name: opportunity_decision_events; Type: TABLE; Schema: public; Owner: job_hunt
--

CREATE TABLE public.opportunity_decision_events (
    id character varying(32) NOT NULL,
    owner_id character varying(64) NOT NULL,
    owner_opportunity_id character varying(32) NOT NULL,
    job_posting_id character varying(32) NOT NULL,
    posting_version_id character varying(32) NOT NULL,
    previous_decision character varying(16) NOT NULL,
    new_decision character varying(16) NOT NULL,
    reason_code character varying(64),
    encrypted_note text,
    note_key_id character varying(32),
    compensates_event_id character varying(32),
    idempotency_key_hash character varying(64) NOT NULL,
    request_hash character varying(64) NOT NULL,
    occurred_at timestamp with time zone NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_opportunity_decision_events_decision_changed CHECK (((previous_decision)::text <> (new_decision)::text)),
    CONSTRAINT ck_opportunity_decision_events_decision_reason CHECK (((((new_decision)::text = 'dismiss'::text) AND (reason_code IS NOT NULL)) OR (((new_decision)::text = ANY ((ARRAY['inbox'::character varying, 'watch'::character varying, 'pursued'::character varying])::text[])) AND (reason_code IS NULL)))),
    CONSTRAINT ck_opportunity_decision_events_decision_values CHECK ((((previous_decision)::text = ANY ((ARRAY['inbox'::character varying, 'watch'::character varying, 'dismiss'::character varying, 'pursued'::character varying])::text[])) AND ((new_decision)::text = ANY ((ARRAY['inbox'::character varying, 'watch'::character varying, 'dismiss'::character varying, 'pursued'::character varying])::text[])))),
    CONSTRAINT ck_opportunity_decision_events_not_self_compensating CHECK (((compensates_event_id IS NULL) OR ((compensates_event_id)::text <> (id)::text))),
    CONSTRAINT ck_opportunity_decision_events_note_envelope_complete CHECK ((((encrypted_note IS NULL) AND (note_key_id IS NULL)) OR ((encrypted_note IS NOT NULL) AND (note_key_id IS NOT NULL))))
);


ALTER TABLE public.opportunity_decision_events OWNER TO job_hunt;

--
-- Name: opportunity_scan_sources; Type: TABLE; Schema: public; Owner: job_hunt
--

CREATE TABLE public.opportunity_scan_sources (
    id character varying(32) NOT NULL,
    owner_id character varying(64) NOT NULL,
    opportunity_scan_id character varying(32) NOT NULL,
    company_slug character varying(120) NOT NULL,
    source character varying(64) NOT NULL,
    status character varying(20) NOT NULL,
    fetch_scope character varying(24) NOT NULL,
    completeness character varying(16) NOT NULL,
    observed_count integer NOT NULL,
    returned_count integer NOT NULL,
    persisted_count integer NOT NULL,
    warning_codes json NOT NULL,
    error_code character varying(100),
    used_fallback boolean NOT NULL,
    cache_hit boolean NOT NULL,
    version integer NOT NULL,
    started_at timestamp with time zone,
    completed_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_opportunity_scan_sources_complete_board_only CHECK ((((completeness)::text <> 'complete'::text) OR (((status)::text = 'succeeded'::text) AND ((fetch_scope)::text = 'board_snapshot'::text)))),
    CONSTRAINT ck_opportunity_scan_sources_completeness CHECK (((completeness)::text = ANY ((ARRAY['unknown'::character varying, 'partial'::character varying, 'complete'::character varying])::text[]))),
    CONSTRAINT ck_opportunity_scan_sources_counts_ordered CHECK (((observed_count >= 0) AND (returned_count >= 0) AND (persisted_count >= 0) AND (returned_count <= observed_count) AND (persisted_count <= returned_count))),
    CONSTRAINT ck_opportunity_scan_sources_failure_code CHECK (((((status)::text = 'failed'::text) AND (error_code IS NOT NULL)) OR (((status)::text <> 'failed'::text) AND (error_code IS NULL)))),
    CONSTRAINT ck_opportunity_scan_sources_fetch_scope CHECK (((fetch_scope)::text = ANY ((ARRAY['criteria_filtered'::character varying, 'board_snapshot'::character varying])::text[]))),
    CONSTRAINT ck_opportunity_scan_sources_status CHECK (((status)::text = ANY ((ARRAY['pending'::character varying, 'running'::character varying, 'succeeded'::character varying, 'failed'::character varying, 'cancelled'::character varying])::text[]))),
    CONSTRAINT ck_opportunity_scan_sources_timestamps_match_status CHECK (((((status)::text = 'pending'::text) AND (started_at IS NULL) AND (completed_at IS NULL)) OR (((status)::text = 'running'::text) AND (started_at IS NOT NULL) AND (completed_at IS NULL)) OR (((status)::text = ANY ((ARRAY['succeeded'::character varying, 'failed'::character varying])::text[])) AND (started_at IS NOT NULL) AND (completed_at IS NOT NULL)) OR (((status)::text = 'cancelled'::text) AND (completed_at IS NOT NULL)))),
    CONSTRAINT ck_opportunity_scan_sources_version_positive CHECK ((version >= 1))
);


ALTER TABLE public.opportunity_scan_sources OWNER TO job_hunt;

--
-- Name: opportunity_scans; Type: TABLE; Schema: public; Owner: job_hunt
--

CREATE TABLE public.opportunity_scans (
    id character varying(32) NOT NULL,
    owner_id character varying(64) NOT NULL,
    saved_search_id character varying(32) NOT NULL,
    saved_search_version integer NOT NULL,
    criteria_schema_version integer NOT NULL,
    criteria_snapshot json NOT NULL,
    pack_snapshot character varying(64) NOT NULL,
    trigger character varying(16) NOT NULL,
    scheduled_for timestamp with time zone NOT NULL,
    dedupe_key character varying(255) NOT NULL,
    idempotency_key_hash character varying(64),
    request_hash character varying(64) NOT NULL,
    background_job_id character varying(32),
    status character varying(20) NOT NULL,
    stage character varying(100) NOT NULL,
    source_count integer NOT NULL,
    terminal_source_count integer NOT NULL,
    successful_source_count integer NOT NULL,
    failed_source_count integer NOT NULL,
    observed_count integer NOT NULL,
    new_posting_count integer NOT NULL,
    changed_posting_count integer NOT NULL,
    new_opportunity_count integer NOT NULL,
    version integer NOT NULL,
    started_at timestamp with time zone,
    finalized_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_opportunity_scans_counts_nonnegative CHECK (((source_count >= 0) AND (terminal_source_count >= 0) AND (successful_source_count >= 0) AND (failed_source_count >= 0) AND (observed_count >= 0) AND (new_posting_count >= 0) AND (changed_posting_count >= 0) AND (new_opportunity_count >= 0))),
    CONSTRAINT ck_opportunity_scans_criteria_version_positive CHECK ((criteria_schema_version >= 1)),
    CONSTRAINT ck_opportunity_scans_finalized_timestamp CHECK (((((status)::text = ANY ((ARRAY['succeeded'::character varying, 'partial'::character varying, 'failed'::character varying, 'cancelled'::character varying])::text[])) AND (finalized_at IS NOT NULL)) OR (((status)::text = ANY ((ARRAY['queued'::character varying, 'running'::character varying])::text[])) AND (finalized_at IS NULL)))),
    CONSTRAINT ck_opportunity_scans_search_version_positive CHECK ((saved_search_version >= 1)),
    CONSTRAINT ck_opportunity_scans_source_counts_ordered CHECK (((terminal_source_count <= source_count) AND (successful_source_count <= terminal_source_count) AND (failed_source_count <= terminal_source_count) AND ((successful_source_count + failed_source_count) <= terminal_source_count))),
    CONSTRAINT ck_opportunity_scans_stage_nonempty CHECK (((stage)::text <> ''::text)),
    CONSTRAINT ck_opportunity_scans_started_timestamp CHECK ((((status)::text <> ALL ((ARRAY['running'::character varying, 'succeeded'::character varying, 'partial'::character varying, 'failed'::character varying])::text[])) OR (started_at IS NOT NULL))),
    CONSTRAINT ck_opportunity_scans_status CHECK (((status)::text = ANY ((ARRAY['queued'::character varying, 'running'::character varying, 'succeeded'::character varying, 'partial'::character varying, 'failed'::character varying, 'cancelled'::character varying])::text[]))),
    CONSTRAINT ck_opportunity_scans_trigger CHECK (((trigger)::text = ANY ((ARRAY['manual'::character varying, 'scheduled'::character varying])::text[]))),
    CONSTRAINT ck_opportunity_scans_version_positive CHECK ((version >= 1))
);


ALTER TABLE public.opportunity_scans OWNER TO job_hunt;

--
-- Name: outreach_events; Type: TABLE; Schema: public; Owner: job_hunt
--

CREATE TABLE public.outreach_events (
    id character varying(32) NOT NULL,
    owner_id character varying(64) NOT NULL,
    application_id character varying(32) NOT NULL,
    outreach_sequence_id character varying(32) NOT NULL,
    application_contact_id character varying(32),
    message_version_id character varying(32),
    sequence_number integer NOT NULL,
    event_type character varying(32) NOT NULL,
    kind character varying(20),
    channel character varying(20),
    outcome character varying(32),
    reason_code character varying(100),
    wave integer,
    follow_up_due_at timestamp with time zone,
    encrypted_note text,
    note_key_id character varying(32),
    occurred_at timestamp with time zone NOT NULL,
    idempotency_key_hash character varying(64) NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_outreach_events_channel CHECK (((channel IS NULL) OR ((channel)::text = ANY ((ARRAY['linkedin'::character varying, 'email'::character varying, 'other'::character varying])::text[])))),
    CONSTRAINT ck_outreach_events_event_shape CHECK (((((event_type)::text = 'sequence_started'::text) AND (application_contact_id IS NULL) AND (message_version_id IS NULL) AND (kind IS NULL) AND (channel IS NULL) AND (outcome IS NULL) AND (reason_code IS NULL) AND (wave = 1)) OR (((event_type)::text = 'message_saved'::text) AND (application_contact_id IS NOT NULL) AND (message_version_id IS NOT NULL) AND (kind IS NOT NULL) AND (channel IS NULL) AND (outcome IS NULL) AND (reason_code IS NULL) AND (wave IS NULL)) OR (((event_type)::text = 'copied'::text) AND (application_contact_id IS NOT NULL) AND (message_version_id IS NOT NULL) AND (kind IS NOT NULL) AND (channel IS NULL) AND (outcome IS NULL) AND (reason_code IS NULL) AND (wave IS NULL)) OR (((event_type)::text = 'marked_sent'::text) AND (application_contact_id IS NOT NULL) AND (message_version_id IS NOT NULL) AND (kind IS NOT NULL) AND (channel IS NOT NULL) AND (outcome IS NULL) AND (reason_code IS NULL) AND (wave IS NULL)) OR (((event_type)::text = 'outcome_recorded'::text) AND (application_contact_id IS NOT NULL) AND (message_version_id IS NULL) AND (kind IS NULL) AND (channel IS NULL) AND (outcome IS NOT NULL) AND (reason_code IS NULL) AND (wave IS NULL)) OR (((event_type)::text = ANY ((ARRAY['paused'::character varying, 'stopped'::character varying])::text[])) AND (application_contact_id IS NULL) AND (message_version_id IS NULL) AND (kind IS NULL) AND (channel IS NULL) AND (outcome IS NULL) AND (reason_code IS NOT NULL) AND (wave IS NULL)) OR (((event_type)::text = 'resumed'::text) AND (application_contact_id IS NULL) AND (message_version_id IS NULL) AND (kind IS NULL) AND (channel IS NULL) AND (outcome IS NULL) AND (reason_code IS NOT NULL) AND (wave IS NULL)) OR (((event_type)::text = 'wave_advanced'::text) AND (application_contact_id IS NULL) AND (message_version_id IS NULL) AND (kind IS NULL) AND (channel IS NULL) AND (outcome IS NULL) AND (reason_code IS NULL) AND ((wave >= 2) AND (wave <= 5))))),
    CONSTRAINT ck_outreach_events_event_type CHECK (((event_type)::text = ANY ((ARRAY['sequence_started'::character varying, 'message_saved'::character varying, 'copied'::character varying, 'marked_sent'::character varying, 'outcome_recorded'::character varying, 'paused'::character varying, 'resumed'::character varying, 'stopped'::character varying, 'wave_advanced'::character varying])::text[]))),
    CONSTRAINT ck_outreach_events_follow_up_due_shape CHECK (((((event_type)::text = 'marked_sent'::text) AND ((kind)::text = 'initial'::text) AND (follow_up_due_at IS NOT NULL)) OR ((NOT (((event_type)::text = 'marked_sent'::text) AND ((kind)::text = 'initial'::text))) AND (follow_up_due_at IS NULL)))),
    CONSTRAINT ck_outreach_events_kind CHECK (((kind IS NULL) OR ((kind)::text = ANY ((ARRAY['initial'::character varying, 'follow_up'::character varying])::text[])))),
    CONSTRAINT ck_outreach_events_mutation_hash CHECK ((length((idempotency_key_hash)::text) = 64)),
    CONSTRAINT ck_outreach_events_note_envelope CHECK ((((encrypted_note IS NULL) AND (note_key_id IS NULL)) OR ((encrypted_note IS NOT NULL) AND (length(TRIM(BOTH FROM encrypted_note)) >= 1) AND (note_key_id IS NOT NULL) AND ((length(TRIM(BOTH FROM note_key_id)) >= 1) AND (length(TRIM(BOTH FROM note_key_id)) <= 32))))),
    CONSTRAINT ck_outreach_events_note_event_type CHECK (((encrypted_note IS NULL) OR ((event_type)::text = ANY ((ARRAY['outcome_recorded'::character varying, 'paused'::character varying, 'resumed'::character varying, 'stopped'::character varying])::text[])))),
    CONSTRAINT ck_outreach_events_outcome CHECK (((outcome IS NULL) OR ((outcome)::text = ANY ((ARRAY['no_reply'::character varying, 'declined'::character varying, 'unreachable'::character varying, 'useful_reply'::character varying, 'introduced'::character varying, 'referred'::character varying, 'do_not_contact'::character varying])::text[])))),
    CONSTRAINT ck_outreach_events_reason_code CHECK (((reason_code IS NULL) OR ((length(TRIM(BOTH FROM reason_code)) >= 1) AND (length(TRIM(BOTH FROM reason_code)) <= 100)))),
    CONSTRAINT ck_outreach_events_sequence_number_positive CHECK ((sequence_number >= 1)),
    CONSTRAINT ck_outreach_events_wave CHECK (((wave IS NULL) OR ((wave >= 1) AND (wave <= 5))))
);


ALTER TABLE public.outreach_events OWNER TO job_hunt;

--
-- Name: outreach_message_versions; Type: TABLE; Schema: public; Owner: job_hunt
--

CREATE TABLE public.outreach_message_versions (
    id character varying(32) NOT NULL,
    owner_id character varying(64) NOT NULL,
    application_id character varying(32) NOT NULL,
    outreach_sequence_id character varying(32) NOT NULL,
    application_contact_id character varying(32) NOT NULL,
    kind character varying(20) NOT NULL,
    version_number integer NOT NULL,
    encrypted_body text NOT NULL,
    encryption_key_id character varying(32) NOT NULL,
    content_hash character varying(64) NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_outreach_message_versions_content_hash CHECK ((length((content_hash)::text) = 64)),
    CONSTRAINT ck_outreach_message_versions_encrypted_body_envelope CHECK (((length(TRIM(BOTH FROM encrypted_body)) >= 1) AND ((length(TRIM(BOTH FROM encryption_key_id)) >= 1) AND (length(TRIM(BOTH FROM encryption_key_id)) <= 32)))),
    CONSTRAINT ck_outreach_message_versions_kind CHECK (((kind)::text = ANY ((ARRAY['initial'::character varying, 'follow_up'::character varying])::text[]))),
    CONSTRAINT ck_outreach_message_versions_version_number_positive CHECK ((version_number >= 1))
);


ALTER TABLE public.outreach_message_versions OWNER TO job_hunt;

--
-- Name: outreach_replies; Type: TABLE; Schema: public; Owner: job_hunt
--

CREATE TABLE public.outreach_replies (
    id character varying(32) NOT NULL,
    owner_id character varying(64) NOT NULL,
    application_id character varying(32) NOT NULL,
    outreach_sequence_id character varying(32) NOT NULL,
    application_contact_id character varying(32) NOT NULL,
    marked_sent_event_id character varying(32) NOT NULL,
    marked_sent_event_type character varying(32) NOT NULL,
    message_version_id character varying(32) NOT NULL,
    message_kind character varying(20) NOT NULL,
    reply_kind character varying(32) NOT NULL,
    received_on date NOT NULL,
    encrypted_note text,
    note_key_id character varying(32),
    recording_method character varying(16) NOT NULL,
    recorded_at timestamp with time zone NOT NULL,
    idempotency_key_hash character varying(64) NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_outreach_replies_marked_sent_event_type CHECK (((marked_sent_event_type)::text = 'marked_sent'::text)),
    CONSTRAINT ck_outreach_replies_message_kind CHECK (((message_kind)::text = ANY ((ARRAY['initial'::character varying, 'follow_up'::character varying])::text[]))),
    CONSTRAINT ck_outreach_replies_mutation_hash CHECK ((length((idempotency_key_hash)::text) = 64)),
    CONSTRAINT ck_outreach_replies_note_envelope CHECK ((((encrypted_note IS NULL) AND (note_key_id IS NULL)) OR ((encrypted_note IS NOT NULL) AND (length(TRIM(BOTH FROM encrypted_note)) >= 1) AND (note_key_id IS NOT NULL) AND ((length(TRIM(BOTH FROM note_key_id)) >= 1) AND (length(TRIM(BOTH FROM note_key_id)) <= 32))))),
    CONSTRAINT ck_outreach_replies_recording_method CHECK (((recording_method)::text = 'manual'::text)),
    CONSTRAINT ck_outreach_replies_reply_kind CHECK (((reply_kind)::text = ANY ((ARRAY['reply_received'::character varying, 'useful_reply'::character varying, 'introduced'::character varying, 'referred'::character varying, 'declined'::character varying, 'do_not_contact'::character varying])::text[])))
);


ALTER TABLE public.outreach_replies OWNER TO job_hunt;

--
-- Name: outreach_sequences; Type: TABLE; Schema: public; Owner: job_hunt
--

CREATE TABLE public.outreach_sequences (
    id character varying(32) NOT NULL,
    owner_id character varying(64) NOT NULL,
    application_id character varying(32) NOT NULL,
    contact_plan_id character varying(32) NOT NULL,
    status character varying(20) NOT NULL,
    active_wave integer,
    reason_code character varying(100),
    version integer NOT NULL,
    started_at timestamp with time zone NOT NULL,
    paused_at timestamp with time zone,
    stopped_at timestamp with time zone,
    completed_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_outreach_sequences_active_wave CHECK (((active_wave IS NULL) OR ((active_wave >= 1) AND (active_wave <= 5)))),
    CONSTRAINT ck_outreach_sequences_reason_code CHECK (((reason_code IS NULL) OR ((length(TRIM(BOTH FROM reason_code)) >= 1) AND (length(TRIM(BOTH FROM reason_code)) <= 100)))),
    CONSTRAINT ck_outreach_sequences_status CHECK (((status)::text = ANY ((ARRAY['active'::character varying, 'paused'::character varying, 'stopped'::character varying, 'completed'::character varying])::text[]))),
    CONSTRAINT ck_outreach_sequences_status_shape CHECK (((((status)::text = 'active'::text) AND (active_wave IS NOT NULL) AND (reason_code IS NULL) AND (paused_at IS NULL) AND (stopped_at IS NULL) AND (completed_at IS NULL)) OR (((status)::text = 'paused'::text) AND (active_wave IS NOT NULL) AND (reason_code IS NOT NULL) AND (paused_at IS NOT NULL) AND (stopped_at IS NULL) AND (completed_at IS NULL)) OR (((status)::text = 'stopped'::text) AND (active_wave IS NULL) AND (reason_code IS NOT NULL) AND (paused_at IS NULL) AND (stopped_at IS NOT NULL) AND (completed_at IS NULL)) OR (((status)::text = 'completed'::text) AND (active_wave IS NULL) AND (paused_at IS NULL) AND (stopped_at IS NULL) AND (completed_at IS NOT NULL)))),
    CONSTRAINT ck_outreach_sequences_version_positive CHECK ((version >= 1))
);


ALTER TABLE public.outreach_sequences OWNER TO job_hunt;

--
-- Name: owner_mutation_receipts; Type: TABLE; Schema: public; Owner: job_hunt
--

CREATE TABLE public.owner_mutation_receipts (
    id character varying(32) NOT NULL,
    owner_id character varying(64) NOT NULL,
    namespace character varying(100) NOT NULL,
    idempotency_key_hash character varying(64) NOT NULL,
    request_hash character varying(64) NOT NULL,
    status character varying(20) NOT NULL,
    resource_type character varying(64),
    resource_id character varying(64),
    result_version integer,
    deleted boolean NOT NULL,
    version integer NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    completed_at timestamp with time zone,
    CONSTRAINT ck_owner_mutation_receipts_completion CHECK (((((status)::text = 'pending'::text) AND (completed_at IS NULL)) OR (((status)::text = 'completed'::text) AND (completed_at IS NOT NULL) AND (resource_type IS NOT NULL) AND (resource_id IS NOT NULL)))),
    CONSTRAINT ck_owner_mutation_receipts_status CHECK (((status)::text = ANY ((ARRAY['pending'::character varying, 'completed'::character varying])::text[]))),
    CONSTRAINT ck_owner_mutation_receipts_version_positive CHECK ((version >= 1))
);


ALTER TABLE public.owner_mutation_receipts OWNER TO job_hunt;

--
-- Name: owner_opportunities; Type: TABLE; Schema: public; Owner: job_hunt
--

CREATE TABLE public.owner_opportunities (
    id character varying(32) NOT NULL,
    owner_id character varying(64) NOT NULL,
    job_posting_id character varying(32) NOT NULL,
    decision character varying(16) NOT NULL,
    decision_reason_code character varying(64),
    reviewed_posting_version_id character varying(32),
    decision_updated_at timestamp with time zone,
    first_surfaced_at timestamp with time zone NOT NULL,
    last_surfaced_at timestamp with time zone NOT NULL,
    version integer NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_owner_opportunities_decision CHECK (((decision)::text = ANY ((ARRAY['inbox'::character varying, 'watch'::character varying, 'dismiss'::character varying, 'pursued'::character varying])::text[]))),
    CONSTRAINT ck_owner_opportunities_decision_reason CHECK (((((decision)::text = 'dismiss'::text) AND (decision_reason_code IS NOT NULL)) OR (((decision)::text = ANY ((ARRAY['inbox'::character varying, 'watch'::character varying, 'pursued'::character varying])::text[])) AND (decision_reason_code IS NULL)))),
    CONSTRAINT ck_owner_opportunities_surfaced_order CHECK ((last_surfaced_at >= first_surfaced_at)),
    CONSTRAINT ck_owner_opportunities_version_positive CHECK ((version >= 1))
);


ALTER TABLE public.owner_opportunities OWNER TO job_hunt;

--
-- Name: owner_privacy_settings; Type: TABLE; Schema: public; Owner: job_hunt
--

CREATE TABLE public.owner_privacy_settings (
    owner_id character varying(64) NOT NULL,
    hunt_run_retention_days integer DEFAULT 30 NOT NULL,
    version integer DEFAULT 1 NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_owner_privacy_settings_hunt_run_retention_days CHECK (((hunt_run_retention_days >= 1) AND (hunt_run_retention_days <= 30))),
    CONSTRAINT ck_owner_privacy_settings_version_positive CHECK ((version >= 1))
);


ALTER TABLE public.owner_privacy_settings OWNER TO job_hunt;

--
-- Name: owner_sessions; Type: TABLE; Schema: public; Owner: job_hunt
--

CREATE TABLE public.owner_sessions (
    id character varying(32) NOT NULL,
    owner_id character varying(64) NOT NULL,
    token_hash character varying(64) NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    last_seen_at timestamp with time zone,
    revoked_at timestamp with time zone
);


ALTER TABLE public.owner_sessions OWNER TO job_hunt;

--
-- Name: owners; Type: TABLE; Schema: public; Owner: job_hunt
--

CREATE TABLE public.owners (
    id character varying(64) NOT NULL,
    display_name character varying(200) NOT NULL,
    timezone character varying(64) NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.owners OWNER TO job_hunt;

--
-- Name: privacy_deletion_receipts; Type: TABLE; Schema: public; Owner: job_hunt
--

CREATE TABLE public.privacy_deletion_receipts (
    id character varying(32) NOT NULL,
    owner_id_hash character varying(64) NOT NULL,
    idempotency_key_hash character varying(64) NOT NULL,
    request_hash character varying(64) NOT NULL,
    deleted_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_privacy_deletion_receipts_idempotency_hash CHECK ((length((idempotency_key_hash)::text) = 64)),
    CONSTRAINT ck_privacy_deletion_receipts_owner_hash CHECK ((length((owner_id_hash)::text) = 64)),
    CONSTRAINT ck_privacy_deletion_receipts_request_hash CHECK ((length((request_hash)::text) = 64))
);


ALTER TABLE public.privacy_deletion_receipts OWNER TO job_hunt;

--
-- Name: resume_versions; Type: TABLE; Schema: public; Owner: job_hunt
--

CREATE TABLE public.resume_versions (
    id character varying(32) NOT NULL,
    owner_id character varying(64) NOT NULL,
    parent_id character varying(32),
    label character varying(120) NOT NULL,
    encrypted_content text NOT NULL,
    encryption_key_id character varying(32) NOT NULL,
    content_hash character varying(64) NOT NULL,
    source character varying(20) NOT NULL,
    is_base boolean NOT NULL,
    version integer NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_resume_versions_parent_not_self CHECK (((parent_id IS NULL) OR ((parent_id)::text <> (id)::text))),
    CONSTRAINT ck_resume_versions_source CHECK (((source)::text = ANY ((ARRAY['pasted'::character varying, 'uploaded'::character varying, 'imported'::character varying, 'edited'::character varying])::text[]))),
    CONSTRAINT ck_resume_versions_version_positive CHECK ((version >= 1))
);


ALTER TABLE public.resume_versions OWNER TO job_hunt;

--
-- Name: saved_search_matches; Type: TABLE; Schema: public; Owner: job_hunt
--

CREATE TABLE public.saved_search_matches (
    id character varying(32) NOT NULL,
    owner_id character varying(64) NOT NULL,
    saved_search_id character varying(32) NOT NULL,
    job_posting_id character varying(32) NOT NULL,
    first_scan_id character varying(32) NOT NULL,
    last_scan_id character varying(32) NOT NULL,
    last_posting_version_id character varying(32) NOT NULL,
    match_count integer NOT NULL,
    first_matched_at timestamp with time zone NOT NULL,
    last_matched_at timestamp with time zone NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_saved_search_matches_match_count_positive CHECK ((match_count >= 1)),
    CONSTRAINT ck_saved_search_matches_matched_order CHECK ((last_matched_at >= first_matched_at))
);


ALTER TABLE public.saved_search_matches OWNER TO job_hunt;

--
-- Name: saved_searches; Type: TABLE; Schema: public; Owner: job_hunt
--

CREATE TABLE public.saved_searches (
    id character varying(32) NOT NULL,
    owner_id character varying(64) NOT NULL,
    career_track_id character varying(32) NOT NULL,
    resume_version_id character varying(32) NOT NULL,
    name character varying(120) NOT NULL,
    criteria_schema_version integer NOT NULL,
    criteria json NOT NULL,
    pack character varying(64) NOT NULL,
    use_self_rag boolean NOT NULL,
    cadence character varying(20) NOT NULL,
    schedule json NOT NULL,
    timezone character varying(64) NOT NULL,
    active boolean NOT NULL,
    last_scan_at timestamp with time zone,
    next_scan_at timestamp with time zone,
    version integer NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_saved_searches_cadence CHECK (((cadence)::text = ANY ((ARRAY['manual'::character varying, 'daily'::character varying, 'weekdays'::character varying, 'weekly'::character varying])::text[]))),
    CONSTRAINT ck_saved_searches_criteria_schema_version CHECK ((criteria_schema_version >= 1)),
    CONSTRAINT ck_saved_searches_schedule_next_scan CHECK ((((((cadence)::text = 'manual'::text) OR (NOT active)) AND (next_scan_at IS NULL)) OR (((cadence)::text <> 'manual'::text) AND active AND (next_scan_at IS NOT NULL)))),
    CONSTRAINT ck_saved_searches_version_positive CHECK ((version >= 1))
);


ALTER TABLE public.saved_searches OWNER TO job_hunt;

--
-- Name: worker_heartbeats; Type: TABLE; Schema: public; Owner: job_hunt
--

CREATE TABLE public.worker_heartbeats (
    worker_id character varying(200) NOT NULL,
    supported_kinds json DEFAULT '[]'::json NOT NULL,
    current_job_id character varying(32),
    build_version character varying(100),
    started_at timestamp with time zone NOT NULL,
    last_seen_at timestamp with time zone NOT NULL
);


ALTER TABLE public.worker_heartbeats OWNER TO job_hunt;

--
-- Name: background_job_events id; Type: DEFAULT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.background_job_events ALTER COLUMN id SET DEFAULT nextval('public.background_job_events_id_seq'::regclass);


--
-- Name: hunt_outcomes id; Type: DEFAULT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.hunt_outcomes ALTER COLUMN id SET DEFAULT nextval('public.hunt_outcomes_id_seq'::regclass);


--
-- Data for Name: achievement_evidence; Type: TABLE DATA; Schema: public; Owner: job_hunt
--

COPY public.achievement_evidence (id, owner_id, source_resume_version_id, encrypted_payload, encryption_key_id, skills, origin, approval_state, approved_at, rejected_at, retired_at, version, created_at, updated_at) FROM stdin;
6cd1eed1a6184dff9afb8b6c1a2aa559	owner	f95aa789a18542549ba927e1d9fb69d1	gAAAAABqU_DmijepuhA2re9JNPvnJg6r5ZjbKZOBOaOKzD7zGu9-5PAmFhCpqyQpJvB7RW9bZYLjvfHR_4LY3MTt8a_UqmUGLK3nA02vHLV6EzkGNsYfr1TSqTXQKQo4tsOqL71eJ4Ajwhz6C4kqoeZaOwF-NyIlr_URhMP6HJzbMzHbB15ntUCGDcsgAFC1y4F2pcD8XR-Kf7xAloHvbemLLjaufHCs6oiDb4SFXn7rdvvWUEy7BZC26VjrsQX-Udm2M--RfFodzWucVGkSF6Phg6kGQWPyrpfzibD7Z2aB-eO7nr4-5e_hsGbRCqIgOLe4NyJHwQzR1cTftx3Xx6t9k0ydzpxsffdx5Bwf-jC_8O43f5I8rDXHK0-wFQ8sXTkDDkf9Za0yHSYq9B0Av3loXMnZ5weaRw==	local-dev	["Python", "PostgreSQL", "Reliability"]	owner_entered	approved	2026-07-12 19:54:23.243561+00	\N	\N	2	2026-07-12 19:54:14.981344+00	2026-07-12 19:54:23.243561+00
\.


--
-- Data for Name: action_items; Type: TABLE DATA; Schema: public; Owner: job_hunt
--

COPY public.action_items (id, owner_id, application_id, kind, title, status, due_on, version, completed_at, cancelled_at, created_at, updated_at, interview_round_id) FROM stdin;
\.


--
-- Data for Name: alembic_version; Type: TABLE DATA; Schema: public; Owner: job_hunt
--

COPY public.alembic_version (version_num) FROM stdin;
20260715_0018
\.


--
-- Data for Name: application_action_reviews; Type: TABLE DATA; Schema: public; Owner: job_hunt
--

COPY public.application_action_reviews (id, owner_id, application_id, action_item_id, decision, prior_due_on, new_due_on, prior_action_version, new_action_version, prior_application_version, new_application_version, recording_method, recorded_at, idempotency_key_hash, created_at) FROM stdin;
\.


--
-- Data for Name: application_activity_events; Type: TABLE DATA; Schema: public; Owner: job_hunt
--

COPY public.application_activity_events (id, owner_id, application_id, sequence_number, event_type, from_stage, to_stage, action_item_id, occurred_at, created_at, previous_action_item_id, submission_id, effective_on, outcome_id, interview_round_id) FROM stdin;
\.


--
-- Data for Name: application_artifact_events; Type: TABLE DATA; Schema: public; Owner: job_hunt
--

COPY public.application_artifact_events (id, owner_id, application_id, application_pack_id, artifact_revision_id, sequence_number, event_type, tailored_resume_version_id, occurred_at, idempotency_key_hash, created_at) FROM stdin;
\.


--
-- Data for Name: application_artifact_revisions; Type: TABLE DATA; Schema: public; Owner: job_hunt
--

COPY public.application_artifact_revisions (id, owner_id, application_id, application_pack_id, grounding_revision_id, parent_artifact_revision_id, revision_number, source, generator_version, encrypted_payload, encryption_key_id, content_hash, created_at) FROM stdin;
\.


--
-- Data for Name: application_contacts; Type: TABLE DATA; Schema: public; Owner: job_hunt
--

COPY public.application_contacts (id, owner_id, application_id, contact_plan_id, contact_id, discovery_provider, discovery_query, result_position, discovered_at, current_title, current_company, category, verification_status, confidence, verified_at, employer_evidence_excerpt, employer_evidence_url, employer_evidence_source, employer_evidence_observed_at, why_relevant, relationship_status, relationship_evidence_summary, relationship_evidence_url, team_proximity_status, team_evidence_summary, team_evidence_url, score_total, score_components, scoring_version, pool_rank, bench_rank, wave, bench_state, exclusion_reason, cooldown_until, unlocked_at, version, created_at, updated_at) FROM stdin;
\.


--
-- Data for Name: application_interview_preparation_revisions; Type: TABLE DATA; Schema: public; Owner: job_hunt
--

COPY public.application_interview_preparation_revisions (id, owner_id, application_id, preparation_id, parent_revision_id, revision_number, application_submission_id, application_pack_id, grounding_revision_id, job_posting_id, posting_version_id, target_kind, interview_round_id, interview_round_version, source_fingerprint, recording_method, encrypted_payload, encryption_key_id, content_hash, created_at) FROM stdin;
\.


--
-- Data for Name: application_interview_preparations; Type: TABLE DATA; Schema: public; Owner: job_hunt
--

COPY public.application_interview_preparations (id, owner_id, application_id, version, created_at, updated_at) FROM stdin;
\.


--
-- Data for Name: application_interview_round_events; Type: TABLE DATA; Schema: public; Owner: job_hunt
--

COPY public.application_interview_round_events (id, owner_id, application_id, interview_round_id, sequence_number, event_type, from_status, to_status, scheduled_start_at, scheduled_timezone, duration_minutes, meeting_format, effective_on, cancelled_by, previous_action_item_id, action_item_id, recording_method, idempotency_key_hash, occurred_at, created_at) FROM stdin;
\.


--
-- Data for Name: application_interview_rounds; Type: TABLE DATA; Schema: public; Owner: job_hunt
--

COPY public.application_interview_rounds (id, owner_id, application_id, application_submission_id, round_number, kind, title, status, scheduled_start_at, scheduled_timezone, duration_minutes, meeting_format, completed_on, cancelled_on, cancelled_by, version, created_at, updated_at) FROM stdin;
\.


--
-- Data for Name: application_metric_snapshots; Type: TABLE DATA; Schema: public; Owner: job_hunt
--

COPY public.application_metric_snapshots (id, owner_id, application_id, job_posting_id, pursued_posting_version_id, acquisition_source, attribution_status, saved_search_id, saved_search_version, saved_search_name, career_track_id, career_track_version, career_track_name, assessment_state, assessment_band, assessment_algorithm_version, assessment_reason, recorded_at, created_at) FROM stdin;
\.


--
-- Data for Name: application_milestone_corrections; Type: TABLE DATA; Schema: public; Owner: job_hunt
--

COPY public.application_milestone_corrections (id, owner_id, application_id, activity_event_id, correction_number, supersedes_correction_id, previous_effective_on, corrected_effective_on, recording_method, recorded_at, created_at) FROM stdin;
\.


--
-- Data for Name: application_outcomes; Type: TABLE DATA; Schema: public; Owner: job_hunt
--

COPY public.application_outcomes (id, owner_id, application_id, application_submission_id, stage_at_outcome, outcome, outcome_on, recording_method, recorded_at, created_at) FROM stdin;
\.


--
-- Data for Name: application_pack_events; Type: TABLE DATA; Schema: public; Owner: job_hunt
--

COPY public.application_pack_events (id, owner_id, application_id, application_pack_id, revision_id, sequence_number, event_type, occurred_at, idempotency_key_hash, created_at) FROM stdin;
\.


--
-- Data for Name: application_pack_revisions; Type: TABLE DATA; Schema: public; Owner: job_hunt
--

COPY public.application_pack_revisions (id, owner_id, application_id, application_pack_id, parent_revision_id, revision_number, source, encrypted_payload, encryption_key_id, content_hash, created_at) FROM stdin;
\.


--
-- Data for Name: application_packs; Type: TABLE DATA; Schema: public; Owner: job_hunt
--

COPY public.application_packs (id, owner_id, application_id, job_posting_id, posting_version_id, base_resume_version_id, version, created_at, updated_at) FROM stdin;
\.


--
-- Data for Name: application_submissions; Type: TABLE DATA; Schema: public; Owner: job_hunt
--

COPY public.application_submissions (id, owner_id, application_id, application_pack_id, application_pack_revision_id, application_pack_review_event_id, application_artifact_revision_id, application_artifact_approval_event_id, tailored_resume_version_id, destination_url, applied_on, submission_method, recorded_at, created_at) FROM stdin;
\.


--
-- Data for Name: applications; Type: TABLE DATA; Schema: public; Owner: job_hunt
--

COPY public.applications (id, owner_id, owner_opportunity_id, job_posting_id, pursued_posting_version_id, stage, version, created_at, updated_at, outcome_id) FROM stdin;
\.


--
-- Data for Name: background_job_events; Type: TABLE DATA; Schema: public; Owner: job_hunt
--

COPY public.background_job_events (id, job_id, from_status, to_status, actor, reason, created_at) FROM stdin;
81	d3f34bc73787473eb71fc5d411021d74	\N	queued	owner:owner	\N	2026-07-12 19:59:12.524372+00
82	d3f34bc73787473eb71fc5d411021d74	queued	running	34ba52499d92-1	\N	2026-07-12 19:59:13.076908+00
83	d3f34bc73787473eb71fc5d411021d74	running	succeeded	34ba52499d92-1	\N	2026-07-12 19:59:13.109971+00
\.


--
-- Data for Name: background_jobs; Type: TABLE DATA; Schema: public; Owner: job_hunt
--

COPY public.background_jobs (id, kind, owner_id, subject_type, subject_id, payload, dedupe_key, status, priority, attempt_count, max_attempts, run_after, lease_owner, lease_token, lease_expires_at, heartbeat_at, stage, stage_checkpoint, last_error, version, created_at, updated_at, started_at, completed_at, failed_at, cancel_requested_at, cancelled_at, dead_lettered_at, dedupe_scope) FROM stdin;
d3f34bc73787473eb71fc5d411021d74	legacy_hunt	owner	hunt_run	411aecdfaf4c4012965eee8f51a9a0db	{"hunt_run_id": "411aecdfaf4c4012965eee8f51a9a0db"}	hunt-idempotency:2e398e65d7508f8e16dcb438097f9701b1f0d5c6568f465d2b44e2fa52737a11	succeeded	100	1	3	2026-07-12 19:59:12.529771+00	\N	\N	\N	\N	succeeded	finalizing	\N	7	2026-07-12 19:59:12.524372+00	2026-07-12 19:59:13.109475+00	2026-07-12 19:59:13.07735+00	2026-07-12 19:59:13.109475+00	\N	\N	\N	\N	owner:owner
\.


--
-- Data for Name: candidate_profiles; Type: TABLE DATA; Schema: public; Owner: job_hunt
--

COPY public.candidate_profiles (id, owner_id, encrypted_payload, encryption_key_id, onboarding_state, version, created_at, updated_at) FROM stdin;
eaec93841de24c87b78ef9d6f9c68ef9	owner	gAAAAABqU_DETbZUSAu6Yoh2pUkGqzV_TIvvIMEWvwyrKqcDUsDDvFjTlo7Drcf9lq6RcIB1Ig5KIvfDe-n4ZmZ9sw_gUcp9MQcNM4Y9IvotMwQprARIdNHShVUZCbAlGnwa39oOyyR5l-zNRcAHZPoAXMrVkdT1npn8q-htGFtC6W-m7Id8lYvv84TIVijM4pxyx8-zl0kiTt0bPXvq--nTncaoeI5Wh-dGFwCZzaMxPunHcPOd42bb8h17VS5_66Tbcn1GnguC5e0UCxD9MNI_0eAIM4m8RaTUlGaFJvtWJMRnZ0UmcDFSvN5Vg8saQGg5o3AhBSM5CBjEKEbI0F7H1NT2VkotYLtCoDgEQArAZybFPhrCgDV26qsxxO3J6fP-HY6wGMZcvSJwFhPUStLRxrVSZlQqDIOf0ztj5MV0uuTdG0irn0oZremb5TTOqS-2K0q712xa4h-bc5ySB0_2hqw1vyfBaotc_RGKH0PnzH77XJAumzX5treAW7yw93_q8Zv_Cn_ruPCJCVkNbFyRwmstnYpn2kjioQZ8p-egY1lIQUs4WN8hHFoIt1V7zcYATZl5a9yi8jFB1NgU503_zFMKc0cWbA==	local-dev	resume	1	2026-07-12 19:53:40.449622+00	2026-07-12 19:53:40.449622+00
\.


--
-- Data for Name: career_tracks; Type: TABLE DATA; Schema: public; Owner: job_hunt
--

COPY public.career_tracks (id, owner_id, name, role_families, seniority_levels, target_locations, priorities, active, version, created_at, updated_at) FROM stdin;
fb46aa14b1d44554ba66e73dc12eba40	owner	Platform leadership · India	["Backend engineering", "Platform engineering"]	["senior", "staff"]	["Remote-India", "Bengaluru"]	{"compensation": 3, "scope": 3, "learning": 3, "company_quality": 3, "flexibility": 3}	t	1	2026-07-12 19:53:59.042614+00	2026-07-12 19:53:59.042614+00
\.


--
-- Data for Name: contact_plans; Type: TABLE DATA; Schema: public; Owner: job_hunt
--

COPY public.contact_plans (id, owner_id, application_id, plan_number, status, target_count, candidate_limit, confidence_floor, policy_version, scoring_version, background_job_id, discovered_count, verified_count, selected_count, coverage_status, exhausted, retryable, shortfall_reasons, error_code, version, started_at, finalized_at, created_at, updated_at) FROM stdin;
\.


--
-- Data for Name: contacts; Type: TABLE DATA; Schema: public; Owner: job_hunt
--

COPY public.contacts (id, owner_id, identity_key, identity_key_hash, profile_url, normalized_profile_url, profile_source, public_name, lifecycle, do_not_contact_at, version, created_at, updated_at) FROM stdin;
\.


--
-- Data for Name: hunt_outcomes; Type: TABLE DATA; Schema: public; Owner: job_hunt
--

COPY public.hunt_outcomes (id, hunt_run_id, draft_id, encrypted_payload, encryption_key_id, logged_at) FROM stdin;
\.


--
-- Data for Name: hunt_runs; Type: TABLE DATA; Schema: public; Owner: job_hunt
--

COPY public.hunt_runs (id, owner_id, background_job_id, access_hash, idempotency_key_hash, request_hash, encrypted_request, request_key_id, request_expires_at, encrypted_result, result_key_id, access_expires_at, request_cleared_at, completed_at, created_at, updated_at) FROM stdin;
411aecdfaf4c4012965eee8f51a9a0db	owner	d3f34bc73787473eb71fc5d411021d74	5dcea85418983054cb0e2dd00bf2e67a16853ab95a1fda029f95cfe449742a29	2e398e65d7508f8e16dcb438097f9701b1f0d5c6568f465d2b44e2fa52737a11	3f1bf5b7a07a4306af260968a87e09a9edebca63fef0ee6979b617c6a67ef725	\N	\N	2026-07-13 19:59:12.523229+00	gAAAAABqU_IRzJP6tFF7B7L5jcRtS4VXLTgRDrJbLGRZGcqgDPQSObrXqSM_6oEDk482F9JAOB0YwldmduPEaFS6ct2Ye3a-6yCY4dlOc0Eh32Xt6sPAlkmuklEYnrSI_G9fDjd1HWBCU-M6HTU14ADsCnt67gt_gV4rHNAPajJKuxilKwoxjJC1qJYnQy-zDm8B17qhCBMdbL7T9Q1L1N3G0uajnwtuTlNYL1UgMajb8qzqnu16eU_DL7FQpkoe_EBaPoOAcGWZyUqMnO0SOlkvQs2UQFlH6Cbyqt4PQ7llgwk3ZNG03R5A3tVDgMqjZ07HGQQ7UI5x0WB1vW4KAHOf8nSkcHPRIlfOfgtzUnzWBuqY9nbYsV3Sr09HKR10mV-LvmbFZXJdjeylw8WxtmrQURqoQZ2IzugNR2DsyWD7jX-zvnQPmLNs6_PCR86beNsoHjxKZi3ydPnmDLS2TDjSyusQFUY7-tSTgwZhbOE8D8mQ7u62WYTOAa1p9PEvMVYYwLkdv3gbnNGOssHDNhA82RsGUnbLP_AFG43XUrb9megyE_aayXFCnDAVMVDgjFtufh_-KOY_ciqZdsrMQeO06RnmuBTb6-dkPiowV8CezQpOKTv6YW4pfpCdejhyfV0RNXaST6HzxVnWd644yU4yLoqPmt69Sg5nQ-TnjGRWTBf3gtQlVYF9YTL08aSQes6jhvSQTGLgQ4mQLROwEbmRRFvx9i7Jt37xsng4TeUINQUtecp_PH5J7KenkO5uzyyBeb0EBp0xLivNwFP8GVYAIUOXWwjaMbVYWwuWIbsuJVjFwQLEkrUfLOGADxbfEP5FQNjswXWQho_IBMITxfbDl17WvIRj08nNkm10tpskLrHjtO-PK2OdlJ8SJzTPozcobnMesJDI6BNW0FZmfhdqZZA7ofL6kn6gJgQAU0tH4ljZnK6mHWZnbc2kju5obxPgLRMZsCxrYvqirGvUN_peD3zrP-FRLMAY1_mch4CbvmBJUisJNKwagVcv_UjwvZFGug4SYSFP2Y29xRVl8cPzAvK_7K0udp67Bc_T9xzwt50RbO5uXuI1Vq5YFj6d1RWn5UKXSox8BqUmLWIOYlqQrygpHzTt3rPAeLvvU6x6dfYDDqlm2OVAfbYzMlkx8iNZQe2IsaavMfTq3zDyMjQgGrcHXbWUXV9HE8uKMt2PNWMBsgLpwXtfcqMTRqy_p1pQtA5KwBCXSDhM89L7HE4hm-WqbnCyO7uZtFOdnsRWGLYE--kO6xDP81lxGnQXkANwdnXEy06KjpF5jq4GOoVtklzg34aupBN6uqsuVU8FtTIcTe2cJrb1NFCjUpouUSxE7dLJV0vjDm3nt2vbAHwCcwy-xHTWLttebKOlsR2SHUf5A6q_SKGwjL8h0Hf1gHKm6Aj9ccPjSq3tE23lIojWkAMHQCdM0fvQm7f4PAOduVcxUeedRtmbVEWnNmFBOyoGwf_v1Tp0xBNtJe5fGRS0cB6VEklz2OdxSFBuxhZcSAO1xYfosEbee2MkDsCWWr5dhgwUcEuMmIkaOUAe61-OpY2f5NzXJzOPuz1yfC06fDy2sDc-gr-OksEjimXbdr-uO9FU1MT2ojcfkpIfgqk2CWky8O1rHnwC5BhgbNp6xHRji_EIsxiWKq9f9lF1elEmRuCqteitUTc7Wb4dJN8hyqzN24okuwujVpD-MY4eSJ5zejj9EnJbapABB_6u8pvNVz2CRPF36NkHXR-NJfeyVaquBBOhRgL2TTKRU9MZyWZhJXKKFbDDrhzafbEnEnNzH0r2IwYfSGObDYfn7J1S7ZW2H_n2SFFjQnXHCAHRTJLGnvVEhHUMI25LALacyInR1FECfD9zij8rQiSWMXRJ13yLM2gE3j8qMT7MQmx6OHfAFLJ0gJLlJaHk_Rd4M0Kzx_n_wo-ACn1ixxmrvB1RkaUgrwNHQdc__36o0uTJ5s5rPT7O4GaKypmOvDKE8Pyv7ZCeZA1eApMHHt5XDGj93bXhwJInE9bP7WTtNY1zZjkhhDHpjono3tkCGIpyQV-F5x8kuT-J58wpayQ84aIbAcNe3HwNLNFr3Rqn0qslJ2AtDhACBtYm9TsIM2AIzYe6bQjj_m-XPw5fH3awqqQc7DBuxHyDG35vB6H-hNslhnDGoda0m4-EE5a7O81MKN5SpOCyPvltUEiwwkxgLrPGTKvtw_GEmS0Tp2eAzDQJk0PchBEL7dpDF3j5IKZX6T7kDgeMIT-Tl9FpIAxK25TinJd0bTWj_sFhvMlIsWrK5dfaF4YtPcPb_vWsgIVBATlepWUfY-ks7CEVUUy_OhHabF5WkvYuX0SB21j0Pa-pGIBbmpvbVvX3VXQLeZBgO2s5-kFzMiaR1TZklJyc_bwSueki15ZELOCWQrAviMVE3yQqWoxJ-ofE5uZx7WGGAG1BIjrfnWoCWGql4GRXgbI00f3He0vMb_tuUH31_7y6IIfdY6MQDF1sGxxjvY4snvuRKGRR1jkcWBrvcMfZDCylLAToEGOlD2XHuL9ik4mykL7nPFk9wk4VZ-eW1UWuiRttR5Uqoxm_l_9WkfXUHF7WEwCBpblojGgui8YYM4s1ZLnBl6zvvkAvjbBY9nx8N00CGUAV9oicXjQu0km3FH4mGbRdGiAERh2ZmptTbEV4SRNoiNmXMh6AiRcSwEFkSfYZ9hE-bwUBi0M_ivScG4GE-ZngqPVGH6tK3ZR9DZalDaIaWe9vdlUJoCBv-piN7tyF7_ap4TU6LQRPix2OJ-QgCRkuPEmyZpricMUrjPtucYJlHDZJpPZly_YjcNOvxMbh18C8HbE7FLag--x3dqPPrXh9R1pV4G32uRQ1mkYrw-LJXMuEbHX9vbXtUq0LkYZYEdFX3q4oEw2TCAshxkPLr5DbxQlEi-aJHdYLJ9r2KnIxpCesMzo3umajTTC4PmPbc1JIrpWSw314ny_mFa0GJYtIfLQo5wPxT3abXYlwo7YOk9gv3tfVsy4HKi2Iv8XhycaR8j0aB7hDDf_AIJtx5ArLJbPRIg7k0vG0uLvqzqFaw11WNnDxN-93B8bPntffNCzYgVu3OrAPVxeSu-Vp6x_Mq80FjKk7tSKQS1Z0yjZoovb-LKj46SFUY__PUN082TDSAa2NTBDk0H0XdyWq9TXP9NQ2m5lkOPR1OaDkcizdzN5u8kecc0kTYhCh53eWIkPEYfWsnXGEZMGWSBOZ3nsrAifL-_NrmKxTRAakySR-4J1T7T7d7F9MvrVS4ZwyB2yLBBrF74hQOrJ2Sbg8i5K9Gw3ihfBjYcv2ReZakOOQUJvubKx_CWUTaH_bqXwwrpWyuWhdUw5lEg7BvxJPMerpxBHnm3cZ8BLTlGPkWBpcyCLdbGP2Wj9VMj9DvttKxWvqz3IbXhzWg1V4pUOzMd5DFKUI8GmGMk7knYJsnw2wdzA4B34m9wwnpEqwbKmQPd-pGub09kReeK_UXQzQKktGp0b2x-NDSmLkBTr6BwFyBd5O4GEaVgXWQ3RiFS0bAeet1tYz8NX8UXO0E76LmKn-b7WdFLhnpTF5hi_dtI-gd1cJv-V7r0ZuQDuEEE16Hn3wGdSk7SvJsnk0zChBiMt6L-sf4WZDPCiPY7ELSlR6ypo2Q8b21hbRatipsDCagJoxJFFEbYtigykFVP2aolyKTwPQG8HSHUN_5jjgiHYwNyWAbqEWtIej0s4cixBxKM5Es4XiKUjMPvZFpagN2GL6YwFL1-W1Bym2PT4cMhhrDizVq-ezYw2dJnENwpf4DmEoh_t0ZHDVRwJE4sCFxBoe-dtcMkyS08t08PD4ZiSIotLhLIW2bnJW8ixJ4b6Y5Z54a4AFKS8urim87XrGuE4StgFyPZJELJuUXxxwvyxtqbMnPNfG6Bj1ElqhmR_cwndZ-oyFf9acZ9dNyiKvbCk0pE-yUsAfEyWdGDMm1Iqkobe25PN9h-y_p1uiOTY2RRHyRsohQK7t_TKYh9LAd2q9sGAZWZOB0NQPJtJIhmVV-LlH7GDwjbjhGCSxA4F4aiJRgS8K7_3L5eWdNFMXJE0FSQfAKXhxEHc8hAPqWRBacXLNkUQJSThBYWkuCqQL-WjBqlWb_BuFg8t4rfe5SWs0nfzlQlrrvdg8kIR9MPPz6WSC-ltBwkQkl2UQGLFKneJiuZ3CY4_2qDEuABnVYWt2iW2riik60j9xIS-nGpNzEPg0yaBVS3pQDK1f1plqi9g19cWfosbdC0Q4ES-4IOhx0sD0jkc2tyfetJVtIQH7eWQBO5wx4r5qC_kpzw1qH1ezePdXpf1Srj15Q-_L5aPCii539BalHgDyOnO2-8kSDWLxaFllKX3x6Xz_UP4NElWRvrYM-IwtMEPCaZNWagMHS_vIJHwd9_oYTJcmuTd18dbl7lagycCEObwqQjoWHWoXg_msgf1rKaIMRsOouJVoLOTSMMmMNzEux2JZmSA8X8qKbD6mWzU-CjbGLrFdTgmS0lfewKsVL8uvOxlS-VrHnEB32YUqltiH7G8MJ3bQPvARYQpJDczY-d6jWiM6-MNBTBTzm5Kxh9z66roaAKB7BmuAkZVxUZ7jDvmELn8wrsUND7Y8ZhKFRwafR3nCGdkjmG1jJJZ8SLj2ANsJ9crW4II9LEEe-tQTFlEHtrIDWdrGjtTEwi7wx5Gku0Ck1Z755bAKZJvY8BAgjhp_zB6BP0j_OhT6rdIrvPQA1gXU1DUS2FCi4-3Lkwqv86Ru_CZy_Dj80XcqD3NhC7gppjJiLOCBhSNYx-Iw0v3g0bDysFhBJw7NLzQntXMqPJlKlThD2zK1DIIqRrB6f1Gm12OZFA2eOym6b_3LCwA7o15H0I29sEL-6bo8uPjVtymP7lIKvWF9ZQJoO_NfLlH__m6PmW5v8Oq_Qkst9Yir285vug2vpPLWVLo03YbLTHIVYPJSVCVP6WzadjDc0-W38UCz_A_PORGLO3cVO31pL65XTw0r376ImlHjjVPd5Hht7qtmeOKa-xBE9lIC893KRcvXEgeLumpvWw34XPtDpUZUI5b0nvIdx3BrhslDcHEfQA0Y9X9z0pH9bQvjNzgCcr1NtwBEs8UPW_7ZjyFU0vW6ThqzTk5w_M7fnxNcnfSO22ukzceer2zU-2Hu9ar6BAGiFpzJY6lOmkblAQzjwgDcurgFGSWB0vCdB32WMo1-Pfg6au0SXaGD-uDeJQ299_OC7mxVrPYMnbcvdwItg6CnSzTzPCDnpjXrwDqfi9aJijZ_bmOnaMRsgdK0SaFWjMDbOgCU9OkpsxVH2NlMYxcs3e8_ZaalmnYAttZvEIDtIn69S_3A2YLSWQlxoT0NmEjtpDnO7Z0GVxjYvXzvqZi_wdWY9mPLP7v9fpgeJyTkWlJzdGXD8BCpezYhPIMJXqy6MvCgolf7ke7iO5jjIV-FfRr_TwKfmQAI9sYQv94iU8vOeOIV5vEX-iiATZEwQkE1Vkr1iYJydwYFcOEt0XBo_llCIlUpFLGFH1MDzR5w7bN6W-BQGUiEH5csr2kYLZa2H2NoAyXyqWiwuzIQuWgjtFxeATCf8lz7aATuzShGh_T-PBcDn6xMufwGycip750h0i2xldCwVME3e1tJjgMQYp5YACjecdcwPcyAlmYuERusxsH_p0hO1Dle6jw_5W2YUfmHEYKRnPQppPKZERts9VHvgGiqmSgrjQzfkRjKCosLDdBc0VKNMn9zV9ECt-nhKb1_nz7IiE9kNAOhzqTfIJ9vYaLH6AeNygtlok1z3EMVgsscKcW9DV3Ti5kPP60AYi-kDvd3d6JcOu9x414KVnnToB7TYR7-iaZApfO0xo9ZF-qtmH-LJEvOhH-xn_Tj5ra1jnd0xml6IOrH7DvVEdGpOEO2CN1FMcRvmhIBVRp470fIuAZjiuIOzekcEgnhd4jPMm8WsF8fC6Z6r1lBhT-2hGo7k5CTioWw1Rn0Ul959gWdrRszlinj1aDkQF5QJLtHwttpPpAFfNOyfZbP6AN3C761jSOnPJCS9Rh0Kiian0jt5TRjMRAQXzl7rtManrGhspGMgmdGVQAKJlLRISfcC6bC2SRS1GxMekLOS6FWJ6DSQptkv6KPF_bLljy5rAsltsH78v7J8Jp-N9q_dd1lNDJ0vUaVYj-e0gTfo0E0YvwyI3gCuG4gPny0iwqfImp28I2JnuG-hFTDnwcq5IdUvb6TL-IkbSpjgdF87-vigIfeS5i610xudIyBm2X4tAsQ_c4g3igT_-4sOrDYJqEX1hvmcjHkyiX2_kqIiYGqAlS_-t5Y0GdDeScNSlNuMQAPjFGTzEY8GZC0yfU3VKT1tkYrz8k0S_MnwEfV8qNjLWgvkwOFnOZFNjMmKEtu4X0UcU2935qyFVzCNIzajQpzPHD70ss5ByEkZtSL9DQVDN2VyYiqdiuk8EqhKyoHqqQRPYM83CrbRYCXuiFPZn1V1J9zc0-XHpDuDWHV-pOFc-FmQm5g04zj3cU1PdNz4W-lvooXUkaOAVAc7NZID1YBXRer9jfJtxYuaWn8oicp6nAP9gz0TF_YlK-_U3moHvZF7dPC2qalsjoiQ7IF3gLj0ajydi7uca1GuYgxvwYPlhP8TC9CZP1Y9YqQBnvP8i-4B2Ug7L5ndZ68rXMnqQxLo1fceyfFgEeaMeozV-WgejjOojX5mj8vFDkJzFz9ScdHo9k_n-l27Ugi4_8p_xNoNZw4AqPeWm9RloBXSzNzd66zFMV03XUb0UogP3_nW4pG_drT2QLCffqK2CFE2JpYopd_046esXXo5RCtvYwqhzi9ry1er_2rAi_nzX5gxj7WIHlO1RpAuB0qXIeo45Lhdr1V-l39eYylwlgyAPynaVOxw2ywNvpu5roG3YAZqNJvKTG7oxsKREy8oobIuGSVfVBEpdXrq2C4MWzlWLB1CWGNJElbDEbHofwBZTVAgtQQ1hwq7TP4lqq9O5PYZRVwtjJIbYIS01yAU5Oy--AsoUhCaZqyZQ-4DC5N-EMa_KYJTE376GlOxh3ItYIfBHFJSz9Vs_E6AQTYSX0itaqrXolSaR0m35Vxywb9I0B_r26oO5_Nx3kvb-wcRVzqba6e0nK1-7q_qsOCkwXGU9XUMs9gDiQ9vJcbKltUSrLJPLKZARg_eDpoQEWudxr3awIHWXgake1THFxe_nzn6C6_qdJeZsh3OoDtDsyD083g2SnWqUQ0oTsj3Q_lUrXyK0NiR7OfRGmrHGcO3QpWzA9EAOyFhP15T0QnxRAYKu41MC0ZGKHkoap4_8zUPU2obJQ0vpgHZn0_UHTuMJ6vvXZxCp9LUmgRuCd4ZBF6-F5oIcF4337EX57oIlw6Cm4j7DmQHaEhq7RrPcX2tkBo22vEmO74nkmwzT9npZnzqFoeSxR5us8mUct-4Z55hpgMcjJLBtUAYP3yfWb7BvSXkRzWbc1sodWHht6OWmjmoYsHH_FphYo_OneI2dzRgAFHyeH6Q6E7JAXKWmPoeQ2EkcWdJNXyVguDd-80m3cTzDOoULIDX_KNvaZ1AOQmtcMiSgkXAHCWs0kRx5Gv5sczb_1_l9g_bEjcVLp_5qTd3IPTE5mcRo_FKudCEGPl7B2-mYNuRkWNKBK2vA8HzCX5k30LQVhApdbuXMhWoFTYr2cwQu70ony0L0_WG3rMaVnVpkQ8Wq-JNC5FEbPxcaXXejs54lKSbNskUgnL_GCDNLPpQc5-pV_xwpIxfpkJv2_TpIDRa_Z-w7m0sKJpS5L19JN1PyOBkwn7p4zCr0p9OY0sAvxqmI5Heo95kr-MQKlJxbY79nrgcsYYuNX3N_Q5sAxf9RXpTqj_yMMlJlFb9CE-KG3GlX6CQ0HrsIwUHewXX9yXlwaMYXRUB85CPnZDCTWajxVc0wLsv9kFpKR_izl0Wt275cHkrFVIp-UDd-kA4XIxI83qnwhtT-qCxoTi1G5iK_XBjVFShMaW6GZYkKa13Tg8Yw9MSl5aG2Do-bSd6TGdsqY4yMBnnoQAzYaPxHdnj2NeCH1MIjcCfLdtCZR4c39UL9JhBD9WLwA1hbjqpLX4u2qPgxAbMux6mIsP5rtLS3Co3Wadfjy-msV3lpcEzp37voOGLA9DWyDhIow5anfL7s63iVu-kiOoAemuGn-k0TBOJ2rRHYNaa5IxAJ4CKJE008Ad6DgB8tIMq5ko6cIC38Woiqd4D1nCqt6dtv9rzbkE2nZe3VTaJem3C8n2WWDu43lSeI_IbRRxTUQ2csJPFyrPrTtKwXzx7tjvweLwlJtMhdBDIOCMmrXVL7N9op58CisBEadmszEx_6bBHPIs-H3lmowEvOiy1AkM4BkXrrWAx0gq6UNUCqR6NaMlCOdXR6iVeAs-MYlJ-L0GMergSQCrivIPwVpeX6ow09QDk75qCLcEJugz_9ZjLOZw_apqN9ZWqve4gkLsAb1qX7BfcwxTGJgZ5O4lhxkwI3bl50YysN_iK3LnNc5XwoKW-WBBpv509_b6g0f_VtQ3oVIlmm3s5_gu7lD0WPryjOveckIU01_c41RmgTEKtTGwLslOOV_jn6cRR4n-QilJiowLw4RMJNUoLbzHltW6CtojzjqvUb5J2_DXu1SJJHSDbI5Nr171QT_bEOu98El3gm0PwSQGqd3qcroiouMnfeV2WfZCs1zlJ4K9zJT8_lYn2fc-dnagH7qIOX4xbAfGRfn01gLiSoVjSpXk-laOck-X7oWH5nfrIQPjnyOsD3QyfBiSfLQ2vWM1Nfhds-taPPnyOg-KOHhcCgFO6yVG93MjdWqfcoOeFamWByMucUwx_TmaaM0F_xWCngiNMM1LJB2e24ykHvmPvfUi-HT0H_1vJjtAH_WrY-8KShJq9E17GMIaB6Bc-IAYyK5wYLLOiZplDyOTDeUYNP-wc8raEFWhTEsq7q32AzV1VgkW-JsTdRS0p95o52Yi6438_J3UrEEsWug5P8nyTMKAhutZPUonSXuDm3NkVpXYb6MS_fdIquhNXeU-kejNF-f60ftze27s2mxaHE4-6UartB8zM3-OkGShEOtp1P3oendhOA6qo__3NzQCITF0ovcodfeT-p7Bu5tc3QnkAxZhG4mnEdIFbflEYE47lX9K6Put7qmsis4r0jdHf7SAi3JdmmxJ0bL78p01bOEROJv2XsAijLo2TfwQ0CLHKlhJ6asba_UsN_RV200QrduKvOmZHmblInZL-pPK0gqMv8dlBAI78t0w8SdXoUN7wiAcS46ikP4fgcdpTPFy7N5MrlCXRKrGNNUouqEoKXPjLCZrZaXYjiKPsw-qH0G6puhScghDQGVD7Kpb9OIai5w7qwhYD1wLT6QZte933zkoM3xuAQMs52Hk4RfZX_Lz5eChjQxfQ1PzRnAxMdqPffZKVRe3cI_EKoK7MUmj9NaoIwFoycnP6wHWNNnq1o7fwb4jfKRcon7YwVHW5LFv4-89-SsowSj6nWoVICAH9_Yp4lKbTAauyH3dfaikw_6FtANND20D2hUcKM7OkR64qZGJbU_ZfB7IAMjZrrHehXpK-a65PPt5TVYFvN8AqqpmoTWvaYsVclAQYxrV50ROVFOEePqjZR7Jah64EbWoyBM4g6Zq5jjAQOg3xGTTyZRNYJi0dJAw21_khlyF_nxZNQOl8i3-s4jLRmFfWeKVZu43CDwsDz-7iUKRQjQpLrrr8_omXFas-yCrMXIs2OI_1MahxDCivhHqvnP4iC_N8uQ0fJinNgR12xjLkuBd6HBgTzAjejKhct6PQJU-WkY1Q5JOc7Kp3j3klBnmiEZPzW6iTSv8W3fW3yp_hb_bAtVHiPiwJQlYMdY-KLr4VWICvfOq96RPAzTQ-4rhyr5oHgSu3VJDvW9KiYTdx_XOfzGnbUAFQZluv4HIsEYnUsmaosnXzocAYwOnJvWAOoY7JrMOoHglJiLVR6pG_Zt8cjdoGX4iUVWZOgYAuXgHBf28UjmNZmYALrvrtxE3PNuVHPvTjb_K8BV2QuHL4Jbf1ASVOq1GtQ2vaY6um57R8qRmzKOQSmwVFILzXImoK2A_R64qXiGN1wP1TamMvq-ztE4HODQesnw5fbDkU_Zwm0I0PHw_wHmb_dHtKhG0qIoelumLSJjSQ76qtzKyDq3r-O18AQ7KEu5jeRlcRpyHOpm5q6yY_VfEsVK4lE-VEyUt9xfDUU3g9fNyoxLP6LYR0FFtBpZ1UID4NV5WeMZ-I8kTBUFZdVvJBs2v5ZLsVWAhHy7O0_tqUvksgOVRMDZEYjBqPC7qFI1jVXpWChZCd-bP5CaXFmAGVXDoT-MFrmTAMetHgg39fXIhhryl_-m1AAXlu0jZENNZGMgViCb-PSrhDtxGmZJMvmAkqGeXBCDzof_cyCbtb2Gj6aUYqWNX56RFt0oGasywXirjM9yOnd8LmqdNV5iNbsky-E9GDZRYCjvVZNC_cHcpHI1_3fXEUS1Ggm1v4_XSDi-btkkX5aN-76F0YMb584jNnwYpEYdJVy36XXFAfV84NYgG17IZEgs6zFBfdFLZ4YXg4m-wBDPnXRm1C1zodyzeMGHb1pciU9nALT9E8VcakIOIJuMIWcToKV6Vk5-QXRzZ5qWBm0zUs1GMPNQfFHnFjMmsm6FmQG-NBr0cCEmQoVbaH0gQ4wjhEaW8NkSPQRmxxS2L90531_CP2Yo0yc0mU7p47J7kmMDd3b7e7J-C5zpfJddrotY2VHeO0kdaycpA5JJw5yc93nhRsZ9N1tsPHP7eys3BebELgWH6dkRHdjDV6lqSnjORiPaiKVOTrE6GyExTBz8ri46hai6vvoVr0kU3eHlnWjT3rSAy06s5-ML0rQhCYLbXmpAj7RwZ_at7rwHKvCtwg1wgCqtFHzyvVCTnm3u9NZTHkZt7P_0MPLHAAPY_GJF_Yr25sQi7-aMfd2qYg-0k7T44bTOPQXseyJGiai-7mYXvkXKNsozvbR5WZl476lWT1K7EfRml2gDVGpDY903gAyHlQR83RK3Qi9ygEw2UdQXJOAd7RX2oz7SPWGJNnjx4CMuR3HJYh4Gmb6BoQESNST1NXNSNP5LZQmL54rs40rOfOzc5dy-IUno4p2HG6YroHo1CQzEDG0vnLeUlJT-Lb1jaEOqewbbh0aMizDl-y-raQZkOP3NQRFgn7K7hdPY0245Bmvf9JfevICRGNTgSjGKHUTeOmxta57vOmXcmCQbb_cxOITNu90mIR7oP5kypd0u7Qnf6EddrUf-nB7ufR1r0Jz7rDwUeSQ2cudLugCGurWneySAmN4peicDdhTl1aOjxyJV4KTvg173HrmffV09jPGSaQg0fvYwaAIPxiPTAeft3ts1402arXAjNLAwxeYpCIgyVc7wdXhXFM_OW6uSssc_vN2LHFiG9zbxtdO9yRpWVL-qaKZ24_FS8SKTFWcGUQDGlUq9xnfEDCHogLyhqgz5XczYZud1jTTvPKzEoxkQCHT6vWJ6slf3ul7tjKEOYAg8g5lPEIemIrL0n2rqpLU2mm6Bh8TdMQjEMTcX8ehfXkhZSuR5RMbHvqOqy8qM7rVJYWChCYb9GBJ095ViJAqSNZNIGPY0ps0daOGHdHepJuzj7Dq-KT-MfDXrMcFhSHI2sYOY22C-UlZdJBktkC-t4fwYa4Ov75wVD2Zr4xZggxoF2uunaQhKV8l-QxNdpgo6fBohF5EEpOCF4kdJO6wIDZ9nZF8fid72JvBcMn20l6HEanN5lco2LHpgHYOh9FlHsTunBJa_q0KhSCnMbsD8yn6ybWlNUqjVjGLcwawDiM6U2RtWEnc-6zTlIOrFT76m6PlCJGnbuQV6nHBxdXTVFIAQ5r7wY815HbDnt4cFpPmh3jdW0llP_vM3gLRBhaQ8iPN61w4D0K31jALk3eJcMKEdf062dMnF3UpNrxtAlefzANmCOl4Xrx2IwbgrUyMe255HwBSl0ssexjKrRfD5o9MX0zYb4mfUmIQSYLlr0bZvymRlr7QFaRMo3SqcpyIzW3V3IsO1yCFdf1sjQbtCe7Jbbn1xbVX49MqHK9mQDhREkpyNj-xis9IbecBf3ln5QgCvxcx3I-GTkV9Kit8jUWAqeniCISEPB8VhMkLfDn7cEEoWzrSDQ2uGvBKMbRkHYb5y0DZ0yy-BUSzQjXGJNYOD52vlD7poHQ4A5sj9viP1Ah48hiXBztR8rsurmY0EzWjB3B-KotCWwNukVA3q8tpHnnnIuZVNGhmq7zo7MTuyqSfotDJIGscA-cRU25a1UwH7d5qPS-WtZVw_TCUGUWv4PhURs62IzfMWpQpbN6jej3SzVreNTxipLjO9MarElTjdPCTlKOGWjPmPHc_cnWdawVyynougvhKtZcEbK3EfUcVmB66V1jfu4FsY6PJJa8sTVMJJtXK6QlJnZ0_YKjF-RaFTbY7x-ddkL5q7Pr2nYoPA2iWwbn4xSaYvLnErK-ASg8pHt5s0D9aM8jjExHTEjqGwMymRbPb2BRjw6vECsiIRqvPtZ8lHYrrSJWJ3dfdYq7eRGqAWA5ipRGYhtqG2KqGDGwoOrwKLXo2lpGB4lsDkS2Irz6KQt3dQB5TWUP-WG3fIXj1LpS5JA-LMVpXe0T1IZECGRvdW54J6nCRCZO_QJoeLLba0V2tvb_6XS2nNp8PycrgA6b6_hpZugbW6gl_xOkWN1Oz5k3ccclNO376_aD-D0hcFCJ5VxDJuCAidN26kDzyB0IPhPqS4NCuwsLRkiRD46G52WS9D6fbAbLxGekm_BNTi4rCejNA2Z4VGBxYAT5S66OM1-MB6Ul1y7J-i1l4hulBQmzKMvPTHctRt8bxPzDk0EG5eChizpY1v1DuuHEiegg_MnreZsaSiIRV66T1-GQ9_Uoo7-ZRqcihU77xOUdhWfmy6BYMhZttkWapJfFImzqik3DwxF_LZR8wVEgQ1MMzYAwL650rietI7U5Bnjj9Lgfvkgia6-F5kd7h0PP2AtSW4aIRu87EzeZHkjjtQp4pjeSJcus1A2FUKdTVTVn1PenJ4bHHLnsD0wpcS4EgdYAP143dH8Z4NBxozxJOyUJNvmM0iHdBzsg9GfUqdUoaPB3sTFUA1uNIB8bTnnYTRQ0bYCvjkAeNWBFi6zRAg2ClEioaEy0M9xLdlguqT4JT8laX8Il4z26DcHBN4xm4uEsLpFApZ4vZY85CmsQkPMw_r23IeJVEwlYwCSxmGoz9CrnnUtDSxPkYy8sVY1duQ3Z8cfNQ5BxI8nVzuPLLzAvSbrg1oAuvNxQCPT9YpE9KoTQEVxleRYeGQg0u7KcuUwZgz-vFz6KotrwqSVCCmwVwpuQEcg2VMhPxgWI99v8ClHUDbv_HyPMUUKM6Ijx55Bgiu3yb66P4oDUP-sOqTpKjLH-NLnJOl_J7pUfbGEZIukWcpxDCPuVbh-Sto9lIclxtuWr2tNCjEsLZBVVpcqG3edjc9kQWAlTFMvxXvzdGagKOnYxwpRxdDepwrprboIMAn--T4K7OKrCtKRf8KgjPyWRu0poHF2-cB4L6jrxvFbnPrhNGIiIytdbp6vaeoGbQuKc2AdAZKMYKAZc5WjdAGLhALa8ppQ9bIiql3hpihrHnHFR9lcNspYx9NOKNA4KDdKy6X7iw66Fys7uW4Lvts10D1XE9hyukF0b2FhvBJVw6fbdBe5151K8ep_0ejGDs-wRwNHQPHCHJ-b-yzRXKQ-RTNQtWUVVHG7_evje8xyBdGneQHMuDpAXqtbtsrSVOmjrbAqdOkH2y6mdanNaLAfUDukdW-4u4CAAUdh7jB1GCJFE_E2aLyS0QBMy6gJfEXfgsoLbSJT53RB_WO2W4SEdLmkYrvuVRCAg8B_4_VJrgDJ0L6NxQoZBymFmZu18c-w_Ny_fc-Vp5p-ZCHEH1f-mW-EzAQ3_nu8ZoZ0AQk-3x1oezGl2joaVSHRoOVN5hP7Fc1wCplO5ANuL2LPcKJO9-WdiKi-M5rqBY20zxO_wg3M4hurFe6RaUPXt11e0jphDtdBC6Vt1S1EZY4moWgiwonAkFI9bxEs3yVMh4WFtboObBjjVw_tGk_uK9BNe6V7z7GQep368hUO3H_YT5lMurW8zvZF7CFjpgOC4x7zmb2MFZQSYD04MKjElIvxTKtMqB9sEXL_xEyOzYJUF7RVTqIdw8NPsdzktLg1LUWw4nw5NfE3ayGAQT7hPOwrWIEt_qdeBHrg4tisVOZc33opVuqJe5Y3jEO9OvNzjbVru2ifHqhrM2oJD3eTzXGNU11fXTRL2YheR0DXg2Hc3_xK_Pez1olNqsDJzvi7l3bBX-MnhCRPKBVPp1BW5irAuad0xFIecBHnL7pSZQUpW36PkJusnuQxgL7gjxZsPnZ8p_wnu48I2KRLxmI8_TRjqlDyFs-ECoqwX6cx0JmVekoLI_a-sbHNX3ds0fpLP5clcsc9-Euw62rgtlfFEYX9pj3CS11D-PgjANjwfVVef6UNGtsxCspbGhXvy9ZiYqXXBAFVtlOOfIVvXZcGYO86jZx-GAn6e7Pay-Ep9W1-PS9xgeCGrvd1VBybgcJUTBCmdxtXw3OjCW4iU4QkiK9BQ6TdJgXgCMVkANtbvUTHE3zLqX0UEc2Vrn8ly3MtxlyqR0L-X1DoyzZsRXC6miaidxhSO0KWskXT_uXXWTVz3UcVI3Klpkfrus3ZUFM4rIrJ5UCdy3Mrjow4tPrl7-aBZQxGKT55Xog2Gk2G6R9NPzSEhX21rl-hHwXGbWoULt0f4rVOgjy9xaj8YNKMGV4Bdellm1pxCfifg4lCZMQ-rTiHoqbnt1sIkqzAzobqyd7pdxzO5VSeeYOrNh6zQPJ36h0skQX260_7OD5yV8rJglW2hxEwaD06OVQB7vVHU2lGErgEw9AfngKHSmVdvmgXVU0PpEKkHAbg67X8vOWxLczxEWtO2AXj7zG8icrLOGacfXqC5ie4t3f_XJSLVOGAtrmbODBW2QlisrzV_gi_HeyYN6QEtwKcIpRsH9lZGjBOEwjO3GPnC1RPja9r1KB48oLvyDsyB6cAHrKQ2iaAzYmyQ3R5NLclMd5xs9IxBepcrco14mmYHncNvbddRZQPinwp_SdeldCEWifI5C0Pa1n33RmEnTTnpjiJ-pHjJXtcgYusNeitc-QwsZuinfMCdAkDoA0NT6EogY0aMFDbPQmrFSQvzdWnwe3VlfHYvmh3xG0kex1572--8bMDPZerKepclovqzX2m9MP6JYG4aBRb-NAiCtZEC8abppC9amLB3rdlu33CzcA01iQAjMjql2amFBg7nQZbZD9kFWQyFwWlWbe8vOW1C2LWVR3Qh3zQaqjO8D1IUpZ5XjBnH1Ko5_F8W7CG1W0myKbQPOTb047T4mHgpP2STNoxwYoyrws02kWS1mpu6iDm8CHddpM0srTRBn4zTk0ywT-r7fboGeKgZUj7MBbtiOaM8mlUysa0LoDkSqa9QVQWFRtBq1ukhbBXr2r1zOECzvuASt43t63XRErUMCVxH5UTGi0RwsS6mGMItfyz3GoCzfqhHMpwamu-Pg2-IbzPb4APcCNwOu4GH1IA43wG9FWZ56MSTiOgs2nZmGrKdUJMt5mJTgfFKtZNuRjLveb1cGQ77RSGTwfE1bxdNIMEToJuSkXhyJAWhPX3JK-A5dF2hkYgtXtYZt-b5MTYuPgztFJdH4ZaASLpCBvG-r7me-QLZgRKYkmB6c6UNouT9FOMOgTnSy9dptEuioOwgt5LKh0MI1zu1AOe07VbjSEgKANkAxh-qzrTuREzy4XU787FJaAhcrFRTRkLgT6MI4l5MTLOrljEjGVAwDnNXvokNM9pw5uNALUZWkkYsmhLijiqQ2nxDHxYPfPuGfvcc-gRrh3Oy2qtk80DLjmojVPeL624W-qMCCUPxNzLHXiuAqY8ddir_rHpPPTdd15x7Ent9BdaPfHZe4lI_v2uuY0Be8U5inobVU8bb6bUe2-w52J0eHxKJmsP_ZeNCb_wVr0L3v0WxVroc1vX_U1cmfgkyLjReL-4G1ZUwI09XrUkW85ysN3TrQE3uTV5tvuYUx_sQTWWSSwkMwpQ7lJWQMFmzIQK-eoJw8nstLGqh3fhNHVqryRwJXSfcMsdiFr9_CIqu5RyiQTLvIfKz7_ds_hpfeLx8oNdRtzYDOfj0P3fqrfz4VK4LW-vlqL7KoBIqZLrkxQ2zRfITrukXVWL1wfaI5CAJmqju-iH9hS8JSIuVM2UnsOvmng_XHHVXHO7N3gVVEwgjW0zjpe5mzDVoHn1ZY2D6l-2yn7_hfQD59Ljd24LEQ8X_9LkhnNZ68iyCsrkcoK9Z5GZzHtu-Wt8a0O6WTpz4S4TOVMPjDBFZYuZ10YvFTx3Q7I7cqO_gQHgPtPMzKIFLKT4ZReY4SujuIxJoSw8S38S3K9VFmOZ1H_MEo5acuxVwlNKIG9z2AYUy9at5ATVR4wVG1ETuEp9MOKMiTopOkWpSDlt96Un5d9GRctV42UVsPPZYLxXcihIL9-4-x1Uml4SF5m7YzJLBwIfiru7aYYvNrNRdFLCD26I7d__lvrAkLWVm2TJpeSe7xI7zaqK0Jv4tUCEAKHJznPI6NHB48VDtBM1lrtG41mi6KjhrqaBv_rmV4WQW56Ujgray-iIiLJnWnn9ntPdm83LBPJ8DX_EAHV42AXdBSpMC_LCV-UsycA48i9jQ59a2o9XkH3ppNN4lqrplF7UoyaKChZokNOqxSFkMs_yTDdA3_DxLq1eKJwD3lnpiuYJyDABd8rHFjXRAOXyvPacMICd8LbZVWU_5DpXAC92n_2hstlDCbXy6P1-imfF7uRSOzjuxxom9LywyZAEoAYJ0IzsCcRt1sKFdgxO_c1FbBLy6v6q5Zozu-pAD9piiWylhtnoApKOxiICnl-Ts8HV-r-iJXSFXlJ7yy43g7s4iBRMR_Xlan9v6mfZne72dI_7ZekdktDjxTDgYZWBis151MCerYvFk1Uh2Ow7yvsszxQmkIeL9naKFnIHKZ3He493u7BPF-uN3MKYX_6565yGeBI5lymBV_nwrabG6J9N9hrSVFvx5F9QzJ4pMB6mPaVPUgKoabnrgPqXxAmQbJiPZRxxbm5RUPaqUOqjMrW2K3CuTPMOVfOdcPsR0-Njtwq0WNQzxiwsrf8WLpr5nO9f-x8_GhEpb_FJOFz-kpG054cQjZlvxqfPik9KyyqQ_AiSgY-XlDP4JS-VOu88b1QUIGEcM8b1ZRWbxnmDwOFSFoO1bpDjEqmlg5TIYMrMtG09FqpvTwlaqyi_4lWD7FtmjexBcNvdlQvy5Zh1m4NdOqqChpVUzsmDJiPTummGnxj2drjeppTQ6N-H__ozAD-6XMZhns7tTjvZKaOZFCT_bR5N6UkBcmt0UP1UdK7xM5hwlLg2RmHeZ6twZDh4Dvyqyy3uWKnihtxehcV6MVgKjvXJ8zRAvEdemkprLiwtRD_Hh30gKqpNRbmW09tav-2oiVtKjHHWPvsVEtpcB7kTM3g47dUTxMfDvYsMKAJ5ivTBO2ic6DqGP1H0k4XA1j723QM79PaCeg4WAm3w-fA9zAUzZQT3e8Y18cwFbRo8T7-1cqCm-F2ZjPKXrp6ApGVqRhsXe9ZyYNu1BC-w-ui7AEn_LE4Gs3DTT3-mRwuWThcQ7m4XYMcxg5F41NWQMz0uZRrtkeWSMq3hrJYsOtAz82yUCZ7fU_LFiH5BGULN5WeROnReju_nzqLIGMXfbeUSrgAWwGXxKsWI7bZI3U4niS2yJwEbBrNjduAitc-MKOVm4gH86iL8S6DKu34vg0lVUUQ5zU0JsjxqzJVuIw7ATGVe5xT3qHKEZ_0rvee_BMle_O-3jfStrH6cotkilMcjG-cJytHt6KNibUvUg224VBFDnDlX1ehqSS6uXYUqcrJwvB8rjxZRhJ3g0bGEg8iGOjNtRsLe3nIbN24qxKy1KtmoXqFjJ9r-GRuV8Wa6Dj9RQUX5Hf6_Tq-dEdQNLLao3LBS3QECJvv73UpgfaWpxNlusyAHle63eHMOqhPVQOwp2Am4eil4veEo92uN7WEX15iPCgdbDG3xQyJKaEMlhohAT1PneJbzj0dV9DgGFPeOSFsfndoZ22SWInFDWqZNkjmVUN7MTKF3c41NEgTNJN2iay8Rzd9BMb0YiHLd6DWUf6j7-qi9BQE8-zViK9bZgumLYa2COJxQ17cps-4yogEMv--DBkzTCpbsYZuFrU40PI7VM2TWMDucJaPLN-Jp0jtQrvrUMMg6GqF8tcTIxIdIGlDGL5DcHbCrT7h48sr4wmi7bE9k-AnVBryeIW7Ng5ItlBgBpNkxgwEAARTp8Oy36J7MTHc2v9F4xq2H--bh5k3HKYQAEokaDpYlzyuM_EnotSYz1XZypWFLlmHoM49YDgsWjLAwrNvmqW1YYu5QlTCIzzyVA_W5jv6AzmNiNwE_WTlnT0b5hKyRgKYiD9T-deEWmzlL48gP4eC-cxxP6PW1VEdZP7f5xmOH_0WU62ZaTdgjbPSRWE36xCy_z5BbbyhYMt0bl6p1vK--WKg3p0la4k6BGdPWxh2BRjb3GFfKFQviY0MQBBlgQpnGe5axcMpW2fI-drxkhQRSpHhHm4n-RA--dV8HtZKAUA6YhSMz5pa2CuHL1hO7sJDL4yAfKiDEe5C1Ds1XaitbqqPcoZkL1QsQVlB6TRJ3aaUWvEu11ys7bjRNC5Tf9JKPCvTq0F5ShwQRrFi8EOTrpWFu9gMQF_rxc0aHqbdag_xpSrYOz4Q-GDPM_3nrwm7T_5QozOtUKqWa9P4vPP5MXEoSaZBGjdm2BN-4cixNbPLJ7RnwvBCctFh3qngW25D77Sh45LAItWFi6Jq7DEQSv4sZuq2x_NfL-0RLBt28TV-ozahaCWfmRXbsDghTc_L4JIvFRQTPuwgGohWCI3XDBXjaxAF1XLPv2urMy3kRt5xja7U-d4s6M54U1AtFteQuDiahBhDbqpOhxfrGgWm9EBVX80pUSEfHqzAUUhkrv6-QqzF1_7ZNT7zAkt2EfGWG91S4fPys7sOiI10HWhdf35bx7b8l7aQQue1DtCDbAacimZZIzm4N39Yg2ADWIQvGguAtuqN21z0Su_Df01Tx-oCTNpAPMhWQqlKHLwPo1T-Xl4oybTKxcN9RHgCJPgEpP2Gr-BzZtV6zEhwSDl-92eIclIVM95-Ey7HtZuJWn9nr-VHucDtlQhYdUWMlo0y0lcn6RP3NA9g_sRewpGKV7zhk99jt3785SOfgyjY1ewf1c2PtHN-nCBH9T_X0UF-Lfrk-w8hGW_mnCVkP9FtiaD9xOADs758gaMD0jT2TXFVQz4UYK3OYSxwj_LIONjZT0b3jnNmhisszDLFoHgjvFNogsmbfgl71MT2hgt1b8THdMZtQIptdrR0-yL8uPNkMQrHn5VAXSTfUWI-aXl5aPIAlPrEZDz_nMAv2N3pYxc-MtAN9s5XVzIoQZutxKy4-8Ux-EMA1uptRwFUUPewuDRotsuz2uaNk87M79370Pm4G-okPNoJ1i_CEPOD1P5DwI_PC0AjNVTDJNtmX0L2P7T8wBhjHBSsVw7439ztKTP1tB3_2YqFBITUFhmjiONHoBROtf-DGkKYMoMTzZrGphxiHYdXCTpmmMLMY5-MZcq5qI4DjMBQxgd2udOkX7L_cmHODZDduojjS9IvyOF6iP1J_I9C3J_Qr1B3skF_paJqgK3ffHunbEdaeZN8bM6fX-PghO2tUKcNXON9fJ45SLqoYIkw54MOh3mhZSnRyFVFKAjkXOL7ZpTN4MPQ_-qfI2Iihp_2NqcUu_-iRjn2bIvtVSwHsJRAk3iLjbSBNADRjg0-z6LpHbinJn30OpElmYSoqIUlBjDqgaBf2ERoEaFP-fITe7GMt00TrkFCO3s7uTfuaiVqsdbihz42Xc347tOT9A2IY05h4bj91yi6j7DROoVPDBCLycrqo1EHAHhJ4l4tEfms1TsEh8fBTEAC3YPWJ0OiASaohQw_PRRsSSbLYMLCrm-7-ZVZNMVNHyIxA1jS5fjPaq5-e-67V91IdUPk3P9mSy9jYl84jg7l5qcyO5DKJUjNzG8osHbQ30uGC7c6XivYQ3mzlm5HSlwOlnJ8clptoL9VUV7vzQo1Cq4cLcZfmV_39a3byYOpbqtIzeSsAkxzJB1kbUTwWMCXtoURpWxMWyQAozpX18jKvDodwIC6--jlT11bxakjiKeCQGbLAoHyGsPRYHx80eWSTKygkrgkE_qFEe40CvtADxavDVJdV_hX8XC8GSx1D4Sm_YUDcRMxWbpSIWtvpdTJ5ia9kCYcxcUa__Zn56qjgWy8ZY52uFRkKu25w7RGmyq80ievWVWhoM1Dv744WmMkwqdW0Sxlm-6AXM4zpVU5G3pnGcncUDQ2Iq0jeraRk_Jp_pA0CO8m8pPVtZTXATKQkGMplKSyv_RZAfanPtmVmJr53P5yww1eYnOtB_AAbDHZuL9ditRRxizddXYSygc8Jbh_N9X1LP56ImgAZfj2nuAi-akhxnlXTDsuaWMIkjpsr2wDRLsZNI5CDvykjhGLJjyWfzer_QnaHiuTHP3uoG0uW6863ueJTMiAg9WF7arvtiCcaS2GaB8yQ2pNu8otpB6iLWiElOOm19ecMIg8qtQv7HDTkRD1KlzntonpGsRZw3fYoM_e2O8z2gzRswVasRBc1qeZ6X5HSwxQn7gki-KdkZw0iTZmN1oOKYklqYSqoPulNMzM4CN2fKWVIYzqA7hsDNKl8nbB4ts0D34jw0pLc33okqj2nW6LTbykJsqSEn-c7nkD_ABVBmWhVTxIwwyaXZy7xYZBnDFz4Ij14XoE1zH3k-cOPMMLUSzHJgg0el40KVharglLoa_1i0KEl3Twn_aqNIBwhdAIdmlzk1TEnxFn48kCPmwkGFBLkTOCdNeajdc2_G3SFw2U5Swlp86Wj7sZj0SkjLR4d9VqcF5q0Ue1Xi8H3mzohQKuVP6Qt1GbBSmBtkPTk33s08y_2P6SOEi9uKPVTiy37WQayXZNA0m_VavHOBqpEv_TJUp0LrLM2wbj-i-BEDirHqSZnzjmd5SGZreNO3UdPwPUbQaNQAAeT6Xi1ZAYo5M4xGenqDUP8-65cA81ipU5J6nC3u7iznIceBH5VmA-vMXggyeYWDtuV53X-OeefbDzkMu1Gbj7uwDjU7-r9-OBdmsDs9beiC3pFDdEUzwS313TRsJSq81l7xclNYdAA_ImnLZMHmXetsEolTClpzBWo5bouSFPB9rm2P4c5dT67-R1urw5v5hoyUMg0PTO13IC-w_PNLijbUCVJFSXeJEVq7_z1RWXGQRrKAOHNL0qkgh0IK6txWKDW8ARWCU4DfhhPAue2FIkkFt3N3qNvd0-sCN-YqN0Xy6AgrWUuWF7tklvlKA4DcYWNa2Xb3SWalqpbmISaZJxJdcO1aaKFxiaJ3e21cbCJ5rIchI28-q1vmqyPZXzZVCUb-D-NW67UYwoyXzs3k4AD6StaDRQPzhHWGrzf0rPmSv4ODBa6dYpKiCbp8oVA79Gi7INREthx76HZ6RFI5whF_OI4MI-QCXyxXEAPsEsqS0sXQREkXQ4BhS7XbAk0JnDbxBWEL_6xL3Pat7Lraeiai-NDJ_Dzx7reA_OzHG61UaTB6KxXSnbqHP7--Ip88yHNkJRKJwJlRgQsyeduMI6a62OTJPnDnagl8m6wcOi8DpSy_EloGHQPb3lHXuKOpBhBZNsYNdljTok4wsYAu1hjcZUyDAjRFeaKAqCAkBdq5BL_fGJhZgrTFteqUGLWWJWoMpxntgtSNfBDbwgqs4AlLcbk_OxWxfsaucNAfzmRm0v8LDW2Z6frhA_qbCBrd7uQ9GfwLu5AmSRYr6jB8pqIo-3ASzUNlunAEU8CYsO52TKMkrvJDpvnKoenvEV0bhTJcuH3OxymoNswJdV9Zm1gWD7o3NqZg8H6MbzgCO5u2fh7FFwyMucszoNfUJEnlK_OWyrQzK6s9Yd7AWRp70MMviK7eHjjveZf8NE60eD_nyR4oJ0gRfC5Z2DjE6ExI6ueH4DPrkxtzy_GDJpurfyhVrTVP2WZrWO6_29o8Uw67cnuyboKJ_WYIuM_bxZCVDeq0OWvZtjC6QzkanJRFvihQxDDbQrTz8M0GysjbL_h49dW3P7OvxrNed6oCNA86eJVXUd0ACTOiRcLaZddJPXX9NerRX3J8CxK3323dtvE9ghxvU25HGx0Bbc2OzMAbtD1HgowZlfGHWdu41Wj-BHK1bLPIMsq3caj0jLVkhm-fn00Yx3Ue0ZhTY2sQGamLee7CpNYFITfStkaGSzuy64n2yMfYcmfx1iRUmTStwLWXO5I9CsGHa_ZEzarG3B9WYDF7crXeDx4LcSLmIByqt1ZVxSEe_xotvqkHhArDcSWdWtnGXg0fqxB7bZgUjC3SBt7U5k77-fe8lp0iqhuYpB2EaisnLixqWyh4OMvjw04nRZMZEQ3H5wOzIt4TcJ4CSB8XabyUaXGSJ3-IsiXBED9EgLU7u1c3a6EGVyS6K91Zsr9TR3jqumTbAjTbbhI6aMTx_-Se6XmILF3WO6BS_4DsqNAhthOfYLBMWwYapj3ga3sMfhGGkb9kewQxISYqAg_lDQVjF4jXOwDyZoyZP0dMVKjHEf4qbiTqwGQUkvbAQI3c8LaO7UfOmXbqbkwkTyl2doJByNtMpeSd0c48JHocwoMqsh_lt46XmxRx6_2RlxmbqmJCUiHd49bkXxSQiCfbFEPeFPDaNGuTrjLgb8l0FgpA47rXcrHrFsPcd91LCeUrQ-6j0Vf7lMsLH-UsAwDDpDPpCG051nVWTcWiRptN2oTEX1S5ZWWMHOnG0-QVs_3wsE5_RsAVU6KdblPpG5sr3pI3lPTUXOgnr6aqATEWe4q1pmJAkJcX-DFKplcHRBgBJmBd-lbeDWwW7PWJbEbIaF4poBBW-zK76PzJyXHtEa5iMbqMJjI4TCWrSv4iqRZDajn2V7yvB-vWymntyrSgUwpwRUYPfmp6QxfrumemlLjCc_nXcSNdotqXbSlym27AfTfwiiOt_1tvWPNqbs8OqRjQMlOHkfaOlEgbeAbczHhict5QvRp5dX3Gok8q2bVUllQ2YWQ7Oj2lo33GEjWFryxvCml47numyGeScoJOHsNRjoC6eu9gkGj0u1NOgyNUCXR-m8zB02xe7vZ4FpaOHWeWwk2EV_9p6OdjWtTYnpOjIEmJP0QvxIbxmHfQGLI8AjvR_34ubv4n18-K2ssuWDat0bvikDKZ5CyxkCwsrHJhMcszcegWxtWtjJOIyWpyYTbaEipvxWuCUhNtVifB6P4xsWuaknuNkMWF9roSNpuAnv_ajJB2iVZS9GkfA6qvMKIb2ahh4UVvpAtufuWzJwpECtxWOMxquzYHDIzpb5lMwKM8kKsNr17y-f9DpGnGzIb-njl0PU9zqINo8taUxxQLfcZ2Q5uVTB0DMEKiN9sE7HXvnaEZa5hkJu7_P8Q_4T_on0h4-xL304YnIdL-Ms-I5meWq9i-JTXt3KodxeYnwfWVqKimYk-sznFYiEVG520vD_YblnCQrgo6SWSnhQKgNdODpmln3jmmQIu7R6ZFF9Jv9S3YVwULeaMfcRvmgiwzLpH03JDMqdOKl4C99cF0SVgdELkODt7Znka4LWxKK9ZaRheBfv-QslWWufzhZiOYLc3-gwk-YXARc-gVgxcfLJBeT7AGJ635_C8p3rN7tslayQLPzQXkwwcM0UMlJOPcEuN8sSGX62a4kvNwAqTHtL-zR2xempEXR2dLqqLdV-mgtHo4wxbqJ5sLc9zjWHYVcbqc-gIapquB6AKNshJZYESYu177fOuMU0SzAwTI-TEhe35nCRHqYushkla7IeTTx8sjnnc0XjtnxeHYE09jNmuBbHTsBHxONUdrCoZks97PlVmrKKJUeMwjGBYkyDE2cfGhJcrr_KMeBjsnvsuY0fd4f9XONLL2H0JUBva2VE1LcYwxGkrAI2KRH5P4mvl7Y16rf6ERlzMRfnG22qIl6jNvlEmAwfxRJsnlE6eq6F7qFAFDTeSRn-PeACbyFfloUIbTfr-5wEx_AsrhUHFKeFbx6xa4ffA0GUJAZYY-Db3P3zCmyf2R78-2cwla_BbbB_Ngpc_2XDg6zTfwJFmhm-yJ4dQ3o2WLi8julhS51RuybXPeCU3juu0wOgJKhPV-f_q361mpC1IxapHGEoQAoktaobbpXPlZ6PaVyNWeubYqnqWWMoQ_gksJei9U_7VVHPToLuV_q9HHdeI-MIpPtIaUO8DDM225_7278azmG11j1oNkLO7mXBhKA8coyISuPxO85sF56szjEAd6y_DizpMMYWzC9Eu67Nsi5o2sBgnJ3Zo2ZCS8G766SIBSex3-1Y6keauXM_rwKCf2EYETCc2GdYnNubz7e37YPT6h8I-3JiA9krmu6rVW58trpkB4UtGFv7rH0wBi4oGGfUvp4rHcvHE_s9zVnDk72MOsbu7VqShlTD_kJSuVb0CMvRpbi5Zkp0-ZeDggTq_xaoJPyt0zuGF12XZhU0UFCwLLWbsJGFSaZmwMXJJW-50Y9XSHOuq4_6LKgFxpMVz19Fba3dSozxP37YslcvujyIHxOWx9NuHj9VcGpAKpWJ2s0mKEF4riqOwnPiYbmHewC7tgkYxC4W1LNBdPhqZO_yVkp_-C-ZXrmG3t443I-fOx0vfEfqvK00aG5PLVZb3VsFizk7N5rU89hjjz02_uEOflIS71XCkq_yiSbU468hb3gWUeUXossyKNTpWyZSHpoLrLnrbDnsZkWwbssNs0iBMGyvdVAgnDtaXeWWKXoUVXr6BtPTLVgckdQeL1WztuA1YIXvH9RaBEohRLJBueh_LnCxiKwaNrBS2eXDbK5477PIp62-RS4ww8hz0xpMIbWA5K9CKXneMbY_er3gFkjDEGPQK8-Nowi4Ds50-kOPf6mLBVW5Bz6bSJwICieJczGSXf6lpPw9ySUyb3Gi6R4ein0klP0cJOp_-uKYvnU0QpEI6VlgTDp1MX7tnldkduI7fdKSrn7FY1LWfM515ydz81ymrZ3Y0CKuKJHltuHlga-DAZ_pWhhn8yBbJ3i3lvv_fMPtHlCb9qMIiE-6pTyN68tf9LaHzUF01VIKBC3w4Er0Nc-RDU8T7YMM3Cuv-um7aIGruJpxvbMKgkYVVZ3H1FjCrD_21ttN-PAPmuUiZzxXHLi-326iZ6eS3mHwq9Ggj0ZVe_iG9r31KUwzwkwqsYaZV16D6qVc_CaU_3YI8oEaUtnQsnmjqlDqNQMJtDDxlmSIZPMSCcckyJx824jc4j3_jpUd1WiGDluSbJ3GXtXnCaHASz4YUaiFtCah7kRxq6n-inRzxWFtGxuPr6rToIDhUbwl4mXRaSZROXE--NoVMb5SVdAXiXyJ-Y9r6Ldtk9Xn70YWdGdP24ttSO59a5WJxqiFJaoNZHoGXUTLsoJgtEqTbDuVEoHHmUksr1Ma-U0xGj8cdAKswO_3tdCrxjTMuDewvIdKB-01AkacC-vtYq41EL9I_smfOetqceXMxGw9v4EXGJAszVs46sMEg19zGT0ZZ7i2kiEeiXOeooI6qM8o1s1iLw74xOZFayN4E78i0GAyWjDAix_-YVtaF7WFBDKaSVAko2KfScvu3NyvgWQYDzSqSD65Y0QFLO0z9wzaJeaqVmLw7QuvoH1vqeKKBmZxd59cdhDZ7UMRfFZNAzqQTFoL5muD2A3W_9v8aSa5qvCJDqqaaFCi08f77M2KCOGxSdXQCrQcqhP3rgxzELv8v_q6YyooYisSIaL5jL00VGYCG7sI2uDg6qez7q1DFvMYY676P-X92g-8YDr0Oo0u613V5TG-wgWoKQb1WoZh_lIOnHqacs7xWg_aNjRyr_Ye1XDLDK1dkgGAIxXFakdv4cVNhnxAHKk3kLg-fjoWOKI9BmDo4YAgZKirHaBj608NZZUNFH5D2cUd_SYbUk05Rw5NlK3Xswlmi8gBIxV23PaGgwboRkCV7ci0PLBSzAI6CQfMNV3QjTu_PLHIsv2CIjwXL6W_lD0WQRuRWzeGlFYldAbun7ZgqnSyeQXMnw1cA6bUQ94mSuEBNGNOR4eFgVnLHo89m26U5GKF5ArclEH6TVBxhEO3bXGw0933WsiEHL4HF_22qDVRNxfd3xKMVuGK_cW3ZG7rOax9XvGSw2oqNXz6blWIccX-P4vhfjBW4fUu1TWhWw6Jd37lnLXeMUxKYsmaaZ3bS7vATt31kvy85kSoSCEv7NGLQTT7FECtRd6xLKh1MMK5IiiDNNj5pI1aXDvIcNeQgyC33fRugNOEkhkW1Uvhzg8nqWja9qYQ9wVtClO6_6e7WW0jLsx1v0gNTWmgCjHmvpyfqPqThIpQtxtO6DDjeVWvg3JqOC0ZdvFi61MG2DaIqchq9gyXImyVi5LlckeaUCRSplfARTnTL61yWI_misFV4A9I56c0Qj93kPEiO5wuQDalK-Kva-y5PRTYWMRBMd17yO7IVjJU0em10JtO5PydyeB7uPW-lp3_M5nuctr123OwN4UztzVuwP602CsgD14qi15b2QmxYwnMh7yIt4cYgbMQ2eiIPS2hNCYOBs7I_2TK4xW4io2Wz5bqWWL6COlpQlRlPGL6OI3kFCdLKXs34oD6nuLoizS-N13-Dtj49pfvDLaYsufXHXfk3E04oDYM_U9D2dyPUrgOf8y1AuMD3uarfQOAQ92JqePwZrl4-4lEqwJJ6johqXLe0KWUE26uejgFeyzMtflCeeRkHRiAFV99CXITndbP2rpXUvG1gEtsOv8dv2uLXdexY8yeT9ApCPeF1stJCEGWOND87B_Zm8ETMD5T6hrlflCWaY9zSTrJ9KopCv_865h4kKLTlRDhH_uH6QLQDD5HY_av14y3M9PLV3oKg-kMDLC0d_zho88lxyZW3aX-GbtXPTkx4knRioadgRRziqaJD2cxa5uO-xyPtDWk-lWNfX20EAoehvZnTFGK3XTCRXKBQk3qEvVKVbsPAVTfi-EN482wqcUAH20OPc7lIoc4NjiMU9yaWCEf06ELT9Ha0hwUVuRosdMr9axTrcel9F1WuoYBDgjhJ_EhXXqS7AFBuGo_eZjW3mK-k3_wCuPgnBjxL17XBGPeabeNjfZm-W5hIhgR3wG4XlGqKcpdUBB_t1yK34KSa8r76-jjDrSiN9OQmHug0qJWnMQ5Sq8FMX1wpa8ibR3YCxiYc8Ckf8loxKOY5EeR65guWvttTNV2010RUhIiiNbfjJZrjepHJJol2GtmpZqlPa04IO1k178RlTSGaepMLgtuobtqmAx1xLDVtvYfwtNomiJ7svHRp19OaRTyl6lgWihcOSMQs7X-BhdfZ7pK9TCFYqnnTJqtnDjgHocRacbCps_LSAq8AIphvuMLj3pBxN5dcpVTXRruiJZDMW5cDCn2Xe0am0WpIX_RHnEgdoREtz3tqvKjJBFTfbYO9fz2t5E0euuabh7YEB_1rdQ6wZJkP16tzY-Fz3HKhxwqUYpvkGsJMa0YQaZsryk2QGeTOLm78YS9AUWtcz7oKgORwNOA0F3vsQD3SPCxH84qoztyyOljhW2SC0N0r6cRxAXe2MXWtM9mglrk5dGYwN3ULKzLTLv_m222g9Yxzpfo3yftX1fhNEOdZwEnQJxOdDBJmNBmlgnmvXdKma3nGqYTu8LA69QDIhd-sj0jSQ10wH2sHdR4BO9ECPkz-uMPigyRA1eI2BtPevqImawMv1SsvJw8tFK1hFnZaWbs-yGihpSAmn1xKZNfL3Y2fk_bMTcvIvkLZpbGRPHqTUBThF1M-h0Qf8ahaK8VaFHhUD0Hc62hzRmGuxsG6xIrdXFGHMAVN847kfPTbmbWj4_FPTOSFnbZfRcFYxsM4uY-Ug8lSL7oT_9SCb0tg6HE4Vko9AcJfPPMOO7DBJmk7JI_YZ6N-myolmtdBXsS2P79jBiT9g0i7G8mU-mnEsOqbOKPaeh5S1ueXjBbhJpEhdEyss2GLox2vVunxDnxjqMR--ldIX6kRJeF_J1UQyayDZ6-79jMr3LafdcK1XlVe971kXgc1BPIHqsQuZy6M0WL29nygrm91KxvjOi0qDC8Iuc5f_DcS1IjKx4VYVOCy0lwK8c8AdnOmk6BOE_JQRK1qr3Ch8uk2Ei_0jCQuMQLisFRcABGinPh7W0cpmH02NAnamSnqEjoCZDefyVT5TfMnr4_FKNQ1ew_pb3LpIkn_zGmhptw0LSI-qdMpSlCmGmtJgPAhtvJLcHgaFBg5jsJEkJuKVh3Tm-eGhQPC4WKBqipEy9MsxpoXURzf-oSnzQwx6yLi9XQ3kFyqdl5yhUp6nJXdfR1e_Q24hZ9-ffwDmG0f_SYCz8IJUPJOCMk9F4VuAMlQEN190dJF4HUjsEQlBCLGpAGqfkw=	local-dev	2026-08-11 19:59:12.523229+00	2026-07-12 19:59:13.109475+00	2026-07-12 19:59:13.109475+00	2026-07-12 19:59:12.523229+00	2026-07-12 19:59:13.109475+00
\.


--
-- Data for Name: job_observations; Type: TABLE DATA; Schema: public; Owner: job_hunt
--

COPY public.job_observations (id, owner_id, opportunity_scan_id, opportunity_scan_source_id, job_posting_id, job_posting_version_id, job_posting_alias_id, first_party_url_verified, observed_at, created_at) FROM stdin;
\.


--
-- Data for Name: job_posting_aliases; Type: TABLE DATA; Schema: public; Owner: job_hunt
--

COPY public.job_posting_aliases (id, owner_id, job_posting_id, alias_kind, alias_key, alias_key_hash, source, company_slug, source_job_id, normalized_url, first_seen_at, last_seen_at, created_at) FROM stdin;
\.


--
-- Data for Name: job_posting_versions; Type: TABLE DATA; Schema: public; Owner: job_hunt
--

COPY public.job_posting_versions (id, owner_id, job_posting_id, version_number, content_hash, source, source_job_id, company_name, title, canonical_url, apply_urls, location, summary, description, employment_type, posted_at_text, source_updated_at_text, source_facts, source_confidence, observed_at, created_at) FROM stdin;
\.


--
-- Data for Name: job_postings; Type: TABLE DATA; Schema: public; Owner: job_hunt
--

COPY public.job_postings (id, owner_id, identity_kind, identity_key, identity_key_hash, source, company_slug, source_job_id, canonical_url, lifecycle_state, closure_reason, consecutive_complete_omissions, first_confirmed_at, last_confirmed_at, last_changed_at, last_lifecycle_evaluated_at, closed_at, version, created_at, updated_at) FROM stdin;
\.


--
-- Data for Name: opportunity_decision_events; Type: TABLE DATA; Schema: public; Owner: job_hunt
--

COPY public.opportunity_decision_events (id, owner_id, owner_opportunity_id, job_posting_id, posting_version_id, previous_decision, new_decision, reason_code, encrypted_note, note_key_id, compensates_event_id, idempotency_key_hash, request_hash, occurred_at, created_at) FROM stdin;
\.


--
-- Data for Name: opportunity_scan_sources; Type: TABLE DATA; Schema: public; Owner: job_hunt
--

COPY public.opportunity_scan_sources (id, owner_id, opportunity_scan_id, company_slug, source, status, fetch_scope, completeness, observed_count, returned_count, persisted_count, warning_codes, error_code, used_fallback, cache_hit, version, started_at, completed_at, created_at, updated_at) FROM stdin;
\.


--
-- Data for Name: opportunity_scans; Type: TABLE DATA; Schema: public; Owner: job_hunt
--

COPY public.opportunity_scans (id, owner_id, saved_search_id, saved_search_version, criteria_schema_version, criteria_snapshot, pack_snapshot, trigger, scheduled_for, dedupe_key, idempotency_key_hash, request_hash, background_job_id, status, stage, source_count, terminal_source_count, successful_source_count, failed_source_count, observed_count, new_posting_count, changed_posting_count, new_opportunity_count, version, started_at, finalized_at, created_at, updated_at) FROM stdin;
\.


--
-- Data for Name: outreach_events; Type: TABLE DATA; Schema: public; Owner: job_hunt
--

COPY public.outreach_events (id, owner_id, application_id, outreach_sequence_id, application_contact_id, message_version_id, sequence_number, event_type, kind, channel, outcome, reason_code, wave, follow_up_due_at, encrypted_note, note_key_id, occurred_at, idempotency_key_hash, created_at) FROM stdin;
\.


--
-- Data for Name: outreach_message_versions; Type: TABLE DATA; Schema: public; Owner: job_hunt
--

COPY public.outreach_message_versions (id, owner_id, application_id, outreach_sequence_id, application_contact_id, kind, version_number, encrypted_body, encryption_key_id, content_hash, created_at) FROM stdin;
\.


--
-- Data for Name: outreach_replies; Type: TABLE DATA; Schema: public; Owner: job_hunt
--

COPY public.outreach_replies (id, owner_id, application_id, outreach_sequence_id, application_contact_id, marked_sent_event_id, marked_sent_event_type, message_version_id, message_kind, reply_kind, received_on, encrypted_note, note_key_id, recording_method, recorded_at, idempotency_key_hash, created_at) FROM stdin;
\.


--
-- Data for Name: outreach_sequences; Type: TABLE DATA; Schema: public; Owner: job_hunt
--

COPY public.outreach_sequences (id, owner_id, application_id, contact_plan_id, status, active_wave, reason_code, version, started_at, paused_at, stopped_at, completed_at, created_at, updated_at) FROM stdin;
\.


--
-- Data for Name: owner_mutation_receipts; Type: TABLE DATA; Schema: public; Owner: job_hunt
--

COPY public.owner_mutation_receipts (id, owner_id, namespace, idempotency_key_hash, request_hash, status, resource_type, resource_id, result_version, deleted, version, created_at, updated_at, completed_at) FROM stdin;
23e72216c90f429aa48ea679f7c67df7	owner	resume_version.create	5efc2dd422df87f27c13a965504fe650b8efa15e8f144ec6c25fb7f3ec3a018a	bc08e2b6793f2904c710e9a5ec521095041f8acb5fc6c811c2771f59a5508e63	completed	resume_version	f95aa789a18542549ba927e1d9fb69d1	1	f	2	2026-07-12 19:53:49.225417+00	2026-07-12 19:53:49.232383+00	2026-07-12 19:53:49.232383+00
9a25570836624a7ab01ee84b19db51a3	owner	career_track.create	2100c16c87bb38f9a8bf33849cef9dd27f50c9d122f10fcfbf910d2624ab08ec	dba438058074e83f20d0d9f17afbe8bc7ddbddb7f0adad40c67c21fab84e6750	completed	career_track	fb46aa14b1d44554ba66e73dc12eba40	1	f	2	2026-07-12 19:53:59.041079+00	2026-07-12 19:53:59.043985+00	2026-07-12 19:53:59.043985+00
f61e28cc5d844eeab338fc0b8a04d27c	owner	achievement_evidence.create	fa291e75bfcfc06703e9782d81a63013c79279f0a87c10e854e097350215271c	ac4f08bae423bb5a51b402070c8c515fc979ec8e1e376e825d73847381768f6f	completed	achievement_evidence	6cd1eed1a6184dff9afb8b6c1a2aa559	1	f	2	2026-07-12 19:54:14.980263+00	2026-07-12 19:54:14.985939+00	2026-07-12 19:54:14.985939+00
18cde1694bcf4f098fc1670f61d5361a	owner	saved_search.create	186e4977813095450a1412161faa8e652c4e0e89d2842628414a618b2c986705	27412cafb5cbd6407b0f66533f9171b856c574868c1794fc3faa618fddd57843	completed	saved_search	fceb13754cb64ec49560afc261feec2a	1	f	2	2026-07-12 19:58:49.657405+00	2026-07-12 19:58:49.665176+00	2026-07-12 19:58:49.665176+00
\.


--
-- Data for Name: owner_opportunities; Type: TABLE DATA; Schema: public; Owner: job_hunt
--

COPY public.owner_opportunities (id, owner_id, job_posting_id, decision, decision_reason_code, reviewed_posting_version_id, decision_updated_at, first_surfaced_at, last_surfaced_at, version, created_at, updated_at) FROM stdin;
\.


--
-- Data for Name: owner_privacy_settings; Type: TABLE DATA; Schema: public; Owner: job_hunt
--

COPY public.owner_privacy_settings (owner_id, hunt_run_retention_days, version, created_at, updated_at) FROM stdin;
\.


--
-- Data for Name: owner_sessions; Type: TABLE DATA; Schema: public; Owner: job_hunt
--

COPY public.owner_sessions (id, owner_id, token_hash, created_at, expires_at, last_seen_at, revoked_at) FROM stdin;
b6441adab5c14788820d4188aa40768d	owner	911e50eab59eccfad9dcf06e37310ea04e6d240f991062f5c9d228384a8b0331	2026-07-12 18:57:55.732656+00	2026-08-11 18:57:55.732003+00	2026-07-12 18:57:57.177255+00	2026-07-12 18:57:57.180392+00
4b0fbb86bdff4fcf94de9ff7b88d546b	owner	53b037673ec1ad7714cb9816b851ac23c361f001486a7418fa88d2cd1b13ab05	2026-07-12 18:18:03.416986+00	2026-08-11 18:18:03.4087+00	2026-07-12 18:18:53.68846+00	\N
3358a012936443d8b010ca3705d77120	owner	2ea4c5e3a5f206a98cebbcbba4a73f4fb77a4649401ceaec722c7ae35530b9a2	2026-07-12 18:23:20.740117+00	2026-08-11 18:23:20.732415+00	2026-07-12 18:24:01.785393+00	2026-07-12 18:24:01.790654+00
a2dac4125d4642b38bd58a1fae85719b	owner	d51024a4d21dac39990ec9abe34e684d72bf7988ee63b122566bb47609a4d22b	2026-07-19 19:42:57.484165+00	2026-08-18 19:42:57.482871+00	2026-07-19 19:43:39.982711+00	\N
28a2b36fe92147cdba62914b6ea816f4	owner	f136b399805a9ef225b7e7cc746011ad4043e3f74fe559d784f495dac5beeda9	2026-07-19 19:43:54.350639+00	2026-08-18 19:43:54.349193+00	2026-07-19 19:43:54.383492+00	\N
cdd18957005c419b8434add69e4c358b	owner	f81c5e11fbd79efed3d12398ad638f022a6132f0aceb01b7c4daf4493b897a95	2026-07-12 18:57:18.827588+00	2026-08-11 18:57:18.826379+00	2026-07-12 18:57:18.863361+00	\N
70e7d0edca4243edb271bddc7ad12ada	owner	7bf5bd26c16f8de32bbbd34bd2366137bd69f0c7e2cb6c0e1ac72354ac776b46	2026-07-12 19:53:08.406681+00	2026-08-11 19:53:08.405766+00	2026-07-12 20:01:28.504525+00	\N
3c5f2b1270c54dc1846b813751e46860	owner	dfc02ae666e2946432d0579436eb9a2071db10c41ab87cea36758060d17e13e2	2026-07-19 19:34:03.293027+00	2026-08-18 19:34:03.290732+00	2026-07-19 19:34:03.290732+00	\N
5676e725b88346b6bf51be90e28c891e	owner	654dd9223597b59ea2719ca6be0f97d517844c6d1778aea06e3c65de63d0c839	2026-07-19 19:42:32.236588+00	2026-08-18 19:42:32.235479+00	2026-07-19 19:42:32.235479+00	\N
\.


--
-- Data for Name: owners; Type: TABLE DATA; Schema: public; Owner: job_hunt
--

COPY public.owners (id, display_name, timezone, created_at, updated_at) FROM stdin;
owner	Owner	UTC	2026-07-12 18:18:03.416986+00	2026-07-12 18:18:03.416986+00
\.


--
-- Data for Name: privacy_deletion_receipts; Type: TABLE DATA; Schema: public; Owner: job_hunt
--

COPY public.privacy_deletion_receipts (id, owner_id_hash, idempotency_key_hash, request_hash, deleted_at) FROM stdin;
\.


--
-- Data for Name: resume_versions; Type: TABLE DATA; Schema: public; Owner: job_hunt
--

COPY public.resume_versions (id, owner_id, parent_id, label, encrypted_content, encryption_key_id, content_hash, source, is_base, version, created_at, updated_at) FROM stdin;
f95aa789a18542549ba927e1d9fb69d1	owner	\N	Platform resume · Browser QA	gAAAAABqU_DNHFpCgI5kKEx9rLQt_sIReRoUFB1uvDWQT5qaLoV3CCvoHJf5ywnwSg_JhaKQhyOWSF8VcBCCN3ujAajALWyJ_kodEVJKB4gOv6i468Gd9P9h95HS-rNtzxDYhQPgCsb0gq2s28FIBgyE6NDeDmFN3S3k0o-W62hGezhCJwyzUR8WEawJN89i8JGsMG6GJH6XUpuCJNAfKanUVeW_jBU5pmpoUo-TBl11s6kkMaJQkBQ4AOTWg3MX59XyJbe4nygcQTF6W6K9rtv2CM3gbAL6kuDifSA5BdvOTi6rsLQNIlq0k7p6hIXZuw2-Mir3QmuyIlQyWVceQqYXi90-aZOV3PY2geSLbxCOVFpAmDPPujgQ6vqkT-YbD-6jPRv5zgZHozz54rFMz7CcTRbhZX6-5A60IO5euo3yqnsK0ufGbr4-dUlT6mcRFbKwmCrccVlN03XWEVroDAgAuY-K6rxiig==	local-dev	bf5db9e2892eeb7235eaad7d1e2bed57ce32fd03eaa061e71b2a5ccfc3070471	pasted	t	1	2026-07-12 19:53:49.229854+00	2026-07-12 19:53:49.229854+00
\.


--
-- Data for Name: saved_search_matches; Type: TABLE DATA; Schema: public; Owner: job_hunt
--

COPY public.saved_search_matches (id, owner_id, saved_search_id, job_posting_id, first_scan_id, last_scan_id, last_posting_version_id, match_count, first_matched_at, last_matched_at, created_at, updated_at) FROM stdin;
\.


--
-- Data for Name: saved_searches; Type: TABLE DATA; Schema: public; Owner: job_hunt
--

COPY public.saved_searches (id, owner_id, career_track_id, resume_version_id, name, criteria_schema_version, criteria, pack, use_self_rag, cadence, schedule, timezone, active, last_scan_at, next_scan_at, version, created_at, updated_at) FROM stdin;
fceb13754cb64ec49560afc261feec2a	owner	fb46aa14b1d44554ba66e73dc12eba40	f95aa789a18542549ba927e1d9fb69d1	Senior platform roles · India	1	{"role_keywords": ["backend engineer", "platform engineer", "distributed systems"], "seniority": "senior", "location": ["Remote-India", "Bengaluru"], "comp_min_lpa": 35, "comp_max_lpa": 65, "employment_types": ["full_time"], "max_age_days": 45, "country": "in"}	backend_india	t	manual	{"local_time": null, "days_of_week": []}	Asia/Calcutta	t	\N	\N	2	2026-07-12 19:58:49.659147+00	2026-07-12 20:01:28.438322+00
\.


--
-- Data for Name: worker_heartbeats; Type: TABLE DATA; Schema: public; Owner: job_hunt
--

COPY public.worker_heartbeats (worker_id, supported_kinds, current_job_id, build_version, started_at, last_seen_at) FROM stdin;
0c457a159623-1	["legacy_hunt"]	\N	\N	2026-07-12 18:56:16.773244+00	2026-07-12 18:58:38.426185+00
34ba52499d92-1	["legacy_hunt"]	\N	\N	2026-07-12 19:58:28.661095+00	2026-07-12 21:12:21.85538+00
d7295e9b50ce-1	["legacy_hunt"]	\N	\N	2026-07-12 19:56:12.376511+00	2026-07-12 19:58:25.380799+00
abcaa3f71c7b-1	["discover_contacts", "legacy_hunt", "scan_saved_search"]	\N	\N	2026-07-19 19:32:51.11998+00	2026-07-19 19:33:49.293591+00
267683760b8b-1	["legacy_hunt"]	\N	\N	2026-07-12 19:51:38.687127+00	2026-07-12 19:52:21.164443+00
7a767ef104da-1	["discover_contacts", "legacy_hunt", "scan_saved_search"]	\N	\N	2026-07-19 19:33:54.701984+00	2026-07-19 19:45:41.283314+00
969a60b7c860-1	["legacy_hunt"]	\N	\N	2026-07-12 18:22:50.764024+00	2026-07-12 18:24:37.743436+00
aff66011c2c3-1	["legacy_hunt"]	\N	\N	2026-07-12 18:16:26.022119+00	2026-07-12 18:22:46.904111+00
13597a985047-1	["legacy_hunt"]	\N	\N	2026-07-11 17:45:22.966979+00	2026-07-11 21:09:22.848096+00
4a56ec6fb30e-1	["legacy_hunt"]	\N	\N	2026-07-12 19:52:25.361304+00	2026-07-12 19:56:08.905511+00
\.


--
-- Name: background_job_events_id_seq; Type: SEQUENCE SET; Schema: public; Owner: job_hunt
--

SELECT pg_catalog.setval('public.background_job_events_id_seq', 83, true);


--
-- Name: hunt_outcomes_id_seq; Type: SEQUENCE SET; Schema: public; Owner: job_hunt
--

SELECT pg_catalog.setval('public.hunt_outcomes_id_seq', 1, true);


--
-- Name: alembic_version alembic_version_pkc; Type: CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.alembic_version
    ADD CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num);


--
-- Name: achievement_evidence pk_achievement_evidence; Type: CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.achievement_evidence
    ADD CONSTRAINT pk_achievement_evidence PRIMARY KEY (id);


--
-- Name: action_items pk_action_items; Type: CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.action_items
    ADD CONSTRAINT pk_action_items PRIMARY KEY (id);


--
-- Name: application_action_reviews pk_application_action_reviews; Type: CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.application_action_reviews
    ADD CONSTRAINT pk_application_action_reviews PRIMARY KEY (id);


--
-- Name: application_activity_events pk_application_activity_events; Type: CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.application_activity_events
    ADD CONSTRAINT pk_application_activity_events PRIMARY KEY (id);


--
-- Name: application_artifact_events pk_application_artifact_events; Type: CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.application_artifact_events
    ADD CONSTRAINT pk_application_artifact_events PRIMARY KEY (id);


--
-- Name: application_artifact_revisions pk_application_artifact_revisions; Type: CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.application_artifact_revisions
    ADD CONSTRAINT pk_application_artifact_revisions PRIMARY KEY (id);


--
-- Name: application_contacts pk_application_contacts; Type: CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.application_contacts
    ADD CONSTRAINT pk_application_contacts PRIMARY KEY (id);


--
-- Name: application_interview_preparation_revisions pk_application_interview_preparation_revisions; Type: CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.application_interview_preparation_revisions
    ADD CONSTRAINT pk_application_interview_preparation_revisions PRIMARY KEY (id);


--
-- Name: application_interview_preparations pk_application_interview_preparations; Type: CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.application_interview_preparations
    ADD CONSTRAINT pk_application_interview_preparations PRIMARY KEY (id);


--
-- Name: application_interview_round_events pk_application_interview_round_events; Type: CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.application_interview_round_events
    ADD CONSTRAINT pk_application_interview_round_events PRIMARY KEY (id);


--
-- Name: application_interview_rounds pk_application_interview_rounds; Type: CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.application_interview_rounds
    ADD CONSTRAINT pk_application_interview_rounds PRIMARY KEY (id);


--
-- Name: application_metric_snapshots pk_application_metric_snapshots; Type: CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.application_metric_snapshots
    ADD CONSTRAINT pk_application_metric_snapshots PRIMARY KEY (id);


--
-- Name: application_milestone_corrections pk_application_milestone_corrections; Type: CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.application_milestone_corrections
    ADD CONSTRAINT pk_application_milestone_corrections PRIMARY KEY (id);


--
-- Name: application_outcomes pk_application_outcomes; Type: CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.application_outcomes
    ADD CONSTRAINT pk_application_outcomes PRIMARY KEY (id);


--
-- Name: application_pack_events pk_application_pack_events; Type: CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.application_pack_events
    ADD CONSTRAINT pk_application_pack_events PRIMARY KEY (id);


--
-- Name: application_pack_revisions pk_application_pack_revisions; Type: CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.application_pack_revisions
    ADD CONSTRAINT pk_application_pack_revisions PRIMARY KEY (id);


--
-- Name: application_packs pk_application_packs; Type: CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.application_packs
    ADD CONSTRAINT pk_application_packs PRIMARY KEY (id);


--
-- Name: application_submissions pk_application_submissions; Type: CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.application_submissions
    ADD CONSTRAINT pk_application_submissions PRIMARY KEY (id);


--
-- Name: applications pk_applications; Type: CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.applications
    ADD CONSTRAINT pk_applications PRIMARY KEY (id);


--
-- Name: background_job_events pk_background_job_events; Type: CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.background_job_events
    ADD CONSTRAINT pk_background_job_events PRIMARY KEY (id);


--
-- Name: background_jobs pk_background_jobs; Type: CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.background_jobs
    ADD CONSTRAINT pk_background_jobs PRIMARY KEY (id);


--
-- Name: candidate_profiles pk_candidate_profiles; Type: CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.candidate_profiles
    ADD CONSTRAINT pk_candidate_profiles PRIMARY KEY (id);


--
-- Name: career_tracks pk_career_tracks; Type: CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.career_tracks
    ADD CONSTRAINT pk_career_tracks PRIMARY KEY (id);


--
-- Name: contact_plans pk_contact_plans; Type: CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.contact_plans
    ADD CONSTRAINT pk_contact_plans PRIMARY KEY (id);


--
-- Name: contacts pk_contacts; Type: CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.contacts
    ADD CONSTRAINT pk_contacts PRIMARY KEY (id);


--
-- Name: hunt_outcomes pk_hunt_outcomes; Type: CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.hunt_outcomes
    ADD CONSTRAINT pk_hunt_outcomes PRIMARY KEY (id);


--
-- Name: hunt_runs pk_hunt_runs; Type: CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.hunt_runs
    ADD CONSTRAINT pk_hunt_runs PRIMARY KEY (id);


--
-- Name: job_observations pk_job_observations; Type: CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.job_observations
    ADD CONSTRAINT pk_job_observations PRIMARY KEY (id);


--
-- Name: job_posting_aliases pk_job_posting_aliases; Type: CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.job_posting_aliases
    ADD CONSTRAINT pk_job_posting_aliases PRIMARY KEY (id);


--
-- Name: job_posting_versions pk_job_posting_versions; Type: CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.job_posting_versions
    ADD CONSTRAINT pk_job_posting_versions PRIMARY KEY (id);


--
-- Name: job_postings pk_job_postings; Type: CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.job_postings
    ADD CONSTRAINT pk_job_postings PRIMARY KEY (id);


--
-- Name: opportunity_decision_events pk_opportunity_decision_events; Type: CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.opportunity_decision_events
    ADD CONSTRAINT pk_opportunity_decision_events PRIMARY KEY (id);


--
-- Name: opportunity_scan_sources pk_opportunity_scan_sources; Type: CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.opportunity_scan_sources
    ADD CONSTRAINT pk_opportunity_scan_sources PRIMARY KEY (id);


--
-- Name: opportunity_scans pk_opportunity_scans; Type: CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.opportunity_scans
    ADD CONSTRAINT pk_opportunity_scans PRIMARY KEY (id);


--
-- Name: outreach_events pk_outreach_events; Type: CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.outreach_events
    ADD CONSTRAINT pk_outreach_events PRIMARY KEY (id);


--
-- Name: outreach_message_versions pk_outreach_message_versions; Type: CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.outreach_message_versions
    ADD CONSTRAINT pk_outreach_message_versions PRIMARY KEY (id);


--
-- Name: outreach_replies pk_outreach_replies; Type: CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.outreach_replies
    ADD CONSTRAINT pk_outreach_replies PRIMARY KEY (id);


--
-- Name: outreach_sequences pk_outreach_sequences; Type: CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.outreach_sequences
    ADD CONSTRAINT pk_outreach_sequences PRIMARY KEY (id);


--
-- Name: owner_mutation_receipts pk_owner_mutation_receipts; Type: CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.owner_mutation_receipts
    ADD CONSTRAINT pk_owner_mutation_receipts PRIMARY KEY (id);


--
-- Name: owner_opportunities pk_owner_opportunities; Type: CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.owner_opportunities
    ADD CONSTRAINT pk_owner_opportunities PRIMARY KEY (id);


--
-- Name: owner_privacy_settings pk_owner_privacy_settings; Type: CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.owner_privacy_settings
    ADD CONSTRAINT pk_owner_privacy_settings PRIMARY KEY (owner_id);


--
-- Name: owner_sessions pk_owner_sessions; Type: CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.owner_sessions
    ADD CONSTRAINT pk_owner_sessions PRIMARY KEY (id);


--
-- Name: owners pk_owners; Type: CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.owners
    ADD CONSTRAINT pk_owners PRIMARY KEY (id);


--
-- Name: privacy_deletion_receipts pk_privacy_deletion_receipts; Type: CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.privacy_deletion_receipts
    ADD CONSTRAINT pk_privacy_deletion_receipts PRIMARY KEY (id);


--
-- Name: resume_versions pk_resume_versions; Type: CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.resume_versions
    ADD CONSTRAINT pk_resume_versions PRIMARY KEY (id);


--
-- Name: saved_search_matches pk_saved_search_matches; Type: CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.saved_search_matches
    ADD CONSTRAINT pk_saved_search_matches PRIMARY KEY (id);


--
-- Name: saved_searches pk_saved_searches; Type: CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.saved_searches
    ADD CONSTRAINT pk_saved_searches PRIMARY KEY (id);


--
-- Name: worker_heartbeats pk_worker_heartbeats; Type: CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.worker_heartbeats
    ADD CONSTRAINT pk_worker_heartbeats PRIMARY KEY (worker_id);


--
-- Name: achievement_evidence uq_achievement_evidence_owner_id_id; Type: CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.achievement_evidence
    ADD CONSTRAINT uq_achievement_evidence_owner_id_id UNIQUE (owner_id, id);


--
-- Name: action_items uq_action_items_owner_application_id; Type: CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.action_items
    ADD CONSTRAINT uq_action_items_owner_application_id UNIQUE (owner_id, application_id, id);


--
-- Name: action_items uq_action_items_owner_id_id; Type: CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.action_items
    ADD CONSTRAINT uq_action_items_owner_id_id UNIQUE (owner_id, id);


--
-- Name: application_action_reviews uq_application_action_reviews_owner_application_mutation; Type: CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.application_action_reviews
    ADD CONSTRAINT uq_application_action_reviews_owner_application_mutation UNIQUE (owner_id, application_id, idempotency_key_hash);


--
-- Name: application_action_reviews uq_application_action_reviews_owner_id_id; Type: CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.application_action_reviews
    ADD CONSTRAINT uq_application_action_reviews_owner_id_id UNIQUE (owner_id, id);


--
-- Name: application_activity_events uq_application_activity_events_owner_application_id; Type: CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.application_activity_events
    ADD CONSTRAINT uq_application_activity_events_owner_application_id UNIQUE (owner_id, application_id, id);


--
-- Name: application_activity_events uq_application_activity_events_owner_id_id; Type: CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.application_activity_events
    ADD CONSTRAINT uq_application_activity_events_owner_id_id UNIQUE (owner_id, id);


--
-- Name: application_activity_events uq_application_activity_events_owner_sequence; Type: CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.application_activity_events
    ADD CONSTRAINT uq_application_activity_events_owner_sequence UNIQUE (owner_id, application_id, sequence_number);


--
-- Name: application_artifact_events uq_application_artifact_events_owner_id_id; Type: CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.application_artifact_events
    ADD CONSTRAINT uq_application_artifact_events_owner_id_id UNIQUE (owner_id, id);


--
-- Name: application_artifact_events uq_application_artifact_events_owner_mutation; Type: CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.application_artifact_events
    ADD CONSTRAINT uq_application_artifact_events_owner_mutation UNIQUE (owner_id, application_pack_id, idempotency_key_hash);


--
-- Name: application_artifact_events uq_application_artifact_events_owner_sequence; Type: CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.application_artifact_events
    ADD CONSTRAINT uq_application_artifact_events_owner_sequence UNIQUE (owner_id, application_pack_id, sequence_number);


--
-- Name: application_artifact_events uq_application_artifact_events_owner_terminal; Type: CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.application_artifact_events
    ADD CONSTRAINT uq_application_artifact_events_owner_terminal UNIQUE (owner_id, application_pack_id, artifact_revision_id);


--
-- Name: application_artifact_events uq_application_artifact_events_submission_ref; Type: CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.application_artifact_events
    ADD CONSTRAINT uq_application_artifact_events_submission_ref UNIQUE (owner_id, application_id, application_pack_id, artifact_revision_id, id);


--
-- Name: application_artifact_revisions uq_application_artifact_revisions_event_ref; Type: CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.application_artifact_revisions
    ADD CONSTRAINT uq_application_artifact_revisions_event_ref UNIQUE (owner_id, application_id, application_pack_id, id);


--
-- Name: application_artifact_revisions uq_application_artifact_revisions_owner_id_id; Type: CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.application_artifact_revisions
    ADD CONSTRAINT uq_application_artifact_revisions_owner_id_id UNIQUE (owner_id, id);


--
-- Name: application_artifact_revisions uq_application_artifact_revisions_owner_number; Type: CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.application_artifact_revisions
    ADD CONSTRAINT uq_application_artifact_revisions_owner_number UNIQUE (owner_id, application_pack_id, revision_number);


--
-- Name: application_contacts uq_application_contacts_owner_application_id; Type: CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.application_contacts
    ADD CONSTRAINT uq_application_contacts_owner_application_id UNIQUE (owner_id, application_id, id);


--
-- Name: application_contacts uq_application_contacts_owner_id_id; Type: CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.application_contacts
    ADD CONSTRAINT uq_application_contacts_owner_id_id UNIQUE (owner_id, id);


--
-- Name: application_contacts uq_application_contacts_owner_plan_contact; Type: CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.application_contacts
    ADD CONSTRAINT uq_application_contacts_owner_plan_contact UNIQUE (owner_id, contact_plan_id, contact_id);


--
-- Name: application_contacts uq_application_contacts_owner_plan_pool_rank; Type: CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.application_contacts
    ADD CONSTRAINT uq_application_contacts_owner_plan_pool_rank UNIQUE (owner_id, contact_plan_id, pool_rank);


--
-- Name: application_interview_round_events uq_application_interview_round_events_owner_id_id; Type: CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.application_interview_round_events
    ADD CONSTRAINT uq_application_interview_round_events_owner_id_id UNIQUE (owner_id, id);


--
-- Name: application_interview_round_events uq_application_interview_round_events_owner_mutation; Type: CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.application_interview_round_events
    ADD CONSTRAINT uq_application_interview_round_events_owner_mutation UNIQUE (owner_id, application_id, idempotency_key_hash);


--
-- Name: application_interview_round_events uq_application_interview_round_events_owner_sequence; Type: CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.application_interview_round_events
    ADD CONSTRAINT uq_application_interview_round_events_owner_sequence UNIQUE (owner_id, application_id, interview_round_id, sequence_number);


--
-- Name: application_interview_rounds uq_application_interview_rounds_owner_application_id; Type: CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.application_interview_rounds
    ADD CONSTRAINT uq_application_interview_rounds_owner_application_id UNIQUE (owner_id, application_id, id);


--
-- Name: application_interview_rounds uq_application_interview_rounds_owner_id_id; Type: CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.application_interview_rounds
    ADD CONSTRAINT uq_application_interview_rounds_owner_id_id UNIQUE (owner_id, id);


--
-- Name: application_interview_rounds uq_application_interview_rounds_owner_number; Type: CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.application_interview_rounds
    ADD CONSTRAINT uq_application_interview_rounds_owner_number UNIQUE (owner_id, application_id, round_number);


--
-- Name: application_metric_snapshots uq_application_metric_snapshots_owner_application; Type: CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.application_metric_snapshots
    ADD CONSTRAINT uq_application_metric_snapshots_owner_application UNIQUE (owner_id, application_id);


--
-- Name: application_metric_snapshots uq_application_metric_snapshots_owner_id_id; Type: CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.application_metric_snapshots
    ADD CONSTRAINT uq_application_metric_snapshots_owner_id_id UNIQUE (owner_id, id);


--
-- Name: application_milestone_corrections uq_application_milestone_corrections_owner_event_id; Type: CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.application_milestone_corrections
    ADD CONSTRAINT uq_application_milestone_corrections_owner_event_id UNIQUE (owner_id, application_id, activity_event_id, id);


--
-- Name: application_milestone_corrections uq_application_milestone_corrections_owner_id_id; Type: CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.application_milestone_corrections
    ADD CONSTRAINT uq_application_milestone_corrections_owner_id_id UNIQUE (owner_id, id);


--
-- Name: application_milestone_corrections uq_application_milestone_corrections_owner_number; Type: CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.application_milestone_corrections
    ADD CONSTRAINT uq_application_milestone_corrections_owner_number UNIQUE (owner_id, application_id, activity_event_id, correction_number);


--
-- Name: application_outcomes uq_application_outcomes_owner_application; Type: CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.application_outcomes
    ADD CONSTRAINT uq_application_outcomes_owner_application UNIQUE (owner_id, application_id);


--
-- Name: application_outcomes uq_application_outcomes_owner_application_id; Type: CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.application_outcomes
    ADD CONSTRAINT uq_application_outcomes_owner_application_id UNIQUE (owner_id, application_id, id);


--
-- Name: application_outcomes uq_application_outcomes_owner_id_id; Type: CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.application_outcomes
    ADD CONSTRAINT uq_application_outcomes_owner_id_id UNIQUE (owner_id, id);


--
-- Name: application_pack_events uq_application_pack_events_owner_id_id; Type: CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.application_pack_events
    ADD CONSTRAINT uq_application_pack_events_owner_id_id UNIQUE (owner_id, id);


--
-- Name: application_pack_events uq_application_pack_events_owner_mutation; Type: CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.application_pack_events
    ADD CONSTRAINT uq_application_pack_events_owner_mutation UNIQUE (owner_id, application_pack_id, idempotency_key_hash);


--
-- Name: application_pack_events uq_application_pack_events_owner_reviewed; Type: CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.application_pack_events
    ADD CONSTRAINT uq_application_pack_events_owner_reviewed UNIQUE (owner_id, application_pack_id, revision_id, event_type);


--
-- Name: application_pack_events uq_application_pack_events_owner_sequence; Type: CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.application_pack_events
    ADD CONSTRAINT uq_application_pack_events_owner_sequence UNIQUE (owner_id, application_pack_id, sequence_number);


--
-- Name: application_pack_events uq_application_pack_events_submission_ref; Type: CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.application_pack_events
    ADD CONSTRAINT uq_application_pack_events_submission_ref UNIQUE (owner_id, application_id, application_pack_id, revision_id, id);


--
-- Name: application_pack_revisions uq_application_pack_revisions_event_ref; Type: CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.application_pack_revisions
    ADD CONSTRAINT uq_application_pack_revisions_event_ref UNIQUE (owner_id, application_id, application_pack_id, id);


--
-- Name: application_pack_revisions uq_application_pack_revisions_owner_id_id; Type: CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.application_pack_revisions
    ADD CONSTRAINT uq_application_pack_revisions_owner_id_id UNIQUE (owner_id, id);


--
-- Name: application_pack_revisions uq_application_pack_revisions_owner_number; Type: CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.application_pack_revisions
    ADD CONSTRAINT uq_application_pack_revisions_owner_number UNIQUE (owner_id, application_pack_id, revision_number);


--
-- Name: application_packs uq_application_packs_owner_application; Type: CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.application_packs
    ADD CONSTRAINT uq_application_packs_owner_application UNIQUE (owner_id, application_id);


--
-- Name: application_packs uq_application_packs_owner_application_id; Type: CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.application_packs
    ADD CONSTRAINT uq_application_packs_owner_application_id UNIQUE (owner_id, application_id, id);


--
-- Name: application_packs uq_application_packs_owner_id_id; Type: CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.application_packs
    ADD CONSTRAINT uq_application_packs_owner_id_id UNIQUE (owner_id, id);


--
-- Name: application_submissions uq_application_submissions_owner_application; Type: CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.application_submissions
    ADD CONSTRAINT uq_application_submissions_owner_application UNIQUE (owner_id, application_id);


--
-- Name: application_submissions uq_application_submissions_owner_application_id; Type: CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.application_submissions
    ADD CONSTRAINT uq_application_submissions_owner_application_id UNIQUE (owner_id, application_id, id);


--
-- Name: application_submissions uq_application_submissions_owner_id_id; Type: CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.application_submissions
    ADD CONSTRAINT uq_application_submissions_owner_id_id UNIQUE (owner_id, id);


--
-- Name: applications uq_applications_owner_id_id; Type: CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.applications
    ADD CONSTRAINT uq_applications_owner_id_id UNIQUE (owner_id, id);


--
-- Name: applications uq_applications_owner_opportunity; Type: CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.applications
    ADD CONSTRAINT uq_applications_owner_opportunity UNIQUE (owner_id, owner_opportunity_id);


--
-- Name: background_jobs uq_background_jobs_scope_kind_dedupe; Type: CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.background_jobs
    ADD CONSTRAINT uq_background_jobs_scope_kind_dedupe UNIQUE (dedupe_scope, kind, dedupe_key);


--
-- Name: candidate_profiles uq_candidate_profiles_owner_id; Type: CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.candidate_profiles
    ADD CONSTRAINT uq_candidate_profiles_owner_id UNIQUE (owner_id);


--
-- Name: career_tracks uq_career_tracks_owner_id_id; Type: CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.career_tracks
    ADD CONSTRAINT uq_career_tracks_owner_id_id UNIQUE (owner_id, id);


--
-- Name: career_tracks uq_career_tracks_owner_name; Type: CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.career_tracks
    ADD CONSTRAINT uq_career_tracks_owner_name UNIQUE (owner_id, name);


--
-- Name: contact_plans uq_contact_plans_background_job_id; Type: CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.contact_plans
    ADD CONSTRAINT uq_contact_plans_background_job_id UNIQUE (background_job_id);


--
-- Name: contact_plans uq_contact_plans_owner_application_id; Type: CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.contact_plans
    ADD CONSTRAINT uq_contact_plans_owner_application_id UNIQUE (owner_id, application_id, id);


--
-- Name: contact_plans uq_contact_plans_owner_application_number; Type: CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.contact_plans
    ADD CONSTRAINT uq_contact_plans_owner_application_number UNIQUE (owner_id, application_id, plan_number);


--
-- Name: contact_plans uq_contact_plans_owner_id_id; Type: CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.contact_plans
    ADD CONSTRAINT uq_contact_plans_owner_id_id UNIQUE (owner_id, id);


--
-- Name: contacts uq_contacts_owner_id_id; Type: CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.contacts
    ADD CONSTRAINT uq_contacts_owner_id_id UNIQUE (owner_id, id);


--
-- Name: contacts uq_contacts_owner_identity_hash; Type: CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.contacts
    ADD CONSTRAINT uq_contacts_owner_identity_hash UNIQUE (owner_id, identity_key_hash);


--
-- Name: contacts uq_contacts_owner_normalized_profile_url; Type: CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.contacts
    ADD CONSTRAINT uq_contacts_owner_normalized_profile_url UNIQUE (owner_id, normalized_profile_url);


--
-- Name: hunt_runs uq_hunt_runs_background_job_id; Type: CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.hunt_runs
    ADD CONSTRAINT uq_hunt_runs_background_job_id UNIQUE (background_job_id);


--
-- Name: hunt_runs uq_hunt_runs_owner_idempotency_key_hash; Type: CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.hunt_runs
    ADD CONSTRAINT uq_hunt_runs_owner_idempotency_key_hash UNIQUE (owner_id, idempotency_key_hash);


--
-- Name: application_interview_preparation_revisions uq_interview_prep_revisions_event_ref; Type: CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.application_interview_preparation_revisions
    ADD CONSTRAINT uq_interview_prep_revisions_event_ref UNIQUE (owner_id, application_id, preparation_id, id);


--
-- Name: application_interview_preparation_revisions uq_interview_prep_revisions_owner_id; Type: CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.application_interview_preparation_revisions
    ADD CONSTRAINT uq_interview_prep_revisions_owner_id UNIQUE (owner_id, id);


--
-- Name: application_interview_preparation_revisions uq_interview_prep_revisions_owner_number; Type: CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.application_interview_preparation_revisions
    ADD CONSTRAINT uq_interview_prep_revisions_owner_number UNIQUE (owner_id, preparation_id, revision_number);


--
-- Name: application_interview_preparations uq_interview_preps_owner_application; Type: CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.application_interview_preparations
    ADD CONSTRAINT uq_interview_preps_owner_application UNIQUE (owner_id, application_id);


--
-- Name: application_interview_preparations uq_interview_preps_owner_application_id; Type: CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.application_interview_preparations
    ADD CONSTRAINT uq_interview_preps_owner_application_id UNIQUE (owner_id, application_id, id);


--
-- Name: application_interview_preparations uq_interview_preps_owner_id; Type: CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.application_interview_preparations
    ADD CONSTRAINT uq_interview_preps_owner_id UNIQUE (owner_id, id);


--
-- Name: job_observations uq_job_observations_owner_id_id; Type: CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.job_observations
    ADD CONSTRAINT uq_job_observations_owner_id_id UNIQUE (owner_id, id);


--
-- Name: job_observations uq_job_observations_source_posting; Type: CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.job_observations
    ADD CONSTRAINT uq_job_observations_source_posting UNIQUE (owner_id, opportunity_scan_source_id, job_posting_id);


--
-- Name: job_posting_aliases uq_job_posting_aliases_owner_hash; Type: CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.job_posting_aliases
    ADD CONSTRAINT uq_job_posting_aliases_owner_hash UNIQUE (owner_id, alias_key_hash);


--
-- Name: job_posting_aliases uq_job_posting_aliases_owner_posting_id; Type: CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.job_posting_aliases
    ADD CONSTRAINT uq_job_posting_aliases_owner_posting_id UNIQUE (owner_id, job_posting_id, id);


--
-- Name: job_posting_versions uq_job_posting_versions_owner_number; Type: CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.job_posting_versions
    ADD CONSTRAINT uq_job_posting_versions_owner_number UNIQUE (owner_id, job_posting_id, version_number);


--
-- Name: job_posting_versions uq_job_posting_versions_owner_posting_id; Type: CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.job_posting_versions
    ADD CONSTRAINT uq_job_posting_versions_owner_posting_id UNIQUE (owner_id, job_posting_id, id);


--
-- Name: job_postings uq_job_postings_owner_id_id; Type: CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.job_postings
    ADD CONSTRAINT uq_job_postings_owner_id_id UNIQUE (owner_id, id);


--
-- Name: job_postings uq_job_postings_owner_identity_hash; Type: CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.job_postings
    ADD CONSTRAINT uq_job_postings_owner_identity_hash UNIQUE (owner_id, identity_key_hash);


--
-- Name: opportunity_decision_events uq_opportunity_decision_events_idempotency; Type: CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.opportunity_decision_events
    ADD CONSTRAINT uq_opportunity_decision_events_idempotency UNIQUE (owner_id, owner_opportunity_id, idempotency_key_hash);


--
-- Name: opportunity_decision_events uq_opportunity_decision_events_owner_id_id; Type: CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.opportunity_decision_events
    ADD CONSTRAINT uq_opportunity_decision_events_owner_id_id UNIQUE (owner_id, id);


--
-- Name: opportunity_decision_events uq_opportunity_decision_events_owner_opportunity_id; Type: CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.opportunity_decision_events
    ADD CONSTRAINT uq_opportunity_decision_events_owner_opportunity_id UNIQUE (owner_id, owner_opportunity_id, id);


--
-- Name: opportunity_scan_sources uq_opportunity_scan_sources_owner_id_id; Type: CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.opportunity_scan_sources
    ADD CONSTRAINT uq_opportunity_scan_sources_owner_id_id UNIQUE (owner_id, id);


--
-- Name: opportunity_scan_sources uq_opportunity_scan_sources_owner_scan_id; Type: CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.opportunity_scan_sources
    ADD CONSTRAINT uq_opportunity_scan_sources_owner_scan_id UNIQUE (owner_id, opportunity_scan_id, id);


--
-- Name: opportunity_scan_sources uq_opportunity_scan_sources_partition; Type: CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.opportunity_scan_sources
    ADD CONSTRAINT uq_opportunity_scan_sources_partition UNIQUE (owner_id, opportunity_scan_id, company_slug, source);


--
-- Name: opportunity_scans uq_opportunity_scans_background_job_id; Type: CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.opportunity_scans
    ADD CONSTRAINT uq_opportunity_scans_background_job_id UNIQUE (background_job_id);


--
-- Name: opportunity_scans uq_opportunity_scans_owner_id_id; Type: CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.opportunity_scans
    ADD CONSTRAINT uq_opportunity_scans_owner_id_id UNIQUE (owner_id, id);


--
-- Name: opportunity_scans uq_opportunity_scans_owner_idempotency; Type: CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.opportunity_scans
    ADD CONSTRAINT uq_opportunity_scans_owner_idempotency UNIQUE (owner_id, idempotency_key_hash);


--
-- Name: opportunity_scans uq_opportunity_scans_owner_search_dedupe; Type: CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.opportunity_scans
    ADD CONSTRAINT uq_opportunity_scans_owner_search_dedupe UNIQUE (owner_id, saved_search_id, dedupe_key);


--
-- Name: opportunity_scans uq_opportunity_scans_owner_search_id; Type: CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.opportunity_scans
    ADD CONSTRAINT uq_opportunity_scans_owner_search_id UNIQUE (owner_id, saved_search_id, id);


--
-- Name: outreach_events uq_outreach_events_owner_id_id; Type: CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.outreach_events
    ADD CONSTRAINT uq_outreach_events_owner_id_id UNIQUE (owner_id, id);


--
-- Name: outreach_events uq_outreach_events_owner_sequence_mutation; Type: CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.outreach_events
    ADD CONSTRAINT uq_outreach_events_owner_sequence_mutation UNIQUE (owner_id, outreach_sequence_id, idempotency_key_hash);


--
-- Name: outreach_events uq_outreach_events_owner_sequence_number; Type: CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.outreach_events
    ADD CONSTRAINT uq_outreach_events_owner_sequence_number UNIQUE (owner_id, outreach_sequence_id, sequence_number);


--
-- Name: outreach_message_versions uq_outreach_message_versions_event_ref; Type: CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.outreach_message_versions
    ADD CONSTRAINT uq_outreach_message_versions_event_ref UNIQUE (owner_id, application_id, outreach_sequence_id, application_contact_id, id, kind);


--
-- Name: outreach_message_versions uq_outreach_message_versions_owner_id_id; Type: CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.outreach_message_versions
    ADD CONSTRAINT uq_outreach_message_versions_owner_id_id UNIQUE (owner_id, id);


--
-- Name: outreach_message_versions uq_outreach_message_versions_revision; Type: CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.outreach_message_versions
    ADD CONSTRAINT uq_outreach_message_versions_revision UNIQUE (owner_id, outreach_sequence_id, application_contact_id, kind, version_number);


--
-- Name: outreach_replies uq_outreach_replies_owner_id_id; Type: CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.outreach_replies
    ADD CONSTRAINT uq_outreach_replies_owner_id_id UNIQUE (owner_id, id);


--
-- Name: outreach_replies uq_outreach_replies_owner_sequence_mutation; Type: CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.outreach_replies
    ADD CONSTRAINT uq_outreach_replies_owner_sequence_mutation UNIQUE (owner_id, outreach_sequence_id, idempotency_key_hash);


--
-- Name: outreach_sequences uq_outreach_sequences_owner_application; Type: CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.outreach_sequences
    ADD CONSTRAINT uq_outreach_sequences_owner_application UNIQUE (owner_id, application_id);


--
-- Name: outreach_sequences uq_outreach_sequences_owner_application_id; Type: CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.outreach_sequences
    ADD CONSTRAINT uq_outreach_sequences_owner_application_id UNIQUE (owner_id, application_id, id);


--
-- Name: outreach_sequences uq_outreach_sequences_owner_id_id; Type: CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.outreach_sequences
    ADD CONSTRAINT uq_outreach_sequences_owner_id_id UNIQUE (owner_id, id);


--
-- Name: owner_mutation_receipts uq_owner_mutation_receipts_owner_namespace_key; Type: CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.owner_mutation_receipts
    ADD CONSTRAINT uq_owner_mutation_receipts_owner_namespace_key UNIQUE (owner_id, namespace, idempotency_key_hash);


--
-- Name: owner_opportunities uq_owner_opportunities_owner_id_id; Type: CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.owner_opportunities
    ADD CONSTRAINT uq_owner_opportunities_owner_id_id UNIQUE (owner_id, id);


--
-- Name: owner_opportunities uq_owner_opportunities_owner_id_posting; Type: CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.owner_opportunities
    ADD CONSTRAINT uq_owner_opportunities_owner_id_posting UNIQUE (owner_id, id, job_posting_id);


--
-- Name: owner_opportunities uq_owner_opportunities_owner_posting; Type: CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.owner_opportunities
    ADD CONSTRAINT uq_owner_opportunities_owner_posting UNIQUE (owner_id, job_posting_id);


--
-- Name: owner_sessions uq_owner_sessions_token_hash; Type: CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.owner_sessions
    ADD CONSTRAINT uq_owner_sessions_token_hash UNIQUE (token_hash);


--
-- Name: privacy_deletion_receipts uq_privacy_deletion_receipts_owner_key; Type: CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.privacy_deletion_receipts
    ADD CONSTRAINT uq_privacy_deletion_receipts_owner_key UNIQUE (owner_id_hash, idempotency_key_hash);


--
-- Name: resume_versions uq_resume_versions_owner_content_hash; Type: CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.resume_versions
    ADD CONSTRAINT uq_resume_versions_owner_content_hash UNIQUE (owner_id, content_hash);


--
-- Name: resume_versions uq_resume_versions_owner_id_id; Type: CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.resume_versions
    ADD CONSTRAINT uq_resume_versions_owner_id_id UNIQUE (owner_id, id);


--
-- Name: saved_search_matches uq_saved_search_matches_owner_id_id; Type: CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.saved_search_matches
    ADD CONSTRAINT uq_saved_search_matches_owner_id_id UNIQUE (owner_id, id);


--
-- Name: saved_search_matches uq_saved_search_matches_owner_search_posting; Type: CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.saved_search_matches
    ADD CONSTRAINT uq_saved_search_matches_owner_search_posting UNIQUE (owner_id, saved_search_id, job_posting_id);


--
-- Name: saved_searches uq_saved_searches_owner_id_id; Type: CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.saved_searches
    ADD CONSTRAINT uq_saved_searches_owner_id_id UNIQUE (owner_id, id);


--
-- Name: saved_searches uq_saved_searches_owner_name; Type: CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.saved_searches
    ADD CONSTRAINT uq_saved_searches_owner_name UNIQUE (owner_id, name);


--
-- Name: ix_achievement_evidence_owner_state; Type: INDEX; Schema: public; Owner: job_hunt
--

CREATE INDEX ix_achievement_evidence_owner_state ON public.achievement_evidence USING btree (owner_id, approval_state);


--
-- Name: ix_action_items_owner_due; Type: INDEX; Schema: public; Owner: job_hunt
--

CREATE INDEX ix_action_items_owner_due ON public.action_items USING btree (owner_id, status, due_on);


--
-- Name: ix_application_action_reviews_timeline; Type: INDEX; Schema: public; Owner: job_hunt
--

CREATE INDEX ix_application_action_reviews_timeline ON public.application_action_reviews USING btree (owner_id, application_id, recorded_at, id);


--
-- Name: ix_application_activity_events_timeline; Type: INDEX; Schema: public; Owner: job_hunt
--

CREATE INDEX ix_application_activity_events_timeline ON public.application_activity_events USING btree (application_id, occurred_at, sequence_number);


--
-- Name: ix_application_artifact_events_timeline; Type: INDEX; Schema: public; Owner: job_hunt
--

CREATE INDEX ix_application_artifact_events_timeline ON public.application_artifact_events USING btree (owner_id, application_pack_id, occurred_at, sequence_number);


--
-- Name: ix_application_artifact_revisions_pack_created; Type: INDEX; Schema: public; Owner: job_hunt
--

CREATE INDEX ix_application_artifact_revisions_pack_created ON public.application_artifact_revisions USING btree (owner_id, application_pack_id, revision_number);


--
-- Name: ix_application_contacts_owner_application; Type: INDEX; Schema: public; Owner: job_hunt
--

CREATE INDEX ix_application_contacts_owner_application ON public.application_contacts USING btree (owner_id, application_id, bench_rank);


--
-- Name: ix_application_contacts_owner_contact; Type: INDEX; Schema: public; Owner: job_hunt
--

CREATE INDEX ix_application_contacts_owner_contact ON public.application_contacts USING btree (owner_id, contact_id, created_at);


--
-- Name: ix_application_interview_round_events_timeline; Type: INDEX; Schema: public; Owner: job_hunt
--

CREATE INDEX ix_application_interview_round_events_timeline ON public.application_interview_round_events USING btree (owner_id, application_id, interview_round_id, sequence_number);


--
-- Name: ix_application_interview_rounds_owner_schedule; Type: INDEX; Schema: public; Owner: job_hunt
--

CREATE INDEX ix_application_interview_rounds_owner_schedule ON public.application_interview_rounds USING btree (owner_id, status, scheduled_start_at);


--
-- Name: ix_application_metric_snapshots_owner_recorded; Type: INDEX; Schema: public; Owner: job_hunt
--

CREATE INDEX ix_application_metric_snapshots_owner_recorded ON public.application_metric_snapshots USING btree (owner_id, recorded_at, application_id);


--
-- Name: ix_application_milestone_corrections_timeline; Type: INDEX; Schema: public; Owner: job_hunt
--

CREATE INDEX ix_application_milestone_corrections_timeline ON public.application_milestone_corrections USING btree (owner_id, application_id, activity_event_id, correction_number);


--
-- Name: ix_application_outcomes_owner_metrics; Type: INDEX; Schema: public; Owner: job_hunt
--

CREATE INDEX ix_application_outcomes_owner_metrics ON public.application_outcomes USING btree (owner_id, outcome, outcome_on);


--
-- Name: ix_application_pack_events_timeline; Type: INDEX; Schema: public; Owner: job_hunt
--

CREATE INDEX ix_application_pack_events_timeline ON public.application_pack_events USING btree (owner_id, application_pack_id, occurred_at, sequence_number);


--
-- Name: ix_application_pack_revisions_pack_created; Type: INDEX; Schema: public; Owner: job_hunt
--

CREATE INDEX ix_application_pack_revisions_pack_created ON public.application_pack_revisions USING btree (owner_id, application_pack_id, revision_number);


--
-- Name: ix_application_packs_owner_updated; Type: INDEX; Schema: public; Owner: job_hunt
--

CREATE INDEX ix_application_packs_owner_updated ON public.application_packs USING btree (owner_id, updated_at);


--
-- Name: ix_application_submissions_owner_applied; Type: INDEX; Schema: public; Owner: job_hunt
--

CREATE INDEX ix_application_submissions_owner_applied ON public.application_submissions USING btree (owner_id, applied_on, recorded_at);


--
-- Name: ix_applications_owner_stage; Type: INDEX; Schema: public; Owner: job_hunt
--

CREATE INDEX ix_applications_owner_stage ON public.applications USING btree (owner_id, stage, updated_at);


--
-- Name: ix_background_job_events_job_created; Type: INDEX; Schema: public; Owner: job_hunt
--

CREATE INDEX ix_background_job_events_job_created ON public.background_job_events USING btree (job_id, created_at);


--
-- Name: ix_background_jobs_claim; Type: INDEX; Schema: public; Owner: job_hunt
--

CREATE INDEX ix_background_jobs_claim ON public.background_jobs USING btree (status, run_after, priority);


--
-- Name: ix_background_jobs_owner; Type: INDEX; Schema: public; Owner: job_hunt
--

CREATE INDEX ix_background_jobs_owner ON public.background_jobs USING btree (owner_id, created_at);


--
-- Name: ix_candidate_profiles_owner_updated; Type: INDEX; Schema: public; Owner: job_hunt
--

CREATE INDEX ix_candidate_profiles_owner_updated ON public.candidate_profiles USING btree (owner_id, updated_at);


--
-- Name: ix_career_tracks_owner_active; Type: INDEX; Schema: public; Owner: job_hunt
--

CREATE INDEX ix_career_tracks_owner_active ON public.career_tracks USING btree (owner_id, active);


--
-- Name: ix_contact_plans_owner_status; Type: INDEX; Schema: public; Owner: job_hunt
--

CREATE INDEX ix_contact_plans_owner_status ON public.contact_plans USING btree (owner_id, status, updated_at);


--
-- Name: ix_contacts_owner_lifecycle; Type: INDEX; Schema: public; Owner: job_hunt
--

CREATE INDEX ix_contacts_owner_lifecycle ON public.contacts USING btree (owner_id, lifecycle, updated_at);


--
-- Name: ix_hunt_outcomes_draft; Type: INDEX; Schema: public; Owner: job_hunt
--

CREATE INDEX ix_hunt_outcomes_draft ON public.hunt_outcomes USING btree (draft_id);


--
-- Name: ix_hunt_outcomes_run_logged; Type: INDEX; Schema: public; Owner: job_hunt
--

CREATE INDEX ix_hunt_outcomes_run_logged ON public.hunt_outcomes USING btree (hunt_run_id, logged_at);


--
-- Name: ix_hunt_runs_access_expiry; Type: INDEX; Schema: public; Owner: job_hunt
--

CREATE INDEX ix_hunt_runs_access_expiry ON public.hunt_runs USING btree (access_expires_at);


--
-- Name: ix_hunt_runs_owner_created; Type: INDEX; Schema: public; Owner: job_hunt
--

CREATE INDEX ix_hunt_runs_owner_created ON public.hunt_runs USING btree (owner_id, created_at);


--
-- Name: ix_hunt_runs_request_expiry; Type: INDEX; Schema: public; Owner: job_hunt
--

CREATE INDEX ix_hunt_runs_request_expiry ON public.hunt_runs USING btree (request_expires_at);


--
-- Name: ix_interview_prep_revisions_timeline; Type: INDEX; Schema: public; Owner: job_hunt
--

CREATE INDEX ix_interview_prep_revisions_timeline ON public.application_interview_preparation_revisions USING btree (owner_id, application_id, preparation_id, revision_number);


--
-- Name: ix_interview_preps_owner_updated; Type: INDEX; Schema: public; Owner: job_hunt
--

CREATE INDEX ix_interview_preps_owner_updated ON public.application_interview_preparations USING btree (owner_id, updated_at);


--
-- Name: ix_job_observations_posting; Type: INDEX; Schema: public; Owner: job_hunt
--

CREATE INDEX ix_job_observations_posting ON public.job_observations USING btree (job_posting_id, observed_at);


--
-- Name: ix_job_observations_scan; Type: INDEX; Schema: public; Owner: job_hunt
--

CREATE INDEX ix_job_observations_scan ON public.job_observations USING btree (opportunity_scan_id, observed_at);


--
-- Name: ix_job_posting_aliases_posting; Type: INDEX; Schema: public; Owner: job_hunt
--

CREATE INDEX ix_job_posting_aliases_posting ON public.job_posting_aliases USING btree (job_posting_id, last_seen_at);


--
-- Name: ix_job_posting_versions_posting_observed; Type: INDEX; Schema: public; Owner: job_hunt
--

CREATE INDEX ix_job_posting_versions_posting_observed ON public.job_posting_versions USING btree (job_posting_id, observed_at);


--
-- Name: ix_job_postings_owner_company; Type: INDEX; Schema: public; Owner: job_hunt
--

CREATE INDEX ix_job_postings_owner_company ON public.job_postings USING btree (owner_id, company_slug, last_confirmed_at);


--
-- Name: ix_job_postings_owner_lifecycle; Type: INDEX; Schema: public; Owner: job_hunt
--

CREATE INDEX ix_job_postings_owner_lifecycle ON public.job_postings USING btree (owner_id, lifecycle_state);


--
-- Name: ix_opportunity_decision_events_timeline; Type: INDEX; Schema: public; Owner: job_hunt
--

CREATE INDEX ix_opportunity_decision_events_timeline ON public.opportunity_decision_events USING btree (owner_opportunity_id, occurred_at);


--
-- Name: ix_opportunity_scan_sources_health; Type: INDEX; Schema: public; Owner: job_hunt
--

CREATE INDEX ix_opportunity_scan_sources_health ON public.opportunity_scan_sources USING btree (owner_id, source, completed_at);


--
-- Name: ix_opportunity_scan_sources_scan_status; Type: INDEX; Schema: public; Owner: job_hunt
--

CREATE INDEX ix_opportunity_scan_sources_scan_status ON public.opportunity_scan_sources USING btree (opportunity_scan_id, status);


--
-- Name: ix_opportunity_scans_owner_status; Type: INDEX; Schema: public; Owner: job_hunt
--

CREATE INDEX ix_opportunity_scans_owner_status ON public.opportunity_scans USING btree (owner_id, status, created_at);


--
-- Name: ix_opportunity_scans_search_scheduled; Type: INDEX; Schema: public; Owner: job_hunt
--

CREATE INDEX ix_opportunity_scans_search_scheduled ON public.opportunity_scans USING btree (saved_search_id, scheduled_for);


--
-- Name: ix_outreach_events_contact_outcome; Type: INDEX; Schema: public; Owner: job_hunt
--

CREATE INDEX ix_outreach_events_contact_outcome ON public.outreach_events USING btree (owner_id, application_contact_id, event_type, occurred_at);


--
-- Name: ix_outreach_events_timeline; Type: INDEX; Schema: public; Owner: job_hunt
--

CREATE INDEX ix_outreach_events_timeline ON public.outreach_events USING btree (owner_id, outreach_sequence_id, occurred_at, sequence_number);


--
-- Name: ix_outreach_message_versions_sequence; Type: INDEX; Schema: public; Owner: job_hunt
--

CREATE INDEX ix_outreach_message_versions_sequence ON public.outreach_message_versions USING btree (owner_id, outreach_sequence_id, application_contact_id, kind, created_at);


--
-- Name: ix_outreach_replies_sent_attempt; Type: INDEX; Schema: public; Owner: job_hunt
--

CREATE INDEX ix_outreach_replies_sent_attempt ON public.outreach_replies USING btree (owner_id, outreach_sequence_id, marked_sent_event_id, recorded_at, id);


--
-- Name: ix_outreach_replies_timeline; Type: INDEX; Schema: public; Owner: job_hunt
--

CREATE INDEX ix_outreach_replies_timeline ON public.outreach_replies USING btree (owner_id, outreach_sequence_id, recorded_at, id);


--
-- Name: ix_outreach_sequences_owner_status; Type: INDEX; Schema: public; Owner: job_hunt
--

CREATE INDEX ix_outreach_sequences_owner_status ON public.outreach_sequences USING btree (owner_id, status, updated_at);


--
-- Name: ix_owner_mutation_receipts_owner_created; Type: INDEX; Schema: public; Owner: job_hunt
--

CREATE INDEX ix_owner_mutation_receipts_owner_created ON public.owner_mutation_receipts USING btree (owner_id, created_at);


--
-- Name: ix_owner_opportunities_today; Type: INDEX; Schema: public; Owner: job_hunt
--

CREATE INDEX ix_owner_opportunities_today ON public.owner_opportunities USING btree (owner_id, decision, last_surfaced_at);


--
-- Name: ix_owner_sessions_owner_id; Type: INDEX; Schema: public; Owner: job_hunt
--

CREATE INDEX ix_owner_sessions_owner_id ON public.owner_sessions USING btree (owner_id);


--
-- Name: ix_privacy_deletion_receipts_deleted; Type: INDEX; Schema: public; Owner: job_hunt
--

CREATE INDEX ix_privacy_deletion_receipts_deleted ON public.privacy_deletion_receipts USING btree (deleted_at);


--
-- Name: ix_resume_versions_owner_created; Type: INDEX; Schema: public; Owner: job_hunt
--

CREATE INDEX ix_resume_versions_owner_created ON public.resume_versions USING btree (owner_id, created_at);


--
-- Name: ix_saved_search_matches_owner_recent; Type: INDEX; Schema: public; Owner: job_hunt
--

CREATE INDEX ix_saved_search_matches_owner_recent ON public.saved_search_matches USING btree (owner_id, last_matched_at);


--
-- Name: ix_saved_searches_due; Type: INDEX; Schema: public; Owner: job_hunt
--

CREATE INDEX ix_saved_searches_due ON public.saved_searches USING btree (active, next_scan_at);


--
-- Name: ix_saved_searches_owner_track; Type: INDEX; Schema: public; Owner: job_hunt
--

CREATE INDEX ix_saved_searches_owner_track ON public.saved_searches USING btree (owner_id, career_track_id);


--
-- Name: uq_action_items_owner_application_open; Type: INDEX; Schema: public; Owner: job_hunt
--

CREATE UNIQUE INDEX uq_action_items_owner_application_open ON public.action_items USING btree (owner_id, application_id) WHERE ((status)::text = 'open'::text);


--
-- Name: uq_application_activity_events_owner_applied; Type: INDEX; Schema: public; Owner: job_hunt
--

CREATE UNIQUE INDEX uq_application_activity_events_owner_applied ON public.application_activity_events USING btree (owner_id, application_id) WHERE ((event_type)::text = 'application_applied'::text);


--
-- Name: uq_application_activity_events_owner_closed; Type: INDEX; Schema: public; Owner: job_hunt
--

CREATE UNIQUE INDEX uq_application_activity_events_owner_closed ON public.application_activity_events USING btree (owner_id, application_id) WHERE ((event_type)::text = 'application_closed'::text);


--
-- Name: uq_application_activity_events_owner_created; Type: INDEX; Schema: public; Owner: job_hunt
--

CREATE UNIQUE INDEX uq_application_activity_events_owner_created ON public.application_activity_events USING btree (owner_id, application_id) WHERE ((event_type)::text = 'application_created'::text);


--
-- Name: uq_application_activity_events_owner_interviewing; Type: INDEX; Schema: public; Owner: job_hunt
--

CREATE UNIQUE INDEX uq_application_activity_events_owner_interviewing ON public.application_activity_events USING btree (owner_id, application_id) WHERE ((event_type)::text = 'application_interviewing'::text);


--
-- Name: uq_application_activity_events_owner_offer; Type: INDEX; Schema: public; Owner: job_hunt
--

CREATE UNIQUE INDEX uq_application_activity_events_owner_offer ON public.application_activity_events USING btree (owner_id, application_id) WHERE ((event_type)::text = 'application_offer'::text);


--
-- Name: uq_application_activity_events_owner_outcome; Type: INDEX; Schema: public; Owner: job_hunt
--

CREATE UNIQUE INDEX uq_application_activity_events_owner_outcome ON public.application_activity_events USING btree (owner_id, outcome_id) WHERE (outcome_id IS NOT NULL);


--
-- Name: uq_application_activity_events_owner_ready; Type: INDEX; Schema: public; Owner: job_hunt
--

CREATE UNIQUE INDEX uq_application_activity_events_owner_ready ON public.application_activity_events USING btree (owner_id, application_id) WHERE ((event_type)::text = 'application_ready_to_apply'::text);


--
-- Name: uq_application_activity_events_owner_screening; Type: INDEX; Schema: public; Owner: job_hunt
--

CREATE UNIQUE INDEX uq_application_activity_events_owner_screening ON public.application_activity_events USING btree (owner_id, application_id) WHERE ((event_type)::text = 'application_screening'::text);


--
-- Name: uq_application_activity_events_owner_submission; Type: INDEX; Schema: public; Owner: job_hunt
--

CREATE UNIQUE INDEX uq_application_activity_events_owner_submission ON public.application_activity_events USING btree (owner_id, submission_id) WHERE (submission_id IS NOT NULL);


--
-- Name: uq_application_contacts_owner_plan_bench_rank; Type: INDEX; Schema: public; Owner: job_hunt
--

CREATE UNIQUE INDEX uq_application_contacts_owner_plan_bench_rank ON public.application_contacts USING btree (owner_id, contact_plan_id, bench_rank) WHERE (bench_rank IS NOT NULL);


--
-- Name: uq_application_interview_round_events_owner_terminal; Type: INDEX; Schema: public; Owner: job_hunt
--

CREATE UNIQUE INDEX uq_application_interview_round_events_owner_terminal ON public.application_interview_round_events USING btree (owner_id, application_id, interview_round_id) WHERE ((event_type)::text = ANY ((ARRAY['completed'::character varying, 'cancelled'::character varying])::text[]));


--
-- Name: uq_application_interview_rounds_owner_scheduled; Type: INDEX; Schema: public; Owner: job_hunt
--

CREATE UNIQUE INDEX uq_application_interview_rounds_owner_scheduled ON public.application_interview_rounds USING btree (owner_id, application_id) WHERE ((status)::text = 'scheduled'::text);


--
-- Name: uq_application_milestone_corrections_owner_supersedes; Type: INDEX; Schema: public; Owner: job_hunt
--

CREATE UNIQUE INDEX uq_application_milestone_corrections_owner_supersedes ON public.application_milestone_corrections USING btree (owner_id, application_id, activity_event_id, supersedes_correction_id) WHERE (supersedes_correction_id IS NOT NULL);


--
-- Name: uq_applications_metric_snapshot_target; Type: INDEX; Schema: public; Owner: job_hunt
--

CREATE UNIQUE INDEX uq_applications_metric_snapshot_target ON public.applications USING btree (owner_id, id, job_posting_id, pursued_posting_version_id);


--
-- Name: uq_contact_plans_owner_application_active; Type: INDEX; Schema: public; Owner: job_hunt
--

CREATE UNIQUE INDEX uq_contact_plans_owner_application_active ON public.contact_plans USING btree (owner_id, application_id) WHERE ((status)::text = ANY ((ARRAY['queued'::character varying, 'running'::character varying])::text[]));


--
-- Name: uq_outreach_events_marked_sent; Type: INDEX; Schema: public; Owner: job_hunt
--

CREATE UNIQUE INDEX uq_outreach_events_marked_sent ON public.outreach_events USING btree (owner_id, outreach_sequence_id, application_contact_id, kind) WHERE ((event_type)::text = 'marked_sent'::text);


--
-- Name: uq_outreach_events_reply_target; Type: INDEX; Schema: public; Owner: job_hunt
--

CREATE UNIQUE INDEX uq_outreach_events_reply_target ON public.outreach_events USING btree (owner_id, application_id, outreach_sequence_id, application_contact_id, id, event_type, message_version_id, kind);


--
-- Name: uq_resume_versions_owner_base; Type: INDEX; Schema: public; Owner: job_hunt
--

CREATE UNIQUE INDEX uq_resume_versions_owner_base ON public.resume_versions USING btree (owner_id) WHERE is_base;


--
-- Name: achievement_evidence fk_achievement_evidence_owner_id_owners; Type: FK CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.achievement_evidence
    ADD CONSTRAINT fk_achievement_evidence_owner_id_owners FOREIGN KEY (owner_id) REFERENCES public.owners(id) ON DELETE CASCADE;


--
-- Name: achievement_evidence fk_achievement_evidence_owner_resume; Type: FK CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.achievement_evidence
    ADD CONSTRAINT fk_achievement_evidence_owner_resume FOREIGN KEY (owner_id, source_resume_version_id) REFERENCES public.resume_versions(owner_id, id) ON DELETE RESTRICT;


--
-- Name: action_items fk_action_items_owner_application; Type: FK CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.action_items
    ADD CONSTRAINT fk_action_items_owner_application FOREIGN KEY (owner_id, application_id) REFERENCES public.applications(owner_id, id) ON DELETE CASCADE;


--
-- Name: action_items fk_action_items_owner_id_owners; Type: FK CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.action_items
    ADD CONSTRAINT fk_action_items_owner_id_owners FOREIGN KEY (owner_id) REFERENCES public.owners(id) ON DELETE CASCADE;


--
-- Name: action_items fk_action_items_owner_interview_round; Type: FK CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.action_items
    ADD CONSTRAINT fk_action_items_owner_interview_round FOREIGN KEY (owner_id, application_id, interview_round_id) REFERENCES public.application_interview_rounds(owner_id, application_id, id) DEFERRABLE INITIALLY DEFERRED;


--
-- Name: application_action_reviews fk_application_action_reviews_owner_action; Type: FK CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.application_action_reviews
    ADD CONSTRAINT fk_application_action_reviews_owner_action FOREIGN KEY (owner_id, application_id, action_item_id) REFERENCES public.action_items(owner_id, application_id, id) ON DELETE CASCADE DEFERRABLE INITIALLY DEFERRED;


--
-- Name: application_action_reviews fk_application_action_reviews_owner_application; Type: FK CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.application_action_reviews
    ADD CONSTRAINT fk_application_action_reviews_owner_application FOREIGN KEY (owner_id, application_id) REFERENCES public.applications(owner_id, id) ON DELETE CASCADE;


--
-- Name: application_action_reviews fk_application_action_reviews_owner_id_owners; Type: FK CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.application_action_reviews
    ADD CONSTRAINT fk_application_action_reviews_owner_id_owners FOREIGN KEY (owner_id) REFERENCES public.owners(id) ON DELETE CASCADE;


--
-- Name: application_activity_events fk_application_activity_events_owner_action; Type: FK CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.application_activity_events
    ADD CONSTRAINT fk_application_activity_events_owner_action FOREIGN KEY (owner_id, application_id, action_item_id) REFERENCES public.action_items(owner_id, application_id, id) DEFERRABLE INITIALLY DEFERRED;


--
-- Name: application_activity_events fk_application_activity_events_owner_application; Type: FK CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.application_activity_events
    ADD CONSTRAINT fk_application_activity_events_owner_application FOREIGN KEY (owner_id, application_id) REFERENCES public.applications(owner_id, id) ON DELETE CASCADE;


--
-- Name: application_activity_events fk_application_activity_events_owner_id_owners; Type: FK CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.application_activity_events
    ADD CONSTRAINT fk_application_activity_events_owner_id_owners FOREIGN KEY (owner_id) REFERENCES public.owners(id) ON DELETE CASCADE;


--
-- Name: application_activity_events fk_application_activity_events_owner_interview_round; Type: FK CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.application_activity_events
    ADD CONSTRAINT fk_application_activity_events_owner_interview_round FOREIGN KEY (owner_id, application_id, interview_round_id) REFERENCES public.application_interview_rounds(owner_id, application_id, id) DEFERRABLE INITIALLY DEFERRED;


--
-- Name: application_activity_events fk_application_activity_events_owner_outcome; Type: FK CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.application_activity_events
    ADD CONSTRAINT fk_application_activity_events_owner_outcome FOREIGN KEY (owner_id, application_id, outcome_id) REFERENCES public.application_outcomes(owner_id, application_id, id) DEFERRABLE INITIALLY DEFERRED;


--
-- Name: application_activity_events fk_application_activity_events_owner_previous_action; Type: FK CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.application_activity_events
    ADD CONSTRAINT fk_application_activity_events_owner_previous_action FOREIGN KEY (owner_id, application_id, previous_action_item_id) REFERENCES public.action_items(owner_id, application_id, id) DEFERRABLE INITIALLY DEFERRED;


--
-- Name: application_activity_events fk_application_activity_events_owner_submission; Type: FK CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.application_activity_events
    ADD CONSTRAINT fk_application_activity_events_owner_submission FOREIGN KEY (owner_id, application_id, submission_id) REFERENCES public.application_submissions(owner_id, application_id, id) DEFERRABLE INITIALLY DEFERRED;


--
-- Name: application_artifact_events fk_application_artifact_events_owner_id_owners; Type: FK CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.application_artifact_events
    ADD CONSTRAINT fk_application_artifact_events_owner_id_owners FOREIGN KEY (owner_id) REFERENCES public.owners(id) ON DELETE CASCADE;


--
-- Name: application_artifact_events fk_application_artifact_events_owner_pack; Type: FK CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.application_artifact_events
    ADD CONSTRAINT fk_application_artifact_events_owner_pack FOREIGN KEY (owner_id, application_id, application_pack_id) REFERENCES public.application_packs(owner_id, application_id, id) ON DELETE CASCADE;


--
-- Name: application_artifact_events fk_application_artifact_events_owner_resume; Type: FK CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.application_artifact_events
    ADD CONSTRAINT fk_application_artifact_events_owner_resume FOREIGN KEY (owner_id, tailored_resume_version_id) REFERENCES public.resume_versions(owner_id, id) ON DELETE RESTRICT;


--
-- Name: application_artifact_events fk_application_artifact_events_owner_revision; Type: FK CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.application_artifact_events
    ADD CONSTRAINT fk_application_artifact_events_owner_revision FOREIGN KEY (owner_id, application_id, application_pack_id, artifact_revision_id) REFERENCES public.application_artifact_revisions(owner_id, application_id, application_pack_id, id) ON DELETE RESTRICT;


--
-- Name: application_artifact_revisions fk_application_artifact_revisions_owner_grounding; Type: FK CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.application_artifact_revisions
    ADD CONSTRAINT fk_application_artifact_revisions_owner_grounding FOREIGN KEY (owner_id, application_id, application_pack_id, grounding_revision_id) REFERENCES public.application_pack_revisions(owner_id, application_id, application_pack_id, id) ON DELETE RESTRICT;


--
-- Name: application_artifact_revisions fk_application_artifact_revisions_owner_id_owners; Type: FK CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.application_artifact_revisions
    ADD CONSTRAINT fk_application_artifact_revisions_owner_id_owners FOREIGN KEY (owner_id) REFERENCES public.owners(id) ON DELETE CASCADE;


--
-- Name: application_artifact_revisions fk_application_artifact_revisions_owner_pack; Type: FK CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.application_artifact_revisions
    ADD CONSTRAINT fk_application_artifact_revisions_owner_pack FOREIGN KEY (owner_id, application_id, application_pack_id) REFERENCES public.application_packs(owner_id, application_id, id) ON DELETE CASCADE;


--
-- Name: application_artifact_revisions fk_application_artifact_revisions_owner_parent; Type: FK CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.application_artifact_revisions
    ADD CONSTRAINT fk_application_artifact_revisions_owner_parent FOREIGN KEY (owner_id, application_id, application_pack_id, parent_artifact_revision_id) REFERENCES public.application_artifact_revisions(owner_id, application_id, application_pack_id, id) ON DELETE RESTRICT;


--
-- Name: application_contacts fk_application_contacts_owner_application; Type: FK CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.application_contacts
    ADD CONSTRAINT fk_application_contacts_owner_application FOREIGN KEY (owner_id, application_id) REFERENCES public.applications(owner_id, id) ON DELETE CASCADE;


--
-- Name: application_contacts fk_application_contacts_owner_contact; Type: FK CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.application_contacts
    ADD CONSTRAINT fk_application_contacts_owner_contact FOREIGN KEY (owner_id, contact_id) REFERENCES public.contacts(owner_id, id) DEFERRABLE INITIALLY DEFERRED;


--
-- Name: application_contacts fk_application_contacts_owner_id_owners; Type: FK CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.application_contacts
    ADD CONSTRAINT fk_application_contacts_owner_id_owners FOREIGN KEY (owner_id) REFERENCES public.owners(id) ON DELETE CASCADE;


--
-- Name: application_contacts fk_application_contacts_owner_plan; Type: FK CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.application_contacts
    ADD CONSTRAINT fk_application_contacts_owner_plan FOREIGN KEY (owner_id, application_id, contact_plan_id) REFERENCES public.contact_plans(owner_id, application_id, id) ON DELETE CASCADE;


--
-- Name: application_interview_preparation_revisions fk_application_interview_preparation_revisions_owner_id_owners; Type: FK CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.application_interview_preparation_revisions
    ADD CONSTRAINT fk_application_interview_preparation_revisions_owner_id_owners FOREIGN KEY (owner_id) REFERENCES public.owners(id) ON DELETE CASCADE;


--
-- Name: application_interview_preparations fk_application_interview_preparations_owner_id_owners; Type: FK CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.application_interview_preparations
    ADD CONSTRAINT fk_application_interview_preparations_owner_id_owners FOREIGN KEY (owner_id) REFERENCES public.owners(id) ON DELETE CASCADE;


--
-- Name: application_interview_round_events fk_application_interview_round_events_owner_action; Type: FK CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.application_interview_round_events
    ADD CONSTRAINT fk_application_interview_round_events_owner_action FOREIGN KEY (owner_id, application_id, action_item_id) REFERENCES public.action_items(owner_id, application_id, id) DEFERRABLE INITIALLY DEFERRED;


--
-- Name: application_interview_round_events fk_application_interview_round_events_owner_id_owners; Type: FK CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.application_interview_round_events
    ADD CONSTRAINT fk_application_interview_round_events_owner_id_owners FOREIGN KEY (owner_id) REFERENCES public.owners(id) ON DELETE CASCADE;


--
-- Name: application_interview_round_events fk_application_interview_round_events_owner_previous_action; Type: FK CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.application_interview_round_events
    ADD CONSTRAINT fk_application_interview_round_events_owner_previous_action FOREIGN KEY (owner_id, application_id, previous_action_item_id) REFERENCES public.action_items(owner_id, application_id, id) DEFERRABLE INITIALLY DEFERRED;


--
-- Name: application_interview_round_events fk_application_interview_round_events_owner_round; Type: FK CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.application_interview_round_events
    ADD CONSTRAINT fk_application_interview_round_events_owner_round FOREIGN KEY (owner_id, application_id, interview_round_id) REFERENCES public.application_interview_rounds(owner_id, application_id, id) ON DELETE CASCADE;


--
-- Name: application_interview_rounds fk_application_interview_rounds_owner_application; Type: FK CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.application_interview_rounds
    ADD CONSTRAINT fk_application_interview_rounds_owner_application FOREIGN KEY (owner_id, application_id) REFERENCES public.applications(owner_id, id) ON DELETE CASCADE;


--
-- Name: application_interview_rounds fk_application_interview_rounds_owner_id_owners; Type: FK CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.application_interview_rounds
    ADD CONSTRAINT fk_application_interview_rounds_owner_id_owners FOREIGN KEY (owner_id) REFERENCES public.owners(id) ON DELETE CASCADE;


--
-- Name: application_interview_rounds fk_application_interview_rounds_owner_submission; Type: FK CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.application_interview_rounds
    ADD CONSTRAINT fk_application_interview_rounds_owner_submission FOREIGN KEY (owner_id, application_id, application_submission_id) REFERENCES public.application_submissions(owner_id, application_id, id) DEFERRABLE INITIALLY DEFERRED;


--
-- Name: application_metric_snapshots fk_application_metric_snapshots_owner_application; Type: FK CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.application_metric_snapshots
    ADD CONSTRAINT fk_application_metric_snapshots_owner_application FOREIGN KEY (owner_id, application_id, job_posting_id, pursued_posting_version_id) REFERENCES public.applications(owner_id, id, job_posting_id, pursued_posting_version_id) ON DELETE CASCADE;


--
-- Name: application_metric_snapshots fk_application_metric_snapshots_owner_career_track; Type: FK CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.application_metric_snapshots
    ADD CONSTRAINT fk_application_metric_snapshots_owner_career_track FOREIGN KEY (owner_id, career_track_id) REFERENCES public.career_tracks(owner_id, id) ON DELETE RESTRICT;


--
-- Name: application_metric_snapshots fk_application_metric_snapshots_owner_id_owners; Type: FK CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.application_metric_snapshots
    ADD CONSTRAINT fk_application_metric_snapshots_owner_id_owners FOREIGN KEY (owner_id) REFERENCES public.owners(id) ON DELETE CASCADE;


--
-- Name: application_metric_snapshots fk_application_metric_snapshots_owner_posting_version; Type: FK CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.application_metric_snapshots
    ADD CONSTRAINT fk_application_metric_snapshots_owner_posting_version FOREIGN KEY (owner_id, job_posting_id, pursued_posting_version_id) REFERENCES public.job_posting_versions(owner_id, job_posting_id, id) DEFERRABLE INITIALLY DEFERRED;


--
-- Name: application_metric_snapshots fk_application_metric_snapshots_owner_saved_search; Type: FK CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.application_metric_snapshots
    ADD CONSTRAINT fk_application_metric_snapshots_owner_saved_search FOREIGN KEY (owner_id, saved_search_id) REFERENCES public.saved_searches(owner_id, id) ON DELETE RESTRICT;


--
-- Name: application_milestone_corrections fk_application_milestone_corrections_owner_activity; Type: FK CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.application_milestone_corrections
    ADD CONSTRAINT fk_application_milestone_corrections_owner_activity FOREIGN KEY (owner_id, application_id, activity_event_id) REFERENCES public.application_activity_events(owner_id, application_id, id) ON DELETE CASCADE;


--
-- Name: application_milestone_corrections fk_application_milestone_corrections_owner_application; Type: FK CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.application_milestone_corrections
    ADD CONSTRAINT fk_application_milestone_corrections_owner_application FOREIGN KEY (owner_id, application_id) REFERENCES public.applications(owner_id, id) ON DELETE CASCADE;


--
-- Name: application_milestone_corrections fk_application_milestone_corrections_owner_id_owners; Type: FK CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.application_milestone_corrections
    ADD CONSTRAINT fk_application_milestone_corrections_owner_id_owners FOREIGN KEY (owner_id) REFERENCES public.owners(id) ON DELETE CASCADE;


--
-- Name: application_milestone_corrections fk_application_milestone_corrections_owner_supersedes; Type: FK CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.application_milestone_corrections
    ADD CONSTRAINT fk_application_milestone_corrections_owner_supersedes FOREIGN KEY (owner_id, application_id, activity_event_id, supersedes_correction_id) REFERENCES public.application_milestone_corrections(owner_id, application_id, activity_event_id, id) DEFERRABLE INITIALLY DEFERRED;


--
-- Name: application_outcomes fk_application_outcomes_owner_application; Type: FK CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.application_outcomes
    ADD CONSTRAINT fk_application_outcomes_owner_application FOREIGN KEY (owner_id, application_id) REFERENCES public.applications(owner_id, id) ON DELETE CASCADE;


--
-- Name: application_outcomes fk_application_outcomes_owner_id_owners; Type: FK CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.application_outcomes
    ADD CONSTRAINT fk_application_outcomes_owner_id_owners FOREIGN KEY (owner_id) REFERENCES public.owners(id) ON DELETE CASCADE;


--
-- Name: application_outcomes fk_application_outcomes_owner_submission; Type: FK CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.application_outcomes
    ADD CONSTRAINT fk_application_outcomes_owner_submission FOREIGN KEY (owner_id, application_id, application_submission_id) REFERENCES public.application_submissions(owner_id, application_id, id) DEFERRABLE INITIALLY DEFERRED;


--
-- Name: application_pack_events fk_application_pack_events_owner_id_owners; Type: FK CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.application_pack_events
    ADD CONSTRAINT fk_application_pack_events_owner_id_owners FOREIGN KEY (owner_id) REFERENCES public.owners(id) ON DELETE CASCADE;


--
-- Name: application_pack_events fk_application_pack_events_owner_pack; Type: FK CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.application_pack_events
    ADD CONSTRAINT fk_application_pack_events_owner_pack FOREIGN KEY (owner_id, application_id, application_pack_id) REFERENCES public.application_packs(owner_id, application_id, id) ON DELETE CASCADE;


--
-- Name: application_pack_events fk_application_pack_events_owner_revision; Type: FK CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.application_pack_events
    ADD CONSTRAINT fk_application_pack_events_owner_revision FOREIGN KEY (owner_id, application_id, application_pack_id, revision_id) REFERENCES public.application_pack_revisions(owner_id, application_id, application_pack_id, id) ON DELETE RESTRICT;


--
-- Name: application_pack_revisions fk_application_pack_revisions_owner_id_owners; Type: FK CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.application_pack_revisions
    ADD CONSTRAINT fk_application_pack_revisions_owner_id_owners FOREIGN KEY (owner_id) REFERENCES public.owners(id) ON DELETE CASCADE;


--
-- Name: application_pack_revisions fk_application_pack_revisions_owner_pack; Type: FK CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.application_pack_revisions
    ADD CONSTRAINT fk_application_pack_revisions_owner_pack FOREIGN KEY (owner_id, application_id, application_pack_id) REFERENCES public.application_packs(owner_id, application_id, id) ON DELETE CASCADE;


--
-- Name: application_pack_revisions fk_application_pack_revisions_owner_parent; Type: FK CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.application_pack_revisions
    ADD CONSTRAINT fk_application_pack_revisions_owner_parent FOREIGN KEY (owner_id, application_id, application_pack_id, parent_revision_id) REFERENCES public.application_pack_revisions(owner_id, application_id, application_pack_id, id) ON DELETE RESTRICT;


--
-- Name: application_packs fk_application_packs_owner_application; Type: FK CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.application_packs
    ADD CONSTRAINT fk_application_packs_owner_application FOREIGN KEY (owner_id, application_id) REFERENCES public.applications(owner_id, id) ON DELETE CASCADE;


--
-- Name: application_packs fk_application_packs_owner_base_resume; Type: FK CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.application_packs
    ADD CONSTRAINT fk_application_packs_owner_base_resume FOREIGN KEY (owner_id, base_resume_version_id) REFERENCES public.resume_versions(owner_id, id) ON DELETE RESTRICT;


--
-- Name: application_packs fk_application_packs_owner_id_owners; Type: FK CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.application_packs
    ADD CONSTRAINT fk_application_packs_owner_id_owners FOREIGN KEY (owner_id) REFERENCES public.owners(id) ON DELETE CASCADE;


--
-- Name: application_packs fk_application_packs_owner_posting_version; Type: FK CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.application_packs
    ADD CONSTRAINT fk_application_packs_owner_posting_version FOREIGN KEY (owner_id, job_posting_id, posting_version_id) REFERENCES public.job_posting_versions(owner_id, job_posting_id, id) ON DELETE RESTRICT;


--
-- Name: application_submissions fk_application_submissions_owner_application; Type: FK CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.application_submissions
    ADD CONSTRAINT fk_application_submissions_owner_application FOREIGN KEY (owner_id, application_id) REFERENCES public.applications(owner_id, id) ON DELETE CASCADE;


--
-- Name: application_submissions fk_application_submissions_owner_artifact_approval; Type: FK CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.application_submissions
    ADD CONSTRAINT fk_application_submissions_owner_artifact_approval FOREIGN KEY (owner_id, application_id, application_pack_id, application_artifact_revision_id, application_artifact_approval_event_id) REFERENCES public.application_artifact_events(owner_id, application_id, application_pack_id, artifact_revision_id, id) DEFERRABLE INITIALLY DEFERRED;


--
-- Name: application_submissions fk_application_submissions_owner_artifact_revision; Type: FK CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.application_submissions
    ADD CONSTRAINT fk_application_submissions_owner_artifact_revision FOREIGN KEY (owner_id, application_id, application_pack_id, application_artifact_revision_id) REFERENCES public.application_artifact_revisions(owner_id, application_id, application_pack_id, id) DEFERRABLE INITIALLY DEFERRED;


--
-- Name: application_submissions fk_application_submissions_owner_id_owners; Type: FK CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.application_submissions
    ADD CONSTRAINT fk_application_submissions_owner_id_owners FOREIGN KEY (owner_id) REFERENCES public.owners(id) ON DELETE CASCADE;


--
-- Name: application_submissions fk_application_submissions_owner_pack; Type: FK CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.application_submissions
    ADD CONSTRAINT fk_application_submissions_owner_pack FOREIGN KEY (owner_id, application_id, application_pack_id) REFERENCES public.application_packs(owner_id, application_id, id) DEFERRABLE INITIALLY DEFERRED;


--
-- Name: application_submissions fk_application_submissions_owner_pack_review; Type: FK CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.application_submissions
    ADD CONSTRAINT fk_application_submissions_owner_pack_review FOREIGN KEY (owner_id, application_id, application_pack_id, application_pack_revision_id, application_pack_review_event_id) REFERENCES public.application_pack_events(owner_id, application_id, application_pack_id, revision_id, id) DEFERRABLE INITIALLY DEFERRED;


--
-- Name: application_submissions fk_application_submissions_owner_pack_revision; Type: FK CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.application_submissions
    ADD CONSTRAINT fk_application_submissions_owner_pack_revision FOREIGN KEY (owner_id, application_id, application_pack_id, application_pack_revision_id) REFERENCES public.application_pack_revisions(owner_id, application_id, application_pack_id, id) DEFERRABLE INITIALLY DEFERRED;


--
-- Name: application_submissions fk_application_submissions_owner_tailored_resume; Type: FK CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.application_submissions
    ADD CONSTRAINT fk_application_submissions_owner_tailored_resume FOREIGN KEY (owner_id, tailored_resume_version_id) REFERENCES public.resume_versions(owner_id, id) DEFERRABLE INITIALLY DEFERRED;


--
-- Name: applications fk_applications_owner_id_owners; Type: FK CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.applications
    ADD CONSTRAINT fk_applications_owner_id_owners FOREIGN KEY (owner_id) REFERENCES public.owners(id) ON DELETE CASCADE;


--
-- Name: applications fk_applications_owner_opportunity; Type: FK CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.applications
    ADD CONSTRAINT fk_applications_owner_opportunity FOREIGN KEY (owner_id, owner_opportunity_id, job_posting_id) REFERENCES public.owner_opportunities(owner_id, id, job_posting_id) ON DELETE CASCADE;


--
-- Name: applications fk_applications_owner_outcome; Type: FK CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.applications
    ADD CONSTRAINT fk_applications_owner_outcome FOREIGN KEY (owner_id, id, outcome_id) REFERENCES public.application_outcomes(owner_id, application_id, id) DEFERRABLE INITIALLY DEFERRED;


--
-- Name: applications fk_applications_owner_posting_version; Type: FK CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.applications
    ADD CONSTRAINT fk_applications_owner_posting_version FOREIGN KEY (owner_id, job_posting_id, pursued_posting_version_id) REFERENCES public.job_posting_versions(owner_id, job_posting_id, id) DEFERRABLE INITIALLY DEFERRED;


--
-- Name: background_job_events fk_background_job_events_job_id_background_jobs; Type: FK CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.background_job_events
    ADD CONSTRAINT fk_background_job_events_job_id_background_jobs FOREIGN KEY (job_id) REFERENCES public.background_jobs(id) ON DELETE CASCADE;


--
-- Name: background_jobs fk_background_jobs_owner_id_owners; Type: FK CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.background_jobs
    ADD CONSTRAINT fk_background_jobs_owner_id_owners FOREIGN KEY (owner_id) REFERENCES public.owners(id) ON DELETE CASCADE;


--
-- Name: candidate_profiles fk_candidate_profiles_owner_id_owners; Type: FK CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.candidate_profiles
    ADD CONSTRAINT fk_candidate_profiles_owner_id_owners FOREIGN KEY (owner_id) REFERENCES public.owners(id) ON DELETE CASCADE;


--
-- Name: career_tracks fk_career_tracks_owner_id_owners; Type: FK CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.career_tracks
    ADD CONSTRAINT fk_career_tracks_owner_id_owners FOREIGN KEY (owner_id) REFERENCES public.owners(id) ON DELETE CASCADE;


--
-- Name: contact_plans fk_contact_plans_background_job_id_background_jobs; Type: FK CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.contact_plans
    ADD CONSTRAINT fk_contact_plans_background_job_id_background_jobs FOREIGN KEY (background_job_id) REFERENCES public.background_jobs(id) ON DELETE SET NULL;


--
-- Name: contact_plans fk_contact_plans_owner_application; Type: FK CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.contact_plans
    ADD CONSTRAINT fk_contact_plans_owner_application FOREIGN KEY (owner_id, application_id) REFERENCES public.applications(owner_id, id) ON DELETE CASCADE;


--
-- Name: contact_plans fk_contact_plans_owner_id_owners; Type: FK CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.contact_plans
    ADD CONSTRAINT fk_contact_plans_owner_id_owners FOREIGN KEY (owner_id) REFERENCES public.owners(id) ON DELETE CASCADE;


--
-- Name: contacts fk_contacts_owner_id_owners; Type: FK CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.contacts
    ADD CONSTRAINT fk_contacts_owner_id_owners FOREIGN KEY (owner_id) REFERENCES public.owners(id) ON DELETE CASCADE;


--
-- Name: hunt_outcomes fk_hunt_outcomes_hunt_run_id_hunt_runs; Type: FK CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.hunt_outcomes
    ADD CONSTRAINT fk_hunt_outcomes_hunt_run_id_hunt_runs FOREIGN KEY (hunt_run_id) REFERENCES public.hunt_runs(id) ON DELETE CASCADE;


--
-- Name: hunt_runs fk_hunt_runs_background_job_id_background_jobs; Type: FK CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.hunt_runs
    ADD CONSTRAINT fk_hunt_runs_background_job_id_background_jobs FOREIGN KEY (background_job_id) REFERENCES public.background_jobs(id) ON DELETE CASCADE;


--
-- Name: hunt_runs fk_hunt_runs_owner_id_owners; Type: FK CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.hunt_runs
    ADD CONSTRAINT fk_hunt_runs_owner_id_owners FOREIGN KEY (owner_id) REFERENCES public.owners(id) ON DELETE CASCADE;


--
-- Name: application_interview_preparation_revisions fk_interview_prep_revisions_owner_grounding; Type: FK CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.application_interview_preparation_revisions
    ADD CONSTRAINT fk_interview_prep_revisions_owner_grounding FOREIGN KEY (owner_id, application_id, application_pack_id, grounding_revision_id) REFERENCES public.application_pack_revisions(owner_id, application_id, application_pack_id, id) ON DELETE RESTRICT;


--
-- Name: application_interview_preparation_revisions fk_interview_prep_revisions_owner_parent; Type: FK CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.application_interview_preparation_revisions
    ADD CONSTRAINT fk_interview_prep_revisions_owner_parent FOREIGN KEY (owner_id, application_id, preparation_id, parent_revision_id) REFERENCES public.application_interview_preparation_revisions(owner_id, application_id, preparation_id, id) ON DELETE RESTRICT;


--
-- Name: application_interview_preparation_revisions fk_interview_prep_revisions_owner_posting_version; Type: FK CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.application_interview_preparation_revisions
    ADD CONSTRAINT fk_interview_prep_revisions_owner_posting_version FOREIGN KEY (owner_id, job_posting_id, posting_version_id) REFERENCES public.job_posting_versions(owner_id, job_posting_id, id) ON DELETE RESTRICT;


--
-- Name: application_interview_preparation_revisions fk_interview_prep_revisions_owner_prep; Type: FK CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.application_interview_preparation_revisions
    ADD CONSTRAINT fk_interview_prep_revisions_owner_prep FOREIGN KEY (owner_id, application_id, preparation_id) REFERENCES public.application_interview_preparations(owner_id, application_id, id) ON DELETE CASCADE;


--
-- Name: application_interview_preparation_revisions fk_interview_prep_revisions_owner_round; Type: FK CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.application_interview_preparation_revisions
    ADD CONSTRAINT fk_interview_prep_revisions_owner_round FOREIGN KEY (owner_id, application_id, interview_round_id) REFERENCES public.application_interview_rounds(owner_id, application_id, id) ON DELETE RESTRICT;


--
-- Name: application_interview_preparation_revisions fk_interview_prep_revisions_owner_submission; Type: FK CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.application_interview_preparation_revisions
    ADD CONSTRAINT fk_interview_prep_revisions_owner_submission FOREIGN KEY (owner_id, application_id, application_submission_id) REFERENCES public.application_submissions(owner_id, application_id, id) ON DELETE RESTRICT;


--
-- Name: application_interview_preparations fk_interview_preps_owner_application; Type: FK CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.application_interview_preparations
    ADD CONSTRAINT fk_interview_preps_owner_application FOREIGN KEY (owner_id, application_id) REFERENCES public.applications(owner_id, id) ON DELETE CASCADE;


--
-- Name: job_observations fk_job_observations_owner_id_owners; Type: FK CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.job_observations
    ADD CONSTRAINT fk_job_observations_owner_id_owners FOREIGN KEY (owner_id) REFERENCES public.owners(id) ON DELETE CASCADE;


--
-- Name: job_observations fk_job_observations_owner_posting_alias; Type: FK CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.job_observations
    ADD CONSTRAINT fk_job_observations_owner_posting_alias FOREIGN KEY (owner_id, job_posting_id, job_posting_alias_id) REFERENCES public.job_posting_aliases(owner_id, job_posting_id, id) ON DELETE RESTRICT;


--
-- Name: job_observations fk_job_observations_owner_posting_version; Type: FK CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.job_observations
    ADD CONSTRAINT fk_job_observations_owner_posting_version FOREIGN KEY (owner_id, job_posting_id, job_posting_version_id) REFERENCES public.job_posting_versions(owner_id, job_posting_id, id) ON DELETE RESTRICT;


--
-- Name: job_observations fk_job_observations_owner_scan_source; Type: FK CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.job_observations
    ADD CONSTRAINT fk_job_observations_owner_scan_source FOREIGN KEY (owner_id, opportunity_scan_id, opportunity_scan_source_id) REFERENCES public.opportunity_scan_sources(owner_id, opportunity_scan_id, id) ON DELETE CASCADE;


--
-- Name: job_posting_aliases fk_job_posting_aliases_owner_id_owners; Type: FK CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.job_posting_aliases
    ADD CONSTRAINT fk_job_posting_aliases_owner_id_owners FOREIGN KEY (owner_id) REFERENCES public.owners(id) ON DELETE CASCADE;


--
-- Name: job_posting_aliases fk_job_posting_aliases_owner_posting; Type: FK CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.job_posting_aliases
    ADD CONSTRAINT fk_job_posting_aliases_owner_posting FOREIGN KEY (owner_id, job_posting_id) REFERENCES public.job_postings(owner_id, id) ON DELETE CASCADE;


--
-- Name: job_posting_versions fk_job_posting_versions_owner_id_owners; Type: FK CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.job_posting_versions
    ADD CONSTRAINT fk_job_posting_versions_owner_id_owners FOREIGN KEY (owner_id) REFERENCES public.owners(id) ON DELETE CASCADE;


--
-- Name: job_posting_versions fk_job_posting_versions_owner_posting; Type: FK CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.job_posting_versions
    ADD CONSTRAINT fk_job_posting_versions_owner_posting FOREIGN KEY (owner_id, job_posting_id) REFERENCES public.job_postings(owner_id, id) ON DELETE CASCADE;


--
-- Name: job_postings fk_job_postings_owner_id_owners; Type: FK CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.job_postings
    ADD CONSTRAINT fk_job_postings_owner_id_owners FOREIGN KEY (owner_id) REFERENCES public.owners(id) ON DELETE CASCADE;


--
-- Name: opportunity_decision_events fk_opportunity_decision_events_compensates; Type: FK CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.opportunity_decision_events
    ADD CONSTRAINT fk_opportunity_decision_events_compensates FOREIGN KEY (owner_id, owner_opportunity_id, compensates_event_id) REFERENCES public.opportunity_decision_events(owner_id, owner_opportunity_id, id) ON DELETE RESTRICT;


--
-- Name: opportunity_decision_events fk_opportunity_decision_events_owner_id_owners; Type: FK CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.opportunity_decision_events
    ADD CONSTRAINT fk_opportunity_decision_events_owner_id_owners FOREIGN KEY (owner_id) REFERENCES public.owners(id) ON DELETE CASCADE;


--
-- Name: opportunity_decision_events fk_opportunity_decision_events_owner_opportunity; Type: FK CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.opportunity_decision_events
    ADD CONSTRAINT fk_opportunity_decision_events_owner_opportunity FOREIGN KEY (owner_id, owner_opportunity_id, job_posting_id) REFERENCES public.owner_opportunities(owner_id, id, job_posting_id) ON DELETE CASCADE;


--
-- Name: opportunity_decision_events fk_opportunity_decision_events_owner_version; Type: FK CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.opportunity_decision_events
    ADD CONSTRAINT fk_opportunity_decision_events_owner_version FOREIGN KEY (owner_id, job_posting_id, posting_version_id) REFERENCES public.job_posting_versions(owner_id, job_posting_id, id) ON DELETE RESTRICT;


--
-- Name: opportunity_scan_sources fk_opportunity_scan_sources_owner_id_owners; Type: FK CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.opportunity_scan_sources
    ADD CONSTRAINT fk_opportunity_scan_sources_owner_id_owners FOREIGN KEY (owner_id) REFERENCES public.owners(id) ON DELETE CASCADE;


--
-- Name: opportunity_scan_sources fk_opportunity_scan_sources_owner_scan; Type: FK CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.opportunity_scan_sources
    ADD CONSTRAINT fk_opportunity_scan_sources_owner_scan FOREIGN KEY (owner_id, opportunity_scan_id) REFERENCES public.opportunity_scans(owner_id, id) ON DELETE CASCADE;


--
-- Name: opportunity_scans fk_opportunity_scans_background_job_id_background_jobs; Type: FK CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.opportunity_scans
    ADD CONSTRAINT fk_opportunity_scans_background_job_id_background_jobs FOREIGN KEY (background_job_id) REFERENCES public.background_jobs(id) ON DELETE SET NULL;


--
-- Name: opportunity_scans fk_opportunity_scans_owner_id_owners; Type: FK CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.opportunity_scans
    ADD CONSTRAINT fk_opportunity_scans_owner_id_owners FOREIGN KEY (owner_id) REFERENCES public.owners(id) ON DELETE CASCADE;


--
-- Name: opportunity_scans fk_opportunity_scans_owner_search; Type: FK CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.opportunity_scans
    ADD CONSTRAINT fk_opportunity_scans_owner_search FOREIGN KEY (owner_id, saved_search_id) REFERENCES public.saved_searches(owner_id, id) ON DELETE RESTRICT;


--
-- Name: outreach_events fk_outreach_events_owner_contact; Type: FK CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.outreach_events
    ADD CONSTRAINT fk_outreach_events_owner_contact FOREIGN KEY (owner_id, application_id, application_contact_id) REFERENCES public.application_contacts(owner_id, application_id, id) DEFERRABLE INITIALLY DEFERRED;


--
-- Name: outreach_events fk_outreach_events_owner_id_owners; Type: FK CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.outreach_events
    ADD CONSTRAINT fk_outreach_events_owner_id_owners FOREIGN KEY (owner_id) REFERENCES public.owners(id) ON DELETE CASCADE;


--
-- Name: outreach_events fk_outreach_events_owner_message_version; Type: FK CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.outreach_events
    ADD CONSTRAINT fk_outreach_events_owner_message_version FOREIGN KEY (owner_id, application_id, outreach_sequence_id, application_contact_id, message_version_id, kind) REFERENCES public.outreach_message_versions(owner_id, application_id, outreach_sequence_id, application_contact_id, id, kind) DEFERRABLE INITIALLY DEFERRED;


--
-- Name: outreach_events fk_outreach_events_owner_sequence; Type: FK CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.outreach_events
    ADD CONSTRAINT fk_outreach_events_owner_sequence FOREIGN KEY (owner_id, application_id, outreach_sequence_id) REFERENCES public.outreach_sequences(owner_id, application_id, id) ON DELETE CASCADE;


--
-- Name: outreach_message_versions fk_outreach_message_versions_owner_contact; Type: FK CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.outreach_message_versions
    ADD CONSTRAINT fk_outreach_message_versions_owner_contact FOREIGN KEY (owner_id, application_id, application_contact_id) REFERENCES public.application_contacts(owner_id, application_id, id) DEFERRABLE INITIALLY DEFERRED;


--
-- Name: outreach_message_versions fk_outreach_message_versions_owner_id_owners; Type: FK CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.outreach_message_versions
    ADD CONSTRAINT fk_outreach_message_versions_owner_id_owners FOREIGN KEY (owner_id) REFERENCES public.owners(id) ON DELETE CASCADE;


--
-- Name: outreach_message_versions fk_outreach_message_versions_owner_sequence; Type: FK CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.outreach_message_versions
    ADD CONSTRAINT fk_outreach_message_versions_owner_sequence FOREIGN KEY (owner_id, application_id, outreach_sequence_id) REFERENCES public.outreach_sequences(owner_id, application_id, id) ON DELETE CASCADE;


--
-- Name: outreach_replies fk_outreach_replies_owner_contact; Type: FK CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.outreach_replies
    ADD CONSTRAINT fk_outreach_replies_owner_contact FOREIGN KEY (owner_id, application_id, application_contact_id) REFERENCES public.application_contacts(owner_id, application_id, id) DEFERRABLE INITIALLY DEFERRED;


--
-- Name: outreach_replies fk_outreach_replies_owner_id_owners; Type: FK CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.outreach_replies
    ADD CONSTRAINT fk_outreach_replies_owner_id_owners FOREIGN KEY (owner_id) REFERENCES public.owners(id) ON DELETE CASCADE;


--
-- Name: outreach_replies fk_outreach_replies_owner_message_version; Type: FK CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.outreach_replies
    ADD CONSTRAINT fk_outreach_replies_owner_message_version FOREIGN KEY (owner_id, application_id, outreach_sequence_id, application_contact_id, message_version_id, message_kind) REFERENCES public.outreach_message_versions(owner_id, application_id, outreach_sequence_id, application_contact_id, id, kind) DEFERRABLE INITIALLY DEFERRED;


--
-- Name: outreach_replies fk_outreach_replies_owner_sent_event; Type: FK CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.outreach_replies
    ADD CONSTRAINT fk_outreach_replies_owner_sent_event FOREIGN KEY (owner_id, application_id, outreach_sequence_id, application_contact_id, marked_sent_event_id, marked_sent_event_type, message_version_id, message_kind) REFERENCES public.outreach_events(owner_id, application_id, outreach_sequence_id, application_contact_id, id, event_type, message_version_id, kind) ON DELETE CASCADE DEFERRABLE INITIALLY DEFERRED;


--
-- Name: outreach_replies fk_outreach_replies_owner_sequence; Type: FK CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.outreach_replies
    ADD CONSTRAINT fk_outreach_replies_owner_sequence FOREIGN KEY (owner_id, application_id, outreach_sequence_id) REFERENCES public.outreach_sequences(owner_id, application_id, id) ON DELETE CASCADE;


--
-- Name: outreach_sequences fk_outreach_sequences_owner_application; Type: FK CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.outreach_sequences
    ADD CONSTRAINT fk_outreach_sequences_owner_application FOREIGN KEY (owner_id, application_id) REFERENCES public.applications(owner_id, id) ON DELETE CASCADE;


--
-- Name: outreach_sequences fk_outreach_sequences_owner_contact_plan; Type: FK CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.outreach_sequences
    ADD CONSTRAINT fk_outreach_sequences_owner_contact_plan FOREIGN KEY (owner_id, application_id, contact_plan_id) REFERENCES public.contact_plans(owner_id, application_id, id) DEFERRABLE INITIALLY DEFERRED;


--
-- Name: outreach_sequences fk_outreach_sequences_owner_id_owners; Type: FK CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.outreach_sequences
    ADD CONSTRAINT fk_outreach_sequences_owner_id_owners FOREIGN KEY (owner_id) REFERENCES public.owners(id) ON DELETE CASCADE;


--
-- Name: owner_mutation_receipts fk_owner_mutation_receipts_owner_id_owners; Type: FK CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.owner_mutation_receipts
    ADD CONSTRAINT fk_owner_mutation_receipts_owner_id_owners FOREIGN KEY (owner_id) REFERENCES public.owners(id) ON DELETE CASCADE;


--
-- Name: owner_opportunities fk_owner_opportunities_owner_id_owners; Type: FK CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.owner_opportunities
    ADD CONSTRAINT fk_owner_opportunities_owner_id_owners FOREIGN KEY (owner_id) REFERENCES public.owners(id) ON DELETE CASCADE;


--
-- Name: owner_opportunities fk_owner_opportunities_owner_posting; Type: FK CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.owner_opportunities
    ADD CONSTRAINT fk_owner_opportunities_owner_posting FOREIGN KEY (owner_id, job_posting_id) REFERENCES public.job_postings(owner_id, id) ON DELETE CASCADE;


--
-- Name: owner_opportunities fk_owner_opportunities_owner_reviewed_version; Type: FK CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.owner_opportunities
    ADD CONSTRAINT fk_owner_opportunities_owner_reviewed_version FOREIGN KEY (owner_id, job_posting_id, reviewed_posting_version_id) REFERENCES public.job_posting_versions(owner_id, job_posting_id, id) ON DELETE RESTRICT;


--
-- Name: owner_privacy_settings fk_owner_privacy_settings_owner_id_owners; Type: FK CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.owner_privacy_settings
    ADD CONSTRAINT fk_owner_privacy_settings_owner_id_owners FOREIGN KEY (owner_id) REFERENCES public.owners(id) ON DELETE CASCADE;


--
-- Name: owner_sessions fk_owner_sessions_owner_id_owners; Type: FK CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.owner_sessions
    ADD CONSTRAINT fk_owner_sessions_owner_id_owners FOREIGN KEY (owner_id) REFERENCES public.owners(id) ON DELETE CASCADE;


--
-- Name: resume_versions fk_resume_versions_owner_id_owners; Type: FK CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.resume_versions
    ADD CONSTRAINT fk_resume_versions_owner_id_owners FOREIGN KEY (owner_id) REFERENCES public.owners(id) ON DELETE CASCADE;


--
-- Name: resume_versions fk_resume_versions_owner_parent; Type: FK CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.resume_versions
    ADD CONSTRAINT fk_resume_versions_owner_parent FOREIGN KEY (owner_id, parent_id) REFERENCES public.resume_versions(owner_id, id) ON DELETE RESTRICT;


--
-- Name: saved_search_matches fk_saved_search_matches_owner_first_scan; Type: FK CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.saved_search_matches
    ADD CONSTRAINT fk_saved_search_matches_owner_first_scan FOREIGN KEY (owner_id, saved_search_id, first_scan_id) REFERENCES public.opportunity_scans(owner_id, saved_search_id, id) ON DELETE RESTRICT;


--
-- Name: saved_search_matches fk_saved_search_matches_owner_id_owners; Type: FK CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.saved_search_matches
    ADD CONSTRAINT fk_saved_search_matches_owner_id_owners FOREIGN KEY (owner_id) REFERENCES public.owners(id) ON DELETE CASCADE;


--
-- Name: saved_search_matches fk_saved_search_matches_owner_last_scan; Type: FK CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.saved_search_matches
    ADD CONSTRAINT fk_saved_search_matches_owner_last_scan FOREIGN KEY (owner_id, saved_search_id, last_scan_id) REFERENCES public.opportunity_scans(owner_id, saved_search_id, id) ON DELETE RESTRICT;


--
-- Name: saved_search_matches fk_saved_search_matches_owner_last_version; Type: FK CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.saved_search_matches
    ADD CONSTRAINT fk_saved_search_matches_owner_last_version FOREIGN KEY (owner_id, job_posting_id, last_posting_version_id) REFERENCES public.job_posting_versions(owner_id, job_posting_id, id) ON DELETE RESTRICT;


--
-- Name: saved_search_matches fk_saved_search_matches_owner_posting; Type: FK CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.saved_search_matches
    ADD CONSTRAINT fk_saved_search_matches_owner_posting FOREIGN KEY (owner_id, job_posting_id) REFERENCES public.job_postings(owner_id, id) ON DELETE CASCADE;


--
-- Name: saved_search_matches fk_saved_search_matches_owner_search; Type: FK CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.saved_search_matches
    ADD CONSTRAINT fk_saved_search_matches_owner_search FOREIGN KEY (owner_id, saved_search_id) REFERENCES public.saved_searches(owner_id, id) ON DELETE RESTRICT;


--
-- Name: saved_searches fk_saved_searches_owner_id_owners; Type: FK CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.saved_searches
    ADD CONSTRAINT fk_saved_searches_owner_id_owners FOREIGN KEY (owner_id) REFERENCES public.owners(id) ON DELETE CASCADE;


--
-- Name: saved_searches fk_saved_searches_owner_resume; Type: FK CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.saved_searches
    ADD CONSTRAINT fk_saved_searches_owner_resume FOREIGN KEY (owner_id, resume_version_id) REFERENCES public.resume_versions(owner_id, id) ON DELETE RESTRICT;


--
-- Name: saved_searches fk_saved_searches_owner_track; Type: FK CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.saved_searches
    ADD CONSTRAINT fk_saved_searches_owner_track FOREIGN KEY (owner_id, career_track_id) REFERENCES public.career_tracks(owner_id, id) ON DELETE RESTRICT;


--
-- Name: worker_heartbeats fk_worker_heartbeats_current_job_id_background_jobs; Type: FK CONSTRAINT; Schema: public; Owner: job_hunt
--

ALTER TABLE ONLY public.worker_heartbeats
    ADD CONSTRAINT fk_worker_heartbeats_current_job_id_background_jobs FOREIGN KEY (current_job_id) REFERENCES public.background_jobs(id) ON DELETE SET NULL;


--
-- PostgreSQL database dump complete
--

\unrestrict dmEgFzxkT4hLT0LOgmbtJdgHoGnspD3NwOUhYSSomXlLbs75lR4gb9TE0hd4mKI

