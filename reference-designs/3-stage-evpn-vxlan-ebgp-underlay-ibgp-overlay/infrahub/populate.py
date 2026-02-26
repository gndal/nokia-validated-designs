#!/usr/bin/env python3
"""
Populate Infrahub with Nokia SR Linux 3-stage EVPN/VXLAN DC fabric data.

Topology   : dc1  –  2 spines, 6 leaves, 9 servers
Underlay   : eBGP RFC 5549 (IPv6 link-local dynamic neighbours)
Overlay    : iBGP EVPN AS 65500, spines as route-reflectors
Source     : configs/*.cli  +  3-stage-ebgp-underlay-ibgp-overlay.clab.yaml

Usage
-----
  cd <project-root>
  uv run --python 3.13 --directory ~/infrahub-mcp python infrahub/populate.py

Environment variables:
  INFRAHUB_ADDRESS   http://localhost:8000          (default)
  INFRAHUB_API_TOKEN <required – set in .env or shell>

  Copy .env.example to .env and fill in your token.
"""
from __future__ import annotations

import asyncio
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import yaml

# ── Connection ────────────────────────────────────────────────────────────────
INFRAHUB_URL: str = os.getenv("INFRAHUB_ADDRESS", "http://localhost:8000")
_token = os.getenv("INFRAHUB_API_TOKEN", "")
if not _token:
    sys.exit(
        "ERROR: INFRAHUB_API_TOKEN is not set.\n"
        "       Copy .env.example → .env, set your token, then re-run."
    )
API_TOKEN: str = _token
SCHEMA_FILE: Path = Path(__file__).parent / "schema.yaml"


# ═══════════════════════════════════════════════════════════════════════════════
# Fabric data  (extracted from configs/*.cli + clab YAML)
# ═══════════════════════════════════════════════════════════════════════════════

FABRIC_NAME = "dc1"
FABRIC_DESCRIPTION = "3-stage Nokia SR Linux EVPN/VXLAN fabric — eBGP underlay, iBGP EVPN overlay"

# ── Devices ───────────────────────────────────────────────────────────────────
@dataclass
class DeviceData:
    hostname: str
    role: str          # spine | leaf
    asn: int
    mgmt_ip: str
    system_ip: str
    nos_version: str = "25.3.2"
    device_type: str = "ixrd3l"
    platform: str = "nokia_srlinux"


DEVICES: list[DeviceData] = [
    DeviceData("spine1", "spine", 65500, "172.21.21.101", "192.0.2.101"),
    DeviceData("spine2", "spine", 65500, "172.21.21.102", "192.0.2.102"),
    DeviceData("leaf1",  "leaf",  65411, "172.21.21.11",  "192.0.2.11"),
    DeviceData("leaf2",  "leaf",  65412, "172.21.21.12",  "192.0.2.12"),
    DeviceData("leaf3",  "leaf",  65413, "172.21.21.13",  "192.0.2.13"),
    DeviceData("leaf4",  "leaf",  65414, "172.21.21.14",  "192.0.2.14"),
    DeviceData("leaf5",  "leaf",  65415, "172.21.21.15",  "192.0.2.15"),
    DeviceData("leaf6",  "leaf",  65416, "172.21.21.16",  "192.0.2.16"),
]

# ── Interfaces per device ─────────────────────────────────────────────────────
@dataclass
class InterfaceData:
    device: str
    name: str
    if_type: str        # ethernet | lag | irb | system | vxlan
    description: str = ""
    ip_address: str = ""
    connected_endpoint: str = ""   # server hostname if applicable


INTERFACES: list[InterfaceData] = [
    # ── spine1 ────────────────────────────────────────────────────────────────
    InterfaceData("spine1", "ethernet-1/1", "ethernet", "spine1 → leaf1"),
    InterfaceData("spine1", "ethernet-1/2", "ethernet", "spine1 → leaf2"),
    InterfaceData("spine1", "ethernet-1/3", "ethernet", "spine1 → leaf3"),
    InterfaceData("spine1", "ethernet-1/4", "ethernet", "spine1 → leaf4"),
    InterfaceData("spine1", "ethernet-1/5", "ethernet", "spine1 → leaf5"),
    InterfaceData("spine1", "ethernet-1/6", "ethernet", "spine1 → leaf6"),
    InterfaceData("spine1", "system0",       "system",  "spine1 loopback",  ip_address="192.0.2.101/32"),

    # ── spine2 ────────────────────────────────────────────────────────────────
    InterfaceData("spine2", "ethernet-1/1", "ethernet", "spine2 → leaf1"),
    InterfaceData("spine2", "ethernet-1/2", "ethernet", "spine2 → leaf2"),
    InterfaceData("spine2", "ethernet-1/3", "ethernet", "spine2 → leaf3"),
    InterfaceData("spine2", "ethernet-1/4", "ethernet", "spine2 → leaf4"),
    InterfaceData("spine2", "ethernet-1/5", "ethernet", "spine2 → leaf5"),
    InterfaceData("spine2", "ethernet-1/6", "ethernet", "spine2 → leaf6"),
    InterfaceData("spine2", "system0",       "system",  "spine2 loopback",  ip_address="192.0.2.102/32"),

    # ── leaf1 ─────────────────────────────────────────────────────────────────
    InterfaceData("leaf1", "ethernet-1/1", "ethernet", "leaf1 → spine1"),
    InterfaceData("leaf1", "ethernet-1/2", "ethernet", "leaf1 → spine2"),
    InterfaceData("leaf1", "ethernet-1/3", "ethernet", "leaf1 → s1 (VLAN 10 access)",  connected_endpoint="s1"),
    InterfaceData("leaf1", "ethernet-1/4", "ethernet", "leaf1 → s5 (routed vrf1)",     connected_endpoint="s5", ip_address="172.16.100.0/31"),
    InterfaceData("leaf1", "ethernet-1/5", "ethernet", "leaf1 → s7 (access)",          connected_endpoint="s7"),
    InterfaceData("leaf1", "system0",       "system",  "leaf1 loopback",               ip_address="192.0.2.11/32"),
    InterfaceData("leaf1", "irb0",          "irb",     "leaf1 anycast-gw IRB"),

    # ── leaf2 ─────────────────────────────────────────────────────────────────
    InterfaceData("leaf2", "ethernet-1/1", "ethernet", "leaf2 → spine1"),
    InterfaceData("leaf2", "ethernet-1/2", "ethernet", "leaf2 → spine2"),
    InterfaceData("leaf2", "ethernet-1/3", "ethernet", "leaf2 → s2 (dual-homed leg1)", connected_endpoint="s2"),
    InterfaceData("leaf2", "ethernet-1/6", "ethernet", "leaf2 → s8 (4-homed lag2)",    connected_endpoint="s8"),
    InterfaceData("leaf2", "system0",       "system",  "leaf2 loopback",               ip_address="192.0.2.12/32"),
    InterfaceData("leaf2", "irb0",          "irb",     "leaf2 anycast-gw IRB"),

    # ── leaf3 ─────────────────────────────────────────────────────────────────
    InterfaceData("leaf3", "ethernet-1/1", "ethernet", "leaf3 → spine1"),
    InterfaceData("leaf3", "ethernet-1/2", "ethernet", "leaf3 → spine2"),
    InterfaceData("leaf3", "ethernet-1/3", "ethernet", "leaf3 → s2 (dual-homed leg2)", connected_endpoint="s2"),
    InterfaceData("leaf3", "ethernet-1/6", "ethernet", "leaf3 → s8 (4-homed lag2)",    connected_endpoint="s8"),
    InterfaceData("leaf3", "system0",       "system",  "leaf3 loopback",               ip_address="192.0.2.13/32"),
    InterfaceData("leaf3", "irb0",          "irb",     "leaf3 anycast-gw IRB"),

    # ── leaf4 ─────────────────────────────────────────────────────────────────
    InterfaceData("leaf4", "ethernet-1/1", "ethernet", "leaf4 → spine1"),
    InterfaceData("leaf4", "ethernet-1/2", "ethernet", "leaf4 → spine2"),
    InterfaceData("leaf4", "ethernet-1/3", "ethernet", "leaf4 → s3 (access)",          connected_endpoint="s3"),
    InterfaceData("leaf4", "ethernet-1/4", "ethernet", "leaf4 → s4 (lag1 leg1)",       connected_endpoint="s4"),
    InterfaceData("leaf4", "ethernet-1/6", "ethernet", "leaf4 → s8 (4-homed lag2)",    connected_endpoint="s8"),
    InterfaceData("leaf4", "lag1",          "lag",     "leaf4-leaf5 ESI-LAG to s4"),
    InterfaceData("leaf4", "lag2",          "lag",     "leaf2-leaf3-leaf4-leaf5 ESI-LAG to s8"),
    InterfaceData("leaf4", "system0",       "system",  "leaf4 loopback",               ip_address="192.0.2.14/32"),
    InterfaceData("leaf4", "irb0",          "irb",     "leaf4 anycast-gw IRB"),

    # ── leaf5 ─────────────────────────────────────────────────────────────────
    InterfaceData("leaf5", "ethernet-1/1", "ethernet", "leaf5 → spine1"),
    InterfaceData("leaf5", "ethernet-1/2", "ethernet", "leaf5 → spine2"),
    InterfaceData("leaf5", "ethernet-1/4", "ethernet", "leaf5 → s4 (lag1 leg2)",       connected_endpoint="s4"),
    InterfaceData("leaf5", "ethernet-1/5", "ethernet", "leaf5 → s6 (lag3 leg1)",       connected_endpoint="s6"),
    InterfaceData("leaf5", "ethernet-1/6", "ethernet", "leaf5 → s8 (4-homed lag2)",    connected_endpoint="s8"),
    InterfaceData("leaf5", "lag1",          "lag",     "leaf4-leaf5 ESI-LAG to s4"),
    InterfaceData("leaf5", "lag2",          "lag",     "leaf2-leaf3-leaf4-leaf5 ESI-LAG to s8"),
    InterfaceData("leaf5", "system0",       "system",  "leaf5 loopback",               ip_address="192.0.2.15/32"),
    InterfaceData("leaf5", "irb0",          "irb",     "leaf5 anycast-gw IRB"),

    # ── leaf6 ─────────────────────────────────────────────────────────────────
    InterfaceData("leaf6", "ethernet-1/1", "ethernet", "leaf6 → spine1"),
    InterfaceData("leaf6", "ethernet-1/2", "ethernet", "leaf6 → spine2"),
    InterfaceData("leaf6", "ethernet-1/5", "ethernet", "leaf6 → s6 (lag3 leg2)",       connected_endpoint="s6"),
    InterfaceData("leaf6", "ethernet-1/6", "ethernet", "leaf6 → s9 (access)",          connected_endpoint="s9"),
    InterfaceData("leaf6", "system0",       "system",  "leaf6 loopback",               ip_address="192.0.2.16/32"),
    InterfaceData("leaf6", "irb0",          "irb",     "leaf6 anycast-gw IRB"),
]

# ── Fabric links  (from clab.yaml links section) ─────────────────────────────
@dataclass
class FabricLinkData:
    name: str
    link_type: str       # leaf_spine | server_access | server_lag
    endpoint_a: tuple[str, str]   # (device_hostname, interface_name)
    endpoint_b: tuple[str, str]


FABRIC_LINKS: list[FabricLinkData] = [
    # ── Leaf-spine uplinks ────────────────────────────────────────────────────
    FabricLinkData("leaf1:e1-1--spine1:e1-1", "leaf_spine", ("leaf1", "ethernet-1/1"), ("spine1", "ethernet-1/1")),
    FabricLinkData("leaf1:e1-2--spine2:e1-1", "leaf_spine", ("leaf1", "ethernet-1/2"), ("spine2", "ethernet-1/1")),
    FabricLinkData("leaf2:e1-1--spine1:e1-2", "leaf_spine", ("leaf2", "ethernet-1/1"), ("spine1", "ethernet-1/2")),
    FabricLinkData("leaf2:e1-2--spine2:e1-2", "leaf_spine", ("leaf2", "ethernet-1/2"), ("spine2", "ethernet-1/2")),
    FabricLinkData("leaf3:e1-1--spine1:e1-3", "leaf_spine", ("leaf3", "ethernet-1/1"), ("spine1", "ethernet-1/3")),
    FabricLinkData("leaf3:e1-2--spine2:e1-3", "leaf_spine", ("leaf3", "ethernet-1/2"), ("spine2", "ethernet-1/3")),
    FabricLinkData("leaf4:e1-1--spine1:e1-4", "leaf_spine", ("leaf4", "ethernet-1/1"), ("spine1", "ethernet-1/4")),
    FabricLinkData("leaf4:e1-2--spine2:e1-4", "leaf_spine", ("leaf4", "ethernet-1/2"), ("spine2", "ethernet-1/4")),
    FabricLinkData("leaf5:e1-1--spine1:e1-5", "leaf_spine", ("leaf5", "ethernet-1/1"), ("spine1", "ethernet-1/5")),
    FabricLinkData("leaf5:e1-2--spine2:e1-5", "leaf_spine", ("leaf5", "ethernet-1/2"), ("spine2", "ethernet-1/5")),
    FabricLinkData("leaf6:e1-1--spine1:e1-6", "leaf_spine", ("leaf6", "ethernet-1/1"), ("spine1", "ethernet-1/6")),
    FabricLinkData("leaf6:e1-2--spine2:e1-6", "leaf_spine", ("leaf6", "ethernet-1/2"), ("spine2", "ethernet-1/6")),
    # ── Server single-homed access links ─────────────────────────────────────
    FabricLinkData("leaf1:e1-3--s1:eth1",     "server_access", ("leaf1", "ethernet-1/3"), ("leaf1", "ethernet-1/3")),  # s1 endpoint on leaf side only
    FabricLinkData("leaf4:e1-3--s3:eth1",     "server_access", ("leaf4", "ethernet-1/3"), ("leaf4", "ethernet-1/3")),
    FabricLinkData("leaf1:e1-4--s5:eth1",     "server_access", ("leaf1", "ethernet-1/4"), ("leaf1", "ethernet-1/4")),
    FabricLinkData("leaf1:e1-5--s7:eth1",     "server_access", ("leaf1", "ethernet-1/5"), ("leaf1", "ethernet-1/5")),
    FabricLinkData("leaf6:e1-6--s9:eth1",     "server_access", ("leaf6", "ethernet-1/6"), ("leaf6", "ethernet-1/6")),
    # ── Server multi-homed LAG links ──────────────────────────────────────────
    FabricLinkData("leaf2:e1-3--s2:eth1",     "server_lag",    ("leaf2", "ethernet-1/3"), ("leaf2", "ethernet-1/3")),
    FabricLinkData("leaf3:e1-3--s2:eth2",     "server_lag",    ("leaf3", "ethernet-1/3"), ("leaf3", "ethernet-1/3")),
    FabricLinkData("leaf4:e1-4--s4:eth1",     "server_lag",    ("leaf4", "ethernet-1/4"), ("leaf4", "ethernet-1/4")),
    FabricLinkData("leaf5:e1-4--s4:eth2",     "server_lag",    ("leaf5", "ethernet-1/4"), ("leaf5", "ethernet-1/4")),
    FabricLinkData("leaf5:e1-5--s6:eth1",     "server_lag",    ("leaf5", "ethernet-1/5"), ("leaf5", "ethernet-1/5")),
    FabricLinkData("leaf6:e1-5--s6:eth2",     "server_lag",    ("leaf6", "ethernet-1/5"), ("leaf6", "ethernet-1/5")),
    FabricLinkData("leaf2:e1-6--s8:eth1",     "server_lag",    ("leaf2", "ethernet-1/6"), ("leaf2", "ethernet-1/6")),
    FabricLinkData("leaf3:e1-6--s8:eth2",     "server_lag",    ("leaf3", "ethernet-1/6"), ("leaf3", "ethernet-1/6")),
    FabricLinkData("leaf4:e1-6--s8:eth3",     "server_lag",    ("leaf4", "ethernet-1/6"), ("leaf4", "ethernet-1/6")),
    FabricLinkData("leaf5:e1-6--s8:eth4",     "server_lag",    ("leaf5", "ethernet-1/6"), ("leaf5", "ethernet-1/6")),
]

# ── VRFs ──────────────────────────────────────────────────────────────────────
@dataclass
class VRFData:
    name: str
    description: str
    l3_evi: int
    l3_vni: int
    route_target: str


VRFS: list[VRFData] = [
    VRFData("vrf1", "Tenant 1 – VLANs 10/20/30",  500, 10500, "target:1:500"),
    VRFData("vrf2", "Tenant 2 – VLANs 40/50/60",  501, 10501, "target:1:501"),
]

# ── VLANs ─────────────────────────────────────────────────────────────────────
@dataclass
class VLANData:
    name: str
    vlan_id: int
    evi: int
    vni: int
    anycast_gw: str
    route_target: str
    vrf: str            # VRF name


VLANS: list[VLANData] = [
    VLANData("vlan10", 10, 10, 10010, "172.16.10.254/24", "target:1:10",  "vrf1"),
    VLANData("vlan20", 20, 20, 10020, "172.16.20.254/24", "target:1:20",  "vrf1"),
    VLANData("vlan30", 30, 30, 10030, "172.16.30.254/24", "target:1:30",  "vrf1"),
    VLANData("vlan40", 40, 40, 10040, "172.16.40.254/24", "target:1:40",  "vrf2"),
    VLANData("vlan50", 50, 50, 10050, "172.16.50.254/24", "target:1:50",  "vrf2"),
    VLANData("vlan60", 60, 60, 10060, "172.16.60.254/24", "target:1:60",  "vrf2"),
]


# ═══════════════════════════════════════════════════════════════════════════════
# Infrahub helpers
# ═══════════════════════════════════════════════════════════════════════════════

def _headers() -> dict[str, str]:
    return {
        "X-INFRAHUB-KEY": API_TOKEN,
        "Content-Type": "application/json",
        "X-INFRAHUB-BRANCH": "main",
    }


async def load_schema(client: httpx.AsyncClient) -> None:
    """POST schema.yaml to /api/schema/load wrapped in the expected envelope."""
    raw = SCHEMA_FILE.read_text()
    schema = yaml.safe_load(raw)
    # Infrahub expects {"schemas": [<schema_dict>]}
    resp = await client.post(
        f"{INFRAHUB_URL}/api/schema/load",
        headers=_headers(),
        json={"schemas": [schema]},
        timeout=30,
    )
    if resp.status_code not in (200, 201):
        raise RuntimeError(f"Schema load failed {resp.status_code}: {resp.text[:500]}")
    result = resp.json()
    updated = result.get("schema_updated", "?")
    print(f"  Schema loaded — updated: {updated}")
    if updated:
        print("  Waiting 3 s for schema propagation …")
        await asyncio.sleep(3)


async def gql(client: httpx.AsyncClient, query: str, variables: dict | None = None) -> dict:
    """Run a GraphQL query/mutation."""
    payload: dict[str, Any] = {"query": query}
    if variables:
        payload["variables"] = variables
    resp = await client.post(
        f"{INFRAHUB_URL}/graphql",
        headers=_headers(),
        json=payload,
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    if "errors" in data:
        raise RuntimeError(f"GraphQL errors: {data['errors']}")
    return data["data"]


async def get_id(client: httpx.AsyncClient, kind: str, filter_key: str, filter_val: str | int) -> str | None:
    """Return the id of an existing node or None if not found."""
    filter_str = str(filter_val) if isinstance(filter_val, int) else f'"{filter_val}"'
    query = f"""
    query {{
      {kind}({filter_key}: {filter_str}) {{
        edges {{ node {{ id }} }}
      }}
    }}
    """
    data = await gql(client, query)
    edges = data.get(kind, {}).get("edges", [])
    return edges[0]["node"]["id"] if edges else None


async def create_node(
    client: httpx.AsyncClient,
    mutation_name: str,
    kind: str,
    data: dict[str, Any],
    label: str,
) -> str:
    """Create a node and return its id. Uses GraphQL mutation."""
    # Build attribute/relationship inputs
    fields = []
    for k, v in data.items():
        if isinstance(v, str):
            fields.append(f'{k}: {{value: "{v}"}}')
        elif isinstance(v, int):
            fields.append(f"{k}: {{value: {v}}}")
        elif isinstance(v, dict) and "id" in v:
            fields.append(f'{k}: {{id: "{v["id"]}"}}')
    fields_str = "\n        ".join(fields)

    mutation = f"""
    mutation {{
      {mutation_name}(data: {{
        {fields_str}
      }}) {{
        ok
        object {{ id }}
      }}
    }}
    """
    result = await gql(client, mutation)
    node_id = result[mutation_name]["object"]["id"]
    print(f"  CREATED  {kind:<15} {label:<45} id={node_id}")
    return node_id


# ═══════════════════════════════════════════════════════════════════════════════
# Population steps
# ═══════════════════════════════════════════════════════════════════════════════

async def populate(client: httpx.AsyncClient) -> None:  # noqa: C901
    node_ids: dict[str, str] = {}   # "kind:key" → id

    # ── 1. Schema ─────────────────────────────────────────────────────────────
    print("\n[1/7] Loading schema …")
    await load_schema(client)

    # ── 2. Fabric container ───────────────────────────────────────────────────
    print(f"\n[2/7] Fabric: {FABRIC_NAME}")
    fabric_id = await get_id(client, "DcoFabric", "name__value", FABRIC_NAME)
    if not fabric_id:
        fabric_id = await create_node(
            client, "DcoFabricCreate", "DcoFabric",
            {"name": FABRIC_NAME, "description": FABRIC_DESCRIPTION},
            FABRIC_NAME,
        )
    else:
        print(f"  EXISTS   DCFabric        {FABRIC_NAME}")
    node_ids["DCFabric:dc1"] = fabric_id

    # ── 3. VRFs ───────────────────────────────────────────────────────────────
    print("\n[3/7] VRFs …")
    for vrf in VRFS:
        existing = await get_id(client, "DcoVRF", "name__value", vrf.name)
        if existing:
            print(f"  EXISTS   DCVRF           {vrf.name}")
            node_ids[f"DCVRF:{vrf.name}"] = existing
            continue
        vrf_id = await create_node(
            client, "DcoVRFCreate", "DcoVRF",
            {
                "name": vrf.name,
                "description": vrf.description,
                "l3_evi": vrf.l3_evi,
                "l3_vni": vrf.l3_vni,
                "route_target": vrf.route_target,
                "fabric": {"id": fabric_id},
            },
            vrf.name,
        )
        node_ids[f"DCVRF:{vrf.name}"] = vrf_id

    # ── 4. VLANs ──────────────────────────────────────────────────────────────
    print("\n[4/7] VLANs …")
    for vlan in VLANS:
        existing = await get_id(client, "DcoVLAN", "vlan_id__value", vlan.vlan_id)
        if existing:
            print(f"  EXISTS   DCVLAN          {vlan.name}")
            node_ids[f"DCVLAN:{vlan.vlan_id}"] = existing
            continue
        vrf_id = node_ids[f"DCVRF:{vlan.vrf}"]
        vlan_id = await create_node(
            client, "DcoVLANCreate", "DcoVLAN",
            {
                "name": vlan.name,
                "vlan_id": vlan.vlan_id,
                "evi": vlan.evi,
                "vni": vlan.vni,
                "anycast_gw": vlan.anycast_gw,
                "route_target": vlan.route_target,
                "vrf": {"id": vrf_id},
                "fabric": {"id": fabric_id},
            },
            f"vlan{vlan.vlan_id}  vni={vlan.vni}  gw={vlan.anycast_gw}",
        )
        node_ids[f"DCVLAN:{vlan.vlan_id}"] = vlan_id

    # ── 5. Devices ────────────────────────────────────────────────────────────
    print("\n[5/7] Devices …")
    for dev in DEVICES:
        existing = await get_id(client, "DcoDevice", "hostname__value", dev.hostname)
        if existing:
            print(f"  EXISTS   DCDevice        {dev.hostname}")
            node_ids[f"DCDevice:{dev.hostname}"] = existing
            continue
        dev_id = await create_node(
            client, "DcoDeviceCreate", "DcoDevice",
            {
                "hostname": dev.hostname,
                "role": dev.role,
                "platform": dev.platform,
                "device_type": dev.device_type,
                "nos_version": dev.nos_version,
                "asn": dev.asn,
                "mgmt_ip": dev.mgmt_ip,
                "system_ip": dev.system_ip,
                "fabric": {"id": fabric_id},
            },
            f"{dev.hostname:<8} role={dev.role}  AS{dev.asn}  {dev.system_ip}",
        )
        node_ids[f"DCDevice:{dev.hostname}"] = dev_id

    # ── 6. Interfaces ─────────────────────────────────────────────────────────
    print("\n[6/7] Interfaces …")
    for iface in INTERFACES:
        dev_id = node_ids[f"DCDevice:{iface.device}"]
        # Unique key: device+name (no global uniqueness on interface name alone)
        existing_q = f"""
        query {{
          DcoInterface(name__value: "{iface.name}", device__hostname__value: "{iface.device}") {{
            edges {{ node {{ id }} }}
          }}
        }}
        """
        data = await gql(client, existing_q)
        edges = data.get("DcoInterface", {}).get("edges", [])
        if edges:
            iid = edges[0]["node"]["id"]
            print(f"  EXISTS   DCInterface     {iface.device}:{iface.name}")
            node_ids[f"DCInterface:{iface.device}:{iface.name}"] = iid
            continue

        iface_data: dict[str, Any] = {
            "name": iface.name,
            "if_type": iface.if_type,
            "admin_state": "enable",
            "device": {"id": dev_id},
        }
        if iface.description:
            iface_data["description"] = iface.description
        if iface.ip_address:
            iface_data["ip_address"] = iface.ip_address
        if iface.connected_endpoint:
            iface_data["connected_endpoint"] = iface.connected_endpoint

        iid = await create_node(
            client, "DcoInterfaceCreate", "DcoInterface",
            iface_data,
            f"{iface.device}:{iface.name}",
        )
        node_ids[f"DCInterface:{iface.device}:{iface.name}"] = iid

    # ── 7. Fabric links ──────────────────────────────────────────────────────
    print("\n[7/7] Fabric links …")
    for link in FABRIC_LINKS:
        if link.link_type != "leaf_spine":
            # For server links endpoint_a == endpoint_b (leaf interface only)
            # Skip self-referential links; description is already on the interface
            continue
        existing = await get_id(client, "DcoFabricLink", "name__value", link.name)
        if existing:
            print(f"  EXISTS   DCFabricLink    {link.name}")
            continue
        a_key = f"DCInterface:{link.endpoint_a[0]}:{link.endpoint_a[1]}"
        b_key = f"DCInterface:{link.endpoint_b[0]}:{link.endpoint_b[1]}"
        if a_key not in node_ids or b_key not in node_ids:
            print(f"  SKIP     DCFabricLink    {link.name}  (interface not found)")
            continue
        await create_node(
            client, "DcoFabricLinkCreate", "DcoFabricLink",
            {
                "name": link.name,
                "link_type": link.link_type,
                "endpoint_a": {"id": node_ids[a_key]},
                "endpoint_b": {"id": node_ids[b_key]},
            },
            link.name,
        )


# ═══════════════════════════════════════════════════════════════════════════════
# Entry point
# ═══════════════════════════════════════════════════════════════════════════════

async def main() -> None:
    print(f"Connecting to Infrahub at {INFRAHUB_URL}")
    async with httpx.AsyncClient() as client:
        # Quick health-check
        resp = await client.get(f"{INFRAHUB_URL}/", timeout=10)
        if resp.status_code != 200:
            print(f"ERROR: Infrahub not healthy ({resp.status_code}). Is the server running?")
            sys.exit(1)
        print("Infrahub is healthy ✓")
        await populate(client)

    print("\nDone — all dc1 fabric objects loaded into Infrahub.")


if __name__ == "__main__":
    asyncio.run(main())
