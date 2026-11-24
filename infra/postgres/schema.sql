-- ============================================================================
-- hanyue 数据模型完整初始化脚本（PostgreSQL）
-- ============================================================================
-- 本文件由 scripts/export_pg_schema.py 从 SQLAlchemy 模型自动生成，请勿手工编辑。
-- 模型变更后重新生成：
--   cd apps/api && python scripts/export_pg_schema.py -o ../../infra/postgres/schema.sql
-- CI 校验一致性（模型改了但忘了重新生成会失败）：
--   cd apps/api && python scripts/export_pg_schema.py -o ../../infra/postgres/schema.sql --check
--
-- 适用：全新数据库一次性建表。发现任意应用表时跳过全部 DDL，
--       已有库升级必须由应用 init_db() 执行；本脚本不是迁移脚本。
-- 部署后必须立即运行 bootstrap 设置独立强密码，见 docs/setup.md；本文件不播种账号。
-- 说明：本脚本按「当前模型的理想全新库」建表，个别列会比渐进迁移的老库更严格
--       （如新列是 NOT NULL，而老库经 ALTER TABLE ADD COLUMN 补列时为可空）——这是
--       正确且预期的差异，不影响功能；表/列/索引的集合与老库完全一致。
-- 前置：库已创建（见 create-role-and-db.sql），当前用户在该库有 CREATE 权限。
-- 执行：psql -U <user> -d <db> -v ON_ERROR_STOP=1 -f schema.sql
-- ============================================================================

\set ON_ERROR_STOP on

-- 任一应用表已存在，全部结构迁移交给 init_db。
SELECT EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
  WHERE n.nspname = current_schema() AND c.relkind IN ('r', 'p')
    AND c.relname IN ('agent_approvals', 'agent_events', 'agent_provider_connections', 'agent_runs', 'agent_sessions', 'agent_tool_calls', 'ai_generations', 'analysis_history', 'analysis_keywords', 'analysis_tasks', 'app_settings', 'article_blocks', 'article_revisions', 'assets', 'author_skill_distill_runs', 'author_skill_revisions', 'author_skills', 'background_jobs', 'calendar_entries', 'card_templates', 'characters', 'connected_accounts', 'content_assets', 'content_embeddings', 'content_items', 'content_versions', 'handdraw_colors', 'handdraw_library_meta', 'handdraw_styles', 'hot_clusters', 'hot_items', 'hot_rank_history', 'image_jobs', 'image_styles', 'menus', 'metric_snapshots', 'model_catalog_entries', 'model_connections', 'novel_aliases', 'novel_chapters', 'novel_characters', 'novel_projects', 'originality_analyses', 'originality_reports', 'permissions', 'pet_conversations', 'platform_access_tokens', 'platform_model_settings', 'platform_sync_jobs', 'platform_variants', 'prompt_rules', 'prompt_templates', 'publish_jobs', 'publish_records', 'rbac_audit_logs', 'review_comments', 'revoked_tokens', 'role_menus', 'role_permissions', 'roles', 'scheduled_script_versions', 'scheduled_scripts', 'scheduled_task_runs', 'scheduled_tasks', 'similarity_cache', 'skill_distillation_jobs', 'skill_registry_entries', 'skill_usage_logs', 'sticker_images', 'sticker_sets', 'storyboard_projects', 'storyboard_scenes', 'storyboard_shots', 'studio_articles', 'user_preferences', 'user_roles', 'users', 'week_reviews', 'workspace_skills', 'workspaces')) AS hanyue_existing_schema
\gset
\if :hanyue_existing_schema
\echo '检测到已有应用表，跳过 schema.sql；请启动应用执行迁移。'
\else
BEGIN;

-- ---------- 表（含主键 / 外键 / CHECK / 唯一约束） ----------

-- app_settings
CREATE TABLE IF NOT EXISTS app_settings (
	key VARCHAR(64) NOT NULL,
	value JSON NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (key)
);

-- handdraw_colors
CREATE TABLE IF NOT EXISTS handdraw_colors (
	id VARCHAR(32) NOT NULL,
	color_id VARCHAR(16) NOT NULL,
	category VARCHAR(32) NOT NULL,
	category_zh VARCHAR(60) NOT NULL,
	category_en VARCHAR(60) NOT NULL,
	name_zh VARCHAR(60) NOT NULL,
	name_en VARCHAR(60) NOT NULL,
	quote_zh VARCHAR(120) NOT NULL,
	quote_en VARCHAR(120) NOT NULL,
	prompt_zh TEXT NOT NULL,
	prompt_en TEXT NOT NULL,
	swatch_file VARCHAR(300),
	sort_order INTEGER NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id)
);

-- handdraw_library_meta
CREATE TABLE IF NOT EXISTS handdraw_library_meta (
	id VARCHAR(32) NOT NULL,
	library_key VARCHAR(64) NOT NULL,
	version VARCHAR(32) NOT NULL,
	source_hash VARCHAR(64) NOT NULL,
	imported_at TIMESTAMP WITH TIME ZONE NOT NULL,
	style_count INTEGER NOT NULL,
	color_count INTEGER NOT NULL,
	reference_count INTEGER NOT NULL,
	missing_references JSON NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id)
);

-- handdraw_styles
CREATE TABLE IF NOT EXISTS handdraw_styles (
	id VARCHAR(32) NOT NULL,
	style_number VARCHAR(16) NOT NULL,
	legacy_number VARCHAR(8) NOT NULL,
	group_key VARCHAR(8) NOT NULL,
	group_name VARCHAR(120) NOT NULL,
	reference_author VARCHAR(190) NOT NULL,
	generation_name VARCHAR(190) NOT NULL,
	traits_raw TEXT NOT NULL,
	reference_file VARCHAR(300),
	reference_kind VARCHAR(16) NOT NULL,
	reference_available BOOLEAN NOT NULL,
	sort_order INTEGER NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id)
);

-- hot_clusters
CREATE TABLE IF NOT EXISTS hot_clusters (
	id VARCHAR(32) NOT NULL,
	fingerprint VARCHAR(120) NOT NULL,
	title_norm VARCHAR(300) NOT NULL,
	title VARCHAR(300) NOT NULL,
	track VARCHAR(32) NOT NULL,
	first_seen_day VARCHAR(10) NOT NULL,
	last_seen_day VARCHAR(10) NOT NULL,
	days_seen INTEGER NOT NULL,
	best_rank INTEGER,
	platform_count INTEGER NOT NULL,
	item_count INTEGER NOT NULL,
	platforms JSON NOT NULL,
	score INTEGER NOT NULL,
	grade VARCHAR(16) NOT NULL,
	score_detail JSON NOT NULL,
	risk_flags JSON NOT NULL,
	status VARCHAR(16) NOT NULL,
	captured_at VARCHAR(16) NOT NULL,
	content_item_id VARCHAR(32),
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id)
);

-- hot_items
CREATE TABLE IF NOT EXISTS hot_items (
	id VARCHAR(32) NOT NULL,
	item_uid VARCHAR(255) NOT NULL,
	day VARCHAR(10) NOT NULL,
	platform VARCHAR(32) NOT NULL,
	rank INTEGER,
	title VARCHAR(300) NOT NULL,
	title_norm VARCHAR(300) NOT NULL,
	hot INTEGER,
	url TEXT NOT NULL,
	source_url TEXT NOT NULL,
	first_seen_at VARCHAR(16) NOT NULL,
	last_seen_at VARCHAR(16) NOT NULL,
	first_seen_day VARCHAR(10) NOT NULL,
	days_json JSON NOT NULL,
	seen_count INTEGER NOT NULL,
	prev_rank INTEGER,
	best_rank INTEGER,
	cluster_id VARCHAR(32),
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id)
);

-- hot_rank_history
CREATE TABLE IF NOT EXISTS hot_rank_history (
	id VARCHAR(32) NOT NULL,
	item_id VARCHAR(32) NOT NULL,
	captured_at VARCHAR(16) NOT NULL,
	rank INTEGER,
	hot INTEGER,
	PRIMARY KEY (id),
	CONSTRAINT uq_hot_rank_sample UNIQUE (item_id, captured_at)
);

-- image_styles
CREATE TABLE IF NOT EXISTS image_styles (
	id VARCHAR(32) NOT NULL,
	name VARCHAR(40) NOT NULL,
	purpose VARCHAR(16) NOT NULL,
	prompt TEXT NOT NULL,
	summary VARCHAR(120) DEFAULT '' NOT NULL,
	sort_order INTEGER DEFAULT '0' NOT NULL,
	enabled BOOLEAN DEFAULT 'true' NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT ck_image_styles_purpose CHECK (purpose IN ('base','cover','inline')),
	CONSTRAINT ck_image_styles_sort CHECK (sort_order >= 0 AND sort_order <= 9999)
);

-- permissions
CREATE TABLE IF NOT EXISTS permissions (
	code VARCHAR(64) NOT NULL,
	name VARCHAR(64) NOT NULL,
	module VARCHAR(32) NOT NULL,
	sort INTEGER DEFAULT '0' NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (code)
);

-- platform_access_tokens
CREATE TABLE IF NOT EXISTS platform_access_tokens (
	id VARCHAR(32) NOT NULL,
	platform VARCHAR(24) NOT NULL,
	app_id VARCHAR(64) NOT NULL,
	access_token_ciphertext TEXT NOT NULL,
	expires_at TIMESTAMP WITH TIME ZONE,
	refreshed_at TIMESTAMP WITH TIME ZONE,
	last_error VARCHAR(190),
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_platform_token UNIQUE (platform, app_id)
);

-- platform_model_settings
CREATE TABLE IF NOT EXISTS platform_model_settings (
	id VARCHAR(32) NOT NULL,
	default_text_model_id VARCHAR(32),
	default_image_model_id VARCHAR(32),
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id)
);

-- platform_sync_jobs
CREATE TABLE IF NOT EXISTS platform_sync_jobs (
	id VARCHAR(32) NOT NULL,
	connected_account_id VARCHAR(32) NOT NULL,
	sync_type VARCHAR(32) NOT NULL,
	cursor VARCHAR(190),
	status VARCHAR(24) NOT NULL,
	started_at TIMESTAMP WITH TIME ZONE,
	completed_at TIMESTAMP WITH TIME ZONE,
	error_code VARCHAR(64),
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id)
);

-- prompt_rules
CREATE TABLE IF NOT EXISTS prompt_rules (
	id VARCHAR(32) NOT NULL,
	key VARCHAR(64) NOT NULL,
	category VARCHAR(24) NOT NULL,
	title VARCHAR(120) NOT NULL,
	description TEXT NOT NULL,
	content TEXT NOT NULL,
	payload JSON NOT NULL,
	enabled BOOLEAN DEFAULT 'true' NOT NULL,
	source VARCHAR(16) DEFAULT 'factory' NOT NULL,
	version INTEGER DEFAULT '1' NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT ck_prompt_rules_category CHECK (category IN ('contract','system','outline','repair','tone_lexicon','workflow_note'))
);

-- revoked_tokens
CREATE TABLE IF NOT EXISTS revoked_tokens (
	jti VARCHAR(64) NOT NULL,
	owner_user_id VARCHAR(32) NOT NULL,
	expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (jti)
);

-- roles
CREATE TABLE IF NOT EXISTS roles (
	id VARCHAR(32) NOT NULL,
	role_key VARCHAR(48) NOT NULL,
	name VARCHAR(48) NOT NULL,
	description VARCHAR(200) NOT NULL,
	is_superadmin BOOLEAN DEFAULT 'false' NOT NULL,
	is_system BOOLEAN DEFAULT 'false' NOT NULL,
	sort INTEGER DEFAULT '0' NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT ck_roles_sort CHECK (sort >= 0)
);

-- similarity_cache
CREATE TABLE IF NOT EXISTS similarity_cache (
	id VARCHAR(32) NOT NULL,
	source_version_id VARCHAR(32) NOT NULL,
	target_version_id VARCHAR(32) NOT NULL,
	similarity_score FLOAT NOT NULL,
	matching_segments JSONB NOT NULL,
	tfidf_vector TEXT NOT NULL,
	expires_at TIMESTAMP WITH TIME ZONE,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id)
);

-- skill_registry_entries
CREATE TABLE IF NOT EXISTS skill_registry_entries (
	id VARCHAR(32) NOT NULL,
	source VARCHAR(120) NOT NULL,
	owner_user_id VARCHAR(32),
	skill_key VARCHAR(120) NOT NULL,
	category VARCHAR(24) NOT NULL,
	version VARCHAR(32) NOT NULL,
	display_name VARCHAR(160) NOT NULL,
	description TEXT NOT NULL,
	manifest JSON NOT NULL,
	checksum VARCHAR(80) NOT NULL,
	trust_status VARCHAR(24) NOT NULL,
	allowed_tool_ids JSON NOT NULL,
	risk_level VARCHAR(24) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_skill_owner_key_plain UNIQUE (skill_key, owner_user_id)
);

-- users
CREATE TABLE IF NOT EXISTS users (
	id VARCHAR(32) NOT NULL,
	auth_subject VARCHAR(190) NOT NULL,
	display_name VARCHAR(120) NOT NULL,
	timezone VARCHAR(64) NOT NULL,
	email VARCHAR(190),
	password_hash VARCHAR(255),
	password_changed_at TIMESTAMP WITH TIME ZONE,
	last_login_at TIMESTAMP WITH TIME ZONE,
	failed_login_attempts INTEGER DEFAULT '0' NOT NULL,
	locked_until TIMESTAMP WITH TIME ZONE,
	is_active BOOLEAN DEFAULT '1' NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id)
);

-- agent_provider_connections
CREATE TABLE IF NOT EXISTS agent_provider_connections (
	id VARCHAR(32) NOT NULL,
	owner_user_id VARCHAR(32) NOT NULL,
	provider VARCHAR(48) NOT NULL,
	credential_ciphertext TEXT NOT NULL,
	key_version INTEGER NOT NULL,
	status VARCHAR(24) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(owner_user_id) REFERENCES users (id)
);

-- ai_generations
CREATE TABLE IF NOT EXISTS ai_generations (
	id VARCHAR(32) NOT NULL,
	owner_user_id VARCHAR(32) NOT NULL,
	task_type VARCHAR(48) NOT NULL,
	model_id VARCHAR(32),
	status VARCHAR(24) NOT NULL,
	request_summary JSON NOT NULL,
	prompt_version VARCHAR(32) NOT NULL,
	duration_ms INTEGER,
	token_counts JSON NOT NULL,
	error_code VARCHAR(64),
	result_text TEXT NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(owner_user_id) REFERENCES users (id)
);

-- author_skills
CREATE TABLE IF NOT EXISTS author_skills (
	id VARCHAR(32) NOT NULL,
	owner_user_id VARCHAR(32) NOT NULL,
	name VARCHAR(80) NOT NULL,
	skill_type VARCHAR(16) NOT NULL,
	description TEXT NOT NULL,
	summary TEXT NOT NULL,
	config JSONB NOT NULL,
	source VARCHAR(16) NOT NULL,
	status VARCHAR(16) NOT NULL,
	version INTEGER NOT NULL,
	usage_count INTEGER NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_author_skills_owner_name UNIQUE (owner_user_id, name),
	FOREIGN KEY(owner_user_id) REFERENCES users (id) ON DELETE CASCADE
);

-- background_jobs
CREATE TABLE IF NOT EXISTS background_jobs (
	id VARCHAR(32) NOT NULL,
	owner_user_id VARCHAR(32) NOT NULL,
	queue VARCHAR(48) NOT NULL,
	kind VARCHAR(64) NOT NULL,
	payload JSON NOT NULL,
	status VARCHAR(24) NOT NULL,
	progress INTEGER NOT NULL,
	result JSON NOT NULL,
	error_code VARCHAR(64),
	error_message TEXT,
	attempt_count INTEGER NOT NULL,
	started_at TIMESTAMP WITH TIME ZONE,
	completed_at TIMESTAMP WITH TIME ZONE,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(owner_user_id) REFERENCES users (id)
);

-- card_templates
CREATE TABLE IF NOT EXISTS card_templates (
	id VARCHAR(32) NOT NULL,
	owner_user_id VARCHAR(32) NOT NULL,
	name VARCHAR(120) NOT NULL,
	subtitle VARCHAR(200) NOT NULL,
	config JSON NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(owner_user_id) REFERENCES users (id)
);

-- characters
CREATE TABLE IF NOT EXISTS characters (
	id VARCHAR(32) NOT NULL,
	owner_user_id VARCHAR(32) NOT NULL,
	name VARCHAR(120) NOT NULL,
	aliases JSON NOT NULL,
	appearance JSON NOT NULL,
	images JSON NOT NULL,
	pending_images JSON NOT NULL,
	notes TEXT NOT NULL,
	deleted_at TIMESTAMP WITH TIME ZONE,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(owner_user_id) REFERENCES users (id)
);

-- image_jobs
CREATE TABLE IF NOT EXISTS image_jobs (
	id VARCHAR(32) NOT NULL,
	owner_user_id VARCHAR(32) NOT NULL,
	asset_id VARCHAR(32) NOT NULL,
	operation VARCHAR(32) NOT NULL,
	options JSON NOT NULL,
	status VARCHAR(24) NOT NULL,
	progress INTEGER NOT NULL,
	output_asset_ids JSON NOT NULL,
	error_code VARCHAR(64),
	started_at TIMESTAMP WITH TIME ZONE,
	completed_at TIMESTAMP WITH TIME ZONE,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(owner_user_id) REFERENCES users (id)
);

-- menus
CREATE TABLE IF NOT EXISTS menus (
	id VARCHAR(32) NOT NULL,
	parent_id VARCHAR(32),
	menu_key VARCHAR(64) NOT NULL,
	label VARCHAR(48) NOT NULL,
	short_label VARCHAR(24) NOT NULL,
	icon VARCHAR(32) NOT NULL,
	path VARCHAR(200) NOT NULL,
	kind VARCHAR(16) DEFAULT 'page' NOT NULL,
	permission_code VARCHAR(64),
	sort INTEGER DEFAULT '0' NOT NULL,
	visible BOOLEAN DEFAULT 'true' NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT ck_menus_kind CHECK (kind IN ('group', 'page', 'link')),
	CONSTRAINT ck_menus_sort CHECK (sort >= 0),
	FOREIGN KEY(parent_id) REFERENCES menus (id) ON DELETE RESTRICT,
	FOREIGN KEY(permission_code) REFERENCES permissions (code) ON DELETE RESTRICT
);

-- metric_snapshots
CREATE TABLE IF NOT EXISTS metric_snapshots (
	id VARCHAR(32) NOT NULL,
	owner_user_id VARCHAR(32) NOT NULL,
	connected_account_id VARCHAR(32) NOT NULL,
	platform VARCHAR(24) NOT NULL,
	remote_content_id VARCHAR(190),
	captured_at TIMESTAMP WITH TIME ZONE NOT NULL,
	normalized_metrics JSON NOT NULL,
	raw_metrics JSON NOT NULL,
	source VARCHAR(24) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(owner_user_id) REFERENCES users (id)
);

-- model_connections
CREATE TABLE IF NOT EXISTS model_connections (
	id VARCHAR(32) NOT NULL,
	owner_user_id VARCHAR(32) NOT NULL,
	provider_type VARCHAR(48) NOT NULL,
	display_name VARCHAR(120) NOT NULL,
	base_url VARCHAR(300) NOT NULL,
	credential_ciphertext TEXT NOT NULL,
	key_version INTEGER NOT NULL,
	status VARCHAR(24) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(owner_user_id) REFERENCES users (id)
);

-- prompt_templates
CREATE TABLE IF NOT EXISTS prompt_templates (
	id VARCHAR(32) NOT NULL,
	owner_user_id VARCHAR(32),
	title VARCHAR(120) NOT NULL,
	category VARCHAR(32) NOT NULL,
	content TEXT NOT NULL,
	tags JSON NOT NULL,
	prompt_type VARCHAR(16) DEFAULT 'normal' NOT NULL,
	negative_content TEXT DEFAULT '' NOT NULL,
	summary VARCHAR(120) DEFAULT '' NOT NULL,
	sort_order INTEGER DEFAULT '0' NOT NULL,
	enabled BOOLEAN DEFAULT 'true' NOT NULL,
	preview_asset_ids JSON NOT NULL,
	source VARCHAR(16) DEFAULT 'manual' NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT ck_prompt_templates_type CHECK (prompt_type IN ('normal','cover','illustration','base','comic_panel','reference')),
	CONSTRAINT ck_prompt_templates_source CHECK (source IN ('manual','ai','factory')),
	FOREIGN KEY(owner_user_id) REFERENCES users (id)
);

-- rbac_audit_logs
CREATE TABLE IF NOT EXISTS rbac_audit_logs (
	id VARCHAR(32) NOT NULL,
	actor_user_id VARCHAR(32),
	action VARCHAR(48) NOT NULL,
	target_type VARCHAR(24) NOT NULL,
	target_id VARCHAR(64) NOT NULL,
	detail JSONB NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(actor_user_id) REFERENCES users (id) ON DELETE SET NULL
);

-- role_permissions
CREATE TABLE IF NOT EXISTS role_permissions (
	role_id VARCHAR(32) NOT NULL,
	permission_code VARCHAR(64) NOT NULL,
	PRIMARY KEY (role_id, permission_code),
	FOREIGN KEY(role_id) REFERENCES roles (id) ON DELETE CASCADE,
	FOREIGN KEY(permission_code) REFERENCES permissions (code) ON DELETE CASCADE
);

-- scheduled_scripts
CREATE TABLE IF NOT EXISTS scheduled_scripts (
	id VARCHAR(32) NOT NULL,
	owner_user_id VARCHAR(32) NOT NULL,
	name VARCHAR(120) NOT NULL,
	description VARCHAR(500) DEFAULT '' NOT NULL,
	draft_code TEXT DEFAULT '' NOT NULL,
	published_code TEXT DEFAULT '' NOT NULL,
	published_version INTEGER DEFAULT '0' NOT NULL,
	published_at TIMESTAMP WITH TIME ZONE,
	published_hash VARCHAR(64) DEFAULT '' NOT NULL,
	timeout_seconds INTEGER DEFAULT '60' NOT NULL,
	enabled BOOLEAN DEFAULT 'true' NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_scheduled_script_owner_name UNIQUE (owner_user_id, name),
	FOREIGN KEY(owner_user_id) REFERENCES users (id)
);

-- scheduled_tasks
CREATE TABLE IF NOT EXISTS scheduled_tasks (
	id VARCHAR(32) NOT NULL,
	scope VARCHAR(16) NOT NULL,
	owner_user_id VARCHAR(32) NOT NULL,
	task_type VARCHAR(64) NOT NULL,
	name VARCHAR(120) NOT NULL,
	params JSON NOT NULL,
	schedule_kind VARCHAR(16) NOT NULL,
	cron VARCHAR(120) NOT NULL,
	interval_seconds INTEGER,
	timezone VARCHAR(64) NOT NULL,
	misfire_grace_seconds INTEGER NOT NULL,
	enabled BOOLEAN NOT NULL,
	next_run_at TIMESTAMP WITH TIME ZONE,
	last_fired_at TIMESTAMP WITH TIME ZONE,
	last_status VARCHAR(24) NOT NULL,
	consecutive_failures INTEGER NOT NULL,
	paused_reason VARCHAR(300) NOT NULL,
	active_job_id VARCHAR(32),
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(owner_user_id) REFERENCES users (id)
);

-- skill_distillation_jobs
CREATE TABLE IF NOT EXISTS skill_distillation_jobs (
	id VARCHAR(32) NOT NULL,
	skill_entry_id VARCHAR(32) NOT NULL,
	owner_user_id VARCHAR(32) NOT NULL,
	distillation_type VARCHAR(32) NOT NULL,
	tier VARCHAR(32) NOT NULL,
	source_data JSONB NOT NULL,
	config JSONB NOT NULL,
	status VARCHAR(32) NOT NULL,
	progress INTEGER NOT NULL,
	result JSONB NOT NULL,
	error_message TEXT,
	duration_seconds INTEGER,
	started_at TIMESTAMP WITH TIME ZONE,
	completed_at TIMESTAMP WITH TIME ZONE,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(skill_entry_id) REFERENCES skill_registry_entries (id) ON DELETE CASCADE,
	FOREIGN KEY(owner_user_id) REFERENCES users (id) ON DELETE CASCADE
);

-- skill_usage_logs
CREATE TABLE IF NOT EXISTS skill_usage_logs (
	id VARCHAR(32) NOT NULL,
	skill_entry_id VARCHAR(32),
	owner_user_id VARCHAR(32) NOT NULL,
	context JSONB NOT NULL,
	result_summary TEXT NOT NULL,
	rating INTEGER,
	feedback TEXT,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(skill_entry_id) REFERENCES skill_registry_entries (id) ON DELETE SET NULL,
	FOREIGN KEY(owner_user_id) REFERENCES users (id) ON DELETE CASCADE
);

-- user_preferences
CREATE TABLE IF NOT EXISTS user_preferences (
	id VARCHAR(32) NOT NULL,
	owner_user_id VARCHAR(32) NOT NULL,
	theme VARCHAR(16) NOT NULL,
	default_text_model_id VARCHAR(32),
	default_image_model_id VARCHAR(32),
	studio_image_preferences JSON NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(owner_user_id) REFERENCES users (id)
);

-- user_roles
CREATE TABLE IF NOT EXISTS user_roles (
	user_id VARCHAR(32) NOT NULL,
	role_id VARCHAR(32) NOT NULL,
	PRIMARY KEY (user_id, role_id),
	FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE,
	FOREIGN KEY(role_id) REFERENCES roles (id) ON DELETE CASCADE
);

-- workspaces
CREATE TABLE IF NOT EXISTS workspaces (
	id VARCHAR(32) NOT NULL,
	owner_user_id VARCHAR(32) NOT NULL,
	name VARCHAR(120) NOT NULL,
	settings JSON NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(owner_user_id) REFERENCES users (id)
);

-- agent_sessions
CREATE TABLE IF NOT EXISTS agent_sessions (
	id VARCHAR(32) NOT NULL,
	owner_user_id VARCHAR(32) NOT NULL,
	workspace_id VARCHAR(32) NOT NULL,
	provider_id VARCHAR(32),
	title VARCHAR(200) NOT NULL,
	status VARCHAR(24) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(owner_user_id) REFERENCES users (id),
	FOREIGN KEY(workspace_id) REFERENCES workspaces (id)
);

-- analysis_tasks
CREATE TABLE IF NOT EXISTS analysis_tasks (
	id VARCHAR(32) NOT NULL,
	workspace_id VARCHAR(32) NOT NULL,
	created_by_user_id VARCHAR(32),
	task_type VARCHAR(16) NOT NULL,
	target_ids JSONB NOT NULL,
	analysis_params JSONB NOT NULL,
	status VARCHAR(16) NOT NULL,
	progress INTEGER NOT NULL,
	completed_analysis_ids JSONB NOT NULL,
	error_message TEXT,
	estimated_completion_at TIMESTAMP WITH TIME ZONE,
	completed_at TIMESTAMP WITH TIME ZONE,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(workspace_id) REFERENCES workspaces (id),
	FOREIGN KEY(created_by_user_id) REFERENCES users (id) ON DELETE SET NULL
);

-- assets
CREATE TABLE IF NOT EXISTS assets (
	id VARCHAR(32) NOT NULL,
	owner_user_id VARCHAR(32) NOT NULL,
	workspace_id VARCHAR(32) NOT NULL,
	storage_key VARCHAR(400) NOT NULL,
	original_name VARCHAR(240) NOT NULL,
	mime_type VARCHAR(120) NOT NULL,
	size_bytes INTEGER NOT NULL,
	width INTEGER,
	height INTEGER,
	source_type VARCHAR(24) NOT NULL,
	prompt TEXT,
	license_note TEXT,
	checksum VARCHAR(80),
	parent_asset_id VARCHAR(32),
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(owner_user_id) REFERENCES users (id),
	FOREIGN KEY(workspace_id) REFERENCES workspaces (id)
);

-- author_skill_distill_runs
CREATE TABLE IF NOT EXISTS author_skill_distill_runs (
	id VARCHAR(32) NOT NULL,
	skill_id VARCHAR(32) NOT NULL,
	owner_user_id VARCHAR(32) NOT NULL,
	skill_type VARCHAR(16) NOT NULL,
	status VARCHAR(16) NOT NULL,
	progress INTEGER NOT NULL,
	material_stats JSONB NOT NULL,
	request JSONB NOT NULL,
	result JSONB NOT NULL,
	error TEXT,
	started_at TIMESTAMP WITH TIME ZONE,
	completed_at TIMESTAMP WITH TIME ZONE,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(skill_id) REFERENCES author_skills (id) ON DELETE CASCADE,
	FOREIGN KEY(owner_user_id) REFERENCES users (id) ON DELETE CASCADE
);

-- author_skill_revisions
CREATE TABLE IF NOT EXISTS author_skill_revisions (
	id VARCHAR(32) NOT NULL,
	skill_id VARCHAR(32) NOT NULL,
	owner_user_id VARCHAR(32) NOT NULL,
	version INTEGER NOT NULL,
	summary TEXT NOT NULL,
	config JSONB NOT NULL,
	source VARCHAR(16) NOT NULL,
	note VARCHAR(200) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(skill_id) REFERENCES author_skills (id) ON DELETE CASCADE,
	FOREIGN KEY(owner_user_id) REFERENCES users (id) ON DELETE CASCADE
);

-- calendar_entries
CREATE TABLE IF NOT EXISTS calendar_entries (
	id VARCHAR(32) NOT NULL,
	workspace_id VARCHAR(32) NOT NULL,
	owner_user_id VARCHAR(32) NOT NULL,
	content_item_id VARCHAR(32),
	studio_article_id VARCHAR(32),
	platform VARCHAR(24),
	title VARCHAR(200) NOT NULL,
	planned_at TIMESTAMP WITH TIME ZONE NOT NULL,
	status VARCHAR(24) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(workspace_id) REFERENCES workspaces (id),
	FOREIGN KEY(owner_user_id) REFERENCES users (id)
);

-- connected_accounts
CREATE TABLE IF NOT EXISTS connected_accounts (
	id VARCHAR(32) NOT NULL,
	owner_user_id VARCHAR(32) NOT NULL,
	workspace_id VARCHAR(32) NOT NULL,
	platform VARCHAR(24) NOT NULL,
	platform_subject VARCHAR(190) NOT NULL,
	display_name VARCHAR(190) NOT NULL,
	scopes JSON NOT NULL,
	access_token_ciphertext TEXT NOT NULL,
	refresh_token_ciphertext TEXT NOT NULL,
	expires_at TIMESTAMP WITH TIME ZONE,
	status VARCHAR(32) NOT NULL,
	auto_publish_enabled BOOLEAN NOT NULL,
	last_synced_at TIMESTAMP WITH TIME ZONE,
	auth_source VARCHAR(24) NOT NULL,
	app_id VARCHAR(64) NOT NULL,
	app_secret_ciphertext TEXT NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(owner_user_id) REFERENCES users (id),
	FOREIGN KEY(workspace_id) REFERENCES workspaces (id)
);

-- content_items
CREATE TABLE IF NOT EXISTS content_items (
	id VARCHAR(32) NOT NULL,
	workspace_id VARCHAR(32) NOT NULL,
	owner_user_id VARCHAR(32) NOT NULL,
	title VARCHAR(200) NOT NULL,
	type VARCHAR(32) NOT NULL,
	status VARCHAR(24) NOT NULL,
	current_version_id VARCHAR(32),
	draft_title VARCHAR(200),
	draft_body TEXT,
	archived BOOLEAN NOT NULL,
	archived_at TIMESTAMP WITH TIME ZONE,
	base_style_id VARCHAR(32),
	base_style_name VARCHAR(40) DEFAULT '' NOT NULL,
	base_style_prompt TEXT DEFAULT '' NOT NULL,
	base_style_deleted BOOLEAN DEFAULT 'false' NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(workspace_id) REFERENCES workspaces (id),
	FOREIGN KEY(owner_user_id) REFERENCES users (id)
);

-- model_catalog_entries
CREATE TABLE IF NOT EXISTS model_catalog_entries (
	id VARCHAR(32) NOT NULL,
	connection_id VARCHAR(32) NOT NULL,
	model_code VARCHAR(120) NOT NULL,
	display_name VARCHAR(120) NOT NULL,
	capabilities JSON NOT NULL,
	enabled BOOLEAN NOT NULL,
	supports_streaming BOOLEAN NOT NULL,
	options JSON NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(connection_id) REFERENCES model_connections (id)
);

-- novel_projects
CREATE TABLE IF NOT EXISTS novel_projects (
	id VARCHAR(32) NOT NULL,
	owner_user_id VARCHAR(32) NOT NULL,
	workspace_id VARCHAR(32) NOT NULL,
	title VARCHAR(200) NOT NULL,
	source_text TEXT NOT NULL,
	rights_confirmed_at TIMESTAMP WITH TIME ZONE,
	rules JSON NOT NULL,
	status VARCHAR(16) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(owner_user_id) REFERENCES users (id),
	FOREIGN KEY(workspace_id) REFERENCES workspaces (id)
);

-- publish_jobs
CREATE TABLE IF NOT EXISTS publish_jobs (
	id VARCHAR(32) NOT NULL,
	owner_user_id VARCHAR(32) NOT NULL,
	workspace_id VARCHAR(32) NOT NULL,
	platform_variant_id VARCHAR(32),
	content_item_id VARCHAR(32),
	connected_account_id VARCHAR(32) NOT NULL,
	platform VARCHAR(24) NOT NULL,
	target VARCHAR(24) NOT NULL,
	scheduled_at TIMESTAMP WITH TIME ZONE,
	idempotency_key VARCHAR(120) NOT NULL,
	status VARCHAR(32) NOT NULL,
	status_history JSON NOT NULL,
	attempt_count INTEGER NOT NULL,
	remote_content_id VARCHAR(190),
	error_code VARCHAR(64),
	error_message TEXT,
	request_id VARCHAR(64) NOT NULL,
	reviewed_by VARCHAR(32),
	payload_snapshot JSON NOT NULL,
	layout_snapshot JSON NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_publish_idempotency UNIQUE (owner_user_id, idempotency_key),
	FOREIGN KEY(owner_user_id) REFERENCES users (id),
	FOREIGN KEY(workspace_id) REFERENCES workspaces (id)
);

-- role_menus
CREATE TABLE IF NOT EXISTS role_menus (
	role_id VARCHAR(32) NOT NULL,
	menu_id VARCHAR(32) NOT NULL,
	PRIMARY KEY (role_id, menu_id),
	FOREIGN KEY(role_id) REFERENCES roles (id) ON DELETE CASCADE,
	FOREIGN KEY(menu_id) REFERENCES menus (id) ON DELETE CASCADE
);

-- scheduled_script_versions
CREATE TABLE IF NOT EXISTS scheduled_script_versions (
	id VARCHAR(32) NOT NULL,
	script_id VARCHAR(32) NOT NULL,
	version INTEGER NOT NULL,
	code TEXT NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_scheduled_script_version UNIQUE (script_id, version),
	FOREIGN KEY(script_id) REFERENCES scheduled_scripts (id)
);

-- scheduled_task_runs
CREATE TABLE IF NOT EXISTS scheduled_task_runs (
	id VARCHAR(32) NOT NULL,
	task_id VARCHAR(32) NOT NULL,
	job_id VARCHAR(32),
	triggered_at TIMESTAMP WITH TIME ZONE NOT NULL,
	source VARCHAR(16) NOT NULL,
	status VARCHAR(24) NOT NULL,
	skip_reason VARCHAR(300) NOT NULL,
	error_code VARCHAR(64),
	error_message TEXT,
	duration_ms INTEGER,
	finished_at TIMESTAMP WITH TIME ZONE,
	PRIMARY KEY (id),
	FOREIGN KEY(task_id) REFERENCES scheduled_tasks (id)
);

-- sticker_sets
CREATE TABLE IF NOT EXISTS sticker_sets (
	id VARCHAR(32) NOT NULL,
	owner_user_id VARCHAR(32) NOT NULL,
	workspace_id VARCHAR(32) NOT NULL,
	title VARCHAR(200) NOT NULL,
	theme TEXT NOT NULL,
	style_id VARCHAR(40) NOT NULL,
	track VARCHAR(32),
	hot_cluster_id VARCHAR(32),
	narrative VARCHAR(24) NOT NULL,
	status VARCHAR(16) NOT NULL,
	deleted_at TIMESTAMP WITH TIME ZONE,
	meta_json JSON,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(owner_user_id) REFERENCES users (id),
	FOREIGN KEY(workspace_id) REFERENCES workspaces (id)
);

-- storyboard_projects
CREATE TABLE IF NOT EXISTS storyboard_projects (
	id VARCHAR(32) NOT NULL,
	owner_user_id VARCHAR(32) NOT NULL,
	workspace_id VARCHAR(32) NOT NULL,
	title VARCHAR(200) NOT NULL,
	summary TEXT NOT NULL,
	assets JSON NOT NULL,
	status VARCHAR(16) NOT NULL,
	style_config JSON NOT NULL,
	deleted_at TIMESTAMP WITH TIME ZONE,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(owner_user_id) REFERENCES users (id),
	FOREIGN KEY(workspace_id) REFERENCES workspaces (id)
);

-- week_reviews
CREATE TABLE IF NOT EXISTS week_reviews (
	id VARCHAR(32) NOT NULL,
	workspace_id VARCHAR(32) NOT NULL,
	owner_user_id VARCHAR(32) NOT NULL,
	week_start VARCHAR(10) NOT NULL,
	planned_count INTEGER NOT NULL,
	published_count INTEGER NOT NULL,
	learnings TEXT NOT NULL,
	blockers TEXT NOT NULL,
	next_week_focus TEXT NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(workspace_id) REFERENCES workspaces (id),
	FOREIGN KEY(owner_user_id) REFERENCES users (id)
);

-- workspace_skills
CREATE TABLE IF NOT EXISTS workspace_skills (
	id VARCHAR(32) NOT NULL,
	workspace_id VARCHAR(32) NOT NULL,
	skill_entry_id VARCHAR(32) NOT NULL,
	enabled BOOLEAN NOT NULL,
	granted_tool_ids JSON NOT NULL,
	approval_policy VARCHAR(24) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_workspace_skill UNIQUE (workspace_id, skill_entry_id),
	FOREIGN KEY(workspace_id) REFERENCES workspaces (id),
	FOREIGN KEY(skill_entry_id) REFERENCES skill_registry_entries (id)
);

-- agent_runs
CREATE TABLE IF NOT EXISTS agent_runs (
	id VARCHAR(32) NOT NULL,
	session_id VARCHAR(32) NOT NULL,
	owner_user_id VARCHAR(32) NOT NULL,
	workspace_id VARCHAR(32) NOT NULL,
	goal TEXT NOT NULL,
	status VARCHAR(24) NOT NULL,
	permission_snapshot JSON NOT NULL,
	budget_limit JSON NOT NULL,
	rounds_used INTEGER NOT NULL,
	started_at TIMESTAMP WITH TIME ZONE,
	completed_at TIMESTAMP WITH TIME ZONE,
	error_code VARCHAR(64),
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(session_id) REFERENCES agent_sessions (id),
	FOREIGN KEY(owner_user_id) REFERENCES users (id),
	FOREIGN KEY(workspace_id) REFERENCES workspaces (id)
);

-- content_versions
CREATE TABLE IF NOT EXISTS content_versions (
	id VARCHAR(32) NOT NULL,
	content_item_id VARCHAR(32) NOT NULL,
	version_no INTEGER NOT NULL,
	parent_version_id VARCHAR(32),
	source_type VARCHAR(24) NOT NULL,
	model_id VARCHAR(32),
	prompt_template_version VARCHAR(32),
	title VARCHAR(200) NOT NULL,
	body TEXT NOT NULL,
	summary TEXT NOT NULL,
	layout_options JSON DEFAULT '{}' NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_version_no UNIQUE (content_item_id, version_no),
	FOREIGN KEY(content_item_id) REFERENCES content_items (id)
);

-- novel_aliases
CREATE TABLE IF NOT EXISTS novel_aliases (
	id VARCHAR(32) NOT NULL,
	project_id VARCHAR(32) NOT NULL,
	kind VARCHAR(16) NOT NULL,
	original_name VARCHAR(120) NOT NULL,
	new_name VARCHAR(120) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_novel_alias UNIQUE (project_id, kind, original_name),
	FOREIGN KEY(project_id) REFERENCES novel_projects (id)
);

-- novel_chapters
CREATE TABLE IF NOT EXISTS novel_chapters (
	id VARCHAR(32) NOT NULL,
	project_id VARCHAR(32) NOT NULL,
	sort_order INTEGER NOT NULL,
	source VARCHAR(16) NOT NULL,
	title VARCHAR(200) NOT NULL,
	source_text TEXT NOT NULL,
	rewrite_text TEXT NOT NULL,
	draft_text TEXT NOT NULL,
	accepted_text TEXT NOT NULL,
	draft_version INTEGER NOT NULL,
	status VARCHAR(16) NOT NULL,
	facts JSON NOT NULL,
	stale BOOLEAN NOT NULL,
	impact JSON NOT NULL,
	pending_candidate TEXT,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_novel_chapter_order UNIQUE (project_id, sort_order),
	FOREIGN KEY(project_id) REFERENCES novel_projects (id)
);

-- novel_characters
CREATE TABLE IF NOT EXISTS novel_characters (
	id VARCHAR(32) NOT NULL,
	project_id VARCHAR(32) NOT NULL,
	original_name VARCHAR(120) NOT NULL,
	new_name VARCHAR(120) NOT NULL,
	relations TEXT NOT NULL,
	speech_style TEXT NOT NULL,
	state VARCHAR(24) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(project_id) REFERENCES novel_projects (id)
);

-- platform_variants
CREATE TABLE IF NOT EXISTS platform_variants (
	id VARCHAR(32) NOT NULL,
	content_item_id VARCHAR(32) NOT NULL,
	base_version_id VARCHAR(32),
	platform VARCHAR(24) NOT NULL,
	revision_no INTEGER NOT NULL,
	title VARCHAR(200) NOT NULL,
	body TEXT NOT NULL,
	tags JSON NOT NULL,
	status VARCHAR(24) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(content_item_id) REFERENCES content_items (id)
);

-- publish_records
CREATE TABLE IF NOT EXISTS publish_records (
	id VARCHAR(32) NOT NULL,
	publish_job_id VARCHAR(32) NOT NULL,
	platform VARCHAR(24) NOT NULL,
	remote_content_id VARCHAR(190),
	published_at TIMESTAMP WITH TIME ZONE,
	permalink VARCHAR(500),
	result_status VARCHAR(32) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(publish_job_id) REFERENCES publish_jobs (id)
);

-- review_comments
CREATE TABLE IF NOT EXISTS review_comments (
	id VARCHAR(32) NOT NULL,
	content_item_id VARCHAR(32) NOT NULL,
	author_user_id VARCHAR(32) NOT NULL,
	body TEXT NOT NULL,
	resolved BOOLEAN NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(content_item_id) REFERENCES content_items (id)
);

-- sticker_images
CREATE TABLE IF NOT EXISTS sticker_images (
	id VARCHAR(32) NOT NULL,
	set_id VARCHAR(32) NOT NULL,
	sort_order INTEGER NOT NULL,
	caption TEXT NOT NULL,
	frame_prompt TEXT NOT NULL,
	slot_type VARCHAR(8) NOT NULL,
	asset_id VARCHAR(32),
	job_id VARCHAR(32),
	status VARCHAR(16) NOT NULL,
	error_code VARCHAR(40),
	generated_prompt TEXT,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(set_id) REFERENCES sticker_sets (id)
);

-- storyboard_scenes
CREATE TABLE IF NOT EXISTS storyboard_scenes (
	id VARCHAR(32) NOT NULL,
	project_id VARCHAR(32) NOT NULL,
	sort_order INTEGER NOT NULL,
	title VARCHAR(200) NOT NULL,
	summary TEXT NOT NULL,
	chapter VARCHAR(200) NOT NULL,
	style_config JSON NOT NULL,
	deleted_at TIMESTAMP WITH TIME ZONE,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(project_id) REFERENCES storyboard_projects (id)
);

-- studio_articles
CREATE TABLE IF NOT EXISTS studio_articles (
	id VARCHAR(32) NOT NULL,
	owner_user_id VARCHAR(32) NOT NULL,
	workspace_id VARCHAR(32),
	content_item_id VARCHAR(32),
	topic VARCHAR(400) NOT NULL,
	material TEXT NOT NULL,
	writer_style VARCHAR(32) DEFAULT 'tutorial' NOT NULL,
	front TEXT NOT NULL,
	title VARCHAR(200) NOT NULL,
	hook VARCHAR(8),
	titles JSONB NOT NULL,
	summary TEXT NOT NULL,
	banner_style VARCHAR(24) NOT NULL,
	banners JSONB NOT NULL,
	layout_options JSONB NOT NULL,
	follow_card BOOLEAN DEFAULT 'false' NOT NULL,
	zhuque JSONB NOT NULL,
	chat JSONB NOT NULL,
	notice JSONB,
	cover JSONB NOT NULL,
	inline_diagrams JSONB NOT NULL,
	scheduled_for TIMESTAMP WITH TIME ZONE,
	published_at TIMESTAMP WITH TIME ZONE,
	publish_marked_at TIMESTAMP WITH TIME ZONE,
	archived BOOLEAN DEFAULT 'false' NOT NULL,
	archived_at TIMESTAMP WITH TIME ZONE,
	last_view VARCHAR(16) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(owner_user_id) REFERENCES users (id) ON DELETE CASCADE,
	FOREIGN KEY(workspace_id) REFERENCES workspaces (id) ON DELETE SET NULL,
	FOREIGN KEY(content_item_id) REFERENCES content_items (id) ON DELETE SET NULL
);

-- agent_approvals
CREATE TABLE IF NOT EXISTS agent_approvals (
	id VARCHAR(32) NOT NULL,
	run_id VARCHAR(32) NOT NULL,
	tool_call_id VARCHAR(32),
	requested_action VARCHAR(120) NOT NULL,
	risk_level VARCHAR(24) NOT NULL,
	parameter_digest VARCHAR(120) NOT NULL,
	params JSON NOT NULL,
	status VARCHAR(24) NOT NULL,
	decided_by VARCHAR(32),
	decided_at TIMESTAMP WITH TIME ZONE,
	expires_at TIMESTAMP WITH TIME ZONE,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(run_id) REFERENCES agent_runs (id)
);

-- agent_events
CREATE TABLE IF NOT EXISTS agent_events (
	id VARCHAR(32) NOT NULL,
	run_id VARCHAR(32) NOT NULL,
	seq INTEGER NOT NULL,
	event_type VARCHAR(48) NOT NULL,
	safe_payload JSON NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_agent_event_seq UNIQUE (run_id, seq),
	FOREIGN KEY(run_id) REFERENCES agent_runs (id)
);

-- agent_tool_calls
CREATE TABLE IF NOT EXISTS agent_tool_calls (
	id VARCHAR(32) NOT NULL,
	run_id VARCHAR(32) NOT NULL,
	tool_id VARCHAR(64) NOT NULL,
	input_summary JSON NOT NULL,
	status VARCHAR(24) NOT NULL,
	approval_id VARCHAR(32),
	result_ref JSON NOT NULL,
	error_code VARCHAR(64),
	started_at TIMESTAMP WITH TIME ZONE,
	completed_at TIMESTAMP WITH TIME ZONE,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(run_id) REFERENCES agent_runs (id)
);

-- article_blocks
CREATE TABLE IF NOT EXISTS article_blocks (
	id VARCHAR(32) NOT NULL,
	article_id VARCHAR(32) NOT NULL,
	ordinal INTEGER NOT NULL,
	text TEXT NOT NULL,
	kind VARCHAR(16) NOT NULL,
	"check" JSONB,
	prev_text TEXT,
	inserted_diagram_id VARCHAR(32),
	PRIMARY KEY (id),
	FOREIGN KEY(article_id) REFERENCES studio_articles (id) ON DELETE CASCADE
);

-- article_revisions
CREATE TABLE IF NOT EXISTS article_revisions (
	id VARCHAR(32) NOT NULL,
	article_id VARCHAR(32) NOT NULL,
	label VARCHAR(120) NOT NULL,
	payload JSONB NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(article_id) REFERENCES studio_articles (id) ON DELETE CASCADE
);

-- content_assets
CREATE TABLE IF NOT EXISTS content_assets (
	content_version_id VARCHAR(32) NOT NULL,
	asset_id VARCHAR(32) NOT NULL,
	usage_role VARCHAR(32) NOT NULL,
	sort_order INTEGER NOT NULL,
	PRIMARY KEY (content_version_id, asset_id),
	FOREIGN KEY(content_version_id) REFERENCES content_versions (id),
	FOREIGN KEY(asset_id) REFERENCES assets (id)
);

-- content_embeddings
CREATE TABLE IF NOT EXISTS content_embeddings (
	id VARCHAR(32) NOT NULL,
	content_version_id VARCHAR(32) NOT NULL,
	model_version VARCHAR(32) NOT NULL,
	embedding_vector TEXT NOT NULL,
	embedding_dim INTEGER NOT NULL,
	deprecated BOOLEAN NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(content_version_id) REFERENCES content_versions (id)
);

-- originality_analyses
CREATE TABLE IF NOT EXISTS originality_analyses (
	id VARCHAR(32) NOT NULL,
	content_version_id VARCHAR(32) NOT NULL,
	workspace_id VARCHAR(32) NOT NULL,
	analyzed_at TIMESTAMP WITH TIME ZONE NOT NULL,
	similarity_score INTEGER NOT NULL,
	originality_score INTEGER NOT NULL,
	structure_score INTEGER NOT NULL,
	data_accuracy_score INTEGER NOT NULL,
	style_uniqueness_score INTEGER NOT NULL,
	total_score INTEGER NOT NULL,
	level VARCHAR(20) NOT NULL,
	similar_articles JSONB NOT NULL,
	key_points JSONB NOT NULL,
	risk_flags JSONB NOT NULL,
	recommendations TEXT NOT NULL,
	compare_scope VARCHAR(20) NOT NULL,
	model_version VARCHAR(50) NOT NULL,
	body_hash VARCHAR(64) DEFAULT '' NOT NULL,
	corpus_size INTEGER DEFAULT '0' NOT NULL,
	self_version_count INTEGER DEFAULT '0' NOT NULL,
	summary TEXT DEFAULT '' NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(content_version_id) REFERENCES content_versions (id),
	FOREIGN KEY(workspace_id) REFERENCES workspaces (id)
);

-- pet_conversations
CREATE TABLE IF NOT EXISTS pet_conversations (
	id VARCHAR(32) NOT NULL,
	studio_article_id VARCHAR(32) NOT NULL,
	owner_user_id VARCHAR(32) NOT NULL,
	messages JSONB NOT NULL,
	active_skill_ids JSON NOT NULL,
	last_generation JSONB NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(studio_article_id) REFERENCES studio_articles (id) ON DELETE CASCADE,
	FOREIGN KEY(owner_user_id) REFERENCES users (id) ON DELETE CASCADE
);

-- storyboard_shots
CREATE TABLE IF NOT EXISTS storyboard_shots (
	id VARCHAR(32) NOT NULL,
	scene_id VARCHAR(32) NOT NULL,
	sort_order INTEGER NOT NULL,
	frame_description TEXT NOT NULL,
	action TEXT NOT NULL,
	dialogue TEXT NOT NULL,
	shot_size VARCHAR(24) NOT NULL,
	view_angle VARCHAR(24) NOT NULL,
	camera_motion VARCHAR(24) NOT NULL,
	duration_seconds INTEGER NOT NULL,
	prompt TEXT NOT NULL,
	asset_refs JSON NOT NULL,
	sketch_asset_id VARCHAR(32),
	sketch_job_id VARCHAR(32),
	sketch_status VARCHAR(16) NOT NULL,
	sketch_error TEXT,
	final_asset_ids JSON NOT NULL,
	final_job_id VARCHAR(32),
	final_status VARCHAR(16) NOT NULL,
	final_error TEXT,
	generation_version INTEGER NOT NULL,
	deleted_at TIMESTAMP WITH TIME ZONE,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(scene_id) REFERENCES storyboard_scenes (id)
);

-- analysis_history
CREATE TABLE IF NOT EXISTS analysis_history (
	id VARCHAR(32) NOT NULL,
	content_version_id VARCHAR(32) NOT NULL,
	analysis_id VARCHAR(32) NOT NULL,
	revision INTEGER NOT NULL,
	score_snapshot FLOAT NOT NULL,
	score_change FLOAT,
	improvements JSONB NOT NULL,
	new_similarities JSONB NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(content_version_id) REFERENCES content_versions (id),
	FOREIGN KEY(analysis_id) REFERENCES originality_analyses (id)
);

-- analysis_keywords
CREATE TABLE IF NOT EXISTS analysis_keywords (
	id VARCHAR(32) NOT NULL,
	analysis_id VARCHAR(32) NOT NULL,
	keyword VARCHAR(100) NOT NULL,
	frequency INTEGER NOT NULL,
	importance FLOAT NOT NULL,
	source VARCHAR(16) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(analysis_id) REFERENCES originality_analyses (id)
);

-- originality_reports
CREATE TABLE IF NOT EXISTS originality_reports (
	id VARCHAR(32) NOT NULL,
	content_version_id VARCHAR(32) NOT NULL,
	analysis_id VARCHAR(32) NOT NULL,
	title VARCHAR(200) NOT NULL,
	summary TEXT NOT NULL,
	export_formats JSONB NOT NULL,
	pdf_url VARCHAR(500),
	excel_url VARCHAR(500),
	report_data JSONB NOT NULL,
	status VARCHAR(16) NOT NULL,
	error_message TEXT,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	completed_at TIMESTAMP WITH TIME ZONE,
	PRIMARY KEY (id),
	FOREIGN KEY(content_version_id) REFERENCES content_versions (id),
	FOREIGN KEY(analysis_id) REFERENCES originality_analyses (id)
);

-- ---------- 模型内索引 ----------

-- handdraw_colors.ix_handdraw_colors_color_id
CREATE UNIQUE INDEX IF NOT EXISTS ix_handdraw_colors_color_id ON handdraw_colors (color_id);

-- handdraw_library_meta.ix_handdraw_library_meta_library_key
CREATE UNIQUE INDEX IF NOT EXISTS ix_handdraw_library_meta_library_key ON handdraw_library_meta (library_key);

-- handdraw_styles.ix_handdraw_styles_group_key
CREATE INDEX IF NOT EXISTS ix_handdraw_styles_group_key ON handdraw_styles (group_key);

-- handdraw_styles.ix_handdraw_styles_legacy_number
CREATE INDEX IF NOT EXISTS ix_handdraw_styles_legacy_number ON handdraw_styles (legacy_number);

-- handdraw_styles.ix_handdraw_styles_sort_order
CREATE INDEX IF NOT EXISTS ix_handdraw_styles_sort_order ON handdraw_styles (sort_order);

-- handdraw_styles.ix_handdraw_styles_style_number
CREATE UNIQUE INDEX IF NOT EXISTS ix_handdraw_styles_style_number ON handdraw_styles (style_number);

-- hot_clusters.ix_hot_clusters_fingerprint
CREATE INDEX IF NOT EXISTS ix_hot_clusters_fingerprint ON hot_clusters (fingerprint);

-- hot_clusters.ix_hot_clusters_last_seen_day
CREATE INDEX IF NOT EXISTS ix_hot_clusters_last_seen_day ON hot_clusters (last_seen_day);

-- hot_clusters.ix_hot_clusters_score
CREATE INDEX IF NOT EXISTS ix_hot_clusters_score ON hot_clusters (score);

-- hot_clusters.ix_hot_clusters_status
CREATE INDEX IF NOT EXISTS ix_hot_clusters_status ON hot_clusters (status);

-- hot_clusters.ix_hot_clusters_track
CREATE INDEX IF NOT EXISTS ix_hot_clusters_track ON hot_clusters (track);

-- hot_items.ix_hot_items_cluster_id
CREATE INDEX IF NOT EXISTS ix_hot_items_cluster_id ON hot_items (cluster_id);

-- hot_items.ix_hot_items_day
CREATE INDEX IF NOT EXISTS ix_hot_items_day ON hot_items (day);

-- hot_items.ix_hot_items_item_uid
CREATE UNIQUE INDEX IF NOT EXISTS ix_hot_items_item_uid ON hot_items (item_uid);

-- hot_items.ix_hot_items_platform
CREATE INDEX IF NOT EXISTS ix_hot_items_platform ON hot_items (platform);

-- hot_items.ix_hot_items_title_norm
CREATE INDEX IF NOT EXISTS ix_hot_items_title_norm ON hot_items (title_norm);

-- hot_rank_history.ix_hot_rank_history_captured_at
CREATE INDEX IF NOT EXISTS ix_hot_rank_history_captured_at ON hot_rank_history (captured_at);

-- hot_rank_history.ix_hot_rank_history_item_id
CREATE INDEX IF NOT EXISTS ix_hot_rank_history_item_id ON hot_rank_history (item_id);

-- image_styles.ix_image_styles_purpose_sort
CREATE INDEX IF NOT EXISTS ix_image_styles_purpose_sort ON image_styles (purpose, sort_order, id);

-- permissions.ix_permissions_module_sort
CREATE INDEX IF NOT EXISTS ix_permissions_module_sort ON permissions (module, sort);

-- platform_access_tokens.ix_platform_access_tokens_platform
CREATE INDEX IF NOT EXISTS ix_platform_access_tokens_platform ON platform_access_tokens (platform);

-- platform_sync_jobs.ix_platform_sync_jobs_connected_account_id
CREATE INDEX IF NOT EXISTS ix_platform_sync_jobs_connected_account_id ON platform_sync_jobs (connected_account_id);

-- prompt_rules.ix_prompt_rules_key
CREATE UNIQUE INDEX IF NOT EXISTS ix_prompt_rules_key ON prompt_rules (key);

-- revoked_tokens.ix_revoked_tokens_expires_at
CREATE INDEX IF NOT EXISTS ix_revoked_tokens_expires_at ON revoked_tokens (expires_at);

-- revoked_tokens.ix_revoked_tokens_owner_user_id
CREATE INDEX IF NOT EXISTS ix_revoked_tokens_owner_user_id ON revoked_tokens (owner_user_id);

-- roles.ix_roles_role_key
CREATE UNIQUE INDEX IF NOT EXISTS ix_roles_role_key ON roles (role_key);

-- roles.ix_roles_sort
CREATE INDEX IF NOT EXISTS ix_roles_sort ON roles (sort);

-- similarity_cache.ix_similarity_cache_created_at
CREATE INDEX IF NOT EXISTS ix_similarity_cache_created_at ON similarity_cache (created_at);

-- similarity_cache.ix_similarity_cache_pair
CREATE INDEX IF NOT EXISTS ix_similarity_cache_pair ON similarity_cache (source_version_id, target_version_id);

-- similarity_cache.ix_similarity_cache_source_version_id
CREATE INDEX IF NOT EXISTS ix_similarity_cache_source_version_id ON similarity_cache (source_version_id);

-- similarity_cache.ix_similarity_cache_target_version_id
CREATE INDEX IF NOT EXISTS ix_similarity_cache_target_version_id ON similarity_cache (target_version_id);

-- skill_registry_entries.ix_skill_registry_entries_owner_user_id
CREATE INDEX IF NOT EXISTS ix_skill_registry_entries_owner_user_id ON skill_registry_entries (owner_user_id);

-- skill_registry_entries.ix_skill_registry_entries_skill_key
CREATE INDEX IF NOT EXISTS ix_skill_registry_entries_skill_key ON skill_registry_entries (skill_key);

-- users.ix_users_auth_subject
CREATE UNIQUE INDEX IF NOT EXISTS ix_users_auth_subject ON users (auth_subject);

-- users.ix_users_email
CREATE INDEX IF NOT EXISTS ix_users_email ON users (email);

-- agent_provider_connections.ix_agent_provider_connections_owner_user_id
CREATE INDEX IF NOT EXISTS ix_agent_provider_connections_owner_user_id ON agent_provider_connections (owner_user_id);

-- ai_generations.ix_ai_generations_owner_user_id
CREATE INDEX IF NOT EXISTS ix_ai_generations_owner_user_id ON ai_generations (owner_user_id);

-- author_skills.ix_author_skills_owner_type
CREATE INDEX IF NOT EXISTS ix_author_skills_owner_type ON author_skills (owner_user_id, skill_type);

-- author_skills.ix_author_skills_owner_user_id
CREATE INDEX IF NOT EXISTS ix_author_skills_owner_user_id ON author_skills (owner_user_id);

-- author_skills.ix_author_skills_skill_type
CREATE INDEX IF NOT EXISTS ix_author_skills_skill_type ON author_skills (skill_type);

-- author_skills.ix_author_skills_status
CREATE INDEX IF NOT EXISTS ix_author_skills_status ON author_skills (status);

-- background_jobs.ix_background_jobs_owner_user_id
CREATE INDEX IF NOT EXISTS ix_background_jobs_owner_user_id ON background_jobs (owner_user_id);

-- background_jobs.ix_background_jobs_queue
CREATE INDEX IF NOT EXISTS ix_background_jobs_queue ON background_jobs (queue);

-- background_jobs.ix_background_jobs_status
CREATE INDEX IF NOT EXISTS ix_background_jobs_status ON background_jobs (status);

-- card_templates.ix_card_templates_owner_user_id
CREATE INDEX IF NOT EXISTS ix_card_templates_owner_user_id ON card_templates (owner_user_id);

-- characters.ix_characters_deleted_at
CREATE INDEX IF NOT EXISTS ix_characters_deleted_at ON characters (deleted_at);

-- characters.ix_characters_owner_user_id
CREATE INDEX IF NOT EXISTS ix_characters_owner_user_id ON characters (owner_user_id);

-- image_jobs.ix_image_jobs_asset_id
CREATE INDEX IF NOT EXISTS ix_image_jobs_asset_id ON image_jobs (asset_id);

-- image_jobs.ix_image_jobs_owner_user_id
CREATE INDEX IF NOT EXISTS ix_image_jobs_owner_user_id ON image_jobs (owner_user_id);

-- image_jobs.ix_image_jobs_status
CREATE INDEX IF NOT EXISTS ix_image_jobs_status ON image_jobs (status);

-- menus.ix_menus_menu_key
CREATE UNIQUE INDEX IF NOT EXISTS ix_menus_menu_key ON menus (menu_key);

-- menus.ix_menus_parent_sort
CREATE INDEX IF NOT EXISTS ix_menus_parent_sort ON menus (parent_id, sort);

-- menus.ix_menus_path
CREATE INDEX IF NOT EXISTS ix_menus_path ON menus (path);

-- menus.ix_menus_permission_code
CREATE INDEX IF NOT EXISTS ix_menus_permission_code ON menus (permission_code);

-- metric_snapshots.ix_metric_snapshots_connected_account_id
CREATE INDEX IF NOT EXISTS ix_metric_snapshots_connected_account_id ON metric_snapshots (connected_account_id);

-- metric_snapshots.ix_metric_snapshots_owner_user_id
CREATE INDEX IF NOT EXISTS ix_metric_snapshots_owner_user_id ON metric_snapshots (owner_user_id);

-- model_connections.ix_model_connections_owner_user_id
CREATE INDEX IF NOT EXISTS ix_model_connections_owner_user_id ON model_connections (owner_user_id);

-- prompt_templates.ix_prompt_templates_owner_user_id
CREATE INDEX IF NOT EXISTS ix_prompt_templates_owner_user_id ON prompt_templates (owner_user_id);

-- rbac_audit_logs.ix_rbac_audit_logs_action
CREATE INDEX IF NOT EXISTS ix_rbac_audit_logs_action ON rbac_audit_logs (action);

-- rbac_audit_logs.ix_rbac_audit_logs_actor_user_id
CREATE INDEX IF NOT EXISTS ix_rbac_audit_logs_actor_user_id ON rbac_audit_logs (actor_user_id);

-- rbac_audit_logs.ix_rbac_audit_logs_created_at
CREATE INDEX IF NOT EXISTS ix_rbac_audit_logs_created_at ON rbac_audit_logs (created_at);

-- rbac_audit_logs.ix_rbac_audit_target
CREATE INDEX IF NOT EXISTS ix_rbac_audit_target ON rbac_audit_logs (target_type, target_id);

-- scheduled_scripts.ix_scheduled_scripts_owner_user_id
CREATE INDEX IF NOT EXISTS ix_scheduled_scripts_owner_user_id ON scheduled_scripts (owner_user_id);

-- scheduled_tasks.ix_scheduled_tasks_active_job_id
CREATE INDEX IF NOT EXISTS ix_scheduled_tasks_active_job_id ON scheduled_tasks (active_job_id);

-- scheduled_tasks.ix_scheduled_tasks_enabled
CREATE INDEX IF NOT EXISTS ix_scheduled_tasks_enabled ON scheduled_tasks (enabled);

-- scheduled_tasks.ix_scheduled_tasks_owner_user_id
CREATE INDEX IF NOT EXISTS ix_scheduled_tasks_owner_user_id ON scheduled_tasks (owner_user_id);

-- scheduled_tasks.ix_scheduled_tasks_scope
CREATE INDEX IF NOT EXISTS ix_scheduled_tasks_scope ON scheduled_tasks (scope);

-- scheduled_tasks.ix_scheduled_tasks_task_type
CREATE INDEX IF NOT EXISTS ix_scheduled_tasks_task_type ON scheduled_tasks (task_type);

-- skill_distillation_jobs.ix_skill_distillation_jobs_owner_user_id
CREATE INDEX IF NOT EXISTS ix_skill_distillation_jobs_owner_user_id ON skill_distillation_jobs (owner_user_id);

-- skill_distillation_jobs.ix_skill_distillation_jobs_skill_entry_id
CREATE INDEX IF NOT EXISTS ix_skill_distillation_jobs_skill_entry_id ON skill_distillation_jobs (skill_entry_id);

-- skill_distillation_jobs.ix_skill_distillation_jobs_status
CREATE INDEX IF NOT EXISTS ix_skill_distillation_jobs_status ON skill_distillation_jobs (status);

-- skill_usage_logs.ix_skill_usage_logs_owner_user_id
CREATE INDEX IF NOT EXISTS ix_skill_usage_logs_owner_user_id ON skill_usage_logs (owner_user_id);

-- skill_usage_logs.ix_skill_usage_logs_skill_entry_id
CREATE INDEX IF NOT EXISTS ix_skill_usage_logs_skill_entry_id ON skill_usage_logs (skill_entry_id);

-- user_preferences.ix_user_preferences_owner_user_id
CREATE UNIQUE INDEX IF NOT EXISTS ix_user_preferences_owner_user_id ON user_preferences (owner_user_id);

-- workspaces.ix_workspaces_owner_user_id
CREATE INDEX IF NOT EXISTS ix_workspaces_owner_user_id ON workspaces (owner_user_id);

-- agent_sessions.ix_agent_sessions_owner_user_id
CREATE INDEX IF NOT EXISTS ix_agent_sessions_owner_user_id ON agent_sessions (owner_user_id);

-- agent_sessions.ix_agent_sessions_workspace_id
CREATE INDEX IF NOT EXISTS ix_agent_sessions_workspace_id ON agent_sessions (workspace_id);

-- analysis_tasks.ix_analysis_task_status
CREATE INDEX IF NOT EXISTS ix_analysis_task_status ON analysis_tasks (status, created_at);

-- analysis_tasks.ix_analysis_tasks_status
CREATE INDEX IF NOT EXISTS ix_analysis_tasks_status ON analysis_tasks (status);

-- analysis_tasks.ix_analysis_tasks_workspace_id
CREATE INDEX IF NOT EXISTS ix_analysis_tasks_workspace_id ON analysis_tasks (workspace_id);

-- assets.ix_assets_owner_user_id
CREATE INDEX IF NOT EXISTS ix_assets_owner_user_id ON assets (owner_user_id);

-- assets.ix_assets_workspace_id
CREATE INDEX IF NOT EXISTS ix_assets_workspace_id ON assets (workspace_id);

-- author_skill_distill_runs.ix_author_skill_distill_runs_owner_user_id
CREATE INDEX IF NOT EXISTS ix_author_skill_distill_runs_owner_user_id ON author_skill_distill_runs (owner_user_id);

-- author_skill_distill_runs.ix_author_skill_distill_runs_skill_id
CREATE INDEX IF NOT EXISTS ix_author_skill_distill_runs_skill_id ON author_skill_distill_runs (skill_id);

-- author_skill_distill_runs.ix_author_skill_distill_runs_status
CREATE INDEX IF NOT EXISTS ix_author_skill_distill_runs_status ON author_skill_distill_runs (status);

-- author_skill_revisions.ix_author_skill_revisions_owner_user_id
CREATE INDEX IF NOT EXISTS ix_author_skill_revisions_owner_user_id ON author_skill_revisions (owner_user_id);

-- author_skill_revisions.ix_author_skill_revisions_skill_id
CREATE INDEX IF NOT EXISTS ix_author_skill_revisions_skill_id ON author_skill_revisions (skill_id);

-- calendar_entries.ix_calendar_entries_owner_user_id
CREATE INDEX IF NOT EXISTS ix_calendar_entries_owner_user_id ON calendar_entries (owner_user_id);

-- calendar_entries.ix_calendar_entries_planned_at
CREATE INDEX IF NOT EXISTS ix_calendar_entries_planned_at ON calendar_entries (planned_at);

-- calendar_entries.ix_calendar_entries_studio_article_id
CREATE INDEX IF NOT EXISTS ix_calendar_entries_studio_article_id ON calendar_entries (studio_article_id);

-- calendar_entries.ix_calendar_entries_workspace_id
CREATE INDEX IF NOT EXISTS ix_calendar_entries_workspace_id ON calendar_entries (workspace_id);

-- connected_accounts.ix_connected_accounts_auth_source
CREATE INDEX IF NOT EXISTS ix_connected_accounts_auth_source ON connected_accounts (auth_source);

-- connected_accounts.ix_connected_accounts_owner_user_id
CREATE INDEX IF NOT EXISTS ix_connected_accounts_owner_user_id ON connected_accounts (owner_user_id);

-- connected_accounts.ix_connected_accounts_platform
CREATE INDEX IF NOT EXISTS ix_connected_accounts_platform ON connected_accounts (platform);

-- connected_accounts.ix_connected_accounts_status
CREATE INDEX IF NOT EXISTS ix_connected_accounts_status ON connected_accounts (status);

-- connected_accounts.ix_connected_accounts_workspace_id
CREATE INDEX IF NOT EXISTS ix_connected_accounts_workspace_id ON connected_accounts (workspace_id);

-- content_items.ix_content_items_archived
CREATE INDEX IF NOT EXISTS ix_content_items_archived ON content_items (archived);

-- content_items.ix_content_items_owner_user_id
CREATE INDEX IF NOT EXISTS ix_content_items_owner_user_id ON content_items (owner_user_id);

-- content_items.ix_content_items_status
CREATE INDEX IF NOT EXISTS ix_content_items_status ON content_items (status);

-- content_items.ix_content_items_workspace_id
CREATE INDEX IF NOT EXISTS ix_content_items_workspace_id ON content_items (workspace_id);

-- model_catalog_entries.ix_model_catalog_entries_connection_id
CREATE INDEX IF NOT EXISTS ix_model_catalog_entries_connection_id ON model_catalog_entries (connection_id);

-- novel_projects.ix_novel_projects_owner_user_id
CREATE INDEX IF NOT EXISTS ix_novel_projects_owner_user_id ON novel_projects (owner_user_id);

-- novel_projects.ix_novel_projects_status
CREATE INDEX IF NOT EXISTS ix_novel_projects_status ON novel_projects (status);

-- novel_projects.ix_novel_projects_workspace_id
CREATE INDEX IF NOT EXISTS ix_novel_projects_workspace_id ON novel_projects (workspace_id);

-- publish_jobs.ix_publish_jobs_connected_account_id
CREATE INDEX IF NOT EXISTS ix_publish_jobs_connected_account_id ON publish_jobs (connected_account_id);

-- publish_jobs.ix_publish_jobs_content_item_id
CREATE INDEX IF NOT EXISTS ix_publish_jobs_content_item_id ON publish_jobs (content_item_id);

-- publish_jobs.ix_publish_jobs_owner_user_id
CREATE INDEX IF NOT EXISTS ix_publish_jobs_owner_user_id ON publish_jobs (owner_user_id);

-- publish_jobs.ix_publish_jobs_status
CREATE INDEX IF NOT EXISTS ix_publish_jobs_status ON publish_jobs (status);

-- publish_jobs.ix_publish_jobs_target
CREATE INDEX IF NOT EXISTS ix_publish_jobs_target ON publish_jobs (target);

-- publish_jobs.ix_publish_jobs_workspace_id
CREATE INDEX IF NOT EXISTS ix_publish_jobs_workspace_id ON publish_jobs (workspace_id);

-- scheduled_script_versions.ix_scheduled_script_versions_script_id
CREATE INDEX IF NOT EXISTS ix_scheduled_script_versions_script_id ON scheduled_script_versions (script_id);

-- scheduled_task_runs.ix_scheduled_task_runs_job_id
CREATE INDEX IF NOT EXISTS ix_scheduled_task_runs_job_id ON scheduled_task_runs (job_id);

-- scheduled_task_runs.ix_scheduled_task_runs_status
CREATE INDEX IF NOT EXISTS ix_scheduled_task_runs_status ON scheduled_task_runs (status);

-- scheduled_task_runs.ix_scheduled_task_runs_task_id
CREATE INDEX IF NOT EXISTS ix_scheduled_task_runs_task_id ON scheduled_task_runs (task_id);

-- sticker_sets.ix_sticker_sets_hot_cluster_id
CREATE INDEX IF NOT EXISTS ix_sticker_sets_hot_cluster_id ON sticker_sets (hot_cluster_id);

-- sticker_sets.ix_sticker_sets_owner_user_id
CREATE INDEX IF NOT EXISTS ix_sticker_sets_owner_user_id ON sticker_sets (owner_user_id);

-- sticker_sets.ix_sticker_sets_status
CREATE INDEX IF NOT EXISTS ix_sticker_sets_status ON sticker_sets (status);

-- sticker_sets.ix_sticker_sets_workspace_id
CREATE INDEX IF NOT EXISTS ix_sticker_sets_workspace_id ON sticker_sets (workspace_id);

-- storyboard_projects.ix_storyboard_projects_deleted_at
CREATE INDEX IF NOT EXISTS ix_storyboard_projects_deleted_at ON storyboard_projects (deleted_at);

-- storyboard_projects.ix_storyboard_projects_owner_user_id
CREATE INDEX IF NOT EXISTS ix_storyboard_projects_owner_user_id ON storyboard_projects (owner_user_id);

-- storyboard_projects.ix_storyboard_projects_status
CREATE INDEX IF NOT EXISTS ix_storyboard_projects_status ON storyboard_projects (status);

-- storyboard_projects.ix_storyboard_projects_workspace_id
CREATE INDEX IF NOT EXISTS ix_storyboard_projects_workspace_id ON storyboard_projects (workspace_id);

-- week_reviews.ix_week_reviews_owner_user_id
CREATE INDEX IF NOT EXISTS ix_week_reviews_owner_user_id ON week_reviews (owner_user_id);

-- week_reviews.ix_week_reviews_week_start
CREATE INDEX IF NOT EXISTS ix_week_reviews_week_start ON week_reviews (week_start);

-- week_reviews.ix_week_reviews_workspace_id
CREATE INDEX IF NOT EXISTS ix_week_reviews_workspace_id ON week_reviews (workspace_id);

-- workspace_skills.ix_workspace_skills_workspace_id
CREATE INDEX IF NOT EXISTS ix_workspace_skills_workspace_id ON workspace_skills (workspace_id);

-- agent_runs.ix_agent_runs_owner_user_id
CREATE INDEX IF NOT EXISTS ix_agent_runs_owner_user_id ON agent_runs (owner_user_id);

-- agent_runs.ix_agent_runs_session_id
CREATE INDEX IF NOT EXISTS ix_agent_runs_session_id ON agent_runs (session_id);

-- agent_runs.ix_agent_runs_status
CREATE INDEX IF NOT EXISTS ix_agent_runs_status ON agent_runs (status);

-- agent_runs.ix_agent_runs_workspace_id
CREATE INDEX IF NOT EXISTS ix_agent_runs_workspace_id ON agent_runs (workspace_id);

-- content_versions.ix_content_versions_content_item_id
CREATE INDEX IF NOT EXISTS ix_content_versions_content_item_id ON content_versions (content_item_id);

-- novel_aliases.ix_novel_aliases_project_id
CREATE INDEX IF NOT EXISTS ix_novel_aliases_project_id ON novel_aliases (project_id);

-- novel_chapters.ix_novel_chapters_project_id
CREATE INDEX IF NOT EXISTS ix_novel_chapters_project_id ON novel_chapters (project_id);

-- novel_characters.ix_novel_characters_project_id
CREATE INDEX IF NOT EXISTS ix_novel_characters_project_id ON novel_characters (project_id);

-- platform_variants.ix_platform_variants_content_item_id
CREATE INDEX IF NOT EXISTS ix_platform_variants_content_item_id ON platform_variants (content_item_id);

-- platform_variants.ix_platform_variants_platform
CREATE INDEX IF NOT EXISTS ix_platform_variants_platform ON platform_variants (platform);

-- publish_records.ix_publish_records_publish_job_id
CREATE INDEX IF NOT EXISTS ix_publish_records_publish_job_id ON publish_records (publish_job_id);

-- review_comments.ix_review_comments_content_item_id
CREATE INDEX IF NOT EXISTS ix_review_comments_content_item_id ON review_comments (content_item_id);

-- sticker_images.ix_sticker_images_job_id
CREATE INDEX IF NOT EXISTS ix_sticker_images_job_id ON sticker_images (job_id);

-- sticker_images.ix_sticker_images_set_id
CREATE INDEX IF NOT EXISTS ix_sticker_images_set_id ON sticker_images (set_id);

-- storyboard_scenes.ix_storyboard_scenes_deleted_at
CREATE INDEX IF NOT EXISTS ix_storyboard_scenes_deleted_at ON storyboard_scenes (deleted_at);

-- storyboard_scenes.ix_storyboard_scenes_project_id
CREATE INDEX IF NOT EXISTS ix_storyboard_scenes_project_id ON storyboard_scenes (project_id);

-- studio_articles.ix_studio_articles_archived
CREATE INDEX IF NOT EXISTS ix_studio_articles_archived ON studio_articles (archived);

-- studio_articles.ix_studio_articles_owner_archived
CREATE INDEX IF NOT EXISTS ix_studio_articles_owner_archived ON studio_articles (owner_user_id, archived);

-- studio_articles.ix_studio_articles_owner_user_id
CREATE INDEX IF NOT EXISTS ix_studio_articles_owner_user_id ON studio_articles (owner_user_id);

-- studio_articles.ix_studio_articles_workspace_id
CREATE INDEX IF NOT EXISTS ix_studio_articles_workspace_id ON studio_articles (workspace_id);

-- agent_approvals.ix_agent_approvals_run_id
CREATE INDEX IF NOT EXISTS ix_agent_approvals_run_id ON agent_approvals (run_id);

-- agent_events.ix_agent_events_run_id
CREATE INDEX IF NOT EXISTS ix_agent_events_run_id ON agent_events (run_id);

-- agent_tool_calls.ix_agent_tool_calls_run_id
CREATE INDEX IF NOT EXISTS ix_agent_tool_calls_run_id ON agent_tool_calls (run_id);

-- article_blocks.ix_article_blocks_article_id
CREATE INDEX IF NOT EXISTS ix_article_blocks_article_id ON article_blocks (article_id);

-- article_blocks.ix_article_blocks_article_ordinal
CREATE INDEX IF NOT EXISTS ix_article_blocks_article_ordinal ON article_blocks (article_id, ordinal);

-- article_revisions.ix_article_revisions_article_id
CREATE INDEX IF NOT EXISTS ix_article_revisions_article_id ON article_revisions (article_id);

-- article_revisions.ix_article_revisions_created_at
CREATE INDEX IF NOT EXISTS ix_article_revisions_created_at ON article_revisions (created_at);

-- content_embeddings.ix_content_embeddings_content_version_id
CREATE INDEX IF NOT EXISTS ix_content_embeddings_content_version_id ON content_embeddings (content_version_id);

-- originality_analyses.ix_originality_analyses_content_version_id
CREATE INDEX IF NOT EXISTS ix_originality_analyses_content_version_id ON originality_analyses (content_version_id);

-- originality_analyses.ix_originality_analyses_workspace_id
CREATE INDEX IF NOT EXISTS ix_originality_analyses_workspace_id ON originality_analyses (workspace_id);

-- pet_conversations.ix_pet_conversations_owner_user_id
CREATE INDEX IF NOT EXISTS ix_pet_conversations_owner_user_id ON pet_conversations (owner_user_id);

-- pet_conversations.ix_pet_conversations_studio_article_id
CREATE INDEX IF NOT EXISTS ix_pet_conversations_studio_article_id ON pet_conversations (studio_article_id);

-- storyboard_shots.ix_storyboard_shots_deleted_at
CREATE INDEX IF NOT EXISTS ix_storyboard_shots_deleted_at ON storyboard_shots (deleted_at);

-- storyboard_shots.ix_storyboard_shots_final_job_id
CREATE INDEX IF NOT EXISTS ix_storyboard_shots_final_job_id ON storyboard_shots (final_job_id);

-- storyboard_shots.ix_storyboard_shots_scene_id
CREATE INDEX IF NOT EXISTS ix_storyboard_shots_scene_id ON storyboard_shots (scene_id);

-- storyboard_shots.ix_storyboard_shots_sketch_job_id
CREATE INDEX IF NOT EXISTS ix_storyboard_shots_sketch_job_id ON storyboard_shots (sketch_job_id);

-- analysis_history.ix_analysis_history_content
CREATE INDEX IF NOT EXISTS ix_analysis_history_content ON analysis_history (content_version_id);

-- analysis_history.ix_analysis_history_content_version_id
CREATE INDEX IF NOT EXISTS ix_analysis_history_content_version_id ON analysis_history (content_version_id);

-- analysis_keywords.ix_analysis_keywords_analysis_id
CREATE INDEX IF NOT EXISTS ix_analysis_keywords_analysis_id ON analysis_keywords (analysis_id);

-- originality_reports.ix_originality_report_version
CREATE INDEX IF NOT EXISTS ix_originality_report_version ON originality_reports (content_version_id);

-- originality_reports.ix_originality_reports_content_version_id
CREATE INDEX IF NOT EXISTS ix_originality_reports_content_version_id ON originality_reports (content_version_id);

-- ---------- 补充索引（apps/api/app/core/db.py::_INDEXES） ----------

-- 这些索引在 db.py 用字符串创建，未纳入模型 metadata；
-- 修改清单后请重新生成本文件，避免两处漂移。

CREATE INDEX IF NOT EXISTS ix_assets_owner_created ON assets (owner_user_id, created_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS ix_content_ws_updated ON content_items (workspace_id, updated_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS ix_publish_owner_created ON publish_jobs (owner_user_id, created_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS ix_bj_owner_created ON background_jobs (owner_user_id, created_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS ix_bj_status_started ON background_jobs (status, started_at);
CREATE INDEX IF NOT EXISTS ix_metrics_owner_captured ON metric_snapshots (owner_user_id, captured_at DESC);
CREATE INDEX IF NOT EXISTS ix_accounts_owner_created ON connected_accounts (owner_user_id, created_at, id);
CREATE INDEX IF NOT EXISTS ix_connections_owner_created ON model_connections (owner_user_id, created_at, id);
CREATE INDEX IF NOT EXISTS ix_skills_owner_created ON skill_registry_entries (owner_user_id, created_at, id);
CREATE INDEX IF NOT EXISTS ix_hot_items_day_platform_rank ON hot_items (day, platform, rank);
CREATE INDEX IF NOT EXISTS ix_hot_clusters_day_score ON hot_clusters (last_seen_day, score DESC, id);
CREATE INDEX IF NOT EXISTS ix_sticker_sets_owner_updated ON sticker_sets (owner_user_id, updated_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS ix_sticker_images_set_order ON sticker_images (set_id, sort_order);
CREATE INDEX IF NOT EXISTS ix_storyboard_projects_owner_updated ON storyboard_projects (owner_user_id, updated_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS ix_storyboard_scenes_project_order ON storyboard_scenes (project_id, sort_order);
CREATE INDEX IF NOT EXISTS ix_storyboard_shots_scene_order ON storyboard_shots (scene_id, sort_order);
CREATE INDEX IF NOT EXISTS ix_sched_enabled_next ON scheduled_tasks (enabled, next_run_at);
CREATE INDEX IF NOT EXISTS ix_sched_scope_owner_fired ON scheduled_tasks (scope, owner_user_id, last_fired_at DESC);
CREATE INDEX IF NOT EXISTS ix_sched_runs_task_time ON scheduled_task_runs (task_id, triggered_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS ix_sched_runs_status ON scheduled_task_runs (status);

-- ---------- 特殊唯一索引（来自 db.py._apply_additive_migrations） ----------

-- skill_registry_entries：同一 (skill_key, owner) 只允许一条；
--   用 COALESCE 让 owner 为 NULL 的内置 Skill 也参与唯一性判断。
CREATE UNIQUE INDEX IF NOT EXISTS uq_skill_owner_key ON skill_registry_entries (skill_key, (COALESCE(owner_user_id, '')));

-- users：邮箱大小写不敏感唯一；NULL（历史开发态账号）不参与唯一性。
CREATE UNIQUE INDEX IF NOT EXISTS uq_users_email ON users (lower(email)) WHERE email IS NOT NULL;

COMMIT;
\endif
