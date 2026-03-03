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
    pm_user_prompt_file: Path = Path("local_data/prompt_overrides/pm_jira_query_user.md")
    pm_user_prompt_max_chars: int = 20000
    templates_dir: Path = Path("frontend/templates")
    static_dir: Path = Path("frontend/static")

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
