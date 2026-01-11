"""
Tenant-aware configuration management.

Provides per-tenant configuration overrides loaded from
/etc/radicale/tenants/{tenant_id}.conf files.
"""

import os
from configparser import RawConfigParser
from typing import Dict, Optional, TYPE_CHECKING

from radicale.log import logger

if TYPE_CHECKING:
    from radicale import config
    from radicale.tenant import TenantContext


class TenantAwareConfiguration:
    """
    Wraps Configuration to provide per-tenant overrides.

    Loads tenant-specific configuration files and merges them
    with the base configuration. Results are cached for performance.
    """

    def __init__(self, base_configuration: "config.Configuration") -> None:
        """
        Initialize tenant-aware configuration.

        Args:
            base_configuration: Global Radicale configuration
        """
        self._base = base_configuration
        self._tenant_configs: Dict[str, "config.Configuration"] = {}
        self._config_dir = base_configuration.get("tenant", "config_directory")

    def for_tenant(
        self,
        tenant_id: str,
        context: Optional["TenantContext"] = None
    ) -> "config.Configuration":
        """
        Get configuration for a specific tenant.

        Loads tenant-specific config file and merges with base config.
        Results are cached for performance.

        Args:
            tenant_id: Tenant identifier
            context: Optional tenant context for additional metadata

        Returns:
            Configuration with tenant-specific overrides applied
        """
        if tenant_id in self._tenant_configs:
            return self._tenant_configs[tenant_id]

        # Start with copy of base config
        tenant_config = self._base.copy()

        # Try to load tenant-specific config
        tenant_config_path = os.path.join(
            self._config_dir, f"{tenant_id}.conf"
        )

        if os.path.exists(tenant_config_path):
            self._load_tenant_config(tenant_config, tenant_config_path, tenant_id)
        else:
            logger.debug(
                "No tenant config file for %r at %s",
                tenant_id, tenant_config_path
            )

        self._tenant_configs[tenant_id] = tenant_config
        return tenant_config

    def _load_tenant_config(
        self,
        tenant_config: "config.Configuration",
        path: str,
        tenant_id: str
    ) -> None:
        """
        Load and merge tenant configuration file.

        Args:
            tenant_config: Configuration to update
            path: Path to tenant config file
            tenant_id: Tenant identifier for logging
        """
        try:
            parser = RawConfigParser()
            with open(path, encoding="utf-8") as f:
                parser.read_file(f)

            config_dict = {
                section: {
                    option: parser.get(section, option)
                    for option in parser.options(section)
                }
                for section in parser.sections()
            }

            tenant_config.update(
                config_dict,
                f"tenant config {tenant_id}",
                privileged=False
            )

            logger.info(
                "Loaded tenant config for %r from %s",
                tenant_id, path
            )

        except Exception as e:
            logger.warning(
                "Failed to load tenant config %s: %s",
                path, e
            )

    def invalidate_tenant(self, tenant_id: str) -> None:
        """
        Invalidate cached configuration for a tenant.

        Call this if tenant config file has been modified.

        Args:
            tenant_id: Tenant to invalidate
        """
        if tenant_id in self._tenant_configs:
            del self._tenant_configs[tenant_id]
            logger.debug("Invalidated config cache for tenant %r", tenant_id)

    def invalidate_all(self) -> None:
        """Invalidate all cached tenant configurations."""
        self._tenant_configs.clear()
        logger.debug("Invalidated all tenant config caches")

    @property
    def base(self) -> "config.Configuration":
        """Get the base (global) configuration."""
        return self._base
