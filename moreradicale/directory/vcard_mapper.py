"""
LDAP to vCard Attribute Mapper.

Maps LDAP directory attributes to vCard 4.0 properties.
Supports standard LDAP schemas (inetOrgPerson, organizationalPerson)
and Active Directory attributes.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple
import uuid


# Default LDAP to vCard mapping
# Format: ldap_attr -> (vcard_prop, vcard_params, transform)
DEFAULT_LDAP_MAPPING = {
    # Names
    "cn": ("FN", {}, None),
    "displayName": ("FN", {}, None),
    "givenName": ("N", {"position": "given"}, None),
    "sn": ("N", {"position": "family"}, None),
    "initials": ("N", {"position": "additional"}, None),
    "title": ("TITLE", {}, None),

    # Organization
    "o": ("ORG", {"position": "organization"}, None),
    "ou": ("ORG", {"position": "unit"}, None),
    "company": ("ORG", {"position": "organization"}, None),  # AD
    "department": ("ORG", {"position": "unit"}, None),  # AD

    # Contact
    "mail": ("EMAIL", {"TYPE": "work"}, None),
    "telephoneNumber": ("TEL", {"TYPE": "work"}, None),
    "mobile": ("TEL", {"TYPE": "cell"}, None),
    "homePhone": ("TEL", {"TYPE": "home"}, None),
    "facsimileTelephoneNumber": ("TEL", {"TYPE": "fax"}, None),
    "pager": ("TEL", {"TYPE": "pager"}, None),

    # Address
    "street": ("ADR", {"position": "street", "TYPE": "work"}, None),
    "l": ("ADR", {"position": "locality", "TYPE": "work"}, None),
    "st": ("ADR", {"position": "region", "TYPE": "work"}, None),
    "postalCode": ("ADR", {"position": "code", "TYPE": "work"}, None),
    "c": ("ADR", {"position": "country", "TYPE": "work"}, None),
    "co": ("ADR", {"position": "country", "TYPE": "work"}, None),  # AD

    # URLs and identifiers
    "labeledURI": ("URL", {}, None),
    "wWWHomePage": ("URL", {}, None),  # AD
    "uid": ("UID", {}, "ldap_uid"),
    "employeeNumber": ("X-EMPLOYEE-ID", {}, None),

    # Photo
    "jpegPhoto": ("PHOTO", {"MEDIATYPE": "image/jpeg"}, "base64"),
    "thumbnailPhoto": ("PHOTO", {"MEDIATYPE": "image/jpeg"}, "base64"),  # AD

    # Notes
    "description": ("NOTE", {}, None),
    "info": ("NOTE", {}, None),  # AD

    # Manager relationship
    "manager": ("RELATED", {"TYPE": "supervisor"}, "dn_to_email"),
}


@dataclass
class VCardBuilder:
    """
    Builder for vCard 4.0 format.

    Incrementally builds a vCard from LDAP attributes.
    """
    uid: str = ""
    fn: str = ""
    n_family: str = ""
    n_given: str = ""
    n_additional: str = ""
    n_prefix: str = ""
    n_suffix: str = ""
    org_name: str = ""
    org_unit: str = ""
    title: str = ""
    emails: List[Tuple[str, str]] = field(default_factory=list)  # (email, type)
    phones: List[Tuple[str, str]] = field(default_factory=list)  # (number, type)
    addresses: Dict[str, Dict[str, str]] = field(default_factory=dict)  # type -> {part: value}
    url: str = ""
    photo: str = ""
    photo_mediatype: str = ""
    note: str = ""
    custom_props: List[Tuple[str, str, Dict]] = field(default_factory=list)

    def set_name(self, given: str = "", family: str = "", additional: str = "",
                 prefix: str = "", suffix: str = ""):
        """Set structured name components."""
        if given:
            self.n_given = given
        if family:
            self.n_family = family
        if additional:
            self.n_additional = additional
        if prefix:
            self.n_prefix = prefix
        if suffix:
            self.n_suffix = suffix

    def set_fn(self, fn: str):
        """Set formatted name."""
        self.fn = fn

    def add_email(self, email: str, email_type: str = "work"):
        """Add email address."""
        if email and (email, email_type) not in self.emails:
            self.emails.append((email, email_type))

    def add_phone(self, number: str, phone_type: str = "work"):
        """Add phone number."""
        if number and (number, phone_type) not in self.phones:
            self.phones.append((number, phone_type))

    def set_address_part(self, addr_type: str, part: str, value: str):
        """Set part of an address."""
        if addr_type not in self.addresses:
            self.addresses[addr_type] = {}
        self.addresses[addr_type][part] = value

    def set_org(self, name: str = "", unit: str = ""):
        """Set organization."""
        if name:
            self.org_name = name
        if unit:
            self.org_unit = unit

    def set_title(self, title: str):
        """Set job title."""
        self.title = title

    def set_url(self, url: str):
        """Set URL."""
        self.url = url

    def set_photo(self, data: str, mediatype: str = "image/jpeg"):
        """Set photo (base64 encoded)."""
        self.photo = data
        self.photo_mediatype = mediatype

    def set_note(self, note: str):
        """Set note."""
        self.note = note

    def add_custom(self, name: str, value: str, params: Dict = None):
        """Add custom property."""
        self.custom_props.append((name, value, params or {}))

    def build(self) -> str:
        """Build vCard string."""
        lines = [
            "BEGIN:VCARD",
            "VERSION:4.0",
        ]

        # UID (required)
        if not self.uid:
            self.uid = str(uuid.uuid4())
        lines.append(f"UID:{self.uid}")

        # FN (required)
        if not self.fn:
            self.fn = f"{self.n_given} {self.n_family}".strip() or "Unknown"
        lines.append(f"FN:{_escape_vcard(self.fn)}")

        # N (structured name)
        n_parts = [
            self.n_family,
            self.n_given,
            self.n_additional,
            self.n_prefix,
            self.n_suffix,
        ]
        lines.append("N:" + ";".join(_escape_vcard(p) for p in n_parts))

        # ORG
        if self.org_name or self.org_unit:
            org = _escape_vcard(self.org_name)
            if self.org_unit:
                org += ";" + _escape_vcard(self.org_unit)
            lines.append(f"ORG:{org}")

        # TITLE
        if self.title:
            lines.append(f"TITLE:{_escape_vcard(self.title)}")

        # EMAIL
        for email, email_type in self.emails:
            lines.append(f"EMAIL;TYPE={email_type}:{email}")

        # TEL
        for number, phone_type in self.phones:
            lines.append(f"TEL;TYPE={phone_type}:{number}")

        # ADR
        for addr_type, parts in self.addresses.items():
            # ADR components: PO Box, Extended, Street, City, Region, Postal, Country
            adr_parts = [
                parts.get("pobox", ""),
                parts.get("extended", ""),
                parts.get("street", ""),
                parts.get("locality", ""),
                parts.get("region", ""),
                parts.get("code", ""),
                parts.get("country", ""),
            ]
            adr_value = ";".join(_escape_vcard(p) for p in adr_parts)
            lines.append(f"ADR;TYPE={addr_type}:{adr_value}")

        # URL
        if self.url:
            lines.append(f"URL:{self.url}")

        # PHOTO
        if self.photo:
            # For vCard 4.0, use data URI
            lines.append(f"PHOTO:data:{self.photo_mediatype};base64,{self.photo}")

        # NOTE
        if self.note:
            lines.append(f"NOTE:{_escape_vcard(self.note)}")

        # Custom properties
        for name, value, params in self.custom_props:
            param_str = ""
            if params:
                param_str = ";" + ";".join(f"{k}={v}" for k, v in params.items())
            lines.append(f"{name}{param_str}:{_escape_vcard(value)}")

        lines.append("END:VCARD")

        return "\r\n".join(lines)


def _escape_vcard(value: str) -> str:
    """Escape special characters for vCard."""
    if not value:
        return ""
    # Escape backslash first
    value = value.replace("\\", "\\\\")
    # Escape semicolon, comma, newline
    value = value.replace(";", "\\;")
    value = value.replace(",", "\\,")
    value = value.replace("\n", "\\n")
    return value


class VCardMapper:
    """
    Maps LDAP entry attributes to vCard format.

    Uses configurable attribute mappings with support for
    custom transformations.
    """

    def __init__(self, mapping: Optional[Dict] = None):
        """
        Initialize mapper with attribute mapping.

        Args:
            mapping: LDAP attr -> (vcard_prop, params, transform) mapping
        """
        self.mapping = mapping or DEFAULT_LDAP_MAPPING

    def map_entry(self, dn: str, attrs: Dict[str, Any]) -> str:
        """
        Map LDAP entry to vCard string.

        Args:
            dn: Distinguished name of the entry
            attrs: LDAP attributes dictionary

        Returns:
            vCard 4.0 formatted string
        """
        builder = VCardBuilder()

        # Generate UID from DN
        builder.uid = self._dn_to_uid(dn)

        for ldap_attr, value in attrs.items():
            if ldap_attr not in self.mapping:
                continue

            vcard_prop, params, transform = self.mapping[ldap_attr]

            # Get first value if list
            if isinstance(value, list):
                value = value[0] if value else ""

            # Handle bytes
            if isinstance(value, bytes):
                if transform == "base64":
                    import base64
                    value = base64.b64encode(value).decode("ascii")
                else:
                    value = value.decode("utf-8", errors="replace")

            if not value:
                continue

            # Apply transform
            if transform == "ldap_uid":
                builder.uid = f"ldap-{value}"
            elif transform == "base64":
                # Already handled above
                pass

            # Map to vCard property
            position = params.get("position")
            prop_type = params.get("TYPE", "work")

            if vcard_prop == "FN":
                builder.set_fn(value)
            elif vcard_prop == "N":
                if position == "given":
                    builder.set_name(given=value)
                elif position == "family":
                    builder.set_name(family=value)
                elif position == "additional":
                    builder.set_name(additional=value)
            elif vcard_prop == "ORG":
                if position == "organization":
                    builder.set_org(name=value)
                elif position == "unit":
                    builder.set_org(unit=value)
            elif vcard_prop == "TITLE":
                builder.set_title(value)
            elif vcard_prop == "EMAIL":
                builder.add_email(value, prop_type)
            elif vcard_prop == "TEL":
                builder.add_phone(value, prop_type)
            elif vcard_prop == "ADR":
                builder.set_address_part(prop_type, position, value)
            elif vcard_prop == "URL":
                builder.set_url(value)
            elif vcard_prop == "PHOTO":
                mediatype = params.get("MEDIATYPE", "image/jpeg")
                builder.set_photo(value, mediatype)
            elif vcard_prop == "NOTE":
                builder.set_note(value)
            elif vcard_prop == "UID":
                builder.uid = value
            else:
                # Custom/extended property
                builder.add_custom(vcard_prop, value, params)

        return builder.build()

    def _dn_to_uid(self, dn: str) -> str:
        """Convert DN to a stable UID."""
        import hashlib
        # Use hash of DN for stable UID
        hash_val = hashlib.sha256(dn.encode()).hexdigest()[:16]
        return f"ldap-{hash_val}"

    def extract_search_filter(self, text: str) -> str:
        """
        Build LDAP filter for text search.

        Searches common name attributes.
        """
        # Escape special LDAP characters
        text = self._escape_ldap(text)

        # Build OR filter for common searchable attributes
        attrs = ["cn", "displayName", "givenName", "sn", "mail"]
        filters = [f"({attr}=*{text}*)" for attr in attrs]

        return f"(|{''.join(filters)})"

    def _escape_ldap(self, value: str) -> str:
        """Escape special characters for LDAP filter."""
        replacements = [
            ("\\", "\\5c"),
            ("*", "\\2a"),
            ("(", "\\28"),
            (")", "\\29"),
            ("\x00", "\\00"),
        ]
        for old, new in replacements:
            value = value.replace(old, new)
        return value
