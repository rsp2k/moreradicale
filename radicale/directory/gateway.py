"""
CardDAV Directory Gateway.

Provides read-only CardDAV access to LDAP/Active Directory entries.
Appears as a virtual address book collection in Radicale.
"""

import ssl
import threading
from typing import Dict, Iterator, List, Optional, Tuple, Any

from radicale import config
from radicale.log import logger
from radicale.directory.vcard_mapper import VCardMapper, DEFAULT_LDAP_MAPPING


class DirectoryGateway:
    """
    LDAP to CardDAV Gateway.

    Exposes LDAP directory entries as a virtual read-only address book.
    Supports both python-ldap and ldap3 modules.
    """

    def __init__(self, configuration: config.Configuration):
        """
        Initialize the directory gateway.

        Args:
            configuration: Radicale configuration
        """
        self._configuration = configuration
        self._enabled = configuration.get("directory", "enabled")

        if not self._enabled:
            return

        # Try to import LDAP module
        self._ldap_module_version = 3
        try:
            import ldap3
            self.ldap3 = ldap3
        except ImportError:
            try:
                import ldap
                import ldap.filter
                self._ldap_module_version = 2
                self.ldap = ldap
            except ImportError:
                logger.warning("Directory gateway requires ldap3 or python-ldap module")
                self._enabled = False
                return

        # Load configuration
        self._ldap_uri = configuration.get("directory", "ldap_uri")
        self._ldap_base = configuration.get("directory", "ldap_base")
        self._ldap_reader_dn = configuration.get("directory", "ldap_reader_dn")
        self._ldap_secret = configuration.get("directory", "ldap_secret")
        self._ldap_filter = configuration.get("directory", "ldap_filter")
        self._ldap_security = configuration.get("directory", "ldap_security")
        self._ldap_ssl_verify_mode = configuration.get("directory", "ldap_ssl_verify_mode")
        self._ldap_ssl_ca_file = configuration.get("directory", "ldap_ssl_ca_file")

        # Secret from file
        ldap_secret_file = configuration.get("directory", "ldap_secret_file")
        if ldap_secret_file:
            try:
                with open(ldap_secret_file, "r") as f:
                    self._ldap_secret = f.read().rstrip("\n")
            except Exception as e:
                logger.error("Failed to read LDAP secret file: %s", e)

        # Virtual address book path
        self._virtual_path = configuration.get("directory", "virtual_addressbook")
        if not self._virtual_path.endswith("/"):
            self._virtual_path += "/"

        # Attributes to fetch
        self._ldap_attributes = list(DEFAULT_LDAP_MAPPING.keys())

        # Cache settings
        self._cache_ttl = configuration.get("directory", "cache_ttl")
        self._cache: Dict[str, Tuple[float, str]] = {}  # dn -> (timestamp, vcard)
        self._cache_lock = threading.Lock()

        # vCard mapper
        self._mapper = VCardMapper()

        logger.info("Directory gateway enabled at %s", self._virtual_path)
        logger.info("directory.ldap_uri: %s", self._ldap_uri)
        logger.info("directory.ldap_base: %s", self._ldap_base)

    @property
    def enabled(self) -> bool:
        """Check if gateway is enabled."""
        return self._enabled

    @property
    def virtual_path(self) -> str:
        """Get virtual address book path."""
        return self._virtual_path

    def is_virtual_path(self, path: str) -> bool:
        """Check if path is handled by the directory gateway."""
        if not self._enabled:
            return False
        return path.startswith(self._virtual_path)

    def get_connection(self):
        """
        Get LDAP connection.

        Returns connection object (ldap3 or python-ldap).
        """
        if self._ldap_module_version == 3:
            return self._get_connection_ldap3()
        else:
            return self._get_connection_ldap2()

    def _get_connection_ldap3(self):
        """Get ldap3 connection."""
        if self._ldap_security in ("tls", "starttls"):
            verify_modes = {
                "NONE": ssl.CERT_NONE,
                "OPTIONAL": ssl.CERT_OPTIONAL,
                "REQUIRED": ssl.CERT_REQUIRED,
            }
            tls = self.ldap3.Tls(
                validate=verify_modes.get(self._ldap_ssl_verify_mode, ssl.CERT_REQUIRED)
            )
            if self._ldap_ssl_ca_file:
                tls = self.ldap3.Tls(
                    validate=verify_modes.get(self._ldap_ssl_verify_mode, ssl.CERT_REQUIRED),
                    ca_certs_file=self._ldap_ssl_ca_file
                )

            use_ssl = self._ldap_security == "tls"
            server = self.ldap3.Server(self._ldap_uri, use_ssl=use_ssl, tls=tls)
        else:
            server = self.ldap3.Server(self._ldap_uri)

        conn = self.ldap3.Connection(
            server,
            self._ldap_reader_dn,
            password=self._ldap_secret,
            auto_bind=False
        )

        if self._ldap_security == "starttls":
            conn.start_tls()

        if not conn.bind():
            raise RuntimeError(f"LDAP bind failed: {conn.result}")

        return conn

    def _get_connection_ldap2(self):
        """Get python-ldap connection."""
        conn = self.ldap.initialize(self._ldap_uri)
        conn.protocol_version = self.ldap.VERSION3
        conn.set_option(self.ldap.OPT_REFERRALS, 0)

        if self._ldap_security in ("tls", "starttls"):
            verify_modes = {
                "NONE": self.ldap.OPT_X_TLS_NEVER,
                "OPTIONAL": self.ldap.OPT_X_TLS_ALLOW,
                "REQUIRED": self.ldap.OPT_X_TLS_DEMAND,
            }
            conn.set_option(
                self.ldap.OPT_X_TLS_REQUIRE_CERT,
                verify_modes.get(self._ldap_ssl_verify_mode, self.ldap.OPT_X_TLS_DEMAND)
            )
            if self._ldap_ssl_ca_file:
                conn.set_option(self.ldap.OPT_X_TLS_CACERTFILE, self._ldap_ssl_ca_file)
            conn.set_option(self.ldap.OPT_X_TLS_NEWCTX, self.ldap.OPT_ON)

            if self._ldap_security == "starttls":
                conn.start_tls_s()

        conn.simple_bind_s(self._ldap_reader_dn, self._ldap_secret)
        return conn

    def list_entries(self, search_text: str = "") -> Iterator[Tuple[str, str, str]]:
        """
        List directory entries.

        Args:
            search_text: Optional text to filter results

        Yields:
            Tuples of (uid, filename, vcard_data)
        """
        if not self._enabled:
            return

        try:
            conn = self.get_connection()

            # Build search filter
            if search_text:
                search_filter = f"(&{self._ldap_filter}{self._mapper.extract_search_filter(search_text)})"
            else:
                search_filter = self._ldap_filter

            if self._ldap_module_version == 3:
                yield from self._list_entries_ldap3(conn, search_filter)
            else:
                yield from self._list_entries_ldap2(conn, search_filter)

        except Exception as e:
            logger.error("Directory gateway error: %s", e)

    def _list_entries_ldap3(self, conn, search_filter: str) -> Iterator[Tuple[str, str, str]]:
        """List entries using ldap3."""
        try:
            conn.search(
                search_base=self._ldap_base,
                search_filter=search_filter,
                search_scope=self.ldap3.SUBTREE,
                attributes=self._ldap_attributes
            )

            for entry in conn.entries:
                dn = entry.entry_dn
                attrs = entry.entry_attributes_as_dict

                # Map to vCard
                vcard = self._mapper.map_entry(dn, attrs)

                # Generate filename from uid
                uid = self._mapper._dn_to_uid(dn)
                filename = f"{uid}.vcf"

                yield uid, filename, vcard

        finally:
            conn.unbind()

    def _list_entries_ldap2(self, conn, search_filter: str) -> Iterator[Tuple[str, str, str]]:
        """List entries using python-ldap."""
        try:
            results = conn.search_s(
                self._ldap_base,
                self.ldap.SCOPE_SUBTREE,
                filterstr=search_filter,
                attrlist=self._ldap_attributes
            )

            for dn, attrs in results:
                if dn is None:
                    continue

                # Map to vCard
                vcard = self._mapper.map_entry(dn, attrs)

                # Generate filename from uid
                uid = self._mapper._dn_to_uid(dn)
                filename = f"{uid}.vcf"

                yield uid, filename, vcard

        finally:
            conn.unbind()

    def get_entry(self, uid: str) -> Optional[str]:
        """
        Get single directory entry by UID.

        Args:
            uid: Entry UID (from vCard)

        Returns:
            vCard data or None
        """
        if not self._enabled:
            return None

        # Check cache
        import time
        with self._cache_lock:
            if uid in self._cache:
                timestamp, vcard = self._cache[uid]
                if time.time() - timestamp < self._cache_ttl:
                    return vcard

        # Search for entry
        for entry_uid, _, vcard in self.list_entries():
            if entry_uid == uid:
                # Update cache
                with self._cache_lock:
                    self._cache[uid] = (time.time(), vcard)
                return vcard

        return None

    def get_collection_properties(self) -> Dict[str, str]:
        """
        Get properties for the virtual address book collection.

        Returns:
            Dictionary of DAV properties
        """
        return {
            "tag": "VADDRESSBOOK",
            "D:displayname": "Directory Contacts",
            "D:resourcetype": "collection addressbook",
            "CR:addressbook-description": "Read-only directory contacts from LDAP",
        }

    def supports_write(self) -> bool:
        """Check if collection supports write operations."""
        return False

    def count_entries(self) -> int:
        """Count total entries in directory."""
        count = 0
        for _ in self.list_entries():
            count += 1
        return count


def create_gateway(configuration: config.Configuration) -> DirectoryGateway:
    """
    Factory function to create directory gateway.

    Args:
        configuration: Radicale configuration

    Returns:
        DirectoryGateway instance
    """
    return DirectoryGateway(configuration)
