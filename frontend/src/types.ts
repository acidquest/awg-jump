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
  client_code: number | null
  client_kind: string | null
  client_reported_ip: string | null
  client_reported_at: string | null
  created_at: string | null
}

export type NodeStatus = 'pending' | 'deploying' | 'online' | 'degraded' | 'offline' | 'error'

export interface Node {
  id: number
  name: string
  host: string
  ssh_port: number
  awg_port: number
  provisioning_mode: 'managed' | 'manual'
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
  can_redeploy: boolean
  can_manage_peers: boolean
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
  raw_conf: string | null
}

export interface NodePeer {
  id: number
  node_id: number
  name: string
  public_key: string
  preshared_key: string | null
  tunnel_address: string
  allowed_ips: string
  persistent_keepalive: number | null
  enabled: boolean
  created_at: string | null
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
  provisioning_mode: 'managed' | 'manual'
  shared_peers: NodePeer[]
  deploy_logs: DeployLog[]
}

export interface SystemSettings {
  admin_username: string
  web_mode: 'http' | 'https'
  web_port: number
  tls_common_name: string
  tls_cert_path: string
  tls_key_path: string
  cert: {
    path: string
    sha256: string
    size_bytes: number
  } | null
  restart_required: boolean
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
  zone: string
  upstream: string
  enabled: boolean
  created_at: string | null
}

export interface DnsManualAddress {
  id: number
  domain: string
  address: string
  enabled: boolean
  created_at: string | null
}

export interface DnsZone {
  zone: string
  name: string
  dns_servers: string[]
  description: string
  is_builtin: boolean
  protocol: 'plain' | 'dot' | 'doh'
  endpoint_host: string
  endpoint_port: number | null
  endpoint_url: string
  bootstrap_address: string
  updated_at: string
}

export interface DnsStatus {
  running: boolean
  pid: number | null
  listen_ip: string
  conf_file: string
  local_zone_dns?: string[]
  vpn_zone_dns?: string[]
  stubby?: {
    enabled: boolean
    running: boolean
    listen: string
    config: string
  }
  cloudflared?: {
    enabled: boolean
    running: boolean
    listen: string
    config: string
  }
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
  local_external_ip: string | null
  active_node: {
    id: number
    name: string
    host: string
    external_ip: string
    status: string
    latency_ms: number | null
    last_seen: string | null
  } | null
}

export interface SystemMetricPoint {
  collected_at: string
  cpu_usage_percent: number
  memory_total_bytes: number
  memory_used_bytes: number
  memory_free_bytes: number
}

export interface SystemMetricsResponse {
  period: '1h' | '24h'
  retention_hours: number
  sampling_interval_seconds: number
  latest: SystemMetricPoint | null
  points: SystemMetricPoint[]
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
