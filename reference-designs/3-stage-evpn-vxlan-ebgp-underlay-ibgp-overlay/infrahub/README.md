# Infrahub Artifact Pipeline — Nokia SR Linux L2VPN YANG JSON

This directory contains the **Infrahub automation pipeline** that generates
gNMI-ready YANG JSON L2VPN configuration artifacts for every leaf node in the
Nokia SR Linux 3-stage EVPN/VXLAN reference design.

> **Design:** 3-stage Clos, eBGP RFC-5549 underlay, iBGP EVPN overlay (RR on spines)  
> **Platform:** Nokia SR Linux (XRd3L)  
> **Infrahub version:** Community v1.7.6  

---

## Table of Contents

1. [Architecture Overview](#1-architecture-overview)
2. [Repository Layout](#2-repository-layout)
3. [Pipeline Components](#3-pipeline-components)
   - [`.infrahub.yml` root manifest](#31-infrahubyml-root-manifest)
   - [GraphQL Query](#32-graphql-query)
   - [Jinja2 Transform Template](#33-jinja2-transform-template)
4. [Generated Artifact Structure](#4-generated-artifact-structure)
5. [How to Push an Artifact to a Device](#5-how-to-push-an-artifact-to-a-device)
6. [Infrahub Setup (one-time)](#6-infrahub-setup-one-time)
7. [How to Regenerate Artifacts](#7-how-to-regenerate-artifacts)
8. [Known Pitfalls & Fixes](#8-known-pitfalls--fixes)
9. [Infrahub Object Registry](#9-infrahub-object-registry)

---

## 1. Architecture Overview

```
Git repository (nokia-validated-designs)
    └── .infrahub.yml                          ← root manifest (auto-discovered)
            │
            ├── queries/leaf_l2vpn.graphql     ← registered as CoreGraphQLQuery
            └── templates/leaf_l2vpn.j2        ← registered as CoreTransformJinja2
                                                        │
                                            CoreArtifactDefinition
                                            "leaf-l2vpn-json-config"
                                                        │
                                            (for each leaf in CoreStandardGroup)
                                                        │
                                            CoreArtifact  (application/json)
                                            "<hostname>-l2vpn.json"
```

The pipeline is **data-driven**: the Jinja2 transform reads all `DcoVLAN` and
`DcoVRF` objects from Infrahub and renders device-independent JSON since all
leaves in this fabric carry the same EVPN topology.  A separate per-device
transform (not yet implemented) handles server-facing access interfaces.

---

## 2. Repository Layout

```
infrahub/
├── README.md              ← this file
├── schema.yaml            ← Infrahub node schema (DcoDevice, DcoVLAN, DcoVRF …)
├── populate.py            ← seed script — loads fabric data into Infrahub
├── queries/
│   └── leaf_l2vpn.graphql ← GraphQL query (leaf_l2vpn)
└── templates/
    └── leaf_l2vpn.j2      ← Jinja2 transform → YANG JSON artifact
```

The `.infrahub.yml` manifest lives at the **repository root** (two levels up):
```
nokia-validated-designs/
└── .infrahub.yml          ← declares queries + jinja2_transforms (no version: field)
```

---

## 3. Pipeline Components

### 3.1 `.infrahub.yml` root manifest

```yaml
---
# Nokia SR Linux 3-stage EVPN/VXLAN DC fabric – Infrahub artifact pipeline
# Output format: SR Linux gNMI-ready hierarchical JSON (YANG-modeled)
queries:
  - name: leaf_l2vpn
    file_path: "reference-designs/3-stage-evpn-vxlan-ebgp-underlay-ibgp-overlay/infrahub/queries/leaf_l2vpn.graphql"

jinja2_transforms:
  - name: leaf-l2vpn-config
    description: "Generate SR Linux gNMI-ready YANG JSON L2VPN config for a leaf node"
    query: leaf_l2vpn
    template_path: "reference-designs/3-stage-evpn-vxlan-ebgp-underlay-ibgp-overlay/infrahub/templates/leaf_l2vpn.j2"
```

> **Critical:** Do **NOT** add a `version:` field to the root `.infrahub.yml`.
> Infrahub v1.7.x rejects it with a Pydantic `extra_forbidden` error.

---

### 3.2 GraphQL Query

**File:** `queries/leaf_l2vpn.graphql`

The query accepts a `$device` variable (bound via `ArtifactDefinition.parameters`)
and fetches the target device plus all fabric-wide VLANs and VRFs:

```graphql
query leaf_l2vpn($device: String!) {
  DcoDevice(hostname__value: $device) {
    edges {
      node {
        hostname { value }
        asn      { value }
        system_ip { value }
        role     { value }
      }
    }
  }

  DcoVLAN {
    edges {
      node {
        name       { value }
        vlan_id    { value }
        evi        { value }
        vni        { value }
        anycast_gw { value }
        route_target { value }
        vrf {
          node {
            name     { value }
            l3_evi   { value }
            l3_vni   { value }
            route_target { value }
          }
        }
      }
    }
  }

  DcoVRF {
    edges {
      node {
        name         { value }
        l3_evi       { value }
        l3_vni       { value }
        route_target { value }
      }
    }
  }
}
```

The `ArtifactDefinition.parameters` maps `$device` to the node's hostname:

```json
{ "device": "hostname__value" }
```

Note the value is a **bare attribute path** (`hostname__value`), not a Jinja2
expression.  Infrahub resolves it against the target node at generation time.

---

### 3.3 Jinja2 Transform Template

**File:** `templates/leaf_l2vpn.j2`

Key patterns required by Infrahub's Jinja2 engine (`enable_async=True`):

| Pattern | Why |
|---------|-----|
| `data.DcoDevice.edges[0].node` | Infrahub wraps the GraphQL response in a top-level `data` object |
| `\| map(attribute='node') \| list \| sort(...)` | `map()` returns an async generator — `\| list` must materialise it before `\| sort()` |
| `{% set ns = namespace(vrf_irbs=[]) %}` | Jinja2 scoping rule: variables mutated inside a `for` loop are not visible outside without `namespace()` |

Template output structure (see §4 below):
- `tunnel-interface` — `vxlan0` with one vxlan-interface per VLAN (bridged) and one per VRF (routed)
- `interface` — `irb0` subinterfaces (one per VLAN, indexed by sort-stable `loop.index0`)
- `network-instance` — one `mac-vrf` per VLAN, one `ip-vrf` per VRF

---

## 4. Generated Artifact Structure

Each artifact is a **single JSON document** suitable for an SR Linux
`/` path `gnmic set` or JSON-RPC call.

```jsonc
{
  "tunnel-interface": [
    {
      "name": "vxlan0",
      "vxlan-interface": [
        // one entry per VLAN (type: bridged)
        { "index": 10, "type": "bridged",
          "ingress": { "vni": 10010 },
          "egress":  { "source-ip": "use-system-ipv4-address" } },
        // one entry per VRF (type: routed)
        { "index": 1000, "type": "routed",
          "ingress": { "vni": 100000 },
          "egress":  { "source-ip": "use-system-ipv4-address" } }
      ]
    }
  ],

  "interface": [
    {
      "name": "irb0",
      "subinterface": [
        // one per VLAN — index is loop.index0 (VLANs sorted by vlan_id)
        {
          "index": 0,
          "ip-mtu": 1500,
          "ipv4": {
            "admin-state": "enable",
            "address": [ { "ip-prefix": "10.0.10.1/24", "anycast-gw": true, "primary": {} } ],
            "arp": { "timeout": 250, "proxy-arp": true,
                     "evpn": { "advertise": [ { "route-type": "dynamic" } ] } }
          },
          "anycast-gw": { "virtual-router-id": 1 }
        }
      ]
    }
  ],

  "network-instance": [
    // mac-vrf per VLAN
    {
      "name": "macvrf-v10",
      "type": "mac-vrf",
      "admin-state": "enable",
      "interface": [ { "name": "irb0.0" } ],
      "vxlan-interface": [ { "name": "vxlan0.10" } ],
      "protocols": {
        "bgp-evpn": { "bgp-instance": [
          { "id": 1, "admin-state": "enable",
            "vxlan-interface": "vxlan0.10", "evi": 10, "ecmp": 8,
            "routes": { "bridge-table": { "mac-ip": {
              "advertise-arp-nd-only-with-mac-table-entry": true } } } }
        ] },
        "bgp-vpn": { "bgp-instance": [
          { "id": 1, "route-target": { "export-rt": "64512:10", "import-rt": "64512:10" } }
        ] }
      },
      "bridge-table": {
        "mac-learning": { "admin-state": "enable",
          "aging": { "admin-state": "enable", "age-time": 300 } },
        "mac-duplication": { "admin-state": "enable",
          "monitoring-window": 3, "num-moves": 5, "hold-down-time": 9,
          "action": "stop-learning" }
      }
    },
    // ip-vrf per VRF
    {
      "name": "vrf-red",
      "type": "ip-vrf",
      "admin-state": "enable",
      "interface": [ { "name": "irb0.0" }, { "name": "irb0.1" } ],
      "vxlan-interface": [ { "name": "vxlan0.1000" } ],
      "protocols": {
        "bgp-evpn": { "bgp-instance": [
          { "id": 1, "admin-state": "enable",
            "vxlan-interface": "vxlan0.1000", "evi": 1000, "ecmp": 8,
            "routes": { "route-table": { "mac-ip": {
              "advertise-gateway-mac": true } } } }
        ] },
        "bgp-vpn": { "bgp-instance": [
          { "id": 1, "route-target": { "export-rt": "64512:1000", "import-rt": "64512:1000" } }
        ] }
      }
    }
  ]
}
```

**Current fabric state (6 leaf artifacts, all `Ready`):**
- 6 × mac-vrf (one per VLAN)
- 2 × ip-vrf (one per VRF tenant)

---

## 5. How to Push an Artifact to a Device

### Option A — gnmic (gNMI)

```bash
# Download the artifact from Infrahub
curl -s -H "X-INFRAHUB-KEY: <token>" \
  http://localhost:8000/api/artifact/<artifact-id>/content \
  -o leaf1-l2vpn.json

# Push to device at root path
gnmic -a <leaf-mgmt-ip>:57400 -u admin -p admin --insecure \
  set --update-path / \
      --update-file leaf1-l2vpn.json \
      --encoding JSON_IETF
```

### Option B — SR Linux JSON-RPC

```bash
curl -s -u admin:admin \
  -H 'Content-Type: application/json' \
  -d "{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"set\",
       \"params\":{\"commands\":[{\"action\":\"update\",\"path\":\"/\",
       \"value\":$(cat leaf1-l2vpn.json)}]}}" \
  http://<leaf-mgmt-ip>/jsonrpc
```

### Option C — Ansible (`nokia.grpc` collection)

```yaml
- name: Push L2VPN config
  nokia.grpc.config:
    host: "{{ inventory_hostname }}"
    username: admin
    password: admin
    path: /
    encoding: JSON_IETF
    content: "{{ lookup('file', artifact_path) }}"
```

---

## 6. Infrahub Setup (one-time)

These steps were performed once to wire up the pipeline.  They are recorded
here for repeatability / disaster recovery.

### 6.1 Load schema

```bash
cd infrahub/
infrahubctl schema load schema.yaml
```

Verify `DcoDevice` has `inherit_from: [CoreArtifactTarget]` — this is required
for a node type to be usable as an `ArtifactDefinition` target.

### 6.2 Seed fabric data

```bash
python3 populate.py
```

Creates `DcoFabric`, `DcoDevice` (spines + leaves), `DcoVLAN`, `DcoVRF` objects.

### 6.3 Register the Git repository

```graphql
mutation {
  CoreRepositoryCreate(data: {
    name:     { value: "nokia-validated-designs" }
    location: { value: "https://github.com/gndal/nokia-validated-designs" }
  }) { object { id } }
}
```

Infrahub auto-discovers `.infrahub.yml` and registers the `CoreGraphQLQuery`
and `CoreTransformJinja2` objects automatically.

### 6.4 Create the leaf device group

```graphql
mutation {
  CoreStandardGroupCreate(data: {
    name:  { value: "leaf-devices" }
    label: { value: "Leaf Devices" }
  }) { object { id } }
}
```

Then add all 6 leaf `DcoDevice` node IDs as members via:

```graphql
mutation {
  CoreStandardGroupUpdate(data: {
    id: "<group-id>"
    members: { add: [{ id: "<leaf-id>" }, ...] }
  }) { ok }
}
```

### 6.5 Create the ArtifactDefinition

```graphql
mutation {
  CoreArtifactDefinitionCreate(data: {
    name:         { value: "leaf-l2vpn-json-config" }
    artifact_name: { value: "hostname__value-l2vpn.json" }
    content_type: { value: "application/json" }
    parameters:   { value: { "device": "hostname__value" } }
    targets:      { id: "<leaf-devices-group-id>" }
    transformation: { id: "<leaf-l2vpn-config-transform-id>" }
  }) { object { id } }
}
```

---

## 7. How to Regenerate Artifacts

### Trigger all 6 leaf artifacts

```bash
curl -s -X POST \
  -H "X-INFRAHUB-KEY: <token>" \
  http://localhost:8000/api/artifact/generate/<artifact-definition-id>
```

### Check status

```graphql
{
  CoreArtifact {
    edges {
      node {
        name        { value }
        status      { value }
        content_type { value }
      }
    }
  }
}
```

Expected: all artifacts `status.value == "Ready"`.

### After changing a template or query file

Infrahub only advances `CoreRepository.commit` when `.infrahub.yml` itself
changes.  After editing `leaf_l2vpn.j2` or `leaf_l2vpn.graphql`, either:

**Option A — touch `.infrahub.yml` and push:**
```bash
touch .infrahub.yml && git add .infrahub.yml && git commit -m "bump" && git push
```

**Option B — update `CoreRepository.commit` directly via GraphQL:**
```graphql
mutation {
  CoreRepositoryUpdate(data: {
    id:     "<repo-id>"
    commit: { value: "<new-git-sha>" }
  }) { ok }
}
```

---

## 8. Known Pitfalls & Fixes

| # | Symptom | Root Cause | Fix |
|---|---------|-----------|-----|
| 1 | `'DcoDevice' is undefined` in template | Infrahub wraps GraphQL response: `{"data": {"DcoDevice": ...}}` | Access via `data.DcoDevice`, not bare `DcoDevice` |
| 2 | `TypeError: 'async_generator' object is not iterable` | `enable_async=True` makes `\| map()` return an async generator | Add `\| list` between `\| map()` and `\| sort()` |
| 3 | Template changes not picked up after push | Infrahub only re-syncs when `.infrahub.yml` changes on disk | Touch `.infrahub.yml` or update `CoreRepository.commit` via mutation |
| 4 | `extra_forbidden` Pydantic error on repo sync | Root `.infrahub.yml` contains a `version: "1.0"` field | Remove the `version:` key; it is only valid in `schema.yaml` |
| 5 | `DcoDevice cannot be added to relationship CoreArtifactTarget` | Target node schema missing `inherit_from: [CoreArtifactTarget]` | Add inheritance in `schema.yaml`, reload schema |
| 6 | `ArtifactDefinition.parameters` not resolving | Used Jinja2 `{{ hostname }}` syntax in parameters JSON | Use bare attribute path: `{"device": "hostname__value"}` |
| 7 | In-loop variable mutation silently ignored | Jinja2 scoping: variables assigned inside `{% for %}` don't propagate out | Use `{% set ns = namespace(list=[]) %}` and `{% set ns.list = ns.list + [...] %}` |

---

## 9. Infrahub Object Registry

> Instance: `http://localhost:8000`

| Kind | Name | ID |
|------|------|----|
| `CoreRepository` | `nokia-validated-designs` | `1897ccf9-6065-0833-2c1d-c514cb7b91fe` |
| `CoreGraphQLQuery` | `leaf_l2vpn` | `1897cd2a-b6f4-02ee-2c15-c51d15dfe20f` |
| `CoreTransformJinja2` | `leaf-l2vpn-config` | `1897cd2b-9a9e-b71b-2c18-c515ef9f8a22` |
| `CoreStandardGroup` | `leaf-devices` (6 members) | `1897cd48-51d8-8a99-2c13-c516f2afbe5f` |
| `CoreArtifactDefinition` | `leaf-l2vpn-json-config` | `1897cd52-f6df-a240-2c10-c51dcab38c52` |
| `CoreArtifact` (leaf1) | `leaf1-l2vpn.json` | `1897cdbf-f818-c72c-2c16-c51101598ef9` |

To list all current artifacts and their IDs:
```graphql
{ CoreArtifact { edges { node { id name { value } status { value } } } } }
```
