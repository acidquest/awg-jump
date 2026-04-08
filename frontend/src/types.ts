export interface Interface {
  id: number
  name: string
  mode: 'server' | 'client'
  public_key: string
  listen_port: number | null
  address: string
  dns: string | null
  endpoint: string | null
  allowed_ips: string | null
  persistent_keepalive: number | null
  enabled: boolean
  running: boolean
  obf_jc: number | null
  obf_jmin: number | null
  obf_jmax: number | null
  obf_s1: number | null
  obf_s2: number | null
  obf_s3: number | null
  obf_s4: number | null
  obf_h1: number | null
  obf_h2: number | null
  obf_h3: number | null
  obf_h4: number | null
  obf_generated_at: string | null
}

export interface Peer {
  id: number
  interface_id: number
  name: string
  public_key: string
  preshared_key: string | null
  allowed_ips: string
  tunnel_address: string | null
  persistent_keepalive: number | null
  enabled: boolean
  last_handshake: string | null
  rx_bytes: number | null
  tx_bytes: number | null
  created_at: string | null
}

export type NodeStatus = 'pending' | 'deploying' | 'online' | 'degraded' | 'offline' | 'error'

export interface Node {
  id: number
  name: string
  host: string
  ssh_port: number
  awg_port: number
  awg_address: string | null
  public_key: string | null
  status: NodeStatus
  is_active: boolean
  priority: number
  last_seen: string | null
  last_deploy: string | null
  rx_bytes: number | null
  tx_bytes: number | null
  latency_ms: number | null
  created_at: string | null
}

export interface DeployLog {
  id: number
  node_id: number
  started_at: string | null
  finished_at: string | null
  status: 'running' | 'success' | 'failed'
  log_output: string | null
}

export interface NodeDetail extends Node {
  last_deploy_log: DeployLog | null
}

export interface NodeStats {
  node_id: number
  status: string
  is_active: boolean
  latency_ms: number | null
  rx_bytes: number | null
  tx_bytes: number | null
  last_seen: string | null
  last_deploy: string | null
  deploy_logs: DeployLog[]
}

export interface GeoipSource {
  id: number
  display_name: string
  url: string
  country_code: string
  last_updated: string | null
  prefix_count: number | null
  enabled: boolean
  created_at: string | null
}

export interface GeoipStatus {
  update_running: boolean
  total_prefixes: number
  last_updated: string | null
  sources: GeoipSource[]
}

export interface DnsDomain {
  id: number
  domain: string
  upstream: 'yandex' | 'default'
  enabled: boolean
  created_at: string | null
}

export interface DnsZone {
  zone: 'local' | 'vpn'
  dns_servers: string[]
  description: string
  updated_at: string
}

export interface DnsStatus {
  running: boolean
  pid: number | null
  listen_ip: string
  conf_file: string
  local_zone_dns?: string[]
  vpn_zone_dns?: string[]
}

export interface SystemStatus {
  uptime_seconds: number
  interfaces: Array<{
    name: string
    mode: string
    address: string
    enabled: boolean
    running: boolean
    public_key: string
    peers_count: number
  }>
  geoip: Array<{
    country_code: string
    ipset_name: string
    prefix_count: number
    last_updated: string | null
    cache_fresh: boolean
  }>
  ipsets: Array<{ name: string; count: number }>
  routing: Partial<RoutingStatus> & { error?: string }
  active_node: {
    id: number
    name: string
    host: string
    status: string
    latency_ms: number | null
    last_seen: string | null
  } | null
}

export interface RoutingStatus {
  rule_local: boolean
  rule_vpn: boolean
  route_local: string | null
  route_vpn: string | null
  prerouting_geoip: boolean
  prerouting_other: boolean
  output_geoip: boolean
  output_other: boolean
  nat_eth0: boolean
  nat_awg1: boolean
  invert_geoip: boolean
  geoip_mark: string
  other_mark: string
  geoip_destination: 'local' | 'vpn'
  other_destination: 'local' | 'vpn'
  physical_iface: string
  ip_rules: string[]
  ip_routes: Record<string, string[]>
}
