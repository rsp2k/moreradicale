# Advanced Features Guide for moreradicale

This guide covers advanced features added to moreradicale beyond the core CalDAV/CardDAV functionality: Prometheus metrics, enhanced VTODO support, CardDAV directory gateway, WebSocket real-time sync, and RFC 3253 versioning.

## Table of Contents

1. [Prometheus Metrics](#prometheus-metrics)
   - [Quick Start](#metrics-quick-start)
   - [Configuration](#metrics-configuration)
   - [Available Metrics](#available-metrics)
   - [Integration Examples](#metrics-integration)
2. [Enhanced VTODO Support (RFC 9253)](#enhanced-vtodo-support-rfc-9253)
   - [Task Relationships](#task-relationships)
   - [Task Properties API](#task-properties-api)
   - [Filtering and Sorting](#filtering-and-sorting)
3. [CardDAV Directory Gateway](#carddav-directory-gateway)
   - [Overview](#directory-overview)
   - [LDAP Configuration](#ldap-configuration)
   - [Attribute Mapping](#attribute-mapping)
   - [Active Directory Integration](#active-directory-integration)
4. [WebSocket Real-time Sync](#websocket-real-time-sync)
   - [Protocol Overview](#websocket-protocol)
   - [Client Integration](#client-integration)
   - [JavaScript Example](#javascript-example)
5. [RFC 3253 Versioning (DeltaV)](#rfc-3253-versioning-deltav)
   - [Overview](#versioning-overview)
   - [Configuration](#versioning-configuration)
   - [Read-Only Versioning](#read-only-versioning)
   - [Write Operations (CHECKOUT/CHECKIN)](#write-operations-checkoutcheckin)
   - [Auto-Versioning](#auto-versioning)
   - [Version Properties](#version-properties)
   - [Version Labels](#version-labels-rfc-3253-8)
   - [Activities (Change Sets)](#activities-change-sets)
6. [Troubleshooting](#troubleshooting)

---

## Prometheus Metrics

moreradicale exposes operational metrics in Prometheus format for monitoring server health, performance, and usage patterns.

### Metrics Quick Start

Enable the metrics endpoint in your configuration:

```ini
[metrics]
enabled = True
endpoint = /.metrics
```

Access metrics at `http://localhost:5232/.metrics`

```bash
curl http://localhost:5232/.metrics
```

### Metrics Configuration

All options are in the `[metrics]` section:

| Option | Default | Description |
|--------|---------|-------------|
| `enabled` | `False` | Enable Prometheus metrics endpoint |
| `endpoint` | `/.metrics` | URL path for metrics endpoint |
| `require_auth` | `False` | Require authentication to access metrics |

#### Example Configuration

```ini
[metrics]
enabled = True
endpoint = /.metrics
require_auth = False
```

For production with authentication:

```ini
[metrics]
enabled = True
endpoint = /.metrics
require_auth = True

[auth]
type = htpasswd
htpasswd_filename = /etc/moreradicale/users
htpasswd_encryption = bcrypt
```

### Available Metrics

moreradicale exposes the following Prometheus metrics:

#### Request Metrics

| Metric | Type | Description |
|--------|------|-------------|
| `radicale_requests_total` | Counter | Total HTTP requests by method and status |
| `radicale_request_duration_seconds` | Histogram | Request processing time distribution |
| `radicale_request_size_bytes` | Histogram | Request body size distribution |
| `radicale_response_size_bytes` | Histogram | Response body size distribution |

**Labels:**
- `method`: HTTP method (GET, PUT, DELETE, PROPFIND, etc.)
- `status`: HTTP status code category (2xx, 4xx, 5xx)
- `path_type`: Request path type (item, collection, root)

#### Storage Metrics

| Metric | Type | Description |
|--------|------|-------------|
| `radicale_storage_operations_total` | Counter | Storage operations by type |
| `radicale_collections_total` | Gauge | Number of collections |
| `radicale_items_total` | Gauge | Number of items across all collections |
| `radicale_storage_bytes` | Gauge | Total storage size in bytes |

**Labels:**
- `operation`: Operation type (read, write, delete, list)

#### Authentication Metrics

| Metric | Type | Description |
|--------|------|-------------|
| `radicale_auth_attempts_total` | Counter | Authentication attempts by result |
| `radicale_auth_failures_total` | Counter | Failed authentication attempts |

**Labels:**
- `result`: Authentication result (success, failure)
- `type`: Authentication backend type

#### Calendar/Scheduling Metrics

| Metric | Type | Description |
|--------|------|-------------|
| `radicale_scheduling_requests_total` | Counter | Scheduling requests processed |
| `radicale_invitations_sent_total` | Counter | Meeting invitations sent |

#### WebSocket Metrics

| Metric | Type | Description |
|--------|------|-------------|
| `radicale_websync_connections` | Gauge | Active WebSocket connections |
| `radicale_websync_subscriptions` | Gauge | Active collection subscriptions |
| `radicale_websync_notifications_total` | Counter | Notifications sent |

### Metrics Integration

#### Prometheus Configuration

Add moreradicale as a scrape target in `prometheus.yml`:

```yaml
scrape_configs:
  - job_name: 'radicale'
    static_configs:
      - targets: ['localhost:5232']
    metrics_path: /.metrics
    scrape_interval: 15s
```

With authentication:

```yaml
scrape_configs:
  - job_name: 'radicale'
    static_configs:
      - targets: ['localhost:5232']
    metrics_path: /.metrics
    basic_auth:
      username: prometheus
      password: your-password
```

#### Grafana Dashboard

Example Grafana queries:

**Request Rate:**
```promql
rate(radicale_requests_total[5m])
```

**Request Latency (p95):**
```promql
histogram_quantile(0.95, rate(radicale_request_duration_seconds_bucket[5m]))
```

**Active Collections:**
```promql
radicale_collections_total
```

**Auth Failure Rate:**
```promql
rate(radicale_auth_failures_total[5m])
```

#### Example Metrics Output

```
# HELP radicale_requests_total Total HTTP requests
# TYPE radicale_requests_total counter
radicale_requests_total{method="GET",status="2xx",path_type="item"} 1542
radicale_requests_total{method="PUT",status="2xx",path_type="item"} 328
radicale_requests_total{method="PROPFIND",status="2xx",path_type="collection"} 892

# HELP radicale_request_duration_seconds Request processing time
# TYPE radicale_request_duration_seconds histogram
radicale_request_duration_seconds_bucket{method="GET",le="0.005"} 1200
radicale_request_duration_seconds_bucket{method="GET",le="0.01"} 1400
radicale_request_duration_seconds_bucket{method="GET",le="0.025"} 1500
radicale_request_duration_seconds_bucket{method="GET",le="0.05"} 1530
radicale_request_duration_seconds_bucket{method="GET",le="0.1"} 1540
radicale_request_duration_seconds_bucket{method="GET",le="+Inf"} 1542
radicale_request_duration_seconds_sum{method="GET"} 12.45
radicale_request_duration_seconds_count{method="GET"} 1542

# HELP radicale_collections_total Number of collections
# TYPE radicale_collections_total gauge
radicale_collections_total 45

# HELP radicale_items_total Number of items
# TYPE radicale_items_total gauge
radicale_items_total 2847

# HELP radicale_websync_connections Active WebSocket connections
# TYPE radicale_websync_connections gauge
radicale_websync_connections 12
```

---

## Enhanced VTODO Support (RFC 9253)

moreradicale implements RFC 9253 Task Extensions, providing structured task relationships, dependencies, and hierarchies for VTODO components.

### Task Relationships

RFC 9253 extends the RELATED-TO property with relationship types that enable complex task management workflows.

#### Relationship Types

| RELTYPE | Description | Use Case |
|---------|-------------|----------|
| `PARENT` | Parent task in hierarchy | Project > Phase > Task |
| `CHILD` | Child task (inverse of PARENT) | Sub-task of a larger task |
| `SIBLING` | Related task at same level | Alternative approaches |
| `DEPENDS-ON` | Prerequisite task | Blocking dependencies |
| `REFID` | Reference identifier | Cross-system linking |

#### iCalendar Syntax

```
BEGIN:VTODO
UID:task-001@example.com
DTSTAMP:20251230T100000Z
SUMMARY:Write Documentation
RELATED-TO;RELTYPE=PARENT:project-001@example.com
RELATED-TO;RELTYPE=DEPENDS-ON:task-000@example.com
STATUS:IN-PROCESS
PERCENT-COMPLETE:50
END:VTODO
```

#### Building Task Hierarchies

moreradicale provides utilities for building and querying task hierarchies:

```python
from moreradicale.vtodo.relationships import (
    extract_relationships,
    build_task_hierarchy,
    validate_no_cycles,
    RelationType
)

# Extract relationships from a VTODO
relationships = extract_relationships(vtodo_component)
# Returns: [TaskRelationship(rel_type=RelationType.PARENT, related_uid="project-001")]

# Build hierarchy from multiple VTODOs
hierarchy = build_task_hierarchy(vtodo_list)
# Returns: {
#   "task-001": {
#       "parents": ["project-001"],
#       "children": ["subtask-001", "subtask-002"],
#       "depends_on": ["task-000"],
#       "siblings": []
#   }
# }

# Validate no circular dependencies
is_valid = validate_no_cycles(hierarchy)
```

### Task Properties API

Extract and manipulate VTODO properties programmatically:

```python
from moreradicale.vtodo.properties import get_task_properties

properties = get_task_properties(vtodo_component)
# Returns: {
#     "uid": "task-001@example.com",
#     "summary": "Write Documentation",
#     "status": "IN-PROCESS",
#     "percent_complete": 50,
#     "priority": 5,
#     "due": datetime(2025, 12, 31, 17, 0, 0),
#     "categories": ["work", "documentation"],
#     "created": datetime(2025, 12, 20, 10, 0, 0),
#     "last_modified": datetime(2025, 12, 28, 14, 30, 0)
# }
```

#### Supported Properties

| Property | Type | Description |
|----------|------|-------------|
| `uid` | str | Unique identifier |
| `summary` | str | Task title |
| `description` | str | Detailed description |
| `status` | str | NEEDS-ACTION, IN-PROCESS, COMPLETED, CANCELLED |
| `percent_complete` | int | Completion percentage (0-100) |
| `priority` | int | Priority (1=highest, 9=lowest, 0=undefined) |
| `due` | datetime | Due date/time |
| `dtstart` | datetime | Start date/time |
| `completed` | datetime | Completion timestamp |
| `categories` | list | Category tags |

### Filtering and Sorting

#### Filter by Status

```python
from moreradicale.vtodo.properties import filter_tasks_by_status

# Get incomplete tasks
incomplete = filter_tasks_by_status(vtodo_list, ["NEEDS-ACTION", "IN-PROCESS"])

# Get completed tasks
completed = filter_tasks_by_status(vtodo_list, ["COMPLETED"])
```

#### Filter by Completion Percentage

```python
from moreradicale.vtodo.properties import filter_tasks_by_percent_range

# Tasks 50-100% complete
nearly_done = filter_tasks_by_percent_range(vtodo_list, min_percent=50, max_percent=100)

# Tasks not yet started
not_started = filter_tasks_by_percent_range(vtodo_list, min_percent=0, max_percent=0)
```

#### Sort by Priority

```python
from moreradicale.vtodo.properties import sort_tasks_by_priority

# High priority first (1 before 9)
sorted_tasks = sort_tasks_by_priority(vtodo_list, ascending=True)
```

#### Sort by Due Date

```python
from moreradicale.vtodo.properties import sort_tasks_by_due

# Soonest due first
sorted_tasks = sort_tasks_by_due(vtodo_list, ascending=True)

# Tasks without due dates appear last
```

### Task Lifecycle Example

```
BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//moreradicale//RFC9253//EN

# Parent project
BEGIN:VTODO
UID:project-web@example.com
SUMMARY:Website Redesign
STATUS:IN-PROCESS
PERCENT-COMPLETE:25
PRIORITY:3
END:VTODO

# Child task with dependency
BEGIN:VTODO
UID:task-design@example.com
SUMMARY:Design mockups
RELATED-TO;RELTYPE=PARENT:project-web@example.com
RELATED-TO;RELTYPE=DEPENDS-ON:task-research@example.com
STATUS:NEEDS-ACTION
PRIORITY:5
DUE:20260115T170000Z
END:VTODO

# Prerequisite task
BEGIN:VTODO
UID:task-research@example.com
SUMMARY:User research
RELATED-TO;RELTYPE=PARENT:project-web@example.com
STATUS:COMPLETED
COMPLETED:20260105T150000Z
PERCENT-COMPLETE:100
END:VTODO

END:VCALENDAR
```

---

## CardDAV Directory Gateway

The CardDAV Directory Gateway provides read-only access to LDAP/Active Directory contacts as vCards, enabling enterprise contact lookup from any CardDAV client.

### Directory Overview

| Feature | Description |
|---------|-------------|
| LDAP Integration | Connect to OpenLDAP, 389DS, FreeIPA |
| Active Directory | Full AD/Azure AD support |
| vCard 4.0 | RFC 6350 compliant output |
| Attribute Mapping | Configurable LDAP to vCard mapping |
| Caching | TTL-based entry caching |
| Read-only | Directory entries cannot be modified via CalDAV |

### LDAP Configuration

Add to your moreradicale configuration:

```ini
[directory]
enabled = True
type = ldap
ldap_uri = ldap://ldap.example.com:389
ldap_base_dn = ou=People,dc=example,dc=com
ldap_bind_dn = cn=radicale,ou=Services,dc=example,dc=com
ldap_bind_password = your-password
ldap_filter = (objectClass=inetOrgPerson)
ldap_scope = subtree
cache_ttl = 300
```

#### Configuration Options

| Option | Default | Description |
|--------|---------|-------------|
| `enabled` | `False` | Enable directory gateway |
| `type` | `ldap` | Directory type (ldap) |
| `ldap_uri` | `ldap://localhost:389` | LDAP server URI |
| `ldap_base_dn` | `""` | Base DN for searches |
| `ldap_bind_dn` | `""` | Bind DN (empty for anonymous) |
| `ldap_bind_password` | `""` | Bind password |
| `ldap_filter` | `(objectClass=person)` | Search filter |
| `ldap_scope` | `subtree` | Search scope (base, onelevel, subtree) |
| `ldap_tls` | `False` | Enable STARTTLS |
| `ldap_tls_cacert` | `""` | CA certificate path |
| `ldap_timeout` | `10` | Connection timeout (seconds) |
| `cache_ttl` | `300` | Cache TTL in seconds |

### Attribute Mapping

Default LDAP to vCard attribute mapping:

| LDAP Attribute | vCard Property | Description |
|----------------|----------------|-------------|
| `cn` | `FN` | Full name |
| `givenName` | `N` (given) | First name |
| `sn` | `N` (family) | Last name |
| `mail` | `EMAIL` | Email address |
| `telephoneNumber` | `TEL;TYPE=work` | Work phone |
| `mobile` | `TEL;TYPE=cell` | Mobile phone |
| `homePhone` | `TEL;TYPE=home` | Home phone |
| `facsimileTelephoneNumber` | `TEL;TYPE=fax` | Fax number |
| `title` | `TITLE` | Job title |
| `o` / `organizationName` | `ORG` | Organization |
| `ou` / `department` | `ORG` (unit) | Department |
| `street` / `postalAddress` | `ADR` | Street address |
| `l` / `locality` | `ADR` (locality) | City |
| `st` | `ADR` (region) | State/Province |
| `postalCode` | `ADR` (postal) | Postal code |
| `c` / `co` | `ADR` (country) | Country |
| `jpegPhoto` | `PHOTO` | Photo (base64) |
| `description` | `NOTE` | Notes |
| `labeledURI` | `URL` | Website |
| `uid` | `UID` | Unique identifier |

#### Custom Attribute Mapping

Override the default mapping with a JSON file:

```json
{
  "mail": "EMAIL;TYPE=work",
  "personalEmail": "EMAIL;TYPE=home",
  "manager": "RELATED;TYPE=supervisor",
  "employeeNumber": "X-EMPLOYEE-ID"
}
```

Configure the custom mapping:

```ini
[directory]
attribute_mapping_file = /etc/moreradicale/ldap-mapping.json
```

### Active Directory Integration

Configuration for Microsoft Active Directory:

```ini
[directory]
enabled = True
type = ldap
ldap_uri = ldap://dc.example.com:389
ldap_base_dn = OU=Users,DC=example,DC=com
ldap_bind_dn = CN=moreradicale Service,OU=Services,DC=example,DC=com
ldap_bind_password = your-ad-password
ldap_filter = (&(objectClass=user)(objectCategory=person)(!(userAccountControl:1.2.840.113556.1.4.803:=2)))
ldap_scope = subtree
ldap_tls = True
ldap_tls_cacert = /etc/ssl/certs/ad-ca.pem
```

#### AD-Specific Filter Explained

```
(&
  (objectClass=user)           # User objects
  (objectCategory=person)      # Person category (excludes computers)
  (!(userAccountControl:1.2.840.113556.1.4.803:=2))  # Not disabled
)
```

#### Azure AD via LDAPS

```ini
[directory]
ldap_uri = ldaps://ldap.example.com:636
ldap_tls = True
```

### Accessing Directory Contacts

Directory contacts appear as a read-only address book:

```
/.directory/                    # Directory root
/.directory/contacts/           # All contacts
/.directory/contacts/jdoe.vcf   # Individual contact
```

#### PROPFIND Request

```bash
curl -X PROPFIND \
  -u alice:password \
  -H "Depth: 1" \
  https://server/.directory/contacts/
```

#### GET Contact

```bash
curl -u alice:password \
  https://server/.directory/contacts/jdoe.vcf
```

Returns vCard 4.0:

```
BEGIN:VCARD
VERSION:4.0
UID:jdoe
FN:John Doe
N:Doe;John;;;
EMAIL;TYPE=work:john.doe@example.com
TEL;TYPE=work:+1-555-123-4567
TEL;TYPE=cell:+1-555-987-6543
TITLE:Software Engineer
ORG:Example Corp;Engineering
ADR;TYPE=work:;;123 Main St;Anytown;CA;12345;USA
END:VCARD
```

### Client Configuration

Configure CardDAV clients to access the directory:

| Client | URL |
|--------|-----|
| macOS/iOS Contacts | `https://server/.directory/contacts/` |
| Thunderbird | `https://server/.directory/contacts/` |
| DAVx5 (Android) | `https://server/.directory/contacts/` |

The directory appears as a read-only address book alongside personal contacts.

---

## WebSocket Real-time Sync

WebSocket Real-time Sync provides push-based change notifications, eliminating the need for clients to poll for updates.

### WebSocket Protocol

#### Configuration

```ini
[websync]
enabled = True
require_auth = True
ping_interval = 30
```

| Option | Default | Description |
|--------|---------|-------------|
| `enabled` | `False` | Enable WebSocket sync |
| `require_auth` | `True` | Require authentication |
| `ping_interval` | `30` | Seconds between ping frames |

#### Connection Endpoint

WebSocket upgrade at: `wss://server/.websync`

#### Handshake

```
GET /.websync HTTP/1.1
Host: server.example.com
Upgrade: websocket
Connection: Upgrade
Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==
Sec-WebSocket-Version: 13
Authorization: Basic YWxpY2U6cGFzc3dvcmQ=
```

Response:

```
HTTP/1.1 101 Switching Protocols
Upgrade: websocket
Connection: Upgrade
Sec-WebSocket-Accept: s3pPLMBiTxaQ9kYGzzhZRbK+xOo=
X-WebSync-Connection-ID: uuid-connection-id
```

### Client Messages

Clients send JSON messages to the server:

#### Subscribe to Collection

```json
{
  "action": "subscribe",
  "path": "/alice/calendar/"
}
```

Response:
```json
{
  "status": "subscribed",
  "path": "/alice/calendar/"
}
```

#### Unsubscribe

```json
{
  "action": "unsubscribe",
  "path": "/alice/calendar/"
}
```

#### Ping (Keep-alive)

```json
{
  "action": "ping",
  "timestamp": 1704067200000
}
```

Response:
```json
{
  "action": "pong",
  "timestamp": 1704067200000
}
```

#### Status Query

```json
{
  "action": "status"
}
```

Response:
```json
{
  "status": "ok",
  "stats": {
    "active_connections": 12,
    "total_notifications": 1542,
    "subscription_count": 28
  }
}
```

### Server Notifications

Server pushes change notifications to subscribed clients:

#### Item Created

```json
{
  "type": "create",
  "path": "/alice/calendar/event-123.ics",
  "sync_token": "http://radicale.org/sync/MTcwNDA2NzIwMA==",
  "etag": "\"abc123\"",
  "timestamp": 1704067200.123
}
```

#### Item Updated

```json
{
  "type": "update",
  "path": "/alice/calendar/event-123.ics",
  "sync_token": "http://radicale.org/sync/MTcwNDA2NzIwMQ==",
  "etag": "\"def456\"",
  "timestamp": 1704067260.456
}
```

#### Item Deleted

```json
{
  "type": "delete",
  "path": "/alice/calendar/event-123.ics",
  "sync_token": "http://radicale.org/sync/MTcwNDA2NzMwMA==",
  "timestamp": 1704067320.789
}
```

#### Collection Sync

```json
{
  "type": "sync",
  "path": "/alice/calendar/",
  "sync_token": "http://radicale.org/sync/MTcwNDA2NzQwMA==",
  "timestamp": 1704067380.012
}
```

### Notification Types

| Type | Description | Use Case |
|------|-------------|----------|
| `create` | New item added | Show new event notification |
| `update` | Item modified | Refresh item display |
| `delete` | Item removed | Remove from local cache |
| `sync` | Collection changed | Trigger sync-collection |
| `collection` | Collection metadata changed | Update collection properties |

### Client Integration

#### JavaScript Example

```javascript
class RadicaleWebSync {
  constructor(baseUrl, credentials) {
    this.baseUrl = baseUrl.replace(/^http/, 'ws');
    this.credentials = credentials;
    this.ws = null;
    this.subscriptions = new Set();
    this.onNotification = null;
  }

  connect() {
    return new Promise((resolve, reject) => {
      const url = `${this.baseUrl}/.websync`;
      this.ws = new WebSocket(url);

      // Set auth header via subprotocol or query param
      // Note: WebSocket doesn't support custom headers in browsers
      // Use session cookie or token-based auth instead

      this.ws.onopen = () => {
        console.log('WebSync connected');
        // Resubscribe to previous subscriptions
        this.subscriptions.forEach(path => {
          this._send({ action: 'subscribe', path });
        });
        resolve();
      };

      this.ws.onmessage = (event) => {
        const data = JSON.parse(event.data);

        if (data.type && this.onNotification) {
          // Server notification
          this.onNotification(data);
        } else if (data.status === 'subscribed') {
          console.log(`Subscribed to ${data.path}`);
        } else if (data.action === 'pong') {
          console.log('Pong received');
        }
      };

      this.ws.onerror = (error) => {
        console.error('WebSync error:', error);
        reject(error);
      };

      this.ws.onclose = () => {
        console.log('WebSync disconnected');
        // Attempt reconnect after delay
        setTimeout(() => this.connect(), 5000);
      };
    });
  }

  subscribe(path) {
    this.subscriptions.add(path);
    if (this.ws?.readyState === WebSocket.OPEN) {
      this._send({ action: 'subscribe', path });
    }
  }

  unsubscribe(path) {
    this.subscriptions.delete(path);
    if (this.ws?.readyState === WebSocket.OPEN) {
      this._send({ action: 'unsubscribe', path });
    }
  }

  ping() {
    this._send({ action: 'ping', timestamp: Date.now() });
  }

  _send(data) {
    if (this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(data));
    }
  }

  close() {
    if (this.ws) {
      this.ws.close();
      this.ws = null;
    }
  }
}

// Usage
const sync = new RadicaleWebSync('https://calendar.example.com', {
  username: 'alice',
  password: 'secret'
});

sync.onNotification = (notification) => {
  console.log(`Change detected: ${notification.type} on ${notification.path}`);

  switch (notification.type) {
    case 'create':
      // Fetch new item
      fetchAndDisplayItem(notification.path);
      break;
    case 'update':
      // Refresh existing item
      refreshItem(notification.path, notification.etag);
      break;
    case 'delete':
      // Remove from UI
      removeItemFromDisplay(notification.path);
      break;
    case 'sync':
      // Trigger full sync
      syncCollection(notification.path, notification.sync_token);
      break;
  }
};

await sync.connect();
sync.subscribe('/alice/calendar/');
sync.subscribe('/alice/contacts/');

// Keep-alive ping every 25 seconds
setInterval(() => sync.ping(), 25000);
```

#### Python Example

```python
import asyncio
import json
import websockets

async def websync_client():
    uri = "wss://calendar.example.com/.websync"

    # Note: websockets library supports basic auth in URI
    async with websockets.connect(uri) as ws:
        # Subscribe to calendar
        await ws.send(json.dumps({
            "action": "subscribe",
            "path": "/alice/calendar/"
        }))

        # Process messages
        async for message in ws:
            data = json.loads(message)

            if "type" in data:
                print(f"Notification: {data['type']} on {data['path']}")

                if data["type"] == "update":
                    # Fetch updated item
                    await fetch_item(data["path"])

            elif data.get("status") == "subscribed":
                print(f"Subscribed to {data['path']}")

asyncio.run(websync_client())
```

### Integration with CalDAV Clients

WebSync is designed to complement, not replace, traditional CalDAV sync:

1. **Initial Sync**: Use CalDAV sync-collection (REPORT) to get current state
2. **Subscribe**: Connect via WebSocket and subscribe to collections
3. **Push Updates**: Receive real-time notifications of changes
4. **Efficient Sync**: On notification, use sync-token to fetch only changes

This pattern reduces polling from every 5-15 minutes to instant notifications.

### Access Control

- Users can only subscribe to collections they have read access to
- The `_can_access_path()` check validates subscriptions
- Notifications are filtered: users don't receive notifications for changes they made themselves

---

## RFC 3253 Versioning (DeltaV)

moreradicale implements RFC 3253 WebDAV Versioning (DeltaV), providing git-backed version history for calendar and contact items. This enables viewing historical versions, comparing changes, and using explicit checkout/checkin workflows.

### Versioning Overview

| Feature | Description |
|---------|-------------|
| Git Backend | Version history stored in git repository |
| Read Operations | View version history, retrieve old versions |
| Write Operations | CHECKOUT, CHECKIN, UNCHECKOUT, VERSION-CONTROL |
| Auto-Versioning | Automatic commits on PUT operations |
| VERSION-TREE Report | RFC 3253 compliant version history report |

### Versioning Configuration

Enable versioning in your configuration:

```ini
[storage]
# Enable RFC 3253 versioning
versioning = True

# Maximum versions to return in history (default: 100)
versioning_max_history = 100

# Auto-versioning on PUT: disabled | checkout-checkin
versioning_auto = checkout-checkin

# Fork policy for concurrent checkouts: forbidden | discouraged | ok
versioning_checkout_fork = forbidden

# Checkout timeout in seconds (0 = never expire)
versioning_checkout_timeout = 3600
```

#### Configuration Options

| Option | Default | Description |
|--------|---------|-------------|
| `versioning` | `False` | Enable RFC 3253 versioning support |
| `versioning_max_history` | `100` | Maximum versions returned per item |
| `versioning_auto` | `disabled` | Auto-commit on PUT operations |
| `versioning_checkout_fork` | `forbidden` | Policy for concurrent checkouts |
| `versioning_checkout_timeout` | `3600` | Checkout expiration in seconds |

#### Prerequisites

Versioning requires:
1. Git installed and available in PATH
2. Storage folder initialized as a git repository

```bash
# Initialize git in storage folder
cd /var/lib/moreradicale/collections
git init
git config user.email "radicale@localhost"
git config user.name "moreradicale"
```

### Read-Only Versioning

#### Accessing Version History

Version history is available at virtual `/.versions/` paths:

```
/.versions/{user}/{collection}/{item}/           # Version history (XML)
/.versions/{user}/{collection}/{item}/{sha}      # Specific version content
```

#### Get Version History

```bash
# List all versions of an event
curl -u alice:password \
  https://server/.versions/alice/calendar.ics/event.ics/
```

Returns XML with version list:

```xml
<?xml version="1.0" encoding="utf-8"?>
<D:version-tree xmlns:D="DAV:">
  <D:version>
    <D:href>/.versions/alice/calendar.ics/event.ics/abc12345</D:href>
    <D:version-name>abc12345</D:version-name>
    <D:creator-displayname>alice</D:creator-displayname>
    <D:getlastmodified>Mon, 13 Jan 2025 10:30:00 GMT</D:getlastmodified>
    <D:comment>Update meeting time</D:comment>
  </D:version>
  <D:version>
    <D:href>/.versions/alice/calendar.ics/event.ics/def67890</D:href>
    <D:version-name>def67890</D:version-name>
    <D:creator-displayname>alice</D:creator-displayname>
    <D:getlastmodified>Sun, 12 Jan 2025 14:00:00 GMT</D:getlastmodified>
    <D:comment>Initial creation</D:comment>
  </D:version>
</D:version-tree>
```

#### Get Specific Version Content

```bash
# Retrieve content from a specific version
curl -u alice:password \
  https://server/.versions/alice/calendar.ics/event.ics/def67890
```

Returns the iCalendar content as it existed at that version.

#### VERSION-TREE Report

Use the RFC 3253 VERSION-TREE report for version history:

```bash
curl -X REPORT \
  -u alice:password \
  -H "Content-Type: application/xml" \
  -d '<?xml version="1.0"?>
      <D:version-tree xmlns:D="DAV:">
        <D:prop>
          <D:version-name/>
          <D:creator-displayname/>
          <D:getlastmodified/>
        </D:prop>
      </D:version-tree>' \
  https://server/alice/calendar.ics/event.ics
```

### Write Operations (CHECKOUT/CHECKIN)

RFC 3253 defines an explicit versioning workflow:

1. **CHECKOUT**: Lock item for editing
2. **Modify**: Update the item with PUT
3. **CHECKIN**: Create new version and unlock

#### CHECKOUT - Start Editing

```bash
# Check out an item for editing
curl -X CHECKOUT \
  -u alice:password \
  https://server/alice/calendar.ics/event.ics
```

Response (200 OK):

```xml
<?xml version="1.0" encoding="utf-8"?>
<D:multistatus xmlns:D="DAV:">
  <D:response>
    <D:href>/alice/calendar.ics/event.ics</D:href>
    <D:propstat>
      <D:prop>
        <D:checked-out>
          <D:href>/.versions/alice/calendar.ics/event.ics/abc12345</D:href>
        </D:checked-out>
      </D:prop>
      <D:status>HTTP/1.1 200 OK</D:status>
    </D:propstat>
  </D:response>
</D:multistatus>
```

#### Modify the Item

```bash
# Update the checked-out item
curl -X PUT \
  -u alice:password \
  -H "Content-Type: text/calendar" \
  -d 'BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
UID:meeting-123@example.com
DTSTART:20250115T140000Z
DTEND:20250115T150000Z
SUMMARY:Updated Meeting
END:VEVENT
END:VCALENDAR' \
  https://server/alice/calendar.ics/event.ics
```

#### CHECKIN - Create New Version

```bash
# Check in to create new version
curl -X CHECKIN \
  -u alice:password \
  https://server/alice/calendar.ics/event.ics
```

Response (201 Created):

```xml
<?xml version="1.0" encoding="utf-8"?>
<D:multistatus xmlns:D="DAV:">
  <D:response>
    <D:href>/alice/calendar.ics/event.ics</D:href>
    <D:propstat>
      <D:prop>
        <D:checked-in>
          <D:href>/.versions/alice/calendar.ics/event.ics/xyz98765</D:href>
        </D:checked-in>
        <D:version-name>xyz98765</D:version-name>
      </D:prop>
      <D:status>HTTP/1.1 200 OK</D:status>
    </D:propstat>
  </D:response>
</D:multistatus>
```

The `Location` header contains the URL of the newly created version.

#### UNCHECKOUT - Cancel Editing

Cancel a checkout and discard changes:

```bash
# Cancel checkout without creating version
curl -X UNCHECKOUT \
  -u alice:password \
  https://server/alice/calendar.ics/event.ics
```

This restores the item to its state before checkout.

#### VERSION-CONTROL - Initialize Tracking

Place an untracked item under version control:

```bash
# Initialize version control for an item
curl -X VERSION-CONTROL \
  -u alice:password \
  https://server/alice/calendar.ics/event.ics
```

Response (200 OK):

```xml
<?xml version="1.0" encoding="utf-8"?>
<D:multistatus xmlns:D="DAV:">
  <D:response>
    <D:href>/alice/calendar.ics/event.ics</D:href>
    <D:propstat>
      <D:prop>
        <D:resourcetype>
          <D:version-controlled-resource/>
        </D:resourcetype>
        <D:checked-in>
          <D:href>/.versions/alice/calendar.ics/event.ics/initial1</D:href>
        </D:checked-in>
        <D:version-name>initial1</D:version-name>
      </D:prop>
      <D:status>HTTP/1.1 200 OK</D:status>
    </D:propstat>
  </D:response>
</D:multistatus>
```

### Auto-Versioning

When `versioning_auto = checkout-checkin`, PUT operations automatically create git commits without requiring explicit CHECKOUT/CHECKIN:

```ini
[storage]
versioning = True
versioning_auto = checkout-checkin
```

With auto-versioning enabled:
- Creating a new item: Commits with message `AUTO-VERSION: Create {item}`
- Updating an item: Commits with message `AUTO-VERSION: Update {item}`

This provides transparent versioning without changing client behavior.

### Version Properties

PROPFIND returns versioning properties when enabled:

```bash
curl -X PROPFIND \
  -u alice:password \
  -H "Depth: 0" \
  -H "Content-Type: application/xml" \
  -d '<?xml version="1.0"?>
      <D:propfind xmlns:D="DAV:">
        <D:prop>
          <D:checked-in/>
          <D:version-history/>
          <D:version-name/>
        </D:prop>
      </D:propfind>' \
  https://server/alice/calendar.ics/event.ics
```

Response includes:

```xml
<D:prop>
  <D:checked-in>
    <D:href>/.versions/alice/calendar.ics/event.ics/abc12345</D:href>
  </D:checked-in>
  <D:version-history>
    <D:href>/.versions/alice/calendar.ics/event.ics/</D:href>
  </D:version-history>
  <D:version-name>abc12345</D:version-name>
</D:prop>
```

#### Available Version Properties

| Property | Description |
|----------|-------------|
| `DAV:checked-in` | URL of current version (when not checked out) |
| `DAV:checked-out` | URL of version being edited (when checked out) |
| `DAV:version-history` | URL of version history resource |
| `DAV:version-name` | Short version identifier (git SHA prefix) |
| `DAV:creator-displayname` | Author of the version |
| `DAV:getlastmodified` | Version timestamp |

### Fork Control

The `versioning_checkout_fork` option controls concurrent checkout behavior:

| Policy | Behavior |
|--------|----------|
| `forbidden` | Only one user can checkout at a time (default) |
| `discouraged` | Warn but allow concurrent checkouts |
| `ok` | Allow multiple concurrent checkouts |

With `forbidden` policy, a second CHECKOUT returns 409 Conflict:

```xml
<?xml version="1.0" encoding="utf-8"?>
<D:error xmlns:D="DAV:">
  <D:cannot-modify-version-controlled-content/>
  <D:responsedescription>Resource already checked out by alice</D:responsedescription>
</D:error>
```

### Checkout Expiration

Stale checkouts are automatically cleared after `versioning_checkout_timeout` seconds:

```ini
[storage]
versioning_checkout_timeout = 3600  # 1 hour
```

Set to `0` to disable expiration (checkouts never expire).

### Use Cases

#### Audit Trail
Track all changes to calendar/contact data for compliance:
```bash
# View complete history
curl -u admin:password https://server/.versions/user/calendar.ics/event.ics/
```

#### Undo Changes
Restore an item to a previous version:
```bash
# Get old content
OLD_CONTENT=$(curl -u alice:password \
  https://server/.versions/alice/calendar.ics/event.ics/def67890)

# Restore by PUTting old content
echo "$OLD_CONTENT" | curl -X PUT \
  -u alice:password \
  -H "Content-Type: text/calendar" \
  -d @- \
  https://server/alice/calendar.ics/event.ics
```

#### Conflict Prevention
Use CHECKOUT to prevent concurrent edits in shared calendars:
```bash
# Lock for editing
curl -X CHECKOUT -u alice:password https://server/shared/calendar.ics/meeting.ics

# Make changes...

# Unlock and save
curl -X CHECKIN -u alice:password https://server/shared/calendar.ics/meeting.ics
```

### Version Labels (RFC 3253 §8)

Labels provide human-readable names for specific versions, like git tags. This allows you to mark important versions (releases, milestones, stable points) for easy reference.

#### LABEL Operations

RFC 3253 defines three label operations:

| Operation | Purpose | Example Use Case |
|-----------|---------|------------------|
| **ADD** | Add new label to current version | Mark version as "production" |
| **SET** | Move existing label to current version | Update "latest" to point to new version |
| **REMOVE** | Delete a label from all versions | Remove obsolete "beta" label |

#### Adding Labels

Add one or more labels to the current version:

```bash
# Add single label
curl -X LABEL \
  -u alice:password \
  -H "Content-Type: application/xml" \
  -d '<?xml version="1.0" encoding="utf-8"?>
      <D:label xmlns:D="DAV:">
        <D:add>
          <D:label-name>production</D:label-name>
        </D:add>
      </D:label>' \
  https://server/alice/calendar.ics/event.ics
```

Response (200 OK):
```
LABEL ADD successful for labels: production
```

#### Adding Multiple Labels

```bash
# Add multiple labels at once
curl -X LABEL \
  -u alice:password \
  -H "Content-Type: application/xml" \
  -d '<?xml version="1.0" encoding="utf-8"?>
      <D:label xmlns:D="DAV:">
        <D:add>
          <D:label-name>v1.0</D:label-name>
          <D:label-name>stable</D:label-name>
          <D:label-name>production</D:label-name>
        </D:add>
      </D:label>' \
  https://server/alice/calendar.ics/event.ics
```

#### Moving Labels (SET)

Move an existing label to the current version:

```bash
# Move "latest" label to current version
curl -X LABEL \
  -u alice:password \
  -H "Content-Type: application/xml" \
  -d '<?xml version="1.0" encoding="utf-8"?>
      <D:label xmlns:D="DAV:">
        <D:set>
          <D:label-name>latest</D:label-name>
        </D:set>
      </D:label>' \
  https://server/alice/calendar.ics/event.ics
```

Use SET when you want a label to always point to the most recent version of something (like "production" or "latest").

#### Removing Labels

Delete a label completely:

```bash
# Remove label from all versions
curl -X LABEL \
  -u alice:password \
  -H "Content-Type: application/xml" \
  -d '<?xml version="1.0" encoding="utf-8"?>
      <D:label xmlns:D="DAV:">
        <D:remove>
          <D:label-name>temporary</D:label-name>
        </D:remove>
      </D:label>' \
  https://server/alice/calendar.ics/event.ics
```

#### Querying Labels with PROPFIND

Retrieve all labels for an item using the `DAV:label-name-set` property:

```bash
# Get labels for an item
curl -X PROPFIND \
  -u alice:password \
  -H "Depth: 0" \
  -H "Content-Type: application/xml" \
  -d '<?xml version="1.0" encoding="utf-8"?>
      <propfind xmlns="DAV:">
        <prop>
          <label-name-set/>
        </prop>
      </propfind>' \
  https://server/alice/calendar.ics/event.ics
```

Response:
```xml
<?xml version='1.0' encoding='utf-8'?>
<multistatus xmlns="DAV:">
  <response>
    <href>/alice/calendar.ics/event.ics</href>
    <propstat>
      <prop>
        <label-name-set>
          <label-name>v1.0</label-name>
          <label-name>stable</label-name>
          <label-name>production</label-name>
        </label-name-set>
      </prop>
      <status>HTTP/1.1 200 OK</status>
    </propstat>
  </response>
</multistatus>
```

#### Label Use Cases

**Release Management**:
```bash
# Mark a stable version for distribution
curl -X LABEL -u alice:password \
  -H "Content-Type: application/xml" \
  -d '<D:label xmlns:D="DAV:"><D:add><D:label-name>v2.1-release</D:label-name></D:add></D:label>' \
  https://server/alice/calendar.ics/team-schedule.ics
```

**Environment Tracking**:
```bash
# Track which version is in each environment
curl -X LABEL ... -d '<D:add><D:label-name>dev</D:label-name></D:add>' ...
curl -X LABEL ... -d '<D:add><D:label-name>staging</D:label-name></D:add>' ...
curl -X LABEL ... -d '<D:add><D:label-name>production</D:label-name></D:add>' ...
```

**Milestone Markers**:
```bash
# Mark important milestones in calendar history
curl -X LABEL ... -d '<D:add><D:label-name>before-reorganization</D:label-name></D:add>' ...
curl -X LABEL ... -d '<D:add><D:label-name>2025-Q1-final</D:label-name></D:add>' ...
```

#### Label Storage

Labels are stored as git lightweight tags with path-based namespacing:

```bash
# Git tags visible in storage/collection-root/.git
cd /var/lib/moreradicale/collections
git tag -l
# Output:
#   alice/calendar.ics/event.ics/production
#   alice/calendar.ics/event.ics/stable
#   alice/calendar.ics/event.ics/v1.0
```

This prevents label name collisions across different items while keeping the git repository clean and efficient.

### Activities (Change Sets)

RFC 3253 Activities provide a way to group related changes across multiple resources into logical change sets, similar to feature branches in git. Activities allow you to:

- **Track related changes**: Group multiple CHECKOUT/CHECKIN operations into a single activity
- **Associate versions**: Link git commits to high-level work items
- **Query change sets**: Find all resources and versions associated with an activity
- **Organize work**: Separate concurrent changes into isolated activities

#### Creating Activities

Use the MKACTIVITY method to create a new activity:

```bash
# Create activity with name and description
curl -X MKACTIVITY \
  -u alice:password \
  -H "Content-Type: application/xml" \
  -d '<?xml version="1.0"?>
      <D:mkactivity xmlns:D="DAV:">
        <D:displayname>Q1 2025 Calendar Updates</D:displayname>
        <D:comment>All calendar changes for Q1 2025 planning</D:comment>
      </D:mkactivity>' \
  https://server/.activities/new
```

Response (201 Created):

```xml
<?xml version="1.0" encoding="utf-8"?>
<D:multistatus xmlns:D="DAV:">
  <D:response>
    <D:href>/.activities/f47ac10b-58cc-4372-a567-0e02b2c3d479</D:href>
    <D:propstat>
      <D:prop>
        <D:displayname>Q1 2025 Calendar Updates</D:displayname>
        <D:comment>All calendar changes for Q1 2025 planning</D:comment>
        <D:creator-displayname>alice</D:creator-displayname>
      </D:prop>
      <D:status>HTTP/1.1 200 OK</D:status>
    </D:propstat>
  </D:response>
</D:multistatus>
```

The `Location` header contains the activity URL: `/.activities/{activity-id}`

#### Using Activities with CHECKOUT

Associate a checkout with an activity by including the activity URL in the CHECKOUT request:

```bash
# Checkout with activity context
ACTIVITY_ID="f47ac10b-58cc-4372-a567-0e02b2c3d479"

curl -X CHECKOUT \
  -u alice:password \
  -H "Content-Type: application/xml" \
  -d "<?xml version=\"1.0\"?>
      <D:checkout xmlns:D=\"DAV:\">
        <D:activity-set>
          <D:href>/.activities/${ACTIVITY_ID}</D:href>
        </D:activity-set>
      </D:checkout>" \
  https://server/alice/calendar.ics/event.ics
```

Response includes the activity association:

```xml
<?xml version="1.0" encoding="utf-8"?>
<D:multistatus xmlns:D="DAV:">
  <D:response>
    <D:href>/alice/calendar.ics/event.ics</D:href>
    <D:propstat>
      <D:prop>
        <D:checked-out>
          <D:href>/.versions/alice/calendar.ics/event.ics/abc12345</D:href>
        </D:checked-out>
        <D:activity-set>
          <D:href>/.activities/f47ac10b-58cc-4372-a567-0e02b2c3d479</D:href>
        </D:activity-set>
      </D:prop>
      <D:status>HTTP/1.1 200 OK</D:status>
    </D:propstat>
  </D:response>
</D:multistatus>
```

#### Automatic Version Tracking

When you CHECKIN a resource that was checked out within an activity, the created version (git commit SHA) is automatically associated with the activity:

```bash
# 1. Modify the checked-out resource
curl -X PUT \
  -u alice:password \
  -H "Content-Type: text/calendar" \
  -d 'BEGIN:VCALENDAR...' \
  https://server/alice/calendar.ics/event.ics

# 2. Checkin - version automatically added to activity
curl -X CHECKIN \
  -u alice:password \
  https://server/alice/calendar.ics/event.ics
```

The new version's git commit SHA is recorded in the activity.

#### Querying Activity Associations

Use PROPFIND to discover which activities a resource is associated with:

```bash
# Query activity-set property
curl -X PROPFIND \
  -u alice:password \
  -H "Content-Type: application/xml" \
  -H "Depth: 0" \
  -d '<?xml version="1.0"?>
      <D:propfind xmlns:D="DAV:">
        <D:prop>
          <D:activity-set/>
        </D:prop>
      </D:propfind>' \
  https://server/alice/calendar.ics/event.ics
```

Response:

```xml
<?xml version="1.0" encoding="utf-8"?>
<D:multistatus xmlns:D="DAV:">
  <D:response>
    <D:href>/alice/calendar.ics/event.ics</D:href>
    <D:propstat>
      <D:prop>
        <D:activity-set>
          <D:href>/.activities/f47ac10b-58cc-4372-a567-0e02b2c3d479</D:href>
        </D:activity-set>
      </D:prop>
      <D:status>HTTP/1.1 200 OK</D:status>
    </D:propstat>
  </D:response>
</D:multistatus>
```

#### Activity Workflow Example

Complete workflow for organizing related changes:

```bash
# 1. Create activity for feature work
curl -X MKACTIVITY \
  -u alice:password \
  -H "Content-Type: application/xml" \
  -d '<?xml version="1.0"?>
      <D:mkactivity xmlns:D="DAV:">
        <D:displayname>Team Meeting Reorganization</D:displayname>
        <D:comment>Update all team meeting times for new schedule</D:comment>
      </D:mkactivity>' \
  https://server/.activities/new

# Capture activity ID from Location header
ACTIVITY="abc123..."

# 2. Checkout multiple related events with same activity
for event in meeting-1.ics meeting-2.ics meeting-3.ics; do
  curl -X CHECKOUT \
    -u alice:password \
    -H "Content-Type: application/xml" \
    -d "<?xml version=\"1.0\"?>
        <D:checkout xmlns:D=\"DAV:\">
          <D:activity-set>
            <D:href>/.activities/${ACTIVITY}</D:href>
          </D:activity-set>
        </D:checkout>" \
    https://server/alice/calendar.ics/${event}
done

# 3. Modify each event (update meeting times)
# ... PUT requests for each event ...

# 4. Checkin each event - versions automatically tracked in activity
for event in meeting-1.ics meeting-2.ics meeting-3.ics; do
  curl -X CHECKIN \
    -u alice:password \
    https://server/alice/calendar.ics/${event}
done

# 5. Query activity to see all associated versions
# (Future: GET /.activities/${ACTIVITY} to view activity details)
```

#### Activity Storage

Activities are stored in the `.activities/` directory within the storage folder as JSON files:

```bash
ls -la /var/lib/moreradicale/collections/.activities/
# Output:
# f47ac10b-58cc-4372-a567-0e02b2c3d479.json
# a1b2c3d4-5678-90ab-cdef-123456789012.json
```

Each activity file contains:

```json
{
  "activity_id": "f47ac10b-58cc-4372-a567-0e02b2c3d479",
  "creator": "alice",
  "created": "2025-01-16T10:30:00Z",
  "display_name": "Q1 2025 Calendar Updates",
  "description": "All calendar changes for Q1 2025 planning",
  "checkouts": [
    "collection-root/alice/calendar.ics/event-1.ics",
    "collection-root/alice/calendar.ics/event-2.ics"
  ],
  "versions": [
    "abc12345def67890",
    "fed09876cba54321"
  ]
}
```

#### Activity Use Cases

**Feature Development**:
```bash
# Group all changes for a feature into one activity
MKACTIVITY "Conference Schedule Feature"
CHECKOUT event-1.ics (with activity)
CHECKOUT event-2.ics (with activity)
# ... modify and checkin
# All versions linked to feature activity
```

**Bulk Updates**:
```bash
# Track mass updates across many resources
MKACTIVITY "Timezone Migration - EST to CST"
# Checkout 50 events, update timezones, checkin
# Activity tracks all 50 version changes
```

**Collaborative Changes**:
```bash
# Multiple users working on related calendars
MKACTIVITY "Conference Planning" by alice
# Alice and bob both checkout events with same activity
# All commits tracked together regardless of author
```

**Audit Trail**:
```bash
# Activities provide high-level audit grouping
# "What changed during Q1 planning?"
# Query activity to see all associated git commits
```

---

## Troubleshooting

### Metrics Issues

#### Metrics endpoint returns 404

```
GET /.metrics -> 404 Not Found
```

**Solution**: Enable metrics in configuration:
```ini
[metrics]
enabled = True
```

#### Metrics endpoint returns 401

**Cause**: Authentication required but not provided.

**Solution**: Either disable auth for metrics:
```ini
[metrics]
require_auth = False
```

Or provide credentials:
```bash
curl -u user:password http://localhost:5232/.metrics
```

### VTODO Relationship Issues

#### Circular dependency detected

**Cause**: Task A depends on Task B which depends on Task A.

**Solution**: Review and fix RELATED-TO properties to remove cycles. Use `validate_no_cycles()` to detect issues:

```python
from moreradicale.vtodo.relationships import validate_no_cycles, build_task_hierarchy

hierarchy = build_task_hierarchy(vtodo_list)
if not validate_no_cycles(hierarchy):
    print("Circular dependency detected!")
```

### Directory Gateway Issues

#### LDAP connection timeout

```
LDAPSocketOpenError: socket connection error
```

**Solution**:
1. Verify LDAP server is reachable: `ldapsearch -H ldap://server:389 -x -b "" -s base`
2. Check firewall rules
3. Increase timeout: `ldap_timeout = 30`

#### No contacts returned

**Cause**: LDAP filter too restrictive or wrong base DN.

**Solution**:
1. Test filter with ldapsearch:
   ```bash
   ldapsearch -H ldap://server -D "cn=bind,dc=example,dc=com" -w password \
     -b "ou=People,dc=example,dc=com" "(objectClass=inetOrgPerson)"
   ```
2. Check base_dn matches your LDAP structure
3. Verify bind credentials have read access

#### TLS certificate error

```
LDAPSocketOpenError: TLS negotiation failure
```

**Solution**:
```ini
[directory]
ldap_tls = True
ldap_tls_cacert = /path/to/ca-cert.pem
```

Or for self-signed certs (not recommended for production):
```ini
ldap_tls_require_cert = False
```

### WebSocket Issues

#### Connection immediately closes

**Cause**: Authentication failure or WebSync disabled.

**Solution**:
1. Enable WebSync: `enabled = True`
2. Provide valid credentials in WebSocket handshake
3. Check server logs for auth errors

#### Notifications not received

**Cause**: Not subscribed or subscription path incorrect.

**Solution**:
1. Verify subscription response: `{"status": "subscribed"}`
2. Check path format includes trailing slash for collections: `/alice/calendar/`
3. Ensure changes are being made by different user (self-notifications are filtered)

#### WebSocket disconnects after 30 seconds

**Cause**: Timeout due to no activity.

**Solution**: Send periodic ping messages:
```javascript
setInterval(() => {
  ws.send(JSON.stringify({ action: 'ping', timestamp: Date.now() }));
}, 25000);  // Every 25 seconds
```

### Versioning Issues

#### CHECKOUT/CHECKIN returns 405 Method Not Allowed

**Cause**: Versioning is not enabled.

**Solution**:
```ini
[storage]
versioning = True
```

#### Git commit fails with "empty ident name"

**Cause**: Git user identity not configured in storage folder.

**Solution**: Configure git identity in storage folder:
```bash
cd /var/lib/moreradicale/collections
git config user.email "radicale@localhost"
git config user.name "moreradicale"
```

#### Version history returns empty

**Cause**: Item has no git history or is not tracked.

**Solution**:
1. Verify storage folder is a git repository: `git status`
2. Check if item is tracked: `git log --follow -- collection-root/user/calendar.ics/event.ics`
3. Use VERSION-CONTROL to initialize tracking for untracked items

#### CHECKOUT returns 409 Conflict

**Cause**: Item already checked out by another user (fork policy = forbidden).

**Solution**:
1. Wait for other user to CHECKIN or UNCHECKOUT
2. Or change fork policy to allow concurrent checkouts:
   ```ini
   [storage]
   versioning_checkout_fork = ok
   ```

#### Auto-versioning not creating commits

**Cause**: `versioning_auto` not set to `checkout-checkin`.

**Solution**:
```ini
[storage]
versioning = True
versioning_auto = checkout-checkin
```

#### Version content returns 404

**Cause**: Invalid version SHA or item path.

**Solution**:
1. Verify SHA exists: `git log --oneline -1 {sha}`
2. Check path format: `/.versions/{user}/{collection}/{item}/{sha}`
3. Use short SHA (8 characters) as returned by version-tree

### Debug Logging

Enable debug logging for detailed troubleshooting:

```ini
[logging]
level = debug
```

Look for log messages:
- `WebSync:` - WebSocket connection/subscription events
- `Directory:` - LDAP connection and query events
- `Metrics:` - Metrics collection events
- `VTODO:` - Task relationship processing
- `VERSION-CONTROL`, `CHECKOUT`, `CHECKIN`, `UNCHECKOUT` - Versioning operations
- `Auto-versioned` - Auto-versioning commits

---

## Support

- GitHub Issues: https://github.com/Kozea/moreradicale/issues
- Discussions: https://github.com/Kozea/moreradicale/discussions
- Documentation: https://radicale.org
