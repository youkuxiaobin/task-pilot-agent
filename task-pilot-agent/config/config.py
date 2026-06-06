# settings.py
from __future__ import annotations
from typing import Any, Optional, Annotated, List, Dict
import os, yaml
from pathlib import Path
from pydantic import BaseModel, Field, SecretStr, AliasChoices, computed_field, BeforeValidator, HttpUrl
from pydantic_settings import BaseSettings, SettingsConfigDict, PydanticBaseSettingsSource
from pydantic.fields import FieldInfo 

# 解析 "a,b,c" -> ["a","b","c"]
def _csv_to_list(v):
    if isinstance(v, str):
        return [s for s in (x.strip() for x in v.split(",")) if s]
    return v
CSVList = Annotated[List[str], BeforeValidator(_csv_to_list)]


def reveal_secret(value: SecretStr | str | None) -> str:
    if isinstance(value, SecretStr):
        return value.get_secret_value()
    return value or ""


def reveal_secrets(value: Any) -> Any:
    if isinstance(value, SecretStr):
        return value.get_secret_value()
    if isinstance(value, BaseModel):
        return {name: reveal_secrets(getattr(value, name)) for name in type(value).model_fields}
    if isinstance(value, dict):
        return {key: reveal_secrets(item) for key, item in value.items()}
    if isinstance(value, list):
        return [reveal_secrets(item) for item in value]
    if isinstance(value, tuple):
        return tuple(reveal_secrets(item) for item in value)
    return value


def _env_bool(name: str) -> Optional[bool]:
    raw = os.getenv(name)
    if raw is None:
        return None
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str) -> Optional[int]:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return None
    return int(raw)


AUTH_CONFIG_ALIASES = {
    "AUTH_REQUIRED": "required",
    "AUTH_DEV_USER_ID": "dev_user_id",
    "AUTH_ADMIN_USER_IDS": "admin_user_ids",
    "AUTH_SESSION_COOKIE_NAME": "session_cookie_name",
    "AUTH_SESSION_TTL_SECONDS": "session_ttl_seconds",
    "AUTH_COOKIE_SECURE": "cookie_secure",
}

AUTH_PROVIDER_CONFIG_ALIASES = {
    "google": {
        "GOOGLE_ENABLED": "enabled",
        "GOOGLE_AUTH_ENABLED": "enabled",
        "GOOGLE_CLIENT_ID": "client_id",
        "GOOGLE_CLIENT_SECRET": "client_secret",
        "GOOGLE_REDIRECT_URI": "redirect_uri",
        "GOOGLE_SCOPES": "scopes",
    },
    "microsoft": {
        "MICROSOFT_ENABLED": "enabled",
        "MICROSOFT_AUTH_ENABLED": "enabled",
        "MICROSOFT_CLIENT_ID": "client_id",
        "MICROSOFT_CLIENT_SECRET": "client_secret",
        "MICROSOFT_REDIRECT_URI": "redirect_uri",
        "MICROSOFT_SCOPES": "scopes",
    },
    "wechat": {
        "WECHAT_ENABLED": "enabled",
        "WECHAT_AUTH_ENABLED": "enabled",
        "WECHAT_APP_ID": "client_id",
        "WECHAT_APP_SECRET": "client_secret",
        "WECHAT_REDIRECT_URI": "redirect_uri",
        "WECHAT_SCOPES": "scopes",
    },
}


def normalize_database_url(url: str) -> str:
    """Use the pure-Python MySQL driver when the URL does not name a driver."""
    if url.startswith("mysql://"):
        return f"mysql+pymysql://{url.removeprefix('mysql://')}"
    return url


class DBSettings(BaseModel):
    host: str = Field("127.0.0.1")
    port: int = Field(5432, ge=1, le=65535)
    user: str = Field("app")
    password: SecretStr = Field(..., description="必填，可来自 .env / env / YAML / secrets")
    name: str = Field("app")

    pool_pre_ping: bool = Field(True, description="启用连接出池前的存活检测")
    pool_recycle: int = Field(1800, ge=0, description="连接空闲多久后回收，单位秒")


    # 支持 DATABASE_URL / DB_URL 直接覆盖
    url: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("DATABASE_URL", "DB_URL")
    )

    @computed_field(return_type=str)
    @property
    def dsn(self) -> str:
        if self.url:
            return normalize_database_url(self.url)
        pwd = self.password.get_secret_value() if self.password else ""
        return normalize_database_url(f"mysql://{self.user}:{pwd}@{self.host}:{self.port}/{self.name}")

class CoreSettings(BaseModel):
    agent_id: str = Field("task-pilot-agent")
    upload_dir: str = Field("./uploads", description="Directory for storing uploaded files")

    @computed_field(return_type=Path)
    @property
    def upload_path(self) -> Path:
        raw_path = Path(self.upload_dir).expanduser()
        resolved = raw_path if raw_path.is_absolute() else (Path.cwd() / raw_path).resolve()
        resolved.mkdir(parents=True, exist_ok=True)
        return resolved
    # 修改为 Dict[str, str] 类型，支持 Map 格式
    output_styles: Dict[str, str] = Field({
        "html": "",
        "markdown": ", 最后以 markdown 展示最终结果",
        "table": ", 最后以excel 展示最终结果",
        "ppt": ", 最后以 ppt 展示最终结果",
    })
    default_output_style: str = Field("markdown", description="默认输出样式")
    default_run_environment: str = Field("local", description="默认运行环境：local 或 sandbox")
    planer_max_steps: int = Field(10)
    planer_min_steps: int = Field(1)
    executor_max_steps: int = Field(3)
    react_max_steps: int = Field(6, description="ReAct 模式最大循环次数")
    planner_replan_each_step: bool = Field(True, description="执行步骤之后是否要自动重新规划")
    planner_replan_on_failure: bool = Field(True, description="当某一步骤失败时是否要重新规划")
    planner_max_replans: int = Field(3, ge=0, description="最大重新规划次数")
    
class ServerSettings(BaseModel):
    host: str = Field("0.0.0.0")
    port: int = Field(8080, ge=1, le=65535)
    debug: bool = Field(False)
    cors_allow_origins: CSVList = Field(default=["*"])


class LLMConfigSettings(BaseModel):
    api_key: SecretStr = Field(..., description="必填，可来自 .env / env / YAML / secrets")
    site_url: str = Field(...)
    model:  str = Field("gpt-3.5-turbo")
    temperature: float = Field(0.0)
    context_length: int = Field(10000)

class LLMConfigItemSettings(BaseModel):
    """Named LLM config used by contexts mapping."""
    name: str = Field(..., description="唯一名称，用于 contexts 映射")
    provider: str = Field("openai")
    config: LLMConfigSettings
    model_thinking_field: Optional[Dict[str, str]] = Field(
        default=None,
        description="可选，覆盖默认的思维启用字段映射",
    )


class LLMSettings(BaseModel):
    provider: str = Field("openai")
    config: LLMConfigSettings
    model_thinking_field: Dict[str, str] = Field(
        default_factory=lambda: {
            "pro/deepseek-ai/deepseek-r1": "enable_reasoning",
            "zai-org/glm-4.6": "enable_thinking",
            "pro/moonshotai/kimi-k2-thinking": "thinking_enabled",
        },
        description="Mapping from model name/prefix to the request field used to enable thinking/reasoning.",
    )
    configs: List[LLMConfigItemSettings] = Field(
        default_factory=list,
        description="可选，命名的模型配置列表，用于按阶段切换模型",
    )
    contexts: Dict[str, str] = Field(
        default_factory=dict,
        description="可选，不同阶段->config name 的映射，缺省时使用 llm.config",
    )

class BrowserUseSettings(BaseModel):
    sandbox_url: str = Field("", description="浏览器沙盒的URL")
    provider: str = Field("openai")
    config: Optional[LLMConfigSettings] = Field(None)

class EmbeddingConfigSettings(BaseModel):
    api_key: SecretStr = Field(..., description="必填，可来自 .env / env / YAML / secrets")
    model: str = Field("Qwen/Qwen3-Embedding-8B")
    embedding_dims: int = Field(1024)
    openai_base_url: str = Field("")

class EmbeddingSettings(BaseModel):
    provider: str = Field("openai")
    config: EmbeddingConfigSettings

class SummaryAgentSettings(BaseModel):
    enable_thinking: bool = Field(False, description="Whether summary agent enables structured thinking outputs.")
    discard_reasoning_content: bool = Field(True, description="Discard reasoning_content chunks when streaming summaries.")

class VectorStoreConfigSettings(BaseModel):
    #path: str = Field("/tmp/vector_store")
    collection_name : str = Field("default_collection")
    embedding_model_dims: int = Field(1023)
    url: str = Field("http://localhost:6333")

class VectorStoreSettings(BaseModel):
    provider: str = Field("qdrant")
    config: VectorStoreConfigSettings

class LoggingSettings(BaseModel):
    level: str = Field("info")
    directory: str = Field("./logs")
    filename_prefix: str = Field("task-pilot-agent")
    max_bytes: int = Field(300 * 1024 * 1024, ge=1)
    backup_count: int = Field(10, ge=0, description="日志轮转保留文件数")



# 新增 RAG 检索器配置
class RAGRetrieverConfigSettings(BaseModel):
    top_k: int = Field(10, description="查询时返回的前k个最相似向量")
    query_field: str = Field("query_emb", description="用于查询的字段名")
    query_result: str = Field("query_result", description="检索结果字段名")
    collection: str = Field("example_collection", description="数据库中的集合名")
    dimension: int = Field(128, description="向量的维度")
    distance_metric: str = Field("L2", description="使用欧几里得距离计算向量相似度")
    index_type: str = Field("IVF", description="使用IVF索引类型")
    timeout: int = Field(30, description="查询超时设置，单位秒")
    enable_fallback: bool = Field(True, description="是否启用回退机制")
    address: str = Field("127.0.0.1:19530", description="Milvus服务的地址和端口")
    
    # Milvus 特有配置
    metric_type: str = Field("L2", description="欧几里得距离")
    index_file_size: int = Field(1024, description="索引文件大小，单位MB")
    
    # Qdrant 特有配置
    batch_size: int = Field(32, description="每次查询的批量大小")
    replication_factor: int = Field(2, description="数据副本数量")

class RAGRetrieverSettings(BaseModel):
    provider: str = Field("milvus", description="指定使用的向量数据库，Milvus或Qdrant")
    config: RAGRetrieverConfigSettings

class MemorySettings(BaseModel):
    search_memory: bool = Field(True)
    search_rag: bool = Field(True)
    rag_retriever_config: Optional[RAGRetrieverSettings] = Field(None, description="RAG检索器配置")

class MCPLocalSettings(BaseModel):
    transport: str = Field("streamable-http")
    host: str = Field("0.0.0.0")
    port: int = Field(9009, ge=1, le=65535)

class MCPServerSettings(BaseModel):
    url: str = Field("")
    transport: str = Field("sse")
    authorization: Optional[SecretStr] = Field(default=None)
    tool_prefix: str = Field("srv1")

class MCPMarketSettings(BaseModel):
    refresh_interval_seconds: int = Field(600)
    mcp_servers: List[MCPServerSettings] = Field(default_factory=list)

class MCPSettings(BaseModel):
    mcp_local: MCPLocalSettings = Field(default_factory=MCPLocalSettings)
    mcp_market: MCPMarketSettings = Field(default_factory=MCPMarketSettings)

class AuthProviderSettings(BaseModel):
    enabled: bool = Field(False)
    protocol: str = Field("oidc")
    client_id: Optional[SecretStr] = Field(default=None)
    client_secret: Optional[SecretStr] = Field(default=None)
    client_id_env: Optional[str] = Field(default=None)
    client_secret_env: Optional[str] = Field(default=None)
    redirect_uri: Optional[str] = Field(default=None)
    redirect_uri_env: Optional[str] = Field(default=None)
    issuer: Optional[str] = Field(default=None)
    authorize_url: Optional[str] = Field(default=None)
    token_url: Optional[str] = Field(default=None)
    jwks_url: Optional[str] = Field(default=None)
    userinfo_url: Optional[str] = Field(default=None)
    scopes: List[str] = Field(default_factory=list)
    subject_strategy: str = Field("sub")

    def resolved_client_id(self) -> str:
        if self.client_id_env and os.getenv(self.client_id_env):
            return os.getenv(self.client_id_env) or ""
        return reveal_secret(self.client_id)

    def resolved_client_secret(self) -> str:
        if self.client_secret_env and os.getenv(self.client_secret_env):
            return os.getenv(self.client_secret_env) or ""
        return reveal_secret(self.client_secret)

    def resolved_redirect_uri(self) -> str:
        if self.redirect_uri_env and os.getenv(self.redirect_uri_env):
            return os.getenv(self.redirect_uri_env) or ""
        return self.redirect_uri or ""


class AuthSettings(BaseModel):
    required: bool = Field(False)
    dev_user_id: str = Field("dev-user")
    admin_user_ids: List[str] = Field(default_factory=list)
    session_cookie_name: str = Field("tpa_session")
    session_ttl_seconds: int = Field(60 * 60 * 24 * 30, ge=60)
    cookie_secure: bool = Field(True)
    providers: Dict[str, AuthProviderSettings] = Field(
        default_factory=lambda: {
            "google": AuthProviderSettings(
                enabled=False,
                protocol="oidc",
                client_id_env="GOOGLE_CLIENT_ID",
                client_secret_env="GOOGLE_CLIENT_SECRET",
                redirect_uri_env="GOOGLE_REDIRECT_URI",
                issuer="https://accounts.google.com",
                scopes=["openid", "profile", "email"],
                subject_strategy="sub",
            ),
            "microsoft": AuthProviderSettings(
                enabled=False,
                protocol="oidc",
                client_id_env="MICROSOFT_CLIENT_ID",
                client_secret_env="MICROSOFT_CLIENT_SECRET",
                redirect_uri_env="MICROSOFT_REDIRECT_URI",
                issuer="https://login.microsoftonline.com",
                authorize_url="https://login.microsoftonline.com/common/oauth2/v2.0/authorize",
                token_url="https://login.microsoftonline.com/common/oauth2/v2.0/token",
                jwks_url="https://login.microsoftonline.com/common/discovery/v2.0/keys",
                scopes=["openid", "profile", "email"],
                subject_strategy="tenant_oid",
            ),
            "wechat": AuthProviderSettings(
                enabled=False,
                protocol="oauth2",
                client_id_env="WECHAT_APP_ID",
                client_secret_env="WECHAT_APP_SECRET",
                redirect_uri_env="WECHAT_REDIRECT_URI",
                authorize_url="https://open.weixin.qq.com/connect/qrconnect",
                token_url="https://api.weixin.qq.com/sns/oauth2/access_token",
                userinfo_url="https://api.weixin.qq.com/sns/userinfo",
                scopes=["snsapi_login"],
                subject_strategy="unionid_then_appid_openid",
            ),
        }
    )

class SearchSetting(BaseModel):
    provider: str = Field("jina", description="搜索服务提供方，如 jina/bocha/serper")
    api_key: SecretStr = Field(..., description="必填，可来自 .env / env / YAML / secrets")
    proxy: str | None = Field("", description="可选，HTTP(S) 代理地址")

class AgentSettings(BaseSettings):
    # 顶层
    env: str = Field("dev", description="dev/staging/prod")
    lang: str = Field("ch", description="Prompt language selector, e.g. 'ch' or 'en'")
    prompt_file: str = Field("../config/config.yaml")

    core: CoreSettings = Field(default_factory=CoreSettings)
    server: ServerSettings = Field(default_factory=ServerSettings)
    db: DBSettings
    llm: LLMSettings
    embedder: EmbeddingSettings
    vector_store: VectorStoreSettings
    memory: MemorySettings = Field(default_factory=MemorySettings)
    logging: LoggingSettings = Field(default_factory=LoggingSettings)
    mcp: MCPSettings = Field(default_factory=MCPSettings)
    browser_use: BrowserUseSettings = Field(default_factory=BrowserUseSettings)
    audio_llm: LLMSettings = Field(default_factory=LLMSettings)
    image_llm: LLMSettings = Field(default_factory=LLMSettings)
    video_llm: LLMSettings = Field(default_factory=LLMSettings)
    summary_agent: SummaryAgentSettings = Field(default_factory=SummaryAgentSettings)
    search: List[SearchSetting] = Field(default_factory=list)
    auth: AuthSettings = Field(default_factory=AuthSettings)

    # 统一配置
    model_config = SettingsConfigDict(
        env_file=".env",                 # 让 .env 覆盖 YAML
        env_file_encoding="utf-8",
        env_prefix="APP_",               # 环境变量前缀（例：APP_SERVER__PORT）
        env_nested_delimiter="__",       # 嵌套字段分隔符
        case_sensitive=False,
        secrets_dir="/run/secrets",      # 兼容 K8s/Docker secrets（低优先级）
        extra="ignore",
        validate_default=True,
    )

    # 自定义来源顺序：init > env > .env > YAML > /run/secrets
    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls,                      # ← 必须包含这个参数
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ):
        return(
            init_settings,
            env_settings,
            dotenv_settings,
            YamlConfigSettingsSource(settings_cls),  # YAML 作为较低优先级的"基准"
            #file_secret_settings,                    # /run/secrets 最后
        )

    def dump_with_secrets(self, **kwargs) -> Dict[str, Any]:
        data = self.model_dump(**kwargs)
        return reveal_secrets(data)


class YamlConfigSettingsSource(PydanticBaseSettingsSource):
    """
    从 YAML 加载默认配置。
    路径优先级：
      1) 环境变量 APP_CONFIG_FILE（或无前缀时 CONFIG_FILE）
      2) 默认 "config.yaml"
    """
    def __init__(self, settings_cls):
        super().__init__(settings_cls)
        self._prefix: str = (settings_cls.model_config or {}).get("env_prefix", "") or ""
        self._default_path = "../config/config.yaml"

    def __call__(self) -> dict:
        # 允许大小写两种写法
        key = f"{self._prefix}CONFIG_FILE" if self._prefix else "CONFIG_FILE"
        path = os.getenv(key) or os.getenv(key.upper()) or self._default_path

        try:
            with open(path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
                #print(f'load config file {path} successfully data: {data}')
        except FileNotFoundError:
            data = {}

        # 直接返回嵌套 dict，字段名需与模型一致（server/db/auth）
        return _normalize_auth_config_aliases(data)
    def get_field_value(self, field: FieldInfo, field_name: str) -> tuple[object, str, bool]:
        # YAML 用模型字段名（不走 alias）
        value = self._data.get(field_name, None)
        # 复杂值：交给 pydantic 去递归解析（嵌套模型/列表/字典等）
        is_complex = isinstance(value, (dict, list))
        return value, field_name, is_complex


def _normalize_auth_config_aliases(data: dict) -> dict:
    if not isinstance(data, dict):
        return data

    root_alias_keys = set(AUTH_CONFIG_ALIASES)
    for aliases in AUTH_PROVIDER_CONFIG_ALIASES.values():
        root_alias_keys.update(aliases)
    has_root_auth_alias = any(key in data for key in root_alias_keys)
    auth = data.get("auth")
    if not isinstance(auth, dict):
        if not has_root_auth_alias:
            return data
        auth = {}

    normalized = dict(data)
    auth_config = dict(auth)
    alias_source = {
        **{key: data[key] for key in root_alias_keys if key in data},
        **{key: auth_config[key] for key in root_alias_keys if key in auth_config},
    }

    for alias, field_name in AUTH_CONFIG_ALIASES.items():
        if alias in alias_source and field_name not in auth_config:
            value = alias_source[alias]
            if field_name == "admin_user_ids":
                value = _csv_to_list(value)
            auth_config[field_name] = value

    providers = dict(auth_config.get("providers") or {})
    for provider_name, aliases in AUTH_PROVIDER_CONFIG_ALIASES.items():
        provider_config = dict(providers.get(provider_name) or {})
        found_provider_alias = False
        for alias, field_name in aliases.items():
            if alias not in alias_source or field_name in provider_config:
                continue
            value = alias_source[alias]
            if field_name == "scopes":
                value = _csv_to_list(value)
            provider_config[field_name] = value
            found_provider_alias = True
        if found_provider_alias or provider_name in providers:
            providers[provider_name] = provider_config
    if providers:
        auth_config["providers"] = providers

    normalized["auth"] = auth_config
    return normalized



# 工厂函数
def get_settings() -> AgentSettings:
    settings = AgentSettings()
    _apply_auth_env_overrides(settings)
    return settings


def _apply_auth_env_overrides(settings: AgentSettings) -> None:
    bool_overrides = {
        "AUTH_REQUIRED": "required",
        "AUTH_COOKIE_SECURE": "cookie_secure",
    }
    for env_name, field_name in bool_overrides.items():
        value = _env_bool(env_name)
        if value is not None:
            setattr(settings.auth, field_name, value)

    ttl = _env_int("AUTH_SESSION_TTL_SECONDS")
    if ttl is not None:
        settings.auth.session_ttl_seconds = ttl

    if os.getenv("AUTH_SESSION_COOKIE_NAME"):
        settings.auth.session_cookie_name = os.getenv("AUTH_SESSION_COOKIE_NAME") or settings.auth.session_cookie_name
    if os.getenv("AUTH_DEV_USER_ID"):
        settings.auth.dev_user_id = os.getenv("AUTH_DEV_USER_ID") or settings.auth.dev_user_id
    if os.getenv("AUTH_ADMIN_USER_IDS"):
        settings.auth.admin_user_ids = _csv_to_list(os.getenv("AUTH_ADMIN_USER_IDS")) or []

    
agentSettings = get_settings()

if __name__ == "__main__":
    print(AgentSettings().model_dump_json(indent=2))
