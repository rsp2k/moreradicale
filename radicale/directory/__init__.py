"""
CardDAV Directory Gateway for Radicale.

Provides read-only CardDAV access to LDAP/Active Directory contacts.

Features:
- LDAP attribute to vCard property mapping
- Directory search via CardDAV REPORT
- Configurable attribute mappings
- Support for ldap3 module

Configuration:
    [directory]
    enabled = True
    ldap_uri = ldap://ldap.example.com
    ldap_base = ou=People,dc=example,dc=com
    ldap_reader_dn = cn=reader,dc=example,dc=com
    ldap_secret = password
    ldap_filter = (objectClass=inetOrgPerson)
    virtual_addressbook = /directory/contacts/
"""

from radicale.directory.gateway import DirectoryGateway
from radicale.directory.vcard_mapper import VCardMapper, DEFAULT_LDAP_MAPPING

__all__ = [
    "DirectoryGateway",
    "VCardMapper",
    "DEFAULT_LDAP_MAPPING",
]
