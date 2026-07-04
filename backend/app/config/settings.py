"""配置加载（Phase 2）。

来源优先级（高 → 低）：
    1. 环境变量（嵌套定界符 "__"，如 REDIS__HOST、AWS__S3_BUCKET、STORAGE__TMP_DIR）
    2. config.local.yaml（本地覆盖，gitignored）
    3. config.yaml（项目根目录模板）
    4. 代码内默认值（与附录 B 一致）

密钥永不写入 YAML：AWS 凭证由 boto3 直接从标准环境变量 / IAM Role 读取。
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Literal

from pydantic import BaseModel, Field
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    YamlConfigSettingsSource,
)

# backend/app/config/settings.py → 项目根目录
PROJECT_ROOT = Path(__file__).resolve().parents[3]


class AppConfig(BaseModel):
    app_env: Literal["dev", "prod"] = "dev"
    log_level: str = "INFO"


class StorageConfig(BaseModel):
    tmp_dir: str = "./tmp"
    retention_seconds: int = 300  # 铁律 5：临时文件 ≤ 5 分钟

    def resolved_tmp_dir(self) -> Path:
        p = Path(self.tmp_dir)
        return p if p.is_absolute() else (PROJECT_ROOT / p).resolve()


class RedisConfig(BaseModel):
    host: str = "localhost"
    port: int = 6379
    db: int = 0

    @property
    def url(self) -> str:
        return f"redis://{self.host}:{self.port}/{self.db}"


class CompressionPreset(BaseModel):
    aggressiveness: float = Field(ge=0.0, le=1.0)


def _default_presets() -> Dict[str, CompressionPreset]:
    return {
        "print": CompressionPreset(aggressiveness=0.2),
        "screen": CompressionPreset(aggressiveness=0.5),
        "email": CompressionPreset(aggressiveness=0.8),
    }


class CompressionConfig(BaseModel):
    """压缩决策可调参数（《压缩决策引擎.md》第 9 章 / 附录 B）。"""

    # 三主控变量
    aggressiveness: float = Field(default=0.5, ge=0.0, le=1.0)
    area_sensitivity: float = Field(default=0.6, ge=0.0, le=1.0)
    pixel_ceiling_ratio: float = Field(default=1.2, gt=0.0)
    # 场景预设
    presets: Dict[str, CompressionPreset] = Field(default_factory=_default_presets)
    # 位图参数边界
    quality_floor: int = Field(default=30, ge=0, le=100)
    quality_ceiling: int = Field(default=95, ge=0, le=100)
    dpi_floor: int = Field(default=72, gt=0)
    dpi_ceiling: int = Field(default=300, gt=0)
    # 矢量策略阈值
    vector_ignore_area_ratio: float = Field(default=0.1, ge=0.0, le=1.0)
    vector_simplify_threshold: int = Field(default=10_000, gt=0)
    vector_rasterize_threshold: int = Field(default=100_000, gt=0)
    # 矢量输出成本估算系数（B/控制点；2026-07-04 Phase 10 实测标定 4.49 → 取 4.5）
    vector_bytes_per_control_point: float = Field(default=4.5, ge=1.0, le=10.0)
    # 收敛循环
    max_convergence_rounds: int = Field(default=5, gt=0)
    convergence_tolerance: float = Field(default=0.15, gt=0.0)
    aggressiveness_step_large: float = Field(default=0.15, gt=0.0)
    aggressiveness_step_small: float = Field(default=0.08, gt=0.0)
    gap_large_threshold: float = Field(default=0.3, gt=0.0)
    # Phase 8 派生行为常量（2026-07-04 迁入 config，《压缩决策引擎.md》第 9 章）
    area_tier_large: float = Field(default=0.6, ge=0.0, le=1.0)
    simplify_tolerance_conservative: float = Field(default=0.5, gt=0.0)
    simplify_tolerance_aggressive: float = Field(default=1.0, gt=0.0)
    rasterize_dpi_large: int = Field(default=200, gt=0)
    rasterize_dpi_medium: int = Field(default=150, gt=0)
    rasterize_dpi_small: int = Field(default=96, gt=0)
    vector_rasterize_min_area_ratio: float = Field(default=0.3, ge=0.0, le=1.0)
    rasterize_estimate_quality: int = Field(default=80, ge=0, le=100)
    jpeg_bpp_base: float = Field(default=0.04, gt=0.0)
    jpeg_bpp_quality_coeff: float = Field(default=0.12, ge=0.0)
    png_bytes_per_pixel: float = Field(default=0.35, gt=0.0)
    simplify_size_factor: float = Field(default=0.5, gt=0.0, le=1.0)
    budget_overshoot_tolerance: float = Field(default=1.1, ge=1.0)


class ClassifierConfig(BaseModel):
    """Phase 6 启发式分类阈值（迁入 config：2026-07-04 用户批准）。"""

    photo_area_threshold: float = Field(default=0.5, ge=0.0, le=1.0)
    photo_min_dpi: int = Field(default=100, gt=0)
    chart_vector_area_threshold: float = Field(default=0.3, ge=0.0, le=1.0)
    chart_complexity_threshold: int = Field(default=5000, gt=0)
    chart_max_raster_ratio: float = Field(default=0.3, ge=0.0, le=1.0)
    text_block_count_threshold: int = Field(default=15, gt=0)
    text_max_raster_ratio: float = Field(default=0.15, ge=0.0, le=1.0)
    text_max_vector_complexity: int = Field(default=2000, gt=0)
    complexity_score_slope: float = Field(default=10.0, gt=0.0)


class SessionConfig(BaseModel):
    review_session_ttl: int = 1800


class ApiConfig(BaseModel):
    max_upload_mb: int = 500
    cors_origins: List[str] = Field(default_factory=lambda: ["http://localhost:3000"])


class AwsConfig(BaseModel):
    s3_bucket: str = ""  # 留空 → 不启用 S3，使用本地存储
    region: str = ""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_nested_delimiter="__",
        extra="ignore",
        yaml_file=[PROJECT_ROOT / "config.yaml", PROJECT_ROOT / "config.local.yaml"],
        yaml_file_encoding="utf-8",
    )

    app: AppConfig = Field(default_factory=AppConfig)
    storage: StorageConfig = Field(default_factory=StorageConfig)
    redis: RedisConfig = Field(default_factory=RedisConfig)
    compression: CompressionConfig = Field(default_factory=CompressionConfig)
    classifier: ClassifierConfig = Field(default_factory=ClassifierConfig)
    session: SessionConfig = Field(default_factory=SessionConfig)
    api: ApiConfig = Field(default_factory=ApiConfig)
    aws: AwsConfig = Field(default_factory=AwsConfig)

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        # 靠前的源优先级高：env > yaml（config.local.yaml 覆盖 config.yaml）
        return (
            init_settings,
            env_settings,
            dotenv_settings,
            YamlConfigSettingsSource(settings_cls),
            file_secret_settings,
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()


def summarize_settings(settings: Settings) -> dict:
    """配置概况（隐藏敏感字段），供启动日志与 --check 输出。"""
    return {
        "app_env": settings.app.app_env,
        "log_level": settings.app.log_level,
        "tmp_dir": str(settings.storage.resolved_tmp_dir()),
        "retention_seconds": settings.storage.retention_seconds,
        "redis": f"{settings.redis.host}:{settings.redis.port}/db{settings.redis.db}",
        "s3_enabled": bool(settings.aws.s3_bucket),  # 只报告开关，不泄露 bucket 名
        "max_upload_mb": settings.api.max_upload_mb,
        "presets": {k: v.aggressiveness for k, v in settings.compression.presets.items()},
        "max_convergence_rounds": settings.compression.max_convergence_rounds,
    }
