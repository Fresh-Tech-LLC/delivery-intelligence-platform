from functools import lru_cache
from pathlib import Path
from typing import Optional
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # LLM
    llm_api_base: str = "https://api.openai.com/v1"
    llm_model_name: str = "gpt-4o"
    llm_api_key: str = ""
    # Alternative auth: access_key + secret_key combined as "access_key:secret_key" Bearer token.
    # When both are set they take priority over llm_api_key.
    llm_access_key: str = ""
    llm_secret_key: str = ""
    llm_timeout: int = 300
    llm_max_retries: int = 2
    llm_max_tokens: int = 4096
    # Set to false for models that reject the temperature parameter (e.g. gpt-5, o1, o3)
    llm_temperature_supported: bool = True
    # Optional fixed seed for providers/models that support deterministic sampling.
    llm_seed: Optional[int] = None
    # Token limit param name: "max_tokens" (most OpenAI-compatible APIs) or
    # "max_completion_tokens" (required by gpt-5 / newer OpenAI models)
    llm_max_tokens_param: str = "max_tokens"
    # Path to a custom CA bundle (.pem) for self-signed/corporate TLS certificates
    llm_ca_bundle: Optional[str] = None

    # Jira
    jira_base_url: str = ""
    jira_user: str = ""
    jira_api_token: str = ""
    # Jira auth mode: basic | bearer | auto
    jira_auth_mode: str = "basic"
    # Optional dedicated bearer token (if blank in bearer mode, jira_api_token is used)
    jira_bearer_token: str = ""
    jira_project_key: str = ""
    jira_timeout: int = 30
    jira_verify_ssl: bool = False

    # Paths
    prompts_dir: Path = Path("prompts")
    config_dir: Path = Path("config")
    local_data_dir: Path = Path("local_data")
    data_dir: Path = Path("data")                              # mutable runtime data (projects, checklists)
    pm_user_prompt_file: Path = Path("local_data/prompt_overrides/pm_jira_query_user.md")
    pm_user_prompt_max_chars: int = 20000
    templates_dir: Path = Path("frontend/templates")
    static_dir: Path = Path("frontend/static")

    # Knowledge / Ingestion
    knowledge_raw_capture_enabled: bool = True
    knowledge_default_project_key: Optional[str] = None  # fallback if not passed at call time; also falls back to jira_project_key
    knowledge_local_docs_dir: Path = Path("local_data/knowledge_docs")
    knowledge_jira_max_results: int = 200
    knowledge_max_file_bytes: int = 20_971_520  # 20 MB guard for local file ingest
    knowledge_chunk_target_chars: int = 2000
    knowledge_chunk_overlap_chars: int = 200
    knowledge_max_chunks_per_artifact: int = 200
    knowledge_index_case_sensitive: bool = False
    knowledge_search_default_limit: int = 20
    knowledge_search_max_limit: int = 100
    knowledge_rebuild_index_on_chunk_ingest: bool = True
    knowledge_appian_exports_dir: Path = Path("local_data/appian_exports")
    knowledge_appian_extract_recursive: bool = True
    knowledge_appian_max_file_bytes: int = 52_428_800
    knowledge_entity_min_token_len: int = 3
    knowledge_entity_max_per_chunk: int = 50
    knowledge_linking_enable_jira_refs: bool = True
    knowledge_linking_enable_keyword_overlap: bool = True
    knowledge_linking_overlap_threshold: int = 3
    knowledge_linking_max_related_per_artifact: int = 25
    requirements_workspace_dir: Path = Path("local_data/requirements_workspaces")
    requirements_context_max_search_results: int = 20
    requirements_context_max_related_results: int = 10
    requirements_context_max_pinned_items: int = 50
    requirements_generation_enabled: bool = True
    requirements_generation_model_name: Optional[str] = None
    requirements_story_max_count: int = 25
    requirements_default_story_split_mode: str = "balanced"
    qa_workspace_dir: Path = Path("local_data/qa_workspaces")
    qa_generation_enabled: bool = True
    qa_generation_model_name: Optional[str] = None
    qa_max_scenarios_per_workspace: int = 30
    qa_max_nl_scripts_per_run: int = 20
    qa_max_execution_specs_per_run: int = 20
    qa_playwright_enabled: bool = False
    qa_playwright_command: Optional[str] = None
    qa_playwright_tests_dir: Path = Path("local_data/generated_playwright")
    qa_playwright_evidence_dir: Path = Path("local_data/playwright_evidence")
    qa_exploration_enabled: bool = False
    qa_max_exploration_steps: int = 25
    qa_default_browser_role: str = "qa"
    qa_default_promotion_state: str = "draft"

    # LLM Adapter
    # Selects the normalized adapter used by the capability probe and new app code.
    # Supported: "openai_chat" | "model_engine_ask"
    llm_adapter_type: str = "openai_chat"
    # ModelEngine adapter settings (only used when llm_adapter_type = "model_engine_ask")
    model_engine_default_room_id: Optional[str] = None
    model_engine_use_history: bool = False
    model_engine_default_insight_id: Optional[str] = None

    # Capability Probe
    probe_enabled: bool = True
    probe_context_smoke_size: int = 40    # repetitions of ~100-char paragraph ≈ 4000 chars total
    probe_step_timeout: int = 60          # seconds per step (informational; used in ProbeRunner)
    probe_max_runs_listed: int = 20

    def __repr__(self) -> str:
        return (
            f"Settings(llm_api_base={self.llm_api_base!r}, "
            f"llm_model_name={self.llm_model_name!r}, "
            f"jira_base_url={self.jira_base_url!r}, "
            f"jira_user={self.jira_user!r})"
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
